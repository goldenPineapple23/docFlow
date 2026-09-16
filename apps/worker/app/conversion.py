"""
Tier 2 conversion (CLAUDE.md Section 7.11's middle row): `.doc`, `.xls`,
`.tif`/`.tiff`, `.heic`/`.heif`, `.msg`, `.odt`/`.ods` are converted to a
Tier 1 format and then parsed normally.

"Tier 2 is where the risk is -- treat conversion as parsing." Everything in
this module therefore lives in the isolated worker process and nowhere
else: `apps/api` never imports it, and the libraries it uses (Pillow,
pillow-heif, olefile, xlrd, and LibreOffice when present) are installed in
the worker's environment only. Conversion is subject to every limit in
Section 7.11 -- the size cap and magic-byte/zip checks run on the input
before a converter touches it (the caller's
`file_types.validate_upload`), the page-count and pixel-dimension caps run
before any decode, and the converter's *output* is re-validated as a Tier 1
file before it can reach extraction, because a converter's output is still
bytes we didn't write.

A conversion failure is a clean, catalog-coded `ConversionError` that the
task turns into `failed` -- never a crash, never a partial write, and the
worker is healthy afterwards.

No active content, ever (Section 7.11): nothing here executes a macro, an
embedded object, OLE automation, or JavaScript. The legacy binary formats
are handled by readers that only read (`xlrd` reads BIFF cell records and
never touches a VBA stream; `olefile` reads named streams out of an OLE
container and never executes one), and the LibreOffice path converts with
an isolated, throwaway user profile whose macro security is untouched
default ("never run macros in a converted document"), then discards the
converted file's own container by re-parsing it with python-docx, which has
no macro execution at all.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from docflow_core import file_types
from docflow_core.file_types import FileType

logger = logging.getLogger(__name__)

# ── Named limits (CLAUDE.md Section 7.11) ──────────────────────────────────
# Wall-clock ceiling for one external converter invocation. A converter that
# hangs on a malformed file is killed and the document fails cleanly.
LIBREOFFICE_TIMEOUT_SECONDS = 120

# Converted images are normalized for the vision path: capped on the long
# edge and re-encoded as JPEG. 1568px is the largest edge the extraction
# model uses before it downsamples anyway, so this loses nothing the model
# would have seen while keeping a converted 600-dpi fax page comfortably
# under MAX_FILE_SIZE_BYTES. The original file is never modified -- it stays
# in storage exactly as received.
IMAGE_MAX_EDGE_PIXELS = 1568
IMAGE_JPEG_QUALITY = 90

# Where LibreOffice lives when it is installed. Checked in order; the
# `LIBREOFFICE_PATH` setting overrides all of them.
LIBREOFFICE_CANDIDATE_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


class ConversionError(Exception):
    """
    A Tier 2 file that could not be converted. Always carries an error
    catalog code -- no user-facing failure text is ever built outside
    `docflow_core.errors` (CLAUDE.md Section 7.16.5).
    """

    def __init__(self, error_code: str, detail: str):
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


@dataclass(frozen=True)
class PreparedArtifact:
    """One Tier 1 artifact ready for `build_content_blocks`."""

    file_type: FileType
    content: bytes
    label: str  # provenance for logs only -- never document content


# ── Shared helpers ─────────────────────────────────────────────────────────


def format_cell_value(value: object) -> str:
    """
    Render a spreadsheet cell as text for the model without ever letting a
    float's repr become the number the model reads (CLAUDE.md Section 7.1:
    "Money is never a float"). Legacy `.xls` stores every number as a binary
    double, so the value arrives as a float no matter what: it is converted
    through its shortest round-trip repr into `Decimal` and rendered in
    plain (never scientific) notation, so 47.5 renders "47.5" and never
    "47.500000000000004" or "4.75E+1".
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        try:
            decimal_value = Decimal(repr(value)).normalize()
        except InvalidOperation:
            return str(value)
        if decimal_value == decimal_value.to_integral_value():
            decimal_value = decimal_value.quantize(Decimal(1))
        return format(decimal_value, "f")
    return str(value)


def _sanitize_label(name: str) -> str:
    """
    Filenames are untrusted (CLAUDE.md Section 7.11). Nothing derived from a
    document is ever used in a path or a shell command; this produces a safe
    label for logs and for the extension the re-validation step reads.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name.strip())[-80:]
    return cleaned or "attachment"


def _extension_of(name: str) -> str:
    idx = name.rfind(".")
    return name[idx:].lower() if idx != -1 else ""


# ── Image converters: TIFF and HEIC ────────────────────────────────────────


def _open_image(content: bytes):
    from PIL import Image

    # Explicit, deliberate decompression-bomb guard rather than Pillow's
    # default (CLAUDE.md Section 7.11: "pixel-dimension caps before decode").
    Image.MAX_IMAGE_PIXELS = file_types.MAX_IMAGE_PIXELS
    try:
        return Image.open(BytesIO(content))
    except Exception as exc:
        raise ConversionError(
            "DOC-017", f"Image could not be opened: {type(exc).__name__}"
        ) from exc


def _guard_dimensions(width: int, height: int) -> None:
    if width > file_types.MAX_IMAGE_DIMENSION or height > file_types.MAX_IMAGE_DIMENSION:
        raise ConversionError(
            "DOC-018", f"Image is {width}x{height}px, beyond the per-side dimension cap."
        )
    if width * height > file_types.MAX_IMAGE_PIXELS:
        raise ConversionError(
            "DOC-018", f"Image is {width * height} pixels, beyond the total pixel cap."
        )


def _frame_to_jpeg(image) -> bytes:
    from PIL import Image

    _guard_dimensions(*image.size)
    frame = image.convert("RGB")
    longest = max(frame.size)
    if longest > IMAGE_MAX_EDGE_PIXELS:
        scale = IMAGE_MAX_EDGE_PIXELS / longest
        frame = frame.resize(
            (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
            Image.Resampling.LANCZOS,
        )
    buffer = BytesIO()
    frame.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buffer.getvalue()


def convert_tiff(content: bytes) -> list[tuple[bytes, str]]:
    """
    Multi-page TIFF is treated like a multi-page PDF, page-count cap included
    (CLAUDE.md Section 7.11). Each page becomes its own Tier 1 image.
    """
    image = _open_image(content)
    try:
        page_count = getattr(image, "n_frames", 1)
        if page_count > file_types.MAX_DOCUMENT_PAGES:
            raise ConversionError(
                "DOC-016",
                f"TIFF has {page_count} pages (limit {file_types.MAX_DOCUMENT_PAGES}).",
            )
        pages: list[tuple[bytes, str]] = []
        for index in range(page_count):
            try:
                image.seek(index)
            except EOFError:
                break
            pages.append((_frame_to_jpeg(image), f"page-{index + 1}.jpg"))
        if not pages:
            raise ConversionError("DOC-017", "TIFF contained no readable pages.")
        return pages
    finally:
        image.close()


def convert_heic(content: bytes) -> list[tuple[bytes, str]]:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ConversionError("DOC-017", "HEIC support is not installed in this worker.") from exc

    image = _open_image(content)
    try:
        return [(_frame_to_jpeg(image), "photo.jpg")]
    finally:
        image.close()


# ── Legacy Excel (.xls) ────────────────────────────────────────────────────


def convert_xls(content: bytes) -> list[tuple[bytes, str]]:
    """
    Legacy `.xls` is read directly by `xlrd` (a pure-Python BIFF reader) --
    no LibreOffice needed, nothing executed. See DECISIONS.md.
    """
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ConversionError("DOC-017", "Legacy Excel support is not installed.") from exc

    try:
        book = xlrd.open_workbook(file_contents=content)
    except Exception as exc:
        raise ConversionError(
            "DOC-017", f"Legacy Excel workbook could not be read: {type(exc).__name__}"
        ) from exc

    lines: list[str] = []
    for sheet in book.sheets():
        for row_index in range(sheet.nrows):
            values = [format_cell_value(sheet.cell_value(row_index, c)) for c in range(sheet.ncols)]
            if any(value != "" for value in values):
                lines.append(", ".join(values))
    text = "\n".join(lines)
    if not text.strip():
        raise ConversionError("DOC-017", "Legacy Excel workbook contained no readable cells.")
    return [(text.encode("utf-8"), "converted.txt")]


# ── OpenDocument (.odt / .ods) ─────────────────────────────────────────────


def convert_odf(content: bytes) -> list[tuple[bytes, str]]:
    """
    OpenDocument files are zip + XML, so they are read directly in Python --
    no LibreOffice needed. The zip has already passed the Section 7.11
    decompression-bomb, path-traversal, encryption and XML-entity checks in
    `file_types`; the XML itself is parsed with `defusedxml`, which disables
    DTD processing and external entity resolution outright.
    """
    import zipfile

    from defusedxml.ElementTree import fromstring

    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml_bytes = archive.read("content.xml")
    except Exception as exc:
        raise ConversionError(
            "DOC-017", f"OpenDocument content could not be read: {type(exc).__name__}"
        ) from exc

    try:
        root = fromstring(xml_bytes)
    except Exception as exc:
        raise ConversionError(
            "DOC-017", f"OpenDocument XML could not be parsed: {type(exc).__name__}"
        ) from exc

    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    table_ns = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    cell_tags = (f"{table_ns}table-cell", f"{table_ns}covered-table-cell")

    def element_text(element) -> str:
        return " ".join("".join(element.itertext()).split()).strip()

    lines: list[str] = []

    def walk(element) -> None:
        """
        Depth-first, but a table row is rendered as one comma-joined line
        and not descended into -- so a paragraph inside a cell appears once,
        in its row, where a purchase order's line items actually live.
        """
        for child in element:
            if child.tag == f"{table_ns}table-row":
                cells = [element_text(cell) for cell in child if cell.tag in cell_tags]
                if any(cells):
                    lines.append(", ".join(cells))
                continue
            if child.tag in (f"{text_ns}p", f"{text_ns}h"):
                value = element_text(child)
                if value:
                    lines.append(value)
                continue
            walk(child)

    walk(root)
    text = "\n".join(lines)
    if not text.strip():
        raise ConversionError("DOC-017", "OpenDocument file contained no readable text.")
    return [(text.encode("utf-8"), "converted.txt")]


# ── Legacy Word (.doc) via LibreOffice ─────────────────────────────────────


def find_libreoffice() -> str | None:
    """
    Locates the LibreOffice binary, or returns None when it isn't installed.
    A hosted conversion service is forbidden (CLAUDE.md Section 7.10/7.11 --
    it would send a customer's document to a third party), so the only
    supported answer is a local install; when it is missing, `.doc`
    conversion fails cleanly with a catalog code rather than silently
    degrading. See SETUP.md and DECISIONS.md.
    """
    from docflow_core.config import get_settings

    configured = (get_settings().libreoffice_path or "").strip()
    if configured and Path(configured).exists():
        return configured

    on_path = shutil.which("soffice") or shutil.which("soffice.exe")
    if on_path:
        return on_path

    for candidate in LIBREOFFICE_CANDIDATE_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def convert_with_libreoffice(content: bytes, source_suffix: str, target: str) -> bytes:
    """
    Runs one LibreOffice headless conversion in a throwaway directory with a
    throwaway user profile, a hard wall-clock limit, no stdin, and a
    server-side-generated filename (the untrusted original name never
    reaches the command line). Anything other than a clean, produced output
    file is a catalog-coded ConversionError.
    """
    binary = find_libreoffice()
    if binary is None:
        raise ConversionError(
            "DOC-017",
            "LibreOffice is not installed on this worker, so legacy .doc conversion "
            "is unavailable (see SETUP.md).",
        )

    with tempfile.TemporaryDirectory(prefix="docflow-convert-") as workdir:
        work = Path(workdir)
        source = work / f"input{source_suffix}"
        source.write_bytes(content)
        profile = work / "profile"
        outdir = work / "out"
        outdir.mkdir()

        command = [
            binary,
            "--headless",
            "--invisible",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation=file:///{profile.as_posix().lstrip('/')}",
            "--convert-to",
            target,
            "--outdir",
            str(outdir),
            str(source),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=LIBREOFFICE_TIMEOUT_SECONDS,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                "DOC-017",
                f"Converter exceeded its {LIBREOFFICE_TIMEOUT_SECONDS}s limit and was stopped.",
            ) from exc
        except OSError as exc:
            raise ConversionError("DOC-017", f"Converter could not be started: {type(exc).__name__}") from exc

        produced = sorted(p for p in outdir.iterdir() if p.is_file())
        if completed.returncode != 0 or not produced:
            # Never surface the converter's own stderr to a tenant
            # (Section 7.16.5): the exit code is all that is recorded here.
            raise ConversionError(
                "DOC-017", f"Converter exited with status {completed.returncode} and no output file."
            )
        return produced[0].read_bytes()


def convert_doc(content: bytes) -> list[tuple[bytes, str]]:
    return [(convert_with_libreoffice(content, ".doc", "docx"), "converted.docx")]


# ── Outlook .msg and raw .eml unwrapping ───────────────────────────────────

# MAPI property tags inside an OLE .msg, as stream-name suffixes.
_MSG_SUBJECT = "0037"
_MSG_BODY = "1000"
_MSG_SENDER_EMAIL = "0C1F"
_MSG_ATTACH_LONG_FILENAME = "3707"
_MSG_ATTACH_FILENAME = "3704"
_MSG_ATTACH_DATA = "3701"


def _decode_msg_stream(raw: bytes, type_code: str) -> str:
    if type_code == "001F":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("cp1252", errors="replace")


def _read_msg_property(ole, path_prefix: list[str], prop_id: str) -> tuple[bytes, str] | None:
    for type_code in ("001F", "001E", "0102"):
        stream = path_prefix + [f"__substg1.0_{prop_id}{type_code}"]
        if ole.exists("/".join(stream)):
            return ole.openstream(stream).read(), type_code
    return None


def unwrap_msg(content: bytes) -> list[tuple[bytes, str]]:
    """
    ".msg and .eml are unwrapped: the body is read as text and every
    attachment is re-validated from the top of this list" (CLAUDE.md
    Section 7.11). Parsed with `olefile`, a pure-Python reader of the OLE
    container's named streams -- it never executes anything, and no
    LibreOffice or Outlook is involved.
    """
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ConversionError("DOC-017", "Outlook .msg support is not installed.") from exc

    try:
        ole = olefile.OleFileIO(BytesIO(content))
    except Exception as exc:
        raise ConversionError(
            "DOC-017", f"Outlook message could not be opened: {type(exc).__name__}"
        ) from exc

    try:
        header_lines: list[str] = []
        for label, prop_id in (("Subject", _MSG_SUBJECT), ("From", _MSG_SENDER_EMAIL)):
            found = _read_msg_property(ole, [], prop_id)
            if found is not None:
                raw, type_code = found
                value = _decode_msg_stream(raw, type_code).strip()
                if value:
                    header_lines.append(f"{label}: {value}")

        body = ""
        found_body = _read_msg_property(ole, [], _MSG_BODY)
        if found_body is not None:
            body = _decode_msg_stream(*found_body).strip()

        artifacts: list[tuple[bytes, str]] = []
        message_text = "\n".join([*header_lines, "", body]).strip()
        if message_text:
            artifacts.append((message_text.encode("utf-8"), "message-body.txt"))

        storages = sorted(
            "/".join(entry)
            for entry in ole.listdir(streams=False, storages=True)
            if entry and entry[0].startswith("__attach_version1.0")
        )
        for storage in storages[: file_types.MAX_EMBEDDED_ATTACHMENTS]:
            prefix = storage.split("/")
            name_found = _read_msg_property(
                ole, prefix, _MSG_ATTACH_LONG_FILENAME
            ) or _read_msg_property(ole, prefix, _MSG_ATTACH_FILENAME)
            data_found = _read_msg_property(ole, prefix, _MSG_ATTACH_DATA)
            if data_found is None:
                # An attachment stored as an embedded message, not a file:
                # that is a second level of nesting, which Section 7.11
                # bounds out.
                logger.info("msg_attachment_skipped reason=no_file_data")
                continue
            filename = "attachment"
            if name_found is not None:
                filename = _decode_msg_stream(*name_found).strip("\x00 ") or "attachment"
            artifacts.append((data_found[0], _sanitize_label(filename)))

        if not artifacts:
            raise ConversionError("DOC-017", "Outlook message had no readable body or attachment.")
        return artifacts
    finally:
        ole.close()


def unwrap_eml(content: bytes) -> list[tuple[bytes, str]]:
    """
    Raw RFC 5322 mail, unwrapped with the standard library's own parser --
    no external entity resolution, no network, no active content. Same
    one-level bound as `.msg`.
    """
    import email
    from email import policy

    try:
        message = email.message_from_bytes(content, policy=policy.default)
    except Exception as exc:
        raise ConversionError("DOC-017", f"Email could not be parsed: {type(exc).__name__}") from exc

    header_lines = []
    for label in ("Subject", "From", "Date"):
        value = message.get(label)
        if value:
            header_lines.append(f"{label}: {value}")

    body = ""
    try:
        body_part = message.get_body(preferencelist=("plain", "html"))
        if body_part is not None:
            body = body_part.get_content()
    except Exception:  # a malformed part must not take the worker down
        body = ""

    artifacts: list[tuple[bytes, str]] = []
    message_text = "\n".join([*header_lines, "", str(body)]).strip()
    if message_text:
        artifacts.append((message_text.encode("utf-8"), "message-body.txt"))

    attachment_count = 0
    for part in message.iter_attachments():
        if attachment_count >= file_types.MAX_EMBEDDED_ATTACHMENTS:
            break
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not isinstance(payload, bytes) or not payload:
            continue
        artifacts.append((payload, _sanitize_label(part.get_filename() or "attachment")))
        attachment_count += 1

    if not artifacts:
        raise ConversionError("DOC-017", "Email had no readable body or attachment.")
    return artifacts


# ── The one entry point the task calls ─────────────────────────────────────

_CONVERTERS = {
    "convert_tiff": convert_tiff,
    "convert_heic": convert_heic,
    "convert_xls": convert_xls,
    "convert_odf": convert_odf,
    "convert_doc": convert_doc,
}

_UNWRAPPERS = {
    "convert_msg": unwrap_msg,
    "eml": unwrap_eml,
}


def prepare_artifacts(
    file_type: FileType, content: bytes, *, label: str = "document", depth: int = 0
) -> list[PreparedArtifact]:
    """
    Turns one validated upload into the list of Tier 1 artifacts extraction
    should see, converting or unwrapping as needed.

    Every produced artifact is re-validated through
    `file_types.validate_upload` before it is returned -- a converter's
    output is untrusted input like any other (CLAUDE.md Section 7.11).
    Unwrapping (`.msg`/`.eml`) recurses exactly one level: an attachment
    inside an attachment is rejected with DOC-019, never opened.
    """
    handler = file_type.handler

    if handler in _UNWRAPPERS:
        if depth >= file_types.MAX_EMBEDDING_DEPTH:
            raise ConversionError(
                "DOC-019", "Attachment is nested more than one level inside a message."
            )
        produced = _UNWRAPPERS[handler](content)
    elif handler in _CONVERTERS:
        produced = _CONVERTERS[handler](content)
    else:
        # Already Tier 1 and directly parseable.
        return [PreparedArtifact(file_type=file_type, content=content, label=label)]

    artifacts: list[PreparedArtifact] = []
    rejections: list[str] = []
    for artifact_bytes, artifact_name in produced:
        validation = file_types.validate_upload(artifact_bytes, artifact_name)
        if not validation.ok:
            rejections.append(validation.error_code or "DOC-014")
            logger.info(
                "converted_artifact_rejected source=%s error_code=%s",
                _sanitize_label(label),
                validation.error_code,
            )
            continue
        try:
            artifacts.extend(
                prepare_artifacts(
                    validation.file_type,
                    artifact_bytes,
                    label=_sanitize_label(artifact_name),
                    depth=depth + 1,
                )
            )
        except ConversionError as exc:
            rejections.append(exc.error_code)
            logger.info(
                "converted_artifact_failed source=%s error_code=%s",
                _sanitize_label(label),
                exc.error_code,
            )

    if not artifacts:
        raise ConversionError(
            rejections[0] if rejections else "DOC-017",
            "Nothing readable was produced from this file.",
        )
    return artifacts


def tier2_handlers() -> set[str]:
    """Every handler name this module implements -- used by the worker's own
    completeness test against the allowlist in `docflow_core.file_types`."""
    return set(_CONVERTERS) | set(_UNWRAPPERS)
