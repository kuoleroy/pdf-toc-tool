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


@app.post('/api/write_toc')
def api_write_toc(pdf: UploadFile = File(...), toc: UploadFile = File(...),
                  first_pdf: str = Form(''), first_print: str = Form('')):
    """写入书签：上传PDF+目录txt，返回 _带目录.pdf 副本下载"""
    work_dir = tempfile.mkdtemp(prefix='web_toc_')
    try:
        pdf_path = _save_upload(pdf, work_dir, os.path.basename(pdf.filename or 'book.pdf'))
        txt_path = _save_upload(toc, work_dir, 'toc.txt')
        offset = 0
        first_pdf_no = _parse_optional_int(first_pdf, '正文第一页的PDF页号')
        first_print_no = _parse_optional_int(first_print, '正文第一页的印刷页码')
        if (first_pdf_no is None) != (first_print_no is None):
            raise HTTPException(400, '正文第一页的PDF页号和印刷页码需同时填写或同时留空')
        parsed_entries = core.levels_from_indent(core.parse_toc(txt_path))
        if first_pdf_no is None:
            uses_pdf_pages = all(entry[2] == core.KIND_PDF_PAGE for entry in parsed_entries)
            if not uses_pdf_pages:
                raise HTTPException(400, '目录txt使用印刷页码，必须填写"正文第一页"的PDF页号和印刷页码'
                                         '（或把txt改为[p页号]格式）')
        else:
            offset = first_pdf_no - 1 - first_print_no
        toc_entries = core.build_toc(parsed_entries, offset)
        output_path = core.write_toc(pdf_path, toc_entries, 'copy')
        return FileResponse(output_path, filename=os.path.basename(output_path),
                            media_type='application/pdf',
                            background=lambda: _cleanup_work_dir(work_dir))
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
    """OCR识别目录页：上传文件+页号范围，返回目录txt下载（含 # 提示行）"""
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
            text = ocr_module.apply_first_line_page_fallback(text, end_page + 1)
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
