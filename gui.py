# -*- coding: utf-8 -*-
"""tkinter 图形界面"""
import os
import re
import tkinter as tk
from typing import Callable, List, Optional, Tuple
from tkinter import filedialog, messagebox, ttk

import fitz

import core
import dlg
import ebook
import ocr
import taskmgr

# 拖拽支持（可选依赖）：未安装时降级为普通窗口，不影响其他功能
HAS_DND = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    TkinterDnD = None

VERSION = '1.2.1'

# ---- 常量 ----
PADDING = {'padx': 8, 'pady': 4}
RE_PAGE_RANGE = re.compile(r'^\s*(\d+)\s*[-—–]\s*(\d+)\s*$')
PDF_EXTENSIONS = {'.pdf', '.epub', '.mobi', '.azw3', '.prc', '.azw'}
TXT_EXTENSION = '.txt'
DPI_MIN = 1
DPI_MAX = 1000
DEFAULT_JPEG_QUALITY = 85
JPEG_QUALITY_MIN = 1
JPEG_QUALITY_MAX = 100
LOG_PREVIEW_COUNT = 20       # 日志区预览的最大条数
WATCHDOG_INTERVAL_MS = 4000  # 长时间无进度时的提示更新间隔
POLL_INTERVAL_MS = 100       # 消息队列轮询间隔
OCR_PREVIEW_FILE_NAME = '_pdf_toc_ocr_preview.txt'  # 确认写入时的临时解析文件


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('PDF 书签工具 v%s%s' % (VERSION, '（带OCR版）' if ocr.HAS_OCR else ''))
        root.geometry('920x780')
        root.minsize(860, 600)

        self._tm = taskmgr.TaskManager()
        self._task = None
        self._watchdog_id = None
        self._paused = False
        self._has_progress = False

        # ---- 1. 选择文件 ----
        file_frame = ttk.LabelFrame(root, text='1. 选择文件')
        file_frame.pack(fill='x', **PADDING)
        self.pdf_var = tk.StringVar()
        self.txt_var = tk.StringVar()
        row_frame = ttk.Frame(file_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Label(row_frame, text='PDF文件:').pack(side='left')
        self.pdf_entry = ttk.Entry(row_frame, textvariable=self.pdf_var)
        self.pdf_entry.pack(side='left', fill='x', expand=True, padx=4)
        self._register_drop(self.pdf_entry)
        self.btn_browse_pdf = ttk.Button(
            row_frame, text='浏览…',
            command=lambda: self.browse(self.pdf_var, '选择PDF/电子书文件',
                                        [('PDF/电子书', '*.pdf *.epub *.mobi *.azw3 *.prc'), ('全部', '*.*')]))
        self.btn_browse_pdf.pack(side='left')
        row_frame = ttk.Frame(file_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Label(row_frame, text='目录txt:').pack(side='left')
        self.txt_entry = ttk.Entry(row_frame, textvariable=self.txt_var)
        self.txt_entry.pack(side='left', fill='x', expand=True, padx=4)
        self._register_drop(self.txt_entry)
        self.btn_browse_txt = ttk.Button(
            row_frame, text='浏览…',
            command=lambda: self.browse(self.txt_var, '选择目录txt', [('文本', '*.txt'), ('全部', '*.*')]))
        self.btn_browse_txt.pack(side='left')

        # ---- 2. 操作 ----
        action_frame = ttk.LabelFrame(root, text='2. 操作')
        action_frame.pack(fill='x', **PADDING)
        self.mode = tk.StringVar(value='write')
        row_frame = ttk.Frame(action_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Radiobutton(row_frame, text='写入书签（txt → PDF）', variable=self.mode, value='write',
                        command=self.on_mode).pack(side='left', padx=8)
        ttk.Radiobutton(row_frame, text='提取书签/电子书目录', variable=self.mode, value='extract',
                        command=self.on_mode).pack(side='left', padx=8)
        ttk.Radiobutton(row_frame, text='提取图片', variable=self.mode, value='images',
                        command=self.on_mode).pack(side='left', padx=8)
        action_options_frame = ttk.Frame(action_frame)
        action_options_frame.pack(fill='x', **PADDING)
        self.outmode_var = tk.StringVar(value='copy')
        self.write_options = ttk.Frame(action_options_frame)
        self.write_options.pack(side='left', padx=12)
        ttk.Label(self.write_options, text='正文第一页 PDF页号:').pack(side='left')
        self.first_pdf_var = tk.StringVar()
        ttk.Entry(self.write_options, textvariable=self.first_pdf_var, width=5).pack(side='left', padx=4)
        ttk.Label(self.write_options, text='印刷页码:').pack(side='left')
        self.first_print_var = tk.StringVar()
        ttk.Entry(self.write_options, textvariable=self.first_print_var, width=5).pack(side='left', padx=4)
        self.hint(self.write_options, '目录后第一个正文页，两项必填，自动算偏移').pack(side='left', padx=4)
        ttk.Radiobutton(self.write_options, text='生成_带目录.pdf副本', variable=self.outmode_var,
                        value='copy').pack(side='left')
        ttk.Radiobutton(self.write_options, text='直接写原文件', variable=self.outmode_var,
                        value='same').pack(side='left')
        self.extract_options = ttk.Frame(action_options_frame)
        self.extract_options.pack(side='left', padx=12)
        self.e_pdfpage = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.extract_options, text='行尾带[PDF页号]',
                        variable=self.e_pdfpage).pack(side='left')
        self.hint(self.extract_options, 'EPUB/MOBI无页码,[p序号]=阅读顺序').pack(side='left', padx=4)
        self.extract_options.pack_forget()
        self.image_options = ttk.Frame(action_options_frame)
        self.image_options.pack(side='left', padx=12)
        ttk.Label(self.image_options, text='dpi(空=内嵌):').pack(side='left')
        self.dpi_var = tk.StringVar()
        ttk.Entry(self.image_options, textvariable=self.dpi_var, width=6).pack(side='left', padx=4)
        ttk.Label(self.image_options, text='格式:').pack(side='left')
        self.fmt_var = tk.StringVar(value='orig')
        ttk.Combobox(self.image_options, textvariable=self.fmt_var, width=7, state='readonly',
                     values=('orig', 'png', 'jpeg')).pack(side='left', padx=4)
        ttk.Label(self.image_options, text='JPEG质量:').pack(side='left')
        self.quality_var = tk.StringVar(value=str(DEFAULT_JPEG_QUALITY))
        ttk.Entry(self.image_options, textvariable=self.quality_var, width=4).pack(side='left', padx=4)
        self.image_options.pack_forget()

        # ---- OCR 识别目录页 ----
        ocr_frame = ttk.LabelFrame(root, text='OCR 识别目录页')
        ocr_frame.pack(fill='x', **PADDING)
        if ocr.HAS_OCR:
            row_frame = ttk.Frame(ocr_frame)
            row_frame.pack(fill='x', **PADDING)
            ttk.Label(row_frame, text='页号范围:').pack(side='left')
            self.ocr_range_var = tk.StringVar()
            ttk.Entry(row_frame, textvariable=self.ocr_range_var, width=12).pack(side='left', padx=4)
            self.btn_ocr = ttk.Button(row_frame, text='识别目录', command=self.do_ocr)
            self.btn_ocr.pack(side='left', padx=8)
            self.btn_ocr_text = ttk.Button(row_frame, text='识别文字', command=self.do_ocr_text)
            self.btn_ocr_text.pack(side='left', padx=8)
            self.hint(row_frame, '识别中可点底部[暂停]/[停止]；[识别目录]自动检测偏移；结果可编辑后[确认写入]或[保存OCR结果…]',
                      wrap=430).pack(side='left', padx=8)
        else:
            self.hint(ocr_frame,
                      'OCR组件未安装：本程序为"不带OCR版"。请下载"带OCR版"exe，或 pip install paddleocr 后运行源码。',
                      wrap=760).pack(anchor='w', **PADDING)

        # 进度条与按钮行放在日志区之前，窗口缩小时不遮挡操作按钮
        progress_frame = ttk.Frame(root)
        progress_frame.pack(fill='x', **PADDING)
        self.prog_label = ttk.Label(progress_frame, text='', anchor='w')
        self.prog_label.pack(side='left')
        self.prog = ttk.Progressbar(progress_frame, mode='determinate', maximum=100)
        self.prog.pack(side='left', fill='x', expand=True)

        button_frame = ttk.Frame(root)
        button_frame.pack(fill='x', **PADDING)
        self.btn_confirm = ttk.Button(button_frame, text='写入OCR结果', width=14,
                                      command=self.confirm_ocr_write)
        self.btn_confirm.pack(side='left', padx=4)
        self.btn_save_ocr = ttk.Button(button_frame, text='保存OCR结果…', width=14,
                                       command=self.save_ocr_txt)
        self.btn_save_ocr.pack(side='left', padx=4)
        run_button_frame = ttk.Frame(root)
        run_button_frame.pack(fill='x', **PADDING)
        self.btn_run = ttk.Button(run_button_frame, text='执行', width=8, command=self.run)
        self.btn_run.pack(side='right', padx=4)
        self.btn_pause = ttk.Button(run_button_frame, text='暂停', width=6,
                                    command=self.toggle_pause, state='disabled')
        self.btn_pause.pack(side='right', padx=4)
        self.btn_stop = ttk.Button(run_button_frame, text='停止', width=6,
                                   command=self.stop_task, state='disabled')
        self.btn_stop.pack(side='right', padx=4)
        ttk.Button(run_button_frame, text='清空日志', width=8,
                   command=lambda: self.log.delete('1.0', 'end')).pack(side='right', padx=4)

        # ---- 3. 预览 / 日志 ----
        log_frame = ttk.LabelFrame(root, text='3. 预览 / 日志')
        log_frame.pack(fill='both', expand=True, **PADDING)
        self.nb = ttk.Notebook(log_frame)
        self.nb.pack(fill='both', expand=True, padx=6, pady=6)
        log_tab = ttk.Frame(self.nb)
        self.log = tk.Text(log_tab, font=('Consolas', 9))
        scrollbar = ttk.Scrollbar(log_tab, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.nb.add(log_tab, text='日志')
        ocr_tab = ttk.Frame(self.nb)
        # undo=True 启用撤销栈，配合 Ctrl+Z/Ctrl+Y 编辑 OCR 结果（默认 Text 无此能力）
        self.ocr_text = tk.Text(ocr_tab, font=('Consolas', 9), wrap='none', undo=True,
                                maxundo=-1)
        self.ocr_text.bind('<Control-z>', self._undo_ocr_text)
        self.ocr_text.bind('<Control-y>', self._redo_ocr_text)
        self.ocr_text.bind('<Control-Z>', self._redo_ocr_text)
        self.ocr_text.bind('<Control-s>', self._save_ocr_shortcut)
        ocr_scrollbar = ttk.Scrollbar(ocr_tab, command=self.ocr_text.yview)
        self.ocr_text.configure(yscrollcommand=ocr_scrollbar.set)
        ocr_scrollbar.pack(side='right', fill='y')
        self.ocr_text.pack(side='left', fill='both', expand=True)
        self.nb.add(ocr_tab, text='OCR结果（可编辑）')

    def hint(self, parent: ttk.Frame, text: str, wrap: int = 0) -> ttk.Label:
        """灰色小字提示"""
        hint_label = ttk.Label(parent, text=text, foreground='gray')
        if wrap:
            hint_label.configure(wraplength=wrap, justify='left')
        return hint_label

    def browse(self, target_var: tk.StringVar, title: str,
               file_type_filters: List[Tuple[str, str]]) -> None:
        selected_path = filedialog.askopenfilename(title=title, filetypes=file_type_filters)
        if selected_path:
            target_var.set(selected_path)

    def _register_drop(self, target_entry: ttk.Entry) -> None:
        """注册拖放目标：支持把文件直接拖入输入框（需 tkinterdnd2）"""
        if not HAS_DND:
            return
        try:
            target_entry.drop_target_register(DND_FILES)
            target_entry.dnd_bind('<<Drop>>', self._on_file_drop)
        except tk.TclError:
            # 非 TkinterDnD 根窗口（如无拖拽的测试环境）下 tkdnd 命令不可用，忽略即可
            pass

    def _on_file_drop(self, event) -> str:
        """处理拖入的文件：按扩展名归类填入PDF/txt输入框（混合拖入则分别填入）"""
        dropped_paths = list(self.root.tk.splitlist(event.data))
        pdf_path = None
        txt_path = None
        for dropped_path in dropped_paths:
            file_extension = os.path.splitext(dropped_path)[1].lower()
            if file_extension in PDF_EXTENSIONS and pdf_path is None:
                pdf_path = dropped_path
            elif file_extension == TXT_EXTENSION and txt_path is None:
                txt_path = dropped_path
        if pdf_path:
            self.pdf_var.set(pdf_path)
        if txt_path:
            self.txt_var.set(txt_path)
        self.logln('已拖入: %s' % '  |  '.join(dropped_paths))
        return 'break'

    def on_mode(self) -> None:
        """按操作模式切换选项区显示（写入/提取/图片）"""
        if self.mode.get() == 'write':
            self.write_options.pack(side='left', padx=12)
            self.extract_options.pack_forget()
            self.image_options.pack_forget()
        elif self.mode.get() == 'extract':
            self.write_options.pack_forget()
            self.extract_options.pack(side='left', padx=12)
            self.image_options.pack_forget()
        else:
            self.write_options.pack_forget()
            self.extract_options.pack_forget()
            self.image_options.pack(side='left', padx=12)

    def logln(self, text: str) -> None:
        self.log.insert('end', text + '\n')
        self.log.see('end')

    def _undo_ocr_text(self, _event=None) -> str:
        """Ctrl+Z 撤销；撤销栈为空时忽略"""
        try:
            self.ocr_text.edit_undo()
        except tk.TclError:
            pass
        return 'break'

    def _redo_ocr_text(self, _event=None) -> str:
        """Ctrl+Y / Ctrl+Shift+Z 重做；无重做记录时忽略"""
        try:
            self.ocr_text.edit_redo()
        except tk.TclError:
            pass
        return 'break'

    def _save_ocr_shortcut(self, _event=None) -> str:
        """Ctrl+S 保存OCR结果；仅光标在OCR结果编辑区时触发，内容为空时忽略"""
        if self.ocr_text.get('1.0', 'end').strip():
            self._prompt_save_ocr_result()
        return 'break'

    # ---- 后台任务 ----

    def _task_start(self, task_type: str, target: Callable, arguments: Tuple) -> None:
        if self._task is not None:
            messagebox.showwarning('提示', '任务正在进行中')
            return
        self._task = task_type
        self._paused = False
        self._has_progress = False
        self.btn_pause.configure(text='暂停', state='normal')
        self.btn_stop.configure(state='normal')
        for button in (self.btn_run, self.btn_confirm, self.btn_save_ocr):
            button.configure(state='disabled')
        self.btn_browse_pdf.configure(state='disabled')
        self.btn_browse_txt.configure(state='disabled')
        if ocr.HAS_OCR:
            self.btn_ocr.configure(state='disabled')
            self.btn_ocr_text.configure(state='disabled')
        self._prog_reset()
        self.prog_label.configure(text='启动中…')
        self._tm.start(target, arguments)
        self._watchdog_schedule()
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def _task_end(self) -> Optional[str]:
        """结束后台任务并恢复界面状态；返回刚结束的任务类型（供结果分支区分）"""
        finished_type = self._task
        self._task = None
        self._paused = False
        self._tm.finish()
        if self._watchdog_id is not None:
            self.root.after_cancel(self._watchdog_id)
            self._watchdog_id = None
        self.prog.stop()
        self.prog.configure(mode='determinate', value=0)
        self.prog_label.configure(text='')
        for button in (self.btn_run, self.btn_confirm, self.btn_save_ocr):
            button.configure(state='normal')
        self.btn_browse_pdf.configure(state='normal')
        self.btn_browse_txt.configure(state='normal')
        if ocr.HAS_OCR:
            self.btn_ocr.configure(state='normal')
            self.btn_ocr_text.configure(state='normal')
        self.btn_pause.configure(state='disabled')
        self.btn_stop.configure(state='disabled')
        return finished_type

    def _prog_reset(self) -> None:
        self.prog.stop()
        self.prog.configure(mode='determinate', value=0, maximum=100)

    def _prog_show(self, done: int, total: int, message: str) -> None:
        if self._task is None:
            return
        self._has_progress = True
        if self._watchdog_id is not None:
            self.root.after_cancel(self._watchdog_id)
            self._watchdog_id = None
        self.prog.configure(maximum=max(total, 1), value=max(done, 0))
        self.prog_label.configure(text=message if message else '')
        self._watchdog_schedule()

    def _watchdog_schedule(self) -> None:
        if self._watchdog_id is not None:
            self.root.after_cancel(self._watchdog_id)
        self._watchdog_id = self.root.after(WATCHDOG_INTERVAL_MS, self._watchdog)

    def _watchdog(self) -> None:
        self._watchdog_id = None
        if self._task is None:
            return
        # 保持填充式进度条，仅更新提示文字（模型加载/打开文件等阶段无进度信息）
        if self._has_progress:
            self.prog_label.configure(text='处理中…（长时间无进度更新）')
        else:
            self.prog_label.configure(text='打开/加载中…')

    def _poll_queue(self) -> None:
        if self._task is None:
            return
        message = self._tm.poll_message()
        if message is None:
            self.root.after(POLL_INTERVAL_MS, self._poll_queue)
            return
        message_kind = message[0]
        if message_kind == 'progress':
            _kind, done, total, progress_message = message
            self._prog_show(done, total, progress_message)
        elif message_kind == 'done':
            finished_type = self._task_end()
            _kind, output_dir, image_count = message
            self.logln('提取完成: %d 张%s' % (
                image_count, '页面' if finished_type == 'images' else '图片'))
            self.logln('已保存到: %s' % output_dir)
            messagebox.showinfo('完成', '已提取 %d 张图片\n%s' % (image_count, output_dir))
        elif message_kind == 'done_write':
            self._task_end()
            _kind, output_path, bookmark_count = message
            self.logln('写入完成: %s（%d 条书签）' % (output_path, bookmark_count))
            messagebox.showinfo('完成', '已写入 %d 条书签\n%s' % (bookmark_count, output_path))
        elif message_kind == 'extract_done':
            self._task_end()
            _kind, is_ebook, extracted_entries, output_path = message
            entry_label = ('目录（[p序号]为阅读顺序号，非页码）' if is_ebook else '书签')
            self.logln('提取完成: %d 条%s' % (len(extracted_entries), entry_label))
            for entry_item in extracted_entries[:LOG_PREVIEW_COUNT]:
                if is_ebook:
                    indent, title, sequence_number = entry_item
                    self.logln('%s%s [p%d]' % ('  ' * indent, title, sequence_number))
                else:
                    level, title, page_number = entry_item
                    self.logln('%s%s [p%d]' % ('  ' * (level - 1), title, page_number))
            if len(extracted_entries) > LOG_PREVIEW_COUNT:
                self.logln('... 共 %d 条' % len(extracted_entries))
            self.logln('已保存到: %s' % output_path)
            messagebox.showinfo('完成', '已提取 %d 条%s\n%s' % (
                len(extracted_entries), '目录' if is_ebook else '书签', output_path))
        elif message_kind == 'extract_none':
            self._task_end()
            self.logln('该PDF没有书签。')
            messagebox.showinfo('提示', '该PDF没有书签。')
        elif message_kind == 'ocr_done':
            _kind, ocr_result_text = message
            self.ocr_text.delete('1.0', 'end')
            self.ocr_text.insert('1.0', ocr_result_text)
            self.nb.select(1)
            self.logln('OCR完成。可在"OCR结果"页签修改后点[确认写入]。')
        elif message_kind == 'ocr_text_done':
            self._task_end()
            _kind, extracted_text = message
            self.ocr_text.delete('1.0', 'end')
            self.ocr_text.insert('1.0', extracted_text)
            self.nb.select(1)
            self.logln('文字识别完成。结果在"OCR结果"页签，可编辑后[保存OCR结果…]。')
            self._notify_ocr_finished()
        elif message_kind == 'ocr_offset':
            self._task_end()
            _kind, offset = message
            if offset is not None:
                self.logln('自动检测：PDF页偏移=%d。写入书签时请在"2.操作"填正文第一页的PDF页号和印刷页码。' % offset)
            else:
                self.logln('自动检测偏移失败。请在"2.操作"填正文第一页的PDF页号和印刷页码。')
            self._notify_ocr_finished()
        elif message_kind == 'error':
            self._task_end()
            error_message = message[1]
            if '已取消' in error_message or 'TaskCancelled' in error_message:
                self.logln('任务已取消')
                messagebox.showinfo('提示', '任务已取消')
            else:
                self.logln('错误: %s' % error_message)
                messagebox.showerror('错误', error_message)
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def toggle_pause(self) -> None:
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

    def stop_task(self) -> None:
        if self._task is None:
            return
        self.logln('正在停止…')
        self._tm.cancel_event.set()

    # ---- 前台操作 ----

    def run(self) -> None:
        try:
            if self.mode.get() == 'write':
                self.do_write()
            elif self.mode.get() == 'extract':
                self.do_extract()
            else:
                self.do_images()
        except Exception as error:
            self.logln('错误: %s' % error)
            messagebox.showerror('错误', str(error))

    def do_images(self) -> None:
        pdf_path = self.pdf_var.get().strip()
        if not pdf_path:
            raise ValueError('请选择PDF文件')
        if not os.path.isfile(pdf_path):
            raise ValueError('PDF文件不存在: %s' % pdf_path)
        dpi_text = self.dpi_var.get().strip()
        render_dpi = int(dpi_text) if dpi_text else None
        if render_dpi is not None and not (DPI_MIN <= render_dpi <= DPI_MAX):
            raise ValueError('分辨率dpi应在%d-%d之间' % (DPI_MIN, DPI_MAX))
        image_format = self.fmt_var.get().strip().lower() or 'orig'
        try:
            quality_text = self.quality_var.get().strip() or str(DEFAULT_JPEG_QUALITY)
            jpeg_quality = int(quality_text)
        except ValueError:
            raise ValueError('JPEG质量必须是整数')
        if not (JPEG_QUALITY_MIN <= jpeg_quality <= JPEG_QUALITY_MAX):
            raise ValueError('JPEG质量应在%d-%d之间' % (JPEG_QUALITY_MIN, JPEG_QUALITY_MAX))
        # 弹框选择保存位置（父目录），文件夹名固定 "<书名>_图片"，可取消
        default_dir_name = os.path.splitext(os.path.basename(pdf_path))[0] \
            + core.IMAGE_OUTPUT_SUFFIX
        chosen_parent = filedialog.askdirectory(
            title='选择保存文件夹（将创建: %s）' % default_dir_name,
            initialdir=os.path.dirname(pdf_path) or None)
        if not chosen_parent:
            self.logln('已取消：未选择保存文件夹')
            return
        output_dir = os.path.join(chosen_parent, default_dir_name)
        self.logln('正在提取%s（%s）…' % ('页面' if render_dpi else 'PDF内嵌图片', image_format))
        self._task_start('images', core._mp_extract_images,
                         (pdf_path, render_dpi, image_format, jpeg_quality,
                          self._tm.cancel_event, self._tm.pause_event, output_dir))

    def do_write(self) -> None:
        pdf_path = self.pdf_var.get().strip()
        txt_path = self.txt_var.get().strip()
        if not pdf_path or not txt_path:
            raise ValueError('请选择PDF文件和目录txt')
        if not os.path.isfile(pdf_path):
            raise ValueError('PDF文件不存在: %s' % pdf_path)
        if not os.path.isfile(txt_path):
            raise ValueError('txt文件不存在: %s' % txt_path)
        offset = self._field_offset()
        if offset is None:
            offset_fields = self._ask_offset_fields()
            if offset_fields is None:
                self.logln('已取消：未填写偏移信息')
                return
            self.first_pdf_var.set(str(offset_fields[0]))
            self.first_print_var.set(str(offset_fields[1]))
            offset = self._field_offset()
            if offset is None:
                self.logln('提示: 偏移信息无效')
                return
        parsed_entries = core.levels_from_indent(core.parse_toc(txt_path))
        toc_entries = core.build_toc(parsed_entries, offset)
        self.logln('解析完成: %d 条书签（偏移=%d）' % (len(toc_entries), offset))
        for level, title, page_number in toc_entries[:LOG_PREVIEW_COUNT]:
            self.logln('%s%s [p%d]' % ('  ' * (level - 1), title, page_number))
        if len(toc_entries) > LOG_PREVIEW_COUNT:
            self.logln('... 共 %d 条' % len(toc_entries))
        self.logln('正在写入书签（后台）…')
        self._task_start('write', core._mp_write_toc,
                         (pdf_path, toc_entries, self.outmode_var.get()))

    def do_extract(self) -> None:
        source_path = self.pdf_var.get().strip()
        if not source_path:
            raise ValueError('请选择PDF或电子书文件')
        if not os.path.isfile(source_path):
            raise ValueError('文件不存在: %s' % source_path)
        self.logln('正在读取目录（后台）…')
        self._task_start('extract', core._mp_extract_toc,
                         (source_path, self.e_pdfpage.get()))

    def _get_ocr_args(self) -> Tuple[str, int, int]:
        pdf_path = self.pdf_var.get().strip()
        if not pdf_path:
            raise ValueError('请先在"选择文件"里选择PDF')
        if not os.path.isfile(pdf_path):
            raise ValueError('PDF文件不存在: %s' % pdf_path)
        range_match = RE_PAGE_RANGE.match(self.ocr_range_var.get().strip())
        if not range_match:
            raise ValueError('请填写目录页PDF页号范围，如 11-16')
        start_page, end_page = int(range_match.group(1)), int(range_match.group(2))
        return pdf_path, start_page, end_page

    def _field_offset(self) -> Optional[int]:
        """由"正文第一页的PDF页号+印刷页码"计算偏移量；缺失/非法返回None"""
        try:
            first_pdf_page = int(self.first_pdf_var.get().strip())
            first_printed_page = int(self.first_print_var.get().strip())
            if first_pdf_page < 1 or first_printed_page < 1:
                return None
            return (first_pdf_page - 1) - first_printed_page
        except ValueError:
            return None

    def _ask_offset_fields(self) -> Optional[Tuple[int, int]]:
        """弹框要求填写正文第一页的PDF页号和印刷页码；返回 (pdf_no, print_no) 或 None(取消)"""
        return dlg.ask_offset_fields(self.root, self.first_pdf_var, self.first_print_var)

    def _prepare_ocr_range(self) -> Optional[Tuple[str, int, int]]:
        """校验/补齐 OCR 页号范围；返回 (pdf_path, start_page, end_page) 或 None(取消)"""
        pdf_path = self.pdf_var.get().strip()
        range_text = self.ocr_range_var.get().strip()
        if (not pdf_path or not os.path.isfile(pdf_path)
                or not RE_PAGE_RANGE.match(range_text)):
            ocr_fields = dlg.ask_ocr_args(self.root, self.pdf_var, self.ocr_range_var)
            if ocr_fields is None:
                self.logln('已取消：未填写OCR识别信息')
                return None
            self.pdf_var.set(ocr_fields[0])
            self.ocr_range_var.set(ocr_fields[1])
        try:
            return self._get_ocr_args()
        except ValueError as error:
            raise ValueError(str(error))

    def do_ocr(self) -> None:
        try:
            ocr_args = self._prepare_ocr_range()
            if ocr_args is None:
                return
            pdf_path, start_page, end_page = ocr_args
            self.logln('加载OCR引擎并识别目录 PDF页 %d-%d …（首次运行下载模型，约需几分钟）'
                       % (start_page, end_page))
            self._task_start('ocr', ocr._mp_ocr_task,
                             (pdf_path, start_page, end_page,
                              self._tm.cancel_event, self._tm.pause_event))
        except Exception as error:
            self.logln('OCR错误: %s' % error)
            messagebox.showerror('OCR错误', str(error))

    def do_ocr_text(self) -> None:
        try:
            ocr_args = self._prepare_ocr_range()
            if ocr_args is None:
                return
            pdf_path, start_page, end_page = ocr_args
            self.logln('加载OCR引擎并识别文字 PDF页 %d-%d …（首次运行下载模型，约需几分钟）'
                       % (start_page, end_page))
            self._task_start('ocr_text', ocr._mp_ocr_text_task,
                             (pdf_path, start_page, end_page,
                              self._tm.cancel_event, self._tm.pause_event))
        except Exception as error:
            self.logln('OCR错误: %s' % error)
            messagebox.showerror('OCR错误', str(error))

    def _prepare_write_from_text(self, text_to_parse: str) -> Optional[str]:
        """把待解析文本写入临时文件（write/parse_toc 需要文件路径），返回临时路径"""
        temp_preview_path = os.path.join(os.environ.get('TEMP', '.'), OCR_PREVIEW_FILE_NAME)
        try:
            with open(temp_preview_path, 'w', encoding='utf-8-sig') as output_file:
                output_file.write(text_to_parse)
        except OSError as io_error:
            raise OSError('写入临时文件失败: %s' % io_error)
        return temp_preview_path

    def _require_offset(self) -> Optional[int]:
        """取偏移；未填则弹框要求填写，返回偏移或 None(取消/无效)"""
        offset = self._field_offset()
        if offset is not None:
            return offset
        offset_fields = self._ask_offset_fields()
        if offset_fields is None:
            self.logln('已取消：未填写偏移信息')
            return None
        self.first_pdf_var.set(str(offset_fields[0]))
        self.first_print_var.set(str(offset_fields[1]))
        offset = self._field_offset()
        if offset is None:
            self.logln('提示: 偏移信息无效')
        return offset

    def confirm_ocr_write(self) -> None:
        try:
            pdf_path = self.pdf_var.get().strip()
            ocr_result_text = self.ocr_text.get('1.0', 'end')
            if not ocr_result_text.strip():
                raise ValueError('OCR结果为空，请先执行识别')
            if not pdf_path:
                raise ValueError('请选择PDF文件')
            if not os.path.isfile(pdf_path):
                raise ValueError('PDF文件不存在: %s' % pdf_path)
            temp_preview_path = self._prepare_write_from_text(ocr_result_text)
            offset = self._require_offset()
            if offset is None:
                return
            parsed_entries = core.levels_from_indent(core.parse_toc(temp_preview_path))
            toc_entries = core.build_toc(parsed_entries, offset)
            self.logln('解析OCR结果: %d 条书签（偏移=%d）' % (len(toc_entries), offset))
            self.logln('正在写入书签（后台）…')
            self._task_start('write', core._mp_write_toc,
                             (pdf_path, toc_entries, self.outmode_var.get()))
        except Exception as error:
            self.logln('错误: %s' % error)
            messagebox.showerror('错误', str(error))

    def _prompt_save_ocr_result(self) -> None:
        """弹保存对话框保存OCR结果页签内容（默认 PDF同目录 + _目录.txt），可取消"""
        ocr_result_text = self.ocr_text.get('1.0', 'end')
        if not ocr_result_text.strip():
            return
        pdf_path = self.pdf_var.get().strip()
        file_base, _extension = os.path.splitext(pdf_path)
        if not file_base:
            file_base = 'OCR结果'
        save_path = filedialog.asksaveasfilename(
            title='保存OCR结果',
            defaultextension='.txt',
            filetypes=[('文本', '*.txt')],
            initialdir=os.path.dirname(pdf_path) or None,
            initialfile=os.path.basename(file_base) + '_目录.txt')
        if not save_path:
            return
        try:
            with open(save_path, 'w', encoding='utf-8-sig') as output_file:
                output_file.write(ocr_result_text)
        except OSError as io_error:
            self.logln('保存失败: %s' % io_error)
            messagebox.showerror('错误', '保存失败: %s' % io_error)
            return
        self.logln('已保存: %s' % save_path)

    def _notify_ocr_finished(self) -> None:
        """识别完成提示：弹"已完成"框询问是否立即保存，选是才弹保存框"""
        if messagebox.askyesno('已完成', '识别已完成，是否立即保存结果？'):
            self._prompt_save_ocr_result()

    def save_ocr_txt(self) -> None:
        ocr_result_text = self.ocr_text.get('1.0', 'end')
        if not ocr_result_text.strip():
            messagebox.showwarning('提示', 'OCR结果为空')
            return
        self._prompt_save_ocr_result()


def main() -> int:
    # 优先使用支持文件拖放的根窗口；未安装 tkinterdnd2 时降级普通窗口
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root)
    root.mainloop()
    return 0
