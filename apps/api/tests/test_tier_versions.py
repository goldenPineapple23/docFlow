"""
Tier price versions (slice 5.9, D-137) against the real database.

The tiers table is real configuration, not test data, so every test that writes
does it inside one transaction and rolls it back: nothing here ever changes the
prices DocFlow actually offers. All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from docflow_core import tiers
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_console_schema


class _Rollback(Exception):
    pass


def _founder_id(session) -> str:
    """A fictional platform admin, created inside the caller's transaction, which
    every test here rolls back. It used to borrow whichever real platform admin
    the database happened to hold, so it only passed on staging and failed on a
    fresh database (found when CI got one, Phase 5.5 Stage 0)."""
    user_id = str(uuid4())
    session.execute(
        text(
            "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
            "VALUES (:id, NULL, :auth, :email, 'owner', true)"
        ),
        {"id": user_id, "auth": str(uuid4()), "email": f"tier-test-{user_id}@example.test"},
    )
    session.execute(
        text("INSERT INTO platform_admins (user_id, granted_by) VALUES (:id, :id)"), {"id": user_id}
    )
    return user_id


@requires_console_schema
def test_a_new_version_is_offered_to_new_tenants_and_existing_ones_keep_theirs():
    with pytest.raises(_Rollback), platform_session() as session:
        before = tiers.current(session, "starter")
        before_id = session.execute(
            text("SELECT id FROM tiers WHERE code = 'starter' AND is_current")
        ).scalar_one()
        new_id = tiers.create(
            session,
            "starter",
            tiers.Changes(monthly_price=before.monthly_price + Decimal("30")),
            platform_admin_user_id=_founder_id(session),
            note="test: price rise, rolled back",
        )

        after = tiers.current(session, "starter")
        assert after.version == before.version + 1
        assert after.monthly_price == before.monthly_price + Decimal("30")
        # Everything not named is carried over.
        assert (after.document_allowance, after.promo_monthly_price) == (
            before.document_allowance,
            before.promo_monthly_price,
        )
        # The old version still exists for the tenants on it; it is just no
        # longer offered.
        old = session.execute(
            text("SELECT is_current FROM tiers WHERE id = :i"), {"i": str(before_id)}
        ).scalar_one()
        assert old is False
        action = session.execute(
            text("SELECT payload FROM admin_actions WHERE target_id = :i AND action = 'tier_version_create'"),
            {"i": str(new_id)},
        ).scalar_one()
        assert action["before"]["monthly_price"] == str(before.monthly_price)
        assert action["after"]["monthly_price"] == str(before.monthly_price + Decimal("30"))
        raise _Rollback  # never keep a test price

    # And it really was rolled back.
    with platform_session() as session:
        assert tiers.current(session, "starter") == before


@requires_console_schema
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (tiers.Changes(), "nothing would change"),
        (tiers.Changes(monthly_price=Decimal("-1")), "zero or more"),
        (tiers.Changes(monthly_price=Decimal("10.005")), "whole cents"),
        (tiers.Changes(document_allowance=0), "at least 1"),
        (tiers.Changes(promo_monthly_price=Decimal("10000")), "below the monthly price"),
        (tiers.Changes(name="  "), "needs a name"),
    ],
)
def test_a_version_that_makes_no_sense_is_refused_before_anything_is_written(changes, reason):
    with platform_session() as session:
        with pytest.raises(tiers.TierVersionError, match=reason):
            tiers.preview(session, "starter", changes)


@requires_console_schema
def test_the_founding_price_is_removed_as_a_pair():
    with platform_session() as session:
        _, proposed, _ = tiers.preview(session, "growth", tiers.Changes(clear_promo=True))
        assert proposed.promo_monthly_price is None and proposed.promo_days is None
