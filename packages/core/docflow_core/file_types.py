"""
The file-format allowlist (CLAUDE.md Section 7.11): "one module defines
it -- extension, magic-byte signature(s), tier, and handler -- and the
upload endpoint, email intake, and the Console's staging upload all read
from it." This is that module.

Scope as of the Tier 2 slice: Tier 1 (parse natively) and Tier 2 (convert,
then parse) are both accepted here; Tier 3 is rejected with a per-case
catalog code that names the format and the fix (Section 7.11: "Tier 3
entries each get a catalog code", "Every rejection names the format and the
fix"). Conversion itself never happens in this module or in the web
process -- this module only decides *what* a file is and *whether* it is
allowed; `apps/worker/app/conversion.py` does the converting, inside the
isolated worker.

Every uploaded file is hostile until proven otherwise: never trust the
extension or the Content-Type header. `detect_file_type` sniffs magic
bytes; zip-based formats (.docx/.xlsx/.odt/.ods) get a decompression-bomb,
path-traversal, encryption and XML-entity (XXE) check on their zip
*directory* and the first bytes of their XML entries before any real
parsing touches them; OLE compound formats (.doc/.xls/.msg) are told apart
by the stream names that appear in their directory sectors, not by their
extension.

Nothing in this module parses a document. The zip directory read and the
bounded XML *prefix scan* are validation (the same category as magic-byte
sniffing), not parsing: no XML parser, no office library, and no unbounded
decompression is involved, and the byte budgets below cap what any of it
can cost. See DECISIONS.md.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import Literal

Tier = Literal["tier1", "tier2", "tier3"]

# Named constant per CLAUDE.md Section 7.11's per-file size cap requirement.
# 25 MB covers every real-world PO/catalog file seen in the proof-of-concept
# and onboarding samples with generous headroom; see DECISIONS.md.
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

# Page-count cap (CLAUDE.md Section 7.11: "PDFs (page-count cap, per-page
# render timeouts)" and "Multi-page TIFF is treated like a multi-page PDF
# (page-count cap applies)"). Enforced in the worker, where pages are
# actually counted; defined here so both the PDF and the TIFF path read the
# same number. 100 matches the extraction model's own per-document page
# limit, so a document above it could not be extracted anyway.
MAX_DOCUMENT_PAGES = 100

# Image decompression-bomb defense (Section 7.11: "pixel-dimension caps
# before decode"). 50 MP is ~4x a 600-dpi A4 scan and ~4x a modern phone
# photo; anything larger is not a purchase order. Both are checked against
# the *declared* dimensions in the image header, before any decode.
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_DIMENSION = 20_000

# .msg/.eml unwrapping (Section 7.11): "bounded to one level -- an
# attachment inside an attachment inside an attachment is rejected."
MAX_EMBEDDED_ATTACHMENTS = 10
MAX_EMBEDDING_DEPTH = 1

# Decompression-bomb defense for zip-based formats (.docx/.xlsx/.odt/.ods).
# These limits are deliberately generous for a normal PO/catalog document
# (which is at most a few hundred KB of actual content) while still catching
# a deliberately crafted bomb.
ZIP_MAX_ENTRY_COUNT = 2000
ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
ZIP_MAX_COMPRESSION_RATIO = 100  # uncompressed / compressed, per entry

# XXE defense (Section 7.11): a DOCTYPE/ENTITY declaration must appear before
# the document element, so scanning a bounded prefix of each XML entry finds
# every one of them. Budgets keep the scan itself from being the bomb.
XML_SCAN_PREFIX_BYTES = 16 * 1024
XML_SCAN_TOTAL_BUDGET_BYTES = 8 * 1024 * 1024

# How much of a PDF's tail to scan for an /Encrypt trailer entry. The
# trailer lives at the end of the file by construction.
PDF_TRAILER_SCAN_BYTES = 8192


class FileTypeName(str, Enum):
    # ── Tier 1: parse natively ──────────────────────────────────────────
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    GIF = "gif"
    WEBP = "webp"
    TXT = "txt"
    CSV = "csv"
    MD = "md"
    HTML = "html"
    RTF = "rtf"
    EML = "eml"
    DOCX = "docx"
    XLSX = "xlsx"
    # ── Tier 2: convert inside the isolated worker, then parse ──────────
    DOC = "doc"
    XLS = "xls"
    TIFF = "tiff"
    HEIC = "heic"
    MSG = "msg"
    ODT = "odt"
    ODS = "ods"


@dataclass(frozen=True)
class FileType:
    name: FileTypeName
    tier: Tier
    media_type: str
    # The worker-side handler that knows how to turn this format into model
    # content. Tier 1 handlers parse; Tier 2 handlers convert first. Named
    # here so "adding a format is one edit plus a test fixture"
    # (Section 7.11) stays true -- apps/worker asserts in its own test suite
    # that every allowlisted format has a handler it implements.
    handler: str = ""


@dataclass(frozen=True)
class FormatSpec:
    name: FileTypeName
    tier: Tier
    media_type: str
    extensions: tuple[str, ...]
    handler: str
    # Extensions that are *not* this format's own but that a buyer's mail
    # client or word processor plausibly puts on it (Word writes RTF as
    # `.doc`; Excel writes .xlsx content as `.xls`). Accepting these avoids
    # refusing a perfectly readable purchase order over a benign extension
    # lie, while the extension/magic-byte mismatch check still catches the
    # dangerous case (a `.exe` renamed `.pdf`), whose content matches no
    # allowlisted signature at all.
    also_accepts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    file_type: FileType | None
    error_code: str | None
    detail: str


# ── The allowlist itself: extension(s), tier, media type, handler ──────────
# Magic-byte signatures live in `_MAGIC_SIGNATURES` / the container
# classifiers below, keyed by the same FileTypeName.
FORMATS: tuple[FormatSpec, ...] = (
    FormatSpec(FileTypeName.PDF, "tier1", "application/pdf", (".pdf",), "pdf"),
    FormatSpec(FileTypeName.PNG, "tier1", "image/png", (".png",), "image"),
    FormatSpec(FileTypeName.JPEG, "tier1", "image/jpeg", (".jpg", ".jpeg"), "image"),
    FormatSpec(FileTypeName.WEBP, "tier1", "image/webp", (".webp",), "image"),
    FormatSpec(FileTypeName.GIF, "tier1", "image/gif", (".gif",), "image"),
    FormatSpec(FileTypeName.TXT, "tier1", "text/plain", (".txt",), "text"),
    FormatSpec(FileTypeName.CSV, "tier1", "text/csv", (".csv",), "text"),
    FormatSpec(FileTypeName.MD, "tier1", "text/markdown", (".md",), "text"),
    FormatSpec(FileTypeName.HTML, "tier1", "text/html", (".html", ".htm"), "text"),
    FormatSpec(FileTypeName.RTF, "tier1", "application/rtf", (".rtf",), "rtf", (".doc",)),
    FormatSpec(FileTypeName.EML, "tier1", "message/rfc822", (".eml",), "eml"),
    FormatSpec(
        FileTypeName.DOCX,
        "tier1",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        (".docx",),
        "docx",
        (".doc",),
    ),
    FormatSpec(
        FileTypeName.XLSX,
        "tier1",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        (".xlsx", ".xlsm"),
        "xlsx",
        (".xls",),
    ),
    FormatSpec(FileTypeName.DOC, "tier2", "application/msword", (".doc",), "convert_doc"),
    FormatSpec(FileTypeName.XLS, "tier2", "application/vnd.ms-excel", (".xls",), "convert_xls"),
    FormatSpec(FileTypeName.TIFF, "tier2", "image/tiff", (".tif", ".tiff"), "convert_tiff"),
    FormatSpec(FileTypeName.HEIC, "tier2", "image/heic", (".heic", ".heif"), "convert_heic"),
    FormatSpec(FileTypeName.MSG, "tier2", "application/vnd.ms-outlook", (".msg",), "convert_msg"),
    FormatSpec(
        FileTypeName.ODT, "tier2", "application/vnd.oasis.opendocument.text", (".odt",), "convert_odf"
    ),
    FormatSpec(
        FileTypeName.ODS,
        "tier2",
        "application/vnd.oasis.opendocument.spreadsheet",
        (".ods",),
        "convert_odf",
    ),
)

ALL_TYPES: dict[FileTypeName, FileType] = {
    spec.name: FileType(
        name=spec.name, tier=spec.tier, media_type=spec.media_type, handler=spec.handler
    )
    for spec in FORMATS
}

# Retained for callers written before Tier 2 existed.
TIER1_TYPES: dict[FileTypeName, FileType] = {
    name: file_type for name, file_type in ALL_TYPES.items() if file_type.tier == "tier1"
}

ALLOWED_EXTENSIONS: dict[str, FileTypeName] = {
    ext: spec.name for spec in FORMATS for ext in spec.extensions
}

_EXTENSIONS_BY_TYPE: dict[FileTypeName, set[str]] = {
    spec.name: set(spec.extensions) | set(spec.also_accepts) for spec in FORMATS
}

_TEXT_LIKE = {FileTypeName.TXT, FileTypeName.CSV, FileTypeName.MD, FileTypeName.HTML}
_TEXT_LIKE_EXTENSIONS = {".txt", ".csv", ".md", ".html", ".htm"}


# ── Tier 3: rejected, each with its own catalog code ───────────────────────
# (CLAUDE.md Section 7.11: "Tier 3 entries each get a catalog code" /
# "Every rejection names the format and the fix.")
ERR_PDF_PASSWORD_PROTECTED = "DOC-001"
ERR_ARCHIVE = "DOC-010"
ERR_IWORK = "DOC-011"
ERR_CAD_EDI = "DOC-012"
ERR_ENCRYPTED = "DOC-013"
ERR_UNKNOWN_SIGNATURE = "DOC-014"
ERR_UNSAFE_XML = "DOC-015"

_TIER3_EXTENSION_CODES: dict[str, str] = {
    ".zip": ERR_ARCHIVE,
    ".rar": ERR_ARCHIVE,
    ".7z": ERR_ARCHIVE,
    ".gz": ERR_ARCHIVE,
    ".tar": ERR_ARCHIVE,
    ".bz2": ERR_ARCHIVE,
    ".xz": ERR_ARCHIVE,
    ".pages": ERR_IWORK,
    ".numbers": ERR_IWORK,
    ".key": ERR_IWORK,
    ".dwg": ERR_CAD_EDI,
    ".dxf": ERR_CAD_EDI,
    ".dgn": ERR_CAD_EDI,
    ".edi": ERR_CAD_EDI,
    ".x12": ERR_CAD_EDI,
    ".edifact": ERR_CAD_EDI,
}


class FileRejection(Exception):
    """
    A file that is not on the allowlist, or is on it but failed a hardening
    check. Carries the catalog code the caller must surface -- no
    user-facing failure text is ever built outside docflow_core.errors
    (CLAUDE.md Section 7.16.5).
    """

    def __init__(self, error_code: str, detail: str):
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class ZipInspectionError(FileRejection):
    """A zip-shaped file that failed the decompression-bomb / path checks."""


# ── Magic-byte signatures ──────────────────────────────────────────────────
# Each FileTypeName maps to a list of *variants*; a variant is itself a list
# of (offset, signature) parts that must ALL match (AND) for that variant to
# hit. A type matches if ANY of its variants matches (OR). GIF has two valid
# single-part header variants (GIF87a / GIF89a); WEBP needs a single variant
# with two parts -- the RIFF wrapper AND the WEBP fourcc at offset 8, to
# avoid matching other RIFF containers (e.g. .wav). TIFF's two variants are
# the little-endian ("II") and big-endian ("MM") byte orders.
_MAGIC_SIGNATURES: list[tuple[FileTypeName, list[list[tuple[int, bytes]]]]] = [
    (FileTypeName.PDF, [[(0, b"%PDF-")]]),
    (FileTypeName.PNG, [[(0, b"\x89PNG\r\n\x1a\n")]]),
    (FileTypeName.JPEG, [[(0, b"\xff\xd8\xff")]]),
    (FileTypeName.GIF, [[(0, b"GIF87a")], [(0, b"GIF89a")]]),
    (FileTypeName.WEBP, [[(0, b"RIFF"), (8, b"WEBP")]]),
    (FileTypeName.TIFF, [[(0, b"II\x2a\x00")], [(0, b"MM\x00\x2a")]]),
    (FileTypeName.RTF, [[(0, b"{\\rtf")]]),
]

_ZIP_SIGNATURE = b"PK\x03\x04"
# An empty zip archive uses this signature instead of PK\x03\x04.
_ZIP_EMPTY_SIGNATURE = b"PK\x05\x06"
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# ISO base-media-file-format brands that mean "this is a HEIF/HEIC image".
# AVIF is deliberately absent -- it is not on the Section 7.11 allowlist.
_HEIF_BRANDS = {
    b"heic",
    b"heix",
    b"heim",
    b"heis",
    b"hevc",
    b"hevx",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
    b"heif",
}

_ARCHIVE_SIGNATURES: list[tuple[bytes, str]] = [
    (b"Rar!\x1a\x07", ".rar"),
    (b"7z\xbc\xaf\x27\x1c", ".7z"),
    (b"\x1f\x8b", ".gz"),
    (b"BZh", ".bz2"),
    (b"\xfd7zXZ\x00", ".xz"),
]

def _looks_like_dwg(content: bytes) -> bool:
    """AutoCAD drawings start with a six-byte version tag: AC1012..AC1032."""
    return content[:2] == b"AC" and content[2:6].isdigit()

_PDF_ENCRYPT_RE = re.compile(rb"/Encrypt\s*(\d+\s+\d+\s+R|<<)")
_MAIL_HEADER_RE = re.compile(
    rb"^(received|from|to|subject|date|message-id|mime-version|return-path|delivered-to)\s*:",
    re.IGNORECASE | re.MULTILINE,
)


def _sniff_magic(content: bytes) -> FileTypeName | None:
    for name, variants in _MAGIC_SIGNATURES:
        for parts in variants:
            if all(content[offset : offset + len(sig)] == sig for offset, sig in parts):
                return name
    return None


def _is_heif(content: bytes) -> bool:
    return content[4:8] == b"ftyp" and content[8:12] in _HEIF_BRANDS


def _looks_like_text(content: bytes) -> bool:
    """
    Plain text/csv/md/html/eml have no magic bytes, so validate by decode
    instead: must be valid UTF-8 and free of NUL bytes / a control-byte
    flood (a crude but effective binary-content detector).
    """
    if b"\x00" in content:
        return False
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not decoded:
        return True
    control_count = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\r\n\t")
    return (control_count / len(decoded)) < 0.05


def _looks_like_email_message(content: bytes) -> bool:
    """
    RFC 5322 messages start with a header block. Requiring both a
    header-shaped first line and a known header name in the first block
    tells a forwarded `.eml` from a plain-text PO that happens to contain a
    colon.
    """
    head = content[:8192]
    first_line = head.split(b"\n", 1)[0].strip()
    if not re.match(rb"^[A-Za-z][A-Za-z0-9-]*:\s", first_line):
        return False
    return _MAIL_HEADER_RE.search(head) is not None


def _looks_like_edi(content: bytes) -> bool:
    head = content[:512].lstrip()
    return head.startswith(b"ISA*") or head.startswith(b"ISA~") or head.startswith((b"UNA", b"UNB+"))


def pdf_looks_encrypted(content: bytes) -> bool:
    """
    CLAUDE.md Section 7.11: "Encrypted / password-protected files are
    detected and rejected with a clear message... never brute-forced, never
    passed to the model." A PDF's encryption dictionary is referenced from
    its trailer, which lives at the end of the file, so a bounded tail scan
    catches it without opening the document. The worker's PDF path performs
    the authoritative check too (the reference can hide inside a compressed
    cross-reference stream this scan cannot see), and both map to DOC-001.
    """
    return _PDF_ENCRYPT_RE.search(content[-PDF_TRAILER_SCAN_BYTES:]) is not None


def _inspect_zip_entries(content: bytes) -> zipfile.ZipFile:
    """
    Decompression-bomb, path-traversal and encryption defense (CLAUDE.md
    Section 7.11): enumerate zip entries -- never extract -- and reject
    before any real parsing touches the file if total declared uncompressed
    size, compression ratio, or entry count exceeds fixed limits, if any
    entry path traverses (`../`) or is absolute, if any entry is encrypted,
    or if any entry is itself an archive (no nested archives).
    """
    try:
        zf = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ZipInspectionError("DOC-005", f"Could not read zip directory: {exc}") from exc

    infos = zf.infolist()
    if len(infos) > ZIP_MAX_ENTRY_COUNT:
        raise ZipInspectionError(
            "DOC-003", f"Zip archive has {len(infos)} entries (limit {ZIP_MAX_ENTRY_COUNT})."
        )

    total_uncompressed = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
            raise ZipInspectionError("DOC-003", f"Zip entry has an unsafe path: {name!r}.")
        if info.flag_bits & 0x1:
            raise ZipInspectionError(
                ERR_ENCRYPTED, f"Zip entry {name!r} is encrypted or password-protected."
            )
        if name.lower().endswith((".zip", ".rar", ".7z", ".gz", ".bz2", ".xz")):
            raise ZipInspectionError("DOC-003", f"Zip entry {name!r} is a nested archive.")
        total_uncompressed += info.file_size
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > ZIP_MAX_COMPRESSION_RATIO:
                raise ZipInspectionError(
                    "DOC-003",
                    f"Zip entry {name!r} has a suspicious compression ratio ({ratio:.0f}x).",
                )
    if total_uncompressed > ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ZipInspectionError(
            "DOC-003",
            f"Zip archive's total uncompressed size ({total_uncompressed} bytes) "
            f"exceeds the limit ({ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES} bytes).",
        )
    return zf


def _scan_zip_xml_for_entities(zf: zipfile.ZipFile) -> None:
    """
    XXE / entity-expansion defense (CLAUDE.md Section 7.11): "Parse with
    external entity resolution and DTD processing disabled... Any file that
    triggers an entity-expansion or external-reference attempt is rejected
    and logged as suspicious."

    Office and OpenDocument files are zip archives of XML. A DTD subset --
    the only place an entity can be declared -- must appear before the
    document element, so a bounded prefix scan of every XML entry finds
    every declaration there can be. This runs *before* any XML library sees
    the bytes, which matters: `openpyxl` reads through `lxml`, whose default
    parser expands internal entities (verified on this codebase's pinned
    versions), so relying on the library's defaults would not be enough.
    """
    budget = XML_SCAN_TOTAL_BUDGET_BYTES
    for info in zf.infolist():
        lowered = info.filename.lower()
        if not lowered.endswith((".xml", ".rels")):
            continue
        if budget <= 0:
            raise ZipInspectionError(
                "DOC-003", "Zip archive has more XML content than DocFlow will inspect."
            )
        to_read = min(XML_SCAN_PREFIX_BYTES, budget)
        try:
            with zf.open(info) as handle:
                prefix = handle.read(to_read)
        except Exception as exc:  # a truncated/corrupt member
            raise ZipInspectionError(
                "DOC-005", f"Could not read zip entry {info.filename!r}: {type(exc).__name__}"
            ) from exc
        budget -= len(prefix)
        lowered_prefix = prefix.lower()
        if b"<!doctype" in lowered_prefix or b"<!entity" in lowered_prefix:
            raise ZipInspectionError(
                ERR_UNSAFE_XML,
                f"Zip entry {info.filename!r} declares an XML DTD or entity.",
            )


def _classify_zip(content: bytes) -> FileTypeName | None:
    """
    .docx/.xlsx/.odt/.ods (and Apple's iWork formats) are all zip archives
    (PK\x03\x04); distinguish them by peeking at internal entry names rather
    than trusting the extension. Raises FileRejection for a zip that is a
    Tier 3 format; returns None for a zip that is nothing recognizable.
    """
    zf = _inspect_zip_entries(content)
    names = set(zf.namelist())

    if any(n.startswith("Index/") and n.endswith(".iwa") for n in names) or (
        "buildVersionHistory.plist" in names
    ):
        raise FileRejection(ERR_IWORK, "Zip archive is an Apple iWork document.")

    if "word/document.xml" in names:
        _scan_zip_xml_for_entities(zf)
        return FileTypeName.DOCX
    if "xl/workbook.xml" in names:
        _scan_zip_xml_for_entities(zf)
        return FileTypeName.XLSX
    if "content.xml" in names and "mimetype" in names:
        mimetype = zf.read("mimetype").decode("utf-8", errors="replace").strip()
        if "opendocument.text" in mimetype:
            _scan_zip_xml_for_entities(zf)
            return FileTypeName.ODT
        if "opendocument.spreadsheet" in mimetype:
            _scan_zip_xml_for_entities(zf)
            return FileTypeName.ODS
    return None


# OLE compound-file stream/storage names, as they appear (UTF-16LE) in the
# file's directory sectors. Order matters: a .msg carrying an .xls attachment
# contains that attachment's own directory bytes, so the .msg marker is
# checked first.
_OLE_MARKERS: list[tuple[str, FileTypeName | None, str | None, str]] = [
    ("__substg1.0_", FileTypeName.MSG, None, "Outlook message"),
    ("EncryptedPackage", None, ERR_ENCRYPTED, "encrypted Office document"),
    ("WordDocument", FileTypeName.DOC, None, "legacy Word document"),
    ("Workbook", FileTypeName.XLS, None, "legacy Excel workbook"),
    ("Book", FileTypeName.XLS, None, "legacy Excel workbook"),
    ("PowerPoint Document", None, "DOC-004", "PowerPoint presentation"),
    ("Visio", None, "DOC-004", "Visio drawing"),
]

# The names an OLE file DocFlow reads may carry. With the signature but none
# of the markers above, such a file is damaged rather than another format
# (D-146). The extension only chooses the message: it can never admit a file.
_OLE_EXTENSIONS = frozenset({".doc", ".xls", ".msg"})


def _classify_ole(content: bytes) -> FileTypeName | None:
    """
    .doc, .xls and .msg are all OLE compound files sharing one magic-byte
    signature, so the signature alone cannot tell them apart. Their
    directory sectors carry the stream names as UTF-16LE, which is a
    content-level signal (never the extension) and needs no OLE library --
    keeping this module parser-free, per its own docstring.
    """
    for marker, name, error_code, description in _OLE_MARKERS:
        if marker.encode("utf-16-le") in content:
            if error_code is not None:
                raise FileRejection(error_code, f"OLE compound file is a {description}.")
            return name
    return None


def detect_file_type(content: bytes, claimed_extension: str = "") -> FileType | None:
    """
    Magic-byte sniffing -- never trust the extension or Content-Type header.
    Returns the detected FileType (Tier 1 or Tier 2) or None if the content
    doesn't match anything on the allowlist. Raises FileRejection (of which
    ZipInspectionError is one kind) when the content is recognizable but
    must be refused -- an archive, an iWork file, an encrypted file, a zip
    bomb, an XXE payload.

    `claimed_extension` is used only to choose among the text-like formats
    (.txt/.csv/.md/.html/.eml), which are indistinguishable by content
    alone; it can never promote a file to a type its bytes don't support.
    """
    magic_hit = _sniff_magic(content)
    if magic_hit is not None:
        if magic_hit == FileTypeName.PDF and pdf_looks_encrypted(content):
            raise FileRejection(ERR_PDF_PASSWORD_PROTECTED, "PDF declares an encryption dictionary.")
        return ALL_TYPES.get(magic_hit)

    if _is_heif(content):
        return ALL_TYPES[FileTypeName.HEIC]

    # A raw mail message (`.eml`) has no magic bytes and can carry binary
    # parts, so it is recognized by its header block before the text check.
    if claimed_extension in ("", ".eml") and _looks_like_email_message(content):
        return ALL_TYPES[FileTypeName.EML]

    for signature, extension in _ARCHIVE_SIGNATURES:
        if content.startswith(signature):
            raise FileRejection(ERR_ARCHIVE, f"File is a '{extension}' archive.")

    if _looks_like_dwg(content):
        raise FileRejection(ERR_CAD_EDI, "File is a '.dwg' CAD drawing.")

    if content[:4] == _ZIP_SIGNATURE or content[:4] == _ZIP_EMPTY_SIGNATURE:
        zip_type = _classify_zip(content)
        if zip_type is not None:
            return ALL_TYPES[zip_type]
        raise FileRejection(ERR_ARCHIVE, "File is a '.zip' archive.")

    if content[:8] == _OLE_SIGNATURE:
        ole_type = _classify_ole(content)
        if ole_type is not None:
            return ALL_TYPES[ole_type]
        if claimed_extension in _OLE_EXTENSIONS:
            # Named as a format DocFlow reads, with an Office signature, but
            # none of that format's contents: a damaged file, not another
            # Office type. DOC-004 would tell the sender it's "PowerPoint or
            # Visio", which is wrong and gives them nothing to do (D-146).
            raise FileRejection(
                "DOC-005", f"OLE compound file named '{claimed_extension}' has none of its streams."
            )
        raise FileRejection(
            "DOC-004", "File is a legacy Microsoft OLE compound file DocFlow doesn't read."
        )

    if _looks_like_text(content):
        if _looks_like_edi(content):
            raise FileRejection(ERR_CAD_EDI, "File is an EDI interchange payload.")
        # Magic bytes can't distinguish txt/csv/md/html from each other;
        # the caller's claimed extension (validated separately) picks among
        # them. Default to TXT as the generic text FileType.
        return ALL_TYPES[FileTypeName.TXT]

    return None


def _extension_of(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


def validate_upload(content: bytes, claimed_filename: str) -> ValidationResult:
    """
    Full Section 7.11 pre-parse validation pipeline: size cap, magic-byte
    detection, extension/magic-byte mismatch, decompression-bomb / XXE /
    encryption defense, and a per-case Tier 3 rejection code. This is the
    single entry point the upload endpoint, email intake, the worker's
    re-validation, and the Console staging upload all call.
    """
    if len(content) > MAX_FILE_SIZE_BYTES:
        return ValidationResult(
            ok=False,
            file_type=None,
            error_code="DOC-002",
            detail=f"File is {len(content)} bytes, exceeding the {MAX_FILE_SIZE_BYTES}-byte limit.",
        )

    ext = _extension_of(claimed_filename)

    if not content:
        return ValidationResult(
            ok=False,
            file_type=None,
            error_code=ERR_UNKNOWN_SIGNATURE,
            detail=f"'{ext or claimed_filename}' file is empty.",
        )

    try:
        detected = detect_file_type(content, ext)
    except FileRejection as exc:
        return ValidationResult(ok=False, file_type=None, error_code=exc.error_code, detail=exc.detail)

    if detected is None:
        tier3_code = _TIER3_EXTENSION_CODES.get(ext)
        if tier3_code is not None:
            return ValidationResult(
                ok=False,
                file_type=None,
                error_code=tier3_code,
                detail=f"'{ext}' is not a format DocFlow can read.",
            )
        return ValidationResult(
            ok=False,
            file_type=None,
            error_code=ERR_UNKNOWN_SIGNATURE,
            detail=(
                f"'{ext or claimed_filename}' content matches no format signature on "
                "DocFlow's allowlist."
            ),
        )

    # Extension/magic-byte mismatch: a renamed file (e.g. a .exe as .pdf).
    # Text-like types have no reliable magic bytes among themselves, but a
    # claimed extension outside the text-like set (e.g. ".pdf") still can't
    # be waved through just because the content happens to decode as UTF-8 --
    # a truncated binary file must not be accepted as a PDF.
    if detected.name in _TEXT_LIKE:
        if ext and ext not in _TEXT_LIKE_EXTENSIONS:
            return ValidationResult(
                ok=False,
                file_type=None,
                error_code="DOC-006",
                detail=f"File claims to be '{ext}' but its content is plain text, not '{ext}'.",
            )
    elif ext:
        expected_extensions = _EXTENSIONS_BY_TYPE.get(detected.name, set())
        if expected_extensions and ext not in expected_extensions:
            return ValidationResult(
                ok=False,
                file_type=None,
                error_code="DOC-006",
                detail=(
                    f"File claims to be '{ext}' but its content is actually "
                    f"'{detected.name.value}'."
                ),
            )

    return ValidationResult(ok=True, file_type=detected, error_code=None, detail="ok")
