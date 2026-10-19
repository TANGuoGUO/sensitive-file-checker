# 敏感资料检查工具

在 Windows 办公电脑上按关键词检查文件名和文件文字内容，供合规自查和人工复核。使用 Python + PySide6，提供无需安装 Python 的独立 EXE。

[下载 Windows EXE](https://github.com/TANGuoGUO/sensitive-file-checker/releases/latest) · [全部版本](https://github.com/TANGuoGUO/sensitive-file-checker/releases)

![程序界面，使用虚构示例数据](assets/implemented-exe-ui.png)

## 功能

- 多文件夹扫描、包含子文件夹、排除目录、文件类型和文件大小筛选。
- 文件名与内容可独立检查；支持任一 / 全部关键词、大小写选项、规则导入与保存。
- Word 段落、Excel 单元格、PPT 幻灯片及备注、PDF 页码等命中定位和原文高亮。
- 后台检查、暂停、继续、停止；读取失败与内容覆盖不完整的文件单独展示。
- CSV 和 JSON 报告，包含检查范围、规则、结果、任务状态和异常记录。
- 本地只读检查，不上传文件或内容；启动时不会自动扫描任何目录。

## 使用

从 Releases 下载 `SensitiveFileChecker.exe` 后双击运行，无需安装 Python。添加检查目录，输入关键词，选择文件名 / 内容、任一 / 全部关键词、文件类型和其他范围设置，然后开始检查。检查过程中可以暂停、继续或停止；停止后的结果只包含已处理文件，不代表整个目录检查完成。

关键词按字面文本匹配，每行一个。当同时选择文件名与内容时，两个位置的命中会合并：“全部关键词”要求每个关键词在同一文件的文件名或可提取内容中至少出现一次。可导入仓库中的 `sample-rules.json` 作为规则示例。

默认检查常见 Office 文件、PDF 和文本文件，实际类型可在界面中选择。文件解析失败、超出大小限制、权限不足和目录访问异常会明确记录。命中表示发现了关键词，须人工核对文件及上下文，不能直接据此认定违规。

## 报告

- **JSON**：完整任务报告，包含检查范围、规则、任务状态和统计、所有已处理文件及异常，适合留档。
- **CSV**：适合用 Excel 筛选的结果清单，使用 UTF-8 BOM，支持中文、路径和换行。`记录类型` 中 `FILE` 为文件结果，`TASK` 为任务摘要与范围规则，`WARNING` 为任务异常。即使没有文件结果，也会保留任务摘要，避免将未完成检查误认为无风险。
- 命中明细可能按预览数量上限截断，报告会标明。报告会包含路径、关键词和命中片段，本身也应按敏感资料妥善保存。

## 开发运行与打包

需要 Windows 10 / 11、Python 3.9 或更高版本。开发运行：

```powershell
git clone https://github.com/TANGuoGUO/sensitive-file-checker.git
cd sensitive-file-checker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

构建独立 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

可用 `-PythonPath C:\路径\python.exe` 指定 Python。脚本自动寻找可用 Python、检查依赖，失败即停止；不会自动联网安装依赖。生成文件为 `dist\SensitiveFileChecker.exe`，首次启动需等待单文件程序解包。

运行测试：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

`--demo` 只展示虚构界面数据；正常启动不会自动扫描任何目录。`--smoke-test 检查结果.json` 使用自动创建的人工文本、PDF 和加密 PDF 验证程序，不读取用户办公文件。

独立验证打包后的程序：

```powershell
.\dist\SensitiveFileChecker.exe --smoke-test smoke-result.json
```

当前验证：41 项自动测试通过；打包 EXE 启动、人工文本/PDF 命中和加密 PDF 异常处理自检通过。旧版 Office 自动化分支已用模拟测试验证，尚未进行真实 Microsoft Office 读取实测。

## 检查边界

- 检查只读取所选文件，结果保存在本机，不上传文件或检查内容。
- DOCX / XLSX / PPTX 等现代 Office 格式直接解析；旧版 DOC / XLS / PPT 需要 Windows 上安装对应 Microsoft Office 组件，并启用旧格式兼容读取（默认关闭）。旧格式解析通过 Office 自动化完成，可能受到 Office 安装、首次启动对话框或文件保护状态影响。
- 扫描版 PDF 和图片不做 OCR；检测到图片、嵌入对象等未展开内容时，会显示“有遗漏”并记录原因。图片文字、嵌入对象内部内容、音视频、压缩包内部文件不属于内容检查范围。
- 加密、损坏、受权限限制或超过限制的文件可能无法完整检查，请查看异常与内容检查状态。未命中不能证明机器上不存在内部资料。

## 项目结构

| 文件 | 用途 |
| --- | --- |
| `app.py` | PySide6 界面、后台任务与交互 |
| `scanner.py` | 目录遍历、关键词匹配、暂停停止与统计 |
| `extractors.py` | Office / PDF / 文本读取与覆盖状态 |
| `reports.py` | JSON / CSV 报告导出 |
| `tests/` | 人工文件与模拟场景测试 |
| `build.ps1`、`SensitiveFileChecker.spec` | Windows 单文件 EXE 构建 |

`assets/ui-concept.png` 为生图设计稿，`assets/implemented-exe-ui.png` 为实际程序界面（虚构示例数据）。设计使用内置 imagegen，提示词保存在 `assets/ui-design-prompt.txt`。依赖通过 `requirements.txt` 安装，构建缓存、临时检查报告和依赖安装目录不纳入仓库。
