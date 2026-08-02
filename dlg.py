# -*- coding: utf-8 -*-
"""对话框组件：模态输入弹框"""
import os
import re
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Optional, Tuple


def ask_offset_fields(parent: tk.Tk, first_pdf_page_var: tk.StringVar,
                      first_printed_page_var: tk.StringVar) -> Optional[Tuple[int, int]]:
    """弹框要求填写正文第一页的PDF页号和印刷页码（用于计算偏移量）。

    返回 (pdf_page_number, printed_page_number)；取消返回 None。
    边界情况：输入非法时在弹框内提示，不关闭弹框。
    """
    dialog = tk.Toplevel(parent)
    dialog.title('填写偏移信息（必填）')
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    digits_cmd = (dialog.register(_digits_only), '%P')

    tk.Label(dialog, text='为防止书签页码错位，请填写正文第一页（目录后第一个正文页）的信息:',
             anchor='w').pack(fill='x', padx=12, pady=(10, 4))

    input_row = tk.Frame(dialog)
    input_row.pack(padx=12, pady=4)
    tk.Label(input_row, text='PDF页号:').pack(side='left')
    pdf_page_var = tk.StringVar(value=first_pdf_page_var.get())
    tk.Entry(input_row, textvariable=pdf_page_var, width=6,
             validate='key', validatecommand=digits_cmd).pack(side='left', padx=4)
    tk.Label(input_row, text='印刷页码:').pack(side='left')
    printed_page_var = tk.StringVar(value=first_printed_page_var.get())
    tk.Entry(input_row, textvariable=printed_page_var, width=6,
             validate='key', validatecommand=digits_cmd).pack(side='left', padx=4)

    tk.Label(dialog, text='印刷页码 = 该页正文右下角印的数字（如 1）。',
             fg='gray').pack(anchor='w', padx=12)
    error_label = tk.Label(dialog, text='', fg='red')
    error_label.pack(padx=12, anchor='w')

    dialog_result = {}

    def on_confirm() -> None:
        try:
            pdf_page_number = int(pdf_page_var.get().strip())
            printed_page_number = int(printed_page_var.get().strip())
            if pdf_page_number < 1 or printed_page_number < 1:
                raise ValueError
        except ValueError:
            error_label.configure(text='请输入有效的正整数')
            return
        dialog_result['value'] = (pdf_page_number, printed_page_number)
        dialog.destroy()

    def on_cancel() -> None:
        dialog_result['value'] = None
        dialog.destroy()

    button_row = tk.Frame(dialog)
    button_row.pack(padx=12, pady=8)
    tk.Button(button_row, text='确定', command=on_confirm, width=10).pack(side='left', padx=4)
    tk.Button(button_row, text='取消', command=on_cancel, width=10).pack(side='left', padx=4)

    dialog.update_idletasks()
    dialog.geometry('+%d+%d' % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 120))
    input_row.winfo_children()[1].focus_set()
    parent.wait_window(dialog)
    return dialog_result.get('value')


def _digits_only(text: str) -> bool:
    return bool(re.fullmatch(r'\d*', text))


def _range_input(text: str) -> bool:
    return bool(re.fullmatch(r'\d*[—–\-]?\d*', text))


def ask_ocr_args(parent: tk.Tk, pdf_path_var: tk.StringVar,
                 ocr_range_var: tk.StringVar) -> Optional[Tuple[str, str]]:
    """弹框选择PDF文件并填写目录页的PDF页号范围（如 11-16）。

    返回 (pdf_path, range_text)；取消返回 None。
    边界情况：PDF 不存在或范围格式非法时在弹框内提示，不关闭弹框。
    """
    dialog = tk.Toplevel(parent)
    dialog.title('填写OCR识别信息（必填）')
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    range_cmd = (dialog.register(_range_input), '%P')

    tk.Label(dialog, text='请选择PDF文件并填写目录页的PDF页号范围:',
             anchor='w').pack(fill='x', padx=12, pady=(10, 4))

    file_row = tk.Frame(dialog)
    file_row.pack(fill='x', padx=12, pady=4)
    tk.Label(file_row, text='PDF文件:').pack(side='left')
    pdf_path_input_var = tk.StringVar(value=pdf_path_var.get())
    tk.Entry(file_row, textvariable=pdf_path_input_var, width=40).pack(
        side='left', fill='x', expand=True, padx=4)

    def on_browse() -> None:
        selected_path = filedialog.askopenfilename(
            title='选择PDF文件',
            filetypes=[('PDF/电子书', '*.pdf *.epub *.mobi *.azw3 *.prc'), ('全部', '*.*')],
            parent=dialog)
        if selected_path:
            pdf_path_input_var.set(selected_path)

    tk.Button(file_row, text='浏览…', command=on_browse).pack(side='left')

    range_row = tk.Frame(dialog)
    range_row.pack(padx=12, pady=4)
    tk.Label(range_row, text='目录页PDF页号:').pack(side='left')
    range_input_var = tk.StringVar(value=ocr_range_var.get())
    tk.Entry(range_row, textvariable=range_input_var, width=12,
             validate='key', validatecommand=range_cmd).pack(side='left', padx=4)
    tk.Label(range_row, text='如 11-16（目录页在PDF中的页号）', fg='gray').pack(side='left')

    error_label = tk.Label(dialog, text='', fg='red')
    error_label.pack(padx=12, anchor='w')

    PAGE_RANGE_PATTERN = re.compile(r'^\s*\d+\s*[-—–]\s*\d+\s*$')
    dialog_result = {}

    def on_confirm() -> None:
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

    button_row = tk.Frame(dialog)
    button_row.pack(padx=12, pady=8)
    tk.Button(button_row, text='确定', command=on_confirm, width=10).pack(side='left', padx=4)
    tk.Button(button_row, text='取消', command=on_cancel, width=10).pack(side='left', padx=4)

    dialog.update_idletasks()
    dialog.geometry('+%d+%d' % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 120))
    dialog.wait_window(dialog)
    return dialog_result.get('value')
