"""Regression coverage for AES PDF text extraction through real spawn workers."""
import hashlib
from pathlib import Path
import tempfile

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import extractors
from scanner import ScanConfig, run_scan


@pytest.fixture
def aes_fixture_folder():
    # Keep every generated PDF inside this workspace, never in user document roots.
    with tempfile.TemporaryDirectory(prefix="pdf-aes-fixture-", dir=Path(__file__).parent) as directory:
        yield Path(directory)


def _write_aes_pdf(path, algorithm, user_password):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Confidential ALPHA) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    # A nonempty owner password ensures an empty user password does not disable
    # encryption; algorithm selection must require the installed AES backend.
    writer.encrypt(user_password=user_password, owner_password="artificial-owner-password",
                   algorithm=algorithm)
    with path.open("wb") as handle:
        writer.write(handle)
    # Verify these are real AES fixtures, rather than the library's RC4 default.
    reader = PdfReader(path)
    assert reader.is_encrypted
    crypt_filter = reader.trailer["/Encrypt"]["/CF"]["/StdCF"]["/CFM"]
    assert crypt_filter == ("/AESV2" if algorithm == "AES-128" else "/AESV3")


@pytest.mark.parametrize(
    "algorithm,user_password,readable",
    [("AES-128", "", True), ("AES-256", "", True),
     ("AES-256", "artificial-user-password", False)],
    ids=["aes128-empty-user-password", "aes256-empty-user-password", "aes256-password-required"],
)
def test_aes_pdf_scans_in_spawn_worker_without_changing_source(
        aes_fixture_folder, monkeypatch, algorithm, user_password, readable):
    path = aes_fixture_folder / "artificial-encrypted.pdf"
    _write_aes_pdf(path, algorithm, user_password)
    original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original_mtime = path.stat().st_mtime_ns
    contexts = []
    original_get_context = extractors.multiprocessing.get_context

    def record_context(method=None):
        contexts.append(method)
        return original_get_context(method)

    def forbidden_parent_extraction(*args, **kwargs):
        pytest.fail("PDF parsing must run in a spawned child, not the scanner process")

    monkeypatch.setattr(extractors.multiprocessing, "get_context", record_context)
    # Spawn imports a clean extractors module, so this also detects accidentally
    # replacing process isolation with a direct extraction in the parent.
    monkeypatch.setattr(extractors, "_extract_pdf_direct", forbidden_parent_extraction)
    events = []
    summary = run_scan(ScanConfig(roots=(str(aes_fixture_folder),), keywords=("ALPHA",),
                                  match_name=False, extensions=(".pdf",)),
                       on_event=events.append)
    records = [event["result"] for event in events if event["type"] == "result"]
    assert "spawn" in contexts
    assert summary.state == "completed" and summary.processed == 1
    assert len(records) == 1
    record = records[0]
    assert record.path == str(path)
    if readable:
        assert record.matched and record.matched_keywords == ("ALPHA",)
        assert record.content_status == "complete" and record.parser == "pypdf"
        assert record.total_matches == 1
        assert record.matches[0].location == "第 1 页"
        assert "Confidential ALPHA" in record.matches[0].snippet
        assert summary.matched == 1 and summary.content_complete == 1
        assert summary.content_unreadable == 0
    else:
        assert not record.matched and not record.matches
        assert record.content_status == "unreadable"
        assert any("加密" in issue and "密码" in issue for issue in record.issues)
        assert summary.matched == 0 and summary.content_unreadable == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_digest
    assert path.stat().st_mtime_ns == original_mtime
    assert list(aes_fixture_folder.iterdir()) == [path]
