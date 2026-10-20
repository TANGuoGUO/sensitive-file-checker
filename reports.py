"""Export local scan reports without changing the files that were checked."""

import csv
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Sequence


CSV_COLUMNS = (
    "记录类型", "文件路径", "文件名", "大小（字节）", "修改时间", "是否命中",
    "命中关键词", "命中总次数", "命中来源", "命中位置", "命中片段",
    "内容检查状态", "解析器", "命中明细已截断", "异常信息", "任务状态",
    "检查范围", "检查规则", "任务摘要", "详细数据",
)


def _plain(value: Any) -> Any:
    """Keep every dataclass field and normalize values to JSON-compatible data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_plain(item) for item in sorted(value, key=str)]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("Report value cannot be serialized: {}".format(type(value).__name__))


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _csv_safe(value: Any) -> Any:
    # Spreadsheet programs may execute text beginning with a formula marker.
    # Also guard whitespace/control characters before a marker. Prefixing an
    # apostrophe preserves the visible text while preventing formula evaluation.
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.lstrip(" \t\r\n\v\f\x00\ufeff")
        if value.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def _lines(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values
    return "\n".join(str(value) for value in values)


def _modified_at(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            return str(value)
    return "" if not value else str(value)


def validate_report_destination(path: str, protected_paths: Sequence[str]) -> None:
    """Reject a report destination that would replace an original scan file.

    Normalized resolved paths catch spelling/case/link aliases; samefile also
    catches distinct existing names that refer to the same filesystem file.
    The helper only reads path metadata and never creates an output file.
    """
    try:
        destination = Path(path).expanduser().resolve()
        destination_key = os.path.normcase(str(destination))
        for original in protected_paths:
            if not original:
                continue
            try:
                source = Path(original).expanduser().resolve()
            except (OSError, RuntimeError):
                # A failed file result can contain a malformed/inaccessible
                # original name. Keep its literal absolute name protected
                # without preventing export to an unrelated valid location.
                source = Path(os.path.abspath(os.path.expanduser(str(original))))
            is_original = destination_key == os.path.normcase(str(source))
            if not is_original:
                try:
                    is_original = os.path.samefile(str(destination), str(source))
                except OSError:
                    # New report names and disappeared source files have no
                    # identity to compare; normalized path protection remains.
                    pass
            if is_original:
                raise ValueError("报告不能覆盖本次检查的原始文件，请选择其他保存位置。")
    except (OSError, RuntimeError) as exc:
        raise ValueError("无法确认报告保存路径，请选择其他保存位置。") from exc


def _file_row(result: Mapping) -> dict:
    path = str(result.get("path", ""))
    matches = [match for match in result.get("matches", []) if isinstance(match, Mapping)]
    sources = list(dict.fromkeys(str(match.get("source", "")) for match in matches))
    locations = ["{}: {}".format(match.get("source", ""), match.get("location", "")) for match in matches]
    snippets = ["[{}] {}".format(match.get("keyword", ""), match.get("snippet", "")) for match in matches]
    return {
        "记录类型": "FILE",
        "文件路径": path,
        # Path.name on non-Windows hosts would leave Windows separators intact.
        "文件名": path.replace("\\", "/").rsplit("/", 1)[-1],
        "大小（字节）": result.get("size", ""),
        "修改时间": _modified_at(result.get("modified_at")),
        "是否命中": "是" if result.get("matched") else "否",
        "命中关键词": _lines(result.get("matched_keywords", [])),
        "命中总次数": result.get("total_matches", 0),
        "命中来源": _lines(sources),
        "命中位置": _lines(locations),
        "命中片段": _lines(snippets),
        "内容检查状态": result.get("content_status", ""),
        "解析器": result.get("parser", ""),
        "命中明细已截断": "是" if result.get("matches_truncated") else "否",
        "异常信息": _lines(result.get("issues", [])),
        "详细数据": _compact(result),
    }


def _write_csv(handle: Any, payload: Mapping) -> None:
    writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
    writer.writeheader()

    def write(row: Mapping) -> None:
        writer.writerow({key: _csv_safe(value) for key, value in row.items()})

    for result in payload["results"]:
        write(_file_row(result))

    config = payload["config"]
    summary = payload["summary"]
    rules = {key: value for key, value in config.items() if key != "roots"}
    task_details = {
        "format_version": payload["format_version"],
        "generated_at": payload["generated_at"],
        "config": config,
        "summary": summary,
    }
    if "export_scope" in payload:
        task_details["export_scope"] = payload["export_scope"]
    write({
        "记录类型": "TASK",
        "任务状态": summary.get("state", ""),
        "检查范围": _lines(config.get("roots", [])),
        "检查规则": _compact(rules),
        "任务摘要": _compact(summary),
        "详细数据": _compact(task_details),
    })
    for warning in payload["warnings"]:
        write({
            "记录类型": "WARNING",
            "文件路径": warning.get("path", ""),
            "异常信息": warning.get("message", warning.get("error", warning.get("detail", _compact(warning)))),
            "任务状态": summary.get("state", ""),
            "详细数据": _compact(warning),
        })


def export_report(path: str, config: Any, summary: Any,
                  results: Sequence[Any], warnings: Sequence[dict], *, selection=None) -> None:
    """Save a complete JSON report or a UTF-8-BOM CSV results table.

    CSV uses FILE, TASK and WARNING record types. Its task row remains present
    even when no files were processed, so errors or a stopped scan are visible.
    An atomic replacement leaves an existing report intact if writing fails.
    ``selection`` optionally identifies a selected subset without changing the
    original scan summary. Omit it for the original complete-report behavior.
    """
    destination = Path(path)
    extension = destination.suffix.lower()
    if extension not in (".json", ".csv"):
        raise ValueError("报告格式必须为 .json 或 .csv")
    payload = {
        "format_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": _plain(config),
        "summary": _plain(summary),
        "results": _plain(results),
        "warnings": _plain(warnings),
    }
    if not isinstance(payload["config"], dict) or not isinstance(payload["summary"], dict):
        raise TypeError("config and summary must be dataclasses or mappings")
    if selection is not None:
        scope = _plain(selection)
        count = len(payload["results"])
        if not isinstance(scope, dict) or scope.get("kind") != "selected":
            raise ValueError("所选导出范围必须包含 kind='selected'。")
        total = scope.get("total_result_count")
        if isinstance(total, bool) or not isinstance(total, int) or total < count:
            raise ValueError("全部结果数量必须为不小于所选结果数量的整数。")
        if "count" in scope and scope["count"] != count:
            raise ValueError("所选结果数量与实际导出数量不一致。")
        scope["count"] = count
        payload["export_scope"] = scope
    validate_report_destination(path, [result.get("path", "") for result in payload["results"]])
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig" if extension == ".csv" else "utf-8",
            newline="", dir=str(destination.parent), prefix=".report-", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            if extension == ".json":
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
            else:
                _write_csv(handle, payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, str(destination))
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def export_selected_report(path: str, config: Any, summary: Any,
                           results: Sequence[Any], warnings: Sequence[dict],
                           total_results_count: int) -> None:
    """Export selected file results while preserving full task statistics."""
    export_report(path, config, summary, results, warnings, selection={
        "kind": "selected",
        "total_result_count": total_results_count,
    })
