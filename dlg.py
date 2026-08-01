# -*- coding: utf-8 -*-
"""对话框组件"""
import tkinter as tk


def ask_offset_fields(parent, first_pdf_var, first_print_var):
    """弹框要求填写正文第一页的PDF页号和印刷页码；返回 (pdf_no, print_no) 或 None(取消)"""
    top = tk.Toplevel(parent)
    top.title('填写偏移信息（必填）')
    top.transient(parent)
    top.grab_set()
    top.resizable(False, False)
    tk.Label(top, text='为防止书签页码错位，请填写正文第一页（目录后第一个正文页）的信息:',
             anchor='w').pack(fill='x', padx=12, pady=(10, 4))
    row = tk.Frame(top)
    row.pack(padx=12, pady=4)
    tk.Label(row, text='PDF页号:').pack(side='left')
    v1 = tk.StringVar(value=first_pdf_var.get())
    tk.Entry(row, textvariable=v1, width=6).pack(side='left', padx=4)
    tk.Label(row, text='印刷页码:').pack(side='left')
    v2 = tk.StringVar(value=first_print_var.get())
    tk.Entry(row, textvariable=v2, width=6).pack(side='left', padx=4)
    tk.Label(top, text='印刷页码 = 该页正文右下角印的数字（如 1）。',
             fg='gray').pack(anchor='w', padx=12)
    err = tk.Label(top, text='', fg='red')
    err.pack(padx=12, anchor='w')
    result = {}

    def ok():
        try:
            p1, p2 = int(v1.get().strip()), int(v2.get().strip())
            if p1 < 1 or p2 < 1:
                raise ValueError
        except ValueError:
            err.configure(text='请输入有效的正整数')
            return
        result['ok'] = (p1, p2)
        top.destroy()

    def cancel():
        result['ok'] = None
        top.destroy()

    row2 = tk.Frame(top)
    row2.pack(padx=12, pady=8)
    tk.Button(row2, text='确定', command=ok, width=10).pack(side='left', padx=4)
    tk.Button(row2, text='取消', command=cancel, width=10).pack(side='left', padx=4)
    top.update_idletasks()
    top.geometry('+%d+%d' % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 120))
    row.winfo_children()[1].focus_set()
    parent.wait_window(top)
    return result.get('ok')
