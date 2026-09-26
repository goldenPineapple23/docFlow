"""
Seed a test tenant so the slice 5.7 screens (D-126) have something to show:
documents held for each reason, and a monthly usage figure near or over the
allowance.

Nothing is sent to the model until YOU release a held document (each has a real,
tiny, fictional PO file, so a release costs about a cent), and nothing here
bypasses a rule the app enforces: these are plain rows, marked with a `demo-seed-` filename so `--clean` removes
exactly them and nothing else. Every sender is obviously fake (CLAUDE.md
Section 0 rule 4).

    python scripts/seed_quarantine_demo.py <tenant-id> [--usage 85] [--set-tier] [--ceiling]
    python scripts/seed_quarantine_demo.py --tenant-of walkthrough@example.test --usage 100
    python scripts/seed_quarantine_demo.py <tenant-id> --clean

  --usage N      make this month's counted documents N percent of the tier's allowance
                 (counted as "rejected" so they stay out of the review queue)
                 85 shows the 80% banner, 100 the 100% banner, 120 "keeps processing"
  --ceiling      go to 3x the allowance, so the NEXT real document is held (abuse ceiling)
  --set-tier     give the tenant the cheapest current tier if it has none (a test tenant only)
  --no-held      with --usage/--ceiling, change only the usage; don't add more held documents
  --clean        remove everything this script added
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from docflow_core.db import platform_session
from docflow_core.storage import save_file
from sqlalchemy import text

PREFIX = "demo-seed-"

# (reason, sender, subject, spf, dkim, dmarc, how many)
HELD = [
    (
        "attachment_cap",
        "bulk-sender@seed-buyer.example",
        "Twelve POs at once",
        "pass",
        "pass",
        "pass",
        2,
    ),
    (
        "unknown_sender_velocity",
        "new-buyer@seed-newco.example",
        "First order",
        "none",
        "none",
        "none",
        2,
    ),
    ("auth_fail", "ap@seed-spoof.example", "Urgent PO", "fail", "none", "fail", 1),
    ("abuse_ceiling", "flood@seed-flood.example", "PO", "pass", "pass", "pass", 1),
]


def _tenant_of(email: str) -> UUID:
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT tenant_id FROM users WHERE lower(email) = lower(:e) AND tenant_id IS NOT NULL"
            ),
            {"e": email},
        ).first()
    if row is None:
        sys.exit(f"No tenant user with the email {email}.")
    return UUID(str(row[0]))


def clean(tenant_id: UUID) -> None:
    with platform_session() as session:
        docs = session.execute(
            text(
                "DELETE FROM documents WHERE tenant_id = :t AND original_filename LIKE :p"
            ),
            {"t": str(tenant_id), "p": PREFIX + "%"},
        ).rowcount
        mails = session.execute(
            text("DELETE FROM raw_emails WHERE tenant_id = :t AND message_id LIKE :p"),
            {"t": str(tenant_id), "p": PREFIX + "%"},
        ).rowcount
    print(f"Removed {docs} seeded documents and {mails} seeded emails.")


def seed(
    tenant_id: UUID,
    usage_pct: int | None,
    ceiling: bool,
    set_tier: bool,
    with_held: bool = True,
) -> None:
    with platform_session() as session:
        tenant = (
            session.execute(
                text(
                    "SELECT t.name, t.tier_id, tr.name AS tier, tr.document_allowance "
                    "FROM tenants t LEFT JOIN tiers tr ON tr.id = t.tier_id WHERE t.id = :t"
                ),
                {"t": str(tenant_id)},
            )
            .mappings()
            .first()
        )
        if tenant is None:
            sys.exit("No such tenant.")
        if tenant["tier_id"] is None:
            if not set_tier:
                sys.exit(
                    f"{tenant['name']} has no tier, so it has no allowance. "
                    "Re-run with --set-tier to give it the cheapest current one."
                )
            tier = (
                session.execute(
                    text(
                        "SELECT id, name, document_allowance FROM tiers WHERE is_current ORDER BY monthly_price LIMIT 1"
                    )
                )
                .mappings()
                .first()
            )
            session.execute(
                text("UPDATE tenants SET tier_id = :i WHERE id = :t"),
                {"i": str(tier["id"]), "t": str(tenant_id)},
            )
            tenant = {
                **tenant,
                "tier": tier["name"],
                "document_allowance": tier["document_allowance"],
            }
            print(f"Gave {tenant['name']} the {tier['name']} tier.")
        allowance = int(tenant["document_allowance"])

        for reason, sender, subject, spf, dkim, dmarc, count in (
            HELD if with_held else []
        ):
            message_id = f"{PREFIX}{reason}-{uuid4().hex[:8]}"
            session.execute(
                text(
                    "INSERT INTO raw_emails (id, tenant_id, message_id, sender_email, sender_domain, subject, "
                    "spf_result, dkim_result, dmarc_result, attachment_count, outcome, created_at) "
                    "VALUES (:id, :t, :m, :s, :d, :subj, :spf, :dkim, :dmarc, :n, 'quarantined', now())"
                ),
                {
                    "id": str(uuid4()),
                    "t": str(tenant_id),
                    "m": message_id,
                    "s": sender,
                    "d": sender.split("@")[1],
                    "subj": subject,
                    "spf": spf,
                    "dkim": dkim,
                    "dmarc": dmarc,
                    "n": count,
                },
            )
            for i in range(count):
                doc_id = uuid4()
                # A real, tiny, fictional PO file, so releasing a seeded document
                # sends something the worker can actually read.
                content = (
                    "PURCHASE ORDER\n"
                    f"PO Number: SEED-{reason.upper()}-{i + 1}-{uuid4().hex[:6]}\n"
                    "Date: 2026-09-23\nBuyer: Acme Test Distributor\nVendor: Beacon Test Supply\n"
                    "Line 1: TEST-1001  Test Beans 5lb  Qty 4  Unit Price 47.50  Total 190.00\n"
                    "Order Total: 190.00 USD\n"
                ).encode()
                storage_path = save_file(
                    tenant_id, f"{PREFIX}{reason}-{i + 1}.txt", content
                )
                session.execute(
                    text(
                        "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, "
                        "status, content_sha256, sender_email, message_id, quarantine_reason, "
                        "quarantined_at, created_at) "
                        "VALUES (:id, :t, :f, :p, 'email', 'quarantined', :sha, :s, :m, :r, now(), "
                        "now() - make_interval(mins => :age))"
                    ),
                    {
                        "id": str(doc_id),
                        "t": str(tenant_id),
                        "f": f"{PREFIX}{reason}-{i + 1}.txt",
                        "p": storage_path,
                        "sha": hashlib.sha256(content).hexdigest(),
                        "s": sender,
                        "m": message_id,
                        "r": reason,
                        "age": 30 - 5 * i,
                    },
                )
        if with_held:
            print(
                "Seeded 6 held documents: attachment cap x2, unknown senders x2, "
                "failed sender check x1, abuse ceiling x1."
            )

        target = None
        if ceiling:
            target = allowance * 3
        elif usage_pct is not None:
            target = round(allowance * usage_pct / 100)
        if target is not None:
            counted = session.execute(
                text(
                    "SELECT count(*) FROM documents d JOIN tenants t ON t.id = d.tenant_id "
                    "WHERE d.tenant_id = :t AND NOT d.is_test_batch AND d.deleted_at IS NULL "
                    "AND d.duplicate_of_document_id IS NULL AND d.status NOT IN ('failed','quarantined','staged') "
                    "AND (d.created_at AT TIME ZONE coalesce(nullif(t.timezone,''),'UTC')) >= "
                    "date_trunc('month', now() AT TIME ZONE coalesce(nullif(t.timezone,''),'UTC'))"
                ),
                {"t": str(tenant_id)},
            ).scalar_one()
            extra = max(0, target - int(counted))
            if extra:
                session.execute(
                    text(
                        "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, "
                        "status, content_sha256, raw_json, created_at) "
                        "SELECT gen_random_uuid(), :t, :p || g || '.txt', 'tenants/seed/seed.txt', 'upload', "
                        "'rejected', md5(random()::text || g::text), "
                        # Seeded outside the pipeline: a labelled stand-in answer (0027, D-158).
                        "'{\"header\": {}, \"line_items\": [], \"seeded_demo\": true}'::jsonb, "
                        "now() FROM generate_series(1, :n) g"
                    ),
                    {"t": str(tenant_id), "p": PREFIX + "count-", "n": extra},
                )
            print(
                f"This month now counts {int(counted) + extra} of {allowance} documents ({tenant['tier']})."
            )

    print("Done. `--clean` removes exactly what was added.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tenant_id", nargs="?")
    parser.add_argument("--tenant-of", help="use the tenant this user belongs to")
    parser.add_argument("--usage", type=int)
    parser.add_argument("--ceiling", action="store_true")
    parser.add_argument("--set-tier", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--no-held",
        action="store_true",
        help="change only the usage figure; do not add another set of held documents",
    )
    args = parser.parse_args()
    if not args.tenant_id and not args.tenant_of:
        parser.error("give a tenant id, or --tenant-of <email>")
    tid = UUID(args.tenant_id) if args.tenant_id else _tenant_of(args.tenant_of)
    if args.clean:
        clean(tid)
    else:
        seed(tid, args.usage, args.ceiling, args.set_tier, with_held=not args.no_held)
