# -*- coding: utf-8 -*-
"""tkinter 图形界面"""
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz

import core
import ocr

VERSION = '1.1'


class App:
    def __init__(self, root):
        self.root = root
        root.title('PDF 书签工具 v%s%s' % (VERSION, '（带OCR版）' if ocr.HAS_OCR else ''))
        root.geometry('820x760')

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

        focr = ttk.LabelFrame(root, text='OCR 识别目录页（扫描目录 → 可编辑txt → 写入）')
        focr.pack(fill='x', **pad)
        if ocr.HAS_OCR:
            row = ttk.Frame(focr)
            row.pack(fill='x', **pad)
            ttk.Label(row, text='目录页PDF页号(如 11-16):').pack(side='left')
            self.ocr_range_var = tk.StringVar()
            ttk.Entry(row, textvariable=self.ocr_range_var, width=12).pack(side='left', padx=4)
            ttk.Button(row, text='识别目录', command=self.do_ocr).pack(side='left', padx=8)
            ttk.Label(row, text='识别后自动检测页码偏移；结果见“OCR结果”页签，可编辑后点[确认写入]。首次运行下载OCR模型，需联网。').pack(side='left', padx=8)
            row2 = ttk.Frame(focr)
            row2.pack(fill='x', **pad)
            ttk.Label(row2, text='偏移自动检测失败时：填正文第一页的').pack(side='left')
            ttk.Label(row2, text='PDF页号:').pack(side='left')
            self.first_pdf_var = tk.StringVar()
            ttk.Entry(row2, textvariable=self.first_pdf_var, width=5).pack(side='left', padx=2)
            ttk.Label(row2, text='印刷页码:').pack(side='left')
            self.first_print_var = tk.StringVar()
            ttk.Entry(row2, textvariable=self.first_print_var, width=5).pack(side='left', padx=2)
            ttk.Button(row2, text='算偏移', command=self.calc_offset).pack(side='left', padx=8)
        else:
            ttk.Label(focr, text='OCR组件未安装：本程序为“不带OCR版”。请下载“带OCR版”exe，或 pip install paddleocr 后运行源码。').pack(anchor='w', **pad)

        f3 = ttk.LabelFrame(root, text='3. 预览 / 日志')
        f3.pack(fill='both', expand=True, **pad)
        self.nb = ttk.Notebook(f3)
        self.nb.pack(fill='both', expand=True, padx=6, pady=6)
        f_log = ttk.Frame(self.nb)
        self.log = tk.Text(f_log, font=('Consolas', 9))
        sb = ttk.Scrollbar(f_log, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.nb.add(f_log, text='日志')
        f_ocr = ttk.Frame(self.nb)
        self.ocr_text = tk.Text(f_ocr, font=('Consolas', 9), wrap='none')
        sb2 = ttk.Scrollbar(f_ocr, command=self.ocr_text.yview)
        self.ocr_text.configure(yscrollcommand=sb2.set)
        sb2.pack(side='right', fill='y')
        self.ocr_text.pack(side='left', fill='both', expand=True)
        self.nb.add(f_ocr, text='OCR结果（可编辑）')

        f4 = ttk.Frame(root)
        f4.pack(fill='x', **pad)
        ttk.Button(f4, text='确认写入（使用OCR结果）', command=self.confirm_ocr_write).pack(side='left', padx=4)
        ttk.Button(f4, text='保存OCR结果txt…', command=self.save_ocr_txt).pack(side='left', padx=4)
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
        entries = core.levels_from_indent(core.parse_toc(txt))
        toc = core.build_toc(entries, offset)
        self.logln('解析完成: %d 条书签' % len(toc))
        for lvl, title, page in toc[:20]:
            self.logln('%s%s [p%d]' % ('  ' * (lvl - 1), title, page))
        if len(toc) > 20:
            self.logln('... 共 %d 条' % len(toc))
        if not messagebox.askyesno('确认', '共解析出 %d 条书签，确认写入？' % len(toc)):
            return
        out = core.write_toc(pdf, toc, self.outmode_var.get())
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
        toc = core.read_toc(pdf)
        if not toc:
            self.logln('该PDF没有书签。')
            return
        base, ext = os.path.splitext(pdf)
        out = base + '_书签' + ext.replace('.pdf', '.txt')
        core.export_toc_txt(toc, out, self.e_pdfpage.get())
        self.logln('提取完成: %d 条书签' % len(toc))
        for lvl, title, page in toc[:20]:
            self.logln('%s%s [p%d]' % ('  ' * (lvl - 1), title, page))
        if len(toc) > 20:
            self.logln('... 共 %d 条' % len(toc))
        self.logln('已保存到: %s' % out)
        messagebox.showinfo('完成', '已提取 %d 条书签\n%s' % (len(toc), out))

    def _get_ocr_args(self):
        pdf = self.pdf_var.get().strip()
        if not pdf:
            raise ValueError('请先在“选择文件”里选择PDF')
        if not os.path.isfile(pdf):
            raise ValueError('PDF文件不存在: %s' % pdf)
        m = re.match(r'^\s*(\d+)\s*[-—–]\s*(\d+)\s*$', self.ocr_range_var.get().strip())
        if not m:
            raise ValueError('请填写目录页PDF页号范围，如 11-16')
        start, end = int(m.group(1)), int(m.group(2))
        return pdf, start, end

    def calc_offset(self):
        try:
            pdf = int(self.first_pdf_var.get().strip())
            prt = int(self.first_print_var.get().strip())
            if pdf < 1 or prt < 1:
                raise ValueError
            offset = (pdf - 1) - prt
            self.offset_var.set(str(offset))
            self.logln('偏移 = %d（印刷页 %d 对应PDF索引 %d）' % (offset, prt, pdf - 1))
        except ValueError:
            messagebox.showerror('错误', '请填写有效的正文第一页PDF页号和印刷页码')

    def do_ocr(self):
        try:
            pdf, start, end = self._get_ocr_args()
            self.logln('加载OCR引擎（首次运行会下载模型，约需几分钟）…')
            self.root.update()
            engine = ocr.load_ocr()
            self.logln('开始识别 PDF页 %d-%d …' % (start, end))
            self.root.update()
            txt = ocr.ocr_to_txt(pdf, start, end, ocr=engine, progress=self.logln)
            self.ocr_text.delete('1.0', 'end')
            self.ocr_text.insert('1.0', txt)
            self.nb.select(1)
            self.logln('OCR完成。可在“OCR结果”页签修改后点[确认写入]。')
            offset, n0 = ocr.detect_offset(pdf, start, end, ocr=engine)
            if offset is not None:
                self.offset_var.set(str(offset))
                self.logln('自动检测：PDF页偏移=%d（已填入偏移框，可手动修改）' % offset)
            else:
                self.logln('自动检测偏移失败。请在下方填“正文第一页”的PDF页号和印刷页码，点[算偏移]。')
        except Exception as e:
            self.logln('OCR错误: %s' % e)
            messagebox.showerror('OCR错误', str(e))

    def confirm_ocr_write(self):
        try:
            pdf = self.pdf_var.get().strip()
            txt = self.ocr_text.get('1.0', 'end')
            if not txt.strip():
                raise ValueError('OCR结果为空，请先执行识别')
            if not pdf:
                raise ValueError('请选择PDF文件')
            if not os.path.isfile(pdf):
                raise ValueError('PDF文件不存在: %s' % pdf)
            tmp = os.path.join(os.environ.get('TEMP', '.'), '_pdf_toc_ocr_preview.txt')
            with open(tmp, 'w', encoding='utf-8-sig') as f:
                f.write(txt)
            offset = int(self.offset_var.get().strip() or '15')
            entries = core.levels_from_indent(core.parse_toc(tmp))
            toc = core.build_toc(entries, offset)
            self.logln('解析OCR结果: %d 条书签' % len(toc))
            if not messagebox.askyesno('确认', 'OCR结果共 %d 条书签，确认写入？' % len(toc)):
                return
            out = core.write_toc(pdf, toc, self.outmode_var.get())
            d = fitz.open(out)
            n = len(d.get_toc())
            d.close()
            self.logln('写入完成: %s（%d 条书签）' % (out, n))
            messagebox.showinfo('完成', '已写入 %d 条书签\n%s' % (n, out))
        except Exception as e:
            self.logln('错误: %s' % e)
            messagebox.showerror('错误', str(e))

    def save_ocr_txt(self):
        txt = self.ocr_text.get('1.0', 'end')
        if not txt.strip():
            messagebox.showwarning('提示', 'OCR结果为空')
            return
        p = filedialog.asksaveasfilename(title='保存OCR结果', defaultextension='.txt',
                                         filetypes=[('文本', '*.txt')])
        if p:
            with open(p, 'w', encoding='utf-8-sig') as f:
                f.write(txt)
            self.logln('已保存: %s' % p)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
