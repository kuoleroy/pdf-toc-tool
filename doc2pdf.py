# -*- coding: utf-8 -*-
"""Word 文档转 PDF：优先 Office/WPS COM（需 pywin32），回退 LibreOffice soffice 命令行。

转换引擎检测顺序：
1. pywin32 + Word.Application / wps.Application COM 自动化
2. LibreOffice（soffice，PATH 或常见安装路径）

两者都不可用时抛出清晰错误，提示安装其一。
"""
import os
import shutil
import subprocess
from typing import Optional

try:
    import win32com.client  # pywin32
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

DOC_EXTENSIONS = frozenset({'.doc', '.docx', '.rtf', '.dot', '.dotx'})

SOFFICE_CANDIDATES = (
    'soffice',
    'C:\\Program Files\\LibreOffice\\program\\soffice.exe',
    'C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe',
)

COM_PROG_IDS = ('Word.Application', 'wps.Application')

WD_FORMAT_PDF = 17  # wdFormatPDF，WPS 兼容


def find_soffice() -> Optional[str]:
    """在 PATH 与常见安装路径中查找 LibreOffice 可执行文件"""
    for candidate in SOFFICE_CANDIDATES:
        resolved = shutil.which(candidate) if os.sep not in candidate else candidate
        if resolved and os.path.isfile(resolved):
            return resolved
    return None


def _convert_via_com(src: str, out: str) -> None:
    """Word/WPS COM：打开文档另存为 PDF"""
    import pythoncom
    pythoncom.CoInitialize()
    word = None
    try:
        for prog_id in COM_PROG_IDS:
            try:
                word = win32com.client.Dispatch(prog_id)
                break
            except Exception:
                word = None
        if word is None:
            raise RuntimeError('未检测到 Microsoft Office 或 WPS')
        document = word.Documents.Open(os.path.abspath(src), ReadOnly=True)
        try:
            document.SaveAs(os.path.abspath(out), FileFormat=WD_FORMAT_PDF)
        finally:
            document.Close(False)
        word.Quit()
        word = None
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _convert_via_soffice(soffice: str, src: str, out: str) -> None:
    """LibreOffice 命令行：--convert-to pdf 输出到目标目录后改名"""
    out_dir = os.path.dirname(os.path.abspath(out)) or '.'
    result = subprocess.run(
        [soffice, '--headless', '--convert-to', 'pdf', '--outdir', out_dir,
         os.path.abspath(src)],
        capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError('LibreOffice 转换失败: %s'
                           % (result.stderr or result.stdout or '').strip())
    base = os.path.splitext(os.path.basename(src))[0] + '.pdf'
    produced = os.path.join(out_dir, base)
    if not os.path.isfile(produced):
        raise RuntimeError('LibreOffice 转换失败：未生成PDF文件')
    if os.path.abspath(produced) != os.path.abspath(out):
        os.replace(produced, out)


def doc_to_pdf(src: str, out: str) -> str:
    """把 Word 文档转为 PDF（优先 COM，回退 LibreOffice），返回输出路径"""
    if not os.path.isfile(src):
        raise ValueError('文件不存在: %s' % src)
    extension = os.path.splitext(src)[1].lower()
    if extension not in DOC_EXTENSIONS:
        raise ValueError('仅支持 Word 文档: %s' % ' '.join(sorted(DOC_EXTENSIONS)))
    if HAS_WIN32COM:
        try:
            _convert_via_com(src, out)
            return out
        except RuntimeError:
            pass
        except Exception as com_error:
            soffice = find_soffice()
            if soffice is None:
                raise ValueError('Office/WPS 转换失败: %s' % com_error)
    soffice = find_soffice()
    if soffice:
        _convert_via_soffice(soffice, src, out)
        return out
    raise RuntimeError('未检测到可用的转换引擎：请安装 Microsoft Office/WPS（或 LibreOffice）后再试')


def _mp_doc_to_pdf(q, src, out) -> None:
    """子进程入口：Word转PDF -> ('convert_done', out)"""
    try:
        doc_to_pdf(src, out)
        q.put(('convert_done', out))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))
