# -*- coding: utf-8 -*-
"""核心逻辑：目录txt解析、书签写入/提取、txt导出、图片提取"""
import hashlib
import os
import re
import shutil
import struct
import time
import zipfile
from typing import Callable, List, Optional, Tuple

import fitz

# ---- 常量 ----
MAX_TOC_LEVEL = 3                # 目录支持的最大层级（PDF书签规范常见限制）
PAGE_NUMBER_MAX = 2000           # 合法印刷页码上限
OUTPUT_COPY_SUFFIX = '_带目录'    # 副本文件名后缀
IMAGE_OUTPUT_SUFFIX = '_图片'     # 图片输出目录后缀
JPEG_QUALITY_MIN = 1
JPEG_QUALITY_MAX = 100
DPI_MIN = 1
DPI_MAX = 1000
IMAGE_FORMATS = frozenset({'orig', 'png', 'jpeg'})
EXTENSION_BY_FORMAT = {'png': 'png', 'jpeg': 'jpg', 'orig': ''}
EPUB_EXTENSION = '.epub'         # EPUB内嵌图片走zip解压提取（HTML/SVG引用的位图）
MOBI_EXTENSIONS = frozenset({'.mobi', '.azw3', '.prc'})  # PalmDB结构的电子书
EBOOK_INLINE_EXTENSIONS = MOBI_EXTENSIONS | {EPUB_EXTENSION}  # 内嵌模式走特殊提取的电子书
IMAGE_FILE_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.webp',
                                   '.bmp', '.svg', '.tif', '.tiff'})

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
                       out_dir=None, start_page=None, end_page=None) -> None:
    """子进程入口：提取图片，进度/结果经 multiprocessing.Queue 回传

    out_dir 可选：为 None 时默认 PDF 同目录的 "<书名>_图片" 文件夹
    start_page/end_page 可选：限定提取的PDF页号范围（1-based，含边界），None=全部
    """
    try:
        out_dir, image_count = extract_images(
            pdf_path, out_dir, dpi, fmt, quality,
            progress=lambda done, total, message: q.put(('progress', done, total, message)),
            cancel_event=cancel_event, pause_event=pause_event,
            start_page=start_page, end_page=end_page)
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
            with open(out, 'w', encoding='utf-8') as file_handle:
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
                   cancel_event=None, pause_event=None,
                   start_page: Optional[int] = None, end_page: Optional[int] = None
                   ) -> Tuple[str, int]:
    """提取文件图片 -> (输出目录, 图片数)

    - dpi=None：提取内嵌图片（fmt='orig' 原样保存，或转 png/jpeg）
    - dpi=整数：按分辨率渲染每页为图片（fmt: png/jpeg，默认 png）
    - quality：JPEG质量(1-100)；progress(done, total, msg)：进度回调
    - cancel_event/pause_event：threading.Event，支持停止/暂停（页间生效）
    - start_page/end_page：限定提取的页号范围（1-based，含边界），None=全部
    - EPUB 内嵌模式（dpi=None）：解压zip提取图片文件，仅支持orig原样，忽略页号范围
    - MOBI/AZW3/PRC 内嵌模式（dpi=None）：解析PalmDB记录按文件头识别图片，仅orig原样
    - EPUB/MOBI 渲染模式（dpi=整数）：按页渲染（受文件格式支持程度限制）
    """
    fmt = (fmt or 'orig').lower()
    if fmt not in IMAGE_FORMATS:
        raise ValueError('不支持的图片格式: %s（支持 %s）' % (fmt, '/'.join(sorted(IMAGE_FORMATS))))
    if fmt == 'orig' and dpi:
        fmt = 'png'
    if dpi is not None and not (DPI_MIN <= dpi <= DPI_MAX):
        raise ValueError('分辨率dpi应在 %d-%d 之间' % (DPI_MIN, DPI_MAX))
    if not (JPEG_QUALITY_MIN <= quality <= JPEG_QUALITY_MAX):
        raise ValueError('JPEG质量应在 %d-%d 之间' % (JPEG_QUALITY_MIN, JPEG_QUALITY_MAX))
    if start_page is not None and start_page < 1:
        raise ValueError('起始页号必须 >= 1')
    if start_page is not None and end_page is not None and start_page > end_page:
        raise ValueError('页号范围无效：起始页大于结束页')
    if out_dir is None:
        base_name, _ = os.path.splitext(pdf_path)
        out_dir = base_name + IMAGE_OUTPUT_SUFFIX
    os.makedirs(out_dir, exist_ok=True)
    file_ext = os.path.splitext(pdf_path)[1].lower()
    if file_ext in EBOOK_INLINE_EXTENSIONS and not dpi:
        if fmt != 'orig':
            raise ValueError('%s内嵌提取仅支持原样保存（格式orig）' % file_ext[1:].upper())
        if file_ext == EPUB_EXTENSION:
            image_count = _export_epub_images(pdf_path, out_dir, progress,
                                              cancel_event, pause_event)
        else:
            image_count = _export_mobi_images(pdf_path, out_dir, progress,
                                              cancel_event, pause_event)
        return out_dir, image_count
    try:
        doc = fitz.open(pdf_path)
    except Exception as error:
        raise ValueError('无法打开文件: %s（%s）' % (os.path.basename(pdf_path), error))
    try:
        if dpi:
            image_count = _export_rendered_pages(doc, out_dir, dpi, fmt, quality,
                                                 progress, cancel_event, pause_event,
                                                 start_page, end_page)
        else:
            image_count = _export_inline_images(doc, out_dir, fmt, quality,
                                                progress, cancel_event, pause_event,
                                                start_page, end_page)
    finally:
        doc.close()
    return out_dir, image_count


def _dedupe_output_name(base_name: str, saved_names: set) -> str:
    """输出文件名去重：同名加 _2/_3 后缀，返回可用名并登记"""
    if base_name not in saved_names:
        saved_names.add(base_name)
        return base_name
    stem, extension = os.path.splitext(base_name)
    index = 2
    while True:
        candidate = '%s_%d%s' % (stem, index, extension)
        if candidate not in saved_names:
            saved_names.add(candidate)
            return candidate
        index += 1


def _export_epub_images(epub_path: str, out_dir: str, progress: Optional[Callable],
                        cancel_event, pause_event) -> int:
    """EPUB：解压zip中的图片文件（HTML/SVG引用的位图）原样保存，返回导出数"""
    try:
        zip_file = zipfile.ZipFile(epub_path)
    except zipfile.BadZipFile:
        raise ValueError('无法打开文件: %s（不是有效的EPUB压缩包）'
                         % os.path.basename(epub_path))
    saved_names = set()
    exported_count = 0
    with zip_file:
        image_names = [name for name in zip_file.namelist()
                       if os.path.splitext(name)[1].lower() in IMAGE_FILE_EXTENSIONS]
        total_count = len(image_names)
        for index, name in enumerate(image_names):
            check_task(cancel_event, pause_event)
            output_name = _dedupe_output_name(os.path.basename(name), saved_names)
            with zip_file.open(name) as source_file:
                with open(os.path.join(out_dir, output_name), 'wb') as target_file:
                    shutil.copyfileobj(source_file, target_file)
            exported_count += 1
            if progress:
                progress(index + 1, total_count, '提取图片 %d/%d' % (index + 1, total_count))
    return exported_count


def _detect_image_format(data: bytes) -> Optional[str]:
    """按文件头识别图片格式：jpg/gif/png/bmp，无法识别返回None"""
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:4] == b'GIF8':
        return 'gif'
    if data[:4] == b'\x89PNG':
        return 'png'
    if data[:2] == b'BM':
        return 'bmp'
    return None


def _export_mobi_images(mobi_path: str, out_dir: str, progress: Optional[Callable],
                        cancel_event, pause_event) -> int:
    """MOBI/AZW3/PRC：解析PalmDB记录，按文件头识别图片并提取（内容MD5去重）

    MOBI/AZW3 的图片以整条PalmDB记录存储（无统一索引字段时按内容识别最可靠）；
    封面/插图记录开头即图片文件头（JPEG/GIF/PNG/BMP），其余记录跳过。
    """
    with open(mobi_path, 'rb') as file_handle:
        palm_data = file_handle.read()
    if len(palm_data) < 78 or palm_data[60:64] != b'BOOK':
        raise ValueError('无法打开文件: %s（不是有效的MOBI/AZW3文件）'
                         % os.path.basename(mobi_path))
    record_count = struct.unpack('>H', palm_data[76:78])[0]
    record_offsets = []
    for index in range(record_count):
        offset = struct.unpack('>I', palm_data[78 + index * 8: 82 + index * 8])[0]
        record_offsets.append(offset)
    saved_digest_set = set()
    exported_count = 0
    for index in range(record_count):
        check_task(cancel_event, pause_event)
        if progress:
            progress(index + 1, record_count, '扫描记录 %d/%d' % (index + 1, record_count))
        start = record_offsets[index]
        end = record_offsets[index + 1] if index + 1 < record_count else len(palm_data)
        record_data = palm_data[start:end]
        image_format = _detect_image_format(record_data)
        if image_format is None:
            continue
        digest = hashlib.md5(record_data).hexdigest()
        if digest in saved_digest_set:
            continue
        saved_digest_set.add(digest)
        exported_count += 1
        file_name = '图片%03d.%s' % (exported_count, image_format)
        with open(os.path.join(out_dir, file_name), 'wb') as target_file:
            target_file.write(record_data)
    return exported_count


def _resolve_page_range(doc, start_page: Optional[int], end_page: Optional[int]
                        ) -> Tuple[int, int]:
    """把1-based页号范围解析为半开区间索引 [start_index, end_index)，None=全部"""
    start_index = 0 if start_page is None else max(start_page - 1, 0)
    end_index = doc.page_count if end_page is None else min(end_page, doc.page_count)
    if start_index > end_index:
        start_index = end_index
    return start_index, end_index


def _export_rendered_pages(doc, out_dir: str, dpi: int, fmt: str, quality: int,
                           progress: Optional[Callable], cancel_event, pause_event,
                           start_page: Optional[int] = None,
                           end_page: Optional[int] = None) -> int:
    """按分辨率渲染页 -> 图片文件，返回导出数（可限定页号范围）"""
    start_index, end_index = _resolve_page_range(doc, start_page, end_page)
    total_pages = end_index - start_index
    exported_count = 0
    for page_index in range(start_index, end_index):
        check_task(cancel_event, pause_event)
        pixmap = doc[page_index].get_pixmap(dpi=dpi, alpha=(fmt != 'jpeg'))
        file_name = '第%03d页.%s' % (page_index + 1, EXTENSION_BY_FORMAT[fmt])
        pixmap.save(os.path.join(out_dir, file_name), jpg_quality=quality)
        exported_count += 1
        if progress:
            progress(page_index - start_index + 1, total_pages,
                     '渲染第 %d/%d 页' % (page_index - start_index + 1, total_pages))
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
                          progress: Optional[Callable], cancel_event, pause_event,
                          start_page: Optional[int] = None,
                          end_page: Optional[int] = None) -> int:
    """提取PDF内嵌图片（按内容MD5去重，可限定页号范围），返回导出数"""
    saved_digest_set = set()
    exported_count = 0
    start_index, end_index = _resolve_page_range(doc, start_page, end_page)
    total_pages = end_index - start_index
    for page_index in range(start_index, end_index):
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
            progress(page_index - start_index + 1, total_pages,
                     '扫描第 %d/%d 页' % (page_index - start_index + 1, total_pages))
    return exported_count


def export_toc_txt(toc: List[List], out_path: str, with_pdf_page: bool = True) -> None:
    """书签导出为txt。缩进2空格/级；with_pdf_page 时行尾带 [p页号]"""
    with open(out_path, 'w', encoding='utf-8') as file_handle:
        for level, title, page in toc:
            indent_text = '  ' * (level - 1)
            if with_pdf_page:
                file_handle.write('%s%s [p%d]\n' % (indent_text, title, page))
            else:
                file_handle.write('%s%s %d\n' % (indent_text, title, page))
