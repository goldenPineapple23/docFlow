"""
An e-mail's own text through POST /intake/email/{token}, against the real
database and Storage (B1's demo half, slice 1; DECISIONS.md D-230;
docs/designs/b1.md 2.1 and section 4).

Slice 1 stores the text and links each attachment's document to its e-mail.
It changes no decision: an e-mail with text and no attachment is still
INT-001, nothing is sent for labelling, and `body_state` stays 'none'.

Also here: what migration 0041's new links and checks do in the database
itself, since the round trip in CI's core job compares definitions only.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest
from docflow_core import storage
from docflow_core.db import platform_session
from docflow_core.storage import CrossTenantStoragePathError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import intake_webhook_headers, requires_mail_body_schema
from tests.test_email_intake import (
    _FakeCeleryClient,
    _pm_attachment,
    _pm_payload,
    _TestIntakeTenant,
)

pytestmark = requires_mail_body_schema

ORDER_TEXT = "Please ship:\r\n4 x TEST-100 hex bolt\r\n2 x TEST-200 washer\r\nPO TEST-PO-7\r\n"
ORDER_HTML = (
    "<html><body><table><tr><td>4</td><td>TEST-100</td></tr></table><script>x()</script></body></html>"
)
DATE_LINE = "Mon, 16 Nov 2026 09:30:00 -0500"


def _post(client, tenant, *, text_body="", html_body="", attachments=None, **kwargs):
    payload = _pm_payload("body-buyer@example.test", attachments=attachments or [], **kwargs)
    payload.update({"TextBody": text_body, "HtmlBody": html_body, "Date": DATE_LINE})
    response = client.post(f"/intake/email/{tenant.token}", json=payload, headers=intake_webhook_headers())
    assert response.status_code == 200
    return response.json()["outcome"]


def _raw_email(tenant) -> dict:
    with platform_session() as session:
        rows = (
            session.execute(
                text(
                    "SELECT id, outcome, date_header, body_storage_path, body_kind, body_sha256, body_bytes, "
                    "body_html_path, body_text_path, body_state, body_label, body_label_confidence, "
                    "body_contains_order_lines, body_order_lines_confidence, body_injection_suspected, "
                    "body_labelled_at FROM raw_emails WHERE tenant_id = :t"
                ),
                {"t": str(tenant.tenant_id)},
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    return dict(rows[0])


def _document_links(tenant) -> list[tuple]:
    with platform_session() as session:
        return [
            tuple(row)
            for row in session.execute(
                text("SELECT raw_email_id, email_part, status FROM documents WHERE tenant_id = :t"),
                {"t": str(tenant.tenant_id)},
            ).all()
        ]


@pytest.fixture()
def celery(monkeypatch):
    fake = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake)
    return fake


# ── the text is stored, as sent ──────────────────────────────────────────────


def test_a_text_body_beside_an_attachment_is_stored_and_the_document_carries_its_email(client, celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant, _TestIntakeTenant("Other Test Co") as other:
        outcome = _post(
            client,
            tenant,
            text_body=ORDER_TEXT,
            attachments=[_pm_attachment("po.txt", b"PO Number: TEST-0001\n")],
        )
        assert outcome == "processed"

        raw = _raw_email(tenant)
        sent = ORDER_TEXT.encode("utf-8")
        assert raw["outcome"] == "processed"
        assert raw["date_header"] == DATE_LINE
        assert raw["body_kind"] == "text"
        assert raw["body_sha256"] == hashlib.sha256(sent).hexdigest()
        assert raw["body_bytes"] == len(sent)
        assert raw["body_html_path"] is None
        assert raw["body_storage_path"].startswith(f"tenants/{tenant.tenant_id}/uploads/")
        assert raw["body_storage_path"].endswith(".txt")
        # The bytes in Storage are the sender's, untouched.
        assert storage.read_file(tenant.tenant_id, raw["body_storage_path"]) == sent
        # Section 7.5: another tenant cannot read it by its path.
        with pytest.raises(CrossTenantStoragePathError):
            storage.read_file(other.tenant_id, raw["body_storage_path"])

        # Nothing was decided about the text: no label, nothing waiting.
        assert raw["body_state"] == "none"
        for column in (
            "body_text_path",
            "body_label",
            "body_label_confidence",
            "body_contains_order_lines",
            "body_order_lines_confidence",
            "body_injection_suspected",
            "body_labelled_at",
        ):
            assert raw[column] is None, column

        assert _document_links(tenant) == [(raw["id"], "attachment", "pending")]
        # The same single nudge as before; no other task.
        assert celery.sent == [{"name": "docflow.dispatch", "args": None, "queue": "dispatch"}]


def test_an_email_with_text_and_no_attachment_is_still_int_001_and_its_text_is_kept(client, celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        assert _post(client, tenant, text_body=ORDER_TEXT) == "rejected"

        assert tenant.documents() == []
        assert [row["error_code"] for row in tenant.intake_rejections()] == ["INT-001"]
        raw = _raw_email(tenant)
        assert raw["outcome"] == "rejected"
        assert raw["body_kind"] == "text" and raw["body_state"] == "none"
        assert storage.read_file(tenant.tenant_id, raw["body_storage_path"]) == ORDER_TEXT.encode("utf-8")
        assert celery.sent == []


def test_an_html_only_body_is_stored_as_the_senders_html(client, celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        _post(client, tenant, html_body=ORDER_HTML, attachments=[_pm_attachment("po.txt", b"PO TEST-0002\n")])

        raw = _raw_email(tenant)
        assert raw["body_kind"] == "html"
        assert raw["body_storage_path"].endswith(".html")
        assert raw["body_html_path"] is None
        # Markup, script and all: nothing is cleaned in the web process.
        assert storage.read_file(tenant.tenant_id, raw["body_storage_path"]) == ORDER_HTML.encode("utf-8")


def test_with_both_parts_the_text_is_the_body_and_the_html_is_kept_beside_it(client, celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        _post(
            client,
            tenant,
            text_body=ORDER_TEXT,
            html_body=ORDER_HTML,
            attachments=[_pm_attachment("po.txt", b"PO TEST-0003\n")],
        )

        raw = _raw_email(tenant)
        assert raw["body_kind"] == "text"
        assert raw["body_html_path"] and raw["body_html_path"] != raw["body_storage_path"]
        assert raw["body_html_path"].startswith(f"tenants/{tenant.tenant_id}/uploads/")
        assert storage.read_file(tenant.tenant_id, raw["body_storage_path"]) == ORDER_TEXT.encode("utf-8")
        assert storage.read_file(tenant.tenant_id, raw["body_html_path"]) == ORDER_HTML.encode("utf-8")
        assert raw["body_sha256"] == hashlib.sha256(ORDER_TEXT.encode("utf-8")).hexdigest()


def test_a_blank_body_leaves_every_body_column_empty(client, celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        _post(client, tenant, text_body=" \r\n", attachments=[_pm_attachment("po.txt", b"PO TEST-0004\n")])

        raw = _raw_email(tenant)
        assert raw["body_state"] == "none"
        for column in ("body_storage_path", "body_kind", "body_sha256", "body_bytes", "body_html_path"):
            assert raw[column] is None, column
        # The document still carries its e-mail.
        assert _document_links(tenant) == [(raw["id"], "attachment", "pending")]


def test_a_held_emails_text_is_stored_and_every_held_document_carries_the_email(client, celery):
    """The attachment cap counts attachments only: 11 quarantines all 11, the
    text is kept, and it is not sent anywhere."""
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        attachments = [_pm_attachment(f"po-{i}.txt", f"PO number {i}".encode()) for i in range(11)]
        assert _post(client, tenant, text_body=ORDER_TEXT, attachments=attachments) == "quarantined"

        raw = _raw_email(tenant)
        assert raw["outcome"] == "quarantined"
        assert raw["body_kind"] == "text" and raw["body_state"] == "none"
        links = _document_links(tenant)
        assert len(links) == 11
        assert set(links) == {(raw["id"], "attachment", "quarantined")}
        assert celery.sent == []


def test_a_second_delivery_of_the_same_email_stores_nothing_more(client, celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        message_id = f"<{uuid4()}@mail.example.test>"
        attachments = [_pm_attachment("po.txt", b"PO TEST-0005\n")]
        assert _post(
            client, tenant, text_body=ORDER_TEXT, attachments=attachments, message_id=message_id
        ) == ("processed")
        first = _raw_email(tenant)
        assert _post(
            client, tenant, text_body=ORDER_TEXT, attachments=attachments, message_id=message_id
        ) == ("duplicate")
        assert _raw_email(tenant) == first
        assert len(tenant.documents()) == 1


def test_mail_to_an_address_that_is_not_live_keeps_no_text(client, celery):
    """Not live means nothing is read (D-114): the row and the reply as
    before, and the text is not stored, as its attachments are not."""
    with _TestIntakeTenant("Acme Test Distributor", live=False) as tenant:
        assert (
            _post(
                client,
                tenant,
                text_body=ORDER_TEXT,
                attachments=[_pm_attachment("po.txt", b"PO TEST-0006\n")],
            )
            == "rejected"
        )

        raw = _raw_email(tenant)
        assert raw["body_storage_path"] is None and raw["body_state"] == "none"
        assert [row["error_code"] for row in tenant.intake_rejections()] == ["INT-005"]


# ── what 0041's links and checks do in the database ──────────────────────────


def _insert_email(session, tenant_id: UUID, **columns) -> UUID:
    raw_email_id = uuid4()
    names = ["id", "tenant_id", "outcome", *columns]
    values = {"id": str(raw_email_id), "tenant_id": str(tenant_id), "outcome": "processed", **columns}
    session.execute(
        text(f"INSERT INTO raw_emails ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"),
        values,
    )
    return raw_email_id


def _insert_document(session, tenant_id: UUID, raw_email_id: UUID | None, email_part: str | None) -> UUID:
    document_id = uuid4()
    session.execute(
        text(
            "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
            "content_sha256, raw_email_id, email_part) VALUES (:id, :t, 'seed.txt', 'tenants/seed/seed.txt', "
            "'email', 'pending', :sha, :raw_email_id, :email_part)"
        ),
        {
            "id": str(document_id),
            "t": str(tenant_id),
            "sha": uuid4().hex,
            "raw_email_id": str(raw_email_id) if raw_email_id else None,
            "email_part": email_part,
        },
    )
    return document_id


def _insert_run(
    session, tenant_id: UUID, *, document_id=None, raw_email_id=None, run_kind="mail_label"
) -> UUID:
    run_id = uuid4()
    session.execute(
        text(
            "INSERT INTO extraction_runs (id, tenant_id, document_id, raw_email_id, run_kind, succeeded) "
            "VALUES (:id, :t, :document_id, :raw_email_id, :run_kind, true)"
        ),
        {
            "id": str(run_id),
            "t": str(tenant_id),
            "document_id": str(document_id) if document_id else None,
            "raw_email_id": str(raw_email_id) if raw_email_id else None,
            "run_kind": run_kind,
        },
    )
    return run_id


def test_a_label_run_needs_no_document_and_goes_with_its_email():
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with platform_session() as session:
            raw_email_id = _insert_email(session, tenant.tenant_id)
            document_id = _insert_document(session, tenant.tenant_id, raw_email_id, "body")
            run_id = _insert_run(session, tenant.tenant_id, raw_email_id=raw_email_id)

        with platform_session() as session:
            # The document goes first, as in the tenant's hard delete: the
            # e-mail and its label run stay.
            session.execute(text("DELETE FROM documents WHERE id = :d"), {"d": str(document_id)})
            assert (
                session.execute(
                    text("SELECT count(*) FROM raw_emails WHERE id = :e"), {"e": str(raw_email_id)}
                ).scalar_one()
                == 1
            )
            assert (
                session.execute(
                    text("SELECT count(*) FROM extraction_runs WHERE id = :r"), {"r": str(run_id)}
                ).scalar_one()
                == 1
            )
            # Then the e-mail: its label run goes with it.
            session.execute(text("DELETE FROM raw_emails WHERE id = :e"), {"e": str(raw_email_id)})
            assert (
                session.execute(
                    text("SELECT count(*) FROM extraction_runs WHERE id = :r"), {"r": str(run_id)}
                ).scalar_one()
                == 0
            )


def _body_documents(session, raw_email_id: UUID) -> int:
    return session.execute(
        text("SELECT count(*) FROM documents WHERE raw_email_id = :e AND email_part = 'body'"),
        {"e": str(raw_email_id)},
    ).scalar_one()


def test_a_second_body_document_for_the_same_email_is_refused():
    """Decision 5 (founder, 2026-11-17): the partial unique index allows at
    most one document made from an e-mail's text."""
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with platform_session() as session:
            raw_email_id = _insert_email(session, tenant.tenant_id)
            _insert_document(session, tenant.tenant_id, raw_email_id, "body")
        with (
            pytest.raises(IntegrityError, match="idx_documents_one_body_per_email"),
            platform_session() as session,
        ):
            _insert_document(session, tenant.tenant_id, raw_email_id, "body")
        with platform_session() as session:
            assert _body_documents(session, raw_email_id) == 1


def test_the_slice_4_insert_with_on_conflict_do_nothing_leaves_one_body_document_and_raises_nothing():
    """The recipe slice 4's labelling task and the stuck sweep both use
    (founder, 2026-11-17): ON CONFLICT (raw_email_id) WHERE email_part =
    'body' DO NOTHING, then read the existing row. Proved here against the
    real index: if the conflict target didn't match it, Postgres would refuse
    the statement ("no unique or exclusion constraint matching")."""
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with platform_session() as session:
            raw_email_id = _insert_email(session, tenant.tenant_id)
            first = _insert_document(session, tenant.tenant_id, raw_email_id, "body")
        with platform_session() as session:
            inserted = session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
                    "content_sha256, raw_email_id, email_part) VALUES (:id, :t, 'seed.txt', "
                    "'tenants/seed/seed.txt', 'email', 'pending', :sha, :e, 'body') "
                    "ON CONFLICT (raw_email_id) WHERE email_part = 'body' DO NOTHING RETURNING id"
                ),
                {"id": str(uuid4()), "t": str(tenant.tenant_id), "sha": uuid4().hex, "e": str(raw_email_id)},
            ).first()
            assert inserted is None
            existing = session.execute(
                text("SELECT id FROM documents WHERE raw_email_id = :e AND email_part = 'body'"),
                {"e": str(raw_email_id)},
            ).scalar_one()
        assert UUID(str(existing)) == first


def test_the_index_leaves_attachments_other_emails_and_unlinked_body_documents_alone():
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with platform_session() as session:
            one, two = _insert_email(session, tenant.tenant_id), _insert_email(session, tenant.tenant_id)
            # Several attachments' documents on one e-mail, beside its body document.
            _insert_document(session, tenant.tenant_id, one, "attachment")
            _insert_document(session, tenant.tenant_id, one, "attachment")
            _insert_document(session, tenant.tenant_id, one, "body")
            # A body document on another e-mail.
            _insert_document(session, tenant.tenant_id, two, "body")
            # Body documents whose e-mail is gone (raw_email_id NULL) are not
            # one e-mail's: NULLs never conflict.
            _insert_document(session, tenant.tenant_id, None, "body")
            _insert_document(session, tenant.tenant_id, None, "body")
            assert (_body_documents(session, one), _body_documents(session, two)) == (1, 1)
        with platform_session() as session:
            # Deleting both e-mails blanks their documents' links, which leaves
            # four unlinked body documents and still no conflict.
            session.execute(
                text("DELETE FROM raw_emails WHERE id = ANY(CAST(string_to_array(:ids, ',') AS uuid[]))"),
                {"ids": f"{one},{two}"},
            )
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM documents WHERE tenant_id = :t AND raw_email_id IS NULL "
                        "AND email_part = 'body'"
                    ),
                    {"t": str(tenant.tenant_id)},
                ).scalar_one()
                == 4
            )


def test_deleting_an_email_leaves_its_documents_and_blanks_their_link():
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with platform_session() as session:
            raw_email_id = _insert_email(session, tenant.tenant_id)
            document_id = _insert_document(session, tenant.tenant_id, raw_email_id, "attachment")
        with platform_session() as session:
            session.execute(text("DELETE FROM raw_emails WHERE id = :e"), {"e": str(raw_email_id)})
            row = session.execute(
                text("SELECT raw_email_id, email_part FROM documents WHERE id = :d"), {"d": str(document_id)}
            ).one()
        assert tuple(row) == (None, "attachment")


_REFUSED = [
    (
        "a run with neither a document nor an e-mail",
        "INSERT INTO extraction_runs (id, tenant_id, run_kind, succeeded) "
        "VALUES (:id, :t, 'mail_label', true)",
    ),
    (
        "a run kind outside the list",
        "INSERT INTO extraction_runs (id, tenant_id, raw_email_id, run_kind, succeeded) "
        "VALUES (:id, :t, :e, 'mail_guess', true)",
    ),
    ("a body state outside the list", "UPDATE raw_emails SET body_state = 'skipped' WHERE id = :e"),
    ("a label outside the six", "UPDATE raw_emails SET body_label = 'invoice' WHERE id = :e"),
    ("a confidence above 1", "UPDATE raw_emails SET body_order_lines_confidence = 1.001 WHERE id = :e"),
    ("a confidence below 0", "UPDATE raw_emails SET body_label_confidence = -0.001 WHERE id = :e"),
    ("a body kind outside the two", "UPDATE raw_emails SET body_kind = 'rtf' WHERE id = :e"),
    (
        "a stored path without its kind, hash and size",
        "UPDATE raw_emails SET body_storage_path = 'tenants/seed/body.txt' WHERE id = :e",
    ),
    ("an outcome outside the list", "UPDATE raw_emails SET outcome = 'labelled' WHERE id = :e"),
    (
        "an e-mail part outside the two",
        "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
        "content_sha256, email_part) VALUES (:id, :t, 'seed.txt', 'tenants/seed/seed.txt', 'email', "
        "'pending', 'x', 'inline')",
    ),
    (
        "an ignored-mail label outside the six",
        "INSERT INTO intake_rejections (id, tenant_id, source, error_code, mail_label) "
        "VALUES (:id, :t, 'email', 'INT-001', 'spam')",
    ),
]


@pytest.mark.parametrize(("what", "statement"), _REFUSED, ids=[case[0] for case in _REFUSED])
def test_the_database_refuses(what, statement):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with platform_session() as session:
            raw_email_id = _insert_email(session, tenant.tenant_id)
        with pytest.raises(IntegrityError), platform_session() as session:
            session.execute(
                text(statement),
                {
                    key: value
                    for key, value in {
                        "id": str(uuid4()),
                        "t": str(tenant.tenant_id),
                        "e": str(raw_email_id),
                    }.items()
                    if f":{key}" in statement
                },
            )


def test_the_database_accepts_the_new_values():
    """'received' on an e-mail, every body state and label, and a label run."""
    with _TestIntakeTenant("Acme Test Distributor") as tenant, platform_session() as session:
        raw_email_id = _insert_email(
            session,
            tenant.tenant_id,
            body_storage_path="tenants/seed/body.txt",
            body_kind="html",
            body_sha256="0" * 64,
            body_bytes=0,
        )
        session.execute(
            text("UPDATE raw_emails SET outcome = 'received' WHERE id = :e"), {"e": str(raw_email_id)}
        )
        for state in ("pending", "labelled", "held", "released", "none"):
            session.execute(
                text("UPDATE raw_emails SET body_state = :s WHERE id = :e"),
                {"s": state, "e": str(raw_email_id)},
            )
        for label in ("order", "change", "cancellation", "quote_request", "status_question", "not_an_order"):
            session.execute(
                text(
                    "UPDATE raw_emails SET body_label = :l, body_label_confidence = 1, "
                    "body_order_lines_confidence = 0 WHERE id = :e"
                ),
                {"l": label, "e": str(raw_email_id)},
            )
            session.execute(
                text(
                    "INSERT INTO intake_rejections "
                    "(id, tenant_id, source, error_code, raw_email_id, mail_label) "
                    "VALUES (:id, :t, 'email', 'INT-001', :e, :l)"
                ),
                {"id": str(uuid4()), "t": str(tenant.tenant_id), "e": str(raw_email_id), "l": label},
            )
        _insert_run(session, tenant.tenant_id, raw_email_id=raw_email_id)
