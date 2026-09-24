"""
Tier price versions (Section 7.15.2 "Tier configuration"; slice 5.9, D-137).

"Changing a tier's price creates a new version; existing tenants keep their
version until the founder explicitly moves them." A version is never edited:
a change is a new row, `version + 1`, which becomes the one offered to new
tenants (`is_current`), while every existing tenant keeps pointing at the row
it signed up on. Moving a customer onto the new version is a plan change
(`admin_data_access.change_tier`).

For now the founder does this from the command line
(`scripts/new_tier_version.py`), which previews and writes nothing until told
to. The founder chose no editing screen yet but asked for one to be easy to
add (2026-09-24): a screen would call `preview` and `create` exactly as the
script does, through an admin route, and nothing here would change.

Money is `Decimal` throughout and a string in every payload (Section 7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

CODES = ("starter", "growth", "scale")


class TierVersionError(ValueError):
    """A proposed version that DocFlow will not create; the message says why."""


@dataclass(frozen=True)
class TierVersion:
    code: str
    version: int
    name: str
    monthly_price: Decimal
    promo_monthly_price: Decimal | None
    promo_days: int | None
    document_allowance: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "version": self.version,
            "name": self.name,
            "monthly_price": str(self.monthly_price),
            "promo_monthly_price": None
            if self.promo_monthly_price is None
            else str(self.promo_monthly_price),
            "promo_days": self.promo_days,
            "document_allowance": self.document_allowance,
        }


@dataclass(frozen=True)
class Changes:
    """What to change; anything left as None is carried over. `clear_promo`
    removes the founding price from the new version."""

    name: str | None = None
    monthly_price: Decimal | None = None
    promo_monthly_price: Decimal | None = None
    promo_days: int | None = None
    document_allowance: int | None = None
    clear_promo: bool = False


def current(session: Session, code: str) -> TierVersion:
    if code not in CODES:
        raise TierVersionError(f"unknown tier {code!r}; expected one of {', '.join(CODES)}")
    row = (
        session.execute(
            text(
                "SELECT code, version, name, monthly_price, promo_monthly_price, promo_days, "
                "document_allowance FROM tiers WHERE code = :c AND is_current"
            ),
            {"c": code},
        )
        .mappings()
        .one()
    )
    return TierVersion(
        code=row["code"],
        version=row["version"],
        name=row["name"],
        monthly_price=Decimal(row["monthly_price"]),
        promo_monthly_price=None
        if row["promo_monthly_price"] is None
        else Decimal(row["promo_monthly_price"]),
        promo_days=row["promo_days"],
        document_allowance=row["document_allowance"],
    )


def preview(session: Session, code: str, changes: Changes) -> tuple[TierVersion, TierVersion, int]:
    """(current, proposed, tenants staying on current). Writes nothing."""
    now = current(session, code)
    proposed = replace(
        now,
        version=now.version + 1,
        name=changes.name if changes.name is not None else now.name,
        monthly_price=changes.monthly_price if changes.monthly_price is not None else now.monthly_price,
        promo_monthly_price=(
            None
            if changes.clear_promo
            else changes.promo_monthly_price
            if changes.promo_monthly_price is not None
            else now.promo_monthly_price
        ),
        promo_days=(
            None
            if changes.clear_promo
            else changes.promo_days
            if changes.promo_days is not None
            else now.promo_days
        ),
        document_allowance=(
            changes.document_allowance if changes.document_allowance is not None else now.document_allowance
        ),
    )
    _validate(now, proposed)
    staying = session.execute(
        text(
            "SELECT count(*) FROM tenants t JOIN tiers tr ON tr.id = t.tier_id "
            "WHERE tr.code = :c AND tr.is_current AND t.deleted_at IS NULL"
        ),
        {"c": code},
    ).scalar_one()
    return now, proposed, int(staying)


def _validate(now: TierVersion, proposed: TierVersion) -> None:
    if proposed == replace(now, version=proposed.version):
        raise TierVersionError("nothing would change -- no new version is needed")
    if not proposed.name.strip():
        raise TierVersionError("the tier needs a name")
    if proposed.monthly_price < 0 or proposed.monthly_price != proposed.monthly_price.quantize(
        Decimal("0.01")
    ):
        raise TierVersionError("the monthly price must be zero or more, in whole cents")
    if proposed.document_allowance <= 0:
        raise TierVersionError("the document allowance must be at least 1")
    has_price = proposed.promo_monthly_price is not None
    has_days = proposed.promo_days is not None
    if has_price != has_days:
        raise TierVersionError("a founding price needs both a price and a number of days, or neither")
    if has_price:
        assert proposed.promo_monthly_price is not None and proposed.promo_days is not None
        if not Decimal("0") <= proposed.promo_monthly_price < proposed.monthly_price:
            raise TierVersionError("the founding price must be below the monthly price")
        if proposed.promo_days <= 0:
            raise TierVersionError("the founding period must be at least one day")


def create(session: Session, code: str, changes: Changes, *, platform_admin_user_id: UUID, note: str) -> UUID:
    """Create the new version and make it the one offered to new tenants, in
    the caller's transaction, and record the founder's action with the before
    and after. Existing tenants are not touched."""
    if len(note.strip()) < 5:
        raise TierVersionError("say briefly why the price is changing (it goes in the audit trail)")
    # Serialise with any other change to this tier's versions.
    session.execute(text("SELECT id FROM tiers WHERE code = :c FOR UPDATE"), {"c": code})
    now, proposed, staying = preview(session, code, changes)
    new_id = uuid4()
    # Only one current version per code (idx_tiers_one_current): retire the
    # old one first, in the same transaction.
    session.execute(text("UPDATE tiers SET is_current = false WHERE code = :c AND is_current"), {"c": code})
    session.execute(
        text(
            """
            INSERT INTO tiers (id, code, version, name, monthly_price, promo_monthly_price,
                               promo_days, document_allowance, is_current)
            VALUES (:id, :code, :version, :name, :price, :promo, :days, :allowance, true)
            """
        ),
        {
            "id": str(new_id),
            "code": proposed.code,
            "version": proposed.version,
            "name": proposed.name,
            "price": str(proposed.monthly_price),
            "promo": None if proposed.promo_monthly_price is None else str(proposed.promo_monthly_price),
            "days": proposed.promo_days,
            "allowance": proposed.document_allowance,
        },
    )
    session.execute(
        text(
            """
            INSERT INTO admin_actions (id, platform_admin_user_id, action, target_type, target_id,
                                       payload, created_at)
            VALUES (:id, :by, 'tier_version_create', 'tier', :target, CAST(:payload AS jsonb), :at)
            """
        ),
        {
            "id": str(uuid4()),
            "by": str(platform_admin_user_id),
            "target": str(new_id),
            "payload": json.dumps(
                {
                    "before": now.as_dict(),
                    "after": proposed.as_dict(),
                    "tenants_staying": staying,
                    "note": note,
                }
            ),
            "at": datetime.now(timezone.utc),
        },
    )
    return new_id
