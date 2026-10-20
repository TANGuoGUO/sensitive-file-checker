"""Put existing files on the Windows clipboard with a preferred move effect.

The application never moves or deletes these files. Windows Explorer performs
the move only if the user later pastes the clipboard contents there.

Qt registers application/x-qt-windows-mime values as native Windows formats:
https://doc.qt.io/qt-6/qwindowsmimeconverter.html
The Shell's Preferred DropEffect is a DWORD containing DROPEFFECT_MOVE:
https://learn.microsoft.com/en-us/windows/win32/shell/clipboard
"""

import os
import stat
import struct
from typing import Iterable, Union

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication


PREFERRED_DROP_EFFECT_MIME = 'application/x-qt-windows-mime;value="Preferred DropEffect"'
DROPEFFECT_MOVE = 2


def _validated_paths(paths: Iterable[Union[str, os.PathLike]]) -> list:
    if isinstance(paths, (str, bytes, os.PathLike)):
        raise ValueError("请提供所选文件的路径列表。")
    try:
        candidates = list(paths)
    except TypeError as exc:
        raise ValueError("请提供所选文件的路径列表。") from exc
    if not candidates:
        raise ValueError("请先选择需要剪切的文件。")
    unique = []
    seen = set()
    for candidate in candidates:
        try:
            raw = os.fspath(candidate)
        except TypeError as exc:
            raise ValueError("所选项目包含无效的文件路径。") from exc
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("所选项目包含无效的文件路径。")
        path = os.path.abspath(os.path.expanduser(raw))
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise ValueError("文件不存在或无法访问，剪贴板未更改：{}".format(path)) from exc
        # Name-surrogate reparse tags include Windows links and junctions.
        # Cloud file placeholders may also be reparse points but are not links.
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
            raise ValueError("只能剪切现存的普通文件，不能选择目录或链接：{}".format(path))
        normalized = os.path.normcase(os.path.normpath(path))
        if normalized not in seen:
            seen.add(normalized)
            unique.append(path)
    return unique


def cut_files_to_clipboard(paths: Iterable[Union[str, os.PathLike]], clipboard=None) -> int:
    """Validate all paths, then set one clipboard payload and return file count.

    ``clipboard`` may be injected for tests; it must expose ``setMimeData``.
    With no injection, an existing QApplication and its system clipboard are
    required. Invalid paths are rejected before the clipboard is touched.
    """
    validated = _validated_paths(paths)
    if clipboard is None:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("程序界面尚未初始化，无法使用系统剪贴板。")
        clipboard = application.clipboard()
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(path) for path in validated])
    mime_data.setData(PREFERRED_DROP_EFFECT_MIME, struct.pack("<I", DROPEFFECT_MOVE))
    try:
        clipboard.setMimeData(mime_data)
        # Qt's Windows implementation only warns and returns when the native
        # OleSetClipboard call fails. Ownership confirms the write succeeded;
        # do not report a successful cut while the old clipboard remains.
        owns_clipboard = getattr(clipboard, "ownsClipboard", None)
        if callable(owns_clipboard) and not owns_clipboard():
            raise RuntimeError("系统剪贴板正忙或已被其他程序更改。")
    except Exception as exc:
        raise RuntimeError("无法写入剪贴板，请稍后重试。") from exc
    return len(validated)
