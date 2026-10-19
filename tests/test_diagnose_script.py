"""Validate the launcher wrapper without executing the application or user files."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def test_diagnostic_scripts_are_ascii_and_preserve_batch_exit_code():
    powershell = (ROOT / "diagnose.ps1").read_bytes().decode("ascii")
    batch = (ROOT / "diagnose.cmd").read_bytes().decode("ascii")
    assert '"%~dp0diagnose.ps1"' in batch
    assert 'set "DIAG_EXIT=%ERRORLEVEL%"' in batch
    assert "exit /b %DIAG_EXIT%" in batch
    assert "Start-Process -FilePath $ExecutablePath" in powershell
    assert 'Save-LauncherReport\n$ScriptExitCode' in powershell


@pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell is required")
@pytest.mark.parametrize("invalid_executable", [False, True])
def test_missing_or_rejected_executable_still_writes_report(invalid_executable):
    # Temporary files and subprocess output remain inside this workspace.
    with tempfile.TemporaryDirectory(prefix="diagnose-fixture-", dir=Path(__file__).parent) as folder:
        directory = Path(folder)
        shutil.copy2(ROOT / "diagnose.ps1", directory / "diagnose.ps1")
        if invalid_executable:
            (directory / "SensitiveFileChecker.exe").write_bytes(b"artificial invalid executable")
        output = directory / "diagnostic-result"
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(directory / "diagnose.ps1"), "-OutputDirectory", str(output)],
            cwd=directory, capture_output=True, timeout=30,
        )
        assert result.returncode == 1
        report = (output / "launcher-report.txt").read_text(encoding="utf-8-sig")
        assert "OSVersionFallback=" in report
        assert "Is64BitOperatingSystem=" in report
        assert "PATH=" not in report
        assert str(directory) not in report
        if invalid_executable:
            assert "ExecutableSHA256=" in report
            assert "Status=launch_failed" in report
            assert "LaunchError.Type=" in report
        else:
            assert "Status=executable_missing" in report


@pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell is required")
@pytest.mark.parametrize("matching_hash", [True, False])
def test_renamed_executable_checks_original_release_manifest_entry(matching_hash):
    with tempfile.TemporaryDirectory(prefix="diagnose-fixture-", dir=Path(__file__).parent) as folder:
        directory = Path(folder)
        shutil.copy2(ROOT / "diagnose.ps1", directory / "diagnose.ps1")
        payload = b"artificial invalid executable for checksum test"
        (directory / "scan.exe").write_bytes(payload)
        expected = hashlib.sha256(payload if matching_hash else b"different release").hexdigest()
        (directory / "SHA256SUMS.txt").write_text(expected + "  SensitiveFileChecker.exe\n", encoding="ascii")
        output = directory / "diagnostic-result"
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(directory / "diagnose.ps1"), "-OutputDirectory", str(output)],
            cwd=directory, capture_output=True, timeout=30,
        )
        assert result.returncode == 1  # Artificial bytes are deliberately not executable.
        report = (output / "launcher-report.txt").read_text(encoding="utf-8-sig")
        assert "ExecutableName=scan.exe" in report
        assert "ReleaseExpectedSHA256=" + expected.upper() in report
        assert "ReleaseIntegrity=" + ("matched" if matching_hash else "mismatch") in report
        assert "Status=launch_failed" in report
        assert (output / "bootloader-stderr.log").is_file()
        assert (output / "bootloader-stdout.log").is_file()
        if not matching_hash:
            assert "文件与发行版不一致，可能下载/传输不完整" in report
