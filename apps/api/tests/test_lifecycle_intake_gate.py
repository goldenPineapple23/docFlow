# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
A suspended tenant cannot upload, and can still read and export
(CLAUDE.md Section 7.14; review finding H10). Phase 5.5 Stage 2b.

**What was wrong.** 7.14 says that on entering `suspended`, "the upload endpoint
and API return a clear error, not a 404 or a silent failure". Only email intake
refused -- and it refused for a different reason, reading
`intake_address_active`, which the suspend transition clears. So a cancelled
customer could keep uploading through the app, and every one of those documents
was extracted at DocFlow's cost. No test covered it.

**The half that is just as important.** 7.14 is equally explicit that a
suspended tenant keeps full read and export access through the whole
pending-deletion window, because making data hard to leave with is the wrong
incentive to build into a company whose pitch is trust. A gate that blocked
reading would be a worse defect than the one being fixed, so it is tested here
next to the refusal rather than somewhere else.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from docflow_core import intake_gate
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import (
    intake_webhook_headers,
    requires_documents_schema,
    requires_email_intake_schema,
)
from tests.test_documents_upload import JWT_SECRET, _FakeCeleryClient, _TestTenant
from tests.test_email_intake import _pm_attachment, _pm_payload

PO = b"PO Number: TEST-LIFECYCLE-1\nBuyer: Acme Test Distributor\n"


def _set_status(tenant_id, status: str) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET status = :s, status_changed_at = now() WHERE id = :id"),
            {"s": status, "id": str(tenant_id)},
        )


def _upload(client, tenant):
    return client.post(
        "/documents/upload",
        headers={"Authorization": f"Bearer {tenant.token()}"},
        files={"file": ("po.txt", PO, "text/plain")},
    )


def _rows(tenant_id, table: str) -> int:
    with platform_session() as session:
        return session.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": str(tenant_id)}
        ).scalar_one()


@pytest.fixture()
def _upload_environment(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("app.routers.documents.celery_client", fake_celery)
    yield fake_celery
    get_settings.cache_clear()


# -- Refused where 7.14 says refused ----------------------------------------


@requires_documents_schema
@pytest.mark.parametrize("status", ["suspended", "pending_deletion"])
def test_a_blocked_tenant_cannot_upload(client, _upload_environment, status):
    with _TestTenant("Acme Test Distributor", f"owner-{status}@example.test") as tenant:
        _set_status(tenant.tenant_id, status)
        response = _upload(client, tenant)

        assert response.status_code == 403, response.text
        detail = response.json()["detail"]
        # A catalog entry, with all three of what / why / what-next (7.16.5).
        assert detail["code"] == "INT-010"
        assert detail["title"] and detail["message"] and detail["action"]
        # It speaks to the tenant's own user, not to a buyer whose mail bounced.
        assert "this email was logged" not in detail["message"].lower()
        assert "contact this company" not in detail["action"].lower()
        # And it says the data is still there, which is the whole point of 7.14.
        assert "export" in detail["message"].lower()

        # The file is not stored and nothing is enqueued -- refused before the
        # bytes were looked at, so a cancelled account's upload costs no model
        # call and no storage.
        assert _rows(tenant.tenant_id, "documents") == 0
        assert _upload_environment.sent == []

        # But the *attempt* is recorded (founder, 2026-09-27). Without this the
        # refusal left no trace anywhere, so nobody could tell a customer who
        # tried once from one who tried forty times -- which is a retention
        # signal, since someone still trying to upload wants their account back.
        with platform_session() as session:
            recorded = (
                session.execute(
                    text(
                        "SELECT source, error_code, original_filename, detected_type "
                        "FROM intake_rejections WHERE tenant_id = :t"
                    ),
                    {"t": str(tenant.tenant_id)},
                )
                .mappings()
                .all()
            )
        assert len(recorded) == 1, recorded
        assert recorded[0]["source"] == "upload"
        assert recorded[0]["error_code"] == "INT-010"
        # The name is kept as metadata; the file was never inspected, so there is
        # no detected type to record (7.11: a filename is untrusted metadata only).
        assert recorded[0]["original_filename"] == "po.txt"
        assert recorded[0]["detected_type"] is None


@requires_documents_schema
def test_the_refusal_is_not_a_404(client, _upload_environment):
    """
    7.14 names this specifically: "not a 404 or a silent failure". A 404 would
    tell a paying customer whose account lapsed that their account never existed.
    """
    with _TestTenant("Acme Test Distributor", "owner-not404@example.test") as tenant:
        _set_status(tenant.tenant_id, "suspended")
        assert _upload(client, tenant).status_code == 403


# -- Allowed where 7.14 says allowed ----------------------------------------


@requires_documents_schema
@pytest.mark.parametrize("status", ["active", "cancelling"])
def test_an_active_or_cancelling_tenant_uploads_normally(client, _upload_environment, status):
    """
    `cancelling` matters as much as `active`: 7.14 says everything keeps working
    until the effective date, so a customer who has given notice keeps processing
    orders to the last day. Blocking them early would be the mirror-image defect.
    """
    with _TestTenant("Acme Test Distributor", f"owner-{status}@example.test") as tenant:
        _set_status(tenant.tenant_id, status)
        response = _upload(client, tenant)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "pending"
        assert len(_upload_environment.sent) == 1


# -- The half that would be worse to get wrong ------------------------------


@requires_documents_schema
@pytest.mark.parametrize("status", ["suspended", "pending_deletion"])
def test_a_blocked_tenant_can_still_read_and_export(client, _upload_environment, status):
    """
    7.14: read and export access is NOT revoked when intake is blocked, for the
    whole pending-deletion window. Tested against the tenant's real surfaces
    rather than asserted in a comment.
    """
    with _TestTenant("Acme Test Distributor", f"owner-read-{status}@example.test") as tenant:
        # Upload while still active, so there is something to read afterwards.
        uploaded = _upload(client, tenant)
        assert uploaded.status_code == 200
        document_id = uploaded.json()["document_id"]
        _set_status(tenant.tenant_id, status)

        headers = {"Authorization": f"Bearer {tenant.token()}"}
        for path in (
            "/home",                                    # the tenant dashboard
            "/review/documents",                        # their order history
            f"/review/documents/{document_id}",          # one order, in full
            f"/review/documents/{document_id}/exports",  # and its export history
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, (path, response.status_code, response.text[:200])

        # The order they uploaded is still listed, not hidden along with intake.
        listed = client.get("/review/documents", headers=headers).json()
        assert any(str(d.get("id")) == document_id for d in listed.get("documents", [])), listed


# -- One answer to the lifecycle question, not one per channel --------------


def test_the_two_intake_paths_cannot_disagree_about_lifecycle():
    """
    Email intake and the upload endpoint both ask `blocks_new_intake`, so the set
    of blocking states cannot drift between them -- which is how this defect
    existed in the first place (one channel checked, the other didn't).

    `cancelling` being absent here is the requirement, not an omission.
    """
    assert intake_gate.blocks_new_intake("suspended") is True
    assert intake_gate.blocks_new_intake("pending_deletion") is True
    assert intake_gate.blocks_new_intake("active") is False
    assert intake_gate.blocks_new_intake("cancelling") is False
    assert intake_gate.blocks_new_intake(None) is False
    assert set(intake_gate.LIFECYCLE_BLOCKED_STATUSES) == {"suspended", "pending_deletion"}


# -- Drift between the channels, which is how this defect existed -----------


def _give_intake_address(tenant_id) -> str:
    """An active intake address for this tenant, so the email channel is usable."""
    token = uuid4().hex
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO intake_addresses (id, tenant_id, token, address, status, created_at) "
                "VALUES (:id, :tenant_id, :token, :address, 'active', now())"
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "token": token,
                "address": f"{token}@mail.docflow.test",
            },
        )
    return token


def _set_lifecycle(tenant_id, status: str) -> None:
    """
    Set the lifecycle state the way the real transition does.

    `claim_for_suspend` (`lifecycle.py:305`) sets `status` **and** clears
    `intake_address_active` in one statement, and reactivation sets both back.
    Pairing them here is fidelity, not convenience -- and
    `test_the_blocked_status_and_the_intake_flag_cannot_come_apart` is what holds
    the real transition to it.
    """
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE tenants SET status = :s, status_changed_at = now(), "
                "intake_address_active = :live WHERE id = :id"
            ),
            {"s": status, "live": not intake_gate.blocks_new_intake(status), "id": str(tenant_id)},
        )


@requires_documents_schema
@requires_email_intake_schema
@pytest.mark.parametrize("status", ["active", "cancelling", "suspended", "pending_deletion"])
def test_both_intake_channels_reach_the_same_verdict(client, _upload_environment, status):
    """
    The test the shared predicate alone does not give us (founder, 2026-09-27).

    `test_the_two_intake_paths_cannot_disagree_about_lifecycle` only exercises
    `blocks_new_intake` itself: it would still pass if one channel's call site
    stopped calling it and inlined its own tuple. This one drives **both real
    endpoints** for the same tenant in the same state and asserts they agree, so
    a drifting call site fails it.

    That drift is not hypothetical -- it is exactly how H10 existed. One channel
    checked the lifecycle and the other did not, and every suite was green.
    """
    with _TestTenant("Acme Test Distributor", f"owner-both-{status}@example.test") as tenant:
        token = _give_intake_address(tenant.tenant_id)
        _set_lifecycle(tenant.tenant_id, status)

        upload = _upload(client, tenant)
        email = client.post(
            f"/intake/email/{token}",
            json=_pm_payload(
                "buyer-both@example.test",
                attachments=[_pm_attachment("po.txt", PO)],
            ),
            headers=intake_webhook_headers(),
        )

        # The email webhook always answers 200 -- a provider must never see this
        # pipeline's own decisions as delivery failures -- so its verdict is the
        # outcome, not the status code.
        upload_blocked = upload.status_code == 403
        email_blocked = email.status_code == 404 or (
            email.status_code == 200 and email.json()["outcome"] == "rejected"
        )

        expected = intake_gate.blocks_new_intake(status)
        assert upload_blocked is expected, (status, upload.status_code, upload.text[:200])
        assert email_blocked is expected, (status, email.status_code, email.text[:200])


def test_no_call_site_inlines_the_blocked_status_list():
    """
    The structural half: only `intake_gate` may name the blocking states.

    A call site that wrote the pair of status strings inline would pass every
    behavioural test on the day it was written and drift the moment the set
    changed. Same approach as `packages/core/tests/test_audit_clock.py`.

    **The rule is "name the set, do not inline it" -- not "mention it only
    once".** `lifecycle.REACTIVATABLE` holds the same two states and answers a
    different question: which states a tenant may be *reactivated* from. They
    coincide today and need not tomorrow, so each is named where it is meant, and
    neither should be collapsed into the other. What this test forbids is the pair
    appearing inside a condition, where it has no name and no reason.
    """
    import pathlib
    import re

    repo = pathlib.Path(__file__).resolve().parents[3]
    pair = r"""['"]suspended['"]\s*,\s*['"]pending_deletion['"]"""
    inline = re.compile(rf"(?<!= )\(?\s*{pair}")
    named = re.compile(rf"^[A-Z_]+\s*(:[^=]+)?=\s*\(?\s*{pair}", re.MULTILINE)
    offenders = []
    for root in ("packages/core/docflow_core", "apps/api/app", "apps/worker/app"):
        for path in (repo / root).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            # Drop every constant definition, then look for what is left.
            remaining = named.sub("", source)
            if inline.search(remaining):
                offenders.append(path.relative_to(repo).as_posix())
    assert offenders == [], (
        "these name the blocking states inside a condition instead of asking "
        f"intake_gate.blocks_new_intake() or using a named constant: {offenders}"
    )


@requires_documents_schema
def test_the_blocked_status_and_the_intake_flag_cannot_come_apart(client):
    """
    Upload gates on `tenants.status`; email gates on `intake_address_active`. Two
    different columns, and they agree only because the real transition sets both
    in one statement (`lifecycle.py:305`). This pins that: run the actual
    transition and assert the pair.

    Without it, a future transition that set the status and forgot the flag would
    leave email accepting mail for an account that cannot upload -- and no other
    test would notice.
    """
    from docflow_core import lifecycle

    with _TestTenant("Acme Test Distributor", "owner-invariant@example.test") as tenant:
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE tenants SET status = 'cancelling', intake_address_active = true, "
                    "cancellation_effective_at = now() - interval '1 day', "
                    "cancellation_reason = 'customer_requested' WHERE id = :id"
                ),
                {"id": str(tenant.tenant_id)},
            )
            claim = lifecycle.claim_for_suspend(session, tenant.tenant_id)
        assert claim is not None, "the sweep should have claimed a tenant past its effective date"

        with platform_session() as session:
            row = (
                session.execute(
                    text("SELECT status, intake_address_active FROM tenants WHERE id = :id"),
                    {"id": str(tenant.tenant_id)},
                )
                .mappings()
                .one()
            )
        try:
            assert intake_gate.blocks_new_intake(row["status"]), row["status"]
            assert row["intake_address_active"] is False, (
                "the transition blocked uploads but left the intake address live, so email "
                "intake would still accept mail this account cannot process"
            )
        finally:
            # The real transition writes a lifecycle event, an outbox mail and
            # reminder jobs, all of which reference this tenant -- so
            # `_TestTenant.__exit__` cannot delete it until they are gone, and a
            # failing assertion would otherwise strand the tenant on staging.
            # In a `finally` on purpose (the Stage 5 "robust test cleanup" rule).
            with platform_session() as session:
                # Order matters: founder_alerts references email_outbox.
                for table in (
                    "founder_alerts",
                    "email_outbox",
                    "tenant_lifecycle_events",
                    "scheduled_jobs",
                ):
                    session.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :t"),
                        {"t": str(tenant.tenant_id)},
                    )
