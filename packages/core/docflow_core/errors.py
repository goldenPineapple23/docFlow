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
    # ── Phase 2 (validation slice): CLAUDE.md Section 7.7 / 7.8 ─────────────
    # Review warnings, not failures -- nothing below stops a document, changes
    # a value, or blocks processing. They are in this catalog because Section
    # 7.16.5's rule is about user-facing text describing something wrong
    # ("no user-facing string that describes a failure exists outside it"),
    # and a warning is exactly that; see DECISIONS.md D-072. Each entry holds
    # the what/why/what-next prose; the `document_warnings.detail` payload
    # holds the specifics (which field, which line, which numbers disagreed).
    "VAL-001": ErrorCatalogEntry(
        code="VAL-001",
        title="Line total doesn't match quantity times price",
        message=(
            "On this line, the printed total isn't the quantity multiplied by the unit price. "
            "DocFlow changed nothing -- all three numbers are exactly as the document printed them."
        ),
        action="Check the line against the original document and correct whichever number is wrong.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-002": ErrorCatalogEntry(
        code="VAL-002",
        title="Order total doesn't match the line items",
        message=(
            "The order total doesn't equal the sum of the line totals. Often that means tax, "
            "freight or a discount isn't itemized on the order -- sometimes it means a number "
            "was misread. DocFlow changed nothing either way."
        ),
        action=(
            "Compare the total with the original document and correct whichever value is wrong, "
            "or acknowledge the difference before approving."
        ),
        severity="warning",
        audience="tenant",
    ),
    "VAL-003": ErrorCatalogEntry(
        code="VAL-003",
        title="A quantity isn't a positive number",
        message="This line's quantity is zero or negative, so it can't be fulfilled as written.",
        action="Correct the quantity from the original document, or remove the line, before approving.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-004": ErrorCatalogEntry(
        code="VAL-004",
        title="We couldn't read a date on this order",
        message=(
            "One of the dates on this order isn't in a form DocFlow can read, so it was left "
            "exactly as printed rather than reinterpreted."
        ),
        action="Enter the correct date from the original document before approving.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-005": ErrorCatalogEntry(
        code="VAL-005",
        title="A date on this order looks wrong",
        message=(
            "One of the dates is far outside the range a purchase order normally carries, "
            "which usually means a digit was misread on the page."
        ),
        action=(
            "Check the date against the original document and correct it, "
            "or acknowledge it if it's right."
        ),
        severity="warning",
        audience="tenant",
    ),
    "VAL-006": ErrorCatalogEntry(
        code="VAL-006",
        title="A required field is missing",
        message=(
            "DocFlow couldn't find one of the fields an order needs, so it was left empty "
            "rather than guessed at."
        ),
        action="Fill the field in from the original document before approving.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-007": ErrorCatalogEntry(
        code="VAL-007",
        title="Currency code isn't one we recognize",
        message=(
            "The currency on this order isn't a valid ISO 4217 code, so DocFlow can't be sure "
            "what money the amounts are in."
        ),
        action="Set the correct currency from the original document before approving.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-008": ErrorCatalogEntry(
        code="VAL-008",
        title="Unit of measure disagrees with the catalog",
        message=(
            "This line's unit doesn't match the unit on the catalog item it matched. DocFlow "
            "never changes a unit on its own, so both are shown exactly as they are."
        ),
        action=(
            "Confirm which unit is right before approving -- a case ordered as an each ships "
            "the wrong quantity."
        ),
        severity="warning",
        audience="tenant",
    ),
    "VAL-009": ErrorCatalogEntry(
        code="VAL-009",
        title="This document contains an embedded instruction",
        message=(
            "Text in this document tried to give DocFlow's extraction instructions instead of "
            "being order data. It was ignored, not followed, and the document is held for review."
        ),
        action=(
            "Read every field against the original document before approving, and treat this "
            "sender with caution. DocFlow has already flagged it."
        ),
        severity="high",
        audience="both",
    ),
    "VAL-010": ErrorCatalogEntry(
        code="VAL-010",
        title="A value here is below our confidence threshold",
        message=(
            "DocFlow isn't confident it read this value correctly -- usually a low-quality scan, "
            "a handwritten entry, or an ambiguous layout."
        ),
        action="Check this value against the original document before approving.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-011": ErrorCatalogEntry(
        code="VAL-011",
        title="Currency was inferred from a symbol",
        message=(
            "This order doesn't state its currency. DocFlow read it from a currency symbol, "
            "which is a guess about the country rather than something printed on the page."
        ),
        action="Confirm the currency is right before approving.",
        severity="info",
        audience="tenant",
    ),
    "VAL-012": ErrorCatalogEntry(
        code="VAL-012",
        title="This looks like a document we already have",
        message=(
            "Another document on this account has exactly the same contents, so this is "
            "probably a resend. Both have been kept -- nothing was replaced or deleted."
        ),
        action="Open the earlier document to compare, then reject this one if it's a duplicate.",
        severity="warning",
        audience="tenant",
    ),
    "VAL-013": ErrorCatalogEntry(
        code="VAL-013",
        title="This may revise an earlier order",
        message=(
            "An earlier document on this account has the same PO number but different contents, "
            "which usually means this is a change order or a revised copy. Both have been kept."
        ),
        action="Compare the two before approving, so the version that gets exported is the right one.",
        severity="high",
        audience="tenant",
    ),
    "AUTH-002": ErrorCatalogEntry(
        code="AUTH-002",
        title="Your account can view this, not change it",
        message=(
            "Viewer accounts can read orders and download exports, but editing and approving "
            "are left to reviewers so it's always clear who signed off on an order."
        ),
        action="Ask an owner or admin on your account to change your role, or to approve this order.",
        severity="warning",
        audience="tenant",
    ),
    # ── REV-0xx · human review and approval (Section 7.3) ────────────────────
    "REV-001": ErrorCatalogEntry(
        code="REV-001",
        title="Some warnings still need a look",
        message=(
            "This order has warnings nobody has acknowledged yet. DocFlow doesn't approve an "
            "order while something on it is still unexplained."
        ),
        action="Open each warning, fix the value or confirm it's right, then approve.",
        severity="warning",
        audience="tenant",
    ),
    "REV-002": ErrorCatalogEntry(
        code="REV-002",
        title="This order was already approved",
        message=(
            "Someone approved this order before you did. Approving it twice would create a "
            "second frozen copy, so DocFlow stopped."
        ),
        action=(
            "Reload the order to see the approved version. Edit it if something is wrong "
            "-- that reopens it for review."
        ),
        severity="warning",
        audience="tenant",
    ),
    "REV-003": ErrorCatalogEntry(
        code="REV-003",
        title="That field can't be edited here",
        message=(
            "The review screen can change the values read off the order. It can't change how "
            "DocFlow recorded or processed the document."
        ),
        action="Edit the order's own fields -- the PO number, dates, buyer, totals, and line items.",
        severity="warning",
        audience="tenant",
    ),
    "REV-004": ErrorCatalogEntry(
        code="REV-004",
        title="This order isn't ready to approve",
        message=(
            "Only an order that has finished processing and is waiting for review can be "
            "approved. This one is in another state."
        ),
        action="Wait for processing to finish, or open the order to see why it stopped.",
        severity="warning",
        audience="tenant",
    ),
    "REV-005": ErrorCatalogEntry(
        code="REV-005",
        title="Someone else changed this order first",
        message=(
            "This order changed while you had it open, so saving now would quietly overwrite "
            "what the other person did."
        ),
        action="Reload the order, check what changed, then make your edit again.",
        severity="warning",
        audience="tenant",
    ),
    # ── EXP-0xx · export files (Section 7.4, Phase 4) ────────────────────────
    "EXP-001": ErrorCatalogEntry(
        code="EXP-001",
        title="Only approved orders can be exported",
        message=(
            "This order isn't approved right now -- it hasn't been checked yet, or it was changed "
            "after approval -- and DocFlow only exports what a person has signed off."
        ),
        action="Review and approve the order, then export it.",
        severity="warning",
        audience="tenant",
    ),
    "EXP-002": ErrorCatalogEntry(
        code="EXP-002",
        title="We don't export to that format",
        message="DocFlow exports orders as CSV, Excel, JSON or QuickBooks Desktop (IIF) files.",
        action="Choose one of those four formats.",
        severity="warning",
        audience="tenant",
    ),
    "EXP-003": ErrorCatalogEntry(
        code="EXP-003",
        title="This download link has expired",
        message=(
            "Download links only work for a few minutes, so a copied or old link can't be used "
            "to fetch an order file."
        ),
        action="Open the order in DocFlow and download the file again from there.",
        severity="warning",
        audience="tenant",
    ),
    "EXP-004": ErrorCatalogEntry(
        code="EXP-004",
        title="Export didn't match the approved data",
        message=(
            "The file we generated failed our integrity check against the approved snapshot, "
            "so we didn't give it to you."
        ),
        action="Try again; if it fails a second time, DocFlow has already been alerted.",
        severity="error",
        audience="both",
    ),
    "EXP-005": ErrorCatalogEntry(
        code="EXP-005",
        title="QuickBooks' file format can't hold a value",
        message=(
            "This order contains a character, line break or date that QuickBooks Desktop's IIF "
            "format can't store exactly, so the file wouldn't match the approved order."
        ),
        action=(
            "Download it as CSV or Excel instead, or correct the value in DocFlow, re-approve, "
            "and export to QuickBooks again."
        ),
        severity="warning",
        audience="tenant",
    ),
    "EXP-006": ErrorCatalogEntry(
        code="EXP-006",
        title="QuickBooks can't import this order yet",
        message=(
            "QuickBooks Desktop only imports an order that has a buyer name and an order date, "
            "and whose line totals add up exactly to the order total. This one doesn't, so we "
            "didn't make a file QuickBooks would reject."
        ),
        action=(
            "Check the buyer name, order date, order total and line totals, or download the "
            "order as CSV or Excel instead."
        ),
        severity="warning",
        audience="tenant",
    ),
    "EXP-007": ErrorCatalogEntry(
        code="EXP-007",
        title="The export file couldn't be saved",
        message=(
            "The file was built and checked against the approved order, but saving it failed, "
            "so there's nothing to download yet. The failure has been logged."
        ),
        action="Try the export again in a minute.",
        severity="error",
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
