# -*- coding: utf-8 -*-
"""核心逻辑：目录txt解析、书签写入/提取、txt导出、图片提取"""
import hashlib
import os
import re
import shutil
import time
from typing import Callable, List, Optional, Tuple

import fitz

# ---- 常量 ----
MAX_TOC_LEVEL = 3                # 目录支持的最大层级（PDF书签规范常见限制）
PAGE_NUMBER_MAX = 2000           # 合法印刷页码上限
OUTPUT_COPY_SUFFIX = '_带目录'    # 副本文件名后缀
IMAGE_OUTPUT_SUFFIX = '_图片'     # 图片输出目录后缀
JPEG_QUALITY_MIN = 1
JPEG_QUALITY_MAX = 100
IMAGE_FORMATS = frozenset({'orig', 'png', 'jpeg'})
EXTENSION_BY_FORMAT = {'png': 'png', 'jpeg': 'jpg', 'orig': ''}

KIND_PDF_PAGE = 'pdf'      # 页码类型：直接PDF页号 [pN]
KIND_PRINT_PAGE = 'print'  # 页码类型：印刷页码（写入时需加偏移）

# 目录txt行格式（全角空格缩进 + 标题 + 页码）
RE_RANGE = re.compile(r'^(　*)(.*?)[…\.]*\s*(\d+)\s*[—–\-]\s*(\d+)\s*$')
RE_SINGLE = re.compile(r'^(　*)(.*?)[…\.]*\s*(\d+)\s*$')
RE_PDF_PAGE = re.compile(r'^(　*)(.*?)[…\.]*\s*\[p(\d+)\]\s*$')
RE_TOC_TITLE = re.compile(r'^\s*目\s*录\s*$')
RE_YEAR_RANGE = re.compile(r'^\s*\d{4}\s*[—–\-]\s*\d{4}\s*年?\s*$')
RE_FULLWIDTH_INDENT = re.compile(r'^(　*)')
RE_TRAILING_DOTS = re.compile(r'[…\.]+$')
RE_LOW_CONFIDENCE = re.compile(r'^#\s*低置信度\(\d*\.?\d+\):\s*(.*)$')


class TaskCancelled(Exception):
    """任务被用户取消"""
    pass


def check_task(cancel_event=None, pause_event=None) -> None:
    """每页循环中调用：cancel_event 置位则抛 TaskCancelled；pause_event 置位则阻塞等待。

    边界情况：暂停期间用户点停止（cancel_event 置位）应立即取消，而不是继续阻塞。
    """
    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelled()
    if pause_event is not None:
        while pause_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                raise TaskCancelled()
            time.sleep(0.1)


def _mp_extract_images(q, pdf_path, dpi, fmt, quality, cancel_event, pause_event,
                       out_dir=None) -> None:
    """子进程入口：提取图片，进度/结果经 multiprocessing.Queue 回传

    out_dir 可选：为 None 时默认 PDF 同目录的 "<书名>_图片" 文件夹
    """
    try:
        out_dir, image_count = extract_images(
            pdf_path, out_dir, dpi, fmt, quality,
            progress=lambda done, total, message: q.put(('progress', done, total, message)),
            cancel_event=cancel_event, pause_event=pause_event)
        q.put(('done', out_dir, image_count))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))


def _mp_write_toc(q, pdf_path, toc, out_mode) -> None:
    """子进程入口：写入书签，结果经 multiprocessing.Queue 回传"""
    try:
        output_path = write_toc(pdf_path, toc, out_mode)
        doc = fitz.open(output_path)
        try:
            bookmark_count = len(doc.get_toc())
        finally:
            doc.close()
        q.put(('done_write', output_path, bookmark_count))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))


def _mp_extract_toc(q, path, page_fmt) -> None:
    """子进程入口：提取PDF书签或电子书目录 -> ('extract_done', is_ebook, entries, out)"""
    import ebook
    try:
        base_name, extension = os.path.splitext(path)
        is_ebook = ebook.is_ebook(path)
        if is_ebook:
            entries = ebook.extract_toc(path)
            out = base_name + '_目录.txt'
            with open(out, 'w', encoding='utf-8-sig') as file_handle:
                file_handle.write(ebook.to_txt(entries))
            q.put(('extract_done', True, entries, out))
            return
        toc = read_toc(path)
        if not toc:
            q.put(('extract_none',))
            return
        out = base_name + '_书签' + extension.replace('.pdf', '.txt')
        export_toc_txt(toc, out, page_fmt)
        q.put(('extract_done', False, toc, out))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))


def parse_toc(txt_path: str) -> List[List]:
    """解析目录txt -> [[indent, title, page_kind, page_number]]

    规则：
    - 行首全角空格缩进决定层级（0级=1级标题）
    - 无页码的行视为上一标题的续行，自动拼接
    - `#` 开头为提示行：低置信度行剥去前缀保留，其余（无页码/缺标题/页码异常）忽略
    - page_kind: KIND_PDF_PAGE（直接PDF页号）或 KIND_PRINT_PAGE（印刷页码，需加偏移）
    """
    parsed_entries: List[List] = []
    pending_line: Optional[List] = None  # [缩进, 累计文本]：等待页码的续行
    with open(txt_path, encoding='utf-8-sig') as file_handle:
        for raw_line in file_handle:
            line = raw_line.rstrip('\r\n')
            if not line.strip():
                continue
            if line.startswith('#'):
                low_confidence_match = RE_LOW_CONFIDENCE.match(line)
                if not low_confidence_match:
                    continue
                line = low_confidence_match.group(1)
            if RE_TOC_TITLE.match(line) or RE_YEAR_RANGE.match(line):
                continue
            pdf_page_match = RE_PDF_PAGE.match(line)
            if pdf_page_match:
                indent_level = len(pdf_page_match.group(1))
                title = pdf_page_match.group(2)
                page_number = int(pdf_page_match.group(3))
                page_kind = KIND_PDF_PAGE
            else:
                range_match = RE_RANGE.match(line) or RE_SINGLE.match(line)
                if not range_match:
                    # 无页码的行：作为续行暂存，等待后续行提供页码
                    indent_match = RE_FULLWIDTH_INDENT.match(line)
                    pending_indent = len(indent_match.group(1))
                    if pending_line is None:
                        pending_line = [pending_indent, line.strip()]
                    else:
                        pending_line[1] += line.strip()
                    continue
                indent_level = len(range_match.group(1))
                title = range_match.group(2)
                page_number = int(range_match.group(3))
                page_kind = KIND_PRINT_PAGE
            if pending_line is not None:
                title = pending_line[1] + title
                indent_level = pending_line[0]
                pending_line = None
            title = RE_TRAILING_DOTS.sub('', title).strip()
            if title:
                parsed_entries.append([indent_level, title, page_kind, page_number])
    if pending_line is not None:
        raise ValueError('目录文件末尾存在未完成的续行: %r' % pending_line[:60])
    return parsed_entries


def levels_from_indent(entries: List[List]) -> List[List]:
    """缩进 -> 层级：0级=L1，1级=L2，>=2级=L3（封顶MAX_TOC_LEVEL）"""
    converted_entries: List[List] = []
    for indent_level, title, page_kind, page_number in entries:
        converted_entries.append(
            [min(indent_level + 1, MAX_TOC_LEVEL), title, page_kind, page_number])
    return converted_entries


def build_toc(entries: List[List], offset: int) -> List[List]:
    """(已含level的entries) -> [[level, title, pdf_page]]

    层级平滑：不允许跳级（L1 直接到 L3 会被压到 L2），避免 PDF 报 bad hierarchy level。
    pdf_page = 印刷页码 + offset + 1（offset 为 0-based PDF 索引差）。
    """
    built_toc: List[List] = []
    previous_level = 0
    for level, title, page_kind, page_number in entries:
        if level > previous_level + 1:
            level = previous_level + 1
        previous_level = max(level, 1)
        if page_kind == KIND_PDF_PAGE:
            pdf_page = page_number
        else:
            pdf_page = page_number + offset + 1
        if pdf_page < 1:
            raise ValueError('页码越界: %s [%d]' % (title, page_number))
        built_toc.append([level, title, pdf_page])
    return built_toc


def write_toc(pdf_path: str, toc: List[List], out_mode: str = 'copy') -> str:
    """写入书签，返回输出文件路径。

    out_mode: copy=生成副本（原名_带目录.pdf，原文件不动）；same=直接增量写原文件。
    使用增量保存，未修改的页面数据不动，速度更快且不重新压缩。
    """
    target_path = pdf_path
    if out_mode == 'copy':
        base_name, extension = os.path.splitext(pdf_path)
        target_path = base_name + OUTPUT_COPY_SUFFIX + extension
        shutil.copyfile(pdf_path, target_path)
    doc = fitz.open(target_path)
    try:
        doc.set_toc(toc)
        doc.save(target_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    finally:
        doc.close()
    return target_path


def read_toc(pdf_path: str) -> List[List]:
    """提取PDF书签 -> [[level, title, pdf_page]]"""
    doc = fitz.open(pdf_path)
    try:
        return doc.get_toc()
    finally:
        doc.close()


def extract_images(pdf_path: str, out_dir: Optional[str] = None, dpi: Optional[int] = None,
                   fmt: str = 'orig', quality: int = 85, progress: Optional[Callable] = None,
                   cancel_event=None, pause_event=None) -> Tuple[str, int]:
    """提取PDF图片 -> (输出目录, 图片数)

    - dpi=None：提取内嵌图片（fmt='orig' 原样保存，或转 png/jpeg）
    - dpi=整数：按分辨率渲染每页为图片（fmt: png/jpeg，默认 png）
    - quality：JPEG质量(1-100)；progress(done, total, msg)：进度回调
    - cancel_event/pause_event：threading.Event，支持停止/暂停（页间生效）
    """
    fmt = (fmt or 'orig').lower()
    if fmt not in IMAGE_FORMATS:
        raise ValueError('不支持的图片格式: %s（支持 %s）' % (fmt, '/'.join(sorted(IMAGE_FORMATS))))
    if fmt == 'orig' and dpi:
        fmt = 'png'
    if not (JPEG_QUALITY_MIN <= quality <= JPEG_QUALITY_MAX):
        raise ValueError('JPEG质量应在 %d-%d 之间' % (JPEG_QUALITY_MIN, JPEG_QUALITY_MAX))
    if out_dir is None:
        base_name, _ = os.path.splitext(pdf_path)
        out_dir = base_name + IMAGE_OUTPUT_SUFFIX
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        if dpi:
            image_count = _export_rendered_pages(doc, out_dir, dpi, fmt, quality,
                                                 progress, cancel_event, pause_event)
        else:
            image_count = _export_inline_images(doc, out_dir, fmt, quality,
                                                progress, cancel_event, pause_event)
    finally:
        doc.close()
    return out_dir, image_count


def _export_rendered_pages(doc, out_dir: str, dpi: int, fmt: str, quality: int,
                           progress: Optional[Callable], cancel_event, pause_event) -> int:
    """按分辨率渲染每页 -> 图片文件，返回导出数"""
    total_pages = doc.page_count
    exported_count = 0
    for page_index in range(total_pages):
        check_task(cancel_event, pause_event)
        pixmap = doc[page_index].get_pixmap(dpi=dpi, alpha=(fmt != 'jpeg'))
        file_name = '第%03d页.%s' % (page_index + 1, EXTENSION_BY_FORMAT[fmt])
        pixmap.save(os.path.join(out_dir, file_name), jpg_quality=quality)
        exported_count += 1
        if progress:
            progress(page_index + 1, total_pages, '渲染第 %d/%d 页' % (page_index + 1, total_pages))
    return exported_count


def _extract_single_image(doc, xref: int, fmt: str, quality: int) -> Optional[bytes]:
    """提取/转换单张内嵌图片 -> 字节数据。

    边界情况：单张图片损坏或格式不支持时返回 None（跳过该图，不影响整体提取）。
    """
    try:
        if fmt == 'orig':
            image_info = doc.extract_image(xref)
            return image_info['image']
        pixmap = fitz.Pixmap(doc, xref)
        if fmt == 'jpeg' and pixmap.alpha:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        return pixmap.tobytes(fmt, jpg_quality=quality)
    except (ValueError, KeyError, RuntimeError):
        return None


def _export_inline_images(doc, out_dir: str, fmt: str, quality: int,
                          progress: Optional[Callable], cancel_event, pause_event) -> int:
    """提取PDF内嵌图片（按内容MD5去重），返回导出数"""
    saved_digest_set = set()
    exported_count = 0
    total_pages = doc.page_count
    for page_index in range(total_pages):
        check_task(cancel_event, pause_event)
        for image_ref in doc.get_page_images(page_index, full=True):
            xref_number = image_ref[0]
            image_data = _extract_single_image(doc, xref_number, fmt, quality)
            if image_data is None:
                continue
            digest = hashlib.md5(image_data).hexdigest()
            if digest in saved_digest_set:
                continue
            saved_digest_set.add(digest)
            exported_count += 1
            file_name = '第%03d页_%03d_%s.%s' % (
                page_index + 1, exported_count, xref_number, EXTENSION_BY_FORMAT[fmt])
            with open(os.path.join(out_dir, file_name), 'wb') as file_handle:
                file_handle.write(image_data)
        if progress:
            progress(page_index + 1, total_pages, '扫描第 %d/%d 页' % (page_index + 1, total_pages))
    return exported_count


def export_toc_txt(toc: List[List], out_path: str, with_pdf_page: bool = True) -> None:
    """书签导出为txt。缩进2空格/级；with_pdf_page 时行尾带 [p页号]"""
    with open(out_path, 'w', encoding='utf-8-sig') as file_handle:
        for level, title, page in toc:
            indent_text = '  ' * (level - 1)
            if with_pdf_page:
                file_handle.write('%s%s [p%d]\n' % (indent_text, title, page))
            else:
                file_handle.write('%s%s %d\n' % (indent_text, title, page))
