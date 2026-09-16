"""
The error catalog (CLAUDE.md Section 7.16.5) -- the one source for every
user-facing failure message. UI, email, API responses, and intake
auto-replies all render from this catalog. No user-facing string describing
a failure is allowed to exist outside it.

Entries are added here as each phase introduces failures that can reach a
user (Phase 1 adds the file-parsing and quarantine codes, Phase 4 adds
export codes, etc.) -- this file starts small on purpose and grows with the
build, per CLAUDE.md Section 7.16.5's own instruction to "extend for every
failure."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from docflow_core.file_types import MAX_DOCUMENT_PAGES

Audience = Literal["tenant", "founder", "both"]
Severity = Literal["info", "warning", "high", "critical"]


@dataclass(frozen=True)
class ErrorCatalogEntry:
    code: str
    title: str
    message: str
    action: str
    severity: Severity
    audience: Audience


# Populated incrementally. Codes are stable once introduced -- never reused
# for a different meaning, never removed once a customer could have seen it.
CATALOG: dict[str, ErrorCatalogEntry] = {
    "AUTH-001": ErrorCatalogEntry(
        code="AUTH-001",
        title="This link has expired",
        message="Your invite link is no longer valid.",
        action="Ask your account owner to resend the invite.",
        severity="warning",
        audience="tenant",
    ),
    # ── Phase 1: file intake and extraction (CLAUDE.md Section 7.11, 7.16.5) ──
    "DOC-001": ErrorCatalogEntry(
        code="DOC-001",
        title="We couldn't read this file",
        message="This PDF is password-protected. DocFlow doesn't open protected files.",
        action="Ask the sender for an unprotected copy, or remove the password and upload it again.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-002": ErrorCatalogEntry(
        code="DOC-002",
        title="This file is too large",
        message="This file is larger than the 25 MB limit DocFlow can safely process.",
        action="Split the document, compress it, or send a smaller file.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-003": ErrorCatalogEntry(
        code="DOC-003",
        title="This file couldn't be verified as safe",
        message=(
            "This file's internal structure doesn't look like a normal document, "
            "so DocFlow didn't open it."
        ),
        action="Re-save the file from its original source and upload it again.",
        severity="high",
        audience="tenant",
    ),
    "DOC-004": ErrorCatalogEntry(
        code="DOC-004",
        title="We can't read this format",
        message=(
            "This is an Office file type DocFlow doesn't read as a purchase order, such as "
            "PowerPoint or Visio."
        ),
        action=(
            "Send the purchase order as a PDF, Word (.doc/.docx) or Excel (.xls/.xlsx) file "
            "instead."
        ),
        severity="warning",
        audience="tenant",
    ),
    "DOC-005": ErrorCatalogEntry(
        code="DOC-005",
        title="This file appears to be corrupted",
        message="DocFlow could not open this file's internal structure to read it.",
        action="Re-save or re-export the file from its original source and upload it again.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-006": ErrorCatalogEntry(
        code="DOC-006",
        title="This file's contents don't match its name",
        message="The file's actual content doesn't match the file type its name suggests.",
        action="Check the file and upload the original, unmodified document.",
        severity="high",
        audience="tenant",
    ),
    "DOC-007": ErrorCatalogEntry(
        code="DOC-007",
        title="This scan is too low-quality to trust",
        message=(
            "We extracted what we could, but the image resolution is low and "
            "confidence is below our threshold."
        ),
        action="Review every field carefully before approving, or request a clearer copy from the buyer.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-008": ErrorCatalogEntry(
        code="DOC-008",
        title="Extraction failed",
        message="DocFlow could not get a valid response from the extraction service for this document.",
        action=(
            "This document has been held as failed. Try re-running it; if it fails "
            "again, DocFlow has already been alerted."
        ),
        severity="high",
        audience="both",
    ),
    "DOC-009": ErrorCatalogEntry(
        code="DOC-009",
        title="Extraction response was invalid",
        message="The extraction service's response for this document didn't match the expected structure.",
        action=(
            "This document has been held as failed and the raw response was saved. "
            "DocFlow has already been alerted."
        ),
        severity="critical",
        audience="both",
    ),
    # ── Phase 1 (Tier 2/Tier 3 slice): CLAUDE.md Section 7.11's three-tier ──
    # allowlist. Every Tier 3 case gets its own code, and every message names
    # the format and the fix ("We can't read .zip files -- please send the
    # purchase order itself as a PDF or Excel attachment" beats "unsupported
    # file type").
    "DOC-010": ErrorCatalogEntry(
        code="DOC-010",
        title="We can't read archive files",
        message=(
            "This is a .zip, .rar or .7z archive. DocFlow doesn't open archives, because "
            "there's no way to tell which file inside is the purchase order."
        ),
        action=(
            "Send the purchase order itself as a separate attachment -- PDF, Word, Excel "
            "or a photo all work."
        ),
        severity="warning",
        audience="tenant",
    ),
    "DOC-011": ErrorCatalogEntry(
        code="DOC-011",
        title="We can't read Apple iWork files",
        message=(
            "This is a Pages, Numbers or Keynote file, which only Apple's own apps can open."
        ),
        action=(
            "In Pages or Numbers choose File > Export To > PDF (or Word/Excel) and send "
            "that file instead."
        ),
        severity="warning",
        audience="tenant",
    ),
    "DOC-012": ErrorCatalogEntry(
        code="DOC-012",
        title="We can't read CAD or EDI files",
        message=(
            "This is a CAD drawing or a raw EDI interchange payload, not a document DocFlow "
            "can read as a purchase order."
        ),
        action=(
            "Send the purchase order as a PDF, Word, Excel or image file. If your buyer sends "
            "EDI, contact us -- that needs a different setup."
        ),
        severity="warning",
        audience="tenant",
    ),
    "DOC-013": ErrorCatalogEntry(
        code="DOC-013",
        title="This file is password-protected",
        message=(
            "This file is encrypted or password-protected. DocFlow never tries to guess or "
            "break a password, so it can't open it."
        ),
        action="Ask the sender for an unprotected copy, or remove the password and send it again.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-014": ErrorCatalogEntry(
        code="DOC-014",
        title="This file isn't a document we recognize",
        message=(
            "Whatever its name says, this file's contents don't match any document format "
            "DocFlow reads, so nothing was opened."
        ),
        action=(
            "Send the purchase order as a PDF, Word (.doc/.docx), Excel (.xls/.xlsx), or a "
            "photo or scan (PNG, JPG, TIFF, HEIC)."
        ),
        severity="warning",
        audience="tenant",
    ),
    "DOC-015": ErrorCatalogEntry(
        code="DOC-015",
        title="This file contains an unsafe instruction",
        message=(
            "This Word, Excel or OpenDocument file includes an XML instruction that tries to "
            "pull in outside content, which DocFlow refuses to process."
        ),
        action=(
            "Re-save the document from its original program and send it again. If it keeps "
            "failing, DocFlow has already been alerted."
        ),
        severity="high",
        audience="both",
    ),
    "DOC-016": ErrorCatalogEntry(
        code="DOC-016",
        title="This document has too many pages",
        message=(
            f"This file has more than {MAX_DOCUMENT_PAGES} pages, which is far more than a "
            "purchase order needs and more than DocFlow will read in one document."
        ),
        action="Send just the purchase-order pages, or split the file and send it in parts.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-017": ErrorCatalogEntry(
        code="DOC-017",
        title="We couldn't convert this older file",
        message=(
            "This is a legacy Word (.doc), Excel (.xls), OpenDocument, TIFF, HEIC or Outlook "
            "(.msg) file, which DocFlow converts before reading -- and the conversion didn't "
            "complete."
        ),
        action=(
            "Re-save or export it as PDF, .docx or .xlsx and send it again -- DocFlow reads "
            "those directly. DocFlow has already been alerted."
        ),
        severity="high",
        audience="both",
    ),
    "DOC-018": ErrorCatalogEntry(
        code="DOC-018",
        title="This image is too large to open",
        message=(
            "This image's pixel dimensions are far beyond a normal scan or photo, so DocFlow "
            "didn't decode it."
        ),
        action="Send a normal-resolution scan or photo of the purchase order, or a PDF.",
        severity="warning",
        audience="tenant",
    ),
    "DOC-019": ErrorCatalogEntry(
        code="DOC-019",
        title="This attachment is buried too deep",
        message=(
            "This purchase order arrived as an attachment inside an attachment inside another "
            "message. DocFlow opens one level of forwarding, not more."
        ),
        action="Forward the purchase order with the file attached directly to the email.",
        severity="warning",
        audience="tenant",
    ),
    # ── Phase 1 (email intake slice): CLAUDE.md Section 7.16.3 / 7.16.4 ──────
    "INT-001": ErrorCatalogEntry(
        code="INT-001",
        title="No attachment to process",
        message=(
            "This email didn't include an attachment DocFlow recognizes as a document, "
            "so nothing was processed."
        ),
        action="Resend with the purchase order attached as a PDF, Word, or Excel file.",
        severity="warning",
        audience="tenant",
    ),
    "INT-002": ErrorCatalogEntry(
        code="INT-002",
        title="Held: too many attachments",
        message=(
            "This email had more attachments than DocFlow allows in one message, so we're "
            "holding all of them until someone confirms they're real."
        ),
        action="Open 'Held for review' to release these attachments, or resend them in smaller batches.",
        severity="warning",
        audience="tenant",
    ),
    "INT-003": ErrorCatalogEntry(
        code="INT-003",
        title="Held: unusual volume from new senders",
        message=(
            "This account received more than 20 documents from unknown senders in the last "
            "hour, so we're holding new ones until someone confirms they're real."
        ),
        action="Open 'Held for review' to release the ones you recognize.",
        severity="warning",
        audience="tenant",
    ),
    "INT-004": ErrorCatalogEntry(
        code="INT-004",
        title="Held: sender couldn't be verified",
        message=(
            "This email failed the sending domain's own authentication check, so DocFlow held "
            "it instead of processing it automatically."
        ),
        action="DocFlow will review this before releasing it -- no action is needed from you right now.",
        severity="high",
        audience="both",
    ),
}


def get_error(code: str) -> ErrorCatalogEntry:
    if code not in CATALOG:
        raise KeyError(
            f"Unknown error catalog code '{code}'. Every user-facing failure must be "
            "added to docflow_core.errors.CATALOG before it can be raised "
            "(CLAUDE.md Section 7.16.5)."
        )
    return CATALOG[code]
