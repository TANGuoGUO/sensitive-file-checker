import csv
import json
import os
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

from reports import export_report, export_selected_report, validate_report_destination


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
    size: int = 123
    modified_at: float = 0.0
    matched: bool = True
    matched_keywords: Tuple[str, ...] = ("内部",)
    matches: List[Match] = field(default_factory=list)
    content_status: str = "complete"
    parser: str = "docx"
    issues: List[str] = field(default_factory=list)
    total_matches: int = 1
    matches_truncated: bool = False


@dataclass
class Config:
    roots: Tuple[str, ...] = ("D:\\办公资料",)
    keywords: Tuple[str, ...] = ("内部", "=恶意公式")
    keyword_mode: str = "any"
    excluded_dirs: Tuple[str, ...] = ("D:\\办公资料\\缓存",)
    match_content: bool = True


@dataclass
class Summary:
    state: str = "stopped"
    processed: int = 1
    matched: int = 1
    directory_errors: int = 1


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parent))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.result = FileResult(
            path="D:\\办公资料\\内部,报告.docx",
            matches=[Match("内部", "content", "第 2 段", "内部资料,请保留\n第二行")],
            issues=["部分内容无法读取"],
        )
        self.warnings = [{"path": "D:\\办公资料\\受限目录", "message": "拒绝访问"}]

    def test_json_preserves_configuration_results_warnings_and_stop_state(self):
        destination = self.directory / "中文报告.json"
        export_report(str(destination), Config(), Summary(), [self.result], self.warnings)
        data = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(data["config"]["keywords"], ["内部", "=恶意公式"])
        self.assertEqual(data["config"]["excluded_dirs"], ["D:\\办公资料\\缓存"])
        self.assertEqual(data["summary"]["state"], "stopped")
        self.assertEqual(data["results"][0]["matches"][0]["snippet"], "内部资料,请保留\n第二行")
        self.assertEqual(data["warnings"], self.warnings)
        self.assertIn("内部", destination.read_text(encoding="utf-8"))
        self.assertIsNotNone(datetime.fromisoformat(data["generated_at"]).tzinfo)

    def test_csv_roundtrip_unicode_newlines_and_record_types(self):
        destination = self.directory / "报告.csv"
        export_report(str(destination), Config(), Summary(), [self.result], self.warnings)
        self.assertTrue(destination.read_bytes().startswith(b"\xef\xbb\xbf"))
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["记录类型"] for row in rows], ["FILE", "TASK", "WARNING"])
        self.assertEqual(rows[0]["文件名"], "内部,报告.docx")
        self.assertIn("内部资料,请保留\n第二行", rows[0]["命中片段"])
        self.assertEqual(json.loads(rows[0]["详细数据"])["matches"][0]["snippet"], "内部资料,请保留\n第二行")
        self.assertEqual(rows[1]["任务状态"], "stopped")
        self.assertEqual(json.loads(rows[1]["检查规则"])["keywords"], ["内部", "=恶意公式"])
        self.assertEqual(rows[2]["异常信息"], "拒绝访问")

    def test_csv_formula_injection_is_neutralized(self):
        result = FileResult(path="=HYPERLINK(\"bad\")", matched_keywords=("  +SUM(1,2)",), issues=["\t=1+1"])
        warnings = [{"path": "@command", "message": "\n=1+1"}]
        destination = self.directory / "safe.csv"
        export_report(str(destination), Config(), Summary(), [result], warnings)
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows[0]["文件路径"].startswith("'="))
        self.assertTrue(rows[0]["命中关键词"].startswith("'  +"))
        self.assertTrue(rows[0]["异常信息"].startswith("'\t"))
        self.assertTrue(rows[2]["文件路径"].startswith("'@"))
        self.assertTrue(rows[2]["异常信息"].startswith("'\n"))

    def test_empty_scan_still_exports_task_and_warning_rows(self):
        destination = self.directory / "empty.csv"
        export_report(str(destination), Config(), Summary(processed=0, matched=0), [], self.warnings)
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["记录类型"] for row in rows], ["TASK", "WARNING"])
        self.assertEqual(json.loads(rows[0]["任务摘要"])["processed"], 0)

    def test_failed_export_preserves_existing_report_and_cleans_temporary_file(self):
        destination = self.directory / "existing.csv"
        destination.write_text("original", encoding="utf-8")
        with patch("reports.os.replace", side_effect=PermissionError("report is open")):
            with self.assertRaises(PermissionError):
                export_report(str(destination), Config(), Summary(), [self.result], [])
        self.assertEqual(destination.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.directory.glob(".report-*.tmp")), [])

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaises(ValueError):
            export_report(str(self.directory / "report.xlsx"), Config(), Summary(), [], [])

    def test_selected_json_marks_subset_and_keeps_original_task_summary(self):
        destination = self.directory / "selected.json"
        original_summary = Summary(processed=12, matched=7)
        export_selected_report(str(destination), Config(), original_summary, [self.result], self.warnings, 12)
        data = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(data["export_scope"], {"kind": "selected", "count": 1, "total_result_count": 12})
        self.assertEqual(data["summary"]["processed"], 12)
        self.assertEqual(data["summary"]["matched"], 7)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["warnings"], self.warnings)
        self.assertEqual(original_summary.processed, 12)

    def test_selected_csv_task_row_contains_export_scope(self):
        destination = self.directory / "selected.csv"
        export_selected_report(str(destination), Config(), Summary(processed=12), [self.result], [], 12)
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        task = next(row for row in rows if row["记录类型"] == "TASK")
        details = json.loads(task["详细数据"])
        self.assertEqual(details["export_scope"], {"kind": "selected", "count": 1, "total_result_count": 12})
        self.assertEqual(json.loads(task["任务摘要"])["processed"], 12)

    def test_all_export_output_remains_without_selection_scope(self):
        destination = self.directory / "all.json"
        export_report(str(destination), Config(), Summary(), [self.result], [])
        self.assertNotIn("export_scope", json.loads(destination.read_text(encoding="utf-8")))

    def test_invalid_selection_metadata_preserves_existing_report(self):
        destination = self.directory / "existing.json"
        destination.write_text("original", encoding="utf-8")
        for total in (-1, 0, True, "12"):
            with self.assertRaises(ValueError):
                export_selected_report(str(destination), Config(), Summary(), [self.result], [], total)
        self.assertEqual(destination.read_text(encoding="utf-8"), "original")

    def test_report_cannot_overwrite_an_original_csv_file(self):
        original = self.directory / "原始资料.csv"
        original.write_text("人工原始资料,必须保留", encoding="utf-8")
        record = FileResult(path=str(original))
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            export_report(str(original), Config(), Summary(), [record], [])
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            export_selected_report(str(original), Config(), Summary(), [record], [], 12)
        self.assertEqual(original.read_text(encoding="utf-8"), "人工原始资料,必须保留")

    def test_destination_helper_protects_all_scan_files_outside_selected_subset(self):
        unselected = self.directory / "未选中的资料.json"
        unselected.write_text("artificial original", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            validate_report_destination(str(unselected), [str(unselected)])
        self.assertEqual(unselected.read_text(encoding="utf-8"), "artificial original")

    def test_destination_aliases_are_rejected(self):
        original = self.directory / "original.csv"
        original.write_text("artificial original", encoding="utf-8")
        spelling_alias = self.directory / "other" / ".." / original.name
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            validate_report_destination(str(spelling_alias), [str(original)])
        if os.name == "nt":
            with self.assertRaisesRegex(ValueError, "不能覆盖"):
                validate_report_destination(str(original).upper(), [str(original)])

    def test_samefile_catches_existing_hardlink_alias(self):
        original = self.directory / "original.csv"
        alias = self.directory / "alias.csv"
        original.write_text("artificial original", encoding="utf-8")
        try:
            os.link(str(original), str(alias))
        except OSError:
            self.skipTest("Filesystem does not allow hardlink fixtures")
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            validate_report_destination(str(alias), [str(original)])
        self.assertEqual(original.read_text(encoding="utf-8"), "artificial original")

    def test_new_report_destination_is_allowed_without_creating_file(self):
        destination = self.directory / "new-report.json"
        validate_report_destination(str(destination), [str(self.directory / "missing-original.csv")])
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
