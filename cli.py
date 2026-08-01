# -*- coding: utf-8 -*-
"""命令行接口"""
import os
import re
import sys

import core
import ebook
import ocr


def _print_usage():
    print('用法:')
    print('  %s extract <pdf> [输出txt]' % os.path.basename(sys.argv[0]))
    print('  %s write <pdf> <目录txt> [偏移(默认15)] [copy|same]' % os.path.basename(sys.argv[0]))
    print('  %s ocr <pdf> <起始PDF页>-<结束PDF页> [-a] [-o 输出txt]   (OCR目录页，需带OCR版；-a=自动检测偏移并打印)' % os.path.basename(sys.argv[0]))
    print('  %s ebook <电子书> [输出txt]   (提取EPUB/MOBI/AZW3内置目录，页码为阅读顺序号[p序号])' % os.path.basename(sys.argv[0]))
    print('  %s pdfimages <pdf> [输出目录] [-r 分辨率dpi] [-f 格式] [-q 质量]   (提取图片：默认内嵌原样；-r=渲染页面；格式 orig/png/jpeg；-q为JPEG质量)' % os.path.basename(sys.argv[0]))


def main(args):
    if not args:
        _print_usage()
        return 1
    cmd = args[0]
    if cmd == 'extract' and len(args) >= 2:
        pdf = args[1]
        out = args[2] if len(args) > 2 else os.path.splitext(pdf)[0] + '_书签.txt'
        toc = core.read_toc(pdf)
        if not toc:
            print('该PDF没有书签。')
            return 1
        core.export_toc_txt(toc, out)
        print('提取 %d 条书签 -> %s' % (len(toc), out))
        return 0
    if cmd == 'write' and len(args) >= 3:
        pdf, txt = args[1], args[2]
        offset = int(args[3]) if len(args) > 3 else 15
        mode = args[4] if len(args) > 4 else 'copy'
        entries = core.levels_from_indent(core.parse_toc(txt))
        toc = core.build_toc(entries, offset)
        out = core.write_toc(pdf, toc, mode)
        print('写入 %d 条书签 -> %s' % (len(toc), out))
        return 0
    if cmd == 'ocr' and len(args) >= 3:
        pdf = args[1]
        m = re.match(r'^(\d+)[-—–](\d+)$', args[2])
        if not m:
            _print_usage()
            return 1
        start, end = int(m.group(1)), int(m.group(2))
        out_path = None
        detect = False
        i = 3
        while i < len(args):
            if args[i] == '-o' and i + 1 < len(args):
                out_path = args[i + 1]
                i += 2
            elif args[i] == '-a':
                detect = True
                i += 1
            else:
                _print_usage()
                return 1
        engine = ocr.load_ocr()
        prog = lambda d, t, m: sys.stdout.write('\rOCR %d/%d  %s   ' % (d, t, m))
        txt = ocr.ocr_to_txt(pdf, start, end, ocr=engine, progress=prog)
        sys.stdout.write('\n')
        if detect:
            offset, n0 = ocr.detect_offset(pdf, start, end, ocr=engine,
                                           progress=prog, skip_first=True)
            sys.stdout.write('\n')
            if offset is not None:
                print('自动检测：PDF页偏移=%d' % offset)
            else:
                print('自动检测偏移失败，请手动计算偏移（正文第一页PDF索引 - 印刷页码）。')
        if out_path:
            with open(out_path, 'w', encoding='utf-8-sig') as f:
                f.write(txt)
            print('OCR完成 -> %s' % out_path)
        else:
            print(txt)
        return 0
    if cmd == 'ebook' and len(args) >= 2:
        book = args[1]
        out = args[2] if len(args) > 2 else os.path.splitext(book)[0] + '_目录.txt'
        entries = ebook.extract_toc(book)
        txt = ebook.to_txt(entries)
        with open(out, 'w', encoding='utf-8-sig') as f:
            f.write(txt)
        print('提取 %d 条目录（[p序号]为阅读顺序号，非页码） -> %s' % (len(entries), out))
        return 0
    if cmd == 'pdfimages' and len(args) >= 2:
        pdf = args[1]
        out_dir = None
        dpi = None
        fmt = 'orig'
        quality = 85
        i = 2
        while i < len(args):
            if args[i] == '-r' and i + 1 < len(args):
                dpi = int(args[i + 1])
                i += 2
            elif args[i] == '-f' and i + 1 < len(args):
                fmt = args[i + 1].lower()
                i += 2
            elif args[i] == '-q' and i + 1 < len(args):
                quality = int(args[i + 1])
                i += 2
            elif out_dir is None:
                out_dir = args[i]
                i += 1
            else:
                _print_usage()
                return 1
        prog = lambda d, t, m: sys.stdout.write('\r提取 %d/%d  %s   ' % (d, t, m))
        out_dir, count = core.extract_images(pdf, out_dir, dpi, fmt, quality, progress=prog)
        sys.stdout.write('\n')
        print('提取 %d 张%s（%s） -> %s' % (count, '页面' if dpi else '图片', fmt, out_dir))
        return 0
    _print_usage()
    return 1
