# -*- coding: utf-8 -*-
"""OCR 识别目录页：渲染PDF页 -> OCR -> 缩进式txt + 自动检测页码偏移

OCR 后端：RapidOCR（PaddleOCR 模型权重 + onnxruntime 推理）。
paddle 3.x CPU 推理存在 oneDNN/PIR 不兼容 bug（每页约 50 秒且无法启用加速），
故采用同款模型、速度 10 倍以上的 RapidOCR。

页码约定：
  目录页码范围使用 PDF 页号（1-based，直接对应阅读器/PDF查看器显示的页码）。
  目录条目的行尾数字是"印刷页码"，写入书签时需要偏移量 offset（印刷页p -> PDF索引 = p + offset）。
  detect_offset() 可从目录条目最小页码 + 正文起始页自动推算偏移。

rapidocr 为可选依赖：未安装时 HAS_OCR=False，本模块其余功能不受影响
（程序整体仍可正常使用，仅 OCR 功能禁用）。
"""
import re
import time

import fitz
import numpy as np

from core import check_task

HAS_OCR = False
_POCR_MODULE = None
try:
    from rapidocr_onnxruntime import RapidOCR as _POCR_MODULE
    HAS_OCR = True
except Exception:
    HAS_OCR = False

RE_DOTS = re.compile(r'[….\.·]{2,}')

_ocr_instances = {}


def _mp_ocr_task(q, pdf_path, start_page, end_page, cancel_event, pause_event):
    """子进程入口：OCR目录页 + 检测偏移，结果经 multiprocessing.Queue 回传
    进度：渲染阶段 0~T，识别阶段 T~2T，检测偏移阶段 2T~2T+S（T=页数, S=扫描页数）"""
    try:
        engine = load_ocr()
        txt = ocr_to_txt(pdf_path, start_page, end_page, ocr=engine,
                         progress=lambda d, t, m: q.put(('progress', d, t, m)),
                         cancel_event=cancel_event, pause_event=pause_event)
        q.put(('ocr_done', txt))
        offset, n0 = detect_offset(pdf_path, start_page, end_page, ocr=engine,
                                   progress=lambda d, t, m: q.put(('progress', d, t, m)),
                                   cancel_event=cancel_event, pause_event=pause_event,
                                   skip_first=True)
        q.put(('ocr_offset', offset))
    except Exception as e:
        q.put(('error', type(e).__name__ + ': ' + str(e)))


def load_ocr():
    """加载（并缓存）OCR 引擎实例；首次运行自动下载模型，需联网"""
    global _ocr_instances
    if not HAS_OCR or _POCR_MODULE is None:
        raise RuntimeError('OCR组件不可用：当前为不带OCR版，请安装 rapidocr-onnxruntime 后重试')
    key = 'default'
    if key not in _ocr_instances:
        _ocr_instances[key] = _POCR_MODULE()
    return _ocr_instances[key]


def render_pages(pdf_path, start_page, end_page, dpi=150, cancel_event=None, pause_event=None,
                 progress=None):
    """渲染PDF页(1-based PDF页号) -> [(pdf_page_no, numpy RGB图像)]
    progress(done, total, msg)：每渲染一页回调一次"""
    doc = fitz.open(pdf_path)
    out = []
    try:
        total = max(end_page - start_page + 1, 0)
        for p in range(start_page, end_page + 1):
            idx = p - 1
            if idx < 0 or idx >= doc.page_count:
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('任务已取消')
            if pause_event is not None:
                while pause_event.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError('任务已取消')
                    time.sleep(0.1)
            pix = doc[idx].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            out.append((idx + 1, arr))
            if progress:
                progress(len(out), total, '渲染第 %d/%d 页' % (len(out), total))
    finally:
        doc.close()
    return out


def _ocr_images(ocr, images, progress=None, cancel_event=None, pause_event=None):
    """逐张OCR -> [(pdf_page_no, box_x, text, score)]
    progress(done, total, msg)：每识别一页回调一次"""
    lines = []
    total = len(images)
    for k, (pno, img) in enumerate(images):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('任务已取消')
        if pause_event is not None:
            while pause_event.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError('任务已取消')
                time.sleep(0.1)
        raw = ocr(img)
        res = raw[0] if isinstance(raw, tuple) else raw
        if not res:
            if progress:
                progress(k + 1, total, '识别第 %d/%d 页：无文字' % (k + 1, total))
            continue
        for j, item in enumerate(res):
            box, txt, sc = item[0], item[1], item[2]
            if not txt:
                continue
            x = 0.0
            try:
                x = float(np.asarray(box).reshape(-1)[0])
            except Exception:
                pass
            lines.append((pno, x, str(txt), float(sc)))
        if progress:
            progress(k + 1, total, '识别第 %d/%d 页：%d 行' % (k + 1, total, len(res)))
    return lines


def _indent_level(x, min_x, step=18.0):
    """按 x 坐标相对最小x的偏移推断缩进级"""
    if min_x is None:
        return 0
    d = x - min_x
    if d < step * 0.6:
        return 0
    if d < step * 2.2:
        return 1
    return 2


def _extract_entries(pdf_path, start_page, end_page, ocr, dpi=150, progress=None,
                     cancel_event=None, pause_event=None):
    """OCR目录页 -> [(pdf_page_no, indent, title, start_num, end_num, score)]
    title 可为空(纯页码行)；start/end 为印刷页码，None 表示该行无页码
    progress(done, total, msg)：渲染阶段 0~T，识别阶段 T~2T（T=页数）"""
    total = end_page - start_page + 1
    images = render_pages(pdf_path, start_page, end_page, dpi, cancel_event, pause_event,
                          progress and (lambda d, t, m: progress(d, 2 * total, m)))
    if not images:
        raise ValueError('页码范围无效：PDF页 %d-%d 不存在' % (start_page, end_page))
    lines = _ocr_images(ocr, images,
                        progress and (lambda d, t, m: progress(total + d, 2 * total, m)),
                        cancel_event, pause_event)
    if not lines:
        raise ValueError('OCR未识别到任何文字（请确认页码范围是否为目录页）')
    by_page = {}
    for pno, x, txt, sc in lines:
        by_page.setdefault(pno, []).append((x, txt, sc))
    entries = []
    for pno in sorted(by_page):
        rows = by_page[pno]
        min_x = min(r[0] for r in rows)
        for x, txt, sc in rows:
            t = RE_DOTS.sub('', txt).strip()
            if not t:
                continue
            title = t
            start = end = None
            m = re.search(r'(\d+)\s*[—–\-]{1,3}\s*(\d+)\s*$', t) or re.search(r'(\d+)\s+(\d+)\s*$', t)
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                title = t[:m.start()]
            else:
                m = re.search(r'(\d+)\s*$', t)
                if m:
                    start = end = int(m.group(1))
                    title = t[:m.start()]
            title = title.rstrip('　 .…·:：-—–（(').strip()
            entries.append((pno, _indent_level(x, min_x), title, start, end, sc))
    return entries


def _fmt_page(start, end):
    if start is None:
        return ''
    return '%d-%d' % (start, end) if start != end else str(start)


def _split_glued(s):
    """粘连页码拆分："8694" -> (86, 94)；"390451" -> (390, 451)。
    取所有分法中范围最短且合法的 (a, b)，要求 1<=a<b<=999 且 b-a<=600"""
    best = None
    for k in range(1, len(s)):
        a, b = int(s[:k]), int(s[k:])
        if 1 <= a < b <= 999 and b - a <= 600:
            if best is None or (b - a) < (best[1] - best[0]):
                best = (a, b)
    return best


def ocr_to_txt(pdf_path, start_page, end_page, ocr=None, dpi=150, progress=None,
               cancel_event=None, pause_event=None):
    """OCR目录页(PDF页号) -> 缩进式txt文本（全角空格缩进 + 标题 + 页码）
    ocr 可传入已加载的引擎实例（load_ocr()），否则自动加载
    说明：# 开头为提示行（无页码/缺标题/低置信度/页码异常），可删除或修改"""
    if ocr is None:
        ocr = load_ocr()
    entries = _extract_entries(pdf_path, start_page, end_page, ocr, dpi, progress,
                               cancel_event, pause_event)
    if not entries:
        raise ValueError('OCR未识别到任何目录条目')
    out = []
    last = None  # (pno, out索引, 缩进, 标题, 是否已有页码)：最近的有标题行，供无标题页码行合并
    for pno, ind, title, start, end, sc in entries:
        if start is None:
            if title and title.isdigit() and len(title) <= 4 and 1 <= int(title) <= 999:
                start = end = int(title)
                title = ''
            else:
                if title:
                    title = re.sub(r'^[　\s*+\-—–.]+', '', title).strip()
                    if title:
                        out.append('# 无页码(可能是续行，可并入上一行): %s' % title)
                        last = (pno, len(out) - 1, ind, title, False)
                continue
        # 粘连数字拆分（"8694" -> 86-94）
        if start == end and len(str(start)) >= 4:
            g = _split_glued(str(start))
            if g:
                start, end = g
            else:
                out.append('# 页码异常(第%d页): %s [%s]' % (pno, title or '?', _fmt_page(start, end)))
                continue
        if start < 1 or end > 2000 or start > end:
            out.append('# 页码异常(第%d页): %s [%s]' % (pno, title or '?', _fmt_page(start, end)))
            continue
        # 行首符号清洗（"-95 115" -> "95 115"）；清洗后纯数字视为无标题（页码被拆分）
        title = re.sub(r'^[　\s*+\-—–.]+', '', title).strip()
        if title and not title.isdigit():
            line = '%s%s %s' % ('　' * ind, title, _fmt_page(start, end))
            if sc < 0.6:
                line = '# 低置信度(%.2f): %s' % (sc, line)
            out.append(line)
            last = (pno, len(out) - 1, ind, title, True)
        else:
            # 无标题页码行：合并到同一页最近的有标题行（标题与页码被OCR分开识别）
            if last and last[0] == pno and not last[4]:
                idx, ind2, title2 = last[1], last[2], last[3]
                old = out[idx]
                if old.startswith('# 无页码'):
                    newline = '%s%s %s' % ('　' * ind2, title2, _fmt_page(start, end))
                else:
                    newline = old
                    newline = '%s %s' % (newline.rstrip(), _fmt_page(start, end))
                if sc < 0.6:
                    newline = '# 低置信度(%.2f): %s' % (sc, newline)
                out[idx] = newline
                last = (pno, idx, ind2, title2, True)
            else:
                out.append('# 缺标题(第%d页): %s' % (pno, _fmt_page(start, end)))
    return '\n'.join(out) + '\n'


def detect_offset(pdf_path, start_page, end_page, ocr=None, dpi=150, progress=None,
                  cancel_event=None, pause_event=None, skip_first=False):
    """自动检测印刷页->PDF页偏移量。

    原理：目录页之后是正文，正文每页底部有印刷页码（独立数字行）。
    对目录后的若干页扫描页脚数字，页码 num 出现在 PDF 索引 idx 时
    满足 idx = num + offset，取一致性最高的 offset 作为结果。

    skip_first=True：跳过目录页的重复OCR（调用方已识别过，仅作页脚扫描）。

    返回 (offset, n0)；失败返回 (None, None)。n0 为目录中最小的印刷页码(可能为None)。
    """
    if ocr is None:
        ocr = load_ocr()
    # 目录最小页码（参考值）
    n0 = None
    if not skip_first:
        try:
            entries = _extract_entries(pdf_path, start_page, end_page, ocr, dpi, progress,
                                       cancel_event, pause_event)
            nums = sorted({s for _, _, _, s, e, _ in entries if s and 1 <= s <= 300})
            if nums:
                n0 = nums[0]
        except Exception:
            pass
    # 扫描目录之后的正文页页脚
    doc = fitz.open(pdf_path)
    try:
        scan_end = min(end_page + 8, doc.page_count)
        s_total = scan_end - end_page
        cands = []
        for k, idx in enumerate(range(end_page, scan_end)):
            check_task(cancel_event, pause_event)
            if progress:
                progress(2 * s_total + k, 2 * s_total + s_total, '扫描页脚 %d/%d' % (k + 1, s_total))
            pix = doc[idx].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            h = pix.height
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            raw = ocr(arr)
            res = raw[0] if isinstance(raw, tuple) else raw
            if not res:
                continue
            for item in res:
                box, txt, sc = item[0], item[1], item[2]
                t = str(txt).strip()
                if not t.isdigit():
                    continue
                num = int(t)
                if not (1 <= num <= 500):
                    continue
                b = np.asarray(box, dtype=float)
                y_top = b[:, 1].min() / h
                if y_top <= 0.35:  # 页眉位置（印刷页码通常在页眉）
                    cands.append((idx, num))
        if cands:
            offsets = {}
            for idx, num in cands:
                o = idx - num
                offsets[o] = offsets.get(o, 0) + 1
            best = max(offsets.items(), key=lambda kv: kv[1])
            if best[1] >= 2 and 0 <= best[0] <= 300:
                return best[0], n0
    finally:
        doc.close()
    return None, n0
