"""
Deal terms (DECISIONS.md D-117): the tier, the founding-customer price and
the setup fee, agreed in the sales conversation and recorded when the
tenant is created (Section 7.15.2 Step 2) -- editable until go-live, locked
after. Go-live (Step 9) bills exactly what is recorded here.

Every amount comes from the `tiers` and `setup_fee_presets` tables, never
from code (Section 10). A typed amount is accepted only inside the chosen
preset's range: exactly $750 for Founding, $2,000-$2,500 for Complex, and so
on. Money is Decimal throughout and a string on the wire (Section 7.1).

One validator for both callers -- Create tenant and the Overview's deal
editor -- so the rules can't drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

BILLING_MODES = ("stripe", "invoiced_manually")


class DealTermsError(Exception):
    """A refusal with an error-catalog code (CON-003, ONB-007, ONB-011..ONB-014)."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class DealTerms:
    """The deal as the founder entered it -- unvalidated."""

    tier_code: str
    setup_fee_preset: str
    setup_fee_amount: str | None  # money as a string; None = the preset's own amount
    setup_fee_billing: str
    setup_fee_note: str | None
    founding_price: bool


@dataclass(frozen=True)
class ResolvedDeal:
    tier_id: UUID
    tier_code: str
    preset_id: UUID
    preset_code: str
    setup_fee_amount: Decimal
    setup_fee_billing: str
    setup_fee_note: str | None
    founding_price: bool

    def columns(self) -> dict[str, Any]:
        """The tenants columns this deal sets, as bind parameters."""
        return {
            "tier_id": str(self.tier_id),
            "setup_fee_preset_id": str(self.preset_id),
            "setup_fee_amount": str(self.setup_fee_amount),
            "setup_fee_billing": self.setup_fee_billing,
            "setup_fee_note": self.setup_fee_note,
            "founding_price": self.founding_price,
        }

    def summary(self) -> dict[str, Any]:
        """For admin_actions and lifecycle payloads -- no customer data."""
        return {
            "tier": self.tier_code,
            "tier_id": str(self.tier_id),
            "setup_fee_preset": self.preset_code,
            "setup_fee_preset_id": str(self.preset_id),
            "setup_fee_amount": str(self.setup_fee_amount),
            "setup_fee_billing": self.setup_fee_billing,
            "founding_price": self.founding_price,
        }


def parse_amount(raw: str | None) -> Decimal | None:
    """A money string to Decimal (two places at most), or None if blank.
    Raises ONB-007 for anything that isn't a plain, non-negative amount."""
    if raw is None or not raw.strip():
        return None
    try:
        amount = Decimal(raw.strip().replace(",", "").lstrip("$"))
    except InvalidOperation:
        raise DealTermsError("ONB-007") from None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise DealTermsError("ONB-007")
    return amount.quantize(Decimal("0.01"))


def resolve(
    session: Session,
    terms: DealTerms,
    *,
    current_tier_id: UUID | None = None,
    current_preset_id: UUID | None = None,
) -> ResolvedDeal:
    """
    Validate the deal against the tables. A tier or preset whose code is
    unchanged keeps the version the tenant already has (`current_*`), so
    editing a note never silently moves a tenant to a newer price; a new
    choice takes the current version.
    """
    tier = _pick(session, "tiers", terms.tier_code, current_tier_id, "id, code, promo_monthly_price")
    if tier is None:
        raise DealTermsError("CON-003")
    preset = _pick(
        session,
        "setup_fee_presets",
        terms.setup_fee_preset,
        current_preset_id,
        "id, code, name, default_amount, min_amount, max_amount, note_required",
    )
    if preset is None:
        raise DealTermsError("ONB-014")
    if terms.setup_fee_billing not in BILLING_MODES:
        raise DealTermsError("ONB-007")

    amount = parse_amount(terms.setup_fee_amount)
    if amount is None:
        if preset["default_amount"] is None:
            raise DealTermsError("ONB-007")
        amount = Decimal(preset["default_amount"])
    low = Decimal(preset["min_amount"])
    high = Decimal(preset["max_amount"]) if preset["max_amount"] is not None else None
    if amount < low or (high is not None and amount > high):
        raise DealTermsError(
            "ONB-012",
            {
                "preset": preset["name"],
                "min_amount": str(low),
                "max_amount": str(high) if high is not None else None,
            },
        )

    note = (terms.setup_fee_note or "").strip() or None
    if preset["note_required"] and note is None:
        raise DealTermsError("ONB-013", {"preset": preset["name"]})

    return ResolvedDeal(
        tier_id=UUID(str(tier["id"])),
        tier_code=tier["code"],
        preset_id=UUID(str(preset["id"])),
        preset_code=preset["code"],
        setup_fee_amount=amount,
        setup_fee_billing=terms.setup_fee_billing,
        setup_fee_note=note,
        # A tier without a promo has no founding price to give.
        founding_price=terms.founding_price and tier["promo_monthly_price"] is not None,
    )


def _pick(session: Session, table: str, code: str, current_id: UUID | None, columns: str):
    # `table` and `columns` are fixed literals from this module, never input.
    if current_id is not None:
        row = session.execute(
            text(f"SELECT {columns} FROM {table} WHERE id = :id AND code = :code"),
            {"id": str(current_id), "code": code},
        ).mappings().first()
        if row is not None:
            return row
    return session.execute(
        text(f"SELECT {columns} FROM {table} WHERE code = :code AND is_current"),
        {"code": code},
    ).mappings().first()


def list_current_presets(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT id, code, version, name, description, default_amount, min_amount, max_amount, "
            "note_required FROM setup_fee_presets WHERE is_current ORDER BY sort_order"
        )
    ).mappings().all()
    return [dict(r) for r in rows]
