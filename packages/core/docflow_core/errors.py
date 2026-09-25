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
            "DocFlow has already been alerted. To have it read again, upload the same "
            "file again; if it fails a second time, DocFlow will look into it."
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
        action=(
            "Tick the ones you recognize and release them, or ask the sender to resend in "
            "smaller batches."
        ),
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
        action="Tick the ones you recognize and release them.",
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
        action="DocFlow has been alerted and will release it once the sender is confirmed.",
        severity="high",
        audience="both",
    ),
    "INT-005": ErrorCatalogEntry(
        code="INT-005",
        title="This address isn't active yet",
        message=(
            "DocFlow is still being set up for this account, so orders sent to this address "
            "are not read yet. This email was logged, not processed."
        ),
        action="Please send the order to your usual contact at the company for now.",
        severity="info",
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
    "AUTH-003": ErrorCatalogEntry(
        code="AUTH-003",
        title="This page is for account admins",
        message=(
            "The dashboard and the people on this account are managed by your account's "
            "admin, so this page isn't part of a reviewer's access."
        ),
        action="Ask your account admin if you need something from it, or go to Purchase orders.",
        severity="info",
        audience="tenant",
    ),
    "AUTH-004": ErrorCatalogEntry(
        code="AUTH-004",
        title="Your access to this account has ended",
        message=(
            "Your account's admin has removed you from this DocFlow account, so you can no "
            "longer see or change its orders. Your past work is still in the account's history."
        ),
        action="If you think this is a mistake, ask your account's admin to invite you again.",
        severity="warning",
        audience="tenant",
    ),
    "AUTH-005": ErrorCatalogEntry(
        code="AUTH-005",
        title="You're signed out",
        message=(
            "This browser isn't signed in to DocFlow, or your sign-in has expired, so DocFlow "
            "can't show this page. Nothing was changed."
        ),
        action="Sign in again; DocFlow will bring you back to this page.",
        severity="info",
        audience="tenant",
    ),
    # ── BIL-0xx · plan changes from the Console (slice 5.9, D-138) ───────────
    "BIL-001": ErrorCatalogEntry(
        code="BIL-001",
        title="This tenant has no subscription yet",
        message=(
            "A plan change moves a live Stripe subscription. This tenant isn't live, so its "
            "plan is still part of the deal terms."
        ),
        action="Change the tier in Deal terms on the Overview; go-live bills whatever is set there.",
        severity="info",
        audience="founder",
    ),
    "BIL-002": ErrorCatalogEntry(
        code="BIL-002",
        title="Only an active tenant's plan can change",
        message=(
            "This tenant is cancelling, suspended or winding down, so changing what it pays "
            "would bill for a service it is leaving."
        ),
        action="Reactivate the tenant first if it is staying, then change the plan.",
        severity="info",
        audience="founder",
    ),
    "BIL-003": ErrorCatalogEntry(
        code="BIL-003",
        title="That plan isn't one DocFlow offers",
        message="The plan asked for doesn't match a current tier.",
        action="Pick Starter, Growth or Scale from the list.",
        severity="warning",
        audience="founder",
    ),
    "BIL-004": ErrorCatalogEntry(
        code="BIL-004",
        title="The tenant is already on that plan",
        message="This tenant already pays the current version of that tier, so there is nothing to change.",
        action="Pick a different tier, or leave the plan as it is.",
        severity="info",
        audience="founder",
    ),
    "BIL-005": ErrorCatalogEntry(
        code="BIL-005",
        title="Stripe didn't make the plan change",
        message=(
            "Stripe refused or didn't answer, so the subscription and DocFlow's record are "
            "both unchanged. The failure has been logged."
        ),
        action="Try again in a few minutes; check the customer in Stripe if it keeps failing.",
        severity="warning",
        audience="founder",
    ),
    # ── EXM-0xx · approved-example prompting (slice 5.10, D-141) ────────────
    "EXM-001": ErrorCatalogEntry(
        code="EXM-001",
        title="Confirm the live golden check first",
        message=(
            "Example prompting changes what the extraction model is sent, so it may only be "
            "switched on after a live golden-fixture run with examples has passed (Section "
            "7.13). The confirmation wasn't ticked, so nothing was changed."
        ),
        action=(
            "Run `pytest -m live_api tests/test_example_prompting_golden.py` in apps/api; when "
            "it passes, tick the confirmation and switch it on again."
        ),
        severity="warning",
        audience="founder",
    ),
    # ── SYS-0xx · DocFlow itself failed (slice 5.9, D-136) ──────────────────
    "SYS-001": ErrorCatalogEntry(
        code="SYS-001",
        title="DocFlow hit a problem on its side",
        message=(
            "DocFlow received your request but failed while handling it. Nothing was "
            "changed, and the failure has been logged."
        ),
        action="Try again in a minute. If it happens again, DocFlow already has the details.",
        severity="high",
        audience="both",
    ),
    # ── TEAM-0xx · the people on an account (slice 5.8d, D-132) ──────────────
    "TEAM-001": ErrorCatalogEntry(
        code="TEAM-001",
        title="That doesn't look like an email address",
        message="DocFlow sends the invite by email, so it needs a complete address to send it to.",
        action="Check the address (it should look like name@company.com) and try again.",
        severity="info",
        audience="tenant",
    ),
    "TEAM-002": ErrorCatalogEntry(
        code="TEAM-002",
        title="This person is already on the team",
        message="Someone with this email address already has access to this account.",
        action=(
            "If they haven't signed in yet, use Resend invite next to their name instead."
        ),
        severity="info",
        audience="tenant",
    ),
    "TEAM-003": ErrorCatalogEntry(
        code="TEAM-003",
        title="This address can't be added here",
        message=(
            "This email address is already used for a different DocFlow sign-in, and one "
            "address can belong to only one account. Nothing was changed."
        ),
        action="Ask the person for another work address to invite, or contact DocFlow if they need both.",
        severity="warning",
        audience="tenant",
    ),
    "TEAM-004": ErrorCatalogEntry(
        code="TEAM-004",
        title="We couldn't create the invite just now",
        message=(
            "DocFlow's sign-in service didn't answer, so no invite was created and nobody was "
            "added. DocFlow has logged the failure."
        ),
        action="Try again in a few minutes.",
        severity="warning",
        audience="tenant",
    ),
    "TEAM-005": ErrorCatalogEntry(
        code="TEAM-005",
        title="They have already signed in",
        message=(
            "This person has set their password and signed in, so there's no invite left to "
            "resend."
        ),
        action="If they've forgotten their password, they can reset it from the sign-in page.",
        severity="info",
        audience="tenant",
    ),
    "TEAM-006": ErrorCatalogEntry(
        code="TEAM-006",
        title="You can't remove yourself",
        message=(
            "Removing your own access would leave the account without you, and you're the "
            "person who manages who's on it."
        ),
        action="Ask DocFlow if the account's admin needs to change.",
        severity="info",
        audience="tenant",
    ),
    "TEAM-007": ErrorCatalogEntry(
        code="TEAM-007",
        title="The account's admin can't be removed here",
        message=(
            "Every account keeps its admin, the person who manages who's on it, so this page "
            "only removes reviewers."
        ),
        action="Contact DocFlow if the account's admin needs to change.",
        severity="info",
        audience="tenant",
    ),
    "TEAM-008": ErrorCatalogEntry(
        code="TEAM-008",
        title="That person isn't on this team",
        message=(
            "There's no one with access to this account matching that request; they may "
            "already have been removed."
        ),
        action="Reload the Team page to see who is on the account now.",
        severity="info",
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
    "REV-006": ErrorCatalogEntry(
        code="REV-006",
        title="We can't find that order",
        message=(
            "There's no order at this address on your account. The link may be mistyped, or "
            "the order may belong to a different account."
        ),
        action="Go back to Purchase orders and open the order from the list.",
        severity="info",
        audience="tenant",
    ),
    # ── CON-0xx · the founder Console (Section 7.15, Phase 5) ────────────────
    # Founder-audience: terser, and allowed to name the moving parts.
    "CON-001": ErrorCatalogEntry(
        code="CON-001",
        title="That intake doesn't exist",
        message="No open intake has that id -- it may have been removed.",
        action="Go back to the intake list and pick it again.",
        severity="warning",
        audience="founder",
    ),
    "CON-002": ErrorCatalogEntry(
        code="CON-002",
        title="That intake already belongs to a tenant",
        message="Its files were moved into a tenant when that tenant was created.",
        action="Open the linked tenant and upload there, or start a new intake.",
        severity="warning",
        audience="founder",
    ),
    "CON-003": ErrorCatalogEntry(
        code="CON-003",
        title="That tier isn't available",
        message="There is no current version of the chosen tier in the tiers table.",
        action="Pick Starter, Growth or Scale; if one is missing, check the tiers table.",
        severity="warning",
        audience="founder",
    ),
    "CON-004": ErrorCatalogEntry(
        code="CON-004",
        title="This tenant has no owner to invite",
        message="The tenant has no active owner user, so there is nobody to send the invite to.",
        action="Check the tenant's users; an owner is created with the tenant.",
        severity="warning",
        audience="founder",
    ),
    "CON-005": ErrorCatalogEntry(
        code="CON-005",
        title="The owner is linked to another login",
        message=(
            "This owner's row already points at a different Supabase sign-in account than the "
            "one for this email address, so re-sending could hand the tenant to the wrong person."
        ),
        action="Check the owner's email address and the Supabase Auth user before trying again.",
        severity="high",
        audience="founder",
    ),
    "CON-006": ErrorCatalogEntry(
        code="CON-006",
        title="Stripe or Supabase didn't answer",
        message=(
            "A call to an outside service failed, so nothing was changed: a tenant is not "
            "created without its Stripe customer, and an invite is not recorded without its link."
        ),
        action="Check the keys in .env and the service's status page, then try again.",
        severity="high",
        audience="founder",
    ),
    "CON-007": ErrorCatalogEntry(
        code="CON-007",
        title="That file isn't one DocFlow accepts",
        message=(
            "The staging upload uses the same file checks as customer intake, and this file "
            "didn't pass them."
        ),
        action="Ask the prospect for the file in a supported format (PDF, Word, Excel, CSV, image).",
        severity="warning",
        audience="founder",
    ),
    # ── IMP / CAT / BUY · catalog and customer-list import (Phase 5, 5.2) ───
    "IMP-001": ErrorCatalogEntry(
        code="IMP-001",
        title="A catalog has to be a spreadsheet",
        message=(
            "This file passed the intake checks, but it is not a table: catalogs and "
            "customer lists are read from CSV or Excel files only."
        ),
        action="Ask the prospect for the list as CSV or Excel (.xlsx, .xls).",
        severity="warning",
        audience="founder",
    ),
    "IMP-002": ErrorCatalogEntry(
        code="IMP-002",
        title="This file has no rows to import",
        message="DocFlow found a header row and nothing under it, or no content at all.",
        action="Check it is the right file, and that the list is on the first sheet.",
        severity="warning",
        audience="founder",
    ),
    "IMP-003": ErrorCatalogEntry(
        code="IMP-003",
        title="This file is too big to import",
        message="It has more rows or columns than one import handles (50,000 rows, 100 columns).",
        action="Split the list into smaller files and import them one after another.",
        severity="warning",
        audience="founder",
    ),
    "IMP-004": ErrorCatalogEntry(
        code="IMP-004",
        title="We couldn't read this spreadsheet",
        message=(
            "The file's contents couldn't be opened as a table -- it may be damaged, or "
            "saved in an unusual way."
        ),
        action="Open it in Excel, save a fresh copy as .xlsx or .csv, and upload that.",
        severity="warning",
        audience="founder",
    ),
    "IMP-005": ErrorCatalogEntry(
        code="IMP-005",
        title="Some rows still need fixing",
        message=(
            "At least one row has a problem that blocks the import, such as a blank or "
            "duplicate SKU, so nothing was committed."
        ),
        action=(
            "Fix the rows marked in red -- here, or in the file and upload it again -- then"
            " commit."
        ),
        severity="warning",
        audience="founder",
    ),
    "IMP-006": ErrorCatalogEntry(
        code="IMP-006",
        title="This import can't be changed any more",
        message="It was already committed or discarded, or is still being read.",
        action="Start a new import from the file to make further changes.",
        severity="warning",
        audience="founder",
    ),
    "IMP-007": ErrorCatalogEntry(
        code="IMP-007",
        title="Map the required columns first",
        message=(
            "Every import needs certain columns -- a SKU and a description for a catalog, a"
            " name for a customer list -- and at least one isn't mapped yet."
        ),
        action="Choose which of the file's columns holds each required field.",
        severity="warning",
        audience="founder",
    ),
    "IMP-008": ErrorCatalogEntry(
        code="IMP-008",
        title="That mapping or fix doesn't fit this file",
        message=(
            "It points at a column or row this file doesn't have, uses one column twice, or"
            " names a field this import doesn't take."
        ),
        action="Reload the import and choose again.",
        severity="warning",
        audience="founder",
    ),
    "CAT-001": ErrorCatalogEntry(
        code="CAT-001",
        title="Rows with no SKU",
        message="These rows have no SKU, so there is nothing to match purchase orders against.",
        action="Add the SKU, or remove the row from the file.",
        severity="warning",
        audience="founder",
    ),
    "CAT-002": ErrorCatalogEntry(
        code="CAT-002",
        title="The same SKU appears more than once",
        message=(
            "Each SKU can be in the catalog once; these rows share one, so DocFlow can't "
            "tell which is right."
        ),
        action="Keep one row per SKU -- fix or remove the others.",
        severity="warning",
        audience="founder",
    ),
    "CAT-003": ErrorCatalogEntry(
        code="CAT-003",
        title="One description, several SKUs",
        message=(
            "These rows describe the item the same way but have different SKUs. Allowed, "
            "but matching may confuse them."
        ),
        action="Check they really are different items; if so, nothing to do.",
        severity="warning",
        audience="founder",
    ),
    "CAT-004": ErrorCatalogEntry(
        code="CAT-004",
        title="SKUs had stray spaces or hidden characters",
        message=(
            "Spaces at the ends of these SKUs, or invisible characters inside them, were "
            "removed so they match what buyers type."
        ),
        action="Nothing to do -- listed so the change is visible.",
        severity="warning",
        audience="founder",
    ),
    "CAT-005": ErrorCatalogEntry(
        code="CAT-005",
        title="A value is too long",
        message="These rows have a value longer than DocFlow stores for that field.",
        action="Shorten the value, or check the column is mapped to the right field.",
        severity="warning",
        audience="founder",
    ),
    "CAT-006": ErrorCatalogEntry(
        code="CAT-006",
        title="Rows with no description",
        message=(
            "These items have a SKU but no description, which makes matching buyers' "
            "wording harder."
        ),
        action=(
            "Add descriptions if the prospect has them; otherwise these can be imported as "
            "they are."
        ),
        severity="warning",
        audience="founder",
    ),
    "CAT-007": ErrorCatalogEntry(
        code="CAT-007",
        title="Retiring SKUs that learned rules use",
        message=(
            "These SKUs are not in the new file, so they will be retired -- but a learned "
            "rule still maps buyers' wording to them."
        ),
        action=(
            "Check the SKUs are really discontinued; if not, add them back to the file "
            "before committing."
        ),
        severity="warning",
        audience="founder",
    ),
    "BUY-001": ErrorCatalogEntry(
        code="BUY-001",
        title="Rows with no customer name",
        message="These rows have no name, so there is no customer to create.",
        action="Add the name, or remove the row.",
        severity="warning",
        audience="founder",
    ),
    "BUY-002": ErrorCatalogEntry(
        code="BUY-002",
        title="The same customer appears more than once",
        message=(
            "These rows have the same name (ignoring case, spacing and punctuation), so "
            "DocFlow can't tell which is right."
        ),
        action="Keep one row per customer.",
        severity="warning",
        audience="founder",
    ),
    "BUY-003": ErrorCatalogEntry(
        code="BUY-003",
        title="Looks like a customer that already exists",
        message=(
            "These names are close to an existing customer's. They will be created and "
            "flagged for you to merge -- never merged automatically."
        ),
        action="Review the merge suggestions after committing.",
        severity="warning",
        audience="founder",
    ),
    "BUY-004": ErrorCatalogEntry(
        code="BUY-004",
        title="The same account number is used twice",
        message="Two rows share an account number, so one of them is probably wrong.",
        action="Fix the account numbers so each customer has its own.",
        severity="warning",
        audience="founder",
    ),
    "BUY-005": ErrorCatalogEntry(
        code="BUY-005",
        title="A value is too long",
        message="These rows have a value longer than DocFlow stores for that field.",
        action="Shorten it, or check the column is mapped to the right field.",
        severity="warning",
        audience="founder",
    ),
    "BUY-006": ErrorCatalogEntry(
        code="BUY-006",
        title="Email addresses that don't look valid",
        message="These values don't look like email addresses. They will be imported as written.",
        action="Correct them if they are typos.",
        severity="warning",
        audience="founder",
    ),
    "BUY-007": ErrorCatalogEntry(
        code="BUY-007",
        title="Names had stray spaces or hidden characters",
        message=(
            "Spaces at the ends of these names, or invisible characters inside them, were "
            "removed."
        ),
        action="Nothing to do -- listed so the change is visible.",
        severity="warning",
        audience="founder",
    ),
    # ── BUY-008..009 · merging buyers (Section 7.6, D-119) ─────────────────
    "BUY-008": ErrorCatalogEntry(
        code="BUY-008",
        title="This pair can't be merged any more",
        message=(
            "Since this list was loaded, the pair was already merged or dismissed, or one of "
            "the two customers was merged into someone else. Nothing was changed."
        ),
        action="Reload the list to see what is still open.",
        severity="info",
        audience="founder",
    ),
    "BUY-009": ErrorCatalogEntry(
        code="BUY-009",
        title="Both customers have a rule for this wording",
        message=(
            "Each of these customers already has a learned rule for the same product wording or "
            "unit. Merging would have to throw one away, and which one is right is your call, so "
            "nothing was merged."
        ),
        action=(
            "Open Learned rules for this tenant, delete the rule that is wrong, then merge again."
        ),
        severity="warning",
        audience="founder",
    ),
    # ── RUL-0xx · learned rules (Section 7.13, D-119) ────────────────────────
    "RUL-001": ErrorCatalogEntry(
        code="RUL-001",
        title="Proposed rules can't be switched on here",
        message=(
            "This rule is only a proposal. Proposals are not part of DocFlow yet, and no rule "
            "becomes active without a person confirming it in a review."
        ),
        action="Nothing to do. Confirm the match in a review if you want the rule.",
        severity="info",
        audience="founder",
    ),
    # ── ONB-0xx · test batch and go-live (Section 7.15.2 Steps 6-9, 5.3) ────
    "ONB-001": ErrorCatalogEntry(
        code="ONB-001",
        title="Load the catalog first",
        message=(
            "The test batch is matched against this tenant's catalog, and no catalog has been "
            "committed yet, so the results wouldn't show how DocFlow will really perform."
        ),
        action="Commit the catalog on the Catalog page, then upload the test batch.",
        severity="warning",
        audience="founder",
    ),
    "ONB-002": ErrorCatalogEntry(
        code="ONB-002",
        title="The test batch is already finished",
        message=(
            "This tenant's test batch was marked complete, so nothing more is added to it or run "
            "as part of it."
        ),
        action=(
            "Nothing to do for onboarding. Anything the customer sends from now on goes through "
            "their normal intake."
        ),
        severity="info",
        audience="founder",
    ),
    "ONB-003": ErrorCatalogEntry(
        code="ONB-003",
        title="Nothing is waiting to run",
        message="Every test-batch document has already been sent for extraction.",
        action="Upload more sample orders first if you want to run more.",
        severity="info",
        audience="founder",
    ),
    "ONB-004": ErrorCatalogEntry(
        code="ONB-004",
        title="Some test orders aren't approved yet",
        message=(
            "The test batch is complete only when every document in it has been reviewed and "
            "approved, and at least one hasn't been."
        ),
        action="Open the remaining test orders from the list on this page and review them.",
        severity="warning",
        audience="founder",
    ),
    "ONB-005": ErrorCatalogEntry(
        code="ONB-005",
        title="Not ready to go live yet",
        message=(
            "Going live needs a completed test batch, so the customer's first real orders are "
            "read with a checked catalog and reviewed settings."
        ),
        action="Finish the test batch on this page, then go live.",
        severity="warning",
        audience="founder",
    ),
    "ONB-006": ErrorCatalogEntry(
        code="ONB-006",
        title="This tenant is already live",
        message="Go-live has already run for this tenant, so nothing was changed or billed again.",
        action="Nothing to do.",
        severity="info",
        audience="founder",
    ),
    "ONB-007": ErrorCatalogEntry(
        code="ONB-007",
        title="The setup fee isn't a usable amount",
        message=(
            "The deal records the setup fee on the tenant, and go-live bills it. The amount was "
            "missing, wasn't a plain number with at most two decimal places, was negative, or "
            "the billing choice wasn't one DocFlow offers."
        ),
        action="Enter the fee as a number like 1500 or 1500.00, choose how it's billed, and save again.",
        severity="warning",
        audience="founder",
    ),
    "ONB-008": ErrorCatalogEntry(
        code="ONB-008",
        title="Stripe didn't finish setting up billing",
        message=(
            "Go-live stopped before the tenant was put live, so their intake address is still "
            "off and no go-live email was sent. Any step Stripe already completed is reused, "
            "never repeated, when you try again."
        ),
        action="Try go-live again. If it fails again, check this customer in the Stripe dashboard.",
        severity="high",
        audience="founder",
    ),
    "ONB-009": ErrorCatalogEntry(
        code="ONB-009",
        title="This tenant has no tier",
        message="Billing and the monthly allowance both come from the tenant's tier, and none is set.",
        action="Set the tier on the tenant, then go live.",
        severity="warning",
        audience="founder",
    ),
    # ── ONB-010..014 · deal terms (D-117) ───────────────────────────────────
    "ONB-010": ErrorCatalogEntry(
        code="ONB-010",
        title="Set the deal terms first",
        message=(
            "Go-live bills the plan and setup fee recorded on the tenant, and this tenant has no "
            "setup fee recorded yet, so nothing was billed or turned on."
        ),
        action="Open Deal terms on this page, choose the setup fee you agreed, save, then go live.",
        severity="warning",
        audience="founder",
    ),
    "ONB-011": ErrorCatalogEntry(
        code="ONB-011",
        title="The deal can't change after go-live",
        message=(
            "This tenant is live, so its plan and setup fee are already with Stripe. Editing the "
            "original deal here would make DocFlow's record disagree with what was billed."
        ),
        action=(
            "Nothing was changed. Moving a live customer to another plan is a tier change, "
            "which comes with billing."
        ),
        severity="info",
        audience="founder",
    ),
    "ONB-012": ErrorCatalogEntry(
        code="ONB-012",
        title="That fee is outside the preset's range",
        message=(
            "Each setup fee preset has the amount (or range) from the pricing document, and the "
            "amount entered falls outside it."
        ),
        action="Enter an amount inside the range shown, or choose Custom and say why in the note.",
        severity="warning",
        audience="founder",
    ),
    "ONB-013": ErrorCatalogEntry(
        code="ONB-013",
        title="Say why in the note",
        message=(
            "A waived or custom setup fee is a departure from the price list, so DocFlow keeps a "
            "reason with it. The note was empty."
        ),
        action="Add a short note, e.g. \"waived for the pilot\", and save again.",
        severity="warning",
        audience="founder",
    ),
    "ONB-014": ErrorCatalogEntry(
        code="ONB-014",
        title="That setup fee option isn't available",
        message="There is no current version of the chosen setup fee preset in the presets table.",
        action=(
            "Choose Founding, Standard, Complex, Waived or Custom; if one is missing, check "
            "setup_fee_presets."
        ),
        severity="warning",
        audience="founder",
    ),
    # ── FLD-0xx · per-tenant field schema (Section 7.13, D-120) ─────────────
    "FLD-001": ErrorCatalogEntry(
        code="FLD-001",
        title="That isn't a field DocFlow reads",
        message=(
            "The list of fields sent to save included a name DocFlow doesn't extract, or a "
            "setting other than required, optional or hidden. Nothing was saved."
        ),
        action="Reload the field settings page and save again.",
        severity="warning",
        audience="founder",
    ),
    "FLD-002": ErrorCatalogEntry(
        code="FLD-002",
        title="This field can't be turned off",
        message=(
            "PO number and quantity stay required for every tenant: an order with no PO number "
            "can't be traced back to what the buyer sent, and a line with no quantity isn't an "
            "order line."
        ),
        action="Leave that field required and save the rest.",
        severity="warning",
        audience="founder",
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
        severity="high",
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
        severity="high",
        audience="both",
    ),
    # ── INT-006 · email intake (Section 7.14, slice 5.6) ────────────────────
    "INT-006": ErrorCatalogEntry(
        code="INT-006",
        title="This account is no longer active",
        message=(
            "DocFlow is no longer reading orders for this account. This email was logged, "
            "not processed."
        ),
        action="Please contact this company directly to send your order another way.",
        severity="info",
        audience="both",
    ),
    # ── LIFE-0xx · tenant lifecycle actions (Section 7.14 / 7.15.4, slice 5.6) ─
    # Founder-audience, like every other Console refusal (CON-0xx, ONB-0xx).
    "LIFE-001": ErrorCatalogEntry(
        code="LIFE-001",
        title="This tenant can't be cancelled from here",
        message=(
            "Cancel only applies to an active tenant. This one is already cancelling, "
            "suspended, pending deletion or deleted."
        ),
        action="Check the tenant's current status on the Lifecycle tab.",
        severity="warning",
        audience="founder",
    ),
    "LIFE-002": ErrorCatalogEntry(
        code="LIFE-002",
        title="Say why, in at least 20 characters",
        message="A for-cause cancellation takes effect immediately, so DocFlow keeps a reason with it.",
        action="Add a short explanation of the cause and submit again.",
        severity="warning",
        audience="founder",
    ),
    "LIFE-003": ErrorCatalogEntry(
        code="LIFE-003",
        title="The effective date can only move later",
        message="The founder may push a cancellation's effective date out, never pull it earlier.",
        action="Choose a date on or after the computed effective date shown.",
        severity="warning",
        audience="founder",
    ),
    "LIFE-004": ErrorCatalogEntry(
        code="LIFE-004",
        title="This tenant can't be reactivated from here",
        message="Reactivate only applies to a tenant that is suspended or pending deletion.",
        action="Check the tenant's current status on the Lifecycle tab.",
        severity="warning",
        audience="founder",
    ),
    "LIFE-005": ErrorCatalogEntry(
        code="LIFE-005",
        title="That name doesn't match",
        message=(
            "Deleting a tenant is irreversible, so DocFlow asks for the tenant's name typed "
            "exactly, not just a click. What was typed didn't match."
        ),
        action="Copy the tenant's name exactly as shown and try again.",
        severity="warning",
        audience="founder",
    ),
    "LIFE-006": ErrorCatalogEntry(
        code="LIFE-006",
        title="This tenant isn't ready to delete",
        message=(
            "A tenant can only be hard-deleted once it is in the pending-deletion window and "
            "that window has elapsed."
        ),
        action="Check the Ready to delete queue -- it only lists tenants past their window.",
        severity="warning",
        audience="founder",
    ),
    # ── LIM / INT / QUA · allowances and quarantine (Section 7.16, slice 5.7) ─
    # `{name}` placeholders are filled by render_error(); get_error() returns
    # the raw template, which is what the snapshot test records.
    "LIM-001": ErrorCatalogEntry(
        code="LIM-001",
        title="You've used most of this month's documents",
        message=(
            "You've used {used} of {allowance} documents included in {tier} this month. "
            "Documents continue to process normally."
        ),
        action="{next_tier} includes {next_allowance} documents per month -- contact us to upgrade.",
        severity="info",
        audience="tenant",
    ),
    "LIM-002": ErrorCatalogEntry(
        code="LIM-002",
        title="You've reached your monthly allowance",
        message=(
            "You've used {used} of {allowance} documents included in {tier} this month. "
            "Documents continue to process normally."
        ),
        action="{next_tier} includes {next_allowance} documents per month -- contact us to upgrade.",
        severity="info",
        audience="tenant",
    ),
    "LIM-003": ErrorCatalogEntry(
        code="LIM-003",
        title="You've used most of this month's documents",
        message=(
            "You've used {used} of {allowance} documents included in {tier} this month. "
            "Documents continue to process normally."
        ),
        action="Contact us if you expect to keep going above this level -- we'll set up a plan that fits.",
        severity="info",
        audience="tenant",
    ),
    "LIM-004": ErrorCatalogEntry(
        code="LIM-004",
        title="You've reached your monthly allowance",
        message=(
            "You've used {used} of {allowance} documents included in {tier} this month. "
            "Documents continue to process normally."
        ),
        action="Contact us if you expect to keep going above this level -- we'll set up a plan that fits.",
        severity="info",
        audience="tenant",
    ),
    "INT-007": ErrorCatalogEntry(
        code="INT-007",
        title="Received and held for review",
        message=(
            "This account received an unusual volume of documents, so new ones are being held "
            "instead of processed until the volume is confirmed as expected. Nothing has been "
            "discarded."
        ),
        action="DocFlow has been alerted and will release them once the volume is confirmed.",
        severity="warning",
        audience="both",
    ),
    "INT-008": ErrorCatalogEntry(
        code="INT-008",
        title="This address has changed",
        message=(
            "Orders for {tenant_name} are now read at a different address, so this email was "
            "logged, not processed."
        ),
        action="Please contact {tenant_name} for the new address and send your order there.",
        severity="info",
        audience="both",
    ),
    "INT-009": ErrorCatalogEntry(
        code="INT-009",
        title="Held: sender isn't on your approved list",
        message=(
            "This account only accepts orders from approved senders, and this sender isn't on "
            "the list, so we're holding the document instead of processing it."
        ),
        action="Tick it and release it, or ask DocFlow to add the sender to your list.",
        severity="warning",
        audience="tenant",
    ),
    "QUA-001": ErrorCatalogEntry(
        code="QUA-001",
        title="Only DocFlow can release these documents",
        message=(
            "These documents are held because of a surge in volume or a failed sender check, "
            "so only DocFlow can release them."
        ),
        action="DocFlow has been alerted and will release them once they're confirmed.",
        severity="info",
        audience="tenant",
    ),
    "QUA-002": ErrorCatalogEntry(
        code="QUA-002",
        title="Those documents are no longer held",
        message="One or more of the documents you chose have already been released or removed.",
        action="Refresh 'Held for review' to see what is still waiting.",
        severity="info",
        audience="tenant",
    ),
    "QUA-003": ErrorCatalogEntry(
        code="QUA-003",
        title="That name doesn't match",
        message="The name typed doesn't exactly match this tenant, so nothing was cleared.",
        action="Type the tenant's name exactly as shown to clear its held documents.",
        severity="warning",
        audience="founder",
    ),
    "QUA-004": ErrorCatalogEntry(
        code="QUA-004",
        title="This address can't be replaced",
        message="This tenant has no active intake address to replace, so no new address was issued.",
        action="Check that the tenant went live and hasn't been suspended, then try again.",
        severity="warning",
        audience="founder",
    ),
    "QUA-005": ErrorCatalogEntry(
        code="QUA-005",
        title="That approved-sender list can't be saved",
        message=(
            "An entry isn't a valid email address or domain, or the list is empty while "
            "the approved-sender rule is on, which would hold every order."
        ),
        action="Enter full addresses (buyer@example.com) or bare domains (example.com), at least one.",
        severity="warning",
        audience="founder",
    ),
}


def render_error(code: str, **params: object) -> ErrorCatalogEntry:
    """The catalog entry with its `{placeholders}` filled (e.g. LIM-002's
    numbers). The wording still lives only in the catalog; callers supply
    values, never prose."""
    entry = get_error(code)
    values = {k: str(v) for k, v in params.items()}
    return ErrorCatalogEntry(
        code=entry.code,
        title=entry.title.format(**values),
        message=entry.message.format(**values),
        action=entry.action.format(**values),
        severity=entry.severity,
        audience=entry.audience,
    )


def get_error(code: str) -> ErrorCatalogEntry:
    if code not in CATALOG:
        raise KeyError(
            f"Unknown error catalog code '{code}'. Every user-facing failure must be "
            "added to docflow_core.errors.CATALOG before it can be raised "
            "(CLAUDE.md Section 7.16.5)."
        )
    return CATALOG[code]
