"""Regression checks for failing closed when the PDF AES backend is missing."""
import sys
from types import ModuleType

import pytest

import check_build_dependencies


def test_pdf_crypto_rejects_fallback_before_creating_documents(monkeypatch):
    import pypdf
    import pypdf._crypt_providers as providers

    monkeypatch.setattr(providers, "crypt_provider", ("local_crypt_fallback", "0.0.0"))
    monkeypatch.setattr(pypdf, "PdfWriter", lambda: pytest.fail("Fallback provider must fail before PDF creation"))
    with pytest.raises(RuntimeError, match="cryptography PDF backend is required"):
        check_build_dependencies.verify_pdf_crypto()


def test_main_returns_failure_even_when_dependencies_import(monkeypatch, capsys):
    # Stub the import-only checks so this failure test requires no Qt, COM,
    # installer execution, cryptography installation, or network access.
    for name in ("PySide6", "PyInstaller", "pythoncom", "pywintypes"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    win32com = ModuleType("win32com")
    win32com.__path__ = []
    win32com.client = ModuleType("win32com.client")
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", win32com.client)
    monkeypatch.setattr(sys, "path", list(sys.path))
    calls = []

    def unavailable_aes():
        calls.append(True)
        raise RuntimeError("synthetic AES provider unavailable")

    monkeypatch.setattr(check_build_dependencies, "verify_pdf_crypto", unavailable_aes)
    assert check_build_dependencies.main() != 0
    assert calls == [True]
    output = capsys.readouterr()
    assert "Build dependency check failed" in output.err
    assert "synthetic AES provider unavailable" in output.err
    assert "Build dependencies are available" not in output.out
