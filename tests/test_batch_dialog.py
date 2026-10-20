import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import batch_dialog
from batch_dialog import BatchSelectionDialog
from scanner import FileResult, ScanConfig, ScanSummary


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialog(qt_app, tmp_path):
    records = [FileResult(str(tmp_path / name), matched=True, matched_keywords=(keyword,), size=200)
               for name, keyword in (("secret.docx", "内部资料"), ("budget.xlsx", "预算"), ("roadmap.pptx", "内部资料"), ("report.pdf", "评估"))]
    window = BatchSelectionDialog(records, ScanConfig((str(tmp_path),), ("内部资料",)), ScanSummary())
    window.show()
    qt_app.processEvents()
    yield window
    window.close()
    qt_app.processEvents()


def click_row(dialog, row, column=1, modifiers=Qt.KeyboardModifier.NoModifier):
    point = dialog.table.visualItemRect(dialog.table.item(row, column)).center()
    QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, modifiers, point)


def test_real_checks_tri_state_and_one_toggle_per_checkbox_click(dialog):
    assert not dialog.export_button.isEnabled() and not dialog.cut_button.isEnabled()
    click_row(dialog, 0, 0)
    assert len(dialog.selected_paths()) == 1
    assert dialog.select_all.checkState() == Qt.CheckState.PartiallyChecked
    QTest.mouseClick(dialog.select_all, Qt.MouseButton.LeftButton)
    assert len(dialog.selected_paths()) == 4
    assert dialog.select_all.checkState() == Qt.CheckState.Checked
    QTest.mouseClick(dialog.select_all, Qt.MouseButton.LeftButton)
    assert not dialog.selected_paths()


def test_selection_survives_filters_and_hidden_count(dialog):
    click_row(dialog, 0)
    dialog.type_combo.setCurrentIndex(2)
    assert dialog.table.rowCount() == 1 and len(dialog.selected_paths()) == 1
    assert "隐藏 1" in dialog.selection_label.text()
    dialog.select_visible()
    dialog.type_combo.setCurrentIndex(0)
    dialog.search.setText("内部资料")
    assert dialog.table.rowCount() == 2 and len(dialog.selected_paths()) == 2
    dialog.clear_selection()
    assert not dialog.selected_paths()


def test_shift_click_selects_visible_range(dialog):
    click_row(dialog, 0)
    click_row(dialog, 2, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert len(dialog.selected_paths()) == 3
    assert dialog.table.item(1, 0).checkState() == Qt.CheckState.Checked


def test_shortcuts_only_apply_to_table_not_search(dialog, monkeypatch):
    calls = []
    monkeypatch.setattr(dialog, "cut_selected", lambda: calls.append("cut"))
    dialog.search.setFocus()
    dialog.search.setText("secret")
    QTest.keyClick(dialog.search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert dialog.search.selectedText() == "secret" and not dialog.selected_paths()
    QTest.keyClick(dialog.search, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert dialog.search.text() == "" and not calls
    dialog.table.setFocus()
    QTest.keyClick(dialog.table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(dialog.selected_paths()) == 4
    QTest.keyClick(dialog.table, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert calls == ["cut"]


def test_invalid_file_prevents_entire_clipboard_operation(dialog, monkeypatch, tmp_path):
    existing = dialog.records[0].path
    with open(existing, "w", encoding="utf-8") as stream:
        stream.write("人工文件")
    calls = []
    monkeypatch.setattr(batch_dialog, "cut_files_to_clipboard", lambda paths: calls.append(paths))
    dialog.select_visible()
    dialog.cut_selected()
    assert not calls and "剪贴板未修改" in dialog.message_label.text()


def test_initial_selection_restore_and_demo_preview(qt_app, tmp_path):
    record = FileResult(str(tmp_path / "fake.docx"), matched=True)
    dialog = BatchSelectionDialog([record], None, ScanSummary(), demo=True, initially_selected=(record.path,))
    assert dialog.selected_records() == [record]
    assert dialog.export_button.isEnabled() and not dialog.cut_button.isEnabled()
    dialog.reject()
    assert dialog.selected_paths() == [record.path]
    dialog.close()


def test_report_destination_is_validated_before_export(dialog, monkeypatch, tmp_path):
    import reports
    dialog.select_visible()
    calls = []
    destination = str(tmp_path / "selected.csv")
    monkeypatch.setattr(batch_dialog.QFileDialog, "getSaveFileName", lambda *args: (destination, "CSV (*.csv)"))
    def reject_source(path, protected_paths):
        calls.append((path, protected_paths))
        raise ValueError("不能覆盖被检查的原文件")
    monkeypatch.setattr(reports, "validate_report_destination", reject_source, raising=False)
    monkeypatch.setattr(batch_dialog, "export_selected_report", lambda *args: pytest.fail("Export ran before validation"))
    dialog.export_selected()
    assert calls[0][0] == destination
    assert set(calls[0][1]) == set(record.path for record in dialog.records)
    assert "不能覆盖" in dialog.message_label.text()


def test_extra_task_paths_are_protected_outside_popup(qt_app, tmp_path):
    record = FileResult(str(tmp_path / "visible.docx"))
    other = str(tmp_path / "other-tab.csv")
    dialog = BatchSelectionDialog([record], None, ScanSummary(), protected_paths=(other,))
    assert set(dialog.protected_paths) == {record.path, other}
    dialog.close()
