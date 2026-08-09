# -*- coding: utf-8 -*-
"""对话框组件：模态输入弹框（ttkbootstrap 主题，可选依赖）。

ttkbootstrap 未安装时自动降级为原生 tkinter 控件，界面代码不变。
对外仅提供 ask_ocr_args：选 PDF 文件 + 填写目录页的 PDF 页号范围。
"""
import os
import re
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Optional, Tuple

# ttkbootstrap 为可选依赖：已安装时弹窗带现代主题；
# 未安装时降级为原生 tkinter 控件。下方用统一别名 _Root/_Frame/_Label...
# 屏蔽差异，后续界面代码无需区分
try:
    import ttkbootstrap as tb
    HAS_TTKB = True
    _Root = tb.Toplevel
    _Frame = tb.Frame
    _Label = tb.Label
    _Entry = tb.Entry
    _Button = tb.Button
except ImportError:
    HAS_TTKB = False
    _Root = tk.Toplevel
    _Frame = tk.Frame
    _Label = tk.Label
    _Entry = tk.Entry
    _Button = tk.Button


def _digits_only(text: str) -> bool:
    """逐键校验：只允许数字输入（允许为空，便于退格删除）"""
    return bool(re.fullmatch(r'\d*', text))


def _is_dark_theme() -> bool:
    """弹窗运行时的主题是否为暗色（从 gui 读当前主题；gui 不可用时按默认暗色）"""
    try:
        from gui import CURRENT_THEME
        return 'dark' in CURRENT_THEME
    except Exception:
        return True


def _error_fg() -> str:
    """按当前主题返回错误提示文字颜色（暗色/亮色用不同色值）"""
    return '#ff6b6b' if _is_dark_theme() else '#dc3545'


def _hint_fg() -> str:
    """按当前主题返回辅助提示文字颜色"""
    return '#9aa0a6' if _is_dark_theme() else '#8a8f98'


def _range_input(text: str) -> bool:
    """逐键校验：数字 + 可选的单个连字符（—、–、-），可留空"""
    return bool(re.fullmatch(r'\d*[—–\-]?\d*', text))


def ask_ocr_args(parent: tk.Tk, pdf_path_var: tk.StringVar,
                 ocr_range_var: tk.StringVar) -> Optional[Tuple[str, str]]:
    """弹框选择PDF文件并填写目录页的PDF页号范围（如 11-16）。

    返回 (pdf_path, range_text)；取消返回 None。
    边界情况：PDF 不存在或范围格式非法时在弹框内提示，不关闭弹框。
    """
    # 模态弹窗三步：transient 将弹窗挂到主窗、grab_set 独占输入焦点、
    # wait_window 阻塞本函数直到弹窗关闭（见函数末尾）
    dialog = _Root(parent)
    dialog.title('填写OCR识别信息（必填）')
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    # register 注册逐键校验回调：'%P' 是输入后的新文本，不合法则拒绝输入
    range_cmd = (dialog.register(_range_input), '%P')

    _Label(dialog, text='请选择PDF文件并填写目录页的PDF页号范围:',
           anchor='w').pack(fill='x', padx=12, pady=(10, 4))

    file_row = _Frame(dialog)
    file_row.pack(fill='x', padx=12, pady=4)
    _Label(file_row, text='PDF文件:').pack(side='left')
    pdf_path_input_var = tk.StringVar(value=pdf_path_var.get())
    _Entry(file_row, textvariable=pdf_path_input_var, width=40).pack(
        side='left', fill='x', expand=True, padx=4)

    def on_browse() -> None:
        selected_path = filedialog.askopenfilename(
            title='选择PDF文件',
            filetypes=[('PDF/电子书', '*.pdf *.epub *.mobi *.azw3 *.prc'), ('全部', '*.*')],
            parent=dialog)
        if selected_path:
            pdf_path_input_var.set(selected_path)

    _Button(file_row, text='浏览…', command=on_browse).pack(side='left')

    range_row = _Frame(dialog)
    range_row.pack(padx=12, pady=4)
    _Label(range_row, text='目录页PDF页号:').pack(side='left')
    range_input_var = tk.StringVar(value=ocr_range_var.get())
    _Entry(range_row, textvariable=range_input_var, width=12,
           validate='key', validatecommand=range_cmd).pack(side='left', padx=4)
    _Label(range_row, text='如 11-16（目录页在PDF中的页号）',
           foreground=_hint_fg()).pack(side='left')

    error_label = _Label(dialog, text='', foreground=_error_fg())
    error_label.pack(padx=12, anchor='w')

    # 提交时严格校验：必须为"数字-数字"形式，与逐键校验的宽松规则互补
    PAGE_RANGE_PATTERN = re.compile(r'^\s*\d+\s*[-—–]\s*\d+\s*$')
    # wait_window 阻塞期间确定/取消回调在事件循环中执行，
    # 无法直接 return，用 dict 存放结果，关闭后统一取出
    dialog_result = {}

    def on_confirm() -> None:
        # 输入校验：PDF 文件必须存在、页号范围必须匹配"数字-数字"，
        # 不通过则在弹窗内显示红色错误提示，弹窗保持打开
        pdf_path = pdf_path_input_var.get().strip()
        range_text = range_input_var.get().strip()
        if not pdf_path or not os.path.isfile(pdf_path):
            error_label.configure(text='请选择有效的PDF文件')
            return
        if not PAGE_RANGE_PATTERN.match(range_text):
            error_label.configure(text='请填写目录页PDF页号范围，如 11-16')
            return
        dialog_result['value'] = (pdf_path, range_text)
        dialog.destroy()

    def on_cancel() -> None:
        dialog_result['value'] = None
        dialog.destroy()

    button_row = _Frame(dialog)
    button_row.pack(padx=12, pady=8)
    _Button(button_row, text='确定', command=on_confirm, width=10).pack(side='left', padx=4)
    _Button(button_row, text='取消', command=on_cancel, width=10).pack(side='left', padx=4)

    dialog.update_idletasks()
    # 偏移定位在父窗口右下方，避免完全遮挡父窗口内容
    dialog.geometry('+%d+%d' % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 120))
    dialog.wait_window(dialog)
    return dialog_result.get('value')
