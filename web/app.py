# -*- coding: utf-8 -*-
"""PDF 书签工具 网页版后端（FastAPI）

复用仓库根目录的 core.py / ebook.py 全部处理逻辑，仅提供 HTTP 接口层。

健壮性设计：
- 端点全部用同步 def：FastAPI 自动放入线程池执行，互不阻塞事件循环
- 上传流式落盘并限制大小（MAX_UPLOAD_MB），超限返回 413
- 每个请求独立临时目录，响应发送完成后由 BackgroundTask 自动清理
- 参数前置校验（分辨率/格式/页号范围/偏移），错误返回 400 + 中文说明

启动：cd web && pip install -r requirements.txt && uvicorn app:app --host 0.0.0.0 --port 8000
"""
import os
import re
import shutil
import sys
import tempfile
import threading
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import core
import ebook

app = FastAPI(title='PDF 书签工具（网页版）', version='1.1.0')

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEB_DIR, 'static')
IMAGE_INLINE = '内嵌图片'
MAX_UPLOAD_MB = 500
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
RE_PAGE_RANGE = re.compile(r'^\s*(\d+)\s*[-—–]\s*(\d+)\s*$')
RE_SINGLE_PAGE = re.compile(r'^\s*(\d+)\s*$')


def _save_upload(upload: UploadFile, work_dir: str, name: str) -> str:
    """流式保存上传文件到临时目录；超过大小上限抛 413"""
    path = os.path.join(work_dir, name)
    total_size = 0
    with open(path, 'wb') as file_handle:
        while True:
            chunk = upload.file.read(CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, '文件超过 %dMB 上限' % MAX_UPLOAD_MB)
            file_handle.write(chunk)
    return path


def _cleanup_work_dir(work_dir: str) -> None:
    """响应发送完成后清理请求临时目录"""
    shutil.rmtree(work_dir, ignore_errors=True)


def _parse_optional_int(value: str, field_name: str) -> Optional[int]:
    """解析可空整数表单值；非空且非数字时抛 400"""
    text = (value or '').strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise HTTPException(400, '%s必须是数字' % field_name)


def _parse_resolution(resolution: str) -> Optional[int]:
    """分辨率：空/'内嵌图片' -> None（内嵌）；数字 -> dpi（1-1000）"""
    text = (resolution or '').strip()
    if not text or text == IMAGE_INLINE:
        return None
    try:
        dpi = int(text)
    except ValueError:
        raise HTTPException(400, '分辨率必须是数字或"%s"' % IMAGE_INLINE)
    if not (core.DPI_MIN <= dpi <= core.DPI_MAX):
        raise HTTPException(400, '分辨率dpi应在 %d-%d 之间' % (core.DPI_MIN, core.DPI_MAX))
    return dpi


def _parse_page_range(range_text: str) -> Tuple[Optional[int], Optional[int]]:
    """页号范围：空 -> (None, None)；'12-30' -> 区间；'12' -> 单页；非法抛 400"""
    text = (range_text or '').strip()
    if not text:
        return None, None
    range_match = RE_PAGE_RANGE.match(text)
    if range_match:
        start_page, end_page = int(range_match.group(1)), int(range_match.group(2))
    else:
        single_match = RE_SINGLE_PAGE.match(text)
        if not single_match:
            raise HTTPException(400, '页号范围格式应为"12-30"或"12"（空=全部）')
        start_page = end_page = int(single_match.group(1))
    if start_page < 1 or end_page < start_page:
        raise HTTPException(400, '页号范围无效：起始页>=1 且 起始页<=结束页')
    return start_page, end_page


def _parse_format(fmt: str) -> str:
    """图片格式：仅允许 orig/png/jpeg"""
    image_format = (fmt or 'orig').strip().lower()
    if image_format not in core.IMAGE_FORMATS:
        raise HTTPException(400, '不支持的图片格式: %s（支持 orig/png/jpeg）' % image_format)
    return image_format


def _parse_required_range(page_range: str) -> Tuple[int, int]:
    """OCR 页号范围：必填（如 11-16）"""
    start_page, end_page = _parse_page_range(page_range)
    if start_page is None or end_page is None:
        raise HTTPException(400, 'OCR需要填写页号范围，如 11-16')
    return start_page, end_page


# OCR 引擎为全局单例且重资源：全局锁串行化，同一时间只跑一个OCR任务
_OCR_LOCK = threading.Lock()


def _load_ocr_module():
    """延迟导入 ocr 模块；服务器缺 OCR 依赖时给清晰错误"""
    try:
        import ocr as ocr_module
    except ImportError as error:
        raise HTTPException(500, '服务器缺少OCR依赖（numpy / rapidocr-onnxruntime），'
                                 '请先安装 web/requirements.txt 后重启') from error
    return ocr_module


def _validate_toc_lines(txt_path: str) -> None:
    """逐行校验目录txt：任何非注释行必须带页码（行尾[pN]或数字），无页码直接禁止写入"""
    with open(txt_path, encoding='utf-8-sig') as file_handle:
        for line_number, raw_line in enumerate(file_handle, 1):
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            if re.search(r'\[p\s*\d+\]\s*$', line):
                continue
            if re.search(r'\d+\s*$', line):
                continue
            raise HTTPException(400, '目录txt第 %d 行没有页码，禁止写入: %s'
                                     % (line_number, line[:40]))


_TOC_CLEAN_RE = re.compile(r'[\s.·…‥⋯・︙。、，,．－\-—_—]')


def _auto_detect_offset(file_path: str, ocr_module, engine) -> Optional[int]:
    """扫描PDF前若干页页眉/页脚数字，投票得出印刷页码->PDF页偏移；失败返回None

    优先用PDF文本层（页眉/页脚区域内的纯数字），无文本层的扫描件改走OCR条带。
    """
    import numpy as np
    import fitz
    doc = fitz.open(file_path)
    try:
        scan_count = min(30, doc.page_count)
        text_total = sum(len(doc[i].get_text()) for i in range(min(5, doc.page_count)))
        if text_total > 20:
            offset_votes = _vote_text_band(doc, scan_count)
        else:
            offset_votes = _vote_ocr_band(doc, scan_count, ocr_module, engine)
        if not offset_votes:
            return None
        best_offset, best_votes = max(offset_votes.items(), key=lambda item: item[1])
        if best_votes >= 2 and 0 <= best_offset <= 300:
            return best_offset
        return None
    finally:
        doc.close()


def _vote_text_band(doc, scan_count: int) -> dict:
    """文本层：页眉/页脚区域内的纯数字文本投票"""
    offset_votes: dict = {}
    for page_index in range(scan_count):
        page = doc[page_index]
        for word in page.get_text('words'):
            x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
            if not str(text).isdigit():
                continue
            printed_number = int(text)
            if not (1 <= printed_number <= 2000):
                continue
            if not (y1 <= page.rect.height * 0.15 or y0 >= page.rect.height * 0.85):
                continue
            offset_candidate = page_index - printed_number
            offset_votes[offset_candidate] = offset_votes.get(offset_candidate, 0) + 1
    return offset_votes


def _vote_ocr_band(doc, scan_count: int, ocr_module, engine) -> dict:
    """OCR条带：渲染页眉/页脚条带放大识别数字投票"""
    import numpy as np
    import fitz
    offset_votes: dict = {}
    for page_index in range(scan_count):
        page = doc[page_index]
        for band in (0.0, 0.85):
            clip = fitz.Rect(0, page.rect.height * band, page.rect.width,
                             page.rect.height * (band + 0.15))
            pixmap = page.get_pixmap(dpi=ocr_module.OCR_DPI_DEFAULT * 2, clip=clip,
                                     colorspace=fitz.csRGB, alpha=False)
            pixel_array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, 3)
            raw_output = engine(pixel_array)
            ocr_results = raw_output[0] if isinstance(raw_output, tuple) else raw_output
            if not ocr_results:
                continue
            for result_item in ocr_results:
                _bbox, text, score = result_item[0], result_item[1], result_item[2]
                if float(score) < 0.5:
                    continue
                stripped_text = _TOC_CLEAN_RE.sub('', str(text))
                if not stripped_text.isdigit():
                    continue
                printed_number = int(stripped_text)
                if not (1 <= printed_number <= 2000):
                    continue
                offset_candidate = page_index - printed_number
                offset_votes[offset_candidate] = offset_votes.get(offset_candidate, 0) + 1
    return offset_votes


@app.post('/api/write_toc')
def api_write_toc(pdf: UploadFile = File(...), toc: UploadFile = File(...),
                  first_pdf: str = Form(''), first_print: str = Form('')):
    """写入书签：上传PDF+目录txt，返回 _带目录.pdf 副本下载

    偏移规则：
    - txt 全为 [p页号]：无需偏移
    - txt 为印刷页码：必须提供偏移；未提供时后端自动检测（OCR扫页眉，约1-2分钟），
      检测失败则拒绝并提示手动填写
    """
    work_dir = tempfile.mkdtemp(prefix='web_toc_')
    try:
        pdf_path = _save_upload(pdf, work_dir, os.path.basename(pdf.filename or 'book.pdf'))
        txt_path = _save_upload(toc, work_dir, 'toc.txt')
        _validate_toc_lines(txt_path)
        offset = 0
        auto_detected = False
        first_pdf_no = _parse_optional_int(first_pdf, '正文第一页的PDF页号')
        first_print_no = _parse_optional_int(first_print, '正文第一页的印刷页码')
        if (first_pdf_no is None) != (first_print_no is None):
            raise HTTPException(400, '正文第一页的PDF页号和印刷页码需同时填写或同时留空')
        parsed_entries = core.levels_from_indent(core.parse_toc(txt_path))
        uses_pdf_pages = all(entry[2] == core.KIND_PDF_PAGE for entry in parsed_entries)
        if first_pdf_no is not None:
            offset = first_pdf_no - 1 - first_print_no
        elif not uses_pdf_pages:
            ocr_module = _load_ocr_module()
            with _OCR_LOCK:
                engine = ocr_module.load_ocr()
                offset = _auto_detect_offset(pdf_path, ocr_module, engine)
            if offset is None:
                raise HTTPException(400, '无法自动检测偏移，请手动填写"正文第一页"的PDF页号和印刷页码')
            auto_detected = True
        toc_entries = core.build_toc(parsed_entries, offset)
        output_path = core.write_toc(pdf_path, toc_entries, 'copy')
        response = FileResponse(output_path, filename=os.path.basename(output_path),
                                media_type='application/pdf',
                                background=lambda: _cleanup_work_dir(work_dir))
        if auto_detected:
            response.headers['X-Pdf-Offset'] = str(offset)
        return response
    except HTTPException:
        _cleanup_work_dir(work_dir)
        raise
    except Exception as error:
        _cleanup_work_dir(work_dir)
        raise HTTPException(400, str(error))


@app.post('/api/extract_toc')
def api_extract_toc(file: UploadFile = File(...), with_page: str = Form('1')):
    """提取书签/电子书目录：返回 txt 下载（with_page=1 行尾带 [p页号]/[p序号]）"""
    work_dir = tempfile.mkdtemp(prefix='web_ext_')
    try:
        file_path = _save_upload(file, work_dir, os.path.basename(file.filename or 'book.pdf'))
        is_ebook = ebook.is_ebook(file_path)
        if is_ebook:
            entries = ebook.extract_toc(file_path)
            output_path = os.path.join(work_dir, '目录.txt')
            with open(output_path, 'w', encoding='utf-8') as file_handle:
                file_handle.write(ebook.to_txt(entries, with_page.strip() == '1'))
        else:
            toc_entries = core.read_toc(file_path)
            if not toc_entries:
                raise HTTPException(400, '该PDF没有书签')
            output_path = os.path.join(work_dir, '书签.txt')
            core.export_toc_txt(toc_entries, output_path, with_page.strip() == '1')
        file_stem = os.path.splitext(os.path.basename(file.filename or 'book.pdf'))[0]
        return FileResponse(output_path,
                            filename=file_stem + ('_目录.txt' if is_ebook else '_书签.txt'),
                            media_type='text/plain; charset=utf-8',
                            background=lambda: _cleanup_work_dir(work_dir))
    except HTTPException:
        _cleanup_work_dir(work_dir)
        raise
    except Exception as error:
        _cleanup_work_dir(work_dir)
        raise HTTPException(400, str(error))


@app.post('/api/extract_images')
def api_extract_images(file: UploadFile = File(...),
                       resolution: str = Form(IMAGE_INLINE),
                       fmt: str = Form('orig'),
                       page_range: str = Form('')):
    """提取图片：内嵌原样或按分辨率渲染，打包 zip 下载（电子书内嵌仅orig）"""
    work_dir = tempfile.mkdtemp(prefix='web_img_')
    try:
        file_path = _save_upload(file, work_dir, os.path.basename(file.filename or 'book.pdf'))
        render_dpi = _parse_resolution(resolution)
        image_format = _parse_format(fmt)
        if render_dpi is not None and image_format == 'orig':
            image_format = 'png'
        start_page, end_page = _parse_page_range(page_range)
        out_dir, image_count = core.extract_images(
            file_path, dpi=render_dpi, fmt=image_format, quality=100,
            start_page=start_page, end_page=end_page)
        if image_count == 0:
            raise HTTPException(400, '没有提取到图片')
        zip_path = os.path.join(work_dir, 'images')
        shutil.make_archive(zip_path, 'zip', out_dir)
        return FileResponse(zip_path + '.zip', filename='images.zip',
                            media_type='application/zip',
                            background=lambda: _cleanup_work_dir(work_dir))
    except HTTPException:
        _cleanup_work_dir(work_dir)
        raise
    except Exception as error:
        _cleanup_work_dir(work_dir)
        raise HTTPException(400, str(error))


@app.post('/api/ocr_toc')
def api_ocr_toc(file: UploadFile = File(...), page_range: str = Form('')):
    """OCR识别目录页：上传文件+页号范围，返回目录txt下载（行尾点线+印刷页码，含#提示行）"""
    work_dir = tempfile.mkdtemp(prefix='web_ocr_')
    try:
        file_path = _save_upload(file, work_dir, os.path.basename(file.filename or 'book.pdf'))
        if os.path.splitext(file_path)[1].lower() not in core.OCR_EXTENSIONS:
            raise HTTPException(400, 'OCR识别仅支持PDF/EPUB/MOBI文件')
        start_page, end_page = _parse_required_range(page_range)
        ocr_module = _load_ocr_module()
        with _OCR_LOCK:
            engine = ocr_module.load_ocr()
            text = ocr_module.ocr_to_txt(file_path, start_page, end_page, ocr=engine)
            text = ocr_module.apply_first_line_page_fallback(text, start_page)
        output_path = os.path.join(work_dir, 'OCR目录.txt')
        with open(output_path, 'w', encoding='utf-8') as file_handle:
            file_handle.write(text)
        file_stem = os.path.splitext(os.path.basename(file.filename or 'book.pdf'))[0]
        return FileResponse(output_path, filename=file_stem + '_目录(OCR).txt',
                            media_type='text/plain; charset=utf-8',
                            background=lambda: _cleanup_work_dir(work_dir))
    except HTTPException:
        _cleanup_work_dir(work_dir)
        raise
    except Exception as error:
        _cleanup_work_dir(work_dir)
        raise HTTPException(400, str(error))


@app.post('/api/ocr_text')
def api_ocr_text(file: UploadFile = File(...), page_range: str = Form(''),
                 page_mark: str = Form('0')):
    """OCR识别任意页文字：上传文件+页号范围，返回纯文字txt下载"""
    work_dir = tempfile.mkdtemp(prefix='web_ocr_')
    try:
        file_path = _save_upload(file, work_dir, os.path.basename(file.filename or 'book.pdf'))
        if os.path.splitext(file_path)[1].lower() not in core.OCR_EXTENSIONS:
            raise HTTPException(400, 'OCR识别仅支持PDF/EPUB/MOBI文件')
        start_page, end_page = _parse_required_range(page_range)
        ocr_module = _load_ocr_module()
        with _OCR_LOCK:
            engine = ocr_module.load_ocr()
            text = ocr_module.extract_text(file_path, start_page, end_page, ocr=engine,
                                           with_page_marks=page_mark.strip() == '1')
        output_path = os.path.join(work_dir, 'OCR文字.txt')
        with open(output_path, 'w', encoding='utf-8') as file_handle:
            file_handle.write(text)
        file_stem = os.path.splitext(os.path.basename(file.filename or 'book.pdf'))[0]
        return FileResponse(output_path, filename=file_stem + '_文字(OCR).txt',
                            media_type='text/plain; charset=utf-8',
                            background=lambda: _cleanup_work_dir(work_dir))
    except HTTPException:
        _cleanup_work_dir(work_dir)
        raise
    except Exception as error:
        _cleanup_work_dir(work_dir)
        raise HTTPException(400, str(error))


app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')
