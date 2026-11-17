"""
An e-mail's own text at the webhook (B1's demo half, slice 1; DECISIONS.md
D-230; docs/designs/b1.md 2.1): what the payload boundary reads, which part
is "the body", and how it is stored. No database: the store is called with a
session that records its statements, against the fake Storage backend.

What the database then holds is proved in the API's suite
(apps/api/tests/test_email_body_intake.py), against CI's database.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from docflow_core import email_intake, file_types, storage
from docflow_core.email_intake import (
    BODY_FILENAMES,
    MAX_DATE_HEADER_CHARS,
    MalformedPayloadError,
    ParsedEmail,
    choose_body,
    parse_postmark_payload,
)
from tests.storage_fake import FakeStorage


@pytest.fixture()
def fake():
    backend = FakeStorage()
    with storage.set_backend_for_tests(backend):
        yield backend


class _RecordingSession:
    """Stands in for a database session: keeps each statement's parameters."""

    def __init__(self):
        self.calls: list[dict] = []

    def execute(self, _statement, params=None):
        self.calls.append(dict(params or {}))


def _payload(**overrides) -> dict:
    payload = {
        "FromFull": {"Email": "buyer@example.test", "Name": "Acme Test Buyer"},
        "Subject": "Test order",
        "MessageID": "msg-1",
        "Date": "Mon, 16 Nov 2026 09:30:00 -0500",
        "Headers": [],
        "Attachments": [],
    }
    payload.update(overrides)
    return payload


def _email(text_body: str = "", html_body: str = "") -> ParsedEmail:
    return ParsedEmail(
        sender_email="buyer@example.test",
        sender_name=None,
        subject="Test order",
        message_id="msg-1",
        headers=[],
        text_body=text_body,
        html_body=html_body,
    )


# ── the payload boundary ─────────────────────────────────────────────────────


def test_the_payload_boundary_reads_both_body_parts_and_the_date_as_sent():
    parsed = parse_postmark_payload(
        _payload(TextBody="Please ship 4 x TEST-100.\r\n", HtmlBody="<p>Please ship 4 x TEST-100.</p>")
    )
    assert parsed.text_body == "Please ship 4 x TEST-100.\r\n"
    assert parsed.html_body == "<p>Please ship 4 x TEST-100.</p>"
    assert parsed.date == "Mon, 16 Nov 2026 09:30:00 -0500"


@pytest.mark.parametrize("missing", [{}, {"TextBody": None, "HtmlBody": None}], ids=["absent", "null"])
def test_an_absent_or_null_body_part_is_no_text(missing):
    payload = _payload(**missing)
    payload.pop("Date")
    parsed = parse_postmark_payload(payload)
    assert (parsed.text_body, parsed.html_body, parsed.date) == ("", "", None)


@pytest.mark.parametrize("key", ["TextBody", "HtmlBody"])
@pytest.mark.parametrize("value", [7, ["an order"], {"text": "an order"}, True])
def test_a_body_part_that_is_not_text_is_a_malformed_payload(key, value):
    with pytest.raises(MalformedPayloadError, match=f"{key} must be text"):
        parse_postmark_payload(_payload(**{key: value}))


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("  Mon, 16 Nov 2026 09:30:00 -0500  ", "Mon, 16 Nov 2026 09:30:00 -0500"),
        ("Mon, 16 Nov 2026\r\n 09:30:00 -0500", "Mon, 16 Nov 2026 09:30:00 -0500"),
        ("Mon,\x00 16 Nov", "Mon, 16 Nov"),
        ("   ", None),
        ("", None),
        (20261116, None),
        (None, None),
    ],
)
def test_the_date_line_is_one_short_line_without_a_nul_or_none(sent, kept):
    assert parse_postmark_payload(_payload(Date=sent)).date == kept


def test_an_overlong_date_line_is_cut():
    parsed = parse_postmark_payload(_payload(Date="9" * (MAX_DATE_HEADER_CHARS + 50)))
    assert parsed.date == "9" * MAX_DATE_HEADER_CHARS


# ── which part is the body ───────────────────────────────────────────────────


def test_no_body_when_both_parts_are_blank():
    assert choose_body("", "") is None
    assert choose_body(" \r\n\t", "\n  ") is None


def test_the_text_part_is_the_body_when_it_has_any_character():
    body = choose_body("4 x TEST-100\n", "")
    assert body is not None
    assert (body.kind, body.content, body.html_also) == ("text", b"4 x TEST-100\n", None)


def test_the_html_part_is_the_body_when_the_text_part_is_blank():
    body = choose_body("  \n", "<table><tr><td>4</td><td>TEST-100</td></tr></table>")
    assert body is not None
    assert body.kind == "html"
    assert body.content == b"<table><tr><td>4</td><td>TEST-100</td></tr></table>"
    assert body.html_also is None


def test_with_both_parts_the_text_part_is_read_and_the_html_part_is_kept_beside_it():
    body = choose_body("4 x TEST-100", "<p>4 x TEST-100</p>")
    assert body is not None
    assert (body.kind, body.content, body.html_also) == ("text", b"4 x TEST-100", b"<p>4 x TEST-100</p>")


def test_the_bytes_are_the_senders_characters_untouched():
    text = "  Qty 4 – TEST-100 é\r\n<script>alert(1)</script>\r\n"
    body = choose_body(text, "")
    assert body is not None and body.content == text.encode("utf-8")


def test_a_lone_surrogate_does_not_fail_the_email():
    body = choose_body("order \ud800 4", "")
    assert body is not None and body.content == b"order ? 4"


# ── storing it ───────────────────────────────────────────────────────────────


def test_a_text_body_is_stored_once_under_the_tenants_prefix_with_its_hash_and_size(fake):
    tenant_id, session = uuid4(), _RecordingSession()
    stored = email_intake._store_body(session, tenant_id, _email(text_body="4 x TEST-100\n"))

    assert stored is not None
    assert stored.kind == "text" and stored.html_path is None
    assert stored.storage_path.startswith(f"tenants/{tenant_id}/uploads/")
    assert stored.storage_path.endswith(".txt")
    assert stored.sha256 == hashlib.sha256(b"4 x TEST-100\n").hexdigest()
    assert stored.size == len(b"4 x TEST-100\n")
    assert fake.objects == {stored.storage_path: b"4 x TEST-100\n"}
    assert session.calls == []


def test_an_html_only_body_is_stored_as_html(fake):
    tenant_id = uuid4()
    stored = email_intake._store_body(_RecordingSession(), tenant_id, _email(html_body="<p>4 x TEST-100</p>"))

    assert stored is not None
    assert stored.kind == "html" and stored.html_path is None
    assert stored.storage_path.endswith(".html")
    assert fake.objects == {stored.storage_path: b"<p>4 x TEST-100</p>"}


def test_with_both_parts_two_objects_are_stored_and_only_the_text_one_is_the_body(fake):
    tenant_id = uuid4()
    stored = email_intake._store_body(
        _RecordingSession(), tenant_id, _email(text_body="4 x TEST-100", html_body="<p>4 x TEST-100</p>")
    )

    assert stored is not None
    assert stored.kind == "text"
    assert stored.html_path is not None and stored.html_path != stored.storage_path
    assert stored.html_path.startswith(f"tenants/{tenant_id}/uploads/") and stored.html_path.endswith(".html")
    assert fake.objects == {stored.storage_path: b"4 x TEST-100", stored.html_path: b"<p>4 x TEST-100</p>"}
    assert stored.sha256 == hashlib.sha256(b"4 x TEST-100").hexdigest()


def test_a_blank_body_stores_nothing(fake):
    assert email_intake._store_body(_RecordingSession(), uuid4(), _email()) is None
    assert fake.objects == {}


def test_a_body_over_the_size_cap_is_not_stored_and_is_refused_with_the_size_code(fake, monkeypatch):
    monkeypatch.setattr(file_types, "MAX_FILE_SIZE_BYTES", 10)
    tenant_id, session = uuid4(), _RecordingSession()

    stored = email_intake._store_body(session, tenant_id, _email(text_body="x" * 11))

    assert stored is None
    assert fake.objects == {}
    assert len(session.calls) == 1
    rejection = session.calls[0]
    assert rejection["error_code"] == "DOC-002"
    assert rejection["original_filename"] == BODY_FILENAMES["text"]
    assert rejection["tenant_id"] == str(tenant_id)
    # The row names the e-mail, never its text (Section 7.10).
    assert "x" * 11 not in repr(rejection)


def test_a_body_exactly_at_the_size_cap_is_stored(fake, monkeypatch):
    monkeypatch.setattr(file_types, "MAX_FILE_SIZE_BYTES", 10)
    stored = email_intake._store_body(_RecordingSession(), uuid4(), _email(text_body="x" * 10))
    assert stored is not None and stored.size == 10


def test_an_html_part_over_the_cap_beside_a_text_body_is_left_out_and_the_body_still_stored(
    fake, monkeypatch
):
    monkeypatch.setattr(file_types, "MAX_FILE_SIZE_BYTES", 10)
    stored = email_intake._store_body(
        _RecordingSession(), uuid4(), _email(text_body="4 x T-1", html_body="<p>" + "x" * 20 + "</p>")
    )
    assert stored is not None and stored.html_path is None
    assert list(fake.objects.values()) == [b"4 x T-1"]


def test_the_tenants_hard_delete_removes_the_stored_body(fake):
    """D-230 item 8: a body goes at the tenant's hard delete, like every
    object under its prefix, and another tenant's body is not touched."""
    tenant_id, other = uuid4(), uuid4()
    stored = email_intake._store_body(
        _RecordingSession(), tenant_id, _email(text_body="4 x TEST-100", html_body="<p>4 x TEST-100</p>")
    )
    kept = email_intake._store_body(_RecordingSession(), other, _email(text_body="another tenant's order"))
    assert stored is not None and kept is not None

    assert storage.delete_tenant_storage(tenant_id) == 2

    assert list(fake.objects) == [kept.storage_path]


def test_the_stored_names_carry_only_a_fixed_extension():
    assert BODY_FILENAMES == {"text": "E-mail text.txt", "html": "E-mail text.html"}
