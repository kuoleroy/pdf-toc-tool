# -*- coding: utf-8 -*-
"""Word 文档转 PDF / HTML。

转换引擎检测顺序（转 PDF）：
1. Office/WPS COM（需 pywin32）
2. LibreOffice（soffice，PATH 或常见安装路径）
3. 纯 Python（仅 .docx：mammoth -> xhtml2pdf，中文字体用内置 CID 字体 STSong-Light，
   排版与 Word 有差异，无引擎时的简化方案）

转 HTML（仅 .docx）：mammoth 直接转换，无需任何引擎。

所有引擎都不可用且非 .docx 时，抛出清晰错误提示安装其一。

文件底部的 _mp_doc_to_pdf/_mp_doc_to_html 是供 taskmgr 子进程调用的
入口，转换结果经 multiprocessing 队列回传主进程。
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

# .docx 为 OOXML 格式，可走纯 Python 引擎；.doc/.rtf/.dot/.dotx 是
# 旧格式（二进制/RTF），只能依赖 COM 或 LibreOffice 转换
DOC_EXTENSIONS = frozenset({'.doc', '.docx', '.rtf', '.dot', '.dotx'})

SOFFICE_CANDIDATES = (
    'soffice',
    'C:\\Program Files\\LibreOffice\\program\\soffice.exe',
    'C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe',
)

COM_PROG_IDS = ('Word.Application', 'wps.Application')

WD_FORMAT_PDF = 17  # wdFormatPDF，WPS 兼容

# 纯 Python 路径用的模板：STSong-Light 是 PDF 阅读器内置的中文 CID 字体，
# 无需嵌入字体文件即可正确显示中文，但字形与 Word 有差异
PURE_HTML_TEMPLATE = (
    '<html><head><meta charset="utf-8">'
    '<style>body { font-family: STSong-Light; }'
    'table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 2px 6px; }'
    '</style></head><body>%s</body></html>')

# 浏览器查看用模板：指定系统中文字体，纯前端渲染，与 PDF 生成无关
HTML_TEMPLATE = (
    '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<title>%s</title>'
    '<style>body { font-family: "Microsoft YaHei", SimSun, sans-serif; '
    'max-width: 800px; margin: 0 auto; padding: 16px; }'
    'table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 2px 6px; }'
    '</style></head><body>%s</body></html>')


def find_soffice() -> Optional[str]:
    """在 PATH 与常见安装路径中查找 LibreOffice 可执行文件"""
    for candidate in SOFFICE_CANDIDATES:
        resolved = shutil.which(candidate) if os.sep not in candidate else candidate
        if resolved and os.path.isfile(resolved):
            return resolved
    return None


def _mammoth_html(docx_path: str) -> str:
    """docx -> HTML（mammoth）；缺少依赖时给出安装提示"""
    try:
        import mammoth
    except ImportError:
        raise RuntimeError('纯Python转换缺少依赖：请运行 pip install mammoth xhtml2pdf 后重试')
    return mammoth.convert_to_html(docx_path).value


def _convert_via_com(src: str, out: str) -> None:
    """Word/WPS COM：打开文档另存为 PDF"""
    import pythoncom
    # COM 调用前必须先在线程中初始化；本函数可能运行在子进程里
    pythoncom.CoInitialize()
    word = None
    try:
        # 依次尝试 Word 与 WPS，谁注册了 ProgID 就用谁
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
        # 兜底清理：转换出错时也要退出 Office/WPS 并释放 COM 线程初始化
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _convert_via_soffice(soffice: str, src: str, out: str) -> None:
    """LibreOffice 命令行：--convert-to pdf 输出到目标目录后改名"""
    # soffice 只接受输出目录、不接受输出文件名，产物按"源文件名.pdf"
    # 命名，因此转换后需要移动/改名到目标路径
    out_dir = os.path.dirname(os.path.abspath(out)) or '.'
    result = subprocess.run(
        [soffice, '--headless', '--convert-to', 'pdf', '--outdir', out_dir,
         os.path.abspath(src)],
        capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError('LibreOffice 转换失败: %s'
                           % (result.stderr or result.stdout or '').strip())
    # 目标路径与产物不同名时用 os.replace 改名（同盘符内是原子操作）
    base = os.path.splitext(os.path.basename(src))[0] + '.pdf'
    produced = os.path.join(out_dir, base)
    if not os.path.isfile(produced):
        raise RuntimeError('LibreOffice 转换失败：未生成PDF文件')
    if os.path.abspath(produced) != os.path.abspath(out):
        os.replace(produced, out)


def _convert_via_pure(docx_path: str, out: str) -> None:
    """纯Python：mammoth -> xhtml2pdf（中文字体 STSong-Light，无嵌入）"""
    # 仅 .docx 可走此路径；xhtml2pdf 使用内置 CID 字体 STSong-Light，
    # 不嵌入字体文件，部分阅读器/打印场景下渲染结果与 Word 有差异
    try:
        from xhtml2pdf import pisa
    except ImportError:
        raise RuntimeError('纯Python转换缺少依赖：请运行 pip install mammoth xhtml2pdf 后重试')
    html = _mammoth_html(docx_path)
    styled = PURE_HTML_TEMPLATE % html
    with open(out, 'wb') as file_handle:
        result = pisa.CreatePDF(styled, dest=file_handle)
    if result.err:
        raise RuntimeError('纯Python转换失败（xhtml2pdf 错误码 %d）' % result.err)


def doc_to_pdf(src: str, out: str) -> str:
    """把 Word 文档转为 PDF（COM -> LibreOffice -> 纯Python），返回输出路径"""
    if not os.path.isfile(src):
        raise ValueError('文件不存在: %s' % src)
    extension = os.path.splitext(src)[1].lower()
    if extension not in DOC_EXTENSIONS:
        raise ValueError('仅支持 Word 文档: %s' % ' '.join(sorted(DOC_EXTENSIONS)))
    # 降级链：COM（效果最好）-> LibreOffice -> 纯 Python（仅 .docx）。
    # RuntimeError 表示"未安装对应软件"，可直接忽略换下一引擎；
    # 其他异常（如文档损坏）记录到 com_error，供最终向用户报告
    com_error = None
    if HAS_WIN32COM:
        try:
            _convert_via_com(src, out)
            return out
        except RuntimeError:
            pass
        except Exception as error:
            com_error = error
    soffice = find_soffice()
    if soffice:
        try:
            _convert_via_soffice(soffice, src, out)
            return out
        except Exception:
            pass
    if extension == '.docx':
        try:
            _convert_via_pure(src, out)
            return out
        except RuntimeError:
            raise
    if com_error is not None:
        raise RuntimeError('Office/WPS 转换失败: %s' % com_error)
    raise RuntimeError(
        '未检测到可用的转换引擎，且该格式（%s）不支持纯Python转换：'
        '请安装 Microsoft Office/WPS 或 LibreOffice 后再试' % extension)


def doc_to_html(docx_path: str, out: str) -> str:
    """把 .docx 转为完整 HTML 页面（mammoth，无需任何引擎），返回输出路径"""
    if not os.path.isfile(docx_path):
        raise ValueError('文件不存在: %s' % docx_path)
    extension = os.path.splitext(docx_path)[1].lower()
    if extension != '.docx':
        raise ValueError('纯Python转HTML仅支持 .docx（%s 需安装 Office/WPS 或 LibreOffice）'
                         % extension)
    html = _mammoth_html(docx_path)
    title = os.path.splitext(os.path.basename(docx_path))[0]
    full_html = HTML_TEMPLATE % (title, html)
    with open(out, 'w', encoding='utf-8') as file_handle:
        file_handle.write(full_html)
    return out


# 子进程入口：由 taskmgr 启动，队列 q 为第一个参数；
# 成功回传 ('convert_done', out)，失败回传 ('error', 错误文本)
def _mp_doc_to_pdf(q, src, out) -> None:
    """子进程入口：Word转PDF -> ('convert_done', out)"""
    try:
        doc_to_pdf(src, out)
        q.put(('convert_done', out))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))


# 同上：Word 转 HTML 的子进程入口，消息协议与 _mp_doc_to_pdf 一致
def _mp_doc_to_html(q, src, out) -> None:
    """子进程入口：Word转HTML -> ('convert_done', out)"""
    try:
        doc_to_html(src, out)
        q.put(('convert_done', out))
    except Exception as error:
        q.put(('error', type(error).__name__ + ': ' + str(error)))
