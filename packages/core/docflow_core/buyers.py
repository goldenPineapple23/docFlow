"""
Buyer identification and auto-creation (CLAUDE.md Section 7.6).

The rule this module implements, in full:

    "Buyer auto-creation: on a new buyer name, create the record and flag any
     near-duplicate existing names for founder merge. Never auto-merge.
     Merging is a founder action that re-points foreign keys in a
     transaction and is logged."

So there are exactly three outcomes for a document, and no fourth:

  1. The extracted buyer identifies an existing buyer -> the document's
     header is linked to it. Nothing is created. "Identifies" means its
     contact email, its name, or a `buyer_alias` rule a founder merge left
     behind (D-119) -- so a merged-away name links to the kept buyer.
  2. The extracted buyer is new -> a `buyers` row is created, linked, and
     every existing buyer whose name is a near-duplicate is *flagged* in
     `buyer_merge_candidates`. Both rows stay independent and fully usable;
     nothing is merged, nothing is blocked, and the flag is a suggestion for
     a human (Section 7.6: "Fuzzy matches always surface candidate + score",
     Section 10: "never auto-merge buyers").
  3. Extraction returned no buyer name -> the document has no buyer link.
     That is a legitimate outcome, not an error: every field in the
     extraction schema is nullable, and inventing a buyer from nothing is
     exactly what Section 7.1 forbids.

Two distinct normalizations, deliberately (see DECISIONS.md D-054):

  * `normalize_buyer_name` produces the **match key** -- what decides
    "is this the same buyer we already have". It only removes noise that
    carries no meaning (case, punctuation, whitespace). It does NOT strip
    legal suffixes, because "Acme Test Inc" and "Acme Test LLC" can be two
    genuinely different legal entities. Collapsing them here would be a
    silent auto-merge, which is forbidden.
  * `buyer_similarity_key` produces the **comparison key** -- used only for
    near-duplicate *scoring*. It additionally drops trailing legal suffixes,
    so a suffix-only difference scores as a near-duplicate and gets flagged
    for a human, which is the correct handling of exactly that case.

Every function that touches the database takes an already-open, tenant-scoped
`Session` from `docflow_core.db.tenant_session()` -- this module never opens
its own connection and never accepts a tenant_id from request data
(Section 7.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from rapidfuzz import fuzz
from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.db import rowcount

# Above this, two buyer names are flagged as a possible duplicate for the
# founder to decide on. It is a *flagging* threshold, never an auto-merge
# threshold -- there is no score at which this module merges anything.
# Measured against real-shaped name pairs before being chosen; see
# DECISIONS.md D-055 for the numbers and why token_sort_ratio.
NEAR_DUPLICATE_THRESHOLD = Decimal("0.88")

# Trailing legal/entity suffixes dropped from the *comparison* key only.
# Kept deliberately short: an over-aggressive list creates false merge
# candidates, and every entry here is a word that carries no distinguishing
# meaning at the end of a company name.
_LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "lp",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "gmbh",
        "bv",
        "nv",
        "sa",
        "ag",
        "pty",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Apostrophes are removed rather than turned into a space: "Bella's" and
# "Bellas" are the same word written two ways, while "bella s" is a
# different token sequence that would score lower against both.
_APOSTROPHES = re.compile(r"['’ʼ`]")


def normalize_buyer_name(name: str | None) -> str:
    """
    The match key: lowercase, apostrophes dropped, every run of remaining
    non-alphanumeric characters becomes a single space, leading/trailing
    space removed. "Bella's Coffee House, LLC." and "BELLAS COFFEE HOUSE
    LLC" both become "bellas coffee house llc" -- the same buyer written two
    ways.

    Returns "" for a missing or whitespace-only name; callers treat that as
    "no buyer", never as a buyer named "".
    """
    if not name:
        return ""
    return _NON_ALNUM.sub(" ", _APOSTROPHES.sub("", name.lower())).strip()


def buyer_similarity_key(name: str | None) -> str:
    """
    The comparison key: the match key with trailing legal suffixes removed,
    used only for near-duplicate scoring. If a name is *nothing but*
    suffixes ("LLC"), the match key is returned unchanged rather than an
    empty string -- comparing empty strings would score every such name as
    identical to every other.
    """
    tokens = normalize_buyer_name(name).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    if not tokens:
        return normalize_buyer_name(name)
    return " ".join(tokens)


def name_similarity(left: str | None, right: str | None) -> Decimal:
    """
    0.0000-1.0000 similarity between two buyer names, as a Decimal (this
    value is stored in a NUMERIC column and shown to the founder; no float
    reaches the database -- Section 7.1).

    `token_sort_ratio` makes word order irrelevant ("Bella's Coffee House"
    vs "Coffee House, Bella's") while still penalizing a changed word, which
    is what distinguishes a spelling variant of one company from two
    different companies that share a word.
    """
    left_key = buyer_similarity_key(left)
    right_key = buyer_similarity_key(right)
    if not left_key or not right_key:
        return Decimal("0.0000")
    score = fuzz.token_sort_ratio(left_key, right_key) / 100
    return Decimal(repr(score)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def is_near_duplicate(left: str | None, right: str | None) -> bool:
    """True if these two names should be flagged for a founder to look at."""
    return name_similarity(left, right) >= NEAR_DUPLICATE_THRESHOLD


@dataclass(frozen=True)
class MergeCandidate:
    """One flagged pair. A suggestion for a human -- never applied."""

    existing_buyer_id: UUID
    similarity_score: Decimal


@dataclass(frozen=True)
class BuyerIdentification:
    buyer_id: UUID | None
    created: bool
    # "contact_email" | "normalized_name" | "buyer_alias" | None -- how the
    # existing buyer was recognized, recorded so a reviewer can be shown why
    # this link exists.
    matched_on: str | None = None
    merge_candidates: list[MergeCandidate] = field(default_factory=list)
    # The `buyer_alias` rule that made the link, when one did (Section 7.13:
    # every rule that fires is recorded as provenance).
    rule_id: UUID | None = None


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def find_buyer_by_email(session: Session, tenant_id: UUID, contact_email: str) -> UUID | None:
    """
    An exact contact-email match is stronger evidence of identity than a name
    match: a buyer's ordering contact is a unique string, while two different
    companies routinely share a name fragment (DECISIONS.md D-056). Compared
    case-insensitively because an imported customer list may carry addresses
    in any case.
    """
    row = session.execute(
        text(
            "SELECT id FROM buyers "
            "WHERE tenant_id = :tenant_id AND lower(contact_email) = :email "
            "AND deleted_at IS NULL "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "email": contact_email},
    ).mappings().first()
    return UUID(str(row["id"])) if row else None


def find_buyer_by_normalized_name(session: Session, tenant_id: UUID, normalized_name: str) -> UUID | None:
    row = session.execute(
        text(
            "SELECT id FROM buyers "
            "WHERE tenant_id = :tenant_id AND normalized_name = :normalized_name "
            "AND deleted_at IS NULL "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "normalized_name": normalized_name},
    ).mappings().first()
    return UUID(str(row["id"])) if row else None


def find_buyer_by_alias(session: Session, normalized_name: str) -> tuple[UUID, UUID] | None:
    """
    A live buyer that a human merge said this name belongs to: the active
    `buyer_alias` rule with this match key (D-119). Returns (buyer_id,
    rule_id). Only ever created by a founder's merge -- never learned on its
    own (Section 7.13).
    """
    row = session.execute(
        text(
            """
            SELECT r.id AS rule_id, r.buyer_id
            FROM learned_rules r
            JOIN buyers b ON b.id = r.buyer_id AND b.deleted_at IS NULL
            WHERE r.rule_type = 'buyer_alias' AND r.match_key = :key
              AND r.status = 'active' AND r.deleted_at IS NULL
            ORDER BY r.created_at DESC LIMIT 1
            """
        ),
        {"key": normalized_name},
    ).mappings().first()
    return (UUID(str(row["buyer_id"])), UUID(str(row["rule_id"]))) if row else None


def _existing_buyer_names(session: Session, tenant_id: UUID, exclude_id: UUID) -> list[tuple[UUID, str]]:
    rows = session.execute(
        text(
            "SELECT id, name FROM buyers "
            "WHERE tenant_id = :tenant_id AND deleted_at IS NULL AND id <> :exclude_id"
        ),
        {"tenant_id": str(tenant_id), "exclude_id": str(exclude_id)},
    ).mappings().all()
    return [(UUID(str(row["id"])), row["name"]) for row in rows]


def find_near_duplicate_candidates(name: str, existing: list[tuple[UUID, str]]) -> list[MergeCandidate]:
    """
    Pure scoring step, separated from the database so the threshold behavior
    is unit-testable without one. Highest score first, so the founder sees
    the most likely duplicate at the top.
    """
    candidates = [
        MergeCandidate(existing_buyer_id=buyer_id, similarity_score=name_similarity(name, other_name))
        for buyer_id, other_name in existing
    ]
    flagged = [c for c in candidates if c.similarity_score >= NEAR_DUPLICATE_THRESHOLD]
    return sorted(flagged, key=lambda c: c.similarity_score, reverse=True)


def _flag_merge_candidates(
    session: Session,
    tenant_id: UUID,
    new_buyer_id: UUID,
    candidates: list[MergeCandidate],
    *,
    document_id: UUID | None,
) -> None:
    for candidate in candidates:
        session.execute(
            text(
                """
                INSERT INTO buyer_merge_candidates
                    (id, tenant_id, buyer_id, existing_buyer_id, similarity_score,
                     status, detected_from_document_id, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :buyer_id, :existing_buyer_id, :similarity_score,
                     'open', :document_id, now(), now())
                ON CONFLICT (tenant_id, buyer_id, existing_buyer_id) WHERE deleted_at IS NULL
                DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "buyer_id": str(new_buyer_id),
                "existing_buyer_id": str(candidate.existing_buyer_id),
                # str(), not float(): the driver binds this to NUMERIC exactly.
                "similarity_score": str(candidate.similarity_score),
                "document_id": str(document_id) if document_id else None,
            },
        )


def _create_buyer(
    session: Session,
    tenant_id: UUID,
    *,
    name: str,
    normalized_name: str,
    contact_email: str | None,
    document_id: UUID | None,
) -> tuple[UUID, bool]:
    """
    Returns (buyer_id, created). ON CONFLICT DO NOTHING against the partial
    unique index on (tenant_id, normalized_name): if two documents from the
    same brand-new buyer are processed concurrently, the loser re-reads the
    winner's row instead of raising or creating a second buyer. `name` is
    stored exactly as the document had it -- normalization is a derived key,
    never a correction to what was extracted (Section 7.1).
    """
    buyer_id = uuid4()
    result = session.execute(
        text(
            """
            INSERT INTO buyers
                (id, tenant_id, name, normalized_name, contact_email,
                 created_from_document_id, created_at, updated_at)
            VALUES
                (:id, :tenant_id, :name, :normalized_name, :contact_email,
                 :document_id, now(), now())
            ON CONFLICT (tenant_id, normalized_name) WHERE deleted_at IS NULL
            DO NOTHING
            """
        ),
        {
            "id": str(buyer_id),
            "tenant_id": str(tenant_id),
            "name": name,
            "normalized_name": normalized_name,
            "contact_email": contact_email or None,
            "document_id": str(document_id) if document_id else None,
        },
    )
    if rowcount(result) == 1:
        return buyer_id, True

    existing = find_buyer_by_normalized_name(session, tenant_id, normalized_name)
    if existing is None:  # pragma: no cover -- only reachable on a concurrent soft-delete
        raise RuntimeError("Buyer insert conflicted but no live row could be re-read.")
    return existing, False


def identify_or_create_buyer(
    session: Session,
    tenant_id: UUID,
    *,
    buyer_name: str | None,
    buyer_contact_email: str | None = None,
    document_id: UUID | None = None,
) -> BuyerIdentification:
    """
    The Section 7.6 decision, with no side effect beyond creating the buyer
    and flagging candidates. `session` must already be tenant-scoped.
    """
    email = _normalize_email(buyer_contact_email)
    normalized_name = normalize_buyer_name(buyer_name)

    if email:
        matched = find_buyer_by_email(session, tenant_id, email)
        if matched is not None:
            return BuyerIdentification(buyer_id=matched, created=False, matched_on="contact_email")

    if not normalized_name:
        # No name and no email match: there is no buyer to link, and there is
        # certainly no buyer to invent. Not an error.
        return BuyerIdentification(buyer_id=None, created=False)

    matched = find_buyer_by_normalized_name(session, tenant_id, normalized_name)
    if matched is not None:
        if email:
            # Fill in a contact address we didn't have. Never overwrites an
            # existing value -- a stored address may have been set by a human
            # (Section 10: "never overwrite a human correction with a machine
            # value"), and this is a machine value.
            session.execute(
                text(
                    "UPDATE buyers SET contact_email = :email, updated_at = now() "
                    "WHERE id = :id AND contact_email IS NULL"
                ),
                {"id": str(matched), "email": email},
            )
        return BuyerIdentification(buyer_id=matched, created=False, matched_on="normalized_name")

    alias = find_buyer_by_alias(session, normalized_name)
    if alias is not None:
        aliased_buyer, rule_id = alias
        session.execute(
            text(
                "UPDATE learned_rules SET times_applied = times_applied + 1, updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": str(rule_id)},
        )
        return BuyerIdentification(
            buyer_id=aliased_buyer, created=False, matched_on="buyer_alias", rule_id=rule_id
        )

    # A non-empty normalized_name implies a non-empty raw name; `or ""` is
    # here for the type checker, not because the branch is reachable.
    buyer_id, created = _create_buyer(
        session,
        tenant_id,
        name=(buyer_name or "").strip(),
        normalized_name=normalized_name,
        contact_email=email or None,
        document_id=document_id,
    )
    if not created:
        return BuyerIdentification(buyer_id=buyer_id, created=False, matched_on="normalized_name")

    candidates = find_near_duplicate_candidates(
        buyer_name or "", _existing_buyer_names(session, tenant_id, buyer_id)
    )
    _flag_merge_candidates(session, tenant_id, buyer_id, candidates, document_id=document_id)
    return BuyerIdentification(buyer_id=buyer_id, created=True, merge_candidates=candidates)


def identify_and_link_buyer(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    buyer_name: str | None,
    buyer_contact_email: str | None = None,
) -> BuyerIdentification:
    """
    `identify_or_create_buyer` plus writing the link onto the document's
    header row. A document whose extraction found no buyer keeps
    `document_headers.buyer_id IS NULL` and no write happens at all.
    """
    result = identify_or_create_buyer(
        session,
        tenant_id,
        buyer_name=buyer_name,
        buyer_contact_email=buyer_contact_email,
        document_id=document_id,
    )
    if result.buyer_id is None:
        return result

    if result.rule_id is not None:
        # The link came from a learned rule: say so on the field, so the
        # reviewer can see why this buyer is there (Section 7.13).
        session.execute(
            text(
                "UPDATE document_headers SET buyer_id = :buyer_id, "
                "field_provenance = field_provenance || jsonb_build_object('buyer_id', CAST(:prov AS text)), "
                "updated_at = now() WHERE document_id = :document_id"
            ),
            {
                "buyer_id": str(result.buyer_id),
                "document_id": str(document_id),
                "prov": f"learned_rule:{result.rule_id}",
            },
        )
        return result
    session.execute(
        text(
            "UPDATE document_headers SET buyer_id = :buyer_id, updated_at = now() "
            "WHERE document_id = :document_id"
        ),
        {"buyer_id": str(result.buyer_id), "document_id": str(document_id)},
    )
    return result
