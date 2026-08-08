# -*- coding: utf-8 -*-
"""tkinter 图形界面（ttkbootstrap 现代主题，可选依赖）"""
import os
import re
import tkinter as tk
from typing import Callable, List, Optional, Tuple
from tkinter import filedialog, messagebox, ttk

import fitz

from core import IMAGE_OUTPUT_SUFFIX, JPEG_QUALITY_MAX
import core
import dlg
import doc2pdf
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

# 现代主题（可选依赖）：未安装时回退 ttk 默认主题，功能不受影响
try:
    import ttkbootstrap as tb
    HAS_TTKB = True
except ImportError:
    HAS_TTKB = False

VERSION = '1.2.1'

DEFAULT_THEME = 'cosmo'            # 默认亮色主题（打开即亮色）
DARK_THEME = 'darkly'              # 暗色主题
LIGHT_THEME = 'cosmo'              # 亮色主题
THEME_CHOICES = (DEFAULT_THEME, LIGHT_THEME)

# 左侧导航任务（仿 opencode 面板导航）
NAV_ITEMS = ('写入书签', '提取目录/图片', 'OCR 识别', 'PDF 解锁/加密', 'Word 转换')

# 状态栏配色（固定深色，不随主题变化，仿 opencode 顶栏）
SB_BG = '#1e1f24'
SB_FG = '#d5d5d8'
SB_FG_DIM = '#8a8f98'
SB_FG_ACCENT = '#7fb3d5'
NAV_BG = '#1e1f24'
NAV_FG = '#c9cbd1'
NAV_SEL_BG = '#2f3542'
NAV_WIDTH_DEFAULT = 170    # 导航栏默认宽度
NAV_WIDTH_MIN = 120        # 导航栏可拖拽宽度范围
NAV_WIDTH_MAX = 280

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

# 全局主题样式实例（ttkbootstrap 下启用）
_BOOT = None
CURRENT_THEME = DEFAULT_THEME  # 当前主题名（暗色 outline 按钮样式切换用）

# 暗色主题下 outline 系列按钮自绘样式（ttkbootstrap 的 outline 为图片渲染，暗色下文字不可见，
# 无法用 style map 修复，改为：亮色文字 + 细边框 + hover 实心反馈）
OUTLINE_DARK_STYLES = {
    'secondary-outline': ('#c7cdd4', '#8a909a', '#6c757d'),  # fg, 边框, hover背景
    'primary-outline': ('#8ab4f8', '#5d8fd6', '#0d6efd'),
    'info-outline': ('#6edff6', '#4fa8bd', '#0dcaf0'),
}
OUTLINE_STYLE_TO_BOOT = {'darkoutline.%s' % boot.split('-')[0]: boot
                         for boot in OUTLINE_DARK_STYLES}


def _outline_dark_style(bootstyle: str) -> str:
    """注册并返回暗色 outline 按钮的自定义样式名；非 outline/亮色主题返回空"""
    if _BOOT is None or 'dark' not in CURRENT_THEME or bootstyle not in OUTLINE_DARK_STYLES:
        return ''
    fg, border, hover_bg = OUTLINE_DARK_STYLES[bootstyle]
    style_name = 'darkoutline.%s' % bootstyle.split('-')[0]
    try:
        _BOOT.configure(style_name, background='#2a2b2d', foreground=fg,
                        bordercolor=border, lightcolor=border, darkcolor=border,
                        borderwidth=1, relief='solid', padding=(12, 5))
        # layout 须用 layout() 方法设置（configure 的 layout 参数不会被格式化，tcl 层会静默丢弃）
        _BOOT.layout(style_name, _BOOT.layout('TButton') or [])
        _BOOT.map(style_name,
                  background=[('disabled', '#222326'), ('active', hover_bg)],
                  foreground=[('disabled', '#8a909a'), ('active', '#ffffff')])
    except tk.TclError:
        pass
    return style_name

# ---- 字体（仿 opencode 质感：中文字体 + 整体放大） ----
FONT_NAME = 'LXGW WenKai'      # 首选字体（已安装时 Tk 内注册名是"霞鹜文楷"）
FONT_FALLBACK = 'Microsoft YaHei UI'
UI_FONT_SIZE = 11              # UI 基准字号（原 9）
TITLE_FONT_SIZE = 13           # 状态栏标题
NAV_FONT_SIZE = 12             # 任务导航
FONT = (FONT_NAME, UI_FONT_SIZE)


def _apply_global_font(root: tk.Misc) -> None:
    """检测并应用全局字体：LXGW WenKai（回退微软雅黑），放大字号；
    同时改 TkDefaultFont（tk.* 控件）与 ttkbootstrap 主题样式（ttk.* 控件）"""
    global FONT
    family = FONT_NAME
    try:
        import tkinter.font as tkfont
        families = tkfont.families(root)
        if family not in families and '霞鹜文楷' in families:
            family = '霞鹜文楷'
        if family not in families:
            family = FONT_FALLBACK
    except Exception:
        family = FONT_FALLBACK
    FONT = (family, UI_FONT_SIZE)
    try:
        tkfont.nametofont('TkDefaultFont').configure(family=family, size=UI_FONT_SIZE)
    except Exception:
        pass
    if _BOOT is not None:
        _BOOT.configure('.', font=FONT)


def _btn(parent, bootstyle=None, **kwargs) -> ttk.Button:
    """主题化按钮：ttkbootstrap 下支持 bootstyle，未安装时回退普通 ttk；
    暗色主题下 outline 系列改用自绘亮色样式（见 _outline_dark_style）"""
    if HAS_TTKB:
        dark_outline_style = _outline_dark_style(bootstyle or '')
        if dark_outline_style:
            # 用原生 ttk.Button：tb.Button 会把 style 名再过一遍 bootstyle 解析（自定义名会丢失）
            kwargs['style'] = dark_outline_style
            kwargs.pop('bootstyle', None)
            return ttk.Button(parent, **kwargs)
        return tb.Button(parent, bootstyle=bootstyle, **kwargs)
    kwargs.pop('bootstyle', None)
    return ttk.Button(parent, **kwargs)


def _labelframe(parent, text, bootstyle=None) -> ttk.LabelFrame:
    """主题化分组框：ttkbootstrap 下支持 bootstyle，未安装时回退普通 ttk"""
    if HAS_TTKB:
        return tb.LabelFrame(parent, text=text, bootstyle=bootstyle)
    return ttk.LabelFrame(parent, text=text)


def _Toplevel(parent, **kwargs) -> tk.Toplevel:
    """主题化的 Toplevel：未启用 ttkbootstrap 时回退原生"""
    return tb.Toplevel(parent, **kwargs) if HAS_TTKB else tk.Toplevel(parent, **kwargs)


def _Label(parent, **kwargs) -> tk.Widget:
    """主题化的 Label（foreground 兼容原生 fg）"""
    return tb.Label(parent, **kwargs) if HAS_TTKB else tk.Label(parent, **kwargs)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        _apply_global_font(root)  # 独立实例化（如测试）时也保证字体生效
        root.title('PDF 书签工具 v%s' % VERSION)
        root.geometry('980x860')
        root.minsize(880, 660)
        # grid 布局：行1为主内容区(weight=1)，底部固定行不会被挤压
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        self._tm = taskmgr.TaskManager()
        self._task = None
        self._watchdog_id = None
        self._paused = False
        self._has_progress = False
        self._last_ocr_type: Optional[str] = None  # 最近一次OCR类型: 'toc'目录 / 'text'文字
        self._current_page = 1
        self._nav_guard = False

        # 输入框格式限制：非法字符（汉字/英文/符号）输入时不显示
        self._vcmd_digits = (root.register(self._validate_digits), '%P')
        self._vcmd_range = (root.register(self._validate_range_input), '%P')

        self._build_statusbar()        # 顶部状态栏（仿 opencode 顶栏）
        self._build_nav_and_content()  # 左侧任务导航 + 右侧内容区
        self._build_bottom()           # 进度 / 操作按钮 / 快捷键提示
        self._bind_keys()

        self._build_theme_menu()
        self._apply_theme_palette(DEFAULT_THEME)
        self._apply_button_disabled_style()
        self._go_page(1)
        self._update_ocr_button_state()

    def _apply_button_disabled_style(self) -> None:
        """禁用态按钮提亮文字：暗色主题下默认 disabled 文字几乎不可见；
        须在全部按钮创建后执行（ttkbootstrap 按钮构造时会重建 bootstyle 样式覆盖 map）"""
        if _BOOT is None:
            return
        for boot in ('primary', 'secondary', 'secondary-outline', 'success',
                     'warning', 'danger', 'info-outline'):
            _BOOT.map('%s.TButton' % boot, foreground=[('disabled', '#8a909a')])

    # ---- 布局 ----

    def _build_statusbar(self) -> None:
        """顶部状态栏：程序名 | 当前任务 | 当前文件 | 主题切换（配色随主题联动）"""
        status_bar = tk.Frame(self.root, bg=SB_BG)
        status_bar.grid(row=0, column=0, sticky='ew')
        status_bar.grid_columnconfigure(3, weight=1)
        self._sb_bar = status_bar
        self._sb_title = tk.Label(status_bar, text='◆ PDF书签工具', bg=SB_BG, fg='#e8e8e8',
                                  font=(FONT[0], TITLE_FONT_SIZE, 'bold'))
        self._sb_title.grid(row=0, column=0, padx=(10, 4), pady=6)
        self._sb_ver = tk.Label(status_bar, text='v%s' % VERSION, bg=SB_BG, fg=SB_FG_DIM,
                                font=FONT)
        self._sb_ver.grid(row=0, column=1, pady=6)
        self._sb_task_var = tk.StringVar()
        self._sb_task_label = tk.Label(status_bar, textvariable=self._sb_task_var, bg=SB_BG,
                                       fg=SB_FG_ACCENT, font=FONT)
        self._sb_task_label.grid(row=0, column=2, padx=18, pady=6)
        self._sb_file_var = tk.StringVar(value='未选择文件')
        self._sb_file_label = tk.Label(status_bar, textvariable=self._sb_file_var, bg=SB_BG,
                                       fg=SB_FG, font=FONT)
        self._sb_file_label.grid(row=0, column=3, sticky='w', pady=6)
        self._sb_theme_btn = _btn(status_bar, 'dark', text='主题: 暗色', padding=(10, 3),
                                  command=self._toggle_theme)
        self._sb_theme_btn.grid(row=0, column=4, padx=10, pady=4)

    def _build_nav_and_content(self) -> None:
        main = ttk.Frame(self.root)
        main.grid(row=1, column=0, sticky='nsew', padx=8, pady=4)

        # ---- 左侧任务导航 ----
        nav_frame = ttk.Frame(main, width=NAV_WIDTH_DEFAULT)
        nav_frame.pack(side='left', fill='y')
        nav_frame.pack_propagate(False)
        self._nav_frame = nav_frame
        self._nav_title = tk.Label(nav_frame, text='任 务', bg=NAV_BG, fg=SB_FG_DIM,
                                   font=FONT)
        self._nav_title.pack(fill='x', pady=(8, 2))
        self.nav = tk.Listbox(nav_frame, bg=NAV_BG, fg=NAV_FG,
                              selectbackground=NAV_SEL_BG, selectforeground='#ffffff',
                              activestyle='none', bd=0, relief='flat',
                              highlightthickness=0, font=(FONT[0], NAV_FONT_SIZE))
        for index, item_name in enumerate(NAV_ITEMS, start=1):
            self.nav.insert('end', '%d  %s' % (index, item_name))
        self.nav.pack(fill='both', expand=True, padx=6, pady=(0, 8))
        self.nav.bind('<<ListboxSelect>>', self._on_nav_select)

        # 导航宽度拖动手柄（范围限制见 _sash_drag）
        self._sash = tk.Frame(main, width=5, bg=NAV_BG, cursor='sb_h_double_arrow')
        self._sash.pack(side='left', fill='y')
        self._sash.bind('<Button-1>', self._sash_press)
        self._sash.bind('<B1-Motion>', self._sash_drag)

        # ---- 右侧内容区 ----
        right = ttk.Frame(main)
        right.pack(side='left', fill='both', expand=True)

        # 文件面板（所有任务共享）
        file_frame = _labelframe(right, '文件', 'secondary')
        file_frame.pack(fill='x', **PADDING)
        self.pdf_var = tk.StringVar()
        self.txt_var = tk.StringVar()
        row_frame = ttk.Frame(file_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Label(row_frame, text='PDF/电子书/Word:').pack(side='left')
        self.pdf_entry = ttk.Entry(row_frame, textvariable=self.pdf_var)
        self.pdf_entry.pack(side='left', fill='x', expand=True, padx=4)
        self._register_drop(self.pdf_entry)
        self.btn_browse_pdf = _btn(
            row_frame, 'secondary-outline', text='浏览…',
            command=lambda: self.browse(self.pdf_var, '选择PDF/电子书/Word文件',
                                        [('PDF/电子书/Word', '*.pdf *.epub *.mobi *.azw3 *.prc *.docx *.doc *.rtf'),
                                         ('全部', '*.*')]))
        self.btn_browse_pdf.pack(side='left')

        # 参数区（随导航切换）
        self._config_host = ttk.Frame(right)
        self._config_host.pack(fill='x')
        self._pages = {
            1: self._page_write(),
            2: self._page_extract(),
            3: self._page_ocr(),
            4: self._page_pdf_tool(),
            5: self._page_convert(),
        }

        # 预览 / 日志（共享）
        log_frame = _labelframe(right, '预览 / 日志', 'secondary')
        log_frame.pack(fill='both', expand=True, **PADDING)
        self.nb = ttk.Notebook(log_frame)
        self.nb.pack(fill='both', expand=True, padx=6, pady=6)
        log_tab = ttk.Frame(self.nb)
        self.log = tk.Text(log_tab, font=FONT)
        scrollbar = ttk.Scrollbar(log_tab, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.nb.add(log_tab, text='日志')
        ocr_tab = ttk.Frame(self.nb)
        # undo=True 启用撤销栈，配合 Ctrl+Z/Ctrl+Y 编辑 OCR 结果（默认 Text 无此能力）
        self.ocr_text = tk.Text(ocr_tab, font=FONT, wrap='none', undo=True,
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

        # 文件变化联动（各页面构建完成后注册）
        self.pdf_var.trace_add('write', self._on_pdf_file_changed)
        self._on_pdf_file_changed()

    # ---- 任务页面 ----

    def _page_write(self) -> ttk.Frame:
        """任务1：写入书签（txt → PDF）"""
        page = ttk.Frame(self._config_host)
        # 目录txt仅写入书签任务需要，放在本任务页内
        txt_frame = _labelframe(page, '目录txt', 'primary')
        txt_frame.pack(fill='x', **PADDING)
        row_frame = ttk.Frame(txt_frame)
        row_frame.pack(fill='x', **PADDING)
        self.txt_entry = ttk.Entry(row_frame, textvariable=self.txt_var)
        self.txt_entry.pack(side='left', fill='x', expand=True, padx=4)
        self._register_drop(self.txt_entry)
        self.btn_browse_txt = _btn(
            row_frame, 'secondary-outline', text='浏览…',
            command=lambda: self.browse(self.txt_var, '选择目录txt', [('文本', '*.txt'), ('全部', '*.*')]))
        self.btn_browse_txt.pack(side='left')
        action_frame = _labelframe(page, '写入书签（txt → PDF）', 'primary')
        action_frame.pack(fill='x', **PADDING)
        self.mode = tk.StringVar(value='write')
        self.outmode_var = tk.StringVar(value='copy')
        row_frame = ttk.Frame(action_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Radiobutton(row_frame, text='生成_带目录.pdf副本', variable=self.outmode_var,
                        value='copy').pack(side='left', padx=8)
        ttk.Radiobutton(row_frame, text='直接写原文件', variable=self.outmode_var,
                        value='same').pack(side='left', padx=8)
        self.write_options = action_frame
        self.hint(page, 'txt 每行 "页码 标题"，支持 [pN] 直接指定PDF页号免偏移；'
                        '缩进（Tab/空格）自动转为多级书签', wrap=620).pack(anchor='w', **PADDING)
        return page

    def _page_extract(self) -> ttk.Frame:
        """任务2：提取书签 / 提取图片"""
        page = ttk.Frame(self._config_host)
        self.mode = tk.StringVar(value='extract')
        mode_frame = _labelframe(page, '提取内容', 'primary')
        mode_frame.pack(fill='x', **PADDING)
        row_frame = ttk.Frame(mode_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Radiobutton(row_frame, text='提取书签/电子书目录', variable=self.mode, value='extract',
                        command=self.on_mode).pack(side='left', padx=8)
        ttk.Radiobutton(row_frame, text='提取图片', variable=self.mode, value='images',
                        command=self.on_mode).pack(side='left', padx=8)
        options_row = ttk.Frame(mode_frame)
        options_row.pack(fill='x', **PADDING)
        self.extract_options = ttk.Frame(options_row)
        self.extract_options.pack(side='left', padx=12)
        self.e_pdfpage = tk.BooleanVar(value=True)
        self.e_pdfpage_check = ttk.Checkbutton(self.extract_options,
                                               text='行尾带[PDF页号]',
                                               variable=self.e_pdfpage)
        self.e_pdfpage_check.pack(side='left')
        self.hint(self.extract_options, 'EPUB/MOBI无页码,[p序号]=阅读顺序').pack(side='left', padx=4)
        self.image_options = ttk.Frame(options_row)
        self.image_options.pack(side='left', padx=12)
        ttk.Label(self.image_options, text='页号(空=全部):').pack(side='left')
        self.page_range_var = tk.StringVar()
        ttk.Entry(self.image_options, textvariable=self.page_range_var, width=8,
                  validate='key', validatecommand=self._vcmd_range).pack(side='left', padx=4)
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
        self.image_options.pack_forget()
        self.hint(page, '目录提取：PDF书签 / EPUB·MOBI电子书目录；'
                        '图片提取：内嵌图片或按分辨率渲染（AZW3不支持渲染）', wrap=620).pack(anchor='w', **PADDING)
        return page

    def _page_ocr(self) -> ttk.Frame:
        """任务3：OCR 识别目录页"""
        page = ttk.Frame(self._config_host)
        ocr_frame = _labelframe(page, 'OCR 识别', 'primary')
        ocr_frame.pack(fill='x', **PADDING)
        row_frame = ttk.Frame(ocr_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Label(row_frame, text='页号范围:').pack(side='left')
        self.ocr_range_var = tk.StringVar()
        ttk.Entry(row_frame, textvariable=self.ocr_range_var, width=12,
                  validate='key', validatecommand=self._vcmd_range).pack(side='left', padx=4)
        self.btn_ocr = _btn(row_frame, 'primary', text='识别目录', command=self.do_ocr)
        self.btn_ocr.pack(side='left', padx=8)
        self.btn_ocr_text = _btn(row_frame, 'primary-outline', text='识别文字',
                                 command=self.do_ocr_text)
        self.btn_ocr_text.pack(side='left', padx=8)
        self.btn_ai_import = _btn(row_frame, 'info-outline', text='导入AI文本',
                                  command=self.do_ai_import)
        self.btn_ai_import.pack(side='left', padx=8)
        self.ocr_page_mark_var = tk.BooleanVar(value=False)
        offset_row = ttk.Frame(ocr_frame)
        offset_row.pack(fill='x', **PADDING)
        ttk.Label(offset_row, text='正文第一页 PDF页号:').pack(side='left')
        self.first_pdf_var = tk.StringVar()
        ttk.Entry(offset_row, textvariable=self.first_pdf_var, width=8,
                  validate='key', validatecommand=self._vcmd_digits).pack(side='left', padx=4)
        ttk.Label(offset_row, text='印刷页码:').pack(side='left')
        self.first_print_var = tk.StringVar()
        ttk.Entry(offset_row, textvariable=self.first_print_var, width=8,
                  validate='key', validatecommand=self._vcmd_digits).pack(side='left', padx=4)
        self.hint(offset_row, '目录后第一个正文页，识别目录/写书签共用，必填，自动算偏移',
                  wrap=430).pack(side='left', padx=8)
        self.ocr_offset_error_label = tk.Label(offset_row, text='',
                                               fg='#ff6b6b' if 'dark' in CURRENT_THEME else '#b00020')
        self.ocr_offset_error_label.pack(side='left', padx=4)
        self.hint(page, '识别中可点底部[暂停]/[停止]；[识别目录]需先填"正文第一页"两框；'
                        '结果自动带[pN]页号，可在"OCR结果"页签编辑（Ctrl+Z/Ctrl+S）',
                  wrap=620).pack(anchor='w', **PADDING)
        result_row = ttk.Frame(page)
        result_row.pack(fill='x', **PADDING)
        self.btn_confirm = _btn(result_row, 'success', text='写入OCR结果', width=14,
                                command=self.confirm_ocr_write)
        self.btn_confirm.pack(side='left', padx=4)
        self.btn_save_ocr = _btn(result_row, 'secondary-outline', text='保存OCR结果…',
                                 width=14, command=self.save_ocr_txt)
        self.btn_save_ocr.pack(side='left', padx=4)
        return page

    def _page_pdf_tool(self) -> ttk.Frame:
        """任务4：PDF 解锁 / 加密"""
        page = ttk.Frame(self._config_host)
        unlock_frame = _labelframe(page, 'PDF 解锁 / 加密', 'primary')
        unlock_frame.pack(fill='x', **PADDING)
        row_frame = ttk.Frame(unlock_frame)
        row_frame.pack(fill='x', **PADDING)
        ttk.Label(row_frame, text='PDF密码:').pack(side='left')
        self.password_var = tk.StringVar()
        ttk.Entry(row_frame, textvariable=self.password_var, width=20,
                  show='*').pack(side='left', padx=4)
        self.btn_unlock = _btn(row_frame, 'primary', text='解锁并另存…',
                               command=self.do_unlock)
        self.btn_unlock.pack(side='left', padx=8)
        self.btn_encrypt = _btn(row_frame, 'primary', text='加密并另存…',
                                command=self.do_encrypt)
        self.btn_encrypt.pack(side='left', padx=8)
        self.hint(page, '使用"文件"面板中选择的PDF；解锁生成"<名>_已解锁.pdf"；'
                        '加密生成"<名>_已加密_密码<密码>.pdf"（AES-256），原文件不变',
                  wrap=620).pack(anchor='w', **PADDING)
        return page

    def _page_convert(self) -> ttk.Frame:
        """任务5：Word 转 PDF / HTML"""
        page = ttk.Frame(self._config_host)
        convert_frame = _labelframe(page, 'Word 转换', 'primary')
        convert_frame.pack(fill='x', **PADDING)
        row_frame = ttk.Frame(convert_frame)
        row_frame.pack(fill='x', **PADDING)
        self.btn_convert = _btn(row_frame, 'primary', text='转为PDF…', command=self.do_doc2pdf)
        self.btn_convert.pack(side='left', padx=8)
        self.btn_convert_html = _btn(row_frame, 'primary-outline', text='转为HTML…',
                                     command=self.do_doc2html)
        self.btn_convert_html.pack(side='left', padx=8)
        self.hint(page, '把 .docx 转为PDF/HTML（纯Python免安装引擎）；'
                        '.doc/.rtf 老格式转PDF需安装 Office/WPS 或 LibreOffice；原文件不变',
                  wrap=620).pack(anchor='w', **PADDING)
        return page

    # ---- 底部操作区 ----

    def _build_bottom(self) -> None:
        # 进度条与按钮行放在日志区下方，grid 固定行不会因主区拉伸被挤压
        progress_frame = ttk.Frame(self.root)
        progress_frame.grid(row=2, column=0, sticky='ew', padx=8, pady=(4, 2))
        self.prog_label = ttk.Label(progress_frame, text='', anchor='w')
        self.prog_label.pack(side='left')
        self.prog = ttk.Progressbar(progress_frame, mode='determinate', maximum=100)
        self.prog.pack(side='left', fill='x', expand=True)

        button_frame = ttk.Frame(self.root)
        button_frame.grid(row=3, column=0, sticky='ew', padx=8, pady=4)
        _btn(button_frame, 'secondary-outline', text='清空日志', width=8,
             command=lambda: self.log.delete('1.0', 'end')).pack(side='left', padx=4)
        self.btn_run = _btn(button_frame, 'primary', text='执行', width=8, command=self.run)
        self.btn_run.pack(side='right', padx=4)
        self.btn_pause = _btn(button_frame, 'warning', text='暂停', width=6,
                              command=self.toggle_pause, state='disabled')
        self.btn_pause.pack(side='right', padx=4)
        self.btn_stop = _btn(button_frame, 'danger', text='停止', width=6,
                             command=self.stop_task, state='disabled')
        self.btn_stop.pack(side='right', padx=4)

        # 底部快捷键提示条（仿 opencode 底部状态栏）
        self.hint(self.root, '[1-5] 切换任务    [Enter] 执行    [Ctrl+T] 切换主题    '
                             '[Tab] 切换焦点    文件可直接拖入输入框',
                  wrap=0).grid(row=4, column=0, sticky='ew', padx=10, pady=(0, 4))

    # ---- 导航切换与键盘 ----

    def _bind_keys(self) -> None:
        for index in range(1, len(NAV_ITEMS) + 1):
            self.root.bind('<Key-%d>' % index, lambda event, n=index: self._nav_key(n))
        self.root.bind('<Control-t>', self._toggle_theme_key)
        self.root.bind('<Control-T>', self._toggle_theme_key)

    def _nav_key(self, page_number: int) -> Optional[str]:
        """数字键切换任务：光标在输入框/下拉框内时放行（不拦截输入）"""
        focus = self.root.focus_get()
        if isinstance(focus, (ttk.Entry, ttk.Combobox)):
            return None
        self._go_page(page_number)
        return 'break'

    def _toggle_theme_key(self, _event=None) -> str:
        self._toggle_theme()
        return 'break'

    def _toggle_theme(self) -> None:
        """状态栏按钮 / Ctrl+T：亮暗主题一键切换"""
        if not HAS_TTKB:
            self.logln('未安装 ttkbootstrap，主题切换不可用（pip install ttkbootstrap）')
            return
        current = self._theme_var.get()
        new_theme = LIGHT_THEME if current == DARK_THEME else DARK_THEME
        self._set_theme(new_theme)

    def _on_nav_select(self, _event=None) -> None:
        if self._nav_guard:
            return
        selection = self.nav.curselection()
        if selection:
            self._go_page(selection[0] + 1)

    # ---- 导航宽度拖拽 ----

    def _sash_press(self, _event) -> str:
        self._sash_start_x = _event.x_root
        self._sash_start_width = self._nav_frame.winfo_width()
        return 'break'

    def _sash_drag(self, event) -> str:
        if not hasattr(self, '_sash_start_x'):
            return 'break'
        delta = event.x_root - self._sash_start_x
        new_width = max(NAV_WIDTH_MIN, min(NAV_WIDTH_MAX,
                                           self._sash_start_width + delta))
        self._nav_frame.configure(width=new_width)
        return 'break'

    def _go_page(self, page_number: int) -> None:
        """切换任务页：显示对应参数面板，联动底部执行按钮与状态栏"""
        if self._nav_guard or page_number not in self._pages:
            return
        self._nav_guard = True
        try:
            self._current_page = page_number
            for page_index, page_frame in self._pages.items():
                if page_index == page_number:
                    page_frame.pack(fill='x', **PADDING)
                else:
                    page_frame.pack_forget()
            self.nav.selection_clear(0, 'end')
            self.nav.selection_set(page_number - 1)
            self.nav.see(page_number - 1)
            self._sb_task_var.set('任务: %s' % NAV_ITEMS[page_number - 1])
            self._update_run_button()
        finally:
            self._nav_guard = False

    def _update_run_button(self) -> None:
        """按当前任务设置底部主操作按钮（执行/识别目录/禁用）"""
        if self._current_page in (1, 2):
            self.btn_run.configure(text='执行', command=self.run, state='normal')
        elif self._current_page == 3:
            self.btn_run.configure(text='识别目录', command=self.do_ocr, state='normal')
        else:
            self.btn_run.configure(text='执行', state='disabled')

    def _build_theme_menu(self) -> None:
        """顶部菜单：主题切换（ttkbootstrap 可用时）"""
        if not HAS_TTKB:
            return
        self._theme_var = tk.StringVar(value=DEFAULT_THEME)
        menu_bar = tk.Menu(self.root)
        theme_menu = tk.Menu(menu_bar, tearoff=0)
        for theme_name in THEME_CHOICES:
            theme_menu.add_radiobutton(
                label=('亮色' if theme_name == LIGHT_THEME else '暗色'),
                value=theme_name, variable=self._theme_var,
                command=lambda name=theme_name: self._set_theme(name))
        menu_bar.add_cascade(label='主题', menu=theme_menu)
        self.root.config(menu=menu_bar)

    def _set_theme(self, theme_name: str) -> None:
        """切换亮/暗主题；同时联动日志/OCR文本等原生控件配色与状态栏按钮"""
        global CURRENT_THEME
        if _BOOT is None:
            return
        _BOOT.theme_use(theme_name)
        CURRENT_THEME = theme_name
        self._theme_var.set(theme_name)
        self._apply_theme_palette(theme_name)
        self._apply_button_disabled_style()  # theme_use 会重建主题样式，需重应用
        self._refresh_outline_buttons()      # outline 按钮在暗色下需换自绘亮色样式

    def _refresh_outline_buttons(self) -> None:
        """主题切换后重设已创建的 outline 按钮样式（暗色→亮色样式，亮色→恢复原样）"""
        if _BOOT is None:
            return
        self._walk_refresh_outline(self.root)

    def _walk_refresh_outline(self, widget: tk.Widget) -> None:
        for child in widget.winfo_children():
            if child.winfo_class() == 'TButton':
                current_style = str(child.cget('style'))
                boot = OUTLINE_STYLE_TO_BOOT.get(current_style)
                if boot is None:
                    # 亮色主题下形态为 '<变体>-outline.TButton'（darkly）或 '<变体>.Outline.TButton'（cosmo）
                    lower_style = current_style.lower()
                    for outline_boot in OUTLINE_DARK_STYLES:
                        variant = outline_boot.split('-')[0]
                        if lower_style in ('%s-outline.tbutton' % variant,
                                           '%s.outline.tbutton' % variant):
                            boot = outline_boot
                            break
                if boot:
                    new_style = _outline_dark_style(boot)
                    child.configure(style=new_style if new_style else '%s.TButton' % boot)
            self._walk_refresh_outline(child)

    def _apply_theme_palette(self, theme_name: str) -> None:
        """按主题设置原生控件配色：日志/OCR文本、状态栏与导航（外边框区）随主题联动"""
        is_dark = 'dark' in theme_name
        text_bg = '#2b2b2b' if is_dark else '#ffffff'
        text_fg = '#e6e6e6' if is_dark else '#1a1a1a'
        for text_widget in (self.log, self.ocr_text):
            text_widget.configure(bg=text_bg, fg=text_fg)
        if is_dark:
            bar_bg, title_fg, dim_fg, accent_fg, fg = \
                '#1e1f24', '#e8e8e8', '#8a8f98', '#7fb3d5', '#d5d5d8'
            nav_bg, nav_fg, nav_sel_bg, nav_sel_fg = '#1e1f24', '#c9cbd1', '#2f3542', '#ffffff'
            theme_boot = 'dark'
        else:
            bar_bg, title_fg, dim_fg, accent_fg, fg = \
                '#e9ecef', '#212529', '#6c757d', '#0d6efd', '#343a40'
            nav_bg, nav_fg, nav_sel_bg, nav_sel_fg = '#f1f3f5', '#343a40', '#d0d7de', '#111111'
            theme_boot = 'light'
        self._sb_bar.configure(bg=bar_bg)
        self._sb_title.configure(bg=bar_bg, fg=title_fg)
        self._sb_ver.configure(bg=bar_bg, fg=dim_fg)
        self._sb_task_label.configure(bg=bar_bg, fg=accent_fg)
        self._sb_file_label.configure(bg=bar_bg, fg=fg)
        self._nav_title.configure(bg=nav_bg, fg=dim_fg)
        self.nav.configure(bg=nav_bg, fg=nav_fg, selectbackground=nav_sel_bg,
                           selectforeground=nav_sel_fg)
        self._sash.configure(bg=nav_bg)
        if HAS_TTKB:
            self._sb_theme_btn.configure(
                bootstyle=theme_boot,
                text='主题: 暗色' if is_dark else '主题: 亮色')
        self.ocr_offset_error_label.configure(
            fg='#ff6b6b' if is_dark else '#b00020')
        hint_fg = '#9aa0a6' if is_dark else '#6c757d'
        for hint_label in getattr(self, '_hint_labels', ()):
            hint_label.configure(foreground=hint_fg)

    def hint(self, parent: ttk.Frame, text: str, wrap: int = 0) -> ttk.Label:
        """灰色小字提示（前景色随主题：暗色亮灰/亮色深灰）"""
        if not hasattr(self, '_hint_labels'):
            self._hint_labels = []
        hint_label = ttk.Label(
            parent, text=text,
            foreground='#9aa0a6' if 'dark' in CURRENT_THEME else '#6c757d')
        self._hint_labels.append(hint_label)
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
            elif file_extension in doc2pdf.DOC_EXTENSIONS and pdf_path is None:
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
        for button in (self.btn_confirm, self.btn_save_ocr):
            button.configure(state='disabled')
        self.btn_browse_pdf.configure(state='disabled')
        self.btn_browse_txt.configure(state='disabled')
        self.btn_ocr.configure(state='disabled')
        self.btn_ocr_text.configure(state='disabled')
        self.btn_unlock.configure(state='disabled')
        self.btn_encrypt.configure(state='disabled')
        self.btn_convert.configure(state='disabled')
        self.btn_convert_html.configure(state='disabled')
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
        self._update_run_button()
        for button in (self.btn_confirm, self.btn_save_ocr):
            button.configure(state='normal')
        self.btn_browse_pdf.configure(state='normal')
        self.btn_browse_txt.configure(state='normal')
        self.btn_ocr.configure(state='normal')
        self.btn_ocr_text.configure(state='normal')
        self.btn_unlock.configure(state='normal')
        self.btn_encrypt.configure(state='normal')
        self.btn_convert.configure(state='normal')
        self.btn_convert_html.configure(state='normal')
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
            self._open_folder(os.path.dirname(output_path))
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
        elif message_kind == 'unlock_done':
            self._task_end()
            _kind, output_path = message
            self.logln('解锁完成（已去除密码保护）: %s' % output_path)
            messagebox.showinfo('完成', '已解锁并保存:\n%s' % output_path)
            self._open_folder(os.path.dirname(output_path))
        elif message_kind == 'encrypt_done':
            self._task_end()
            _kind, output_path = message
            self.logln('加密完成（打开需密码）: %s' % output_path)
            messagebox.showinfo('完成', '已加密并保存:\n%s' % output_path)
            self._open_folder(os.path.dirname(output_path))
        elif message_kind == 'convert_done':
            self._task_end()
            _kind, output_path = message
            self.logln('转换完成（Word → %s）: %s' % (
                os.path.splitext(output_path)[1].lstrip('.').upper(), output_path))
            messagebox.showinfo('完成', '已转换并保存:\n%s' % output_path)
            self._open_folder(os.path.dirname(output_path))
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
        """所选文件变化时联动：状态栏文件显示、复选框（PDF必带页号置灰；电子书=阅读顺序号）与OCR按钮（仅可渲染格式）"""
        if not hasattr(self, 'btn_ocr'):
            return
        file_path = self.pdf_var.get().strip()
        self._sb_file_var.set(file_path or '未选择文件')
        file_ext = os.path.splitext(file_path)[1].lower()
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
        offset = 0
        try:
            with open(txt_path, encoding='utf-8-sig') as file_handle:
                txt_content = file_handle.read()
        except OSError as io_error:
            raise ValueError('读取txt失败: %s' % io_error)
        if not RE_OCR_PDF_PAGE.search(txt_content):
            offset = self._field_offset()
            if offset is None:
                self.logln('写书签必填：正文第一页的PDF页号和印刷页码（txt为印刷页码格式）')
                self.ocr_offset_error_label.configure(
                    text='写书签必填：PDF页号和印刷页码')
                self.root.after(5000, lambda: self.ocr_offset_error_label.configure(text=''))
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

    def do_unlock(self) -> None:
        try:
            pdf_path = self.pdf_var.get().strip()
            if not pdf_path:
                raise ValueError('请先选择PDF文件')
            if not os.path.isfile(pdf_path):
                raise ValueError('PDF文件不存在: %s' % pdf_path)
            password = self.password_var.get()
            if not password:
                raise ValueError('请输入PDF密码')
            save_path = filedialog.asksaveasfilename(
                title='另存为（解锁后的PDF）',
                defaultextension='.pdf',
                initialdir=os.path.dirname(pdf_path) or None,
                initialfile=os.path.splitext(os.path.basename(pdf_path))[0] + '_已解锁.pdf',
                filetypes=[('PDF', '*.pdf')])
            if not save_path:
                self.logln('已取消：未选择保存路径')
                return
            self.logln('正在解锁PDF（后台）…')
            self._task_start('unlock', core._mp_unlock_pdf, (pdf_path, password, save_path))
        except Exception as error:
            self.logln('错误: %s' % error)
            messagebox.showerror('错误', str(error))

    def do_encrypt(self) -> None:
        try:
            pdf_path = self.pdf_var.get().strip()
            if not pdf_path:
                raise ValueError('请先选择PDF文件')
            if not os.path.isfile(pdf_path):
                raise ValueError('PDF文件不存在: %s' % pdf_path)
            password = self.password_var.get()
            if not password:
                raise ValueError('请输入PDF密码')
            save_path = filedialog.asksaveasfilename(
                title='另存为（加密后的PDF）',
                defaultextension='.pdf',
                initialdir=os.path.dirname(pdf_path) or None,
                initialfile=os.path.splitext(os.path.basename(pdf_path))[0] + '_已加密_密码' + password + '.pdf',
                filetypes=[('PDF', '*.pdf')])
            if not save_path:
                self.logln('已取消：未选择保存路径')
                return
            self.logln('正在加密PDF（后台）…')
            self._task_start('encrypt', core._mp_encrypt_pdf, (pdf_path, password, save_path))
        except Exception as error:
            self.logln('错误: %s' % error)
            messagebox.showerror('错误', str(error))

    def do_doc2pdf(self) -> None:
        try:
            source_path = self.pdf_var.get().strip()
            if not source_path:
                raise ValueError('请先在"选择文件"里选择Word文档')
            if not os.path.isfile(source_path):
                raise ValueError('文件不存在: %s' % source_path)
            extension = os.path.splitext(source_path)[1].lower()
            if extension not in doc2pdf.DOC_EXTENSIONS:
                raise ValueError('仅支持 Word 文档: %s'
                                 % ' '.join(sorted(doc2pdf.DOC_EXTENSIONS)))
            save_path = filedialog.asksaveasfilename(
                title='另存为（转换后的PDF）',
                defaultextension='.pdf',
                initialdir=os.path.dirname(source_path) or None,
                initialfile=os.path.splitext(os.path.basename(source_path))[0] + '_转PDF.pdf',
                filetypes=[('PDF', '*.pdf')])
            if not save_path:
                self.logln('已取消：未选择保存路径')
                return
            self.logln('正在转换Word为PDF（后台）…')
            self._task_start('convert', doc2pdf._mp_doc_to_pdf, (source_path, save_path))
        except Exception as error:
            self.logln('错误: %s' % error)
            messagebox.showerror('错误', str(error))

    def do_doc2html(self) -> None:
        try:
            source_path = self.pdf_var.get().strip()
            if not source_path:
                raise ValueError('请先在"选择文件"里选择Word文档')
            if not os.path.isfile(source_path):
                raise ValueError('文件不存在: %s' % source_path)
            if os.path.splitext(source_path)[1].lower() != '.docx':
                raise ValueError('纯Python转HTML仅支持 .docx（%s 需安装 Office/WPS 或 LibreOffice）'
                                 % os.path.splitext(source_path)[1].lower())
            save_path = filedialog.asksaveasfilename(
                title='另存为（转换后的HTML）',
                defaultextension='.html',
                initialdir=os.path.dirname(source_path) or None,
                initialfile=os.path.splitext(os.path.basename(source_path))[0] + '_转HTML.html',
                filetypes=[('HTML', '*.html')])
            if not save_path:
                self.logln('已取消：未选择保存路径')
                return
            self.logln('正在转换Word为HTML（后台）…')
            self._task_start('convert', doc2pdf._mp_doc_to_html, (source_path, save_path))
        except Exception as error:
            self.logln('错误: %s' % error)
            messagebox.showerror('错误', str(error))

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

    @staticmethod
    def _validate_digits(text: str) -> bool:
        """输入框只允许数字；返回 False 时该字符不显示"""
        return bool(re.fullmatch(r'\d*', text))

    @staticmethod
    def _validate_range_input(text: str) -> bool:
        """页号范围输入框只允许 数字和连字符（如 12-30 或 12）；返回 False 时该字符不显示"""
        return bool(re.fullmatch(r'\d*[—–\-]?\d*', text))

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
                self.logln('识别目录必填：正文第一页的PDF页号和印刷页码')
                self.ocr_offset_error_label.configure(text='识别目录必填：PDF页号和印刷页码')
                self.root.after(5000, lambda: self.ocr_offset_error_label.configure(text=''))
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
        dialog = _Toplevel(self.root)
        dialog.title('导入外部AI识别的目录')
        dialog.transient(self.root)
        dialog.geometry('640x420')
        _Label(dialog, text='粘贴AI识别出的目录文本（每行：标题+页码，支持印刷页码/页码范围/[p页号]，'
                            '行首缩进表示层级），将格式化为标准格式并保存：',
               justify='left', anchor='w', wraplength=600).pack(fill='x', padx=10, pady=(10, 4))
        text_widget = tk.Text(dialog, width=78, height=13)
        text_widget.pack(fill='both', expand=True, padx=10)
        status_var = tk.StringVar()
        _Label(dialog, textvariable=status_var,
               foreground='#ff6b6b' if 'dark' in CURRENT_THEME else '#dc3545', justify='left',
               anchor='w', wraplength=600).pack(fill='x', padx=10, pady=2)

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
        _btn(button_row, 'primary', text='格式化并保存…', command=on_format).pack(side='left')
        _btn(button_row, 'secondary-outline', text='取消',
             command=dialog.destroy).pack(side='left', padx=8)
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
                offset = self._field_offset()
                if offset is None:
                    self.logln('写书签必填：正文第一页的PDF页号和印刷页码（txt为印刷页码格式）')
                    self.ocr_offset_error_label.configure(
                        text='写书签必填：PDF页号和印刷页码')
                    self.root.after(5000, lambda: self.ocr_offset_error_label.configure(text=''))
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
    global _BOOT
    # 优先使用支持文件拖放的根窗口；未安装 tkinterdnd2 时降级普通窗口
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    if HAS_TTKB:
        _BOOT = tb.Style(theme=DEFAULT_THEME)
    _apply_global_font(root)
    App(root)
    root.mainloop()
    return 0
