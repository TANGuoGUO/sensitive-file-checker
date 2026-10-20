"""本地敏感资料检查工具。扫描只读，文件处理由用户明确选择。"""
from __future__ import annotations

import argparse
import html
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from dataclasses import asdict

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.is_dir():
    sys.path.insert(0, str(VENDOR_DIR))

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QProgressBar,
    QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QStyledItemDelegate, QStyle, QStyleFactory, QProxyStyle, QStyleOptionViewItem, QTableWidget,
    QTableWidgetItem, QTabBar, QTextBrowser, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from scanner import FileResult, Match, ScanConfig, ScanControl, ScanSummary, run_scan
from reports import export_report
from version import APP_VERSION


FILE_GROUPS = (
    ("Word 文档", ".docx  .doc", (".docx", ".doc"), "W", "#2462b5"),
    ("Excel 表格", ".xlsx  .xls", (".xlsx", ".xls"), "X", "#238657"),
    ("PowerPoint 演示文稿", ".pptx  .ppt", (".pptx", ".ppt"), "P", "#c55b38"),
    ("PDF 文档", ".pdf", (".pdf",), "PDF", "#cc4e50"),
    ("文本文件", ".txt  .csv  .md  .log", (".txt", ".csv", ".md", ".log"), "T", "#64748b"),
)
DEFAULT_EXCLUDES = (".git", ".venv", "node_modules", "__pycache__", "Windows", "$Recycle.Bin", "System Volume Information")
STATUS_LABELS = {"not_requested": "仅检查文件名", "complete": "可提取文字已检查", "partial": "内容检查有遗漏", "unreadable": "内容未检查"}

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #172b4d; }
QMainWindow, QWidget#App { background: #f4f7fb; }
QFrame#Header { background: #ffffff; border-bottom: 1px solid #dce4ef; }
QLabel#Title { font-size: 25px; font-weight: 700; color: #10264b; }
QLabel#Subtitle, QLabel#Muted { color: #697b94; }
QLabel#SectionTitle { font-size: 15px; font-weight: 700; color: #203655; }
QLabel#PanelTitle { font-size: 21px; font-weight: 700; color: #142d52; }
QLabel#StatusTitle { font-size: 17px; font-weight: 700; }
QLabel#Badge { color: #27724f; background: #e7f5ed; border: 1px solid #c6ead6; border-radius: 12px; padding: 4px 12px; }
QLabel#BadgeBlue { color: #2d64b6; background: #edf4ff; border: 1px solid #d1e3ff; border-radius: 12px; padding: 4px 12px; }
QFrame#Settings, QFrame#Results { background: #ffffff; border: 1px solid #dce4ef; border-radius: 8px; }
QFrame#SettingsInner { background: #f9fbfd; border: none; }
QFrame#StatusBox { background: #f5f8fc; border: 1px solid #e0e7f1; border-radius: 7px; }
QFrame#Divider { background: #e2e8f0; border: none; max-height: 1px; }
QFrame#DetailBox { background: #f8faff; border: 1px solid #dce5f1; border-radius: 7px; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #cbd6e5; border-radius: 5px; padding: 7px 12px; color: #213a5b; }
QPushButton:hover, QToolButton:hover { background: #f0f5fc; border-color: #9aafd0; }
QPushButton:disabled, QToolButton:disabled { color: #a2adba; background: #f6f8fb; border-color: #e0e6ee; }
QPushButton#Primary { background: #1263eb; color: #ffffff; border: 1px solid #1263eb; padding: 10px 22px; font-weight: 600; }
QPushButton#Primary:hover { background: #0758d6; }
QPushButton#Primary:disabled { background: #8aafea; border-color: #8aafea; }
QPushButton#Danger { color: #b64b4b; }
QPushButton#Link { border: none; background: transparent; color: #2366bd; padding: 2px 0; text-align: left; }
QToolButton#Disclosure { border: none; background: transparent; padding: 6px 0; text-align: left; }
QLineEdit, QTextEdit, QSpinBox, QComboBox { background: #ffffff; border: 1px solid #cbd6e5; border-radius: 5px; padding: 7px; selection-background-color: #dceaff; selection-color: #172b4d; }
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus { border: 1px solid #387de8; }
QListWidget { background: #ffffff; border: 1px solid #d3deed; border-radius: 5px; padding: 3px; }
QListWidget::item { padding: 5px 3px; }
QListWidget::item:selected { background: #e6f0ff; color: #173c6a; }
QCheckBox, QRadioButton { spacing: 7px; background: transparent; padding: 3px 0; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #f5f7fa; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #cbd5e1; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QTabBar::tab { background: transparent; border: none; border-bottom: 3px solid transparent; padding: 12px 16px; color: #64758d; font-weight: 600; }
QTabBar::tab:selected { color: #1263eb; border-bottom: 3px solid #1263eb; }
QTableWidget { background: #ffffff; border: 1px solid #dce5f0; border-radius: 5px; gridline-color: #e7edf5; selection-background-color: #e3efff; selection-color: #153c6b; }
QTableWidget::item { border: none; border-bottom: 1px solid #e7edf5; padding: 8px; }
QHeaderView::section { background: #f6f8fc; color: #465c7a; border: none; border-bottom: 1px solid #dce5f0; padding: 10px 8px; font-weight: 500; }
QTextBrowser { background: #ffffff; border: 1px solid #e1e8f2; border-radius: 5px; padding: 8px; }
QProgressBar { background: #e7eef9; border: none; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #2673e7; border-radius: 3px; }
QSplitter::handle { background: transparent; width: 8px; }
QToolTip { background: #ffffff; color: #18314f; border: 1px solid #ccd8e7; padding: 5px; }
"""


def label(text, name="", wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def line():
    widget = QFrame()
    widget.setObjectName("Divider")
    widget.setFixedHeight(1)
    return widget


def make_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#1263eb"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(6, 6, 52, 52, 12, 12)
    painter.setPen(QColor("white"))
    font = QFont("Segoe UI", 31, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "✓")
    painter.end()
    return QIcon(pixmap)


class FileDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        record = index.data(Qt.ItemDataRole.UserRole)
        if record is None:
            return
        ext = Path(record.path).suffix.lower()
        group = next((g for g in FILE_GROUPS if ext in g[2]), FILE_GROUPS[-1])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, y = option.rect.x() + 12, option.rect.y() + 16
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(group[4]))
        painter.drawRoundedRect(x, y, 29, 33, 4, 4)
        painter.setPen(QColor("white"))
        font = QFont(option.font)
        font.setPixelSize(10 if group[3] == "PDF" else 17)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(x, y, 29, 33, Qt.AlignmentFlag.AlignCenter, group[3])
        text_x = x + 41
        available = max(10, option.rect.right() - text_x - 10)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor("#183456"))
        name = painter.fontMetrics().elidedText(Path(record.path).name, Qt.TextElideMode.ElideMiddle, available)
        painter.drawText(text_x, y + 13, name)
        font.setBold(False)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#718098"))
        path = painter.fontMetrics().elidedText(record.path, Qt.TextElideMode.ElideMiddle, available)
        painter.drawText(text_x, y + 31, path)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(300, 66)


class ScanWorker(QThread):
    event = Signal(object)

    def __init__(self, config, control, parent=None):
        super().__init__(parent)
        self.config = config
        self.control = control

    def run(self):
        try:
            run_scan(self.config, self.control, self.event.emit)
        except Exception as exc:
            self.event.emit({"type": "fatal", "message": str(exc)})


class InspectorStyle(QProxyStyle):
    """Draw crisp, legible check controls on both Windows and offscreen Qt."""
    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))

    def drawPrimitive(self, element, option, painter, widget=None):
        checkbox = element == QStyle.PrimitiveElement.PE_IndicatorCheckBox
        radio = element == QStyle.PrimitiveElement.PE_IndicatorRadioButton
        if not checkbox and not radio:
            return super().drawPrimitive(element, option, painter, widget)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        mixed = checkbox and bool(option.state & QStyle.StateFlag.State_NoChange)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        color = QColor("#1769e8" if enabled else "#9db1cd")
        rect = option.rect.adjusted(1, 1, -1, -1)
        painter.setPen(QPen(color if checked or mixed else QColor("#8c9db4"), 1.3))
        painter.setBrush(color if (checked or mixed) and checkbox else QColor("#ffffff"))
        if checkbox:
            painter.drawRoundedRect(rect, 2, 2)
            if mixed:
                painter.setPen(QPen(QColor("#ffffff"), 1.8))
                painter.drawLine(rect.left() + 3, rect.center().y(), rect.right() - 3, rect.center().y())
            elif checked:
                painter.setPen(QPen(QColor("#ffffff"), 1.8))
                painter.drawLine(rect.left() + 3, rect.center().y(), rect.center().x() - 1, rect.bottom() - 3)
                painter.drawLine(rect.center().x() - 1, rect.bottom() - 3, rect.right() - 2, rect.top() + 3)
        else:
            painter.drawEllipse(rect)
            if checked:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(rect.adjusted(3, 3, -3, -3))
        painter.restore()


class MainWindow(QWidget):
    scan_completed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("App")
        self.setWindowTitle("敏感资料检查 v" + APP_VERSION)
        self.setWindowIcon(make_icon())
        self.resize(1380, 930)
        self.setMinimumSize(1060, 710)
        self.worker = None
        self.control = None
        self.paused = False
        self.results = []
        self.warnings = []
        self.summary = ScanSummary()
        self.active_config = None
        self.user_excludes = []
        self.current_matches = []
        self.current_record = None
        self.closing = False
        self.demo = False
        self.started = 0
        self._scan_in_progress = False
        self.batch_selection_by_tab = {0: [], 1: []}
        self._build()
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.render_results)
        self.search.textChanged.connect(lambda: self.filter_timer.start())
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.update_elapsed)
        self.update_rule_hint()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("Header")
        h = QHBoxLayout(header)
        h.setContentsMargins(25, 19, 25, 19)
        icon = label("▤")
        icon.setStyleSheet("font-size: 39px; color: #1c4d94; padding-right: 7px;")
        h.addWidget(icon)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        title_layout.addWidget(label("本机资料检查", "Title"))
        title_layout.addWidget(label("按文件名与内容查找指定关键词", "Subtitle"))
        h.addLayout(title_layout)
        h.addSpacing(15)
        h.addWidget(label("本地处理", "Badge"))
        h.addWidget(label("扫描只读", "BadgeBlue"))
        h.addStretch()
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.hide()
        h.addWidget(self.pause_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self.stop_scan)
        self.stop_btn.hide()
        h.addWidget(self.stop_btn)
        self.start_btn = QPushButton("开始检查")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.start_scan)
        h.addWidget(self.start_btn)
        outer.addWidget(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        split_wrap = QWidget()
        wrap_layout = QVBoxLayout(split_wrap)
        wrap_layout.setContentsMargins(10, 10, 10, 8)
        wrap_layout.addWidget(splitter)
        outer.addWidget(split_wrap, 1)
        self.settings_frame = QFrame()
        self.settings_frame.setObjectName("Settings")
        settings_layout = QVBoxLayout(self.settings_frame)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        self.settings_inner = QFrame()
        self.settings_inner.setObjectName("SettingsInner")
        s = QVBoxLayout(self.settings_inner)
        s.setContentsMargins(18, 17, 18, 16)
        s.setSpacing(10)
        s.addWidget(label("扫描范围", "SectionTitle"))
        self.roots = QListWidget()
        self.roots.setAccessibleName("待检查文件夹")
        self.roots.setMinimumHeight(70)
        self.roots.setMaximumHeight(112)
        self.roots.setToolTip("支持添加多个文件夹或磁盘根目录")
        s.addWidget(self.roots)
        folder_buttons = QHBoxLayout()
        add = QPushButton("＋ 添加文件夹")
        add.clicked.connect(self.add_folder)
        folder_buttons.addWidget(add)
        remove = QPushButton("移除")
        remove.clicked.connect(self.remove_folder)
        folder_buttons.addWidget(remove)
        folder_buttons.addStretch()
        s.addLayout(folder_buttons)
        self.recursive = QCheckBox("包含子文件夹")
        self.recursive.setChecked(True)
        s.addWidget(self.recursive)
        exclude = QToolButton()
        exclude.setObjectName("Disclosure")
        exclude.setText("排除目录")
        exclude.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        exclude.setArrowType(Qt.ArrowType.RightArrow)
        exclude.setCheckable(True)
        self.exclude_panel = QWidget()
        exc_layout = QVBoxLayout(self.exclude_panel)
        exc_layout.setContentsMargins(0, 0, 0, 0)
        self.skip_system = QCheckBox("跳过系统目录与开发缓存")
        self.skip_system.setChecked(True)
        self.skip_system.setToolTip("Windows、回收站、.git、.venv、node_modules、__pycache__ 等")
        exc_layout.addWidget(self.skip_system)
        self.exclude_info = label("未添加自定义排除目录", "Muted", True)
        exc_layout.addWidget(self.exclude_info)
        exc_actions = QHBoxLayout()
        add_exc = QPushButton("添加排除目录")
        add_exc.clicked.connect(self.add_exclude)
        clear_exc = QPushButton("清空")
        clear_exc.clicked.connect(self.clear_excludes)
        exc_actions.addWidget(add_exc)
        exc_actions.addWidget(clear_exc)
        exc_layout.addLayout(exc_actions)
        self.exclude_panel.hide()
        exclude.toggled.connect(lambda checked: (self.exclude_panel.setVisible(checked), exclude.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)))
        s.addWidget(exclude)
        s.addWidget(self.exclude_panel)
        s.addWidget(line())
        keyword_heading = QHBoxLayout()
        keyword_heading.addWidget(label("关键词规则", "SectionTitle"))
        keyword_heading.addStretch()
        load_rules = QPushButton("导入")
        load_rules.setObjectName("Link")
        load_rules.clicked.connect(self.load_rules)
        save_rules = QPushButton("保存")
        save_rules.setObjectName("Link")
        save_rules.clicked.connect(self.save_rules)
        keyword_heading.addWidget(load_rules)
        keyword_heading.addSpacing(9)
        keyword_heading.addWidget(save_rules)
        s.addLayout(keyword_heading)
        self.keywords = QTextEdit()
        self.keywords.setAccessibleName("每行一个关键词")
        self.keywords.setPlaceholderText("每行一个关键词，例如：\n内部资料\n商业秘密\n仅限内部")
        self.keywords.setPlainText("内部资料\n商业秘密\n仅限内部")
        self.keywords.setFixedHeight(98)
        s.addWidget(self.keywords)
        s.addWidget(label("每行一个关键词 · 按字面文本匹配", "Muted"))
        mode = QHBoxLayout()
        self.mode_any = QRadioButton("任一关键词")
        self.mode_all = QRadioButton("全部关键词")
        self.mode_any.setChecked(True)
        mode.addWidget(self.mode_any)
        mode.addWidget(self.mode_all)
        s.addLayout(mode)
        target = QHBoxLayout()
        self.match_name = QCheckBox("文件名")
        self.match_content = QCheckBox("文件内容")
        self.match_name.setChecked(True)
        self.match_content.setChecked(True)
        target.addWidget(self.match_name)
        target.addWidget(self.match_content)
        target.addStretch()
        s.addLayout(target)
        self.rule_hint = label("", "Muted", True)
        s.addWidget(self.rule_hint)
        for control in (self.mode_any, self.mode_all, self.match_name, self.match_content):
            control.toggled.connect(self.update_rule_hint)
        s.addWidget(line())
        s.addWidget(label("文件类型", "SectionTitle"))
        self.type_checks = []
        for title, extensions, values, letter, color in FILE_GROUPS:
            row = QHBoxLayout()
            check = QCheckBox(title)
            check.setChecked(True)
            self.type_checks.append((check, values))
            row.addWidget(check)
            row.addStretch()
            ext_label = label(extensions, "Muted")
            ext_label.setStyleSheet("font-size: 11px;")
            row.addWidget(ext_label)
            s.addLayout(row)
        s.addWidget(line())
        advanced = QToolButton()
        advanced.setObjectName("Disclosure")
        advanced.setText("高级设置")
        advanced.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced.setArrowType(Qt.ArrowType.RightArrow)
        advanced.setCheckable(True)
        self.advanced_panel = QWidget()
        adv_layout = QVBoxLayout(self.advanced_panel)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        self.case_sensitive = QCheckBox("区分英文字母大小写")
        self.legacy_office = QCheckBox("兼容旧版 Office 文件")
        self.legacy_office.setToolTip(".doc / .xls / .ppt 需要本机安装 Microsoft Office；禁用宏，以只读方式打开")
        adv_layout.addWidget(self.case_sensitive)
        adv_layout.addWidget(self.legacy_office)
        adv_layout.addWidget(label("旧版格式需已安装 Microsoft Office。\n图片文字与嵌入附件不做 OCR 或递归检查。", "Muted", True))
        max_row = QHBoxLayout()
        max_row.addWidget(label("单文件上限"))
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 1024)
        self.max_mb.setValue(100)
        self.max_mb.setSuffix(" MB")
        max_row.addWidget(self.max_mb)
        adv_layout.addLayout(max_row)
        self.advanced_panel.hide()
        advanced.toggled.connect(lambda checked: (self.advanced_panel.setVisible(checked), advanced.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)))
        s.addWidget(advanced)
        s.addWidget(self.advanced_panel)
        s.addStretch()
        settings_scroll.setWidget(self.settings_inner)
        settings_layout.addWidget(settings_scroll)
        self.settings_frame.setMinimumWidth(330)
        splitter.addWidget(self.settings_frame)

        result_frame = QFrame()
        result_frame.setObjectName("Results")
        r = QVBoxLayout(result_frame)
        r.setContentsMargins(20, 16, 20, 12)
        r.setSpacing(12)
        result_header = QHBoxLayout()
        result_header.addWidget(label("检查结果", "PanelTitle"))
        self.demo_label = label("示例数据", "BadgeBlue")
        self.demo_label.hide()
        result_header.addWidget(self.demo_label)
        result_header.addStretch()
        self.batch_button = QPushButton("批量处理")
        self.batch_button.setEnabled(False)
        self.batch_button.clicked.connect(self.open_batch_dialog)
        result_header.addWidget(self.batch_button)
        self.export_btn = QPushButton("导出报告")
        self.export_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_btn.clicked.connect(self.export_results)
        self.export_btn.setEnabled(False)
        result_header.addWidget(self.export_btn)
        r.addLayout(result_header)
        status_box = QFrame()
        status_box.setObjectName("StatusBox")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(15, 13, 15, 10)
        status_row = QHBoxLayout()
        self.status_icon = label("○")
        self.status_icon.setStyleSheet("font-size: 24px; color: #7090b4;")
        status_row.addWidget(self.status_icon)
        self.status_title = label("准备检查", "StatusTitle")
        status_row.addWidget(self.status_title)
        status_row.addStretch()
        self.stat_processed = label("已处理  0")
        self.stat_matches = label("命中  0")
        self.stat_issues = label("未检查 / 有遗漏  0")
        status_row.addWidget(self.stat_processed)
        status_row.addSpacing(18)
        status_row.addWidget(self.stat_matches)
        status_row.addSpacing(18)
        status_row.addWidget(self.stat_issues)
        status_layout.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        status_layout.addWidget(self.progress)
        self.current_file = label("添加文件夹，设置关键词后开始检查。", "Muted")
        self.current_file.setMinimumWidth(0)
        status_layout.addWidget(self.current_file)
        r.addWidget(status_box)
        self.issue_note = label("", "Muted", True)
        self.issue_note.hide()
        r.addWidget(self.issue_note)
        toolbar = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.addTab("命中文件  0")
        self.tabs.addTab("未检查 / 有遗漏  0")
        self.tabs.currentChanged.connect(self.render_results)
        toolbar.addWidget(self.tabs)
        toolbar.addStretch()
        self.search = QLineEdit()
        self.search.setAccessibleName("筛选检查结果")
        self.search.setPlaceholderText("筛选文件名、路径或关键词")
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(280)
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search)
        r.addLayout(toolbar)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("文件名 / 路径", "命中位置", "关键词 / 原因", "状态"))
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setItemDelegateForColumn(0, FileDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 128)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 120)
        self.table.setMinimumHeight(175)
        self.table.itemSelectionChanged.connect(self.show_selected)
        r.addWidget(self.table, 1)
        self.empty_label = label("尚无检查结果", "Muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r.addWidget(self.empty_label)
        detail_box = QFrame()
        detail_box.setObjectName("DetailBox")
        d = QVBoxLayout(detail_box)
        d.setContentsMargins(15, 12, 15, 13)
        d.setSpacing(8)
        detail_heading = QHBoxLayout()
        detail_heading.addWidget(label("命中详情", "SectionTitle"))
        detail_heading.addStretch()
        self.copy_btn = QPushButton("复制路径")
        self.copy_btn.clicked.connect(self.copy_path)
        self.folder_btn = QPushButton("打开所在文件夹")
        self.folder_btn.clicked.connect(self.open_folder)
        self.copy_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        detail_heading.addWidget(self.folder_btn)
        detail_heading.addWidget(self.copy_btn)
        d.addLayout(detail_heading)
        self.detail_name = label("选择文件，查看命中位置与原文片段", "Muted", True)
        self.detail_name.setTextFormat(Qt.TextFormat.PlainText)
        d.addWidget(self.detail_name)
        match_row = QHBoxLayout()
        self.location_combo = QComboBox()
        self.location_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.location_combo.setAccessibleName("选择命中片段")
        self.location_combo.currentIndexChanged.connect(self.show_snippet)
        match_row.addWidget(self.location_combo, 1)
        self.match_count = label("", "Muted")
        match_row.addWidget(self.match_count)
        d.addLayout(match_row)
        self.snippet = QTextBrowser()
        self.snippet.setOpenExternalLinks(False)
        self.snippet.setOpenLinks(False)
        self.snippet.setMinimumHeight(85)
        self.snippet.setMaximumHeight(105)
        self.snippet.setHtml('<span style="color:#718198">命中原文将在这里高亮显示。</span>')
        d.addWidget(self.snippet)
        r.addWidget(detail_box)
        footer = QHBoxLayout()
        footer.addWidget(label("提示：关键词命中需人工复核 · 图片文字与嵌入附件不在检查范围内", "Muted"))
        footer.addStretch()
        self.elapsed_label = label("本机离线运行", "Muted")
        footer.addWidget(self.elapsed_label)
        r.addLayout(footer)
        splitter.addWidget(result_frame)
        splitter.setSizes([345, 995])

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择要检查的文件夹或磁盘")
        if folder:
            self.add_root(folder)

    def add_root(self, folder):
        existing = {os.path.normcase(os.path.abspath(self.roots.item(i).text())) for i in range(self.roots.count())}
        if os.path.normcase(os.path.abspath(folder)) not in existing:
            self.roots.addItem(os.path.normpath(folder))
            self.roots.item(self.roots.count() - 1).setToolTip(folder)

    def remove_folder(self):
        for item in self.roots.selectedItems():
            self.roots.takeItem(self.roots.row(item))

    def add_exclude(self):
        folder = QFileDialog.getExistingDirectory(self, "选择要排除的目录")
        if folder and folder not in self.user_excludes:
            self.user_excludes.append(folder)
            self.exclude_info.setText("\n".join(self.user_excludes))

    def clear_excludes(self):
        self.user_excludes = []
        self.exclude_info.setText("未添加自定义排除目录")

    def update_rule_hint(self):
        sources = [text for check, text in ((self.match_name, "文件名"), (self.match_content, "内容")) if check.isChecked()]
        if not sources:
            self.rule_hint.setText("请至少选择一个检查位置。")
            return
        mode = "任意一个" if self.mode_any.isChecked() else "全部"
        self.rule_hint.setText("在%s中找到%s关键词即列出文件。" % ("或".join(sources), mode))

    def make_config(self):
        words = tuple(dict.fromkeys(x.strip() for x in self.keywords.toPlainText().splitlines() if x.strip()))
        roots = tuple(self.roots.item(i).text() for i in range(self.roots.count()))
        extensions = tuple(ext for check, values in self.type_checks if check.isChecked() for ext in values)
        if not roots:
            raise ValueError("请先添加至少一个要检查的文件夹。")
        if not words:
            raise ValueError("请输入至少一个关键词，每行一个。")
        if not self.match_name.isChecked() and not self.match_content.isChecked():
            raise ValueError("请至少勾选“文件名”或“文件内容”。")
        if not extensions:
            raise ValueError("请至少选择一种文件类型。")
        excludes = tuple(self.user_excludes) + (DEFAULT_EXCLUDES if self.skip_system.isChecked() else ())
        return ScanConfig(roots=roots, keywords=words, keyword_mode="any" if self.mode_any.isChecked() else "all", match_name=self.match_name.isChecked(), match_content=self.match_content.isChecked(), extensions=extensions, recursive=self.recursive.isChecked(), excluded_dirs=excludes, case_sensitive=self.case_sensitive.isChecked(), max_file_mb=self.max_mb.value(), use_legacy_office=self.legacy_office.isChecked())

    def start_scan(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            config = self.make_config()
        except ValueError as exc:
            QMessageBox.information(self, "检查设置未完成", str(exc))
            return
        self.demo = False
        self.demo_label.hide()
        self.results = []
        self.warnings = []
        self.summary = ScanSummary()
        self.active_config = config
        self.batch_selection_by_tab = {0: [], 1: []}
        self.table.setRowCount(0)
        self.search.clear()
        self.clear_detail()
        self.issue_note.hide()
        self.control = ScanControl()
        self.paused = False
        self.started = time.time()
        self.set_running(True)
        self.update_summary()
        self.worker = ScanWorker(config, self.control, self)
        self.worker.event.connect(self.handle_event)
        self.worker.finished.connect(self.worker_completed)
        self.worker.start()
        self.elapsed_timer.start()

    def set_running(self, running):
        self._scan_in_progress = running
        self.settings_frame.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.start_btn.setText("正在检查…" if running else "开始检查")
        self.pause_btn.setVisible(running)
        self.stop_btn.setVisible(running)
        self.pause_btn.setText("暂停")
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(not running and self.active_config is not None)
        self.update_batch_button()

    def toggle_pause(self):
        if not self.control:
            return
        self.paused = not self.paused
        if self.paused:
            self.control.pause()
            self.status_title.setText("已暂停")
            self.pause_btn.setText("继续")
        else:
            self.control.resume()
            self.status_title.setText("检查中")
            self.pause_btn.setText("暂停")

    def stop_scan(self):
        if self.control:
            self.control.stop()
            self.status_title.setText("正在停止…")
            self.current_file.setText("等待当前文件处理结束，已完成的结果会保留。")
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)

    def handle_event(self, event):
        kind = event.get("type")
        if kind == "state":
            state = event["state"]
            names = {"discovering": "发现文件中", "scanning": "检查中", "paused": "已暂停", "stopping": "正在停止…", "completed": "检查完成", "stopped": "检查已停止", "failed": "检查失败"}
            self.status_title.setText(names.get(state, state))
            if state == "discovering":
                self.progress.setRange(0, 0)
            self.status_icon.setText("◌" if state not in ("completed", "stopped", "failed") else "✓" if state == "completed" else "!")
        elif kind == "progress":
            self.summary = event["summary"]
            path = event.get("current_file", "")
            if path:
                self.current_file.setToolTip(path)
                metrics = self.current_file.fontMetrics()
                self.current_file.setText(metrics.elidedText(path, Qt.TextElideMode.ElideMiddle, max(100, self.current_file.width() - 6)))
            self.update_summary()
        elif kind == "result":
            record = event["result"]
            self.results.append(record)
            if self.accept_record(record):
                self.append_row(record)
            self.empty_label.setVisible(self.table.rowCount() == 0)
        elif kind == "warning":
            self.warnings.append(event)
            self.issue_note.setText("范围访问异常：%s（见未检查页与导出报告）" % event.get("message", "未知原因"))
            self.issue_note.show()
        elif kind == "finished":
            self.summary = event["summary"]
            self.update_summary()
            self.render_results()
            self.scan_completed.emit(self.summary)
        elif kind == "fatal":
            self.summary.state = "failed"
            self.warnings.append({"type": "warning", "message": event["message"], "path": ""})
            self.status_title.setText("检查失败")
            self.issue_note.setText(event["message"])
            self.issue_note.show()

    def worker_completed(self):
        self.elapsed_timer.stop()
        self.set_running(False)
        self.progress.setRange(0, 100)
        if self.summary.state == "completed":
            self.progress.setValue(100)
            incomplete = self.summary.content_partial + self.summary.content_unreadable + self.summary.directory_errors
            self.status_title.setText("检查完成 · 有遗漏" if incomplete else "检查完成")
            self.status_icon.setText("!" if incomplete else "✓")
            self.status_icon.setStyleSheet("font-size: 24px; color: %s;" % ("#b7791f" if incomplete else "#26835b"))
            self.current_file.setText("已完成选定范围；检查对象为可提取的文字内容。")
        elif self.summary.state == "stopped":
            self.status_title.setText("检查已停止 · 结果不完整")
            self.current_file.setText("仅保留已处理文件的结果；未处理文件尚未检查。")
        if self.closing:
            self.close()

    def update_elapsed(self):
        seconds = int(time.time() - self.started)
        self.elapsed_label.setText("用时 %02d:%02d" % (seconds // 60, seconds % 60))

    def update_summary(self):
        s = self.summary
        errors = s.content_partial + s.content_unreadable
        self.stat_processed.setText("已处理  %s" % format(s.processed, ","))
        self.stat_matches.setText("命中  %s" % format(s.matched, ","))
        self.stat_issues.setText("未检查 / 有遗漏  %s" % format(errors + s.directory_errors, ","))
        self.tabs.setTabText(0, "命中文件  %s" % s.matched)
        self.tabs.setTabText(1, "未检查 / 有遗漏  %s" % (errors + s.directory_errors))
        if s.total is not None:
            self.progress.setRange(0, max(1, s.total))
            self.progress.setValue(s.processed)
        else:
            self.progress.setRange(0, 0)

    def accept_record(self, record):
        wanted = record.matched if self.tabs.currentIndex() == 0 else record.content_status in ("partial", "unreadable")
        query = self.search.text().strip().casefold()
        return wanted and (not query or query in (record.path + " " + " ".join(record.matched_keywords) + " " + " ".join(record.issues)).casefold())

    def append_row(self, record):
        row = self.table.rowCount()
        self.table.insertRow(row)
        first = QTableWidgetItem(Path(record.path).name)
        first.setData(Qt.ItemDataRole.UserRole, record)
        first.setToolTip(record.path)
        self.table.setItem(row, 0, first)
        locations = list(dict.fromkeys(m.location for m in record.matches))
        location = locations[0] if locations else "—"
        if len(locations) > 1:
            location += " 等 %d 处" % len(locations)
        words = "、".join(record.matched_keywords) if record.matched_keywords else (record.issues[0] if record.issues else "—")
        status = "待复核" if record.matched else STATUS_LABELS.get(record.content_status, "未检查")
        if record.matched and record.content_status in ("partial", "unreadable"):
            status = "待复核 · 有遗漏"
        for col, text in ((1, location), (2, words), (3, status)):
            item = QTableWidgetItem(text)
            item.setToolTip(text if col != 2 else "\n".join(record.issues) + "\n" + words)
            if col == 3:
                item.setForeground(QColor("#9a6a24"))
            self.table.setItem(row, col, item)
        self.table.setRowHeight(row, 66)

    def render_results(self):
        if not hasattr(self, "table"):
            return
        selected_path = self.current_record.path if self.current_record else None
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        selected_row = None
        for record in self.results:
            if self.accept_record(record):
                if record.path == selected_path:
                    selected_row = self.table.rowCount()
                self.append_row(record)
        if self.tabs.currentIndex() == 1:
            query = self.search.text().strip().casefold()
            for warning in self.warnings:
                warning_text = warning.get("path", "") + " " + warning.get("message", "")
                if not query or query in warning_text.casefold():
                    record = FileResult(path=warning.get("path") or "范围访问异常", content_status="unreadable", issues=[warning.get("message", "未知错误")])
                    self.append_row(record)
        self.table.setUpdatesEnabled(True)
        self.empty_label.setVisible(self.table.rowCount() == 0)
        if self.summary.state in ("idle", "discovering", "scanning"):
            self.empty_label.setText("尚无检查结果" if self.summary.state == "idle" else "正在检查，结果会陆续出现…")
        elif self.summary.state == "stopped":
            self.empty_label.setText("当前没有符合筛选的结果；检查尚未完成。")
        else:
            self.empty_label.setText("当前没有符合筛选的命中。" if self.tabs.currentIndex() == 0 else "当前没有未检查或有遗漏的文件。")
        if self.table.rowCount():
            self.table.selectRow(selected_row if selected_row is not None else 0)
        else:
            self.clear_detail()
        self.update_batch_button()

    def batch_records(self):
        """Only actual file results belong in a file-operation selection."""
        if self.tabs.currentIndex() == 0:
            return [record for record in self.results if record.matched]
        return [record for record in self.results if record.content_status in ("partial", "unreadable")]

    def update_batch_button(self):
        self.batch_button.setEnabled(
            not self._scan_in_progress and self.active_config is not None and bool(self.batch_records())
        )

    def create_batch_dialog(self):
        if self._scan_in_progress or self.active_config is None:
            return None
        records = self.batch_records()
        if not records:
            return None
        from batch_dialog import BatchSelectionDialog
        dialog = BatchSelectionDialog(
            records, self.active_config, self.summary, self.warnings,
            demo=self.demo,
            initially_selected=self.batch_selection_by_tab.get(self.tabs.currentIndex(), ()),
            file_delegate_cls=FileDelegate, parent=self,
            protected_paths=[record.path for record in self.results],
        )
        dialog.search.setText(self.search.text())
        return dialog

    def open_batch_dialog(self):
        dialog = self.create_batch_dialog()
        if dialog is None:
            return
        tab_index = self.tabs.currentIndex()
        try:
            dialog.exec()
        finally:
            self.batch_selection_by_tab[tab_index] = dialog.selected_paths()
            dialog.deleteLater()

    def clear_detail(self):
        self.current_record = None
        self.current_matches = []
        self.location_combo.clear()
        self.detail_name.setText("选择文件，查看命中位置与原文片段")
        self.match_count.setText("")
        self.snippet.setHtml('<span style="color:#718198">命中原文将在这里高亮显示。</span>')
        self.copy_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)

    def show_selected(self):
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            return
        record = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.current_record = record
        self.current_matches = record.matches
        self.detail_name.setText(Path(record.path).name + "  ·  " + STATUS_LABELS.get(record.content_status, record.content_status))
        self.detail_name.setToolTip(record.path)
        self.copy_btn.setEnabled(True)
        self.folder_btn.setEnabled(not self.demo and Path(record.path).exists())
        self.location_combo.blockSignals(True)
        self.location_combo.clear()
        for m in record.matches:
            self.location_combo.addItem("%s  ·  %s  ·  %s" % ("文件名" if m.source == "name" else "内容", m.location, m.keyword))
        if not record.matches:
            self.location_combo.addItem("读取情况")
        self.location_combo.blockSignals(False)
        self.match_count.setText("%s 次命中%s" % (record.total_matches, " · 预览已截断" if record.matches_truncated else "") if record.matched else "")
        self.show_snippet(0)

    def show_snippet(self, index):
        record = self.current_record
        if record is None:
            return
        if 0 <= index < len(self.current_matches):
            text = self.current_matches[index].snippet
            words = tuple(self.active_config.keywords) if self.active_config else record.matched_keywords
            pattern = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True) if w)
            flags = 0 if self.active_config and self.active_config.case_sensitive else re.IGNORECASE
            pieces = []
            previous = 0
            for match in re.finditer(pattern, text, flags) if pattern else []:
                pieces.append(html.escape(text[previous:match.start()]))
                pieces.append('<span style="background:#ffe2a0;color:#533a0d;">%s</span>' % html.escape(match.group()))
                previous = match.end()
            pieces.append(html.escape(text[previous:]))
            content = "".join(pieces).replace("\n", "<br>")
        else:
            content = "未提取到可预览的命中片段。"
        if record.issues:
            content += '<p style="color:#99671c;">%s</p>' % "<br>".join(html.escape(x) for x in record.issues)
        self.snippet.setHtml('<div style="font-size:14px;line-height:1.6;">%s</div>' % content)

    def copy_path(self):
        if self.current_record:
            QApplication.clipboard().setText(self.current_record.path)

    def open_folder(self):
        if self.current_record and not self.demo:
            path = Path(self.current_record.path)
            folder = path.parent if path.is_file() else path
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def export_results(self):
        if self.active_config is None:
            return
        name, selected_filter = QFileDialog.getSaveFileName(self, "导出检查报告", "资料检查_%s.json" % time.strftime("%Y%m%d_%H%M%S"), "完整任务报告 (*.json);;结果清单 (*.csv)")
        if not name:
            return
        if not Path(name).suffix:
            name += ".csv" if "csv" in selected_filter else ".json"
        try:
            export_report(name, self.active_config, self.summary, self.results, self.warnings)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
        else:
            QMessageBox.information(self, "报告已导出", "报告已保存到：\n%s" % name)

    def save_rules(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存关键词规则", "检查规则.json", "关键词规则 (*.json)")
        if path:
            data = {"keywords": [x.strip() for x in self.keywords.toPlainText().splitlines() if x.strip()], "keyword_mode": "any" if self.mode_any.isChecked() else "all", "match_name": self.match_name.isChecked(), "match_content": self.match_content.isChecked(), "case_sensitive": self.case_sensitive.isChecked()}
            try:
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    def load_rules(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入关键词规则", "", "关键词规则 (*.json);;关键词列表 (*.txt)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            if Path(path).suffix.lower() == ".txt":
                self.keywords.setPlainText(text)
                return
            data = json.loads(text)
            if not isinstance(data, dict) or not isinstance(data.get("keywords"), list) or not all(isinstance(w, str) for w in data["keywords"]):
                raise ValueError("JSON 中的 keywords 必须是字符串列表。")
            if data.get("keyword_mode", "any") not in ("any", "all"):
                raise ValueError("keyword_mode 必须是 any 或 all。")
            for key in ("match_name", "match_content", "case_sensitive"):
                if key in data and not isinstance(data[key], bool):
                    raise ValueError("%s 必须是布尔值。" % key)
            self.keywords.setPlainText("\n".join(data["keywords"]))
            self.mode_all.setChecked(data.get("keyword_mode") == "all")
            self.mode_any.setChecked(data.get("keyword_mode") != "all")
            for key, widget in (("match_name", self.match_name), ("match_content", self.match_content), ("case_sensitive", self.case_sensitive)):
                value = data.get(key, key != "case_sensitive")
                widget.setChecked(value)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def load_demo(self):
        self.batch_selection_by_tab = {0: [], 1: []}
        self._scan_in_progress = False
        self.demo = True
        self.demo_label.show()
        self.roots.addItems([r"C:\Users\Office\Documents", r"D:\项目资料"])
        self.active_config = ScanConfig(roots=(r"C:\Users\Office\Documents", r"D:\项目资料"), keywords=("内部资料", "商业秘密", "仅限内部"))
        self.results = [
            FileResult(path=r"C:\Users\Office\Documents\内部资料_项目合作方案.docx", matched=True, matched_keywords=("内部资料",), matches=[Match("内部资料", "content", "正文 · 段落 12", "本方案为内部资料，请按公司制度管理。")], content_status="complete", total_matches=2),
            FileResult(path=r"D:\项目资料\财务\2026年度预算.xlsx", matched=True, matched_keywords=("内部资料", "仅限内部"), matches=[Match("内部资料", "content", "预算表!D18", "本表属于内部资料，仅限内部使用。"), Match("仅限内部", "content", "预算表!D18", "本表属于内部资料，仅限内部使用。")], content_status="complete", total_matches=2),
            FileResult(path=r"D:\项目资料\产品\产品路线图.pptx", matched=True, matched_keywords=("商业秘密",), matches=[Match("商业秘密", "content", "幻灯片 8 · 备注", "该产品设计涉及商业秘密，请勿外传。")], content_status="complete", total_matches=1),
            FileResult(path=r"C:\Users\Office\Documents\技术评估报告.pdf", matched=True, matched_keywords=("内部资料",), matches=[Match("内部资料", "content", "第 5 页", "本技术报告为内部资料。")], content_status="complete", total_matches=1),
            FileResult(path=r"D:\项目资料\客户清单.xlsx", content_status="unreadable", issues=["文件已加密或无法读取。"]),
            FileResult(path=r"D:\项目资料\历史归档.doc", content_status="unreadable", issues=["未启用旧版 Office 兼容读取。"]),
            FileResult(path=r"D:\项目资料\培训讲义.pdf", content_status="partial", issues=["文件含图片，图片文字未做 OCR。"]),
        ]
        for record, size in zip(self.results, (2516582, 435814, 5976883, 1887437, 204800, 98560, 618496)):
            record.size = size
        self.summary = ScanSummary(state="completed", discovered=128, processed=128, matched=4, content_complete=125, content_partial=1, content_unreadable=2, total=128)
        self.update_summary()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_title.setText("检查完成 · 有遗漏")
        self.status_icon.setText("✓")
        self.status_icon.setStyleSheet("font-size: 24px; color: #26835b;")
        self.current_file.setText("界面展示使用虚构示例数据，未扫描本机文件。")
        self.export_btn.setEnabled(False)
        self.render_results()
        self.table.selectRow(1)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            if not self.closing:
                answer = QMessageBox.question(self, "检查仍在进行", "停止检查并退出？已处理的结果将保留到退出前。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self.closing = True
            self.stop_scan()
            event.ignore()
            return
        event.accept()


_SMOKE_PDF_CASES = (
    ("plain_pdf", "人工测试.pdf", None, "", True),
    ("rc4_password_protected", "加密测试.pdf", "RC4-128", "artificial-test-password", False),
    ("aes128_empty_password", "AES128_空密码.pdf", "AES-128", "", True),
    ("aes256_empty_password", "AES256_空密码.pdf", "AES-256", "", True),
    ("aes256_password_protected", "AES256_需密码.pdf", "AES-256", "artificial-test-password", False),
)


def _create_smoke_fixtures(directory):
    """Create only artificial test documents, including explicit AES cases."""
    import pypdf
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    root = Path(directory)
    Path(root, "人工测试.txt").write_text("这是一份人工生成的内部资料测试文件。", encoding="utf-8")
    for _key, filename, algorithm, password, _readable in _SMOKE_PDF_CASES:
        pdf = pypdf.PdfWriter()
        page = pdf.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): pdf._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (Artificial Confidential ALPHA) Tj ET")
        page[NameObject("/Contents")] = pdf._add_object(stream)
        if algorithm:
            pdf.encrypt(user_password=password, owner_password="artificial-owner-password", algorithm=algorithm)
        with Path(root, filename).open("wb") as output:
            pdf.write(output)


def _smoke_pdf_checks(results):
    """Identify fixture outcomes by filename, independent of traversal order."""
    by_name = {Path(record.path).name: record for record in results}
    checks = {}
    for key, filename, _algorithm, _password, readable in _SMOKE_PDF_CASES:
        record = by_name.get(filename)
        if readable:
            checks[key] = bool(record and record.content_status == "complete" and record.matched
                               and any(match.source == "content" and match.keyword == "ALPHA" for match in record.matches))
        else:
            checks[key] = bool(record and record.content_status == "unreadable" and not record.matched)
    return checks


def main():
    parser = argparse.ArgumentParser(description="敏感资料检查")
    parser.add_argument("--demo", action="store_true", help="展示虚构结果，不扫描本机")
    parser.add_argument("--screenshot", help="将界面截图保存到指定 PNG 后退出")
    parser.add_argument("--batch-screenshot", help="保存使用虚构示例数据的批量选择弹窗截图后退出")
    parser.add_argument("--smoke-test", help="使用临时人工测试文本检查打包程序，并写出 JSON")
    args = parser.parse_args()
    if args.screenshot or args.batch_screenshot or args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv[:1])
    # Qt's offscreen plugin does not enumerate Windows fonts. Register system
    # fonts for screenshots/self-tests; the regular Windows platform needs none.
    if not QFontDatabase.families() and os.name == "nt":
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for font_file in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisym.ttf"):
            if (font_dir / font_file).is_file():
                QFontDatabase.addApplicationFont(str(font_dir / font_file))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setApplicationName("敏感资料检查")
    app.setApplicationVersion(APP_VERSION)
    app.setStyle(InspectorStyle())
    app.setStyleSheet(STYLE)
    window = MainWindow()
    if args.demo or args.batch_screenshot:
        window.load_demo()
    window.show()
    fixture = None
    smoke_result = {"ok": False}
    if args.smoke_test:
        import pypdf
        from pypdf._crypt_providers import crypt_provider
        fixture = tempfile.TemporaryDirectory(prefix="file-inspector-smoke-")
        try:
            _create_smoke_fixtures(fixture.name)
        except Exception as exc:
            Path(args.smoke_test).write_text(json.dumps({
                "ok": False, "aes_pdf": False, "crypto_provider": crypt_provider,
                "error_type": type(exc).__name__, "error": str(exc),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            fixture.cleanup()
            return 1
        window.add_root(fixture.name)
        window.keywords.setPlainText("内部资料\nALPHA")

        def finish_smoke():
            from reports import export_selected_report
            from file_clipboard import cut_files_to_clipboard

            class TestClipboard:
                def setMimeData(self, data):
                    self.data = data

            matched = window.batch_records()
            chosen = next((record for record in matched if Path(record.path).name == "人工测试.txt"), None)
            window.batch_selection_by_tab[0] = [chosen.path] if chosen else []
            dialog = window.create_batch_dialog()
            selected = dialog.selected_records() if dialog else []
            report_path = Path(fixture.name, "selected-report.json")
            export_selected_report(report_path, window.active_config, window.summary, selected, window.warnings, len(matched))
            subset = json.loads(report_path.read_text(encoding="utf-8"))
            clipboard = TestClipboard()
            count = cut_files_to_clipboard([record.path for record in selected], clipboard=clipboard)
            batch_ok = (len(selected) == count == 1 and len(clipboard.data.urls()) == 1
                        and subset["export_scope"]["kind"] == "selected"
                        and len(subset["results"]) == 1
                        and all(Path(record.path).is_file() for record in window.results))
            pdf_checks = _smoke_pdf_checks(window.results)
            aes_ok = all(pdf_checks[key] for key in ("aes128_empty_password", "aes256_empty_password", "aes256_password_protected"))
            smoke_result.update({"ok": window.summary.state == "completed" and window.summary.processed == 6 and window.summary.matched == 4 and window.summary.content_complete == 4 and window.summary.content_unreadable == 2 and window.table.rowCount() == 4 and batch_ok and all(pdf_checks.values()), "batch_selection": batch_ok, "aes_pdf": aes_ok, "crypto_provider": crypt_provider, "pdf_checks": pdf_checks, "summary": asdict(window.summary), "pdf_library": pypdf.__version__, "gui_rows": window.table.rowCount(), "results": [asdict(r) for r in window.results]})
            if dialog:
                dialog.deleteLater()
            Path(args.smoke_test).write_text(json.dumps(smoke_result, ensure_ascii=False, indent=2), encoding="utf-8")
            app.exit(0 if smoke_result["ok"] else 1)

        def start_smoke():
            window.start_scan()
            window.worker.finished.connect(finish_smoke)

        QTimer.singleShot(100, start_smoke)
        QTimer.singleShot(45000, lambda: (Path(args.smoke_test).write_text(json.dumps({"ok": False, "error": "timeout"}), encoding="utf-8"), app.exit(2)) if not smoke_result["ok"] else None)
    elif args.batch_screenshot:
        window.batch_selection_by_tab[0] = [record.path for record in window.batch_records()[:3]]
        batch_preview = window.create_batch_dialog()
        batch_preview.open()

        def save_batch_screenshot():
            destination = Path(args.batch_screenshot)
            destination.parent.mkdir(parents=True, exist_ok=True)
            app.exit(0 if batch_preview.grab().save(str(destination)) else 1)
        QTimer.singleShot(600, save_batch_screenshot)
    elif args.screenshot:
        def save_screenshot():
            destination = Path(args.screenshot)
            destination.parent.mkdir(parents=True, exist_ok=True)
            app.exit(0 if window.grab().save(str(destination)) else 1)
        QTimer.singleShot(600, save_screenshot)
    code = app.exec()
    if fixture:
        fixture.cleanup()
    return code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
