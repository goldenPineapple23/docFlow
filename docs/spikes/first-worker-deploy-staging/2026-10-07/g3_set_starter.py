"""Set the G1/G3 test tenant to the current Starter tier: one UPDATE, one tenant."""

from __future__ import annotations

from datetime import UTC, datetime

from docflow_core import usage
from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

T = "40594983-03ff-47b1-ac40-f636488b8de7"
use_own_login("admin")
with platform_session() as s:
    starters = s.execute(text("SELECT id, document_allowance FROM tiers WHERE name = 'Starter' AND is_current")).all()
    if len(starters) != 1:
        raise SystemExit(f"expected one current Starter tier, found {len(starters)}; nothing changed")
    before = s.execute(text("SELECT tier_id FROM tenants WHERE id = :t"), {"t": T}).scalar_one()
    n = s.execute(
        text("UPDATE tenants SET tier_id = :tier, updated_at = now() WHERE id = :t AND tier_id IS NULL"),
        {"tier": str(starters[0][0]), "t": T},
    ).rowcount
    print(f"{datetime.now(UTC):%H:%M:%S}Z tier before: {before}; rows updated: {n}; Starter tier {starters[0][0]}")
with platform_session() as s:
    print("allowance_for:", usage.allowance_for(s, T))
