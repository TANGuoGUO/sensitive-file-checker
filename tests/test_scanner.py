import codecs
import threading
import time
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from scanner import ScanConfig, ScanControl, run_scan


def scan(root, keywords=("秘密",), **options):
    events = []
    summary = run_scan(ScanConfig((str(root),), keywords, **options), on_event=events.append)
    return summary, [event["result"] for event in events if event["type"] == "result"], events


def office(path, parts):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)


def test_any_all_across_name_and_content_and_true_counts(tmp_path):
    (tmp_path / "秘密.txt").write_text("Project ALPHA alpha Alpha", encoding="utf-8")
    summary, results, events = scan(tmp_path, ("秘密", "alpha"), keyword_mode="all", max_preview_matches=2)
    result = results[0]
    assert result.matched and result.matched_keywords == ("秘密", "alpha")
    assert result.total_matches == 4 and len(result.matches) == 2 and result.matches_truncated
    assert summary.matched == 1 and summary.content_complete == 1
    assert events[-1]["type"] == "finished"


def test_case_sensitive_and_content_only(tmp_path):
    (tmp_path / "秘密.txt").write_text("ALPHA", encoding="utf-8")
    _, result, _ = scan(tmp_path, ("alpha",), match_name=False, case_sensitive=True)
    assert not result[0].matched
    _, result, _ = scan(tmp_path, ("秘密",), match_content=False)
    assert result[0].matched and result[0].content_status == "not_requested"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030", "utf-16", "utf-16-le", "utf-16-be"])
def test_text_encodings(tmp_path, encoding):
    (tmp_path / "test.log").write_bytes("文件 Secret 秘密\n第二行".encode(encoding))
    _, results, _ = scan(tmp_path)
    assert results[0].matched and results[0].content_status == "complete"
    assert "第 1 行" in results[0].matches[0].location


def test_nonrecursive_exclusion_extensions_and_overlapping_roots(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "a.txt").write_text("秘密", encoding="utf-8")
    (nested / "b.txt").write_text("秘密", encoding="utf-8")
    (tmp_path / "a.bin").write_text("秘密", encoding="utf-8")
    summary, results, _ = scan(tmp_path, recursive=False, extensions=("txt",))
    assert summary.processed == 1
    summary, results, _ = scan(tmp_path, excluded_dirs=(str(nested),))
    assert summary.processed == 1 and summary.excluded == 1
    events = []
    summary = run_scan(ScanConfig((str(tmp_path), str(nested)), ("秘密",)), on_event=events.append)
    assert summary.processed == 2


def test_directory_name_exclusions_use_component_boundaries(tmp_path):
    for name in ("node_modules", "node_modules_extra", ".git"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "test.txt").write_text("秘密", encoding="utf-8")
    summary, results, _ = scan(tmp_path, excluded_dirs=("node_modules", ".git"))
    assert summary.processed == 1 and summary.excluded == 2
    assert "node_modules_extra" in results[0].path


def test_unreadable_legacy_keeps_filename_hit(tmp_path):
    (tmp_path / "秘密.doc").write_bytes(b"not a real document")
    summary, results, _ = scan(tmp_path)
    assert results[0].matched and results[0].content_status == "unreadable"
    assert summary.content_unreadable == 1


def test_docx_text_locations_media_and_embedded_coverage(tmp_path):
    office(tmp_path / "a.docx", {
        "word/document.xml": '<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>这是秘密</w:t></w:r></w:p></w:body></w:document>',
        "word/header1.xml": '<w:hdr xmlns:w="urn:word"><w:p><w:r><w:t>秘密页眉</w:t></w:r></w:p></w:hdr>',
        "word/media/image1.png": "fake image",
        "word/embeddings/oleObject1.bin": "fake embed",
    })
    summary, results, _ = scan(tmp_path)
    assert results[0].total_matches == 2 and results[0].content_status == "partial"
    assert len(results[0].issues) == 2 and summary.content_partial == 1
    assert any("页眉" in match.location for match in results[0].matches)


def test_xlsx_shared_inline_formula_and_sheet_location(tmp_path):
    office(tmp_path / "a.xlsx", {
        "xl/workbook.xml": '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="预算" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": '<sst><si><t>秘密</t></si></sst>',
        "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B2" t="inlineStr"><is><t>秘密</t></is></c><c r="C3"><f>IF(A1="秘密",1,0)</f><v>1</v></c></row></sheetData></worksheet>',
    })
    _, results, _ = scan(tmp_path)
    assert results[0].total_matches == 3
    assert {match.location for match in results[0].matches} == {"预算!A1", "预算!B2", "预算!C3 · 公式"}


def test_pptx_slide_order_and_notes(tmp_path):
    office(tmp_path / "a.pptx", {
        "ppt/presentation.xml": '<p:presentation xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="r2"/><p:sldId r:id="r1"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="r1" Target="slides/slide1.xml"/><Relationship Id="r2" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/slide1.xml": '<slide><p><t>普通</t></p></slide>',
        "ppt/slides/slide2.xml": '<slide><p><t>秘密</t></p></slide>',
        "ppt/slides/_rels/slide2.xml.rels": '<Relationships><Relationship Type="http://test/notesSlide" Target="../notesSlides/notesSlide9.xml"/></Relationships>',
        "ppt/notesSlides/notesSlide9.xml": '<notes><p><t>秘密备注</t></p></notes>',
    })
    _, results, _ = scan(tmp_path)
    assert results[0].total_matches == 2
    assert all("第 1 张" in match.location for match in results[0].matches)


def test_size_and_xml_entity_limits(tmp_path):
    office(tmp_path / "bad.docx", {"word/document.xml": '<!DOCTYPE a [<!ENTITY e "秘密">]><document>&e;</document>'})
    _, results, _ = scan(tmp_path)
    assert results[0].content_status == "unreadable" and "实体" in results[0].issues[0]
    (tmp_path / "big.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    _, results, _ = scan(tmp_path, max_file_mb=1)
    assert next(result for result in results if result.path.endswith("big.txt")).content_status == "unreadable"


def test_stop_retains_prior_results_and_summary_snapshot(tmp_path):
    for index in range(3):
        (tmp_path / (str(index) + ".txt")).write_text("秘密", encoding="utf-8")
    control = ScanControl()
    events = []
    def callback(event):
        events.append(event)
        if event["type"] == "result":
            control.stop()
    summary = run_scan(ScanConfig((str(tmp_path),), ("秘密",)), control, callback)
    assert summary.state == "stopped" and summary.processed == 1
    assert next(event for event in events if event["type"] == "progress")["summary"].processed == 0


def test_pause_resume_and_stop_unblocks_pause(tmp_path):
    (tmp_path / "test.txt").write_text("秘密", encoding="utf-8")
    control = ScanControl()
    control.pause()
    result = []
    thread = threading.Thread(target=lambda: result.append(run_scan(ScanConfig((str(tmp_path),), ("秘密",)), control)))
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive() and not result
    control.resume()
    thread.join(3)
    assert not thread.is_alive() and result[0].processed == 1
    control.pause()
    control.stop()
    assert not control.checkpoint()


def test_missing_root_and_invalid_configuration(tmp_path):
    summary, results, events = scan(tmp_path / "missing")
    assert summary.directory_errors == 1 and not results
    assert any(event["type"] == "warning" for event in events)
    with pytest.raises(ValueError):
        scan(tmp_path, (" ",))


def test_pdf_text_location_and_encrypted_document(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Confidential ALPHA) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(tmp_path / "plain.pdf", "wb") as output:
        writer.write(output)
    writer.encrypt("fixture-password")
    with open(tmp_path / "encrypted.pdf", "wb") as output:
        writer.write(output)
    summary, results, _ = scan(tmp_path, ("alpha",))
    plain = next(result for result in results if result.path.endswith("plain.pdf"))
    encrypted = next(result for result in results if result.path.endswith("encrypted.pdf"))
    assert plain.matched and plain.matches[0].location == "第 1 页"
    assert encrypted.content_status == "unreadable" and "加密" in encrypted.issues[0]
    assert summary.content_unreadable == 1


def test_corrupt_secondary_part_keeps_readable_content(tmp_path):
    office(tmp_path / "partial.docx", {
        "word/document.xml": '<document><p><t>秘密</t></p></document>',
        "word/header1.xml": 'not xml',
    })
    _, results, _ = scan(tmp_path)
    assert results[0].matched and results[0].content_status == "partial"


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_bomless_pure_chinese_utf16_is_not_silently_missed(tmp_path, encoding):
    (tmp_path / "chinese.txt").write_bytes("这是秘密资料".encode(encoding))
    _, results, _ = scan(tmp_path)
    assert results[0].matched
    assert results[0].content_status == "partial"
    assert "编码存在歧义" in results[0].issues[0]


def test_ppt_master_smartart_and_xlsx_headers_are_checked(tmp_path):
    office(tmp_path / "master.pptx", {
        "ppt/presentation.xml": '<presentation/>',
        "ppt/slideMasters/slideMaster1.xml": '<master><p><t>秘密母版</t></p></master>',
        "ppt/diagrams/data1.xml": '<diagram><p><t>秘密图示</t></p></diagram>',
    })
    office(tmp_path / "headers.xlsx", {
        "xl/workbook.xml": '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="预算" r:id="r1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet><headerFooter><oddHeader>秘密页眉</oddHeader></headerFooter></worksheet>',
    })
    _, results, _ = scan(tmp_path)
    assert all(result.matched for result in results)
    assert next(result for result in results if result.path.endswith(".pptx")).total_matches == 2
    assert "页眉/页脚" in next(result for result in results if result.path.endswith(".xlsx")).matches[0].location


def test_stat_error_preserves_name_match_and_marks_content_unreadable(tmp_path, monkeypatch):
    import scanner
    path = tmp_path / "秘密.txt"
    path.write_text("fixture", encoding="utf-8")
    original_stat = scanner.os.stat
    def denied_stat(target, *args, **kwargs):
        if str(target) == str(path):
            raise PermissionError("fixture access denied")
        return original_stat(target, *args, **kwargs)
    monkeypatch.setattr(scanner.os, "stat", denied_stat)
    summary, results, _ = scan(tmp_path)
    assert results[0].matched and results[0].content_status == "unreadable"
    assert summary.matched == 1 and summary.content_unreadable == 1


def test_office_lock_files_are_excluded(tmp_path):
    (tmp_path / "~$秘密.docx").write_bytes(b"synthetic lock")
    summary, results, _ = scan(tmp_path)
    assert summary.excluded == 1 and not results


def test_cloud_reparse_tags_are_not_treated_as_path_links(monkeypatch):
    import scanner
    import stat
    with monkeypatch.context() as patch:
        patch.setattr(scanner.os, "lstat", lambda path: SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400, st_reparse_tag=0x9000001A))
        assert not scanner._is_link("synthetic cloud file")
        patch.setattr(scanner.os, "lstat", lambda path: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003))
        assert scanner._is_link("synthetic junction")


def test_offline_cloud_content_is_not_opened(tmp_path, monkeypatch):
    import scanner
    path = tmp_path / "秘密.txt"
    path.write_text("synthetic content", encoding="utf-8")
    original_stat = scanner.os.stat
    def cloud_stat(target, *args, **kwargs):
        actual = original_stat(target, *args, **kwargs)
        if str(target) == str(path):
            return SimpleNamespace(st_mode=actual.st_mode, st_size=actual.st_size, st_mtime=actual.st_mtime,
                                   st_file_attributes=0x400000)
        return actual
    def never_read(*args, **kwargs):
        pytest.fail("Cloud placeholder content was read")
    monkeypatch.setattr(scanner.os, "stat", cloud_stat)
    monkeypatch.setattr(scanner, "extract_file", never_read)
    summary, results, _ = scan(tmp_path)
    assert results[0].matched and results[0].content_status == "unreadable"
    assert "云端占位文件" in results[0].issues[0]


def test_existing_office_process_is_not_modified_or_quit(tmp_path, monkeypatch):
    import extractors
    pythoncom = pytest.importorskip("pythoncom")
    win32process = pytest.importorskip("win32process")
    win32client = pytest.importorskip("win32com.client")
    class FakeApp:
        HWND = 123
        quit_called = False
        def Quit(self):
            self.quit_called = True
    class FakeConnection:
        messages = []
        def send(self, message):
            self.messages.append(message)
        def close(self):
            pass
    app = FakeApp()
    connection = FakeConnection()
    monkeypatch.setattr(pythoncom, "CoInitialize", lambda: None)
    monkeypatch.setattr(win32process, "EnumProcesses", lambda: [999])
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (0, 999))
    monkeypatch.setattr(win32client, "DispatchEx", lambda progid: app)
    extractors._legacy_worker(str(tmp_path / "synthetic.ppt"), connection)
    assert not app.quit_called and not hasattr(app, "AutomationSecurity")
    assert connection.messages[0][0] == "error" and "已有进程" in connection.messages[0][1]


def test_utf16_xml_entity_is_rejected(tmp_path):
    office(tmp_path / "entities.docx", {"word/document.xml": '<!DOCTYPE a [<!ENTITY e "秘密">]><document>&e;</document>'.encode("utf-16")})
    _, results, _ = scan(tmp_path)
    assert results[0].content_status == "unreadable" and "实体" in results[0].issues[0]


def test_deep_directory_error_is_reported_as_unchecked_range(tmp_path, monkeypatch):
    import scanner
    def too_deep(path):
        raise RecursionError("synthetic deep directory")
    monkeypatch.setattr(scanner.os, "scandir", too_deep)
    summary, results, events = scan(tmp_path)
    assert summary.state == "completed" and summary.directory_errors == 1
    assert any(event["type"] == "warning" and "目录嵌套过深" in event["message"] for event in events)
