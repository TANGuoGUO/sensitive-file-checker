"""Fail the build early if its PDF AES backend cannot read encrypted text."""
from io import BytesIO
import json
from pathlib import Path
import sys


def verify_pdf_crypto():
    from pypdf import PdfReader, PdfWriter
    from pypdf._crypt_providers import crypt_provider
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    if crypt_provider[0] != "cryptography":
        raise RuntimeError("The cryptography PDF backend is required; install requirements.txt.")
    checked = []
    for algorithm in ("AES-128", "AES-256"):
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 10 100 Td (SFC AES build check) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        writer.encrypt(user_password="", owner_password="artificial-build-owner", algorithm=algorithm)
        buffer = BytesIO()
        writer.write(buffer)
        reader = PdfReader(BytesIO(buffer.getvalue()))
        if not reader.is_encrypted or not reader.decrypt(""):
            raise RuntimeError(algorithm + " PDF could not be decrypted.")
        if len(reader.pages) != 1 or "SFC AES build check" not in reader.pages[0].extract_text():
            raise RuntimeError(algorithm + " PDF text round-trip failed.")
        checked.append(algorithm)
    return {"crypto_provider": list(crypt_provider), "pdf_round_trip": checked}


def main():
    vendor = Path(__file__).resolve().parent / "vendor"
    if vendor.is_dir():
        sys.path.insert(0, str(vendor))
    try:
        import PySide6
        import PyInstaller
        import pythoncom
        import pywintypes
        import win32com.client
        result = verify_pdf_crypto()
    except Exception as exc:
        print("Build dependency check failed: " + str(exc), file=sys.stderr)
        return 1
    print("Build dependencies are available: " + json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
