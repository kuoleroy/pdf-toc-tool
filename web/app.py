# -*- coding: utf-8 -*-
"""PDF 书签工具 网页版后端（FastAPI）

复用仓库根目录的 core.py / ebook.py 全部处理逻辑，仅提供 HTTP 接口层。
启动：cd web && pip install -r requirements.txt && uvicorn app:app --host 0.0.0.0 --port 8000
"""
import os
import re
import shutil
import sys
import tempfile
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import core
import ebook

app = FastAPI(title='PDF 书签工具（网页版）', version='1.0.0')

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEB_DIR, 'static')
IMAGE_INLINE = '内嵌图片'
RE_PAGE_RANGE = re.compile(r'^\s*(\d+)\s*[-—–]\s*(\d+)\s*$')


def _save_upload(upload: UploadFile, work_dir: str, name: str) -> str:
    """保存上传文件到临时目录，返回落盘路径"""
    path = os.path.join(work_dir, name)
    with open(path, 'wb') as file_handle:
        shutil.copyfileobj(upload.file, file_handle)
    return path


@app.post('/api/write_toc')
async def api_write_toc(pdf: UploadFile = File(...), toc: UploadFile = File(...),
                        first_pdf: str = Form(''), first_print: str = Form('')):
    """写入书签：上传PDF+目录txt，返回 _带目录.pdf 副本下载"""
    work_dir = tempfile.mkdtemp(prefix='web_toc_')
    pdf_path = _save_upload(pdf, work_dir, os.path.basename(pdf.filename or 'book.pdf'))
    txt_path = _save_upload(toc, work_dir, 'toc.txt')
    try:
        offset = 0
        if first_pdf.strip() and first_print.strip():
            try:
                offset = int(first_pdf.strip()) - 1 - int(first_print.strip())
            except ValueError:
                raise HTTPException(400, '正文第一页的PDF页号和印刷页码必须是数字')
        parsed_entries = core.levels_from_indent(core.parse_toc(txt_path))
        toc_entries = core.build_toc(parsed_entries, offset)
        output_path = core.write_toc(pdf_path, toc_entries, 'copy')
        return FileResponse(output_path, filename=os.path.basename(output_path),
                            media_type='application/pdf')
    except Exception as error:
        raise HTTPException(400, str(error))


@app.post('/api/extract_toc')
async def api_extract_toc(file: UploadFile = File(...), with_page: str = Form('1')):
    """提取书签/电子书目录：返回 txt 下载（with_page=1 行尾带 [p页号]/[p序号]）"""
    work_dir = tempfile.mkdtemp(prefix='web_ext_')
    file_path = _save_upload(file, work_dir, os.path.basename(file.filename or 'book.pdf'))
    try:
        if ebook.is_ebook(file_path):
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
        return FileResponse(output_path,
                            filename=os.path.splitext(os.path.basename(file.filename))[0]
                            + ('_目录.txt' if ebook.is_ebook(file_path) else '_书签.txt'),
                            media_type='text/plain; charset=utf-8')
    except Exception as error:
        raise HTTPException(400, str(error))


@app.post('/api/extract_images')
async def api_extract_images(file: UploadFile = File(...),
                             resolution: str = Form(IMAGE_INLINE),
                             fmt: str = Form('orig'),
                             page_range: str = Form('')):
    """提取图片：内嵌原样或按分辨率渲染，打包 zip 下载（电子书内嵌仅orig）"""
    work_dir = tempfile.mkdtemp(prefix='web_img_')
    file_path = _save_upload(file, work_dir, os.path.basename(file.filename or 'book.pdf'))
    try:
        resolution_text = resolution.strip()
        render_dpi = None if not resolution_text or resolution_text == IMAGE_INLINE \
            else int(resolution_text)
        start_page = None
        end_page = None
        range_text = page_range.strip()
        if range_text:
            range_match = RE_PAGE_RANGE.match(range_text)
            if range_match:
                start_page, end_page = int(range_match.group(1)), int(range_match.group(2))
            else:
                start_page = int(range_text)
        image_format = (fmt or 'orig').strip().lower()
        if render_dpi is not None and image_format == 'orig':
            image_format = 'png'
        out_dir, image_count = core.extract_images(
            file_path, dpi=render_dpi, fmt=image_format, quality=100,
            start_page=start_page, end_page=end_page)
        if image_count == 0:
            raise HTTPException(400, '没有提取到图片')
        zip_path = os.path.join(work_dir, 'images')
        shutil.make_archive(zip_path, 'zip', out_dir)
        return FileResponse(zip_path + '.zip', filename='images.zip',
                            media_type='application/zip')
    except Exception as error:
        raise HTTPException(400, str(error))


app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')
