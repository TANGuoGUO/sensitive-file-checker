"""Clipboard tests use artificial files and never change the system clipboard."""

import os
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QMimeData

from file_clipboard import (
    DROPEFFECT_MOVE, PREFERRED_DROP_EFFECT_MIME, cut_files_to_clipboard,
)


class FakeClipboard:
    def __init__(self):
        self.original = QMimeData()
        self.original.setText("原剪贴板内容")
        self.current = self.original
        self.calls = 0

    def setMimeData(self, value):
        self.calls += 1
        self.current = value


@pytest.fixture
def files(tmp_path):
    first = tmp_path / "中文 空格#报告.docx"
    second = tmp_path / "另一个文件.xlsx"
    first.write_bytes(b"artificial fixture one")
    second.write_bytes(b"artificial fixture two")
    return first, second


def test_cut_payload_has_unicode_file_urls_and_little_endian_move(files):
    clipboard = FakeClipboard()
    count = cut_files_to_clipboard(files, clipboard)
    assert count == 2 and clipboard.calls == 1
    assert clipboard.current.hasUrls()
    assert [Path(url.toLocalFile()) for url in clipboard.current.urls()] == list(files)
    assert all(url.isLocalFile() for url in clipboard.current.urls())
    assert bytes(clipboard.current.data(PREFERRED_DROP_EFFECT_MIME)) == b"\x02\x00\x00\x00"
    assert struct.unpack("<I", bytes(clipboard.current.data(PREFERRED_DROP_EFFECT_MIME))) == (DROPEFFECT_MOVE,)
    assert files[0].read_bytes() == b"artificial fixture one"
    assert files[1].read_bytes() == b"artificial fixture two"


def test_duplicate_paths_are_removed_without_reordering(files):
    clipboard = FakeClipboard()
    first, second = files
    duplicate = first.parent / "subfolder" / ".." / first.name
    count = cut_files_to_clipboard([first, str(second), first, duplicate], clipboard)
    assert count == 2
    assert [Path(url.toLocalFile()) for url in clipboard.current.urls()] == [first, second]


def test_invalid_file_prevents_any_clipboard_change(files):
    clipboard = FakeClipboard()
    with pytest.raises(ValueError, match="文件不存在或无法访问"):
        cut_files_to_clipboard([files[0], files[1].parent / "不存在.pptx"], clipboard)
    assert clipboard.calls == 0 and clipboard.current is clipboard.original
    assert clipboard.current.text() == "原剪贴板内容"
    assert files[0].exists()


def test_directory_is_rejected_before_clipboard_change(files):
    clipboard = FakeClipboard()
    with pytest.raises(ValueError, match="普通文件"):
        cut_files_to_clipboard([files[0], files[0].parent], clipboard)
    assert clipboard.calls == 0 and clipboard.current is clipboard.original


@pytest.mark.parametrize("paths", [[], None, "single-path.txt", [""], [b"bytes-path"], ["bad\x00path"]])
def test_empty_or_invalid_input_does_not_modify_clipboard(paths):
    clipboard = FakeClipboard()
    with pytest.raises(ValueError):
        cut_files_to_clipboard(paths, clipboard)
    assert clipboard.calls == 0 and clipboard.current is clipboard.original


def test_windows_case_variants_deduplicate(files):
    if os.name != "nt":
        pytest.skip("Windows filesystem comparison")
    clipboard = FakeClipboard()
    assert cut_files_to_clipboard([files[0], str(files[0]).upper()], clipboard) == 1


def test_link_is_rejected_but_cloud_reparse_file_is_allowed(files):
    clipboard = FakeClipboard()
    original = os.lstat(files[0])
    with patch("file_clipboard.os.lstat", return_value=SimpleNamespace(st_mode=original.st_mode, st_reparse_tag=0xA000000C)):
        with pytest.raises(ValueError, match="链接"):
            cut_files_to_clipboard([files[0]], clipboard)
    assert clipboard.calls == 0
    with patch("file_clipboard.os.lstat", return_value=SimpleNamespace(st_mode=original.st_mode, st_reparse_tag=0x9000001A)):
        assert cut_files_to_clipboard([files[0]], clipboard) == 1


def test_default_clipboard_requires_an_existing_application(files):
    with patch("file_clipboard.QApplication.instance", return_value=None):
        with pytest.raises(RuntimeError, match="尚未初始化"):
            cut_files_to_clipboard(files)


def test_clipboard_exception_is_reported_in_chinese(files):
    clipboard = FakeClipboard()
    with patch.object(clipboard, "setMimeData", side_effect=RuntimeError("native failure")):
        with pytest.raises(RuntimeError, match="无法写入剪贴板"):
            cut_files_to_clipboard(files, clipboard)
    assert clipboard.current is clipboard.original


def test_silent_native_clipboard_failure_is_not_reported_as_success(files):
    clipboard = FakeClipboard()
    clipboard.ownsClipboard = lambda: False
    # Qt's native Windows backend warns instead of raising on a locked
    # clipboard. Simulate that behavior without touching the real clipboard.
    with patch.object(clipboard, "setMimeData", return_value=None):
        with pytest.raises(RuntimeError, match="无法写入剪贴板"):
            cut_files_to_clipboard(files, clipboard)
    assert clipboard.current is clipboard.original


def test_native_clipboard_ownership_confirms_success(files):
    clipboard = FakeClipboard()
    clipboard.ownsClipboard = lambda: True
    assert cut_files_to_clipboard(files, clipboard) == 2
    assert clipboard.calls == 1


def test_cross_drive_paths_use_independent_file_urls_without_moving():
    # Filesystem metadata is simulated for two independent Windows volumes;
    # no actual drives or user files are accessed by this contract test.
    if os.name != "nt":
        pytest.skip("Windows drive path syntax")
    import stat
    paths = [r"C:\人工测试\报告 one.docx", r"D:\人工测试\报告 two.pptx"]
    clipboard = FakeClipboard()
    with patch("file_clipboard.os.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_reparse_tag=0)):
        assert cut_files_to_clipboard(paths, clipboard) == 2
    assert [url.toLocalFile().replace("/", "\\") for url in clipboard.current.urls()] == paths
