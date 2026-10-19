import csv
import json
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

from reports import export_report


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


if __name__ == "__main__":
    unittest.main()
