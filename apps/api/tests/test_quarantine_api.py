# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Allowances and quarantine (CLAUDE.md Section 7.16; D-126), against the real
database and RLS.

What this proves, in the spec's own terms:
  * Metering (7.16.1): a document counts once per month unless it is a test-batch
    document, failed, quarantined, a linked duplicate or deleted -- and the
    Console's tenant list and the tenant's own number agree.
  * 80% and 100% notices: one email per threshold per month, the founder alert
    at 100%, and documents keep processing past 100%.
  * The abuse ceilings (7.16.2): the monthly ceiling and the daily AI-cost
    ceiling quarantine new documents on EVERY channel (email and web upload),
    auto-reply, alert once, leave in-flight work alone, and never touch the
    setup test batch. Nothing is discarded and nothing reaches the model.
  * Release and clear (7.16.4): who may release what (enforced at the API),
    received order, counting only after release, isolation between tenants.
  * Email intake layers (7.16.3): address rotation with a grace period, the
    opt-in strict allowlist, senders known through a buyer.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from docflow_core import intake_admin, usage
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_database
from tests.test_console_api import JWT_SECRET, _Console, _environment, stripe  # noqa: F401
from tests.test_email_intake import _pm_attachment, _pm_payload


def _schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT released_at FROM documents LIMIT 0"))
            session.execute(text("SELECT id FROM allowance_notices LIMIT 0"))
            session.execute(text("SELECT grace_ends_at FROM intake_addresses LIMIT 0"))
            session.execute(text("SELECT strict_sender_mode FROM tenants LIMIT 0"))
        return True
    except Exception:
        return False


requires_quarantine_schema = pytest.mark.skipif(
    not _schema_available(),
    reason="supabase/migrations/0021_allowance_quarantine.sql has not been applied yet -- see D-126.",
)


class _Celery:
    def __init__(self):
        self.sent: list[dict] = []

    def send_task(self, name, args=None, queue=None):
        self.sent.append({"name": name, "args": args, "queue": queue})

    def document_ids(self) -> list[str]:
        return [s["args"][1] for s in self.sent]


@pytest.fixture
def celery(monkeypatch):
    fake = _Celery()
    for target in (
        "docflow_core.email_intake.celery_client",
        "app.routers.documents.celery_client",
        "app.routers.held.celery_client",
        "app.routers.admin.celery_client",
    ):
        monkeypatch.setattr(target, fake)
    return fake


_CLEAN_TABLES = (
    "extraction_runs",
    "document_lines",
    "document_headers",
    "documents",
    "buyers",
    "intake_rejections",
    "raw_emails",
    "allowance_notices",
    "founder_alerts",
    "email_outbox",
    "tenant_lifecycle_events",
    "intake_addresses",
)


class _Tenant:
    """A throwaway live tenant on the cheapest current tier, with an owner who
    can sign in, and cleanup of everything it creates."""

    def __init__(self, name: str = "Acme Test Quarantine", *, tier: bool = True):
        self.name = name
        self.tenant_id = uuid4()
        self.token = uuid4().hex
        self.with_tier = tier
        self.allowance = 0
        self.users: dict[str, tuple[UUID, str]] = {}  # role -> (user_id, auth_user_id)

    def __enter__(self):
        with platform_session() as session:
            tier = (
                session.execute(
                    text(
                        "SELECT id, document_allowance FROM tiers WHERE is_current "
                        "ORDER BY monthly_price LIMIT 1"
                    )
                )
                .mappings()
                .first()
            )
            self.allowance = int(tier["document_allowance"])
            session.execute(
                text(
                    "INSERT INTO tenants (id, name, status, onboarding_status, intake_address_active, "
                    "tier_id, created_at, updated_at, status_changed_at) "
                    "VALUES (:id, :name, 'active', 'tenant_created', true, :tier, now(), now(), now())"
                ),
                {
                    "id": str(self.tenant_id),
                    "name": self.name,
                    "tier": str(tier["id"]) if self.with_tier else None,
                },
            )
            session.execute(
                text(
                    "INSERT INTO intake_addresses (id, tenant_id, token, address, status, created_at) "
                    "VALUES (:id, :t, :token, :address, 'active', now())"
                ),
                {
                    "id": str(uuid4()),
                    "t": str(self.tenant_id),
                    "token": self.token,
                    "address": f"orders+{self.token}@intake.example.test",
                },
            )
        self.add_user("owner")
        return self

    def add_user(self, role: str) -> None:
        user_id, auth_id = uuid4(), str(uuid4())
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
                    "VALUES (:id, :t, :auth, :email, :role, true)"
                ),
                {
                    "id": str(user_id),
                    "t": str(self.tenant_id),
                    "auth": auth_id,
                    "email": f"{role}-{user_id.hex[:8]}@example.test",
                    "role": role,
                },
            )
        self.users[role] = (user_id, auth_id)

    def headers(self, role: str = "owner") -> dict:
        _, auth_id = self.users[role]
        token = jwt.encode(
            {"sub": auth_id, "email": "x@example.test", "aud": "authenticated"},
            JWT_SECRET,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def owner_email(self) -> str:
        with platform_session() as session:
            return session.execute(
                text("SELECT email FROM users WHERE id = :u"), {"u": str(self.users["owner"][0])}
            ).scalar_one()

    # -- data -----------------------------------------------------------------

    def seed(
        self,
        n: int,
        *,
        status: str = "pending",
        source: str = "upload",
        sender: str | None = None,
        test_batch: bool = False,
        created_at: str = "now()",
        reason: str | None = None,
        duplicate_of: UUID | None = None,
        deleted: bool = False,
    ) -> list[UUID]:
        """Insert `n` documents directly. Uploads with no sender by default, so
        they never look like unknown-sender email traffic."""
        with platform_session() as session:
            rows = (
                session.execute(
                    text(
                        f"""
                    INSERT INTO documents
                        (id, tenant_id, original_filename, storage_path, source, status,
                         content_sha256, sender_email, is_test_batch, quarantine_reason,
                         quarantined_at, duplicate_of_document_id, deleted_at, created_at)
                    SELECT gen_random_uuid(), :t, 'seed-' || g || '.txt', 'tenants/seed/seed.txt',
                           :source, :status, md5(random()::text || g::text), :sender, :test,
                           CAST(:reason AS text),
                           CASE WHEN :status = 'quarantined' THEN {created_at} END,
                           CAST(:dup AS uuid),
                           CASE WHEN :deleted THEN now() END,
                           {created_at} + (g || ' milliseconds')::interval
                    FROM generate_series(1, :n) g
                    RETURNING id
                    """
                    ),
                    {
                        "t": str(self.tenant_id),
                        "n": n,
                        "status": status,
                        "source": source,
                        "sender": sender,
                        "test": test_batch,
                        "reason": reason,
                        "dup": str(duplicate_of) if duplicate_of else None,
                        "deleted": deleted,
                    },
                )
                .scalars()
                .all()
            )
        return [UUID(str(r)) for r in rows]

    def docs(self, *, status: str | None = None) -> list[dict]:
        sql = (
            "SELECT id, status, quarantine_reason, released_at, released_by_user_id, "
            "released_acting_as_tenant_id, deleted_at, sender_email FROM documents WHERE tenant_id = :t"
        )
        if status:
            sql += " AND status = :s"
        sql += " ORDER BY created_at, id"
        with platform_session() as session:
            return [
                dict(r)
                for r in session.execute(text(sql), {"t": str(self.tenant_id), "s": status}).mappings().all()
            ]

    def rows(self, sql: str, **params) -> list[dict]:
        with platform_session() as session:
            return [
                dict(r)
                for r in session.execute(text(sql), {"t": str(self.tenant_id), **params}).mappings().all()
            ]

    def outbox(self, template: str | None = None) -> list[dict]:
        sql = "SELECT to_address, template, body_text FROM email_outbox WHERE tenant_id = :t"
        if template:
            sql += " AND template = :tpl"
        return self.rows(sql, tpl=template)

    def alerts(self, alert_type: str) -> list[dict]:
        return self.rows(
            "SELECT severity, payload FROM founder_alerts WHERE tenant_id = :t AND type = :ty", ty=alert_type
        )

    def used(self) -> int:
        with tenant_session(self.tenant_id) as session:
            return usage.month_used(session, self.tenant_id)

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            session.execute(text("UPDATE tenants SET status = 'active' WHERE id = :t"), {"t": tid})
            # A document points at its current run (D-142); runs go first below.
            session.execute(
                text("UPDATE documents SET current_extraction_run_id = NULL WHERE tenant_id = :t"), {"t": tid}
            )
            for table in _CLEAN_TABLES:
                session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid})
            session.execute(text("DELETE FROM admin_actions WHERE target_tenant_id = :t"), {"t": tid})
            session.execute(
                text("UPDATE documents SET released_by_user_id = NULL WHERE tenant_id = :t"), {"t": tid}
            )
            session.execute(text("DELETE FROM users WHERE tenant_id = :t"), {"t": tid})
            session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})


def _email(client, tenant: _Tenant, sender: str, *, n: int = 1, token: str | None = None):
    attachments = [_pm_attachment(f"po-{uuid4().hex[:6]}.txt", f"PO {uuid4()}".encode()) for _ in range(n)]
    return client.post(
        f"/intake/email/{token or tenant.token}", json=_pm_payload(sender, attachments=attachments)
    )


def _upload(client, tenant: _Tenant):
    return client.post(
        "/documents/upload",
        headers=tenant.headers(),
        files={"file": ("po.txt", f"PO {uuid4()}".encode(), "text/plain")},
    )


def _trip_month_ceiling(tenant: _Tenant) -> None:
    tenant.seed(tenant.allowance * 3)


# ── Metering ────────────────────────────────────────────────────────────────


@requires_database
@requires_quarantine_schema
def test_only_documents_that_should_count_are_counted_and_the_console_agrees(client):
    with _Console() as console, _Tenant() as t:
        counted = t.seed(5)
        t.seed(2, test_batch=True)
        t.seed(1, status="failed")
        t.seed(1, status="quarantined", reason="attachment_cap")
        t.seed(2, duplicate_of=counted[0])
        t.seed(1, deleted=True)
        t.seed(3, created_at="now() - interval '40 days'")
        assert t.used() == 5

        listed = client.get("/admin/tenants", headers=console.headers()).json()
        mine = next(row for row in listed if row["id"] == str(t.tenant_id))
        assert mine["documents_this_month"] == 5  # the Console and the tenant cannot drift


# ── 80% / 100% notices ──────────────────────────────────────────────────────


@requires_database
@requires_quarantine_schema
def test_notices_fire_once_each_and_documents_keep_processing_past_the_allowance(client, celery):
    with _Tenant() as t:
        t.seed(int(t.allowance * 0.8) - 1)
        assert client.get("/allowance", headers=t.headers()).json()["banner"] is None

        assert _email(client, t, "buyer-a@example.test").json()["outcome"] == "processed"
        eighty = t.outbox("allowance_notice")
        assert [m["to_address"] for m in eighty] == [t.owner_email()]
        assert "Documents continue to process normally" in eighty[0]["body_text"]
        assert t.alerts("allowance_reached") == []

        _email(client, t, "buyer-a@example.test")  # crossing again is not a second email
        assert len(t.outbox("allowance_notice")) == 1

        # The admin has been emailed at 80%, but nobody's screen is interrupted
        # yet (D-129): the banner waits until the plan is nearly spent, and the
        # month's running numbers live on the admin's dashboard instead.
        assert client.get("/allowance", headers=t.headers()).json()["banner"] is None
        t.seed(int(t.allowance * 0.9) - t.used())
        near_the_limit = client.get("/allowance", headers=t.headers()).json()["banner"]
        assert near_the_limit["code"] in ("LIM-001", "LIM-003")
        assert len(t.outbox("allowance_notice")) == 1  # the banner sends no mail of its own

        t.seed(t.allowance - t.used() - 1)
        assert _email(client, t, "buyer-a@example.test").json()["outcome"] == "processed"
        assert len(t.outbox("allowance_notice")) == 2
        alerts = t.alerts("allowance_reached")
        assert len(alerts) == 1 and alerts[0]["severity"] == "info"  # good news, not a fault

        # Past 100%: still accepted, still enqueued, never held.
        before = len(celery.sent)
        assert _email(client, t, "buyer-a@example.test").json()["outcome"] == "processed"
        assert len(celery.sent) == before + 1
        assert not t.docs(status="quarantined")

        banner = client.get("/allowance", headers=t.headers()).json()["banner"]
        assert banner["code"] in ("LIM-002", "LIM-004") and banner["threshold_pct"] == 100
        assert f"of {t.allowance:,}" in banner["message"]
        assert len(t.rows("SELECT 1 FROM allowance_notices WHERE tenant_id = :t")) == 2


# ── Abuse ceilings ──────────────────────────────────────────────────────────


@requires_database
@requires_quarantine_schema
def test_the_monthly_ceiling_holds_new_email_replies_alerts_once_and_spares_in_flight_work(client, celery):
    with _Tenant() as t:
        in_flight = t.seed(1, status="processing")[0]
        _trip_month_ceiling(t)

        response = _email(client, t, "new-buyer@example.test", n=2)
        assert response.json()["outcome"] == "quarantined"
        held = t.docs(status="quarantined")
        assert len(held) == 2 and all(d["quarantine_reason"] == "abuse_ceiling" for d in held)
        assert celery.sent == []  # never sent to the model

        replies = t.outbox("intake_held")
        assert [r["to_address"] for r in replies] == ["new-buyer@example.test"]
        assert "unusual volume" in replies[0]["body_text"]

        _email(client, t, "new-buyer@example.test")  # a second email: held again, no second reply
        assert len(t.outbox("intake_held")) == 1
        alerts = t.alerts("abuse_ceiling_tripped")
        assert len(alerts) == 1 and alerts[0]["severity"] == "high"

        assert next(d for d in t.docs() if d["id"] == in_flight)["status"] == "processing"


@requires_database
@requires_quarantine_schema
def test_the_daily_ai_cost_ceiling_holds_at_fifty_dollars_but_not_at_a_cent_under(client, celery):
    def spend(t, amount, *, test_batch=False):
        doc = t.seed(1, test_batch=test_batch)[0]
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO extraction_runs "
                    "(id, tenant_id, document_id, est_cost_usd, succeeded, created_at) "
                    "VALUES (:id, :t, :d, :c, true, now())"
                ),
                {"id": str(uuid4()), "t": str(t.tenant_id), "d": str(doc), "c": amount},
            )

    with _Tenant() as t:
        spend(t, "49.99")
        assert _email(client, t, "buyer-c@example.test").json()["outcome"] == "processed"
        spend(t, "0.01")
        assert _email(client, t, "buyer-d@example.test").json()["outcome"] == "quarantined"
        assert t.docs(status="quarantined")[0]["quarantine_reason"] == "cost_breaker"
        assert len(t.alerts("cost_breaker_tripped")) == 1

    with _Tenant() as t:
        spend(t, "500.00", test_batch=True)  # the setup batch never trips a customer ceiling
        assert _email(client, t, "buyer-e@example.test").json()["outcome"] == "processed"


@requires_database
@requires_quarantine_schema
def test_web_uploads_meet_the_same_ceiling_and_the_test_batch_is_exempt(client, celery):
    from app.routers.documents import ingest_upload

    with _Tenant() as t:
        _trip_month_ceiling(t)
        response = _upload(client, t)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "quarantined" and body["held"]["code"] == "INT-007"
        assert celery.sent == []
        assert t.docs(status="quarantined")[0]["quarantine_reason"] == "abuse_ceiling"

        staged = ingest_upload(t.tenant_id, "batch.txt", f"PO {uuid4()}".encode(), is_test_batch=True)
        assert staged["status"] == "staged" and "held" not in staged


@requires_database
@requires_quarantine_schema
def test_a_tenant_with_no_tier_is_never_ceilinged(client, celery):
    with _Tenant(tier=False) as t:
        t.seed(2000)
        assert _email(client, t, "buyer-f@example.test").json()["outcome"] == "processed"


# ── Release, order, clear ───────────────────────────────────────────────────


@requires_database
@requires_quarantine_schema
def test_release_goes_in_received_order_and_only_then_counts(client, celery):
    with _Tenant() as t:
        oldest, middle, newest = (
            t.seed(
                1, status="quarantined", reason="attachment_cap", created_at=f"now() - interval '{m} minutes'"
            )[0]
            for m in (30, 20, 10)
        )
        before = t.used()
        response = client.post(
            "/held/release",
            headers=t.headers(),
            json={"document_ids": [str(newest), str(oldest), str(middle)]},  # deliberately scrambled
        )
        assert response.status_code == 200, response.text
        assert celery.document_ids() == [str(oldest), str(middle), str(newest)]
        assert t.used() == before + 3
        released = {d["id"]: d for d in t.docs()}
        assert all(
            released[i]["status"] == "pending" and released[i]["released_at"]
            for i in (oldest, middle, newest)
        )
        assert released[oldest]["released_by_user_id"] == t.users["owner"][0]
        assert released[oldest]["released_acting_as_tenant_id"] is None


@requires_database
@requires_quarantine_schema
def test_who_may_release_what_is_enforced_by_the_api(client, celery):
    with _Tenant() as t:
        t.add_user("reviewer")
        t.add_user("viewer")
        own_call = t.seed(1, status="quarantined", reason="unknown_sender_velocity")[0]
        founders = t.seed(1, status="quarantined", reason="abuse_ceiling")[0]

        for role in ("reviewer", "viewer"):
            r = client.post("/held/release", headers=t.headers(role), json={"document_ids": [str(own_call)]})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "AUTH-002"

        # A founder-only hold: refused, with the explanation, and NOTHING moves --
        # not even the one the owner could have released.
        r = client.post(
            "/held/release", headers=t.headers(), json={"document_ids": [str(own_call), str(founders)]}
        )
        assert r.status_code == 403 and r.json()["detail"]["code"] == "QUA-001"
        assert {d["status"] for d in t.docs()} == {"quarantined"}

        ok = client.post("/held/release", headers=t.headers(), json={"document_ids": [str(own_call)]})
        assert ok.status_code == 200
        assert len(celery.sent) == 1

        held = client.get("/held", headers=t.headers()).json()
        assert held["total"] == 1
        assert held["groups"][0]["reason"] == "abuse_ceiling" and held["groups"][0]["can_release"] is False
        assert "unusual volume" in held["groups"][0]["message"]


@requires_database
@requires_quarantine_schema
def test_the_founder_can_release_a_hold_even_while_the_ceiling_is_still_tripped(client, celery):
    with _Console() as console, _Tenant() as t:
        _trip_month_ceiling(t)
        doc = t.seed(1, status="quarantined", reason="cost_breaker")[0]

        r = client.post(
            f"/admin/tenants/{t.tenant_id}/quarantine/release",
            headers=console.headers(),
            json={"document_ids": [str(doc)]},
        )
        assert r.status_code == 200, r.text
        released = next(d for d in t.docs() if d["id"] == doc)
        assert released["status"] == "pending"
        assert released["released_by_user_id"] == console.user_id  # the founder, as themselves
        assert released["released_acting_as_tenant_id"] == t.tenant_id
        actions = t.rows("SELECT action FROM admin_actions WHERE target_tenant_id = :t")
        assert "quarantine_release" in {a["action"] for a in actions}


@requires_database
@requires_quarantine_schema
def test_one_tenant_cannot_release_another_tenants_documents(client, celery):
    with _Tenant("Acme Test Quarantine A") as a, _Tenant("Beacon Test Quarantine B") as b:
        theirs = b.seed(1, status="quarantined", reason="attachment_cap")[0]
        r = client.post("/held/release", headers=a.headers(), json={"document_ids": [str(theirs)]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "QUA-002"
        assert b.docs()[0]["status"] == "quarantined"
        assert client.get("/held", headers=a.headers()).json()["total"] == 0


@requires_database
@requires_quarantine_schema
def test_clear_needs_the_exact_name_and_is_a_soft_delete(client):
    with _Console() as console, _Tenant("Acme Test Clear") as t:
        doc = t.seed(1, status="quarantined", reason="auth_fail")[0]
        url = f"/admin/tenants/{t.tenant_id}/quarantine/clear"
        wrong = client.post(
            url,
            headers=console.headers(),
            json={"document_ids": [str(doc)], "confirm_name": "acme test clear"},
        )
        assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "QUA-003"
        assert t.docs()[0]["deleted_at"] is None

        right = client.post(
            url,
            headers=console.headers(),
            json={"document_ids": [str(doc)], "confirm_name": "Acme Test Clear"},
        )
        assert right.status_code == 200
        row = t.docs()[0]
        assert row["deleted_at"] is not None and row["status"] == "quarantined"  # the row is kept
        assert client.get("/held", headers=t.headers()).json()["total"] == 0


@requires_database
@requires_quarantine_schema
def test_the_console_quarantine_screen_is_invisible_to_tenants_and_strangers(client):
    with _Tenant() as t:
        url = f"/admin/tenants/{t.tenant_id}/quarantine"
        assert client.get(url, headers=t.headers()).status_code == 404  # 404, never 403
        assert client.get(url).status_code == 404
        assert client.post(url + "/release", json={"document_ids": [str(uuid4())]}).status_code == 404


# ── Email layers ────────────────────────────────────────────────────────────


@requires_database
@requires_quarantine_schema
def test_rotating_the_address_keeps_a_grace_reply_then_retires_it(client, celery):
    with _Console() as console, _Tenant("Acme Test Rotation") as t:
        old_token = t.token
        r = client.post(f"/admin/tenants/{t.tenant_id}/intake-address/rotate", headers=console.headers())
        assert r.status_code == 200, r.text
        new_address = r.json()["address"]
        grace_ends = datetime.fromisoformat(r.json()["grace_ends_at"])
        assert timedelta(days=29) < grace_ends - datetime.now(UTC) <= timedelta(days=30, minutes=1)

        addresses = {
            a["status"]: a
            for a in t.rows("SELECT status, token, grace_ends_at FROM intake_addresses WHERE tenant_id = :t")
        }
        assert set(addresses) == {"active", "grace"} and addresses["grace"]["token"] == old_token
        assert new_address.split("+")[1].split("@")[0] == addresses["active"]["token"]
        owner_mail = t.outbox("intake_address_rotated")
        assert owner_mail and new_address in owner_mail[0]["body_text"]
        assert t.rows(
            "SELECT 1 FROM tenant_lifecycle_events WHERE tenant_id = :t "
            "AND event_type = 'intake_address_rotated'"
        )

        # The old address: nothing is processed, the sender is told, once.
        first = _email(client, t, "buyer-g@example.test", token=old_token)
        assert first.status_code == 200 and first.json()["outcome"] == "rejected"
        _email(client, t, "buyer-g@example.test", token=old_token)
        replies = t.outbox("intake_address_changed")
        assert len(replies) == 1 and "Acme Test Rotation" in replies[0]["body_text"]
        assert t.docs() == [] and celery.sent == []

        # The new address works.
        new_token = addresses["active"]["token"]
        assert _email(client, t, "buyer-g@example.test", token=new_token).json()["outcome"] == "processed"

        # Past the grace period the old address is a plain 404.
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE intake_addresses SET grace_ends_at = now() - interval '1 minute' "
                    "WHERE tenant_id = :t AND status = 'grace'"
                ),
                {"t": str(t.tenant_id)},
            )
        assert _email(client, t, "buyer-g@example.test", token=old_token).status_code == 404
        # The running worker's own sweep retires expired grace addresses for every
        # tenant every few minutes, so it may get here first: either this call
        # retires it (1) or the sweep already did (0). The END STATE is the point.
        with tenant_session(t.tenant_id) as session:
            assert intake_admin.housekeeping(session, t.tenant_id)["retired_addresses"] in (0, 1)
        assert {a["status"] for a in t.rows("SELECT status FROM intake_addresses WHERE tenant_id = :t")} == {
            "active",
            "retired",
        }


@requires_database
@requires_quarantine_schema
def test_strict_allowlist_is_off_by_default_and_holds_rather_than_rejects(client, celery):
    with _Console() as console, _Tenant() as t:
        assert _email(client, t, "stranger@example.test").json()["outcome"] == "processed"  # default: open

        url = f"/admin/tenants/{t.tenant_id}/sender-settings"
        bad = client.put(
            url, headers=console.headers(), json={"strict_sender_mode": True, "sender_allowlist": []}
        )
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "QUA-005"
        junk = client.put(
            url,
            headers=console.headers(),
            json={"strict_sender_mode": True, "sender_allowlist": ["not a domain"]},
        )
        assert junk.status_code == 422

        ok = client.put(
            url,
            headers=console.headers(),
            json={
                "strict_sender_mode": True,
                "sender_allowlist": ["Allowed@Example.test", "trusted.example.test"],
            },
        )
        assert ok.status_code == 200 and ok.json()["sender_allowlist"] == [
            "allowed@example.test",
            "trusted.example.test",
        ]

        assert _email(client, t, "allowed@example.test").json()["outcome"] == "processed"
        assert _email(client, t, "ap@trusted.example.test").json()["outcome"] == "processed"
        assert _email(client, t, "stranger2@example.test").json()["outcome"] == "quarantined"
        held = t.docs(status="quarantined")
        assert [d["quarantine_reason"] for d in held] == ["sender_not_allowed"]  # held, never discarded

        # It is the tenant's own call, so the owner can release it.
        r = client.post("/held/release", headers=t.headers(), json={"document_ids": [str(held[0]["id"])]})
        assert r.status_code == 200


@requires_database
@requires_quarantine_schema
def test_a_sender_known_through_a_buyer_is_spared_the_velocity_rule_but_free_mail_is_not(client, celery):
    with _Tenant() as t:
        with platform_session() as session:
            for name, email in (
                ("Bigbuyer Test Co", "ap@bigbuyer.example.test"),
                ("Gmail Test Buyer", "someone@gmail.com"),
            ):
                session.execute(
                    text(
                        "INSERT INTO buyers (id, tenant_id, name, normalized_name, contact_email) "
                        "VALUES (:id, :t, :n, :n, :e)"
                    ),
                    {"id": str(uuid4()), "t": str(t.tenant_id), "n": name.lower(), "e": email},
                )
        with platform_session() as session:  # 20 unknown senders in the last hour
            session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, "
                    "status, content_sha256, sender_email, created_at) "
                    "SELECT gen_random_uuid(), :t, 'x.txt', 'tenants/seed/x.txt', 'email', 'pending', "
                    "md5(g::text), 'seed' || g || '@unknown.example.test', now() "
                    "FROM generate_series(1, 20) g"
                ),
                {"t": str(t.tenant_id)},
            )
        assert _email(client, t, "someone.else@gmail.com").json()["outcome"] == "quarantined"
        assert _email(client, t, "other@bigbuyer.example.test").json()["outcome"] == "processed"
        assert _email(client, t, "ap@bigbuyer.example.test").json()["outcome"] == "processed"


@requires_database
@requires_quarantine_schema
def test_held_documents_past_retention_raise_one_alert_and_are_never_deleted(client):
    with _Tenant() as t:
        t.seed(2, status="quarantined", reason="manual", created_at="now() - interval '31 days'")
        t.seed(1, status="quarantined", reason="manual")
        with tenant_session(t.tenant_id) as session:
            assert intake_admin.housekeeping(session, t.tenant_id)["expired_held"] == 2
        with tenant_session(t.tenant_id) as session:
            intake_admin.housekeeping(session, t.tenant_id)  # a second pass: same alert, not another
        assert len(t.alerts("quarantine_ttl_elapsed")) == 1
        assert len(t.docs(status="quarantined")) == 3 and all(d["deleted_at"] is None for d in t.docs())


@requires_database
@requires_quarantine_schema
def test_mail_that_made_no_document_is_listed_for_the_tenant(client):
    with _Tenant() as t:
        client.post(f"/intake/email/{t.token}", json=_pm_payload("nobody@example.test", attachments=[]))
        mail = client.get("/ignored-mail", headers=t.headers()).json()["mail"]
        assert mail and mail[0]["code"] == "INT-001" and mail[0]["sender_email"] == "nobody@example.test"
        assert mail[0]["action"]  # what to do next, from the catalog


@requires_database
@requires_quarantine_schema
def test_a_reviewer_and_a_viewer_can_see_but_not_change_what_is_held(client):
    with _Tenant() as t:
        t.add_user("viewer")
        t.seed(1, status="quarantined", reason="attachment_cap")
        held = client.get("/held", headers=t.headers("viewer")).json()
        assert held["total"] == 1 and held["groups"][0]["can_release"] is False
        assert held["documents"][0]["can_release"] is False
        assert (
            "too many attachments" in held["groups"][0]["title"].lower()
            or held["groups"][0]["code"] == "INT-002"
        )


@requires_database
@requires_quarantine_schema
def test_the_console_names_each_hold_and_shows_whether_its_ceiling_is_still_reached(client):
    """A document held for a ceiling that is no longer reached must say so, so the
    founder can see it is safe to release (a hold outlives the surge that caused it)."""
    with _Console() as console, _Tenant() as t:
        t.seed(1, status="quarantined", reason="abuse_ceiling")
        t.seed(1, status="quarantined", reason="auth_fail")
        view = client.get(f"/admin/tenants/{t.tenant_id}/quarantine", headers=console.headers()).json()

        labels = {d["quarantine_reason"]: d["reason_label"] for d in view["documents"]}
        assert labels["abuse_ceiling"] == "Monthly volume ceiling reached (3x the allowance)"
        assert labels["auth_fail"] == "Sender failed its authentication check"
        assert view["limits"]["monthly_ceiling"] == t.allowance * 3
        assert view["limits"]["monthly_ceiling_reached"] is False  # the tenant is nowhere near it
        assert view["limits"]["daily_cost_ceiling_usd"] == "50"
        assert view["limits"]["daily_cost_ceiling_reached"] is False

        t.seed(t.allowance * 3)  # now the ceiling really is reached
        again = client.get(f"/admin/tenants/{t.tenant_id}/quarantine", headers=console.headers()).json()
        assert again["limits"]["monthly_ceiling_reached"] is True
