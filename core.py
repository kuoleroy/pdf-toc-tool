# -*- coding: utf-8 -*-
"""核心逻辑：目录txt解析、书签写入/提取、txt导出"""
import os
import re
import shutil

import fitz

RE_RANGE = re.compile(r'^(　*)(.*?)[…\.]*\s*(\d+)\s*[—–\-]\s*(\d+)\s*$')
RE_SINGLE = re.compile(r'^(　*)(.*?)[…\.]*\s*(\d+)\s*$')
RE_PDFPAGE = re.compile(r'^(　*)(.*?)[…\.]*\s*\[p(\d+)\]\s*$')


def parse_toc(txt_path):
    """解析txt -> [(level, title, pdf_page)]，level>=1
    pdf_page 为1-based PDF页号；含 [pN] 直接用，否则为印刷页码(由调用方加偏移)"""
    entries = []  # (indent, title, page_kind, number)
    pending = None  # [indent, 累计文本]
    with open(txt_path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            if re.match(r'^\s*目\s*录\s*$', line):
                continue
            if re.match(r'^\s*\d{4}\s*[—–\-]\s*\d{4}\s*年?\s*$', line):
                continue
            m = RE_PDFPAGE.match(line)
            if m:
                indent, title, num = len(m.group(1)), m.group(2), int(m.group(3))
                kind = 'pdf'
            else:
                m = RE_RANGE.match(line) or RE_SINGLE.match(line)
                if not m:
                    ind = len(re.match(r'^(　*)', line).group(1))
                    if pending is None:
                        pending = [ind, line.strip()]
                    else:
                        pending[1] += line.strip()
                    continue
                indent = len(m.group(1))
                title = m.group(2)
                num = int(m.group(3))
                kind = 'print'
            if pending is not None:
                title = pending[1] + title
                indent = pending[0]
                pending = None
            title = re.sub(r'[…\.]+$', '', title).strip()
            if title:
                entries.append([indent, title, kind, num])
    if pending is not None:
        raise ValueError('目录文件末尾存在未完成的续行: %r' % pending[:60])
    return entries


def levels_from_indent(entries):
    """缩进 -> 层级：0级=L1，1级=L2，>=2级=L3"""
    out = []
    for indent, title, kind, num in entries:
        out.append([min(indent + 1, 3), title, kind, num])
    return out


def build_toc(entries, offset):
    """entries(已带level) -> [(level, title, pdf_page)]，offset=印刷页->PDF页偏移(0-based索引差)"""
    toc = []
    for lvl, title, kind, num in entries:
        if kind == 'pdf':
            page = num
        else:
            page = num + offset + 1
        if page < 1:
            raise ValueError('页码越界: %s [%d]' % (title, num))
        toc.append([lvl, title, page])
    return toc


def write_toc(pdf_path, toc, out_mode='copy'):
    """写入书签。out_mode: copy=生成副本(原名_带目录.pdf), same=直接增量写原文件
    返回输出文件路径"""
    if out_mode == 'copy':
        base, ext = os.path.splitext(pdf_path)
        out = base + '_带目录' + ext
        shutil.copyfile(pdf_path, out)
        doc = fitz.open(out)
        doc.set_toc(toc)
        doc.save(out, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()
        return out
    else:
        doc = fitz.open(pdf_path)
        doc.set_toc(toc)
        doc.save(pdf_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()
        return pdf_path


def read_toc(pdf_path):
    """提取书签 -> [(level, title, pdf_page)]"""
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    doc.close()
    return toc


IMAGE_FORMATS = {'orig', 'png', 'jpeg'}


def extract_images(pdf_path, out_dir=None, dpi=None, fmt='orig', quality=85):
    """提取PDF图片 -> (输出目录, 图片数)
    dpi=None：提取内嵌图片（fmt='orig' 原样保存，或转 png/jpeg）；
    dpi=整数：按分辨率渲染每页为图片（fmt: png/jpeg，默认png）；
    quality：JPEG质量(1-100)"""
    fmt = (fmt or 'orig').lower()
    if fmt not in IMAGE_FORMATS:
        raise ValueError('不支持的图片格式: %s（支持 %s）' % (fmt, '/'.join(sorted(IMAGE_FORMATS))))
    if fmt == 'orig' and dpi:
        fmt = 'png'
    ext_map = {'png': 'png', 'jpeg': 'jpg', 'orig': ''}
    ext = ext_map[fmt]
    if out_dir is None:
        base, _ = os.path.splitext(pdf_path)
        out_dir = base + '_图片'
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    count = 0
    try:
        if dpi:
            for page_no in range(doc.page_count):
                pix = doc[page_no].get_pixmap(dpi=dpi, alpha=(fmt not in ('jpeg',)))
                name = '第%03d页.%s' % (page_no + 1, ext)
                pix.save(os.path.join(out_dir, name), jpg_quality=quality)
                count += 1
            return out_dir, count
        import hashlib
        saved = set()

        def save_image(xref, page_no):
            nonlocal count
            try:
                if fmt == 'orig':
                    info = doc.extract_image(xref)
                    data = info['image']
                    fext = info['ext']
                else:
                    pix = fitz.Pixmap(doc, xref)
                    if fmt == 'jpeg' and pix.alpha:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    data = pix.tobytes(fmt, jpg_quality=quality)
                    fext = ext
            except Exception:
                return
            digest = hashlib.md5(data).hexdigest()
            if digest in saved:
                return
            saved.add(digest)
            count += 1
            name = '第%03d页_%03d_%s.%s' % (page_no, count, xref, fext)
            with open(os.path.join(out_dir, name), 'wb') as f:
                f.write(data)

        for page_no in range(doc.page_count):
            for img in doc.get_page_images(page_no, full=True):
                save_image(img[0], page_no + 1)
    finally:
        doc.close()
    return out_dir, count



def export_toc_txt(toc, out_path, with_pdf_page=True):
    """书签导出为txt。缩进2空格/级；with_pdf_page时行尾带 [p页号]"""
    with open(out_path, 'w', encoding='utf-8-sig') as f:
        for lvl, title, page in toc:
            ind = '  ' * (lvl - 1)
            if with_pdf_page:
                f.write('%s%s [p%d]\n' % (ind, title, page))
            else:
                f.write('%s%s %d\n' % (ind, title, page))
