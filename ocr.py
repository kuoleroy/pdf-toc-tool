# -*- coding: utf-8 -*-
"""OCR 识别目录页：渲染PDF页 -> OCR -> 缩进式txt + 自动检测页码偏移

OCR 后端：RapidOCR（PaddleOCR 模型权重 + onnxruntime 推理）。
paddle 3.x CPU 推理存在 oneDNN/PIR 不兼容 bug（每页约 50 秒且无法启用加速），
故采用同款模型、速度 10 倍以上的 RapidOCR。

页码约定：
  目录页码范围使用 PDF 页号（1-based，直接对应阅读器/PDF查看器显示的页码）。
  目录条目的行尾数字是"印刷页码"，写入书签时需要偏移量 offset（印刷页p -> PDF索引 = p + offset）。
  detect_offset() 可从目录条目最小页码 + 正文起始页自动推算偏移。

程序为单一带OCR版本，本模块在运行环境缺少 rapidocr-onnxruntime 时，
OCR功能调用会抛出清晰错误提示安装依赖，其余功能不受影响。
"""
import re
import time
from typing import Callable, List, Optional, Tuple

import fitz
import numpy as np

from core import check_task

HAS_OCR = False
_POCR_MODULE = None
try:
    from rapidocr_onnxruntime import RapidOCR as _POCR_MODULE
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

# ---- 常量 ----
RE_DOTS = re.compile(r'[….\.·]{2,}')
RE_PAGE_RANGE_TAIL = re.compile(r'(\d+)\s*[—–\-]{1,3}\s*(\d+)\s*$')
RE_TWO_NUMBERS_TAIL = re.compile(r'(\d+)\s+(\d+)\s*$')
RE_SINGLE_NUMBER_TAIL = re.compile(r'(\d+)\s*$')
RE_LEADING_SYMBOLS = re.compile(r'^[　\s*+\-—–.]+')

INDENT_STEP_PIXELS = 18.0     # 缩进推断的坐标步长（px）
LOW_CONFIDENCE_THRESHOLD = 0.6
PAGE_NUMBER_MIN = 1
PAGE_NUMBER_MAX = 2000
NUMERIC_TITLE_MAX_LEN = 4     # 纯数字标题视为页码的最大位数
NUMERIC_TITLE_MAX = 999
GLUED_PAGE_NUMBER_MAX = 999   # 粘连页码拆分上限
GLUED_PAGE_SPAN_MAX = 600     # 粘连页码允许的最大跨度
FOOTER_SCAN_PAGES = 8         # 页脚扫描的正文页数
FOOTER_HEADER_Y_RATIO = 0.35  # 印刷页码所在位置（页面高度比例以内视为页眉）
FOOTER_NUMBER_MAX = 500       # 页脚合法页码上限
TOC_PAGE_NUMBER_REF_MAX = 300 # 目录页码参考值上限
OFFSET_MAX = 300              # 偏移量合法上限
OFFSET_MIN_VOTES = 2          # 偏移最少需要的一致票数
OCR_DPI_DEFAULT = 150

_ocr_instances = {}


def _mp_ocr_task(q, pdf_path, start_page, end_page, cancel_event, pause_event, offset) -> None:
    """子进程入口：OCR目录页（用给定偏移），结果经 multiprocessing.Queue 回传

    进度：渲染阶段 0~T，识别阶段 T~2T（T=页数）。结果消息 ('ocr_done', text)。
    offset 由调用方在识别前填写正文第一页两框计算，行尾直接输出 [pN] PDF页号免偏移。
    """
    try:
        engine = load_ocr()
        text = ocr_to_txt(pdf_path, start_page, end_page, ocr=engine,
                          offset=offset,
                          progress=lambda done, total, message: q.put(('progress', done, total, message)),
                          cancel_event=cancel_event, pause_event=pause_event)
        # 目录第一行常无页码，补"输入范围开头"的页号兜底（用户以输入开头为基准），
        # 保证首条书签至少带一个可用的PDF页号；pdf_pages=True 输出 [pN] 免偏移
        text = apply_first_line_page_fallback(text, start_page, pdf_pages=True)
        q.put(('ocr_done', text))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))


def _mp_ocr_text_task(q, pdf_path, start_page, end_page, cancel_event, pause_event,
                      with_page_marks=False) -> None:
    """子进程入口：OCR任意页面的纯文字提取，结果经 multiprocessing.Queue 回传

    进度：渲染阶段 0~T，识别阶段 T~2T（T=页数）。结果消息 ('ocr_text_done', text)。
    """
    try:
        engine = load_ocr()
        text = extract_text(pdf_path, start_page, end_page, ocr=engine,
                            progress=lambda done, total, message: q.put(('progress', done, total, message)),
                            cancel_event=cancel_event, pause_event=pause_event,
                            with_page_marks=with_page_marks)
        q.put(('ocr_text_done', text))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))


def load_ocr():
    """加载（并缓存）OCR 引擎实例；首次运行自动下载模型，需联网"""
    global _ocr_instances
    if not HAS_OCR or _POCR_MODULE is None:
        raise RuntimeError('OCR组件不可用：缺少依赖，请运行 pip install rapidocr-onnxruntime 后重试')
    key = 'default'
    if key not in _ocr_instances:
        _ocr_instances[key] = _POCR_MODULE()
    return _ocr_instances[key]


def render_pages(pdf_path: str, start_page: int, end_page: int, dpi: int = OCR_DPI_DEFAULT,
                 cancel_event=None, pause_event=None,
                 progress: Optional[Callable] = None) -> List[Tuple[int, np.ndarray]]:
    """渲染PDF页(1-based PDF页号) -> [(pdf_page_no, numpy RGB图像)]

    progress(done, total, msg)：每渲染一页回调一次
    """
    doc = fitz.open(pdf_path)
    rendered_pages: List[Tuple[int, np.ndarray]] = []
    try:
        total_pages = max(end_page - start_page + 1, 0)
        for page_number in range(start_page, end_page + 1):
            page_index = page_number - 1
            if page_index < 0 or page_index >= doc.page_count:
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('任务已取消')
            if pause_event is not None:
                while pause_event.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError('任务已取消')
                    time.sleep(0.1)
            pixmap = doc[page_index].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            pixel_array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, 3)
            rendered_pages.append((page_index + 1, pixel_array))
            if progress:
                progress(len(rendered_pages), total_pages,
                         '渲染第 %d/%d 页' % (len(rendered_pages), total_pages))
    finally:
        doc.close()
    return rendered_pages


def _ocr_images(ocr_engine, images: List[Tuple[int, np.ndarray]],
                progress: Optional[Callable] = None, cancel_event=None,
                pause_event=None) -> List[Tuple[int, float, str, float]]:
    """逐张OCR -> [(pdf_page_no, box_x, text, score)]；progress 每识别一页回调一次"""
    recognized_lines: List[Tuple[int, float, str, float]] = []
    total_images = len(images)
    for image_index, (page_number, image_array) in enumerate(images):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('任务已取消')
        if pause_event is not None:
            while pause_event.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError('任务已取消')
                time.sleep(0.1)
        raw_output = ocr_engine(image_array)
        ocr_results = raw_output[0] if isinstance(raw_output, tuple) else raw_output
        if not ocr_results:
            if progress:
                progress(image_index + 1, total_images,
                         '识别第 %d/%d 页：无文字' % (image_index + 1, total_images))
            continue
        for result_item in ocr_results:
            bbox, text, score = result_item[0], result_item[1], result_item[2]
            if not text:
                continue
            x_position = 0.0
            try:
                x_position = float(np.asarray(bbox).reshape(-1)[0])
            except (ValueError, TypeError):
                pass
            recognized_lines.append(
                (page_number, x_position, str(text), float(score)))
        if progress:
            progress(image_index + 1, total_images,
                     '识别第 %d/%d 页：%d 行' % (image_index + 1, total_images, len(ocr_results)))
    return recognized_lines


def _infer_indent_level(x_position: float, min_x_position: Optional[float],
                        step: float = INDENT_STEP_PIXELS) -> int:
    """按 x 坐标相对最小x的偏移推断缩进级"""
    if min_x_position is None:
        return 0
    offset_from_min = x_position - min_x_position
    if offset_from_min < step * 0.6:
        return 0
    if offset_from_min < step * 2.2:
        return 1
    return 2


def _extract_entries(pdf_path: str, start_page: int, end_page: int, ocr_engine, dpi: int,
                     progress: Optional[Callable] = None, cancel_event=None,
                     pause_event=None) -> List[Tuple[int, int, str, Optional[int], Optional[int], float]]:
    """OCR目录页 -> [(pdf_page_no, indent, title, start_num, end_num, score)]

    title 可为空（纯页码行）；start/end 为印刷页码，None 表示该行无页码。
    进度：渲染阶段 0~T，识别阶段 T~2T（T=页数）
    """
    total_pages = end_page - start_page + 1
    rendered_images = render_pages(
        pdf_path, start_page, end_page, dpi, cancel_event, pause_event,
        progress and (lambda done, total, message: progress(done, 2 * total_pages, message)))
    if not rendered_images:
        raise ValueError('页码范围无效：PDF页 %d-%d 不存在' % (start_page, end_page))
    recognized_lines = _ocr_images(
        ocr_engine, rendered_images,
        progress and (lambda done, total, message: progress(total_pages + done, 2 * total_pages, message)),
        cancel_event, pause_event)
    if not recognized_lines:
        raise ValueError('OCR未识别到任何文字（请确认页码范围是否为目录页）')
    page_rows: dict = {}
    for page_number, x_position, text, score in recognized_lines:
        page_rows.setdefault(page_number, []).append((x_position, text, score))
    extracted_entries: List[Tuple[int, int, str, Optional[int], Optional[int], float]] = []
    for page_number in sorted(page_rows):
        rows = page_rows[page_number]
        min_x_position = min(row[0] for row in rows)
        for x_position, text, score in rows:
            cleaned_text = RE_DOTS.sub('', text).strip()
            if not cleaned_text:
                continue
            # 目录页自身的"目录"标题行：跳过，由 ocr_to_txt 无条件写死输出
            if re.match(r'^\s*目\s*录\s*$', cleaned_text):
                continue
            title = cleaned_text
            start_number = end_number = None
            page_match = (RE_PAGE_RANGE_TAIL.search(cleaned_text)
                          or RE_TWO_NUMBERS_TAIL.search(cleaned_text))
            if page_match:
                start_number = int(page_match.group(1))
                end_number = int(page_match.group(2))
                title = cleaned_text[:page_match.start()]
            else:
                page_match = RE_SINGLE_NUMBER_TAIL.search(cleaned_text)
                if page_match:
                    start_number = end_number = int(page_match.group(1))
                    title = cleaned_text[:page_match.start()]
            title = title.rstrip('　 .…·:：-—–（(').strip()
            extracted_entries.append((
                page_number, _infer_indent_level(x_position, min_x_position),
                title, start_number, end_number, score))
    return extracted_entries


def _format_page_number(start_number: Optional[int], end_number: Optional[int],
                        offset: Optional[int] = None) -> str:
    """印刷页码格式化：以起始页码为准（识别的范围如 19-22 只取 19）

    offset 提供时输出 [pN]（PDF页号 = 印刷页码 + offset + 1，写入书签免偏移）；
    否则输出印刷页码数字。无页码返回空串。
    """
    if start_number is None:
        return ''
    if offset is None:
        return str(start_number)
    return '[p%d]' % (start_number + offset + 1)


def _split_glued_page_numbers(glued_text: str) -> Optional[Tuple[int, int]]:
    """粘连页码拆分："8694" -> (86, 94)；"390451" -> (390, 451)。

    取所有分法中跨度最短且合法的 (a, b)，要求 1<=a<b<=GLUED_PAGE_NUMBER_MAX
    且 b-a<=GLUED_PAGE_SPAN_MAX。
    """
    best_split: Optional[Tuple[int, int]] = None
    for split_index in range(1, len(glued_text)):
        first_number, second_number = int(glued_text[:split_index]), int(glued_text[split_index:])
        if (PAGE_NUMBER_MIN <= first_number < second_number <= GLUED_PAGE_NUMBER_MAX
                and second_number - first_number <= GLUED_PAGE_SPAN_MAX):
            if (best_split is None
                    or (second_number - first_number) < (best_split[1] - best_split[0])):
                best_split = (first_number, second_number)
    return best_split


def _append_low_confidence(output_lines: List[str], text: str, score: float) -> None:
    """低置信度行加 '#' 前缀（提示用户核对，但解析时仍会保留）"""
    if score < LOW_CONFIDENCE_THRESHOLD:
        output_lines.append('# 低置信度(%.2f): %s' % (score, text))
    else:
        output_lines.append(text)


def apply_first_line_page_fallback(ocr_text: str, fallback_page: int,
                                   pdf_pages: bool = False) -> str:
    """OCR结果首行页码无条件以输入范围开头为准（不管OCR识别到没有）

    回退页码取"输入范围的开头"（用户以输入开头为基准）：
    目录首条通常对应正文起始，补上后配合偏移即可定位到正文首页。
    pdf_pages=True 时输出 [pN]（PDF页号免偏移）；否则输出印刷页码数字。
    处理两类首行：
      1. "# 无页码(...): 标题" 提示行 -> 去掉前缀并在标题后补页码
      2. 普通标题行 -> 行尾页码替换为范围开头；无页码则补上
    以 # 开头的其他提示行（缺标题/低置信度）与写死的"目录"行保持原样。
    """
    output_lines = ocr_text.split('\n')
    for line_index, line in enumerate(output_lines):
        if not line.strip():
            continue
        if line.startswith('#'):
            no_page_match = re.match(r'^# 无页码.*?:[ ]*(.+)$', line)
            if no_page_match:
                title_with_indent = no_page_match.group(1).rstrip()
                # 保留行首全角缩进（层级信息），仅去尾部空白
                if title_with_indent.strip():
                    output_lines[line_index] = '%s ..... [p%d]' % (title_with_indent,
                                                                   fallback_page) \
                        if pdf_pages else '%s ..... %d' % (title_with_indent, fallback_page)
            break
        # 写死的"目录"行（[p范围开头]，PDF页号免偏移）：跳过，不参与页码回退
        if re.match(r'^\s*目\s*录\s*[….…]*\s*\[p\d+\]\s*$', line):
            continue
        if re.search(r'\d+\s*$', line):
            # 行尾已有页码：无条件替换为范围开头
            output_lines[line_index] = re.sub(
                r'(\s*[…\.]*)\[p\d+\]\s*$' if pdf_pages else r'(\s*[…\.]*)\d+\s*$',
                lambda match: match.group(1) + ('[p%d]' % fallback_page if pdf_pages
                                                else str(fallback_page)), line)
        else:
            output_lines[line_index] = '%s ..... [p%d]' % (line.rstrip(), fallback_page) \
                if pdf_pages else '%s ..... %d' % (line.rstrip(), fallback_page)
        break
    return '\n'.join(output_lines)


def ocr_to_txt(pdf_path: str, start_page: int, end_page: int, ocr=None, dpi: int = OCR_DPI_DEFAULT,
               progress: Optional[Callable] = None, cancel_event=None,
               pause_event=None, offset: Optional[int] = None) -> str:
    """OCR目录页(PDF页号) -> 缩进式txt文本（全角空格缩进 + 标题 + 点线 + 页码）

    ocr 可传入已加载的引擎实例（load_ocr()），否则自动加载。
    说明：# 开头为提示行（无页码/缺标题/低置信度/页码异常），可删除或修改。
    offset 提供时行尾输出 [pN]（PDF页号，写入书签免偏移）；否则输出印刷页码数字
    （写入书签时需偏移）。"目录"行无条件写死为 [p范围开头]（PDF页号）。
    """
    if ocr is None:
        ocr = load_ocr()
    entries = _extract_entries(pdf_path, start_page, end_page, ocr, dpi, progress,
                               cancel_event, pause_event)
    if not entries:
        raise ValueError('OCR未识别到任何目录条目')
    output_lines: List[str] = []
    # (页码, 输出行索引, 缩进, 标题, 是否已有页码)：最近的有标题行，供无标题页码行合并
    last_titled_entry = None
    for page_number, indent_level, title, start_number, end_number, score in entries:
        if start_number is None:
            # 纯数字标题视为被拆分出的页码（如 OCR 把 "86 94" 读成一行 "8694" 前的数字行）
            if title and title.isdigit() and len(title) <= NUMERIC_TITLE_MAX_LEN \
                    and PAGE_NUMBER_MIN <= int(title) <= NUMERIC_TITLE_MAX:
                start_number = end_number = int(title)
                title = ''
            else:
                if title:
                    cleaned_title = RE_LEADING_SYMBOLS.sub('', title).strip()
                    if cleaned_title:
                        output_lines.append('# 无页码(可能是续行，可并入上一行): %s' % cleaned_title)
                        last_titled_entry = (page_number, len(output_lines) - 1,
                                             indent_level, cleaned_title, False)
                continue
        # 粘连数字拆分（"8694" -> 86-94）
        if start_number == end_number and len(str(start_number)) >= 4:
            split_result = _split_glued_page_numbers(str(start_number))
            if split_result:
                start_number, end_number = split_result
            else:
                output_lines.append('# 页码异常(第%d页): %s %s' % (
                    page_number, title or '?', _format_page_number(start_number, end_number, offset)))
                continue
        if start_number < PAGE_NUMBER_MIN or end_number > PAGE_NUMBER_MAX \
                or start_number > end_number:
            output_lines.append('# 页码异常(第%d页): %s %s' % (
                page_number, title or '?', _format_page_number(start_number, end_number, offset)))
            continue
        # 行首符号清洗（"-95 115" -> "95 115"）；清洗后纯数字视为无标题（页码被拆分）
        title = RE_LEADING_SYMBOLS.sub('', title).strip()
        if title and not title.isdigit():
            line_text = '%s%s ..... %s' % ('　' * indent_level, title,
                                           _format_page_number(start_number, end_number, offset))
            _append_low_confidence(output_lines, line_text, score)
            last_titled_entry = (page_number, len(output_lines) - 1,
                                 indent_level, title, True)
        else:
            # 无标题页码行：合并到同一页最近的无页码标题行（标题与页码被OCR分开识别）
            if (last_titled_entry and last_titled_entry[0] == page_number
                    and not last_titled_entry[4]):
                line_index, merge_indent, merge_title = (last_titled_entry[1],
                                                         last_titled_entry[2],
                                                         last_titled_entry[3])
                existing_line = output_lines[line_index]
                if existing_line.startswith('# 无页码'):
                    new_line = '%s%s ..... %s' % ('　' * merge_indent, merge_title,
                                                  _format_page_number(start_number, end_number, offset))
                else:
                    new_line = '%s %s' % (existing_line.rstrip(),
                                          _format_page_number(start_number, end_number, offset))
                _append_low_confidence(output_lines, new_line, score)
                last_titled_entry = (page_number, line_index, merge_indent, merge_title, True)
            else:
                output_lines.append('# 缺标题(第%d页): %s' % (
                    page_number, _format_page_number(start_number, end_number, offset)))
    # 无条件写死"目录"标题行（识别到了也被覆盖）：页码用[p范围开头]（PDF页号，免偏移）
    output_lines.insert(0, '目录 ..... [p%d]' % start_page)
    return '\n'.join(output_lines) + '\n'


def extract_text(pdf_path: str, start_page: int, end_page: int, ocr=None,
                 dpi: int = OCR_DPI_DEFAULT, progress: Optional[Callable] = None,
                 cancel_event=None, pause_event=None,
                 with_page_marks: bool = False) -> str:
    """OCR任意页码范围(PDF页号) -> 纯文字txt（按阅读顺序逐行输出）

    与 ocr_to_txt 不同：不做目录/页码解析，仅按行输出识别文字，
    适合识别正文等任意页面。with_page_marks=True 时每页前加 [第N页] 标记行。
    ocr 可传入已加载的引擎实例（load_ocr()）。
    """
    if ocr is None:
        ocr = load_ocr()
    total_pages = end_page - start_page + 1
    rendered_images = render_pages(
        pdf_path, start_page, end_page, dpi, cancel_event, pause_event,
        progress and (lambda done, total, message: progress(done, 2 * total_pages, message)))
    if not rendered_images:
        raise ValueError('页码范围无效：PDF页 %d-%d 不存在' % (start_page, end_page))
    recognized_lines = _ocr_images(
        ocr, rendered_images,
        progress and (lambda done, total, message: progress(total_pages + done, 2 * total_pages, message)),
        cancel_event, pause_event)
    output_lines: List[str] = []
    recognized_pages = sorted({line_page_number for line_page_number, _x, _t, _s in recognized_lines})
    for page_number in recognized_pages:
        if with_page_marks:
            output_lines.append('')
            output_lines.append('[第%d页]' % page_number)
        seen_lines: set = set()
        for page_number_2, _x, text, _score in recognized_lines:
            if page_number_2 != page_number:
                continue
            page_line = text.strip()
            # 同页相同文本行只保留首次（OCR偶发重复识别同一行）
            if page_line and page_line not in seen_lines:
                seen_lines.add(page_line)
                output_lines.append(page_line)
    return '\n'.join(output_lines).strip() + '\n'


def detect_offset(pdf_path: str, start_page: int, end_page: int, ocr=None,
                  dpi: int = OCR_DPI_DEFAULT, progress: Optional[Callable] = None,
                  cancel_event=None, pause_event=None, skip_first: bool = False
                  ) -> Tuple[Optional[int], Optional[int]]:
    """自动检测印刷页->PDF页偏移量。

    原理：目录页之后是正文，正文每页页眉有印刷页码（独立数字行）。
    对目录后的若干页扫描页眉数字，页码 printed_number 出现在 PDF 索引 page_index 时
    满足 page_index = printed_number + offset，取一致性最高的 offset 作为结果。

    skip_first=True：跳过目录页的重复OCR（调用方已识别过，仅作页脚扫描）。
    返回 (offset, first_printed_number)；失败返回 (None, None)。
    first_printed_number 为目录中最小的印刷页码（可能为None）。
    """
    if ocr is None:
        ocr = load_ocr()
    first_printed_number = None
    if not skip_first:
        try:
            entries = _extract_entries(pdf_path, start_page, end_page, ocr, dpi, progress,
                                       cancel_event, pause_event)
            printed_numbers = sorted(
                {start for _, _, _, start, end, _ in entries
                 if start and PAGE_NUMBER_MIN <= start <= TOC_PAGE_NUMBER_REF_MAX})
            if printed_numbers:
                first_printed_number = printed_numbers[0]
        except (ValueError, RuntimeError):
            # 目录识别失败时仅跳过参考值计算，仍可继续页脚扫描
            pass
    # 扫描目录之后的正文页页眉
    doc = fitz.open(pdf_path)
    try:
        scan_end = min(end_page + FOOTER_SCAN_PAGES, doc.page_count)
        scan_total = scan_end - end_page
        candidates: List[Tuple[int, int]] = []
        for scan_index, page_index in enumerate(range(end_page, scan_end)):
            check_task(cancel_event, pause_event)
            if progress:
                progress(2 * scan_total + scan_index, 2 * scan_total + scan_total,
                         '扫描页脚 %d/%d' % (scan_index + 1, scan_total))
            pixmap = doc[page_index].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            page_height = pixmap.height
            pixel_array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, 3)
            raw_output = ocr(pixel_array)
            ocr_results = raw_output[0] if isinstance(raw_output, tuple) else raw_output
            if not ocr_results:
                continue
            for result_item in ocr_results:
                bbox, text, score = result_item[0], result_item[1], result_item[2]
                stripped_text = str(text).strip()
                if not stripped_text.isdigit():
                    continue
                printed_number = int(stripped_text)
                if not (PAGE_NUMBER_MIN <= printed_number <= FOOTER_NUMBER_MAX):
                    continue
                bbox_array = np.asarray(bbox, dtype=float)
                header_y_ratio = bbox_array[:, 1].min() / page_height
                if header_y_ratio <= FOOTER_HEADER_Y_RATIO:
                    candidates.append((page_index, printed_number))
        if candidates:
            offset_votes: dict = {}
            for page_index, printed_number in candidates:
                offset_candidate = page_index - printed_number
                offset_votes[offset_candidate] = offset_votes.get(offset_candidate, 0) + 1
            best_offset = max(offset_votes.items(), key=lambda vote_item: vote_item[1])
            if best_offset[1] >= OFFSET_MIN_VOTES and 0 <= best_offset[0] <= OFFSET_MAX:
                return best_offset[0], first_printed_number
    finally:
        doc.close()
    return None, first_printed_number
