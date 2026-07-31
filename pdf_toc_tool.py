# -*- coding: utf-8 -*-
"""PDF 书签工具：提取目录 / 写入书签
支持两种目录txt格式：
  格式A：缩进 + 标题 + 页码（可带 … 点线、可带页码范围 1–12、或 [p页号]）
  格式B：无页码行自动作为上一标题的续行
"""
import os
import re
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz

VERSION = "1.0"

# ---------------- 核心逻辑 ----------------

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


def export_toc_txt(toc, out_path, with_pdf_page=True):
    """书签导出为txt。缩进2空格/级；with_pdf_page时行尾带 [p页号]"""
    with open(out_path, 'w', encoding='utf-8-sig') as f:
        for lvl, title, page in toc:
            ind = '  ' * (lvl - 1)
            if with_pdf_page:
                f.write('%s%s [p%d]\n' % (ind, title, page))
            else:
                f.write('%s%s %d\n' % (ind, title, page))


# ---------------- GUI ----------------

class App:
    def __init__(self, root):
        self.root = root
        root.title('PDF 书签工具 v%s' % VERSION)
        root.geometry('760x560')

        pad = {'padx': 8, 'pady': 4}

        f1 = ttk.LabelFrame(root, text='1. 选择文件')
        f1.pack(fill='x', **pad)
        self.pdf_var = tk.StringVar()
        self.txt_var = tk.StringVar()
        row = ttk.Frame(f1)
        row.pack(fill='x', **pad)
        ttk.Label(row, text='PDF文件:').pack(side='left')
        ttk.Entry(row, textvariable=self.pdf_var).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(row, text='浏览…', command=lambda: self.browse(self.pdf_var, '选择PDF文件', [('PDF', '*.pdf')])).pack(side='left')
        row = ttk.Frame(f1)
        row.pack(fill='x', **pad)
        ttk.Label(row, text='目录txt:').pack(side='left')
        ttk.Entry(row, textvariable=self.txt_var).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(row, text='浏览…', command=lambda: self.browse(self.txt_var, '选择目录txt', [('文本', '*.txt'), ('全部', '*.*')])).pack(side='left')

        f2 = ttk.LabelFrame(root, text='2. 操作')
        f2.pack(fill='x', **pad)
        self.mode = tk.StringVar(value='write')
        ttk.Radiobutton(f2, text='写入书签（txt → PDF）', variable=self.mode, value='write',
                        command=self.on_mode).pack(side='left', padx=8, pady=4)
        ttk.Radiobutton(f2, text='提取书签（PDF → txt）', variable=self.mode, value='extract',
                        command=self.on_mode).pack(side='left', padx=8, pady=4)
        self.offset_var = tk.StringVar(value='15')
        self.outmode_var = tk.StringVar(value='copy')
        self.w_opts = ttk.Frame(f2)
        self.w_opts.pack(side='left', padx=12)
        ttk.Label(self.w_opts, text='印刷页偏移(0-based索引差, 卷1-3=15, 卷4=13):').pack(side='left')
        ttk.Entry(self.w_opts, textvariable=self.offset_var, width=6).pack(side='left', padx=4)
        ttk.Radiobutton(self.w_opts, text='生成副本_带目录.pdf', variable=self.outmode_var, value='copy').pack(side='left')
        ttk.Radiobutton(self.w_opts, text='直接写原文件', variable=self.outmode_var, value='same').pack(side='left')
        self.e_opts = ttk.Frame(f2)
        self.e_opts.pack(side='left', padx=12)
        self.e_pdfpage = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.e_opts, text='行尾带[PDF页号]（便于改后回写）', variable=self.e_pdfpage).pack(side='left')
        self.e_opts.pack_forget()

        f3 = ttk.LabelFrame(root, text='3. 预览 / 日志')
        f3.pack(fill='both', expand=True, **pad)
        self.log = tk.Text(f3, height=16, font=('Consolas', 9))
        self.log.pack(fill='both', expand=True, padx=6, pady=6)

        f4 = ttk.Frame(root)
        f4.pack(fill='x', **pad)
        ttk.Button(f4, text='执行', command=self.run).pack(side='right', padx=4)
        ttk.Button(f4, text='清空日志', command=lambda: self.log.delete('1.0', 'end')).pack(side='right', padx=4)

    def browse(self, var, title, ftypes):
        p = filedialog.askopenfilename(title=title, filetypes=ftypes)
        if p:
            var.set(p)

    def on_mode(self):
        if self.mode.get() == 'write':
            self.w_opts.pack(side='left', padx=12)
            self.e_opts.pack_forget()
        else:
            self.w_opts.pack_forget()
            self.e_opts.pack(side='left', padx=12)

    def logln(self, s):
        self.log.insert('end', s + '\n')
        self.log.see('end')

    def run(self):
        try:
            if self.mode.get() == 'write':
                self.do_write()
            else:
                self.do_extract()
        except Exception as e:
            self.logln('错误: %s' % e)
            messagebox.showerror('错误', str(e))

    def do_write(self):
        pdf = self.pdf_var.get().strip()
        txt = self.txt_var.get().strip()
        if not pdf or not txt:
            raise ValueError('请选择PDF文件和目录txt')
        if not os.path.isfile(pdf):
            raise ValueError('PDF文件不存在: %s' % pdf)
        if not os.path.isfile(txt):
            raise ValueError('txt文件不存在: %s' % txt)
        offset = int(self.offset_var.get().strip() or '15')
        entries = parse_toc(txt)
        entries = levels_from_indent(entries)
        toc = build_toc(entries, offset)
        self.logln('解析完成: %d 条书签' % len(toc))
        for lvl, title, page in toc[:20]:
            self.logln('%s%s [p%d]' % ('  ' * (lvl - 1), title, page))
        if len(toc) > 20:
            self.logln('... 共 %d 条' % len(toc))
        if not messagebox.askyesno('确认', '共解析出 %d 条书签，确认写入？' % len(toc)):
            return
        out = write_toc(pdf, toc, self.outmode_var.get())
        d = fitz.open(out)
        n = len(d.get_toc())
        d.close()
        self.logln('写入完成: %s（%d 条书签）' % (out, n))
        messagebox.showinfo('完成', '已写入 %d 条书签\n%s' % (n, out))

    def do_extract(self):
        pdf = self.pdf_var.get().strip()
        if not pdf:
            raise ValueError('请选择PDF文件')
        if not os.path.isfile(pdf):
            raise ValueError('PDF文件不存在: %s' % pdf)
        toc = read_toc(pdf)
        if not toc:
            self.logln('该PDF没有书签。')
            return
        base, ext = os.path.splitext(pdf)
        out = base + '_书签' + ext.replace('.pdf', '.txt')
        export_toc_txt(toc, out, self.e_pdfpage.get())
        self.logln('提取完成: %d 条书签' % len(toc))
        for lvl, title, page in toc[:20]:
            self.logln('%s%s [p%d]' % ('  ' * (lvl - 1), title, page))
        if len(toc) > 20:
            self.logln('... 共 %d 条' % len(toc))
        self.logln('已保存到: %s' % out)
        messagebox.showinfo('完成', '已提取 %d 条书签\n%s' % (len(toc), out))


def main():
    args = sys.argv[1:]
    if args:
        cmd = args[0]
        if cmd == 'extract' and len(args) >= 2:
            pdf = args[1]
            out = args[2] if len(args) > 2 else os.path.splitext(pdf)[0] + '_书签.txt'
            toc = read_toc(pdf)
            if not toc:
                print('该PDF没有书签。')
                return 1
            export_toc_txt(toc, out)
            print('提取 %d 条书签 -> %s' % (len(toc), out))
            return 0
        if cmd == 'write' and len(args) >= 3:
            pdf, txt = args[1], args[2]
            offset = int(args[3]) if len(args) > 3 else 15
            mode = args[4] if len(args) > 4 else 'copy'
            entries = levels_from_indent(parse_toc(txt))
            toc = build_toc(entries, offset)
            out = write_toc(pdf, toc, mode)
            print('写入 %d 条书签 -> %s' % (len(toc), out))
            return 0
        print('用法:')
        print('  %s extract <pdf> [输出txt]' % os.path.basename(sys.argv[0]))
        print('  %s write <pdf> <目录txt> [偏移(默认15)] [copy|same]' % os.path.basename(sys.argv[0]))
        return 1
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
