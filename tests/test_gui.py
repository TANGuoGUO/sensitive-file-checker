"""Qt integration tests use only artificial files inside this workspace."""
import os
from pathlib import Path
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

import app as gui
from scanner import FileResult, Match, ScanConfig, ScanControl, ScanSummary


@pytest.fixture(scope="session")
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


def pump_until(application, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            application.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError("Qt operation did not complete within timeout")


@pytest.fixture
def window(qapp):
    widget = gui.MainWindow()
    yield widget
    if widget.control:
        widget.control.stop()
    if widget.worker:
        assert widget.worker.wait(10000), "Worker failed to stop during cleanup"
    qapp.processEvents()
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


@pytest.fixture
def artificial_folder():
    # Avoid pytest's default user-level temp directory and all real user files.
    with tempfile.TemporaryDirectory(prefix="gui-fixture-", dir=Path(__file__).parent) as name:
        yield Path(name)


def wait_finished(window, qapp):
    pump_until(qapp, lambda: window.worker is not None and not window.worker.isRunning())
    # QThread.finished is queued to the GUI thread after run() returns.
    pump_until(qapp, lambda: window.start_btn.isEnabled())


def test_opening_does_not_select_user_directories_or_scan(window, qapp):
    qapp.processEvents()
    assert window.roots.count() == 0
    assert window.worker is None
    assert window.active_config is None
    assert window.results == []
    assert window.summary.state == "idle"
    assert window.table.rowCount() == 0
    assert not window.export_btn.isEnabled()


def test_demo_tabs_filter_and_detail_selection(window, qapp):
    window.load_demo()
    assert window.worker is None
    assert window.table.rowCount() == 4
    assert window.current_record is not None
    assert window.location_combo.count() == 2
    window.location_combo.setCurrentIndex(1)
    assert "仅限内部" in window.snippet.toPlainText()
    assert not window.folder_btn.isEnabled()

    window.search.setText("产品路线图")
    pump_until(qapp, lambda: window.table.rowCount() == 1)
    assert window.current_record.path.endswith("产品路线图.pptx")
    window.search.setText("no-artificial-record-can-match-this")
    pump_until(qapp, lambda: window.table.rowCount() == 0)
    assert window.current_record is None
    assert not window.copy_btn.isEnabled()

    window.search.clear()
    window.tabs.setCurrentIndex(1)
    assert window.table.rowCount() == 3
    window.search.setText("加密")
    pump_until(qapp, lambda: window.table.rowCount() == 1)
    assert window.current_record.content_status == "unreadable"
    assert "加密" in window.snippet.toPlainText()


def test_detail_escapes_html_in_snippet_keyword_and_issue(window):
    keyword = "<b>秘密</b>"
    snippet = 'literal <img src="missing.png"> ' + keyword + " & tail"
    issue = '<a href="https://invalid.example">issue</a>'
    record = FileResult(path="artificial.txt", matched=True,
                        matched_keywords=(keyword,),
                        matches=[Match(keyword, "content", "第 1 行", snippet)],
                        content_status="partial", issues=[issue], total_matches=1)
    window.active_config = ScanConfig(("unused-artificial-root",), (keyword,))
    window.results = [record]
    window.summary = ScanSummary(state="completed", processed=1, matched=1)
    window.render_results()
    plain = window.snippet.toPlainText()
    rendered = window.snippet.toHtml()
    assert snippet in plain
    assert issue in plain
    assert "<img " not in rendered
    assert '<a href="https://invalid.example"' not in rendered
    assert "background-color:#ffe2a0" in rendered


def test_qthread_scan_shows_matches_and_unreadable_files(window, qapp, artificial_folder):
    root = artificial_folder
    (root / "人工测试.txt").write_text("仅供自动化测试：内部资料，等待复核。", encoding="utf-8")
    (root / "ordinary.txt").write_text("A harmless artificial document.", encoding="utf-8")
    for extension in ("docx", "xlsx", "pptx"):
        (root / ("damaged." + extension)).write_bytes(b"intentionally damaged office fixture")
    # A real password-encrypted PDF complements the corrupt Office fixtures.
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("fixture-password")
    with (root / "encrypted.pdf").open("wb") as stream:
        writer.write(stream)

    window.add_root(str(root))
    window.keywords.setPlainText("内部资料")
    window.start_scan()
    wait_finished(window, qapp)
    assert window.summary.state == "completed"
    assert window.summary.processed == 6
    assert window.summary.matched == 1
    assert window.summary.content_unreadable == 4
    assert window.table.rowCount() == 1
    assert "内部资料" in window.snippet.toPlainText()
    assert "有遗漏" in window.status_title.text()
    assert window.export_btn.isEnabled()
    assert window.settings_frame.isEnabled()
    window.tabs.setCurrentIndex(1)
    assert window.table.rowCount() == 4
    assert all(record.content_status == "unreadable"
               for record in window.results if Path(record.path).suffix != ".txt")


@pytest.mark.parametrize("finish_action", ["resume", "stop"])
def test_ui_pause_can_resume_or_stop_real_worker(window, qapp, artificial_folder,
                                                monkeypatch, finish_action):
    (artificial_folder / "artificial.txt").write_text("内部资料", encoding="utf-8")
    # Begin at a real scanner checkpoint to avoid race-prone timing assumptions.
    prepaused_control = ScanControl()
    prepaused_control.pause()
    monkeypatch.setattr(gui, "ScanControl", lambda: prepaused_control)
    window.add_root(str(artificial_folder))
    window.keywords.setPlainText("内部资料")
    window.start_scan()
    window.toggle_pause()
    pump_until(qapp, lambda: window.status_title.text() == "已暂停")
    assert window.worker.isRunning()
    assert window.summary.processed == 0
    assert window.pause_btn.text() == "继续"
    if finish_action == "resume":
        window.toggle_pause()
    else:
        window.stop_scan()
    wait_finished(window, qapp)
    assert window.summary.state == ("completed" if finish_action == "resume" else "stopped")
    assert window.summary.processed == (1 if finish_action == "resume" else 0)
    assert window.start_btn.isEnabled()
    if finish_action == "stop":
        assert "结果不完整" in window.status_title.text()
