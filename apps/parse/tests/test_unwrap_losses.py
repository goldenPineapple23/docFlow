"""
Nothing in an email is dropped silently (D-207, PR 1b-ii).

What an .eml or .msg unwrap couldn't read comes back on the answer's `lost`
list, and the worker turns each entry into a warning the reviewer must
acknowledge: VAL-017 names an attachment, VAL-018 the body. The founder's
scope (2026-10-18): every attachment the unwrap didn't read, except an
inline part the body references by Content-ID (a signature logo) and
contact cards / calendar invites (.vcf, .ics). A forwarded email attached as
a message does raise it.

The order is still read from what remains; these tests check both halves.
Every fixture is built here with obviously fake content (Section 0 rule 4).
"""

from __future__ import annotations

import base64
import email.message
import json
from email.message import EmailMessage
from email.policy import default as default_policy

import pytest
from docflow_core import file_types

from parse_service.job import _parse
from parse_service.parsing import documents
from tests import fixture_builders as fb
from tests.conftest import FIXTURES
from tests.test_fault_origin import SECRET, _raiser

PO = fb.PO_TEXT.encode("utf-8")
# Not on the allowlist, so re-validation refuses it: an attachment the
# unwrap couldn't read.
UNREADABLE = b"PK\x05\x06" + b"\x00" * 18  # an empty zip archive


def _eml(*, html: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "PO BCH-2291"
    message["From"] = "orders@acmetestcoffee.example"
    message["To"] = "orders@acmetestdistributor.example"
    message["Date"] = "Sat, 14 Mar 2026 09:00:00 -0700"
    message.set_content("Our order is attached.")
    if html is not None:
        message.add_alternative(html, subtype="html")
    return message


def _answer(content: bytes, extension: str) -> dict:
    answer = documents.parse(content, f"upload{extension}")
    assert answer["outcome"] == "ok", answer
    return answer


def _text(answer: dict) -> str:
    return "\n".join(part["text"] for part in answer["parts"] if part["type"] == "text")


def _lost(answer: dict) -> list[tuple[str, str | None]]:
    return [(item["code"], item["filename"]) for item in answer["lost"]]


# ── .eml ─────────────────────────────────────────────────────────────────────


def test_an_unreadable_attachment_is_named_and_the_good_one_is_still_read():
    message = _eml()
    message.add_attachment(UNREADABLE, maintype="application", subtype="zip", filename="old orders.zip")
    message.add_attachment(PO, maintype="text", subtype="plain", filename="purchase_order.txt")
    answer = _answer(message.as_bytes(), ".eml")
    assert _lost(answer) == [("VAL-017", "old_orders.zip")]
    assert "CF-1001" in _text(answer)


def test_an_empty_attachment_is_named():
    message = _eml()
    message.add_attachment(b"", maintype="application", subtype="pdf", filename="po.pdf")
    assert _lost(_answer(message.as_bytes(), ".eml")) == [("VAL-017", "po.pdf")]


def test_a_forwarded_email_attached_as_a_message_is_named():
    inner = _eml()
    inner.set_content("SECRET-INNER-BODY")
    outer = _eml()
    outer.add_attachment(inner)  # message/rfc822: no payload of its own to decode
    answer = _answer(outer.as_bytes(), ".eml")
    assert _lost(answer) == [("VAL-017", "attachment")]
    assert "SECRET-INNER-BODY" not in _text(answer)


def test_a_forwarded_email_attached_as_an_eml_file_is_named():
    outer = fb.build_eml(attachments=[("forwarded.eml", "octet-stream", fb.build_eml())])
    assert _lost(_answer(outer, ".eml")) == [("VAL-017", "forwarded.eml")]


def test_a_logo_inside_the_bodys_own_related_part_is_never_an_attachment():
    """The common shape (mixed > alternative > related > html + logo): the
    standard library's iter_attachments treats the whole alternative as the
    body, so the logo is not an attachment and nothing is decided about it."""
    message = _eml(html='<p>Order attached.</p><img src="cid:logo123@acmetest.example">')
    body = message.get_body(preferencelist=("html",))
    body.add_related(
        b"\x01\x00\x00\x00" + b"\x00" * 60, maintype="image", subtype="x-emf", cid="<logo123@acmetest.example>",
        filename="image001.emz",
    )
    message.add_attachment(PO, maintype="text", subtype="plain", filename="purchase_order.txt")
    answer = _answer(message.as_bytes(), ".eml")
    assert _lost(answer) == []
    assert "CF-1001" in _text(answer)


def test_an_inline_part_the_body_does_not_reference_is_a_loss():
    message = _eml(html="<p>Order attached.</p>")
    body = message.get_body(preferencelist=("html",))
    body.add_related(
        b"\x01\x00\x00\x00" + b"\x00" * 60, maintype="image", subtype="x-emf", cid="<other@acmetest.example>",
        filename="image001.emz",
    )
    # A top-level multipart/related so the inline part is an attachment of
    # the message itself, as some mailers send it.
    related = email.message_from_bytes(body.as_bytes(), policy=default_policy)
    for header in ("Subject", "From", "To", "Date"):
        related[header] = message[header]
    assert _lost(_answer(related.as_bytes(), ".eml")) == [("VAL-017", "image001.emz")]


def test_a_top_level_related_logo_the_body_references_is_not_a_loss():
    message = _eml(html='<p>Order attached.</p><img src="cid:logo123@acmetest.example">')
    body = message.get_body(preferencelist=("html",))
    body.add_related(
        b"\x01\x00\x00\x00" + b"\x00" * 60, maintype="image", subtype="x-emf", cid="<logo123@acmetest.example>",
        filename="image001.emz",
    )
    related = email.message_from_bytes(body.as_bytes(), policy=default_policy)
    for header in ("Subject", "From", "To", "Date"):
        related[header] = message[header]
    assert _lost(_answer(related.as_bytes(), ".eml")) == []


def _eml_with_part(content_type: str, filename: str | None, payload: bytes) -> bytes:
    """One attachment with exactly these headers, built by hand so the type
    and the name can disagree."""
    disposition = f'attachment; filename="{filename}"' if filename else "attachment"
    return (
        b"Subject: PO BCH-2291\r\nFrom: orders@acmetestcoffee.example\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="b1"\r\n\r\n'
        b"--b1\r\nContent-Type: text/plain\r\n\r\nOur order is attached.\r\n"
        + f"--b1\r\nContent-Type: {content_type}\r\nContent-Disposition: {disposition}\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\n".encode()
        + base64.encodebytes(payload)
        + b"--b1--\r\n"
    )


# Bytes no allowlist entry matches, so re-validation refuses the attachment
# and only the exclusion can keep it from being VAL-017.
BINARY = bytes(range(256)) * 4


@pytest.mark.parametrize(
    "filename,content_type",
    [
        ("contact.vcf", "text/vcard"),  # by extension
        ("invite.ics", "text/calendar"),
        ("invite.ics", "application/octet-stream"),  # the extension decides
        (None, "text/calendar"),  # no filename: the type decides
        (None, "text/x-vcard"),
    ],
)
def test_contact_cards_and_calendar_invites_are_not_a_loss(filename, content_type):
    assert _lost(_answer(_eml_with_part(content_type, filename, BINARY), ".eml")) == []


@pytest.mark.parametrize("content_type", ["text/calendar", "text/vcard"])
def test_a_named_file_that_is_not_ics_or_vcf_counts_whatever_its_type_says(content_type):
    """Founder, 2026-10-18: PO-4471.pdf typed as an invite is still a lost
    attachment. The type excludes only an attachment with no filename."""
    content = _eml_with_part(content_type, "PO-4471.pdf", BINARY)
    assert _lost(_answer(content, ".eml")) == [("VAL-017", "PO-4471.pdf")]


def test_an_unnamed_calendar_invite_is_read_as_text_not_lost():
    message = _eml()
    message.add_attachment("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", subtype="calendar")
    answer = _answer(message.as_bytes(), ".eml")
    assert _lost(answer) == [] and "BEGIN:VCALENDAR" in _text(answer)


def test_attachments_past_the_cap_are_each_named():
    message = _eml()
    count = file_types.MAX_EMBEDDED_ATTACHMENTS + 2
    for index in range(count):
        message.add_attachment(PO, maintype="text", subtype="plain", filename=f"po_{index:02d}.txt")
    answer = _answer(message.as_bytes(), ".eml")
    cap = file_types.MAX_EMBEDDED_ATTACHMENTS
    assert _lost(answer) == [("VAL-017", f"po_{index:02d}.txt") for index in range(cap, count)]


def test_an_undecodable_body_is_val_018_and_the_attachment_is_still_read():
    raw = (
        b"Subject: PO BCH-2291\r\n"
        b"From: orders@acmetestcoffee.example\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="b1"\r\n\r\n'
        b"--b1\r\n"
        b'Content-Type: text/plain; charset="x-no-such-charset"\r\n\r\n'
        b"Our order is attached.\r\n"
        b"--b1\r\n"
        b'Content-Type: text/plain; name="purchase_order.txt"\r\n'
        b'Content-Disposition: attachment; filename="purchase_order.txt"\r\n\r\n'
        + PO.replace(b"\n", b"\r\n")
        + b"\r\n--b1--\r\n"
    )
    answer = _answer(raw, ".eml")
    assert _lost(answer) == [("VAL-018", None)]
    assert "CF-1001" in _text(answer)


def test_a_long_filename_is_the_last_80_characters_of_the_sanitized_label():
    name = "Purchase Order " + "x" * 120 + ".zip"
    message = _eml()
    message.add_attachment(UNREADABLE, maintype="application", subtype="zip", filename=name)
    ((code, label),) = _lost(_answer(message.as_bytes(), ".eml"))
    assert code == "VAL-017" and len(label) == 80 and label.endswith("xxx.zip") and " " not in label


def test_an_ordinary_email_loses_nothing():
    assert _answer(fb.build_eml(), ".eml")["lost"] == []
    assert _answer((FIXTURES / "positive" / "po.eml").read_bytes(), ".eml")["lost"] == []


def test_a_document_that_is_not_an_email_has_an_empty_lost_list():
    assert _answer(PO, ".txt")["lost"] == []


def test_an_email_with_nothing_readable_is_still_rejected_not_warned():
    message = EmailMessage()
    message.add_attachment(UNREADABLE, maintype="application", subtype="zip", filename="orders.zip")
    answer = documents.parse(message.as_bytes(), "upload.eml")
    assert answer["outcome"] == "rejected"
    assert "lost" not in answer


# ── .msg ─────────────────────────────────────────────────────────────────────


def test_a_msg_attachment_stored_as_a_message_is_named():
    content = fb.build_msg(attachments=[("Fwd PO 2291.msg", None), ("purchase_order.txt", PO)])
    answer = _answer(content, ".msg")
    assert _lost(answer) == [("VAL-017", "Fwd_PO_2291.msg")]
    assert "CF-1001" in _text(answer)


def test_a_msg_attachment_re_validation_refuses_is_named():
    content = fb.build_msg(attachments=[("orders.zip", UNREADABLE)])
    assert _lost(_answer(content, ".msg")) == [("VAL-017", "orders.zip")]


def test_a_msg_inline_logo_the_html_body_references_is_not_a_loss():
    content = fb.build_msg(
        html_body=b'<html><body><img src="cid:image001.emz@01DA0000.00000000"></body></html>',
        attachments=[
            ("image001.emz", b"\x01\x00\x00\x00" + b"\x00" * 60, {"3712": "image001.emz@01DA0000.00000000"}),
        ],
    )
    assert _lost(_answer(content, ".msg")) == []


def test_a_msg_inline_part_with_no_html_reference_is_a_loss():
    content = fb.build_msg(
        attachments=[("image001.emz", b"\x01\x00\x00\x00" + b"\x00" * 60, {"3712": "image001.emz@01DA"})],
    )
    assert _lost(_answer(content, ".msg")) == [("VAL-017", "image001.emz")]


def test_a_msg_calendar_invite_is_not_a_loss():
    named = fb.build_msg(attachments=[("invite.ics", BINARY)])
    unnamed = fb.build_msg(attachments=[(None, BINARY, {"370E": "text/calendar"})])
    assert _lost(_answer(named, ".msg")) == []
    assert _lost(_answer(unnamed, ".msg")) == []


def test_a_named_msg_attachment_typed_as_an_invite_still_counts():
    content = fb.build_msg(attachments=[("PO-4471.pdf", BINARY, {"370E": "text/calendar"})])
    assert _lost(_answer(content, ".msg")) == [("VAL-017", "PO-4471.pdf")]


def test_msg_attachments_past_the_cap_are_each_named():
    count = file_types.MAX_EMBEDDED_ATTACHMENTS + 1
    content = fb.build_msg(attachments=[(f"po_{index:02d}.txt", PO) for index in range(count)])
    assert _lost(_answer(content, ".msg")) == [("VAL-017", f"po_{count - 1:02d}.txt")]


def test_an_ordinary_msg_loses_nothing():
    assert _answer(fb.build_msg(attachments=[("po.txt", PO)]), ".msg")["lost"] == []


# ── the two narrowed blocks: the library's failure is VAL-0xx, ours is DOC-030 ──


def test_the_body_block_counts_a_library_failure_as_the_emails(monkeypatch):
    monkeypatch.setattr(email.message.MIMEPart, "get_content", _raiser(LookupError, "library"))
    answer = _parse("document", fb.build_eml(attachments=[("po.txt", "octet-stream", PO)]), "upload.eml")
    assert answer["outcome"] == "ok" and ("VAL-018", None) in _lost(answer)


@pytest.mark.parametrize("exc_type", [TypeError, RuntimeError])
def test_the_body_block_answers_error_for_our_own_failure(monkeypatch, exc_type):
    monkeypatch.setattr(email.message.MIMEPart, "get_content", _raiser(exc_type, "ours"))
    answer = _parse("document", fb.build_eml(), "upload.eml")
    assert answer["outcome"] == "error" and answer["error_type"] == exc_type.__name__
    assert SECRET not in json.dumps(answer)


def _payload_raising(where: str, exc_type: type[BaseException]):
    real = email.message.Message.get_payload
    boom = _raiser(exc_type, where)

    def get_payload(self, i=None, decode=False):
        if decode and self.get_filename() == "po.txt":
            boom()
        return real(self, i, decode)

    return get_payload


def test_the_attachment_block_counts_a_library_failure_as_the_emails(monkeypatch):
    monkeypatch.setattr(email.message.Message, "get_payload", _payload_raising("library", ValueError))
    answer = _parse("document", fb.build_eml(attachments=[("po.txt", "octet-stream", PO)]), "upload.eml")
    assert answer["outcome"] == "ok" and _lost(answer) == [("VAL-017", "po.txt")]


def test_the_attachment_block_answers_error_for_our_own_failure(monkeypatch):
    monkeypatch.setattr(email.message.Message, "get_payload", _payload_raising("ours", TypeError))
    answer = _parse("document", fb.build_eml(attachments=[("po.txt", "octet-stream", PO)]), "upload.eml")
    assert answer["outcome"] == "error" and answer["error_type"] == "TypeError"
