"""Batch UI integration using artificial workspace files and a fake clipboard."""
import csv
import os
from pathlib import Path
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

import app as gui
import batch_dialog
from scanner import FileResult, Match, ScanConfig, ScanControl, ScanSummary


@pytest.fixture(scope="module")
def batch_application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def batch_window(batch_application):
    window = gui.MainWindow()
    yield window
    if window.control:
        window.control.stop()
    if window.worker:
        assert window.worker.wait(10000)
    batch_application.processEvents()
    for dialog in window.findChildren(QDialog):
        dialog.close()
        dialog.deleteLater()
    window.close()
    window.deleteLater()
    batch_application.processEvents()


@pytest.fixture
def batch_folder():
    with tempfile.TemporaryDirectory(prefix="batch-fixture-", dir=Path(__file__).parent) as folder:
        yield Path(folder)


def _wait(application, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            application.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError("Batch GUI operation timed out")


def _populate(window, directory):
    records = []
    for name, matched, status in (("first.txt", True, "complete"),
                                  ("second.txt", True, "partial"),
                                  ("ordinary.txt", False, "complete"),
                                  ("unreadable.txt", False, "unreadable")):
        path = directory / name
        path.write_text("人工集成测试：内部资料。", encoding="utf-8")
        records.append(FileResult(
            path=str(path), size=path.stat().st_size, matched=matched,
            matched_keywords=("内部资料",) if matched else (),
            matches=[Match("内部资料", "content", "第 1 行", "内部资料")] if matched else [],
            content_status=status, total_matches=int(matched),
            issues=["人工测试的内容检查遗漏"] if status in ("partial", "unreadable") else [],
        ))
    window.add_root(str(directory))
    window.active_config = ScanConfig((str(directory),), ("内部资料",))
    window.results = records
    window.summary = ScanSummary(state="completed", discovered=4, processed=4,
                                 matched=2, content_complete=2, content_partial=1,
                                 content_unreadable=1, total=4)
    window.handle_event({"type": "finished", "summary": window.summary})
    window.worker_completed()
    return records


def _choose(dialog, path):
    for row in range(dialog.table.rowCount()):
        record = dialog.table.item(row, 1).data(Qt.ItemDataRole.UserRole)
        if record.path == str(path):
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            return
    raise AssertionError("Artificial fixture was not present in batch dialog")


def test_batch_entry_disabled_without_results_and_opens_after_scan(batch_window, batch_folder):
    assert not batch_window.batch_button.isEnabled()
    assert batch_window.create_batch_dialog() is None
    records = _populate(batch_window, batch_folder)
    assert batch_window.batch_button.isEnabled()
    dialog = batch_window.create_batch_dialog()
    assert isinstance(dialog, batch_dialog.BatchSelectionDialog)
    assert dialog.table.rowCount() == 2
    assert {record.path for record in batch_window.batch_records()} == {records[0].path, records[1].path}


def test_batch_scope_uses_current_tab_records_ignoring_main_filter_and_warning_rows(
        batch_window, batch_folder):
    records = _populate(batch_window, batch_folder)
    batch_window.search.setText("nothing-in-this-artificial-fixture")
    batch_window.render_results()
    assert batch_window.table.rowCount() == 0
    assert batch_window.batch_button.isEnabled()
    assert len(batch_window.batch_records()) == 2
    dialog = batch_window.create_batch_dialog()
    assert dialog.search.text() == batch_window.search.text()
    dialog.search.clear()
    dialog.select_visible(True)
    assert set(dialog.selected_paths()) == {records[0].path, records[1].path}

    batch_window.search.clear()
    batch_window.warnings = [{"path": str(batch_folder / "directory-error"), "message": "人工目录错误"}]
    batch_window.tabs.setCurrentIndex(1)
    assert {record.path for record in batch_window.batch_records()} == {records[1].path, records[3].path}
    batch_window.results = []
    batch_window.render_results()
    assert batch_window.table.rowCount() == 1  # Warning rows are not batch files.
    assert not batch_window.batch_button.isEnabled()
    assert batch_window.create_batch_dialog() is None


def test_closing_and_reopening_preserves_selection_per_tab(batch_window, batch_folder,
                                                          monkeypatch):
    records = _populate(batch_window, batch_folder)
    opened = []

    def execute(dialog):
        opened.append(dialog)
        if len(opened) == 1:
            dialog.clear_selection()
            _choose(dialog, records[0].path)
        elif len(opened) == 2:
            assert set(dialog.selected_paths()) == {records[0].path}
            # Main-window filtering can initially hide an already selected file.
            assert dialog.table.rowCount() == 1
            assert dialog.table.item(0, 1).data(Qt.ItemDataRole.UserRole).path == records[1].path
            dialog.search.setText("no-visible-artificial-record")
            assert dialog.table.rowCount() == 0
            assert set(dialog.selected_paths()) == {records[0].path}
            dialog.search.clear()
            assert set(dialog.selected_paths()) == {records[0].path}
        elif len(opened) == 3:
            assert not dialog.selected_paths()
            _choose(dialog, records[3].path)
        elif len(opened) == 4:
            assert set(dialog.selected_paths()) == {records[0].path}
        else:
            assert set(dialog.selected_paths()) == {records[3].path}
        # Closing the dialog through its window controls has the Rejected result.
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(batch_dialog.BatchSelectionDialog, "exec", execute)
    batch_window.batch_button.click()
    batch_window.search.setText("second")
    batch_window.open_batch_dialog()
    batch_window.search.clear()
    batch_window.tabs.setCurrentIndex(1)
    batch_window.open_batch_dialog()
    batch_window.tabs.setCurrentIndex(0)
    batch_window.open_batch_dialog()
    batch_window.tabs.setCurrentIndex(1)
    batch_window.open_batch_dialog()
    assert len(opened) == 5
    assert set(batch_window.batch_selection_by_tab[0]) == {records[0].path}
    assert set(batch_window.batch_selection_by_tab[1]) == {records[3].path}


def test_new_scan_clears_previous_choices_and_disables_batch_while_running(
        batch_window, batch_application, batch_folder, monkeypatch):
    records = _populate(batch_window, batch_folder)
    batch_window.batch_selection_by_tab = {0: [records[0].path], 1: [records[3].path]}
    paused_control = ScanControl()
    paused_control.pause()
    monkeypatch.setattr(gui, "ScanControl", lambda: paused_control)
    batch_window.start_scan()
    assert batch_window.batch_selection_by_tab == {0: [], 1: []}
    assert not batch_window.batch_button.isEnabled()
    assert batch_window.create_batch_dialog() is None
    paused_control.resume()
    _wait(batch_application, lambda: not batch_window.worker.isRunning())
    _wait(batch_application, lambda: batch_window.start_btn.isEnabled())
    assert batch_window.summary.state == "completed"
    assert batch_window.batch_button.isEnabled()


def test_demo_resets_choices_and_never_enables_cut(batch_window, batch_folder):
    records = _populate(batch_window, batch_folder)
    batch_window.batch_selection_by_tab = {0: [records[0].path], 1: [records[3].path]}
    batch_window.load_demo()
    assert batch_window.batch_selection_by_tab == {0: [], 1: []}
    assert batch_window.batch_button.isEnabled()
    dialog = batch_window.create_batch_dialog()
    dialog.select_visible(True)
    assert dialog.selected_records()
    assert not dialog.cut_button.isEnabled()


def test_batch_export_contains_only_selected_files_and_leaves_sources_intact(
        batch_window, batch_folder, monkeypatch):
    records = _populate(batch_window, batch_folder)
    originals = {Path(record.path): Path(record.path).read_bytes() for record in records}
    dialog = batch_window.create_batch_dialog()
    dialog.clear_selection()
    _choose(dialog, records[1].path)
    destination = batch_folder / "selected.csv"
    monkeypatch.setattr(batch_dialog.QFileDialog, "getSaveFileName",
                        lambda *args, **kwargs: (str(destination), "CSV (*.csv)"))
    dialog.export_selected()
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    file_rows = [row for row in rows if row["记录类型"] == "FILE"]
    assert [row["文件路径"] for row in file_rows] == [records[1].path]
    for path, content in originals.items():
        assert path.is_file() and path.read_bytes() == content
    assert set(batch_folder.iterdir()) == set(originals) | {destination}


def test_cut_action_passes_only_selection_to_fake_clipboard_without_moving_files(
        batch_window, batch_folder, monkeypatch):
    records = _populate(batch_window, batch_folder)
    originals = {Path(record.path): Path(record.path).read_bytes() for record in records}
    dialog = batch_window.create_batch_dialog()
    dialog.clear_selection()
    _choose(dialog, records[0].path)
    copied_paths = []

    def fake_cut(paths):
        copied_paths.extend(paths)
        return len(paths)

    monkeypatch.setattr(batch_dialog, "cut_files_to_clipboard", fake_cut)
    dialog.cut_selected()
    assert copied_paths == [records[0].path]
    for path, content in originals.items():
        assert path.is_file() and path.read_bytes() == content
    assert set(batch_folder.iterdir()) == set(originals)
