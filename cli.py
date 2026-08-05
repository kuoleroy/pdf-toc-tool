# -*- coding: utf-8 -*-
"""命令行接口"""
import os
import re
import sys
from typing import List, Optional

import core
import doc2pdf
import ebook
import ocr

RE_PAGE_RANGE = re.compile(r'^(\d+)[-—–](\d+)$')

DEFAULT_OFFSET = 15
DEFAULT_JPEG_QUALITY = 85
DEFAULT_IMAGE_FORMAT = 'orig'
DEFAULT_EXTRACT_SUFFIX = '_书签.txt'
DEFAULT_BOOK_TXT_SUFFIX = '_目录.txt'


def _print_usage() -> None:
    print('用法:')
    print('  %s extract <pdf> [输出txt]' % os.path.basename(sys.argv[0]))
    print('  %s write <pdf> <目录txt> [偏移(默认15)] [copy|same]' % os.path.basename(sys.argv[0]))
    print('  %s ocr <pdf> <起始PDF页>-<结束PDF页> [-a] [-t] [-o 输出txt]   (OCR页面；-a=自动检测偏移并打印；-t=识别纯文字[正文等任意页])' % os.path.basename(sys.argv[0]))
    print('  %s ebook <电子书> [输出txt]   (提取EPUB/MOBI/AZW3内置目录，页码为阅读顺序号[p序号])' % os.path.basename(sys.argv[0]))
    print('  %s pdfimages <文件> [输出目录] [-r 分辨率dpi] [-f 格式] [-q 质量]   (提取图片：默认内嵌原样；-r=渲染页面；格式 orig/png/jpeg；-q为JPEG质量；PDF/EPUB/MOBI/AZW3/PRC均可)' % os.path.basename(sys.argv[0]))
    print('  %s unlock <pdf> <密码> [输出pdf]   (去除密码保护另存；默认<原名>_已解锁.pdf)' % os.path.basename(sys.argv[0]))
    print('  %s encrypt <pdf> <密码> [输出pdf]   (设置打开密码另存 AES-256；默认<原名>_已加密_密码<密码>.pdf，文件名带密码便于记忆)' % os.path.basename(sys.argv[0]))
    print('  %s doc2pdf <Word文档> [输出pdf]   (Word转PDF：.docx免引擎纯Python转换，或装Office/WPS、LibreOffice；默认<原名>_转PDF.pdf)' % os.path.basename(sys.argv[0]))
    print('  %s doc2html <docx> [输出html]   (.docx转HTML，纯Python免引擎；默认<原名>_转HTML.html)' % os.path.basename(sys.argv[0]))


def _make_progress_writer():
    """构造回写当前行的进度回调（\r 覆盖，适合控制台进度显示）"""
    def write_progress(done, total, message) -> None:
        sys.stdout.write('\r%d/%d  %s   ' % (done, total, message))
    return write_progress


def _write_text_file(output_path: str, content: str) -> None:
    """写入 UTF-8 BOM 文本文件，区分 IO 异常类型"""
    try:
        with open(output_path, 'w', encoding='utf-8') as output_file:
            output_file.write(content)
    except OSError as io_error:
        raise OSError('写入文件失败 %s: %s' % (output_path, io_error))


def main(args: List[str]) -> int:
    """CLI 分发：extract / write / ocr / ebook / pdfimages"""
    if not args:
        _print_usage()
        return 1
    command = args[0]

    if command == 'extract' and len(args) >= 2:
        pdf_path = args[1]
        output_path = args[2] if len(args) > 2 \
            else os.path.splitext(pdf_path)[0] + DEFAULT_EXTRACT_SUFFIX
        toc_entries = core.read_toc(pdf_path)
        if not toc_entries:
            print('该PDF没有书签。')
            return 1
        core.export_toc_txt(toc_entries, output_path)
        print('提取 %d 条书签 -> %s' % (len(toc_entries), output_path))
        return 0

    if command == 'write' and len(args) >= 3:
        pdf_path, toc_txt_path = args[1], args[2]
        try:
            offset = int(args[3]) if len(args) > 3 else DEFAULT_OFFSET
        except ValueError:
            print('偏移必须是整数: %s' % args[3])
            return 1
        output_mode = args[4] if len(args) > 4 else 'copy'
        parsed_entries = core.levels_from_indent(core.parse_toc(toc_txt_path))
        toc_entries = core.build_toc(parsed_entries, offset)
        output_path = core.write_toc(pdf_path, toc_entries, output_mode)
        print('写入 %d 条书签 -> %s' % (len(toc_entries), output_path))
        return 0

    if command == 'ocr' and len(args) >= 3:
        pdf_path = args[1]
        range_match = RE_PAGE_RANGE.match(args[2])
        if not range_match:
            _print_usage()
            return 1
        start_page, end_page = int(range_match.group(1)), int(range_match.group(2))
        output_path: Optional[str] = None
        detect_offset_flag = False
        text_mode_flag = False
        arg_index = 3
        while arg_index < len(args):
            if args[arg_index] == '-o' and arg_index + 1 < len(args):
                output_path = args[arg_index + 1]
                arg_index += 2
            elif args[arg_index] == '-a':
                detect_offset_flag = True
                arg_index += 1
            elif args[arg_index] == '-t':
                text_mode_flag = True
                arg_index += 1
            else:
                _print_usage()
                return 1
        engine = ocr.load_ocr()
        progress_callback = _make_progress_writer()
        if text_mode_flag:
            ocr_text = ocr.extract_text(pdf_path, start_page, end_page, ocr=engine,
                                        progress=progress_callback)
            sys.stdout.write('\n')
        else:
            ocr_text = ocr.ocr_to_txt(pdf_path, start_page, end_page, ocr=engine,
                                      progress=progress_callback)
            sys.stdout.write('\n')
        if detect_offset_flag:
            offset, _first_printed_number = ocr.detect_offset(
                pdf_path, start_page, end_page, ocr=engine,
                progress=progress_callback, skip_first=True)
            sys.stdout.write('\n')
            if offset is not None:
                print('自动检测：PDF页偏移=%d' % offset)
            else:
                print('自动检测偏移失败，请手动计算偏移（正文第一页PDF索引 - 印刷页码）。')
        if output_path:
            _write_text_file(output_path, ocr_text)
            print('OCR完成 -> %s' % output_path)
        else:
            print(ocr_text)
        return 0

    if command == 'ebook' and len(args) >= 2:
        book_path = args[1]
        output_path = args[2] if len(args) > 2 \
            else os.path.splitext(book_path)[0] + DEFAULT_BOOK_TXT_SUFFIX
        toc_entries = ebook.extract_toc(book_path)
        toc_text = ebook.to_txt(toc_entries)
        _write_text_file(output_path, toc_text)
        print('提取 %d 条目录（[p序号]为阅读顺序号，非页码） -> %s' % (len(toc_entries), output_path))
        return 0

    if command == 'pdfimages' and len(args) >= 2:
        pdf_path = args[1]
        output_dir: Optional[str] = None
        render_dpi: Optional[int] = None
        image_format = DEFAULT_IMAGE_FORMAT
        jpeg_quality = DEFAULT_JPEG_QUALITY
        arg_index = 2
        while arg_index < len(args):
            if args[arg_index] == '-r' and arg_index + 1 < len(args):
                try:
                    render_dpi = int(args[arg_index + 1])
                except ValueError:
                    print('分辨率必须是整数: %s' % args[arg_index + 1])
                    return 1
                arg_index += 2
            elif args[arg_index] == '-f' and arg_index + 1 < len(args):
                image_format = args[arg_index + 1].lower()
                arg_index += 2
            elif args[arg_index] == '-q' and arg_index + 1 < len(args):
                try:
                    jpeg_quality = int(args[arg_index + 1])
                except ValueError:
                    print('质量必须是整数: %s' % args[arg_index + 1])
                    return 1
                arg_index += 2
            elif output_dir is None:
                output_dir = args[arg_index]
                arg_index += 1
            else:
                _print_usage()
                return 1
        progress_callback = _make_progress_writer()
        output_dir, image_count = core.extract_images(
            pdf_path, output_dir, render_dpi, image_format, jpeg_quality,
            progress=progress_callback)
        sys.stdout.write('\n')
        print('提取 %d 张%s（%s） -> %s' % (image_count, '页面' if render_dpi else '图片',
                                       image_format, output_dir))
        return 0

    if command == 'unlock' and len(args) >= 3:
        pdf_path, password = args[1], args[2]
        output_path = args[3] if len(args) > 3 \
            else os.path.splitext(pdf_path)[0] + '_已解锁.pdf'
        core.unlock_pdf(pdf_path, password, output_path)
        print('解锁完成（已去除密码保护） -> %s' % output_path)
        return 0

    if command == 'encrypt' and len(args) >= 3:
        pdf_path, password = args[1], args[2]
        output_path = args[3] if len(args) > 3 \
            else os.path.splitext(pdf_path)[0] + '_已加密_密码' + password + '.pdf'
        core.encrypt_pdf(pdf_path, password, output_path)
        print('加密完成（打开需密码） -> %s' % output_path)
        return 0

    if command == 'doc2pdf' and len(args) >= 2:
        source_path = args[1]
        output_path = args[2] if len(args) > 2 \
            else os.path.splitext(source_path)[0] + '_转PDF.pdf'
        doc2pdf.doc_to_pdf(source_path, output_path)
        print('转换完成（Word → PDF） -> %s' % output_path)
        return 0

    if command == 'doc2html' and len(args) >= 2:
        source_path = args[1]
        output_path = args[2] if len(args) > 2 \
            else os.path.splitext(source_path)[0] + '_转HTML.html'
        doc2pdf.doc_to_html(source_path, output_path)
        print('转换完成（Word → HTML） -> %s' % output_path)
        return 0

    _print_usage()
    return 1
