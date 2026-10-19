"""Bounded local document text extraction. No document is modified or uploaded."""
import codecs
import multiprocessing
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional
from xml.etree import ElementTree as ET


class ExtractionStopped(Exception):
    pass


class ExtractionError(Exception):
    pass


@dataclass
class TextBlock:
    location: str
    text: str


@dataclass
class ExtractedContent:
    parser: str
    blocks: List[TextBlock] = field(default_factory=list)
    status: str = "complete"
    issues: List[str] = field(default_factory=list)
    _characters: int = field(default=0, repr=False)

    def partial(self, message):
        self.status = "partial"
        if message not in self.issues:
            self.issues.append(message)

    def add(self, location, text):
        if len(self.blocks) >= 200000 or self._characters + len(text) > 20_000_000:
            self.partial("提取内容超过 20 万文本区域或 2000 万字符限制，剩余内容未检查。")
            return False
        self.blocks.append(TextBlock(location, text))
        self._characters += len(text)
        return True


def _check(checkpoint):
    if checkpoint is not None and not checkpoint():
        raise ExtractionStopped()


class _Archive:
    def __init__(self, path, limit):
        self.zip = zipfile.ZipFile(path)
        self.limit = limit
        entries = self.zip.infolist()
        if len(entries) > 30000:
            self.zip.close()
            raise ExtractionError("压缩包文件条目过多，已停止解压。")
        if sum(item.file_size for item in entries) > limit:
            self.zip.close()
            raise ExtractionError("文档解压后超过大小限制，未检查内容。")
        self.names = set(self.zip.namelist())

    def xml(self, name):
        info = self.zip.getinfo(name)
        limit = min(self.limit, 64 * 1024 * 1024)
        if info.file_size > limit:
            raise ExtractionError("文档内单个 XML 部件超过读取限制。")
        with self.zip.open(info) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ExtractionError("文档内 XML 数据超过读取限制。")
        declarations = raw.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
            raise ExtractionError("文档包含不支持的 XML 实体定义。")
        return ET.fromstring(raw)

    def close(self):
        self.zip.close()


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _paragraph_text(element):
    # Nested text boxes have their own paragraph and must not be counted twice.
    parts = []
    def visit(node, first=False):
        if not first and _tag(node) == "p":
            return
        if _tag(node) == "t" and node.text:
            parts.append(node.text)
        elif _tag(node) in ("tab", "br", "cr"):
            parts.append(" " if _tag(node) == "tab" else "\n")
        for child in node:
            visit(child)
    visit(element, True)
    return "".join(parts)


def _extract_docx(archive, result, checkpoint):
    if "word/document.xml" not in archive.names:
        raise ExtractionError("文件缺少 Word 正文部件。")
    parts = [("word/document.xml", "正文")]
    for name in sorted(archive.names):
        if re.fullmatch(r"word/(header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name):
            label = "页眉" if "/header" in name else "页脚" if "/footer" in name else "脚注/尾注/批注"
            parts.append((name, label))
    for name, label in parts:
        _check(checkpoint)
        root = archive.xml(name)
        for index, paragraph in enumerate((node for node in root.iter() if _tag(node) == "p"), 1):
            _check(checkpoint)
            text = _paragraph_text(paragraph)
            if text:
                if not result.add("{} · 段落 {}".format(label, index), text):
                    return


def _numbered_part(name):
    found = re.search(r"(\d+)\.xml$", name)
    return int(found.group(1)) if found else 0


def _extract_pptx(archive, result, checkpoint):
    slides = sorted((name for name in archive.names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=_numbered_part)
    if "ppt/presentation.xml" not in archive.names:
        raise ExtractionError("文件缺少 PowerPoint 演示文稿部件。")
    # presentation.xml relationship order is the user-visible slide order.
    rels = {}
    if "ppt/_rels/presentation.xml.rels" in archive.names:
        for rel in archive.xml("ppt/_rels/presentation.xml.rels"):
            rels[rel.get("Id")] = rel.get("Target", "")
    ordered = []
    for item in archive.xml("ppt/presentation.xml").iter():
        if _tag(item) == "sldId":
            rid = item.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rels.get(rid, "")
            path = "ppt/" + target if not target.startswith("/") else target.lstrip("/")
            path = path.replace("\\", "/")
            if path in archive.names:
                ordered.append(path)
    if ordered:
        slides = ordered
    for index, name in enumerate(slides, 1):
        _check(checkpoint)
        root = archive.xml(name)
        for pindex, paragraph in enumerate((node for node in root.iter() if _tag(node) == "p"), 1):
            text = _paragraph_text(paragraph)
            if text:
                if not result.add("第 {} 张幻灯片 · 段落 {}".format(index, pindex), text):
                    return
        # Notes parts are resolved through the corresponding slide relationship.
        rel_name = "ppt/slides/_rels/" + Path(name).name + ".rels"
        if rel_name in archive.names:
            import posixpath
            for rel in archive.xml(rel_name):
                if rel.get("Type", "").endswith("/notesSlide"):
                    target = rel.get("Target", "")
                    notes_name = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("ppt/slides", target))
                    if notes_name in archive.names:
                        for paragraph in archive.xml(notes_name).iter():
                            if _tag(paragraph) == "p":
                                text = _paragraph_text(paragraph)
                                if text:
                                    if not result.add("第 {} 张幻灯片 · 备注".format(index), text):
                                        return
    for name in sorted(archive.names):
        if re.fullmatch(r"ppt/(slideMasters/slideMaster\d+|slideLayouts/slideLayout\d+|diagrams/data\d+)\.xml", name):
            _check(checkpoint)
            label = "幻灯片母版" if "/slideMasters/" in name else "幻灯片版式" if "/slideLayouts/" in name else "SmartArt 数据"
            for paragraph in archive.xml(name).iter():
                if _tag(paragraph) == "p":
                    text = _paragraph_text(paragraph)
                    if text and not result.add(label + " · " + Path(name).name, text):
                        return
    if any(name.startswith("ppt/charts/") and name.endswith(".xml") for name in archive.names):
        result.partial("图表对象和嵌入工作簿未展开检查；已检查幻灯片与备注文本。")


def _extract_xlsx(archive, result, checkpoint):
    import posixpath
    if "xl/workbook.xml" not in archive.names:
        raise ExtractionError("文件缺少 Excel 工作簿部件。")
    shared = []
    if "xl/sharedStrings.xml" in archive.names:
        for node in archive.xml("xl/sharedStrings.xml"):
            if _tag(node) == "si":
                shared.append("".join(child.text or "" for child in node.iter() if _tag(child) == "t"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in archive.names:
        for rel in archive.xml("xl/_rels/workbook.xml.rels"):
            target = rel.get("Target", "")
            rels[rel.get("Id")] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
    workbook = archive.xml("xl/workbook.xml")
    for sheet in workbook.iter():
        if _tag(sheet) != "sheet":
            continue
        _check(checkpoint)
        label = sheet.get("name", "工作表")
        if not result.add("工作表名称", label):
            return
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        sheet_path = rels.get(rid)
        if not sheet_path or sheet_path not in archive.names:
            result.partial("无法解析工作表 {} 的内容部件。".format(label))
            continue
        root = archive.xml(sheet_path)
        for node in root.iter():
            if _tag(node) in ("oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"):
                text = "".join(node.itertext())
                if text and not result.add(label + " · 页眉/页脚", text):
                    return
        for cell in root.iter():
            if _tag(cell) != "c":
                continue
            _check(checkpoint)
            values = [child.text or "" for child in cell if _tag(child) == "v"]
            formulas = [child.text or "" for child in cell if _tag(child) == "f"]
            if cell.get("t") == "s" and values:
                try:
                    content = shared[int(values[0])]
                except (ValueError, IndexError):
                    result.partial("部分单元格的共享字符串索引无效。")
                    content = ""
            elif cell.get("t") == "inlineStr":
                content = "".join(child.text or "" for child in cell.iter() if _tag(child) == "t")
            else:
                content = " ".join(values)
            location = "{}!{}".format(label, cell.get("r", "?"))
            if content:
                if not result.add(location, content):
                    return
            if formulas:
                if not result.add(location + " · 公式", " ".join(formulas)):
                    return
    for name in sorted(archive.names):
        if re.fullmatch(r"xl/comments\d+\.xml", name):
            for node in archive.xml(name).iter():
                if _tag(node) == "comment":
                    text = "".join(child.text or "" for child in node.iter() if _tag(child) == "t")
                    if text:
                        if not result.add("批注 · " + node.get("ref", "?"), text):
                            return
    if any(name.startswith("xl/drawings/") or name.startswith("xl/charts/") for name in archive.names):
        result.partial("工作簿中的绘图、图表及其文字未展开检查。")


def _extract_office(path, checkpoint, max_uncompressed_bytes):
    suffix = Path(path).suffix.lower()
    result = ExtractedContent(suffix[1:] + "_xml")
    try:
        archive = _Archive(path, max_uncompressed_bytes)
    except zipfile.BadZipFile:
        raise ExtractionError("文档不是可读取的 Office XML 文件；可能已加密、损坏或后缀不符。")
    try:
        fn = {".docx": _extract_docx, ".xlsx": _extract_xlsx, ".pptx": _extract_pptx}[suffix]
        try:
            fn(archive, result, checkpoint)
        except ExtractionStopped:
            raise
        except Exception as exc:
            if not result.blocks:
                raise
            result.partial("部分文档部件读取失败：" + str(exc)[:200])
        if any("/media/" in name for name in archive.names):
            result.partial("文档包含图片/媒体，未进行图片文字识别（OCR）。")
        if any("/embeddings/" in name or "/activeX/" in name for name in archive.names):
            result.partial("文档包含嵌入对象或控件，未检查其内部内容。")
        if any(name.endswith("vbaProject.bin") for name in archive.names):
            result.partial("宏代码未展开检查。")
        if any(name.startswith("word/charts/") or name.startswith("word/altChunk") for name in archive.names):
            result.partial("图表与外部导入内容部件未展开检查。")
        return result
    finally:
        archive.close()


def _extract_text(path, checkpoint, max_bytes):
    _check(checkpoint)
    with open(path, "rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ExtractionError("文本文件超过读取大小限制。")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ("utf-16",)
    elif b"\x00" in raw[:1024]:
        # Office-style UTF-16 exports without BOM are common.
        sample = raw[:1024]
        even = sample[::2].count(0)
        odd = sample[1::2].count(0)
        encodings = ("utf-16-le", "utf-16-be") if odd >= even else ("utf-16-be", "utf-16-le")
    else:
        encodings = ("utf-8-sig", "gb18030")
    text = None
    selected_encoding = None
    for encoding in encodings:
        try:
            text = raw.decode(encoding, errors="strict")
            selected_encoding = encoding
            break
        except UnicodeError:
            continue
    if text is None and len(raw) % 2 == 0:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                candidate = raw.decode(encoding, errors="strict")
            except UnicodeError:
                continue
            if candidate and sum(character.isprintable() or character in "\r\n\t" for character in candidate) / len(candidate) > 0.95:
                text = candidate
                selected_encoding = "ambiguous"
                break
    if text is None:
        raise ExtractionError("无法按 UTF-8、GB18030 或 UTF-16 解码此文本文件。")
    result = ExtractedContent("text")
    for line_number, line in enumerate(text.splitlines(), 1):
        _check(checkpoint)
        if line:
            if not result.add("第 {} 行".format(line_number), line):
                break
    # BOM-less UTF-16 Chinese may also be valid GB18030 bytes. Do not silently
    # report a clean result when there is no reliable way to choose the encoding.
    if selected_encoding in ("gb18030", "ambiguous") and len(raw) % 2 == 0 and raw:
        alternatives = []
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                candidate = raw.decode(encoding, errors="strict")
            except UnicodeError:
                continue
            printable = sum(character.isprintable() or character in "\r\n\t" for character in candidate)
            cjk = sum("\u3400" <= character <= "\u9fff" for character in candidate)
            if candidate and printable / len(candidate) > 0.95 and cjk / len(candidate) > 0.45:
                alternatives.append((encoding, candidate))
        if alternatives:
            result.partial("无 BOM 文本编码存在歧义，已检查可用的 GB18030/UTF-16 解码候选；命中次数含不同解码候选，请复核原文。")
            for encoding, candidate in alternatives:
                if candidate == text:
                    continue
                for number, line in enumerate(candidate.splitlines(), 1):
                    _check(checkpoint)
                    if line and not result.add("{} 候选 · 第 {} 行".format(encoding, number), line):
                        return result
    return result


def _extract_pdf_direct(path, checkpoint):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ExtractionError("未安装 PDF 解析组件 pypdf，无法检查 PDF 内容。")
    result = ExtractedContent("pypdf")
    with open(path, "rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = False
            if not unlocked:
                raise ExtractionError("PDF 已加密，需要密码；未检查内容。")
        chars = 0
        for index, page in enumerate(reader.pages, 1):
            _check(checkpoint)
            try:
                text = page.extract_text() or ""
                chars += len(text)
                if chars > 20_000_000:
                    result.partial("PDF 提取文字超过 2000 万字符，后续页面未检查。")
                    break
                if text:
                    if not result.add("第 {} 页".format(index), text):
                        break
                else:
                    result.partial("PDF 有页面未提取到文字，可能为扫描件；未进行 OCR。")
                resources = page.get("/Resources")
                if resources:
                    resources = resources.get_object()
                    xobjects = resources.get("/XObject")
                    if xobjects:
                        result.partial("PDF 包含图像或图形对象，未进行 OCR 或对象内部检查。")
                if page.get("/Annots"):
                    result.partial("PDF 批注/表单控件内容未展开检查。")
            except ExtractionStopped:
                raise
            except Exception as exc:
                result.partial("第 {} 页文字读取失败：{}".format(index, str(exc)[:160]))
        root = reader.trailer.get("/Root")
        if root and root.get_object().get("/Names"):
            result.partial("PDF 的附件和命名对象未展开检查。")
    return result


def _pdf_worker(path, connection):
    try:
        connection.send(("result", _extract_pdf_direct(path, None)))
    except Exception as exc:
        connection.send(("error", str(exc)[:1000] or type(exc).__name__))
    finally:
        connection.close()


def _extract_pdf(path, checkpoint):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_worker, args=(str(path), sender), daemon=True)
    deadline = time.monotonic() + 30
    try:
        process.start()
        sender.close()
        while True:
            _check(checkpoint)
            if receiver.poll(0.05):
                try:
                    kind, payload = receiver.recv()
                except EOFError:
                    raise ExtractionError("PDF 读取进程意外退出。")
                if kind == "result":
                    return payload
                raise ExtractionError(payload)
            if time.monotonic() >= deadline:
                raise ExtractionError("PDF 内容解析超过 30 秒，已停止当前文件读取。")
            if not process.is_alive() and not receiver.poll():
                raise ExtractionError("PDF 读取进程意外退出。")
    finally:
        if process.pid:
            process.join(0.2)
            if process.is_alive():
                process.terminate()
                process.join(0.5)
        receiver.close()
        sender.close()


def _open_office_process(pid, can_terminate=False):
    import win32api
    import win32process
    handle = win32api.OpenProcess(0x1000 | (1 if can_terminate else 0), False, pid)
    try:
        identity = win32process.GetProcessTimes(handle)["CreationTime"].isoformat()
        return handle, identity
    except Exception:
        handle.Close()
        raise


def _legacy_worker(path, connection):
    """Office is isolated so password dialogs and busy Office cannot block the UI."""
    app = None
    document = None
    owns_app = False
    try:
        import pythoncom
        import win32com.client
        import win32process
        pythoncom.CoInitialize()
        suffix = Path(path).suffix.lower()
        progid = {".doc": "Word.Application", ".xls": "Excel.Application", ".ppt": "PowerPoint.Application"}[suffix]
        prior_pids = set(win32process.EnumProcesses())
        app = win32com.client.DispatchEx(progid)
        try:
            hwnd = app.Hwnd if suffix != ".ppt" else app.HWND
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if pid in prior_pids:
                raise ExtractionError("Office 复用了已有进程，无法安全隔离；请转换为现代 Office 格式。")
            handle, identity = _open_office_process(pid)
            handle.Close()
            owns_app = True
            connection.send(("office_pid", (pid, identity)))
        except Exception as exc:
            raise ExtractionError("无法确认新建的独立 Office 进程：" + str(exc))
        try:
            app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        except Exception:
            raise ExtractionError("无法禁用 Office 宏，已取消旧格式读取。")
        blocks = []
        dummy_password = "sensitive-checker-no-password-90c267"
        if suffix == ".doc":
            app.Visible = False
            app.DisplayAlerts = 0
            document = app.Documents.Open(FileName=path, ConfirmConversions=False, ReadOnly=True,
                AddToRecentFiles=False, PasswordDocument=dummy_password, WritePasswordDocument=dummy_password)
            for index in range(1, document.Paragraphs.Count + 1):
                blocks.append(("正文 · 段落 {}".format(index), str(document.Paragraphs(index).Range.Text)))
        elif suffix == ".xls":
            app.Visible = False
            app.DisplayAlerts = False
            app.EnableEvents = False
            app.AskToUpdateLinks = False
            document = app.Workbooks.Open(Filename=path, UpdateLinks=0, ReadOnly=True,
                Password=dummy_password, WriteResPassword=dummy_password, IgnoreReadOnlyRecommended=True,
                Notify=False, AddToMru=False)
            for sheet in document.Worksheets:
                used = sheet.UsedRange
                if used.CountLarge > 1_000_000:
                    raise ExtractionError("旧格式工作表区域超过 100 万单元格限制。")
                values = used.Value2
                if not isinstance(values, tuple):
                    values = ((values,),)
                for row_offset, row in enumerate(values):
                    for column_offset, value in enumerate(row):
                        if value is not None:
                            location = "{}!R{}C{}".format(sheet.Name, used.Row + row_offset, used.Column + column_offset)
                            blocks.append((location, str(value)))
        else:
            app.DisplayAlerts = 1
            document = app.Presentations.Open(FileName=path, ReadOnly=True, Untitled=False, WithWindow=False)
            for slide in document.Slides:
                for shape in slide.Shapes:
                    if shape.HasTextFrame and shape.TextFrame.HasText:
                        blocks.append(("第 {} 张幻灯片".format(slide.SlideIndex), str(shape.TextFrame.TextRange.Text)))
        connection.send(("result", blocks))
    except Exception as exc:
        connection.send(("error", str(exc)[:500]))
    finally:
        # The parent keeps the process bounded even if Close/Quit blocks.
        try:
            if document is not None:
                document.Close(False) if Path(path).suffix.lower() != ".ppt" else document.Close()
        except Exception:
            pass
        try:
            if app is not None and owns_app:
                app.Quit()
        except Exception:
            pass
        connection.close()


def _extract_legacy(path, checkpoint, timeout):
    if os.name != "nt":
        raise ExtractionError("旧格式 Office 内容读取仅适用于装有 Microsoft Office 的 Windows。")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_legacy_worker, args=(str(path), sender), daemon=True)
    office_handle = None
    result = None
    deadline = time.monotonic() + timeout
    try:
        process.start()
        sender.close()
        while time.monotonic() < deadline:
            _check(checkpoint)
            if receiver.poll(0.05):
                kind, payload = receiver.recv()
                if kind == "office_pid":
                    try:
                        handle, identity = _open_office_process(payload[0], True)
                        if identity == payload[1]:
                            office_handle = handle
                        else:
                            handle.Close()
                    except Exception:
                        pass
                elif kind == "error":
                    raise ExtractionError("旧格式 Office 读取失败：" + payload)
                elif kind == "result":
                    result = ExtractedContent("office_com", [TextBlock(*item) for item in payload], "partial",
                        ["旧格式兼容读取：仅检查主要正文/单元格/幻灯片文字；图片、备注、页眉、嵌入对象等内容未完整覆盖。"])
                    break
            if not process.is_alive() and not receiver.poll():
                break
        if result is None:
            raise ExtractionError("旧格式 Office 读取超时或进程退出；文件可能需要密码或 Office 不可用。")
        return result
    finally:
        if process.pid:
            process.join(0.3)
            if process.is_alive():
                process.terminate()
                process.join(0.5)
            # The retained handle identifies exactly the newly created process;
            # PID reuse cannot target an unrelated Office instance.
            if office_handle is not None:
                try:
                    import win32api
                    win32api.TerminateProcess(office_handle, 1)
                except Exception:
                    pass
                finally:
                    office_handle.Close()
        receiver.close()
        sender.close()


def extract_file(path, checkpoint=None, max_file_bytes=100 * 1024 * 1024,
                 max_uncompressed_bytes=200 * 1024 * 1024,
                 use_legacy_office=False, legacy_timeout_seconds=20):
    suffix = Path(path).suffix.lower()
    _check(checkpoint)
    if os.path.getsize(path) > max_file_bytes:
        raise ExtractionError("文件超过 {} MB 大小限制，未检查内容。".format(max_file_bytes // (1024 * 1024)))
    if suffix in (".docx", ".xlsx", ".pptx"):
        return _extract_office(path, checkpoint, max_uncompressed_bytes)
    if suffix in (".txt", ".csv", ".md", ".log", ".tsv", ".json", ".xml"):
        return _extract_text(path, checkpoint, max_file_bytes)
    if suffix == ".pdf":
        return _extract_pdf(path, checkpoint)
    if suffix in (".doc", ".xls", ".ppt"):
        if not use_legacy_office:
            raise ExtractionError("旧格式 Office 内容读取未启用；请转换为 .docx/.xlsx/.pptx，或启用已安装 Office 的兼容读取。")
        return _extract_legacy(path, checkpoint, legacy_timeout_seconds)
    raise ExtractionError("当前版本不支持读取此文件类型的内容。")
