"""
Approved-example prompting (CLAUDE.md Section 7.13; slice 5.10, D-141).

For an order from buyer X, the extraction prompt may carry up to three of X's
most recent human-approved orders from the same tenant: the text the parser
produced for each (cut to a budget) and its approved values. No training, no
weights -- just a few verified examples shown at request time.

The order of events is fixed by the chicken-and-egg problem 7.13 names: the
buyer normally comes out of extraction, but examples must be chosen before it.
So, before extraction:

  1. The tenant's flag must be on (default off; the founder switches it on
     after a live golden run passes with it on). Off means nothing below runs
     and the request is byte-for-byte the no-example one.
  2. The buyer is identified from the intake email's sender: an exact match
     on a buyer's contact email, else a company domain that belongs to
     exactly one buyer (never a free-mail domain).
  3. Otherwise, and only if some buyer in this tenant could qualify at all,
     one small routing-model call reads the header for the buyer's name and
     email (D-141). Below ROUTING_MIN_CONFIDENCE, or if that read suspects an
     injection, no buyer is known.
  4. The buyer needs EXAMPLE_MIN_APPROVED_DOCS approved or exported orders.
  5. Examples: the buyer's most recent approved/exported orders that have
     stored text, newest first, at most EXAMPLE_MAX_PER_PROMPT.

Every query runs in a tenant-scoped session with an explicit tenant filter as
well, so an example can never come from another tenant (Section 7.5, 7.13:
"No learning crosses a tenant boundary"). Nothing here writes: the buyer this
module finds is only used to pick examples. The buyer the reviewer sees still
comes from the full extraction (Section 7.6).

Logs carry IDs and outcomes only -- never a name, an email or a value
(Section 7.10).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import buyers
from docflow_core.constants import (
    EXAMPLE_MAX_PER_PROMPT,
    EXAMPLE_MIN_APPROVED_DOCS,
    EXAMPLE_TEXT_CHAR_BUDGET,
    ROUTING_MIN_CONFIDENCE,
    ROUTING_TEXT_CHAR_BUDGET,
    constants_in_effect,
)
from docflow_core.extraction import (
    PromptExample,
    RoutingResult,
    build_text_content,
    extraction_input_cost,
    read_buyer_header,
    wrap_document_content,
)
from docflow_core.lifecycle import _lifecycle_event
from docflow_core.storage import read_file

logger = logging.getLogger(__name__)

# Addresses on these domains belong to people, not companies: two unrelated
# buyers can both order from gmail.com, so a domain match here proves nothing.
FREEMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com",
        "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
        "protonmail.com", "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com",
        "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
    }
)

_APPROVED = "('approved', 'exported')"
_TRUNCATION_MARK = "\n[... truncated ...]"

# The response schema's own field names (docflow_core.extraction): the
# example's approved values are shown in exactly the shape the model returns.
_HEADER_FIELDS = (
    "po_number", "order_date", "requested_delivery_date", "buyer_name", "buyer_contact_email",
    "ship_to_address", "payment_terms", "order_total", "currency", "notes",
)
_LINE_FIELDS = ("sku", "description", "quantity", "unit", "unit_price", "line_total")


@dataclass(frozen=True)
class ExamplePlan:
    examples: list[PromptExample] = field(default_factory=list)
    buyer_id: UUID | None = None
    # "sender_email" | "sender_domain" | "header_read" | None
    identified_by: str | None = None
    # The routing call, when one was made -- logged as its own extraction_run.
    routing: RoutingResult | None = None
    # Why examples were or weren't used, for the log line.
    outcome: str = "flag_off"


# ── The pieces, each a plain query in a tenant-scoped session ──────────────


def is_enabled(session: Session, tenant_id: UUID) -> bool:
    row = session.execute(
        text("SELECT example_prompting_enabled FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    ).first()
    return bool(row and row[0])


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[1] if "@" in email else ""


def buyer_from_sender(session: Session, tenant_id: UUID, sender_email: str | None) -> tuple[UUID, str] | None:
    """
    The sender's address as a buyer's own contact email, or the sender's
    company domain when exactly one buyer uses it. Deliberately NOT "whoever
    sent this buyer's earlier orders": a tenant's own staff forwarding orders
    for several buyers would then lend one buyer's examples to another.
    """
    email = (sender_email or "").strip().lower()
    if "@" not in email:
        return None
    matched = buyers.find_buyer_by_email(session, tenant_id, email)
    if matched is not None:
        return matched, "sender_email"
    domain = _domain(email)
    if not domain or domain in FREEMAIL_DOMAINS:
        return None
    rows = session.execute(
        text(
            "SELECT DISTINCT id FROM buyers "
            "WHERE tenant_id = :t AND deleted_at IS NULL "
            "AND lower(split_part(contact_email, '@', 2)) = :domain LIMIT 2"
        ),
        {"t": str(tenant_id), "domain": domain},
    ).all()
    if len(rows) == 1:
        return UUID(str(rows[0][0])), "sender_domain"
    return None


def buyer_from_routing(session: Session, tenant_id: UUID, routing: RoutingResult) -> UUID | None:
    """
    The routing read's buyer, matched read-only the way Section 7.6 matches:
    contact email, then exact normalized name, then a founder-made alias.
    Nothing is created and no rule counter moves -- this answer only picks
    examples.
    """
    if not routing.ok or routing.injection_suspected or routing.buyer_confidence < ROUTING_MIN_CONFIDENCE:
        return None
    email = (routing.buyer_contact_email or "").strip().lower()
    if email:
        matched = buyers.find_buyer_by_email(session, tenant_id, email)
        if matched is not None:
            return matched
    normalized = buyers.normalize_buyer_name(routing.buyer_name)
    if not normalized:
        return None
    matched = buyers.find_buyer_by_normalized_name(session, tenant_id, normalized)
    if matched is not None:
        return matched
    alias = buyers.find_buyer_by_alias(session, normalized)
    return alias[0] if alias else None


def approved_count(session: Session, tenant_id: UUID, buyer_id: UUID) -> int:
    return int(
        session.execute(
            text(
                f"""
                SELECT count(*) FROM documents d
                JOIN document_headers h ON h.document_id = d.id
                WHERE d.tenant_id = :t AND h.tenant_id = :t AND h.buyer_id = :b
                  AND d.status IN {_APPROVED} AND d.deleted_at IS NULL
                  AND d.approved_json IS NOT NULL
                """
            ),
            {"t": str(tenant_id), "b": str(buyer_id)},
        ).scalar_one()
    )


def any_buyer_qualifies(session: Session, tenant_id: UUID) -> bool:
    """Whether the routing call could possibly lead anywhere -- if no buyer
    has enough approved orders, the call is skipped and costs nothing."""
    row = session.execute(
        text(
            f"""
            SELECT 1 FROM documents d
            JOIN document_headers h ON h.document_id = d.id
            WHERE d.tenant_id = :t AND h.tenant_id = :t AND h.buyer_id IS NOT NULL
              AND d.status IN {_APPROVED} AND d.deleted_at IS NULL
              AND d.approved_json IS NOT NULL
            GROUP BY h.buyer_id HAVING count(*) >= :n LIMIT 1
            """
        ),
        {"t": str(tenant_id), "n": EXAMPLE_MIN_APPROVED_DOCS},
    ).first()
    return row is not None


def example_extraction(approved_json: dict[str, Any] | str) -> dict[str, Any]:
    """
    The approved snapshot (docflow_core.review.build_snapshot) reshaped into
    the response schema: printed values only. Catalog matches, IDs and hashes
    are DocFlow's own additions and never belong in a "what the page said"
    example.
    """
    snapshot = json.loads(approved_json) if isinstance(approved_json, str) else approved_json
    header = snapshot.get("header") or {}
    return {
        "header": {name: header.get(name) for name in _HEADER_FIELDS},
        "line_items": [
            {"line_number": line.get("line_number"), **{name: line.get(name) for name in _LINE_FIELDS}}
            for line in snapshot.get("lines") or []
        ],
    }


def _truncate(value: str, budget: int) -> str:
    return value if len(value) <= budget else value[:budget] + _TRUNCATION_MARK


def select_examples(
    session: Session,
    tenant_id: UUID,
    buyer_id: UUID,
    *,
    exclude_document_id: UUID,
    read: Callable[[str], bytes] = read_file,
) -> list[PromptExample]:
    """
    Newest first, approved or exported only, this tenant and this buyer only,
    and only orders whose text DocFlow kept (a scan read visually has none and
    can never be an example -- never an image, never the raw file).
    """
    rows = session.execute(
        text(
            f"""
            SELECT d.id, d.approved_json, d.extracted_text_path
            FROM documents d
            JOIN document_headers h ON h.document_id = d.id
            WHERE d.tenant_id = :t AND h.tenant_id = :t AND h.buyer_id = :b
              AND d.status IN {_APPROVED} AND d.deleted_at IS NULL
              AND d.approved_json IS NOT NULL AND d.extracted_text_path IS NOT NULL
              AND d.id <> :exclude
            ORDER BY d.approved_at DESC NULLS LAST, d.created_at DESC
            LIMIT :n
            """
        ),
        {
            "t": str(tenant_id),
            "b": str(buyer_id),
            "exclude": str(exclude_document_id),
            "n": EXAMPLE_MAX_PER_PROMPT,
        },
    ).mappings().all()

    prefix = f"tenants/{tenant_id}/"
    examples: list[PromptExample] = []
    for row in rows:
        path = row["extracted_text_path"]
        if not path.startswith(prefix):
            # Section 7.5: the storage layer's tenant prefix, checked again.
            logger.error("example_text_outside_tenant document_id=%s", row["id"])
            continue
        try:
            body = read(path).decode("utf-8", errors="replace")
        except OSError as exc:
            logger.error(
                "example_text_unreadable document_id=%s error_type=%s", row["id"], type(exc).__name__
            )
            continue
        if not body.strip():
            continue
        examples.append(
            PromptExample(
                document_id=str(row["id"]),
                text=_truncate(body, EXAMPLE_TEXT_CHAR_BUDGET),
                extraction=example_extraction(row["approved_json"]),
            )
        )
    return examples


def routing_content(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    The header-only view of a document for the routing call: text cut to
    ROUTING_TEXT_CHAR_BUDGET characters in total; a page read visually goes as
    it is (it can't be cut). Same <document> fence as extraction (7.2).
    """
    budget = ROUTING_TEXT_CHAR_BUDGET
    kept: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") == "text":
            if budget <= 0:
                continue
            kept.append({"type": "text", "text": part["text"][:budget]})
            budget -= len(part["text"])
        else:
            kept.append(part)
    if len(kept) == 1 and kept[0]["type"] == "text":
        return build_text_content(kept[0]["text"])
    return wrap_document_content(kept)


# ── The whole decision ──────────────────────────────────────────────────────


SessionFactory = Callable[[UUID], AbstractContextManager[Session]]


def plan(
    client: Any,
    tenant_id: UUID,
    document_id: UUID,
    *,
    sender_email: str | None,
    parts: list[dict[str, Any]],
    session_factory: SessionFactory,
    read: Callable[[str], bytes] = read_file,
) -> ExamplePlan:
    """
    Decide whether this document gets examples, and which. Transactions are
    short and never held across the routing call. The caller treats any
    exception as "no examples": the feature must never cost a document its
    extraction.
    """
    with session_factory(tenant_id) as session:
        if not is_enabled(session, tenant_id):
            return ExamplePlan(outcome="flag_off")
        from_sender = buyer_from_sender(session, tenant_id, sender_email)
        worth_routing = from_sender is None and any_buyer_qualifies(session, tenant_id)

    routing: RoutingResult | None = None
    buyer_id: UUID | None = None
    identified_by: str | None = None
    if from_sender is not None:
        buyer_id, identified_by = from_sender
    elif worth_routing:
        routing = read_buyer_header(client, routing_content(parts))
        with session_factory(tenant_id) as session:
            buyer_id = buyer_from_routing(session, tenant_id, routing)
        identified_by = "header_read" if buyer_id is not None else None

    if buyer_id is None:
        outcome = "routing_injection" if routing is not None and routing.injection_suspected else "no_buyer"
        return ExamplePlan(routing=routing, outcome=outcome)

    with session_factory(tenant_id) as session:
        if approved_count(session, tenant_id, buyer_id) < EXAMPLE_MIN_APPROVED_DOCS:
            return ExamplePlan(
                buyer_id=buyer_id, identified_by=identified_by, routing=routing, outcome="below_threshold"
            )
        examples = select_examples(session, tenant_id, buyer_id, exclude_document_id=document_id, read=read)

    return ExamplePlan(
        examples=examples,
        buyer_id=buyer_id,
        identified_by=identified_by,
        routing=routing,
        outcome="examples_used" if examples else "no_usable_examples",
    )


# ── Console: what the founder sees before switching it on ──────────────────


def buyer_eligibility(session: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    """Every buyer with at least one approved order: how many, how many have
    text DocFlow could show, and whether the buyer qualifies."""
    rows = session.execute(
        text(
            f"""
            SELECT b.id, b.name,
                   count(*) AS approved,
                   count(*) FILTER (WHERE d.extracted_text_path IS NOT NULL) AS with_text
            FROM documents d
            JOIN document_headers h ON h.document_id = d.id
            JOIN buyers b ON b.id = h.buyer_id AND b.deleted_at IS NULL
            WHERE d.tenant_id = :t AND h.tenant_id = :t
              AND d.status IN {_APPROVED} AND d.deleted_at IS NULL
              AND d.approved_json IS NOT NULL
            GROUP BY b.id, b.name
            ORDER BY count(*) DESC, b.name
            """
        ),
        {"t": str(tenant_id)},
    ).mappings().all()
    return [
        {
            "buyer_id": str(row["id"]),
            "name": row["name"],
            "approved": int(row["approved"]),
            "with_text": int(row["with_text"]),
            "qualifies": int(row["approved"]) >= EXAMPLE_MIN_APPROVED_DOCS and int(row["with_text"]) > 0,
        }
        for row in rows
    ]


def month_usage(session: Session, tenant_id: UUID) -> dict[str, Any]:
    """This calendar month (UTC): orders read with examples, the example
    tokens, and what the routing calls cost -- the feature's own bill."""
    row = session.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE run_kind = 'extraction' AND jsonb_array_length(examples_used) > 0)
                AS runs_with_examples,
              coalesce(sum(example_input_tokens) FILTER (WHERE run_kind = 'extraction'), 0)
                AS example_input_tokens,
              count(*) FILTER (WHERE run_kind = 'buyer_routing') AS routing_runs,
              coalesce(sum(est_cost_usd) FILTER (WHERE run_kind = 'buyer_routing'), 0)
                AS routing_cost_usd
            FROM extraction_runs
            WHERE tenant_id = :t AND created_at >= date_trunc('month', now())
            """
        ),
        {"t": str(tenant_id)},
    ).mappings().one()
    return {
        "runs_with_examples": int(row["runs_with_examples"]),
        "example_input_tokens": int(row["example_input_tokens"]),
        "example_cost_usd": str(
            extraction_input_cost(int(row["example_input_tokens"])).quantize(Decimal("0.0001"))
        ),
        "routing_runs": int(row["routing_runs"]),
        "routing_cost_usd": str(row["routing_cost_usd"]),
    }



# ── Console: the switch (Section 7.13, "Activation gate") ───────────────────


class ExamplePromptingError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def overview(session: Session, tenant_id: UUID) -> dict[str, Any]:
    return {
        "enabled": is_enabled(session, tenant_id),
        "min_approved": EXAMPLE_MIN_APPROVED_DOCS,
        "max_examples": EXAMPLE_MAX_PER_PROMPT,
        "buyers": buyer_eligibility(session, tenant_id),
        "this_month": month_usage(session, tenant_id),
    }


def set_enabled(
    session: Session,
    tenant_id: UUID,
    *,
    enabled: bool,
    golden_run_confirmed: bool,
    actor_user_id: UUID,
) -> dict[str, Any]:
    """
    The founder's switch. Turning it on requires confirming that a live
    golden run with examples passed (7.13: "The founder turns it on per
    tenant ... after a live golden run passes with the feature enabled").
    Turning it off never needs anything -- the kill switch takes effect for
    the next document, and nothing in flight depends on it.
    """
    if enabled and not golden_run_confirmed:
        raise ExamplePromptingError("EXM-001")
    was = is_enabled(session, tenant_id)
    session.execute(
        text("UPDATE tenants SET example_prompting_enabled = :on, updated_at = now() WHERE id = :t"),
        {"t": str(tenant_id), "on": enabled},
    )
    if was != enabled:
        _lifecycle_event(
            session,
            tenant_id,
            "example_prompting_enabled" if enabled else "example_prompting_disabled",
            actor_user_id=actor_user_id,
            payload={"golden_run_confirmed": golden_run_confirmed} if enabled else {},
            constants=constants_in_effect(
                "EXAMPLE_MIN_APPROVED_DOCS", "EXAMPLE_MAX_PER_PROMPT", "EXAMPLE_TEXT_CHAR_BUDGET",
                "ROUTING_MIN_CONFIDENCE",
            ),
        )
    return overview(session, tenant_id)
