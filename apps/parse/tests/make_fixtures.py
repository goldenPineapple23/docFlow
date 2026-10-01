"""
Writes the parse service's committed fixture files (Stage 3c).

Run from `apps/parse` with an environment that has the builders' libraries
(the worker's venv has them all):

    ../worker/.venv/Scripts/python.exe -m tests.make_fixtures

Why committed files rather than built at test time (as the worker's tests
did): the same bytes must reach this machine, CI's production image and
Fly staging, so that test D2 (the service answers exactly what today's
parser answered) compares like with like, and so every fixture has a real,
fixed SHA-256. The builders stay beside them, so every file's content is
readable and can be regenerated.

All content is fictional (CLAUDE.md Section 0 rule 4): the "Acme's Test
Coffee House" order the golden fixture uses.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from tests import fixture_builders as fb

HERE = Path(__file__).resolve().parent / "fixtures"
POSITIVE = HERE / "positive"
TABLES = HERE / "tables"
HOSTILE = HERE / "hostile"


def _image(fmt: str, **save) -> bytes:
    buf = io.BytesIO()
    image = fb._po_image(900, 500)
    if fmt == "GIF":
        image = image.convert("P")
    image.save(buf, format=fmt, **save)
    return buf.getvalue()


def _scanned_pdf() -> bytes:
    """A page of pixels with no text layer: what a desktop scanner emits."""
    buf = io.BytesIO()
    fb._po_image(1240, 1754).save(buf, format="PDF", resolution=150)
    return buf.getvalue()


def _xlsm() -> bytes:
    """A real macro-enabled workbook's content type (no macro inside)."""
    src = zipfile.ZipFile(io.BytesIO(fb.build_xlsx()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                    b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                )
            dst.writestr(item, data)
    return out.getvalue()


def _doc() -> bytes:
    """A genuine Word 97-2003 file, written by LibreOffice from the .docx."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))
    from app.conversion import find_libreoffice  # the worker's today, before the move

    binary = find_libreoffice()
    if binary is None:
        raise SystemExit("LibreOffice is needed to write po.doc")
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "po.docx"
        source.write_bytes(fb.build_docx())
        profile = Path(work) / "profile"
        subprocess.run(
            [
                binary,
                "--headless",
                "--norestore",
                f"-env:UserInstallation=file:///{profile.as_posix().lstrip('/')}",
                "--convert-to",
                "doc:MS Word 97",
                "--outdir",
                work,
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return (Path(work) / "po.doc").read_bytes()


def positive() -> dict[str, bytes]:
    text = fb.PO_TEXT.encode("utf-8")
    return {
        # Tier 1
        "po.txt": text,
        "po.csv": text,
        "po.md": text,
        "po.html": f"<html><body><pre>{fb.PO_TEXT}</pre></body></html>".encode("utf-8"),
        "po.pdf": fb.build_pdf([fb.PO_TEXT.replace("\n", " ")]),
        "po-scanned.pdf": _scanned_pdf(),
        "po.docx": fb.build_docx(),
        "po.xlsx": fb.build_xlsx(),
        "po.xlsm": _xlsm(),
        "po.rtf": fb.build_rtf(),
        "po.eml": fb.build_eml([("po.xlsx", "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 fb.build_xlsx())]),
        "po.png": _image("PNG"),
        "po.jpg": _image("JPEG", quality=90),
        "po.webp": _image("WEBP", quality=90),
        "po.gif": _image("GIF"),
        # Tier 2
        "po.doc": _doc(),
        "po.xls": fb.build_xls(),
        "po.tif": fb.build_tiff(pages=2),
        "po.heic": fb.build_heic(),
        "po.msg": fb.build_msg(attachments=[("po.pdf", fb.build_pdf([fb.PO_TEXT.replace("\n", " ")]))]),
        "po.odt": fb.build_odt(),
        "po.ods": fb.build_ods(),
    }


CATALOG_ROWS = [
    ["SKU", "Description", "UOM", "Price"],
    ["CF-1001", "Colombian Whole Bean 5lb", "CS", 47.5],
    ["CF-2210", "Ethiopian Yirgacheffe 5lb", "CS", 62],
    ["SY-0045", "Vanilla Syrup 750ml", "EA", 8.25],
    ["CUP-12", "12oz Paper Cups (1000ct)", "BOX", 54],
]


def tables() -> dict[str, bytes]:
    import openpyxl
    import xlwt

    csv_text = "\n".join(",".join(str(c) for c in row) for row in CATALOG_ROWS) + "\n"
    wb = openpyxl.Workbook()
    for row in CATALOG_ROWS:
        wb.active.append(row)
    xlsx = io.BytesIO()
    wb.save(xlsx)
    book = xlwt.Workbook()
    sheet = book.add_sheet("Catalog")
    for r, row in enumerate(CATALOG_ROWS):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    xls = io.BytesIO()
    book.save(xls)
    return {
        "catalog.csv": csv_text.encode("utf-8"),
        "catalog.txt": csv_text.replace(",", "\t").encode("utf-8"),
        "catalog.xlsx": xlsx.getvalue(),
        "catalog.xls": xls.getvalue(),
    }


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def hostile() -> dict[str, bytes]:
    from docflow_core import file_types

    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 512
    return {
        # Section 7.11's required rejections
        "bomb-entries.docx": _zip({f"file_{i}.txt": b"x" for i in range(2500)}),
        "bomb-ratio.docx": _zip({"word/document.xml": b"\x00" * (5 * 1024 * 1024)}),
        "xxe.docx": _zip(
            {
                "[Content_Types].xml": b"<Types/>",
                "word/document.xml": b"<?xml version='1.0'?>\n"
                b"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>\n"
                b"<w:document>&xxe;</w:document>",
            }
        ),
        "oversized.tif": fb.build_tiff(pages=1, size=(file_types.MAX_IMAGE_DIMENSION + 1, 8)),
        "pages-500.pdf": fb.build_pdf([f"page {i}" for i in range(500)]),
        "exe-renamed.pdf": b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 512,
        "password.pdf": fb.build_pdf_with_encrypt_trailer(),
        "po-in-zip.zip": _zip({"po.pdf": fb.build_pdf([fb.PO_TEXT.replace("\n", " ")])}),
        # A malformed file of each Tier 2 format
        "malformed.doc": ole,
        "malformed.xls": ole,
        "malformed.msg": ole,
        "malformed.tif": b"II\x2a\x00" + b"\xff" * 512,
        "malformed.heic": b"\x00\x00\x00\x18ftypheic" + b"\xff" * 512,
        "malformed.odt": b"PK\x03\x04 truncated opendocument",
        "malformed.ods": b"PK\x03\x04 truncated opendocument",
    }


def write(directory: Path, files: dict[str, bytes]) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    print(f"{directory.name}: {len(files)} files")


def main() -> None:
    write(POSITIVE, positive())
    write(TABLES, tables())
    write(HOSTILE, hostile())


if __name__ == "__main__":
    main()
