# PDF 书签工具（PDF Toc Tool）

为扫描版 PDF 批量提取 / 写入书签（目录）的轻量工具。单文件 Python 实现（GUI + CLI），
基于 [PyMuPDF](https://pymupdf.readthedocs.io/)，无其他依赖。

最初用于给《列宁选集》(1972年版, 4卷, 扫描版) 添加目录书签。

## 功能

- **提取书签**：从 PDF 提取目录到 txt（含层级缩进与 PDF 页号），修改后可原样回写
- **写入书签**：将 txt 目录写入 PDF，兼容两种常见格式（见下）
- **两种层级来源**：txt 缩进自动定层级；含 `[p页号]` 时直接使用 PDF 页号
- **安全写入**：默认生成 `_带目录.pdf` 副本（增量保存，原文件不动），也可选择直接写原文件

## 快速开始

### 图形界面

```bash
python pdf_toc_tool.py
```

1. 选择 PDF 文件和目录 txt
2. 选择"写入书签"或"提取书签"
3. 设置印刷页偏移（默认 15，见"页码偏移"一节）
4. 预览后执行

### 命令行

```bash
# 提取书签
python pdf_toc_tool.py extract <pdf> [输出txt]

# 写入书签
python pdf_toc_tool.py write <pdf> <目录txt> [偏移(默认15)] [copy|same]
```

`copy` 生成 `_带目录.pdf` 副本；`same` 直接增量写原文件（建议先备份）。

### 打包为 exe（Windows）

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name PDFTocTool pdf_toc_tool.py   # GUI 版
pyinstaller --onefile --console   --name PDFTocToolCLI pdf_toc_tool.py # CLI 版
```

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

建议先抽查 PDF 中一两个页码与印刷页码的对应关系再批量处理。
如果 txt 直接使用 `[p页号]` 写法，则不需要偏移。

## 项目结构

```text
pdf-toc-tool/
├── pdf_toc_tool.py        # 主程序（GUI + CLI）
├── scripts/               # 历史构建脚本（针对《列宁选集》的定制流程）
│   ├── build_toc.py       # 第1卷（OCR 识别目录 + 结构规整）
│   ├── build_toc2.py      # 第2卷（人工录入 txt + 结构规则 + 错字修正表）
│   ├── build_toc34.py     # 第3/4卷（点线+页码范围格式解析）
│   ├── parse_toc.py       # 目录页 OCR 解析
│   └── verify_map.py      # 页码映射验证
```

`scripts/` 中的脚本含绝对路径，仅作为定制流程参考，不保证开箱即用。

## 开源许可

[MIT License](LICENSE)
