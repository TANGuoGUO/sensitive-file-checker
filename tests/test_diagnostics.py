import json
import sys
from pathlib import Path

import diagnostics
import launcher


def test_redaction_and_no_general_environment_dump(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\synthetic")
    monkeypatch.setenv("SFC_TEST_SECRET", "never include this")
    assert diagnostics.redact(r"Failed C:\Users\synthetic\app.dll") == r"Failed %USERPROFILE%\app.dll"
    report = diagnostics.collect_report()
    serialized = json.dumps(report)
    assert "SFC_TEST_SECRET" not in serialized and "never include this" not in serialized
    assert "PKG" in report["bootloader_limit"]


def test_log_override_and_structured_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("SFC_LOG_DIR", str(tmp_path))
    directory = diagnostics.create_log_dir()
    assert directory.parent == tmp_path
    diagnostics.StartupLog(directory).stage("synthetic", success=True)
    assert json.loads((directory / "startup.log").read_text(encoding="utf-8"))["stage"] == "synthetic"


def test_diagnostics_interface_writes_report_and_exit_status(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "run_child", lambda *args, **kwargs: {"status": "failed", "exit_code": 123})
    assert launcher.main(["--diagnostics", "--output", str(tmp_path)]) == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["probe"] == {"status": "failed", "exit_code": 123}
    assert "system32_icu" in report


def test_existing_cli_arguments_are_forwarded_without_qt_import(tmp_path, monkeypatch):
    monkeypatch.setenv("SFC_LOG_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(diagnostics, "run_child", lambda arguments, directory: (called.append(arguments) or {"status": "passed", "exit_code": 0}))
    assert launcher.main(["--demo", "--screenshot", "synthetic.png"]) == 0
    assert called == [["--gui-worker", "--demo", "--screenshot", "synthetic.png"]]


def test_source_command_and_timeout_status(tmp_path, monkeypatch):
    assert diagnostics.executable_command(["--probe-qt"])[-1] == "--probe-qt"
    monkeypatch.setattr(diagnostics, "run_child", lambda *args, **kwargs: {"status": "timeout", "exit_code": -1, "timeout_seconds": 30})
    assert launcher.main(["--diagnostics", "--output", str(tmp_path)]) == 2
