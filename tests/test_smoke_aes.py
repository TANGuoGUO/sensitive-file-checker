"""The packaged smoke test must cover AES without reading user documents."""
from pathlib import Path

import pytest
from pypdf import PdfReader

import app
from extractors import ExtractionError, _extract_pdf_direct
from scanner import FileResult, Match


@pytest.fixture(scope="module")
def smoke_fixtures(tmp_path_factory):
    directory = tmp_path_factory.mktemp("artificial-aes-smoke")
    app._create_smoke_fixtures(directory)
    return directory


@pytest.mark.parametrize("key,filename,algorithm,password,readable", app._SMOKE_PDF_CASES)
def test_smoke_pdf_fixtures_cover_real_encryption_variants(smoke_fixtures, key, filename, algorithm, password, readable):
    path = smoke_fixtures / filename
    reader = PdfReader(path)
    assert reader.is_encrypted == (algorithm is not None)
    if algorithm:
        # Explicit security-handler revisions ensure the two AES cases cannot
        # accidentally become the RC4 default used by earlier smoke tests.
        revision = reader.trailer["/Encrypt"]["/R"]
        assert revision == {"RC4-128": 3, "AES-128": 4, "AES-256": 6}[algorithm]
    if readable:
        result = _extract_pdf_direct(str(path), None)
        assert result.status == "complete"
        assert any("ALPHA" in block.text for block in result.blocks)
    else:
        with pytest.raises(ExtractionError, match="已加密"):
            _extract_pdf_direct(str(path), None)


def test_smoke_checks_are_independent_of_file_discovery_order(smoke_fixtures):
    records = []
    for _key, filename, _algorithm, _password, readable in app._SMOKE_PDF_CASES:
        records.append(FileResult(
            path=str(smoke_fixtures / filename), matched=readable,
            content_status="complete" if readable else "unreadable",
            matches=[Match("ALPHA", "content", "第 1 页", "Artificial ALPHA")] if readable else [],
        ))
    assert all(app._smoke_pdf_checks(list(reversed(records))).values())
    records[-1].content_status = "complete"
    assert not app._smoke_pdf_checks(records)["aes256_password_protected"]


def test_smoke_has_six_artificial_files_and_missing_cases_fail(smoke_fixtures):
    assert len(list(smoke_fixtures.iterdir())) == 6
    assert (smoke_fixtures / "人工测试.txt").read_text(encoding="utf-8").startswith("这是一份人工生成")
    assert not any(app._smoke_pdf_checks([]).values())
