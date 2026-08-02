# -*- coding: utf-8 -*-
"""tkinter 图形界面"""
import os
import re
import tkinter as tk
from typing import Callable, List, Optional, Tuple
from tkinter import filedialog, messagebox, ttk

import fitz

from core import IMAGE_OUTPUT_SUFFIX, JPEG_QUALITY_MAX
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
RE_IMAGE_PAGE_RANGE = re.compile(r'^\s*(\d+)\s*(?:[-—–]\s*(\d+))?\s*$')
RE_OCR_PDF_PAGE = re.compile(r'\[p\s*\d+\]')  # txt 中 [pN] 写法：直接PDF页号免偏移
PDF_EXTENSIONS = {'.pdf', '.epub', '.mobi', '.azw3', '.prc', '.azw'}
TXT_EXTENSION = '.txt'
IMAGE_INLINE_OPTION = '内嵌图片'       # 分辨率下拉框：提取PDF内嵌图片（不渲染）
DEFAULT_IMAGE_RESOLUTION = '300'       # 分辨率默认值（渲染模式的默认分辨率）
JPEG_QUALITY_DEFAULT = JPEG_QUALITY_MAX  # 图片提取JPEG质量固定最高
LOG_PREVIEW_COUNT = 20       # 日志区预览的最大条数
WATCHDOG_INTERVAL_MS = 4000  # 长时间无进度时的提示更新间隔
POLL_INTERVAL_MS = 100       # 消息队列轮询间隔
OCR_PREVIEW_FILE_NAME = '_pdf_toc_ocr_preview.txt'  # 确认写入时的临时解析文件


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('PDF 书签工具 v%s' % VERSION)
        root.geometry('920x780')
        root.minsize(860, 600)

        self._tm = taskmgr.TaskManager()
        self._task = None
        self._watchdog_id = None
        self._paused = False
        self._has_progress = False
        self._last_ocr_type: Optional[str] = None  # 最近一次OCR类型: 'toc'目录 / 'text'文字

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
        self.e_pdfpage_check = ttk.Checkbutton(self.extract_options,
                                               text='行尾带[PDF页号]',
                                               variable=self.e_pdfpage)
        self.e_pdfpage_check.pack(side='left')
        self.hint(self.extract_options, 'EPUB/MOBI无页码,[p序号]=阅读顺序').pack(side='left', padx=4)
        self.pdf_var.trace_add('write', self._on_pdf_file_changed)
        self.extract_options.pack_forget()
        self.image_options = ttk.Frame(action_options_frame)
        self.image_options.pack(side='left', padx=12)
        ttk.Label(self.image_options, text='页号(空=全部):').pack(side='left')
        self.page_range_var = tk.StringVar()
        ttk.Entry(self.image_options, textvariable=self.page_range_var, width=8).pack(side='left', padx=4)
        ttk.Label(self.image_options, text='分辨率:').pack(side='left')
        self.resolution_var = tk.StringVar(value=DEFAULT_IMAGE_RESOLUTION)
        ttk.Combobox(self.image_options, textvariable=self.resolution_var, width=8,
                     state='readonly',
                     values=(IMAGE_INLINE_OPTION, '200', '300', '400', '600')).pack(side='left', padx=4)
        self.resolution_var.trace_add('write', self._on_resolution_changed)
        ttk.Label(self.image_options, text='格式:').pack(side='left')
        self.fmt_var = tk.StringVar(value='jpeg')
        ttk.Combobox(self.image_options, textvariable=self.fmt_var, width=7, state='readonly',
                     values=('orig', 'png', 'jpeg')).pack(side='left', padx=4)
        self.hint(self.image_options, '选"内嵌图片"原样提取（PDF/EPUB/MOBI/AZW3/PRC均支持）；'
                                     '选分辨率按每页渲染（EPUB/MOBI支持，AZW3不支持）。'
                                     '渲染默认JPEG，内嵌默认orig，JPEG质量固定最高，'
                                     '电子书内嵌的页号范围按图片出现顺序',
                  wrap=520).pack(side='left', padx=4)
        self.image_options.pack_forget()

        # ---- OCR 识别目录页 ----
        ocr_frame = ttk.LabelFrame(root, text='OCR 识别目录页')
        ocr_frame.pack(fill='x', **PADDING)
        row_frame = ttk.Frame(ocr_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Label(row_frame, text='页号范围:').pack(side='left')
        self.ocr_range_var = tk.StringVar()
        ttk.Entry(row_frame, textvariable=self.ocr_range_var, width=12).pack(side='left', padx=4)
        self.btn_ocr = ttk.Button(row_frame, text='识别目录', command=self.do_ocr)
        self.btn_ocr.pack(side='left', padx=8)
        self.btn_ocr_text = ttk.Button(row_frame, text='识别文字', command=self.do_ocr_text)
        self.btn_ocr_text.pack(side='left', padx=8)
        self.btn_ai_import = ttk.Button(row_frame, text='导入AI文本', command=self.do_ai_import)
        self.btn_ai_import.pack(side='left', padx=8)
        self.ocr_page_mark_var = tk.BooleanVar(value=False)
        # 每页加[第N页]标记 复选框：暂注释隐藏，后续改为弹框控制
        # ttk.Checkbutton(row_frame, text='每页加[第N页]标记',
        #                 variable=self.ocr_page_mark_var).pack(side='left', padx=8)
        self.hint(row_frame, '识别中可点底部[暂停]/[停止]；[识别目录]需先在"2.操作"填正文第一页PDF页号与印刷页码；'
                  '[导入AI文本]粘贴外部AI识别的目录并格式化为标准txt；结果可编辑后[确认写入]或[保存OCR结果…]',
                  wrap=430).pack(side='left', padx=8)

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
        self._update_ocr_button_state()

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
        self.btn_ocr.configure(state='normal')
        self.btn_ocr_text.configure(state='normal')
        self._update_ocr_button_state()
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

    def _open_folder(self, folder_path: str) -> None:
        """在资源管理器中打开文件夹（Windows）；失败时仅记录日志不打断"""
        try:
            os.startfile(folder_path)
        except OSError as error:
            self.logln('提示: 无法打开文件夹 %s（%s）' % (folder_path, error))

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
            self._open_folder(output_dir)
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
            self._open_folder(os.path.dirname(output_path))
        elif message_kind == 'extract_none':
            self._task_end()
            self.logln('该PDF没有书签。')
            messagebox.showinfo('提示', '该PDF没有书签。')
        elif message_kind == 'ocr_done':
            self._task_end()
            _kind, ocr_result_text = message
            self.ocr_text.delete('1.0', 'end')
            self.ocr_text.insert('1.0', ocr_result_text)
            self.nb.select(1)
            self.logln('OCR完成（已按偏移输出[pN]页号）。可在"OCR结果"页签修改后点[确认写入]。')
            self._notify_ocr_finished()
        elif message_kind == 'ocr_text_done':
            self._task_end()
            _kind, extracted_text = message
            self.ocr_text.delete('1.0', 'end')
            self.ocr_text.insert('1.0', extracted_text)
            self.nb.select(1)
            self.logln('文字识别完成。结果在"OCR结果"页签，可编辑后[保存OCR结果…]。')
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
        source_path = self.pdf_var.get().strip()
        if not source_path:
            raise ValueError('请选择文件')
        if not os.path.isfile(source_path):
            raise ValueError('文件不存在: %s' % source_path)
        resolution_text = self.resolution_var.get().strip()
        render_dpi = None if resolution_text == IMAGE_INLINE_OPTION else int(resolution_text)
        image_format = self.fmt_var.get().strip().lower() or 'orig'
        jpeg_quality = JPEG_QUALITY_DEFAULT
        # 页号范围：空=全部页；"12-30"=区间；"12"=单页
        page_range = self._parse_image_page_range()
        # 电子书内嵌提取：仅支持orig原样保存；页号范围按图片出现顺序（EPUB=阅读顺序，MOBI系列=记录顺序）
        if os.path.splitext(source_path)[1].lower() in core.EBOOK_INLINE_EXTENSIONS \
                and not render_dpi:
            if image_format != 'orig':
                self.logln('提示: 电子书内嵌提取仅支持原样保存，格式已改为orig')
                image_format = 'orig'
            if page_range is not None:
                self.logln('提示: 电子书无固定页码，页号范围按图片出现顺序（第%d-%d张）'
                           % page_range)
        # 自动确定并创建保存目录（文件同目录 "<书名>_图片"），弹确认框显示路径；
        # 不用 askdirectory：它只能选已有目录，无法预填程序新建的目录
        default_dir_name = os.path.splitext(os.path.basename(source_path))[0] \
            + core.IMAGE_OUTPUT_SUFFIX
        output_dir = os.path.join(os.path.dirname(source_path) or '.', default_dir_name)
        os.makedirs(output_dir, exist_ok=True)
        if not messagebox.askyesno(
                '确认保存位置',
                '图片将保存到：\n%s\n\n是否继续？' % output_dir):
            chosen_parent = filedialog.askdirectory(
                title='选择保存文件夹（将创建: %s）' % default_dir_name,
                initialdir=os.path.dirname(source_path) or None)
            if not chosen_parent:
                self.logln('已取消：未选择保存文件夹')
                return
            output_dir = os.path.join(chosen_parent, default_dir_name)
        render_desc = ('页面(分辨率%d)' % render_dpi) if render_dpi else '内嵌图片'
        self.logln('保存到: %s' % output_dir)
        if page_range is not None:
            self.logln('正在提取%s（%s）页 %d-%d…'
                       % (render_desc, image_format, page_range[0], page_range[1]))
        else:
            self.logln('正在提取%s（%s）…' % (render_desc, image_format))
        self._task_start('images', core._mp_extract_images,
                         (source_path, render_dpi, image_format, jpeg_quality,
                          self._tm.cancel_event, self._tm.pause_event, output_dir,
                          page_range[0] if page_range else None,
                          page_range[1] if page_range else None))

    def _on_resolution_changed(self, *_args) -> None:
        """分辨率与格式联动：选"内嵌图片"->格式orig；选数字->格式jpeg"""
        resolution_text = self.resolution_var.get().strip()
        if resolution_text == IMAGE_INLINE_OPTION:
            self.fmt_var.set('orig')
        elif resolution_text.isdigit():
            self.fmt_var.set('jpeg')

    def _on_pdf_file_changed(self, *_args) -> None:
        """所选文件变化时联动：复选框（PDF必带页号置灰；电子书=阅读顺序号）与OCR按钮（仅可渲染格式）"""
        if not hasattr(self, 'btn_ocr'):
            return
        file_ext = os.path.splitext(self.pdf_var.get().strip())[1].lower()
        if file_ext in core.EBOOK_INLINE_EXTENSIONS:
            self.e_pdfpage_check.configure(text='行尾带[阅读顺序号]')
        else:
            self.e_pdfpage_check.configure(text='行尾带[PDF页号]')
        self.e_pdfpage.set(True)
        self._update_ocr_button_state()

    def _update_ocr_button_state(self) -> None:
        """OCR只支持可渲染格式（PDF/EPUB/MOBI）：所选文件不可用时禁用识别按钮（任务期间不动）"""
        if self._task is not None:
            return
        is_supported = os.path.splitext(self.pdf_var.get().strip())[1].lower() \
            in core.OCR_EXTENSIONS
        state = 'normal' if is_supported else 'disabled'
        self.btn_ocr.configure(state=state)
        self.btn_ocr_text.configure(state=state)

    def _parse_image_page_range(self) -> Optional[Tuple[int, int]]:
        """解析图片页号范围输入：空=None(全部)，"12-30"=区间，"12"=单页"""
        range_text = self.page_range_var.get().strip()
        if not range_text:
            return None
        match = RE_IMAGE_PAGE_RANGE.match(range_text)
        if not match:
            raise ValueError('页号范围格式应为"12-30"或"12"（空=全部页）')
        start_page = int(match.group(1))
        end_page = int(match.group(2)) if match.group(2) else start_page
        if start_page < 1 or end_page < start_page:
            raise ValueError('页号范围无效：起始页>=1 且 起始页<=结束页')
        return start_page, end_page

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
        if self.outmode_var.get() == 'same':
            if not messagebox.askyesno(
                    '警告',
                    '将直接修改原文件（覆盖原书签），建议先备份原文件！\n\n'
                    '是否继续写入？'):
                self.logln('已取消：未确认直接写入')
                return
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
        if pdf_path and os.path.splitext(pdf_path)[1].lower() not in core.OCR_EXTENSIONS:
            raise ValueError('OCR识别仅支持PDF/EPUB/MOBI文件，请选择支持的文件')
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
            offset = self._field_offset()
            if offset is None:
                self.logln('已取消：识别目录必须在"2.操作"填写正文第一页的PDF页号和印刷页码')
                messagebox.showwarning('识别目录', '请先在"2.操作"填写正文第一页的PDF页号和印刷页码')
                return
            self._last_ocr_type = 'toc'
            self.logln('加载OCR引擎并识别目录 PDF页 %d-%d（偏移=%d）…（首次运行下载模型，约需几分钟）'
                       % (start_page, end_page, offset))
            self._task_start('ocr', ocr._mp_ocr_task,
                             (pdf_path, start_page, end_page,
                              self._tm.cancel_event, self._tm.pause_event, offset))
        except Exception as error:
            self.logln('OCR错误: %s' % error)
            messagebox.showerror('OCR错误', str(error))

    def do_ocr_text(self) -> None:
        try:
            ocr_args = self._prepare_ocr_range()
            if ocr_args is None:
                return
            pdf_path, start_page, end_page = ocr_args
            self._last_ocr_type = 'text'
            self.logln('加载OCR引擎并识别文字 PDF页 %d-%d …（首次运行下载模型，约需几分钟）'
                       % (start_page, end_page))
            self._task_start('ocr_text', ocr._mp_ocr_text_task,
                             (pdf_path, start_page, end_page,
                              self._tm.cancel_event, self._tm.pause_event,
                              self.ocr_page_mark_var.get()))
        except Exception as error:
            self.logln('OCR错误: %s' % error)
            messagebox.showerror('OCR错误', str(error))

    def do_ai_import(self) -> None:
        """导入外部AI识别的目录文本：弹窗粘贴 -> 格式化 -> 另存txt"""
        dialog = tk.Toplevel(self.root)
        dialog.title('导入外部AI识别的目录')
        dialog.transient(self.root)
        dialog.geometry('640x420')
        tk.Label(dialog, text='粘贴AI识别出的目录文本（每行：标题+页码，支持印刷页码/页码范围/[p页号]，'
                              '行首缩进表示层级），将格式化为标准格式并保存：',
                 justify='left', anchor='w', wraplength=600).pack(fill='x', padx=10, pady=(10, 4))
        text_widget = tk.Text(dialog, width=78, height=13)
        text_widget.pack(fill='both', expand=True, padx=10)
        status_var = tk.StringVar()
        tk.Label(dialog, textvariable=status_var, fg='#b00020', justify='left', anchor='w',
                 wraplength=600).pack(fill='x', padx=10, pady=2)

        def on_format() -> None:
            raw = text_widget.get('1.0', 'end').strip()
            if not raw:
                status_var.set('请先粘贴外部AI识别的目录文本')
                return
            try:
                formatted = core.format_toc_text(raw)
            except ValueError as error:
                status_var.set(str(error))
                return
            save_path = filedialog.asksaveasfilename(
                title='保存格式化目录', defaultextension='.txt', initialfile='格式化目录.txt',
                filetypes=[('文本文件', '*.txt')])
            if not save_path:
                return
            try:
                with open(save_path, 'w', encoding='utf-8') as file_handle:
                    file_handle.write(formatted)
            except OSError as io_error:
                messagebox.showerror('保存失败', str(io_error))
                return
            dialog.destroy()
            entry_count = formatted.count('\n') - 1
            self.logln('已格式化外部AI目录并保存: %s（%d 条）' % (save_path, entry_count))
            messagebox.showinfo('完成', '已保存 %d 条目录到:\n%s' % (entry_count, save_path))

        button_row = ttk.Frame(dialog)
        button_row.pack(fill='x', padx=10, pady=10)
        ttk.Button(button_row, text='格式化并保存…', command=on_format).pack(side='left')
        ttk.Button(button_row, text='取消', command=dialog.destroy).pack(side='left', padx=8)
        dialog.wait_window()

    def _prepare_write_from_text(self, text_to_parse: str) -> Optional[str]:
        """把待解析文本写入临时文件（write/parse_toc 需要文件路径），返回临时路径"""
        temp_preview_path = os.path.join(os.environ.get('TEMP', '.'), OCR_PREVIEW_FILE_NAME)
        try:
            with open(temp_preview_path, 'w', encoding='utf-8') as output_file:
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
            if RE_OCR_PDF_PAGE.search(ocr_result_text):
                offset = 0  # txt 已用 [pN] PDF页号，无需偏移
                self.logln('txt 已用 [p页号]，无需偏移')
            else:
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
        """弹保存对话框保存OCR结果页签内容（默认 PDF同目录，目录识别 _目录.txt / 文字识别 _文字.txt）"""
        ocr_result_text = self.ocr_text.get('1.0', 'end')
        if not ocr_result_text.strip():
            return
        pdf_path = self.pdf_var.get().strip()
        file_base, _extension = os.path.splitext(pdf_path)
        if not file_base:
            file_base = 'OCR结果'
        default_suffix = '_文字.txt' if self._last_ocr_type == 'text' else '_目录.txt'
        save_path = filedialog.asksaveasfilename(
            title='保存OCR结果',
            defaultextension='.txt',
            filetypes=[('文本', '*.txt')],
            initialdir=os.path.dirname(pdf_path) or None,
            initialfile=os.path.basename(file_base) + default_suffix)
        if not save_path:
            return
        try:
            with open(save_path, 'w', encoding='utf-8') as output_file:
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
