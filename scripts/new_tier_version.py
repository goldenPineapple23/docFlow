"""
Change a tier's price or allowance by creating a new version (slice 5.9, D-137).

Section 7.15.2: "Changing a tier's price creates a new version; existing
tenants keep their version until the founder explicitly moves them." New
tenants get the new version from now on; everyone already on the tier stays on
their current price until you change their plan from the Console.

Previews by default and writes NOTHING. Add --apply to make the change.

Usage (from the repo folder):

    apps\\api\\.venv\\Scripts\\python.exe scripts\\new_tier_version.py starter ^
        --monthly-price 329 --by you@example.com --note "2027 price list"

    ... --apply        actually create it (after reading the preview)
    --promo-price N / --promo-days N    change the founding price or period
    --no-promo         the new version has no founding price
    --allowance N      documents per month
    --name TEXT        the name customers see

Run by hand, by the founder. Not reachable from any API route. The same
functions (`docflow_core.tiers.preview` / `create`) are what an editing screen
in the Console would call if one is ever added.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

from docflow_core import tiers
from docflow_core.db import platform_session
from sqlalchemy import text


def _money(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"{value!r} is not an amount") from None


def _show(label: str, v: tiers.TierVersion) -> None:
    promo = (
        f"founding ${v.promo_monthly_price} for {v.promo_days} days"
        if v.promo_monthly_price is not None
        else "no founding price"
    )
    print(
        f"  {label:<9} v{v.version}  {v.name}: ${v.monthly_price}/month, "
        f"{v.document_allowance:,} documents, {promo}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("code", choices=tiers.CODES)
    parser.add_argument("--monthly-price", type=_money)
    parser.add_argument("--promo-price", type=_money)
    parser.add_argument("--promo-days", type=int)
    parser.add_argument("--no-promo", action="store_true")
    parser.add_argument("--allowance", type=int)
    parser.add_argument("--name")
    parser.add_argument("--by", required=True, help="your platform-admin email address")
    parser.add_argument("--note", default="", help="why the price is changing (kept in the audit trail)")
    parser.add_argument("--apply", action="store_true", help="create the version (default: preview only)")
    args = parser.parse_args()

    changes = tiers.Changes(
        name=args.name,
        monthly_price=args.monthly_price,
        promo_monthly_price=args.promo_price,
        promo_days=args.promo_days,
        document_allowance=args.allowance,
        clear_promo=args.no_promo,
    )

    with platform_session() as session:
        admin = session.execute(
            text(
                "SELECT u.id FROM users u JOIN platform_admins p ON p.user_id = u.id "
                "WHERE lower(u.email) = lower(:e) AND p.revoked_at IS NULL"
            ),
            {"e": args.by},
        ).scalar_one_or_none()
        if admin is None:
            print(f"{args.by} is not an active platform admin.", file=sys.stderr)
            return 2
        try:
            now, proposed, staying = tiers.preview(session, args.code, changes)
        except tiers.TierVersionError as exc:
            print(f"Not created: {exc}.", file=sys.stderr)
            return 1

        print(f"\n{now.name} ({now.code})")
        _show("now", now)
        _show("proposed", proposed)
        print(
            f"\n  {staying} tenant(s) on the current version keep it until you change their plan.\n"
            "  New tenants get the proposed version.\n"
        )
        if not args.apply:
            print("Preview only -- nothing was written. Add --apply to create it.")
            session.rollback()
            return 0
        try:
            new_id = tiers.create(session, args.code, changes, platform_admin_user_id=admin, note=args.note)
        except tiers.TierVersionError as exc:
            print(f"Not created: {exc}.", file=sys.stderr)
            session.rollback()
            return 1
    print(f"Created {proposed.name} v{proposed.version} ({new_id}). Logged as a founder action.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
