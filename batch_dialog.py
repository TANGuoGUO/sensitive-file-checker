"""Select scan records for reporting or the Windows file clipboard."""
import os
from pathlib import Path
import time

from PySide6.QtCore import QEvent, QPointF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)


def cut_files_to_clipboard(paths):
    from file_clipboard import cut_files_to_clipboard as cut
    return cut(paths)


def export_selected_report(path, config, summary, selected_results, warnings, total_results_count):
    from reports import export_selected_report as export
    return export(path, config, summary, selected_results, warnings, total_results_count)


def _path_key(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _size_text(size):
    if size >= 1024 * 1024:
        return "{:.1f} MB".format(size / (1024 * 1024))
    if size >= 1024:
        return "{:.0f} KB".format(size / 1024)
    return "{} B".format(size)


class _SelectAllBox(QCheckBox):
    def nextCheckState(self):
        # Partial is a display state; a click always completes the selection.
        self.setCheckState(Qt.CheckState.Unchecked if self.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)


class _TypeCombo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#536b8b"), 1.6))
        x, y = self.width() - 17, self.height() / 2
        painter.drawLine(QPointF(x - 4, y - 2), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 4, y - 2))


class BatchSelectionDialog(QDialog):
    TYPE_FILTERS = (
        ("全部类型", ()), ("Word 文档", (".doc", ".docx")),
        ("Excel 表格", (".xls", ".xlsx")), ("PowerPoint", (".ppt", ".pptx")),
        ("PDF 文档", (".pdf",)), ("文本文件", (".txt", ".csv", ".tsv", ".md", ".log", ".json", ".xml")),
    )

    def __init__(self, records, config, summary, warnings=(), demo=False,
                 initially_selected=(), file_delegate_cls=None, parent=None, *, protected_paths=()):
        super().__init__(parent)
        self.setObjectName("BatchDialog")
        self.setWindowTitle("批量选择文件")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.resize(1030, 720)
        self.setMinimumSize(900, 600)
        self.config, self.summary = config, summary
        self.warnings = tuple(warnings)
        self.demo = bool(demo)
        self.records = []
        keys = set()
        for record in records:
            key = _path_key(record.path)
            if key not in keys:
                keys.add(key)
                self.records.append(record)
        self._selected_keys = {_path_key(path) for path in initially_selected} & keys
        self.protected_paths = tuple(dict.fromkeys([str(path) for path in protected_paths] + [str(record.path) for record in self.records]))
        self._visible_records = []
        self._anchor_key = None
        self._updating = False
        self._build(file_delegate_cls)
        self.render_records()

    def _build(self, delegate_cls):
        self.setStyleSheet("""
        QDialog#BatchDialog { background: white; border: 1px solid #dbe4f1; border-radius: 8px; }
        QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #183456; }
        QLabel#batch_title { font-size: 24px; font-weight: 700; color: #10264b; }
        QLabel#selection_label { font-size: 19px; font-weight: 700; color: #10264b; }
        QLabel#batch_subtitle { color: #476283; font-size: 14px; }
        QLabel#message_label, QLabel#batch_hint { color: #7b8ca5; }
        QLineEdit#search, QComboBox#type_combo { border: 1px solid #cfdaec; border-radius: 5px; padding: 10px 12px; background: white; }
        QLineEdit#search:focus { border-color: #1263eb; }
        QComboBox#type_combo::drop-down { border: none; width: 25px; }
        QComboBox#type_combo::down-arrow { image: none; }
        QTableWidget#table { background: white; border: 1px solid #d6e0ee; border-radius: 5px; gridline-color: #e4eaf3; selection-background-color: #e7f1ff; selection-color: #183456; }
        QTableWidget#table::item { padding: 8px; border: none; border-bottom: 1px solid #e4eaf3; }
        QHeaderView::section { color: #526b8f; background: #f4f7fd; border: none; border-bottom: 1px solid #dae3f1; padding: 11px 8px; }
        QPushButton { background: white; border: 1px solid #ccd9ee; border-radius: 5px; padding: 12px 18px; }
        QPushButton:hover { background: #f2f7ff; }
        QPushButton:disabled { color: #a1aec2; border-color: #dce4f0; background: #f6f8fc; }
        QPushButton#clear_selection { color: #1263eb; border: none; padding: 4px 10px; }
        QPushButton#export_button { color: #1263eb; border-color: #1263eb; }
        QPushButton#cut_button { color: white; background: #1263eb; border-color: #1263eb; font-weight: 600; }
        QPushButton#cut_button:hover { background: #0757d5; }
        QPushButton#cut_button:disabled { background: #a9c5ef; border-color: #a9c5ef; color: white; }
        QToolButton#batch_close { border: none; background: transparent; color: #7184a0; font-size: 24px; padding: 0; }
        QCheckBox { spacing: 9px; }
        QCheckBox::indicator { width: 18px; height: 18px; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 23, 28, 23)
        outer.setSpacing(15)
        heading = QHBoxLayout()
        title = QLabel("批量选择文件")
        title.setObjectName("batch_title")
        heading.addWidget(title)
        heading.addStretch()
        close = QToolButton()
        close.setObjectName("batch_close")
        close.setText("×")
        close.setAccessibleName("关闭批量选择")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.reject)
        heading.addWidget(close)
        outer.addLayout(heading)
        subtitle = QLabel("勾选本次检查结果，统一导出报告或剪切文件")
        subtitle.setObjectName("batch_subtitle")
        outer.addWidget(subtitle)
        filters = QHBoxLayout()
        filters.setSpacing(14)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("搜索文件名、路径或关键词")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(42)
        self.type_combo = _TypeCombo()
        self.type_combo.setObjectName("type_combo")
        self.type_combo.setMinimumWidth(146)
        self.type_combo.setMinimumHeight(42)
        for title, extensions in self.TYPE_FILTERS:
            self.type_combo.addItem(title, extensions)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.type_combo)
        outer.addLayout(filters)
        selection_controls = QHBoxLayout()
        self.select_all = _SelectAllBox("全选当前列表")
        self.select_all.setObjectName("select_all")
        self.select_all.setTristate(True)
        selection_controls.addWidget(self.select_all)
        self.clear_button = QPushButton("清空选择")
        self.clear_button.setObjectName("clear_selection")
        self.clear_button.setAutoDefault(False)
        self.clear_button.clicked.connect(self.clear_selection)
        selection_controls.addWidget(self.clear_button)
        selection_controls.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName("count_label")
        selection_controls.addWidget(self.count_label)
        outer.addLayout(selection_controls)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("table")
        self.table.setHorizontalHeaderLabels(("", "文件名 / 路径", "命中关键词", "大小", "状态"))
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(66)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 46)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 88)
        self.table.setColumnWidth(4, 112)
        if delegate_cls is not None:
            self.table.setItemDelegateForColumn(1, delegate_cls(self.table))
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table.itemChanged.connect(self._item_changed)
        outer.addWidget(self.table, 1)
        hint = QLabel("支持逐项勾选、Shift 连选和 Ctrl+A 全选；快捷键仅在列表中生效")
        hint.setObjectName("batch_hint")
        outer.addWidget(hint)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #e0e7f2;")
        outer.addWidget(divider)
        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer_text = QVBoxLayout()
        self.selection_label = QLabel()
        self.selection_label.setObjectName("selection_label")
        footer_text.addWidget(self.selection_label)
        self.message_label = QLabel("示例文件仅供预览，剪切不可用" if self.demo else "剪切后，在目标文件夹按 Ctrl+V 粘贴")
        self.message_label.setObjectName("message_label")
        self.message_label.setWordWrap(True)
        footer_text.addWidget(self.message_label)
        footer.addLayout(footer_text, 1)
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.export_button = QPushButton("导出所选报告")
        self.export_button.setObjectName("export_button")
        self.export_button.setAutoDefault(False)
        self.export_button.clicked.connect(self.export_selected)
        footer.addWidget(self.export_button)
        self.cut_button = QPushButton("剪切所选文件  Ctrl+X")
        self.cut_button.setObjectName("cut_button")
        self.cut_button.setAutoDefault(False)
        self.cut_button.clicked.connect(self.cut_selected)
        footer.addWidget(self.cut_button)
        outer.addLayout(footer)
        self.search.textChanged.connect(self.render_records)
        self.type_combo.currentIndexChanged.connect(self.render_records)
        self.select_all.stateChanged.connect(lambda state: self.select_visible(Qt.CheckState(state) == Qt.CheckState.Checked))

    def selected_paths(self):
        return [record.path for record in self.records if _path_key(record.path) in self._selected_keys]

    def selected_records(self):
        return [record for record in self.records if _path_key(record.path) in self._selected_keys]

    def render_records(self, *_args):
        query = self.search.text().strip().casefold()
        extensions = self.type_combo.currentData() or ()
        self._visible_records = [record for record in self.records
            if (not extensions or Path(record.path).suffix.lower() in extensions)
            and (not query or query in (str(record.path) + " " + " ".join(record.matched_keywords)).casefold())]
        self._updating = True
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._visible_records))
        for row, record in enumerate(self._visible_records):
            checked = _path_key(record.path) in self._selected_keys
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            filename = QTableWidgetItem(Path(record.path).name + "\n" + str(Path(record.path).parent))
            filename.setData(Qt.ItemDataRole.UserRole, record)
            filename.setToolTip(str(record.path))
            self.table.setItem(row, 1, filename)
            keywords = QTableWidgetItem("、".join(record.matched_keywords) or "—")
            keywords.setToolTip("、".join(record.matched_keywords))
            self.table.setItem(row, 2, keywords)
            self.table.setItem(row, 3, QTableWidgetItem(_size_text(record.size)))
            if record.matched:
                status = {"partial": "命中·有遗漏", "unreadable": "命中·未检查"}.get(record.content_status, "待复核")
            else:
                status = {"partial": "内容有遗漏", "unreadable": "内容未检查", "not_requested": "仅文件名", "complete": "未命中"}.get(record.content_status, "待复核")
            state = QTableWidgetItem("")
            state.setData(Qt.ItemDataRole.AccessibleTextRole, status)
            state.setForeground(QColor("#a86b12" if record.matched else "#72849e"))
            state.setToolTip("\n".join(record.issues))
            self.table.setItem(row, 4, state)
            status_container = QWidget()
            status_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            status_layout = QHBoxLayout(status_container)
            status_layout.setContentsMargins(4, 0, 4, 0)
            badge = QLabel(status)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedHeight(28)
            badge.setToolTip(state.toolTip())
            badge.setStyleSheet("background: {}; color: {}; border-radius: 13px; padding: 3px 10px;".format("#fff0d1" if record.matched else "#eff3f8", "#a86b12" if record.matched else "#72849e"))
            status_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 4, status_container)
            self.table.setRowHeight(row, 66)
            self._paint_row(row, checked)
        self._updating = False
        self._refresh_selection()

    def _paint_row(self, row, checked):
        color = QColor("#e7f1ff" if checked else "#ffffff")
        for column in range(5):
            item = self.table.item(row, column)
            if item is not None:
                item.setBackground(color)
                item.setSelected(checked)

    def _refresh_selection(self):
        visible = {_path_key(record.path) for record in self._visible_records}
        count = len(visible & self._selected_keys)
        total = len(self._selected_keys)
        hidden = total - count
        blocker = QSignalBlocker(self.select_all)
        self.select_all.setCheckState(Qt.CheckState.Unchecked if count == 0 else Qt.CheckState.Checked if count == len(visible) else Qt.CheckState.PartiallyChecked)
        del blocker
        self.select_all.setEnabled(bool(visible))
        self.count_label.setText("共 {} 个文件".format(len(visible)) + ("（全部 {} 个）".format(len(self.records)) if len(visible) != len(self.records) else ""))
        self.selection_label.setText("已选 {} 个文件".format(total) + (" · 隐藏 {} 个".format(hidden) if hidden else ""))
        self.clear_button.setEnabled(bool(total))
        self.export_button.setEnabled(bool(total))
        self.cut_button.setEnabled(bool(total) and not self.demo)
        self.cut_button.setToolTip("示例文件不能剪切" if self.demo else "将全部所选真实文件放入文件剪贴板；不会立即移动文件")

    def _sync_checks(self):
        self._updating = True
        for row, record in enumerate(self._visible_records):
            checked = _path_key(record.path) in self._selected_keys
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._paint_row(row, checked)
        self._updating = False
        self._refresh_selection()

    def select_visible(self, checked=True):
        visible = {_path_key(record.path) for record in self._visible_records}
        self._selected_keys.update(visible) if checked else self._selected_keys.difference_update(visible)
        self._sync_checks()

    def clear_selection(self):
        self._selected_keys.clear()
        self._sync_checks()

    def _item_changed(self, item):
        if self._updating or item.column() != 0:
            return
        key = _path_key(self._visible_records[item.row()].path)
        self._selected_keys.add(key) if item.checkState() == Qt.CheckState.Checked else self._selected_keys.discard(key)
        self._anchor_key = key
        self._sync_checks()

    def _toggle_row(self, row, shift=False):
        if row < 0 or row >= len(self._visible_records):
            return
        key = _path_key(self._visible_records[row].path)
        checked = key not in self._selected_keys
        visible_keys = [_path_key(record.path) for record in self._visible_records]
        if shift and self._anchor_key in visible_keys:
            start = visible_keys.index(self._anchor_key)
            targets = visible_keys[min(start, row):max(start, row) + 1]
        else:
            targets = [key]
            self._anchor_key = key
        self._selected_keys.update(targets) if checked else self._selected_keys.difference_update(targets)
        self._sync_checks()

    def eventFilter(self, watched, event):
        if watched is self.table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                index = self.table.indexAt(event.position().toPoint())
                if index.isValid():
                    self.table.setFocus(Qt.FocusReason.MouseFocusReason)
                    self.table.setCurrentCell(index.row(), index.column())
                    self._toggle_row(index.row(), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                return True
        if watched in (self.table, self.table.viewport()) and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            control = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            handled = control and event.key() in (Qt.Key.Key_A, Qt.Key.Key_X)
            if handled:
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    self.select_visible(True) if event.key() == Qt.Key.Key_A else self.cut_selected()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
                self._toggle_row(self.table.currentRow(), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
        return super().eventFilter(watched, event)

    def _message(self, text, error=False):
        self.message_label.setText(text)
        self.message_label.setStyleSheet("color: {};".format("#bd4141" if error else "#337258"))

    def cut_selected(self):
        paths = self.selected_paths()
        if not paths or self.demo:
            return
        try:
            missing = [path for path in paths if not Path(path).is_file()]
        except OSError as exc:
            self._message("无法验证所选文件，剪贴板未修改：" + str(exc), True)
            return
        if missing:
            self._message("无法剪切：{} 个所选文件不存在或无法访问。请清除失效项后重试，剪贴板未修改。".format(len(missing)), True)
            return
        try:
            count = cut_files_to_clipboard(paths)
        except Exception as exc:
            self._message("剪切失败：" + str(exc), True)
            return
        self._message("已将 {} 个文件放入剪贴板，请到目标文件夹按 Ctrl+V 粘贴".format(count))

    def export_selected(self):
        records = self.selected_records()
        if not records:
            return
        path, selected_filter = QFileDialog.getSaveFileName(self, "导出所选文件报告", "所选文件报告_{}.csv".format(time.strftime("%Y%m%d_%H%M%S")), "CSV 文件清单 (*.csv);;JSON 完整报告 (*.json)")
        if not path:
            return
        if Path(path).suffix.lower() not in (".csv", ".json"):
            path += ".json" if "json" in selected_filter.lower() else ".csv"
        try:
            from reports import validate_report_destination
            validate_report_destination(path, self.protected_paths)
            export_selected_report(path, self.config, self.summary, records, self.warnings, len(self.records))
        except Exception as exc:
            self._message("导出失败：" + str(exc), True)
            return
        self._message("已导出 {} 个所选文件的报告".format(len(records)))
