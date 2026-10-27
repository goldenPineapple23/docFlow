"""
D-178 (D-220): a Console write that runs in one transaction with its audit
row leaves a record whether it succeeds or fails.

The request row commits first, in a transaction of its own (founder,
2026-10-27). On success a `succeeded` outcome commits with the work. On
failure the rollback takes that, and a `failed` outcome pointing at the
request row is written in a transaction of its own, with what had already
happened outside the database (Stripe answered; files removed). A stop in
between leaves the request with no outcome: "Outcome unknown".

Driven without a database: the platform session is a fake that keeps a
transaction's rows only if it commits, so a rollback really discards them.
The staging suites cover the real SQL. All data is fictional (CLAUDE.md
Section 0 rule 4).
"""

from __future__ import annotations

import contextlib
import logging
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from docflow_core import admin_data_access, external_services, storage
from docflow_core.admin_data_access import ConsoleError
from tests.storage_fake import FakeStorage

NAME = "Acme Test Distributor"
ADMIN = uuid4()


class _Result:
    rowcount = 0

    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _World:
    """What the fake database holds: committed audit rows, canned answers to
    SELECTs by their first words, and switches that make it fail."""

    def __init__(self) -> None:
        self.committed: list[dict] = []
        self.answers: dict[str, object] = {}
        self.tenant_exists = True
        self.fail_commit_of_session: int | None = None  # 1-based
        self.fail_open_after: int | None = None
        self.stop_at_session: int | None = None  # 1-based: the process stops as it opens
        self.sessions = 0


class _Session:
    def __init__(self, world: _World) -> None:
        self.world = world
        self.rows: list[dict] = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("INSERT INTO admin_actions"):
            self.rows.append(dict(params or {}))
        if sql.startswith("SELECT 1 FROM tenants"):
            return _Result({"x": 1} if self.world.tenant_exists else None)
        for prefix, row in self.world.answers.items():
            if sql.startswith(prefix):
                return _Result(row)
        return _Result()


@pytest.fixture()
def world(monkeypatch):
    w = _World()

    @contextlib.contextmanager
    def platform_session():
        w.sessions += 1
        if w.fail_open_after is not None and w.sessions > w.fail_open_after:
            raise RuntimeError("database unreachable")
        if w.stop_at_session == w.sessions:
            raise _MachineStop
        session = _Session(w)
        yield session  # an exception in the body discards the rows: a rollback
        if w.fail_commit_of_session == w.sessions:
            raise RuntimeError("commit failed")
        w.committed.extend(session.rows)

    monkeypatch.setattr(admin_data_access, "platform_session", platform_session)
    return w


def _attempt(**kw):
    return admin_data_access._attempt(
        platform_admin_user_id=ADMIN,
        action="tier_change",
        target_tenant_id=kw.pop("target_tenant_id", uuid4()),
        target_type="tenant",
        target_id=kw.pop("target_id", None),
        **kw,
    )


def _intent_and_outcome(world: _World) -> tuple[dict, dict]:
    assert len(world.committed) == 2, world.committed
    intent, outcome = world.committed
    assert outcome["action"] == "outcome"
    assert outcome["payload"]["intent_id"] == intent["id"]
    return intent, outcome


# ── The attempt itself ───────────────────────────────────────────────────────


class _MachineStop(BaseException):
    """The process stopping: nothing after it runs, no handler sees it."""


def test_the_request_row_commits_on_its_own_before_the_work_starts(world):
    with _attempt(request={"tier": "growth"}) as attempt:
        # Inside the attempt, before the work has opened its transaction.
        assert world.sessions == 1
        assert len(world.committed) == 1
        request = world.committed[0]
        assert request["id"] == str(attempt.intent_id)
        assert request["action"] == "tier_change"
        assert request["payload"] == {"tier": "growth", "outcome_expected": True}
        with admin_data_access.platform_session() as session:
            attempt.record(session)


def test_success_commits_its_outcome_with_the_work(world):
    with _attempt() as attempt, admin_data_access.platform_session() as session:
        attempt.record(session, detail={"after": "growth"})
    request, outcome = _intent_and_outcome(world)
    assert request["id"] == str(attempt.intent_id)
    assert outcome["payload"] == {
        "intent_id": str(attempt.intent_id),
        "result": "succeeded",
        "code": None,
        "detail": {"after": "growth"},
    }
    assert world.sessions == 2  # the request's transaction, then the work's


def test_a_catalog_failure_is_recorded_after_the_rollback_against_the_request_row(world):
    with pytest.raises(ConsoleError):
        with _attempt(request={"tier": "growth"}) as attempt, admin_data_access.platform_session() as session:
            attempt.record(session)  # written, then rolled back with the work
            raise ConsoleError("BIL-004")
    request, outcome = _intent_and_outcome(world)  # one request row, never a second
    assert request["id"] == str(attempt.intent_id)
    assert request["payload"] == {"tier": "growth", "outcome_expected": True}
    assert outcome["payload"]["result"] == "failed"
    assert outcome["payload"]["code"] == "BIL-004"
    assert "detail" not in outcome["payload"]
    assert world.sessions == 3  # request, work (rolled back), failure record


def test_an_unexpected_error_is_recorded_with_its_type_and_no_code(world):
    with pytest.raises(KeyError):
        with _attempt() as attempt, admin_data_access.platform_session():
            attempt.detail["stage"] = "database"
            raise KeyError("x")
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["code"] is None
    assert outcome["payload"]["detail"] == {"stage": "database", "error_type": "KeyError"}


def test_a_failed_commit_of_the_work_is_recorded_as_failed_not_succeeded(world):
    world.fail_commit_of_session = 2  # the work's
    with pytest.raises(RuntimeError, match="commit failed"):
        with _attempt() as attempt, admin_data_access.platform_session() as session:
            attempt.record(session)
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["result"] == "failed"
    assert outcome["payload"]["detail"] == {"error_type": "RuntimeError"}


def test_a_request_row_that_cannot_be_written_stops_the_work_before_it_starts(world):
    world.fail_open_after = 0  # the request's own transaction is refused
    ran = False
    with pytest.raises(RuntimeError, match="database unreachable"):
        with _attempt():
            ran = True
    assert ran is False
    assert world.committed == []
    assert world.sessions == 1  # no failure record is attempted: nothing ran


def test_a_tenant_that_was_never_created_is_named_only_by_target_id(world):
    """`target_tenant_id` is a foreign key; a failed creation has no row to
    point at, so the record keeps the would-be id in `target_id`."""
    tenant_id = uuid4()
    world.tenant_exists = False
    with pytest.raises(ConsoleError):
        with _attempt(target_tenant_id=tenant_id, target_id=tenant_id):
            raise ConsoleError("CON-003")
    request, outcome = _intent_and_outcome(world)
    for row in (request, outcome):
        assert row["target_tenant_id"] is None
        assert row["target_id"] == str(tenant_id)


def test_a_creation_request_names_the_tenant_by_target_id_and_its_outcome_names_it(world):
    """The request row is committed before the tenant exists; the success
    outcome is written in the transaction that creates it."""
    tenant_id = uuid4()
    world.tenant_exists = False
    with _attempt(target_tenant_id=tenant_id, target_id=tenant_id) as attempt:
        world.tenant_exists = True  # the work inserts the tenant
        with admin_data_access.platform_session() as session:
            attempt.record(session)
    request, outcome = _intent_and_outcome(world)
    assert request["target_tenant_id"] is None and request["target_id"] == str(tenant_id)
    assert outcome["target_tenant_id"] == str(tenant_id)


def test_a_failure_to_record_never_replaces_the_error_the_founder_sees(world, caplog):
    world.fail_open_after = 1  # the request is written; every session after it refuses
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConsoleError) as info:
            with _attempt():
                raise ConsoleError("BIL-005")
    assert info.value.code == "BIL-005"
    assert [row["action"] for row in world.committed] == ["tier_change"]  # the request, no outcome
    assert "console_failure_not_recorded action=tier_change" in caplog.text


def test_a_stop_between_the_request_row_and_the_work_reads_outcome_unknown(world):
    with pytest.raises(_MachineStop):
        with _attempt():
            raise _MachineStop
    assert len(world.committed) == 1
    request = world.committed[0]
    assert request["payload"]["outcome_expected"] is True
    line = {"payload": request["payload"], "outcome": None, "source": "console", "event": "tier_change"}
    assert admin_data_access._audit_outcome(line) == {"outcome": None, "outcome_state": "unknown"}


# ── Tier change: whether Stripe answered ─────────────────────────────────────


@pytest.fixture()
def live_tenant(world):
    world.answers["SELECT t.name, t.status"] = {
        "name": NAME,
        "status": "active",
        "onboarding_status": "live",
        "tier_id": uuid4(),
        "stripe_subscription_id": "sub_test_1",
        "founding_price": False,
        "tier_code": "starter",
        "tier_name": "Starter",
        "tier_version": 1,
        "monthly_price": Decimal("249.00"),
        "document_allowance": 300,
    }
    world.answers["SELECT id, code, name, version"] = {
        "id": uuid4(),
        "code": "growth",
        "name": "Growth",
        "version": 1,
        "monthly_price": Decimal("599.00"),
        "promo_monthly_price": None,
        "document_allowance": 1000,
    }
    return uuid4()


def _change_tier(tenant_id: UUID) -> dict:
    return admin_data_access.change_tier(
        platform_admin_user_id=ADMIN,
        tenant_id=tenant_id,
        tier_code="growth",
        audit={"mfa_challenge_age_seconds": 12},
    )


def test_a_tier_change_stripe_refused_records_bil_005_and_that_stripe_refused(
    world, live_tenant, monkeypatch
):
    def refuse(**kw):
        raise external_services.ExternalServiceError("stripe", "card_declined")

    monkeypatch.setattr(external_services, "change_subscription_tier", refuse)
    with pytest.raises(ConsoleError) as info:
        _change_tier(live_tenant)
    assert info.value.code == "BIL-005"
    intent, outcome = _intent_and_outcome(world)
    assert intent["payload"] == {"tier": "growth", "mfa_challenge_age_seconds": 12, "outcome_expected": True}
    assert outcome["payload"]["code"] == "BIL-005"
    assert outcome["payload"]["detail"] == {"stripe": "refused"}


def test_a_tier_change_whose_commit_fails_after_stripe_accepted_says_so(world, live_tenant, monkeypatch):
    """The known risk D-220 records: Stripe has changed and DocFlow hasn't.
    The record says Stripe accepted, so the founder knows to look there."""
    monkeypatch.setattr(
        external_services,
        "change_subscription_tier",
        lambda **kw: (SimpleNamespace(status="active", founding_ends_at=None), 0),
    )
    world.fail_commit_of_session = 2  # the work's
    with pytest.raises(RuntimeError, match="commit failed"):
        _change_tier(live_tenant)
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["detail"] == {"stripe": "accepted", "error_type": "RuntimeError"}


def test_a_tier_change_that_succeeds_commits_both_rows(world, live_tenant, monkeypatch):
    monkeypatch.setattr(
        external_services,
        "change_subscription_tier",
        lambda **kw: (SimpleNamespace(status="active", founding_ends_at=None), 0),
    )
    _change_tier(live_tenant)
    intent, outcome = _intent_and_outcome(world)
    assert intent["payload"] == {"tier": "growth", "mfa_challenge_age_seconds": 12, "outcome_expected": True}
    assert outcome["payload"]["result"] == "succeeded"
    assert outcome["payload"]["detail"]["after"]["tier"] == "growth"
    assert outcome["payload"]["detail"]["founding_months_carried"] == 0


def test_a_tier_change_stopped_before_its_work_leaves_the_request_and_no_outcome(
    world, live_tenant, monkeypatch
):
    called = []
    monkeypatch.setattr(external_services, "change_subscription_tier", lambda **kw: called.append(kw))
    world.stop_at_session = 2  # the request committed; the work never opens
    with pytest.raises(_MachineStop):
        _change_tier(live_tenant)
    assert called == []  # Stripe never called
    assert [row["action"] for row in world.committed] == ["tier_change"]
    assert world.committed[0]["payload"]["outcome_expected"] is True


# ── Hard delete: the stage it reached ────────────────────────────────────────


@pytest.fixture()
def deletable(world):
    world.answers["SELECT name, status"] = {"name": NAME, "status": "pending_deletion", "due": True}
    fake = FakeStorage()
    with storage.set_backend_for_tests(fake):
        yield fake


def _delete(tenant_id: UUID, *, confirm_name: str = NAME) -> None:
    admin_data_access.delete_tenant(
        platform_admin_user_id=ADMIN,
        tenant_id=tenant_id,
        confirm_name=confirm_name,
        reason="customer asked, window elapsed",
        audit={"mfa_challenge_age_seconds": 40},
    )


def test_a_hard_delete_with_a_bad_name_records_life_005_at_the_check(world, deletable):
    with pytest.raises(ConsoleError) as info:
        _delete(uuid4(), confirm_name="  ")
    assert info.value.code == "LIFE-005"
    intent, outcome = _intent_and_outcome(world)
    assert intent["payload"] == {
        "reason": "customer asked, window elapsed",
        "mfa_challenge_age_seconds": 40,
        "outcome_expected": True,
    }
    assert outcome["payload"]["code"] == "LIFE-005"
    assert outcome["payload"]["detail"] == {"stage": "check"}


def test_a_hard_delete_whose_storage_fails_records_life_007_and_an_unknown_count(world, deletable):
    tenant = uuid4()
    deletable.undeletable.add(storage.save_file(tenant, "po.pdf", b"x"))
    with pytest.raises(ConsoleError) as info:
        _delete(tenant)
    assert info.value.code == "LIFE-007"
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["detail"] == {"stage": "storage", "objects_removed": "unknown"}


def test_a_hard_delete_that_fails_in_the_database_records_the_files_already_gone(
    world, deletable, monkeypatch
):
    tenant = uuid4()
    for _ in range(3):
        storage.save_file(tenant, "po.pdf", b"x")
    real_check = admin_data_access._check_deletable

    def fail_second_check(session, tenant_id, confirm_name, *, lock):
        if lock:
            raise RuntimeError("lock timeout")
        real_check(session, tenant_id, confirm_name, lock=lock)

    monkeypatch.setattr(admin_data_access, "_check_deletable", fail_second_check)
    with pytest.raises(RuntimeError):
        _delete(tenant)
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["code"] is None
    assert outcome["payload"]["detail"] == {
        "stage": "database",
        "objects_removed": 3,
        "error_type": "RuntimeError",
    }


def test_a_hard_delete_that_succeeds_commits_its_outcome_with_the_rows(world, deletable):
    tenant = uuid4()
    storage.save_file(tenant, "po.pdf", b"x")
    _delete(tenant)
    actions = [row["action"] for row in world.committed]
    assert actions == ["tenant_delete", "outcome", "tenant_delete_final_sweep"]
    assert world.committed[1]["payload"]["result"] == "succeeded"
    assert world.committed[1]["payload"]["intent_id"] == world.committed[0]["id"]
    assert world.committed[1]["payload"]["detail"]["objects_removed"] == 1
