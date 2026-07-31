# PDF 书签工具（PDF Toc Tool）

为扫描版 PDF 批量提取 / 写入书签（目录）的轻量工具。Python 实现（GUI + CLI），
基于 [PyMuPDF](https://pymupdf.readthedocs.io/)。提供**两个版本**：

| 版本 | 说明 | 体积 |
|---|---|---|
| **PDFTocTool.exe**（不带OCR） | 手工 txt 目录 → 写书签；PDF → txt 提取 | ~470MB |
| **PDFTocToolOCR.exe**（带OCR） | 额外支持：扫描目录页自动识别为 txt | ~490MB |

OCR 后端为 [RapidOCR](https://github.com/RapidAI/RapidOCR)（PaddleOCR 模型权重 + onnxruntime 推理），
首次识别自动下载模型（需联网）。

最初用于给《列宁选集》(1972年版 扫描版) 添加目录书签。

## 功能

- **提取书签**：从 PDF 提取目录到 txt（含层级缩进与 PDF 页号），修改后可原样回写
- **写入书签**：将 txt 目录写入 PDF，兼容两种常见格式（见下）
- **OCR 识别目录**（带OCR版）：给定扫描目录页的 PDF 页号范围，自动识别为可编辑 txt，
  支持页码范围（`1-85`）、自动合并被拆散的标题/页码、自动检测页码偏移
- **两种层级来源**：txt 缩进自动定层级；含 `[p页号]` 时直接使用 PDF 页号
- **安全写入**：默认生成 `_带目录.pdf` 副本（增量保存，原文件不动），也可选择直接写原文件

## 快速开始

### 图形界面

```bash
python pdf_toc_tool.py
```

1. 选择 PDF 文件（写入模式还需选择目录 txt）
2. 选择"写入书签"或"提取书签"
3. 写入模式设置印刷页偏移（默认 15，见"页码偏移"一节；带OCR版可自动检测）
4. 预览后执行

带OCR版还可在"OCR 识别目录页"区域输入扫描目录页的 **PDF 页号**范围（如 `11-16`），
点"识别目录"后到"OCR结果"页签检查、修改，再点"确认写入"。
若自动检测偏移失败，可填"正文第一页"的 PDF 页号和印刷页码，点"算偏移"。

### 命令行

```bash
# 提取书签
python pdf_toc_tool.py extract <pdf> [输出txt]

# 写入书签
python pdf_toc_tool.py write <pdf> <目录txt> [偏移(默认15)] [copy|same]

# OCR 识别目录页（带OCR版；-a 自动检测偏移）
python pdf_toc_tool.py ocr <pdf> <起始PDF页>-<结束PDF页> [-a] [-o 输出txt]
```

`copy` 生成 `_带目录.pdf` 副本；`same` 直接增量写原文件（建议先备份）。

### 打包为 exe（Windows）

```bash
pip install pyinstaller

# 不带OCR版
pyinstaller --onefile --windowed --noupx --exclude-module rapidocr_onnxruntime --name PDFTocTool pdf_toc_tool.py

# 带OCR版
pip install -r requirements-ocr.txt
pyinstaller --onefile --windowed --noupx --collect-all rapidocr_onnxruntime --collect-all onnxruntime --name PDFTocToolOCR pdf_toc_tool.py
```

详细步骤（含 Python 安装、常见问题、GitHub Actions 自动打包）见 [BUILD.md](BUILD.md)。

## 目录 txt 格式

层级由行首缩进决定：无缩进 = 1 级，1 级缩进 = 2 级，2 级及以上 = 3 级
（缩进使用全角空格 `　` 或普通空格均可）。无页码的行视为上一标题的续行，自动合并。

支持三种页码写法：

```text
# 格式A：印刷页码（配合偏移量换算）
远方来信（摘录） ……………………………… 1–12        # 可带点线和页码范围
  第一封信 第一次革命的第一阶段 …………… 1
提纲 13                                          # 无点线亦可

# 格式B：PDF 页号（提取后直接改回写，无需偏移）
伟大的创举（论后方工人的英雄主义） [p15]
  目前的主要任务 [p38]
```

## 页码偏移

txt 中的页码通常是"印刷页码"，而 PDF 文件（扫描件）开头还有版权页、原书目录等前置页，
两者差值为偏移量 `offset`（0-based PDF 页索引 − 印刷页码）。
书签的 PDF 页号 = 印刷页码 + offset + 1。

- 《列宁选集》1972 版第 1–3 卷：offset = 15
- 第 4 卷：offset = 13（前置页数不同）

带OCR版会尝试自动检测偏移；检测失败时手工计算：
正文第一页的 PDF 索引（PDF页号 − 1）减去该页的印刷页码即为 offset。
如果 txt 直接使用 `[p页号]` 写法，则不需要偏移。

## OCR 输出说明

识别结果中 `#` 开头的行为提示行（无页码的续行、缺标题、低置信度、页码异常），
可直接删除或修改。"确认写入"前请核对条数是否正确。

## 项目结构

```text
pdf-toc-tool/
├── pdf_toc_tool.py        # 入口（无参数=GUI，带参数=CLI）
├── core.py                # 核心逻辑：txt解析、书签写入/提取
├── ocr.py                 # OCR（可选依赖，未装时自动禁用）
├── gui.py                 # tkinter 图形界面
├── cli.py                 # 命令行接口
└── (构建相关：BUILD.md / LICENSE / requirements*.txt 等)
```

## 开源许可

[MIT License](LICENSE)
