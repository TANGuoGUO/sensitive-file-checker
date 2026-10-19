"""Thread-friendly local keyword scanner consumed by the Qt UI."""
import copy
import os
import re
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from extractors import ExtractionStopped, extract_file


DEFAULT_EXTENSIONS = (".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".pdf", ".txt", ".csv", ".md", ".log", ".tsv")


@dataclass(frozen=True)
class ScanConfig:
    roots: Tuple[str, ...]
    keywords: Tuple[str, ...]
    keyword_mode: str = "any"
    match_name: bool = True
    match_content: bool = True
    extensions: Tuple[str, ...] = DEFAULT_EXTENSIONS
    recursive: bool = True
    excluded_dirs: Tuple[str, ...] = ()
    case_sensitive: bool = False
    max_file_mb: int = 100
    max_uncompressed_mb: int = 200
    max_preview_matches: int = 100
    use_legacy_office: bool = False
    legacy_timeout_seconds: int = 20


@dataclass
class Match:
    keyword: str
    source: str
    location: str
    snippet: str
    count: int = 1


@dataclass
class FileResult:
    path: str
    size: int = 0
    modified_at: float = 0.0
    matched: bool = False
    matched_keywords: Tuple[str, ...] = ()
    matches: List[Match] = field(default_factory=list)
    content_status: str = "not_requested"
    parser: str = ""
    issues: List[str] = field(default_factory=list)
    total_matches: int = 0
    matches_truncated: bool = False


@dataclass
class ScanSummary:
    state: str = "idle"
    discovered: int = 0
    processed: int = 0
    matched: int = 0
    content_complete: int = 0
    content_partial: int = 0
    content_unreadable: int = 0
    name_only: int = 0
    excluded: int = 0
    directory_errors: int = 0
    total: Optional[int] = None
    started_at: float = 0.0
    finished_at: Optional[float] = None
    elapsed_seconds: float = 0.0


class ScanControl:
    def __init__(self):
        self._condition = threading.Condition()
        self._paused = False
        self._stopped = False

    def pause(self):
        with self._condition:
            self._paused = True

    def resume(self):
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def stop(self):
        with self._condition:
            self._stopped = True
            self._paused = False
            self._condition.notify_all()

    @property
    def is_paused(self):
        with self._condition:
            return self._paused

    @property
    def is_stopped(self):
        with self._condition:
            return self._stopped

    def checkpoint(self):
        with self._condition:
            while self._paused and not self._stopped:
                self._condition.wait(0.2)
            return not self._stopped


def _normalized(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(str(path)))))


def _within(path, parent):
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:
        return False


def _is_link(path):
    info = os.lstat(path)
    # Cloud placeholders are also reparse points; only name-surrogate tags
    # redirect paths and must be skipped to prevent junction/symlink cycles.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0) & 0x20000000)


def _keywords(config):
    seen = set()
    result = []
    for value in config.keywords:
        word = str(value).strip()
        key = word if config.case_sensitive else word.casefold()
        if word and key not in seen:
            seen.add(key)
            result.append(word)
    return tuple(result)


def _validate(config):
    if not config.roots:
        raise ValueError("请至少选择一个扫描目录。")
    if not _keywords(config):
        raise ValueError("请至少输入一个非空关键词。")
    if not config.match_name and not config.match_content:
        raise ValueError("请至少启用文件名或文件内容匹配。")
    if config.keyword_mode not in ("any", "all"):
        raise ValueError("关键词模式必须为 any 或 all。")
    if not config.extensions:
        raise ValueError("请至少选择一种文件类型。")
    if min(config.max_file_mb, config.max_uncompressed_mb, config.legacy_timeout_seconds) <= 0:
        raise ValueError("文件大小、解压大小与超时限制必须大于零。")
    if config.max_preview_matches < 0:
        raise ValueError("预览数量不能小于零。")


def run_scan(config: ScanConfig, control: Optional[ScanControl] = None,
             on_event: Optional[Callable[[Dict], None]] = None) -> ScanSummary:
    _validate(config)
    control = control or ScanControl()
    callback = on_event or (lambda event: None)
    summary = ScanSummary(started_at=time.time())
    phase = "discovering"

    def emit(kind, **data):
        callback(dict(type=kind, **data))

    def set_state(state):
        summary.state = state
        emit("state", state=state)

    def snapshot():
        summary.elapsed_seconds = time.time() - summary.started_at
        return copy.copy(summary)

    def progress(path=""):
        emit("progress", current_file=path, summary=snapshot())

    def checkpoint():
        if control.is_paused and summary.state != "paused":
            set_state("paused")
        okay = control.checkpoint()
        if not okay:
            if summary.state != "stopping":
                set_state("stopping")
            return False
        if summary.state == "paused":
            set_state(phase)
        return True

    words = _keywords(config)
    patterns = [(word, re.compile(re.escape(word), 0 if config.case_sensitive else re.IGNORECASE)) for word in words]
    extensions = {"." + item.lower().lstrip(".") for item in config.extensions}
    excluded_names = {str(path).casefold() for path in config.excluded_dirs
                      if not os.path.isabs(str(path)) and "/" not in str(path) and "\\" not in str(path)}
    excluded = tuple(_normalized(path) for path in config.excluded_dirs
                     if str(path).casefold() not in excluded_names)
    seen_files = set()
    seen_dirs = set()
    files = []

    def excluded_path(path):
        return (any(_within(path, parent) for parent in excluded)
                or any(part.casefold() in excluded_names for part in Path(path).parts))

    def warning(path, message):
        emit("warning", path=str(path), message=message)

    def accept_file(path):
        if Path(path).suffix.lower() not in extensions:
            return
        normalized = _normalized(path)
        if normalized in seen_files:
            return
        seen_files.add(normalized)
        if excluded_path(normalized) or Path(path).name.startswith("~$"):
            summary.excluded += 1
            return
        try:
            if _is_link(path):
                summary.excluded += 1
                return
        except OSError:
            pass  # A disappeared file is reported by the per-file read step.
        files.append(str(Path(path).absolute()))
        summary.discovered += 1
        if summary.discovered % 100 == 0:
            progress()

    def walk(directory):
        if not checkpoint():
            return
        normalized = _normalized(directory)
        if normalized in seen_dirs:
            return
        if excluded_path(normalized):
            summary.excluded += 1
            return
        try:
            if _is_link(directory):
                summary.excluded += 1
                return
            seen_dirs.add(normalized)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not checkpoint():
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if config.recursive:
                                walk(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            accept_file(entry.path)
                        elif entry.is_symlink():
                            summary.excluded += 1
                    except (OSError, RecursionError) as exc:
                        summary.directory_errors += 1
                        message = "目录嵌套过深，未完整检查此范围。" if isinstance(exc, RecursionError) else "无法访问此路径：" + str(exc)
                        warning(entry.path, message)
        except (OSError, RecursionError) as exc:
            summary.directory_errors += 1
            message = "目录嵌套过深，未完整检查此范围。" if isinstance(exc, RecursionError) else "无法枚举目录：" + str(exc)
            warning(directory, message)

    def scan_file(path):
        result = FileResult(path=path)
        found = set()

        def match_text(text, source, location):
            for word, pattern in patterns:
                if not checkpoint():
                    raise ExtractionStopped()
                for occurrence in pattern.finditer(text):
                    found.add(word)
                    result.total_matches += 1
                    if len(result.matches) < config.max_preview_matches:
                        start = max(0, occurrence.start() - 65)
                        end = min(len(text), occurrence.end() + 85)
                        snippet = ("…" if start else "") + text[start:end].replace("\x00", "") + ("…" if end < len(text) else "")
                        result.matches.append(Match(word, source, location, snippet))

        if config.match_name:
            match_text(Path(path).name, "name", "文件名")
        try:
            info = os.stat(path)
            result.size = info.st_size
            result.modified_at = info.st_mtime
        except OSError as exc:
            result.content_status = "unreadable" if config.match_content else "not_requested"
            result.issues.append("无法读取文件信息：" + str(exc))
        else:
            if config.match_content:
                if getattr(info, "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
                    result.content_status = "unreadable"
                    result.issues.append("云端占位文件未下载到本机，已跳过内容读取以避免触发下载。")
                    result.matched_keywords = tuple(word for word in words if word in found)
                    result.matched = bool(found) if config.keyword_mode == "any" else all(word in found for word in words)
                    result.matches_truncated = result.total_matches > len(result.matches)
                    return result
                try:
                    content = extract_file(path, checkpoint=checkpoint,
                        max_file_bytes=config.max_file_mb * 1024 * 1024,
                        max_uncompressed_bytes=config.max_uncompressed_mb * 1024 * 1024,
                        use_legacy_office=config.use_legacy_office,
                        legacy_timeout_seconds=config.legacy_timeout_seconds)
                    result.parser = content.parser
                    result.content_status = content.status
                    result.issues.extend(content.issues)
                    for block in content.blocks:
                        match_text(block.text, "content", block.location)
                except ExtractionStopped:
                    raise
                except Exception as exc:
                    result.content_status = "unreadable"
                    result.issues.append(str(exc)[:1000] or type(exc).__name__)
        result.matched_keywords = tuple(word for word in words if word in found)
        result.matched = bool(found) if config.keyword_mode == "any" else all(word in found for word in words)
        result.matches_truncated = result.total_matches > len(result.matches)
        return result

    try:
        set_state("discovering")
        progress()
        for root in config.roots:
            if not checkpoint():
                break
            if os.path.isfile(root):
                accept_file(root)
            else:
                walk(root)
        if not control.is_stopped:
            summary.total = len(files)
            phase = "scanning"
            set_state("scanning")
            progress()
            for path in files:
                if not checkpoint():
                    break
                progress(path)
                result = scan_file(path)
                summary.processed += 1
                summary.matched += int(result.matched)
                if result.content_status == "complete":
                    summary.content_complete += 1
                elif result.content_status == "partial":
                    summary.content_partial += 1
                elif result.content_status == "unreadable":
                    summary.content_unreadable += 1
                else:
                    summary.name_only += 1
                emit("result", result=result)
                progress(path)
        set_state("stopped" if control.is_stopped else "completed")
    except ExtractionStopped:
        set_state("stopped")
    except Exception as exc:
        set_state("failed")
        warning("", "扫描任务失败：" + str(exc)[:1000])
    finally:
        summary.finished_at = time.time()
        summary.elapsed_seconds = summary.finished_at - summary.started_at
        emit("finished", summary=copy.copy(summary))
    return summary
