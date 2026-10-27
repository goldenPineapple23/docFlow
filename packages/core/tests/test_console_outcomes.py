"""
D-178 (D-220): a Console write that runs in one transaction with its audit
row leaves a record whether it succeeds or fails.

On success the intent row and a `succeeded` outcome commit with the work. On
failure the rollback takes both, so the failure is written again -- the same
intent id and a `failed` outcome -- in a transaction of its own, with what had
already happened outside the database (Stripe answered; files removed).

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


def test_success_commits_the_intent_and_its_outcome_with_the_work(world):
    with _attempt() as attempt, admin_data_access.platform_session() as session:
        attempt.record(session, payload={"after": "growth"})
    intent, outcome = _intent_and_outcome(world)
    assert intent["action"] == "tier_change"
    assert intent["id"] == str(attempt.intent_id)
    assert intent["payload"] == {"after": "growth", "outcome_expected": True}
    assert outcome["payload"] == {"intent_id": str(attempt.intent_id), "result": "succeeded", "code": None}
    assert world.sessions == 1  # one transaction: they commit together or not at all


def test_a_catalog_failure_is_recorded_after_the_rollback_with_its_code(world):
    with pytest.raises(ConsoleError):
        with _attempt(request={"tier": "growth"}) as attempt, admin_data_access.platform_session() as session:
            attempt.record(session)  # written, then rolled back with the work
            raise ConsoleError("BIL-004")
    intent, outcome = _intent_and_outcome(world)
    assert intent["id"] == str(attempt.intent_id)
    assert intent["payload"] == {"tier": "growth", "outcome_expected": True}
    assert outcome["payload"]["result"] == "failed"
    assert outcome["payload"]["code"] == "BIL-004"
    assert "detail" not in outcome["payload"]


def test_an_unexpected_error_is_recorded_with_its_type_and_no_code(world):
    with pytest.raises(KeyError):
        with _attempt() as attempt, admin_data_access.platform_session():
            attempt.detail["stage"] = "database"
            raise KeyError("x")
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["code"] is None
    assert outcome["payload"]["detail"] == {"stage": "database", "error_type": "KeyError"}


def test_a_failed_commit_is_recorded_too(world):
    world.fail_commit_of_session = 1
    with pytest.raises(RuntimeError, match="commit failed"):
        with _attempt() as attempt, admin_data_access.platform_session() as session:
            attempt.record(session)
    _, outcome = _intent_and_outcome(world)
    assert outcome["payload"]["detail"] == {"error_type": "RuntimeError"}


def test_a_tenant_that_was_never_created_is_named_only_by_target_id(world):
    """`target_tenant_id` is a foreign key; a failed creation has no row to
    point at, so the record keeps the would-be id in `target_id`."""
    tenant_id = uuid4()
    world.tenant_exists = False
    with pytest.raises(ConsoleError):
        with _attempt(target_tenant_id=tenant_id, target_id=tenant_id):
            raise ConsoleError("CON-003")
    intent, outcome = _intent_and_outcome(world)
    for row in (intent, outcome):
        assert row["target_tenant_id"] is None
        assert row["target_id"] == str(tenant_id)


def test_a_failure_to_record_never_replaces_the_error_the_founder_sees(world, caplog):
    world.fail_open_after = 0  # every session refuses, including the record's
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConsoleError) as info:
            with _attempt():
                raise ConsoleError("BIL-005")
    assert info.value.code == "BIL-005"
    assert world.committed == []
    assert "console_failure_not_recorded action=tier_change" in caplog.text


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
    world.fail_commit_of_session = 1
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
    assert intent["payload"]["mfa_challenge_age_seconds"] == 12
    assert intent["payload"]["outcome_expected"] is True
    assert outcome["payload"]["result"] == "succeeded"


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
