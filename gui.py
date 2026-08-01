# -*- coding: utf-8 -*-
"""tkinter 图形界面"""
import os
import queue
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz

import core
import dlg
import ebook
import ocr
import taskmgr

VERSION = '1.2.1'


class App:
    def __init__(self, root):
        self.root = root
        root.title('PDF 书签工具 v%s%s' % (VERSION, '（带OCR版）' if ocr.HAS_OCR else ''))
        root.geometry('920x780')
        root.minsize(860, 600)

        pad = {'padx': 8, 'pady': 4}

        self._tm = taskmgr.TaskManager()
        self._task = None
        self._watchdog_id = None
        self._paused = False
        self._has_progress = False

        f1 = ttk.LabelFrame(root, text='1. 选择文件')
        f1.pack(fill='x', **pad)
        self.pdf_var = tk.StringVar()
        self.txt_var = tk.StringVar()
        row = ttk.Frame(f1)
        row.pack(fill='x', **pad)
        ttk.Label(row, text='PDF文件:').pack(side='left')
        ttk.Entry(row, textvariable=self.pdf_var).pack(side='left', fill='x', expand=True, padx=4)
        self.btn_browse_pdf = ttk.Button(row, text='浏览…', command=lambda: self.browse(self.pdf_var, '选择PDF/电子书文件', [('PDF/电子书', '*.pdf *.epub *.mobi *.azw3 *.prc'), ('全部', '*.*')]))
        self.btn_browse_pdf.pack(side='left')
        row = ttk.Frame(f1)
        row.pack(fill='x', **pad)
        ttk.Label(row, text='目录txt:').pack(side='left')
        ttk.Entry(row, textvariable=self.txt_var).pack(side='left', fill='x', expand=True, padx=4)
        self.btn_browse_txt = ttk.Button(row, text='浏览…', command=lambda: self.browse(self.txt_var, '选择目录txt', [('文本', '*.txt'), ('全部', '*.*')]))
        self.btn_browse_txt.pack(side='left')

        f2 = ttk.LabelFrame(root, text='2. 操作')
        f2.pack(fill='x', **pad)
        self.mode = tk.StringVar(value='write')
        row = ttk.Frame(f2)
        row.pack(fill='x', **pad)
        ttk.Radiobutton(row, text='写入书签（txt → PDF）', variable=self.mode, value='write',
                        command=self.on_mode).pack(side='left', padx=8)
        ttk.Radiobutton(row, text='提取书签/电子书目录', variable=self.mode, value='extract',
                        command=self.on_mode).pack(side='left', padx=8)
        ttk.Radiobutton(row, text='提取图片', variable=self.mode, value='images',
                        command=self.on_mode).pack(side='left', padx=8)
        f2b = ttk.Frame(f2)
        f2b.pack(fill='x', **pad)
        self.offset_var = tk.StringVar(value='')
        self.outmode_var = tk.StringVar(value='copy')
        self.w_opts = ttk.Frame(f2b)
        self.w_opts.pack(side='left', padx=12)
        ttk.Label(self.w_opts, text='正文第一页 PDF页号:').pack(side='left')
        self.first_pdf_var = tk.StringVar()
        ttk.Entry(self.w_opts, textvariable=self.first_pdf_var, width=5).pack(side='left', padx=4)
        ttk.Label(self.w_opts, text='印刷页码:').pack(side='left')
        self.first_print_var = tk.StringVar()
        ttk.Entry(self.w_opts, textvariable=self.first_print_var, width=5).pack(side='left', padx=4)
        self.hint(self.w_opts, '目录后第一个正文页，两项必填，自动算偏移').pack(side='left', padx=4)
        ttk.Radiobutton(self.w_opts, text='生成_带目录.pdf副本', variable=self.outmode_var, value='copy').pack(side='left')
        ttk.Radiobutton(self.w_opts, text='直接写原文件', variable=self.outmode_var, value='same').pack(side='left')
        self.e_opts = ttk.Frame(f2b)
        self.e_opts.pack(side='left', padx=12)
        self.e_pdfpage = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.e_opts, text='行尾带[PDF页号]', variable=self.e_pdfpage).pack(side='left')
        self.hint(self.e_opts, 'EPUB/MOBI无页码,[p序号]=阅读顺序').pack(side='left', padx=4)
        self.e_opts.pack_forget()
        self.i_opts = ttk.Frame(f2b)
        self.i_opts.pack(side='left', padx=12)
        ttk.Label(self.i_opts, text='dpi(空=内嵌):').pack(side='left')
        self.dpi_var = tk.StringVar()
        ttk.Entry(self.i_opts, textvariable=self.dpi_var, width=6).pack(side='left', padx=4)
        ttk.Label(self.i_opts, text='格式:').pack(side='left')
        self.fmt_var = tk.StringVar(value='orig')
        ttk.Combobox(self.i_opts, textvariable=self.fmt_var, width=7, state='readonly',
                     values=('orig', 'png', 'jpeg')).pack(side='left', padx=4)
        ttk.Label(self.i_opts, text='JPEG质量:').pack(side='left')
        self.quality_var = tk.StringVar(value='85')
        ttk.Entry(self.i_opts, textvariable=self.quality_var, width=4).pack(side='left', padx=4)
        self.i_opts.pack_forget()

        focr = ttk.LabelFrame(root, text='OCR 识别目录页')
        focr.pack(fill='x', **pad)
        if ocr.HAS_OCR:
            row = ttk.Frame(focr)
            row.pack(fill='x', **pad)
            ttk.Label(row, text='目录页PDF页号:').pack(side='left')
            self.ocr_range_var = tk.StringVar()
            ttk.Entry(row, textvariable=self.ocr_range_var, width=12).pack(side='left', padx=4)
            self.btn_ocr = ttk.Button(row, text='识别目录', command=self.do_ocr)
            self.btn_ocr.pack(side='left', padx=8)
            self.hint(row, '识别中可点底部[暂停]/[停止]；自动检测偏移；结果可编辑后[确认写入]', wrap=430).pack(side='left', padx=8)
        else:
            self.hint(focr, 'OCR组件未安装：本程序为“不带OCR版”。请下载“带OCR版”exe，或 pip install paddleocr 后运行源码。', wrap=760).pack(anchor='w', **pad)

        fprog = ttk.Frame(root)
        fprog.pack(fill='x', **pad)
        self.prog_label = ttk.Label(fprog, text='', anchor='w')
        self.prog_label.pack(side='left')
        self.prog = ttk.Progressbar(fprog, mode='determinate', maximum=100)
        self.prog.pack(side='left', fill='x', expand=True)

        f4 = ttk.Frame(root)
        f4.pack(fill='x', **pad)
        self.btn_confirm = ttk.Button(f4, text='写入OCR结果', width=14, command=self.confirm_ocr_write)
        self.btn_confirm.pack(side='left', padx=4)
        self.btn_save_ocr = ttk.Button(f4, text='保存OCR结果…', width=14, command=self.save_ocr_txt)
        self.btn_save_ocr.pack(side='left', padx=4)
        f4b = ttk.Frame(root)
        f4b.pack(fill='x', **pad)
        self.btn_run = ttk.Button(f4b, text='执行', width=8, command=self.run)
        self.btn_run.pack(side='right', padx=4)
        self.btn_pause = ttk.Button(f4b, text='暂停', width=6, command=self.toggle_pause, state='disabled')
        self.btn_pause.pack(side='right', padx=4)
        self.btn_stop = ttk.Button(f4b, text='停止', width=6, command=self.stop_task, state='disabled')
        self.btn_stop.pack(side='right', padx=4)
        ttk.Button(f4b, text='清空日志', width=8, command=lambda: self.log.delete('1.0', 'end')).pack(side='right', padx=4)

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

    def hint(self, parent, text, wrap=0):
        """灰色小字提示"""
        lb = ttk.Label(parent, text=text, foreground='gray')
        if wrap:
            lb.configure(wraplength=wrap, justify='left')
        return lb

    def browse(self, var, title, ftypes):
        p = filedialog.askopenfilename(title=title, filetypes=ftypes)
        if p:
            var.set(p)

    def on_mode(self):
        if self.mode.get() == 'write':
            self.w_opts.pack(side='left', padx=12)
            self.e_opts.pack_forget()
            self.i_opts.pack_forget()
        elif self.mode.get() == 'extract':
            self.w_opts.pack_forget()
            self.e_opts.pack(side='left', padx=12)
            self.i_opts.pack_forget()
        else:
            self.w_opts.pack_forget()
            self.e_opts.pack_forget()
            self.i_opts.pack(side='left', padx=12)

    def logln(self, s):
        self.log.insert('end', s + '\n')
        self.log.see('end')

    # ---- 后台任务 ----

    def _task_start(self, task_type, target, args):
        if self._task is not None:
            messagebox.showwarning('提示', '任务正在进行中')
            return
        self._task = task_type
        self._paused = False
        self._has_progress = False
        self.btn_pause.configure(text='暂停', state='normal')
        self.btn_stop.configure(state='normal')
        for b in (self.btn_run, self.btn_confirm, self.btn_save_ocr):
            b.configure(state='disabled')
        self.btn_browse_pdf.configure(state='disabled')
        self.btn_browse_txt.configure(state='disabled')
        if ocr.HAS_OCR:
            self.btn_ocr.configure(state='disabled')
        self._prog_reset()
        self.prog_label.configure(text='启动中…')
        self._tm.start(target, args)
        self._watchdog_schedule()
        self.root.after(100, self._poll_queue)

    def _task_end(self):
        self._task = None
        self._paused = False
        self._tm.finish()
        if self._watchdog_id is not None:
            self.root.after_cancel(self._watchdog_id)
            self._watchdog_id = None
        self.prog.stop()
        self.prog.configure(mode='determinate', value=0)
        self.prog_label.configure(text='')
        for b in (self.btn_run, self.btn_confirm, self.btn_save_ocr):
            b.configure(state='normal')
        self.btn_browse_pdf.configure(state='normal')
        self.btn_browse_txt.configure(state='normal')
        if ocr.HAS_OCR:
            self.btn_ocr.configure(state='normal')
        self.btn_pause.configure(state='disabled')
        self.btn_stop.configure(state='disabled')

    def _prog_reset(self):
        self.prog.stop()
        self.prog.configure(mode='determinate', value=0, maximum=100)

    def _prog_show(self, d, t, m):
        if self._task is None:
            return
        self._has_progress = True
        if self._watchdog_id is not None:
            self.root.after_cancel(self._watchdog_id)
            self._watchdog_id = None
        self.prog.configure(maximum=max(t, 1), value=max(d, 0))
        self.prog_label.configure(text=m if m else '')
        self._watchdog_schedule()

    def _watchdog_schedule(self):
        if self._watchdog_id is not None:
            self.root.after_cancel(self._watchdog_id)
        self._watchdog_id = self.root.after(4000, self._watchdog)

    def _watchdog(self):
        self._watchdog_id = None
        if self._task is None:
            return
        # 保持填充式进度条，仅更新提示文字（模型加载/打开文件等阶段无进度信息）
        if self._has_progress:
            self.prog_label.configure(text='处理中…（长时间无进度更新）')
        else:
            self.prog_label.configure(text='打开/加载中…')

    def _poll_queue(self):
        if self._task is None:
            return
        msg = self._tm.poll()
        if msg is None:
            self.root.after(100, self._poll_queue)
            return
        kind = msg[0]
        if kind == 'progress':
            _, d, t, m = msg
            self._prog_show(d, t, m)
        elif kind == 'done':
            self._task_end()
            _, out_dir, count = msg
            self.logln('提取完成: %d 张%s' % (count, '页面' if self._task_type() == 'images' else '图片'))
            self.logln('已保存到: %s' % out_dir)
            messagebox.showinfo('完成', '已提取 %d 张图片\n%s' % (count, out_dir))
        elif kind == 'done_write':
            self._task_end()
            _, out, n = msg
            self.logln('写入完成: %s（%d 条书签）' % (out, n))
            messagebox.showinfo('完成', '已写入 %d 条书签\n%s' % (n, out))
        elif kind == 'extract_done':
            self._task_end()
            _, is_ebook, entries, out = msg
            self.logln('提取完成: %d 条%s' % (len(entries), '目录（[p序号]为阅读顺序号，非页码）' if is_ebook else '书签'))
            for it in entries[:20]:
                if is_ebook:
                    ind, title, seq = it
                    self.logln('%s%s [p%d]' % ('  ' * ind, title, seq))
                else:
                    lvl, title, page = it
                    self.logln('%s%s [p%d]' % ('  ' * (lvl - 1), title, page))
            if len(entries) > 20:
                self.logln('... 共 %d 条' % len(entries))
            self.logln('已保存到: %s' % out)
            messagebox.showinfo('完成', '已提取 %d 条%s\n%s' % (len(entries), '目录' if is_ebook else '书签', out))
        elif kind == 'extract_none':
            self._task_end()
            self.logln('该PDF没有书签。')
            messagebox.showinfo('提示', '该PDF没有书签。')
        elif kind == 'ocr_done':
            _, txt = msg
            self.ocr_text.delete('1.0', 'end')
            self.ocr_text.insert('1.0', txt)
            self.nb.select(1)
            self.logln('OCR完成。可在“OCR结果”页签修改后点[确认写入]。')
        elif kind == 'ocr_offset':
            self._task_end()
            _, offset = msg
            if offset is not None:
                self.logln('自动检测：PDF页偏移=%d。写入书签时请在“2.操作”填正文第一页的PDF页号和印刷页码。' % offset)
            else:
                self.logln('自动检测偏移失败。请在“2.操作”填正文第一页的PDF页号和印刷页码。')
        elif kind == 'error':
            self._task_end()
            e = msg[1]
            if '已取消' in e or 'TaskCancelled' in e:
                self.logln('任务已取消')
                messagebox.showinfo('提示', '任务已取消')
            else:
                self.logln('错误: %s' % e)
                messagebox.showerror('错误', e)
        self.root.after(100, self._poll_queue)

    def _task_type(self):
        return self._task or ''

    def toggle_pause(self):
        if self._task is None:
            return
        if self._paused:
            self._tm.pause_event.clear()
            self._paused = False
            self.btn_pause.configure(text='暂停')
            self.logln('已继续')
        else:
            self._tm.pause_event.set()
            self._paused = True
            self.btn_pause.configure(text='继续')
            self.logln('已暂停（当前页完成后停止）')

    def stop_task(self):
        if self._task is None:
            return
        self.logln('正在停止…')
        self._tm.cancel_event.set()

    # ---- 前台操作 ----

    def run(self):
        try:
            if self.mode.get() == 'write':
                self.do_write()
            elif self.mode.get() == 'extract':
                self.do_extract()
            else:
                self.do_images()
        except Exception as e:
            self.logln('错误: %s' % e)
            messagebox.showerror('错误', str(e))

    def do_images(self):
        pdf = self.pdf_var.get().strip()
        if not pdf:
            raise ValueError('请选择PDF文件')
        if not os.path.isfile(pdf):
            raise ValueError('PDF文件不存在: %s' % pdf)
        dpi_text = self.dpi_var.get().strip()
        dpi = int(dpi_text) if dpi_text else None
        if dpi is not None and not (1 <= dpi <= 1000):
            raise ValueError('分辨率dpi应在1-1000之间')
        fmt = self.fmt_var.get().strip().lower() or 'orig'
        quality = int(self.quality_var.get().strip() or '85')
        if not (1 <= quality <= 100):
            raise ValueError('JPEG质量应在1-100之间')
        self.logln('正在提取%s（%s）…' % ('页面' if dpi else 'PDF内嵌图片', fmt))
        self._task_start('images', core._mp_extract_images,
                         (pdf, dpi, fmt, quality, self._tm.cancel_event, self._tm.pause_event))

    def do_write(self):
        pdf = self.pdf_var.get().strip()
        txt = self.txt_var.get().strip()
        if not pdf or not txt:
            raise ValueError('请选择PDF文件和目录txt')
        if not os.path.isfile(pdf):
            raise ValueError('PDF文件不存在: %s' % pdf)
        if not os.path.isfile(txt):
            raise ValueError('txt文件不存在: %s' % txt)
        offset = self._field_offset()
        if offset is None:
            fields = self._ask_offset_fields()
            if fields is None:
                self.logln('已取消：未填写偏移信息')
                return
            self.first_pdf_var.set(str(fields[0]))
            self.first_print_var.set(str(fields[1]))
            offset = self._field_offset()
            if offset is None:
                self.logln('提示: 偏移信息无效')
                return
        entries = core.levels_from_indent(core.parse_toc(txt))
        toc = core.build_toc(entries, offset)
        self.logln('解析完成: %d 条书签（偏移=%d）' % (len(toc), offset))
        for lvl, title, page in toc[:20]:
            self.logln('%s%s [p%d]' % ('  ' * (lvl - 1), title, page))
        if len(toc) > 20:
            self.logln('... 共 %d 条' % len(toc))
        self.logln('正在写入书签（后台）…')
        self._task_start('write', core._mp_write_toc,
                         (pdf, toc, self.outmode_var.get()))

    def do_extract(self):
        path = self.pdf_var.get().strip()
        if not path:
            raise ValueError('请选择PDF或电子书文件')
        if not os.path.isfile(path):
            raise ValueError('文件不存在: %s' % path)
        self.logln('正在读取目录（后台）…')
        self._task_start('extract', core._mp_extract_toc,
                         (path, self.e_pdfpage.get()))

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

    def _field_offset(self):
        """由“正文第一页的PDF页号+印刷页码”计算偏移量；缺失/非法返回None"""
        try:
            pdf = int(self.first_pdf_var.get().strip())
            prt = int(self.first_print_var.get().strip())
            if pdf < 1 or prt < 1:
                return None
            return (pdf - 1) - prt
        except ValueError:
            return None

    def _ask_offset_fields(self):
        """弹框要求填写正文第一页的PDF页号和印刷页码；返回 (pdf_no, print_no) 或 None(取消)"""
        return dlg.ask_offset_fields(self.root, self.first_pdf_var, self.first_print_var)

    def do_ocr(self):
        try:
            pdf = self.pdf_var.get().strip()
            rng = self.ocr_range_var.get().strip()
            if (not pdf or not os.path.isfile(pdf)
                    or not re.match(r'^\s*\d+\s*[-—–]\s*\d+\s*$', rng)):
                fields = dlg.ask_ocr_args(self.root, self.pdf_var, self.ocr_range_var)
                if fields is None:
                    self.logln('已取消：未填写OCR识别信息')
                    return
                self.pdf_var.set(fields[0])
                self.ocr_range_var.set(fields[1])
            pdf, start, end = self._get_ocr_args()
            self.logln('加载OCR引擎并识别 PDF页 %d-%d …（首次运行下载模型，约需几分钟）' % (start, end))
            self._task_start('ocr', ocr._mp_ocr_task,
                             (pdf, start, end, self._tm.cancel_event, self._tm.pause_event))
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
            offset = self._field_offset()
            if offset is None:
                fields = self._ask_offset_fields()
                if fields is None:
                    self.logln('已取消：未填写偏移信息')
                    return
                self.first_pdf_var.set(str(fields[0]))
                self.first_print_var.set(str(fields[1]))
                offset = self._field_offset()
                if offset is None:
                    self.logln('提示: 偏移信息无效')
                    return
            entries = core.levels_from_indent(core.parse_toc(tmp))
            toc = core.build_toc(entries, offset)
            self.logln('解析OCR结果: %d 条书签（偏移=%d）' % (len(toc), offset))
            self.logln('正在写入书签（后台）…')
            self._task_start('write', core._mp_write_toc,
                             (pdf, toc, self.outmode_var.get()))
        except Exception as e:
            self.logln('错误: %s' % e)
            messagebox.showerror('错误', str(e))

    def save_ocr_txt(self):
        txt = self.ocr_text.get('1.0', 'end')
        if not txt.strip():
            messagebox.showwarning('提示', 'OCR结果为空')
            return
        pdf = self.pdf_var.get().strip()
        base, _ = os.path.splitext(pdf)
        out = (base + '_目录.txt') if base else 'OCR结果_目录.txt'
        with open(out, 'w', encoding='utf-8-sig') as f:
            f.write(txt)
        self.logln('已保存: %s' % out)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
