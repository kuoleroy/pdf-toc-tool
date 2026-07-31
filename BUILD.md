# 如何自己打包成软件（exe）

本文档写给没有编程环境的用户。按步骤操作，大约 10 分钟。

本工具提供两个版本：

| 版本 | 功能 | 打包命令见 |
|---|---|---|
| **PDFTocTool.exe** | 手工 txt 目录 → 写书签；PDF → txt 提取 | 第 3 步 |
| **PDFTocToolOCR.exe** | 以上全部 + 扫描目录页自动 OCR 识别 | 第 4 步 |

## 方法一：本机打包（推荐）

### 第 1 步：安装 Python

1. 打开 <https://www.python.org/downloads/>，下载最新的 Windows 安装包（64-bit）
2. 运行安装时**务必勾选** "Add python.exe to PATH"（添加到系统路径），然后点 Install Now
3. 安装完成后，按 `Win + R`，输入 `cmd` 回车，输入下面命令验证：

   ```bat
   python --version
   ```
   显示 `Python 3.x.x` 即成功。

### 第 2 步：安装打包工具和依赖

在 cmd 里执行（只执行一次）：

```bat
cd /d %USERPROFILE%\Desktop\pdf-toc-tool
pip install pyinstaller
pip install -r requirements.txt
```

### 第 3 步：打包不带OCR版

```bat
cd /d %USERPROFILE%\Desktop\pdf-toc-tool
pyinstaller --onefile --windowed --noupx --exclude-module rapidocr_onnxruntime --name PDFTocTool pdf_toc_tool.py
```

> `--noupx` 必须保留：Windows 打包机若装有 UPX，PyInstaller 会自动压缩，
> 压缩后的 exe 会损坏无法运行。

### 第 4 步：打包带OCR版（可选）

```bat
cd /d %USERPROFILE%\Desktop\pdf-toc-tool
pip install -r requirements-ocr.txt
pyinstaller --onefile --windowed --noupx --collect-all rapidocr_onnxruntime --collect-all onnxruntime --name PDFTocToolOCR pdf_toc_tool.py
```

等待几分钟（出现 `Build complete!` 即完成），成品在 `dist` 文件夹里：

```text
dist\PDFTocTool.exe       # 不带OCR版（双击使用）
dist\PDFTocToolOCR.exe    # 带OCR版（双击使用）
```

把 `dist` 里的 exe 复制到任何电脑都能直接运行，**不需要安装 Python**。
带OCR版首次执行"识别目录"时会自动下载 OCR 模型（需联网）。

### 以后改了源码怎么办？

重复"第 3 步"（或第 4 步）。若嫌慢，可去掉 `--onefile`：

```bat
pyinstaller --onedir --windowed --name PDFTocTool pdf_toc_tool.py
```

生成 `dist\PDFTocTool\` 文件夹，双击里面的 exe。启动更快、打包更快，但要把整个文件夹一起给别人。

## 方法二：GitHub Actions 自动打包（需要 GitHub 账号）

仓库已内置 `.github/workflows/build-exe.yml`：

1. 把仓库推到 GitHub
2. 打版本标签并推送：

   ```bat
   git tag v1.1.0
   git push origin v1.1.0
   ```

3. 等几分钟，GitHub 会自动打包并在 Releases 页面生成两个 exe 供下载

也可以不推标签，在仓库 Actions 页面手动点 "Run workflow"。

## 常见问题

| 问题 | 解决方法 |
|---|---|
| `python 不是内部或外部命令` | Python 没加入 PATH，重装并勾选 "Add python.exe to PATH" |
| `pip 不是内部或外部命令` | 同上；或执行 `python -m pip install ...` |
| 杀毒软件报毒 | PyInstaller 单文件版常见误报，属正常；加白名单或用 `--onedir` 模式 |
| exe 体积大（约 470MB） | 因为内置了 PyMuPDF 完整库；属正常 |
| 打包提示缺 `fitz` | 先执行 `pip install -r requirements.txt` |
| 下载的 exe 无法运行（报 PKG archive 错误） | 旧版被 UPX 压缩损坏；v1.1.0 起已加 `--noupx`，请用新版本 |

## 快速验证打包结果

打包完成后，命令行可自测（`-w` 的 exe 也会输出）：

```bat
dist\PDFTocTool.exe extract 测试.pdf 测试.txt
```
