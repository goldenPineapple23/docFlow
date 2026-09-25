"""
Catalog (SKU) matching -- the deterministic step that runs *after* the model,
never inside it (CLAUDE.md Section 7.1: "Matching to the catalog is a
separate, deterministic step -- the model never 'helpfully' substitutes a
catalog SKU for what the document says").

Section 7.6 is the whole specification, and it is short:

    "Fuzzy matches always surface candidate + score; below threshold they are
     suggestions, never auto-applied."
    "A learned mapping is created only from a human confirmation, scoped to
     (tenant_id, customer_id, raw_description). Applying a learned mapping
     raises confidence to a high fixed value and records the mapping ID as
     provenance."
    "Never silently 'normalize' units of measure or quantities. Suggest,
     flag, let the human decide."

Three steps, in this order, first hit wins:

  1. **Learned rule.** An active `sku_mapping` rule a human confirmed, keyed
     on the line's description. A buyer-scoped rule beats a tenant-wide one
     (D-065). Score is the fixed `LEARNED_RULE_MATCH_SCORE`; provenance is
     `learned_rule:<id>`.
  2. **Exact SKU.** The printed SKU, normalized only for insignificant
     characters (case, padding, invisible characters), equals a live catalog
     SKU. Provenance `mapped:exact_sku`.
  3. **Fuzzy.** Similarity against catalog descriptions and SKUs. At or above
     `FUZZY_MATCH_THRESHOLD`, *and* past the measure guard and the ambiguity
     guard below, it is applied with provenance `mapped:fuzzy`. Otherwise
     the line stays unmatched and every candidate is recorded with its score.

An unmatched line is a correct, expected outcome that a reviewer resolves --
not an error, and never a reason to fail a document.

Nothing in this module rewrites a value the model extracted. Every
normalization here produces a *derived key* used for comparison only, exactly
as `buyers.normalize_buyer_name` does (DECISIONS.md D-054).

Every function that touches the database takes an already-open, tenant-scoped
`Session` from `docflow_core.db.tenant_session()` -- this module never opens
its own connection and never accepts a tenant_id from request data
(Section 7.5). A learned rule is therefore only ever read inside the tenant
context that owns it; there is no query here that could see another tenant's
catalog or rules (Section 7.13: "No learning crosses a tenant boundary").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from rapidfuzz import fuzz
from sqlalchemy import text
from sqlalchemy.orm import Session

# ── Thresholds and fixed scores ──────────────────────────────────────────────
# Every number below was measured against real-shaped product descriptions
# before it was chosen; the table is in DECISIONS.md D-066. They are Decimals
# because they are compared against, and stored in, NUMERIC columns.

# CLAUDE.md Section 7.6: "Applying a learned mapping raises confidence to a
# high fixed value." Deliberately not 1.0000 -- that is reserved for "the
# printed string and the catalog string are the same string". A human-
# confirmed mapping is the strongest evidence this system has about *meaning*,
# but it is still a person's assertion about a buyer's wording, and keeping
# the two distinguishable costs nothing (D-064).
LEARNED_RULE_MATCH_SCORE = Decimal("0.9900")

# An exact SKU match is an identity, not an estimate.
EXACT_MATCH_SCORE = Decimal("1.0000")

# A reviewer's own choice. Recorded so a re-run can never quietly replace it.
HUMAN_CONFIRMED_MATCH_SCORE = Decimal("1.0000")

# At or above this, a fuzzy candidate MAY be applied -- if it also clears the
# measure guard and the ambiguity guard. Below it, the candidate is recorded
# as a suggestion and the line stays unmatched. Measured gap: the worst
# genuine variant scored 0.92, the best genuinely-different pair that the
# measure guard does not already catch scored 0.86 (D-066).
FUZZY_MATCH_THRESHOLD = Decimal("0.9000")

# If the runner-up is within this of the winner, nothing is applied. A
# catalog that contains "duplicate description with different SKUs" is
# explicitly expected (Section 7.15.2 Step 4), and picking one of two
# equally-good items by sort order would be an invented answer. Deliberately
# narrow: this guards near-ties, not close-but-clear calls.
FUZZY_AMBIGUITY_MARGIN = Decimal("0.0200")

# How many scored alternatives are recorded on the line for the reviewer.
# Five covers a typical size/pack family without turning one review row into
# a scrolling list.
MAX_CANDIDATES = 5

PROVENANCE_EXACT = "mapped:exact_sku"
PROVENANCE_FUZZY = "mapped:fuzzy"
PROVENANCE_HUMAN = "human_edit"

# The provenance key on `document_lines.field_provenance` that matching owns.
# It is not an extracted field -- it is a conclusion about the line -- so it
# gets its own key rather than overloading `sku` (D-067).
PROVENANCE_FIELD_MATCH = "matched_item_id"
PROVENANCE_FIELD_UOM = "matched_uom"


# ── Normalization: derived comparison keys, never corrections ────────────────

_APOSTROPHES = re.compile(r"['’ʼ`]")
# Invisible characters that catalog exports carry and nobody can see:
# zero-width space/non-joiner/joiner, BOM, and the non-breaking space.
_INVISIBLE = re.compile(r"[​‌‍﻿]")
_NBSP = re.compile(r"[   ]")
_WHITESPACE = re.compile(r"\s+")
# Everything that is not a letter, a digit or a period becomes a space. The
# period survives this pass so decimal measures ("1.5lb") stay one token; a
# period that is not between two digits is dropped immediately afterwards.
_DESC_NON_ALNUM = re.compile(r"[^a-z0-9.]+")
_DESC_STRAY_PERIOD = re.compile(r"(?<!\d)\.|\.(?!\d)")
# "5 lb" and "5lb" are the same measure written two ways. Only short alpha
# tokens are glued on, so a unit ("lb", "oz", "ml", "ct") joins the number
# while a real word ("vanilla", "boxes") does not.
_MEASURE_GLUE = re.compile(r"(?<=\d) +(?=[a-z]{1,4}(?![a-z]))")
_MEASURE = re.compile(r"(\d+(?:\.\d+)?) *([a-z]*)")
_SKU_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_description(value: str | None) -> str:
    """
    The description comparison key: lowercase, apostrophes dropped,
    punctuation to spaces, whitespace collapsed, and a measure written with a
    space ("5 lb") joined up to match the same measure written without one
    ("5lb").

    "Colombian Whole Bean, 5 lb." and "COLOMBIAN WHOLE BEAN 5LB" both become
    "colombian whole bean 5lb". The stored description on the line is
    untouched -- this string exists only to be compared.
    """
    if not value:
        return ""
    lowered = _APOSTROPHES.sub("", _INVISIBLE.sub("", _NBSP.sub(" ", value.lower())))
    spaced = _DESC_NON_ALNUM.sub(" ", lowered)
    without_periods = _DESC_STRAY_PERIOD.sub("", spaced)
    collapsed = _WHITESPACE.sub(" ", without_periods).strip()
    return _MEASURE_GLUE.sub("", collapsed)


def normalize_sku(value: str | None) -> str:
    """
    The **exact-match** key for a SKU. It removes only what cannot carry
    meaning: surrounding padding, invisible characters, and letter case. It
    deliberately keeps punctuation, so "CF-1001" and "CF1001" are NOT the
    same SKU here -- in a real catalog they can be two different products,
    and collapsing them at the identity layer would be inventing a match.

    (Section 7.15.2 Step 4 notes that catalogs routinely contain "leading/
    trailing whitespace and invisible characters in SKUs". Trimming them here
    is not a correction to the catalog -- the import screen that fixes the
    stored value is Phase 5 -- it is this module refusing to be fooled by
    them in the meantime.)
    """
    if not value:
        return ""
    cleaned = _INVISIBLE.sub("", _NBSP.sub(" ", value))
    return _WHITESPACE.sub(" ", cleaned).strip().upper()


def sku_comparison_key(value: str | None) -> str:
    """
    The **similarity** key for a SKU: `normalize_sku` with every remaining
    non-alphanumeric character removed, so "CF-1001", "CF 1001" and "CF1001"
    compare as one string. Used only for scoring -- a hit here is still a
    fuzzy candidate that has to clear every guard below, never an identity.
    """
    return _SKU_NON_ALNUM.sub("", normalize_sku(value).lower())


def measure_signature(value: str | None) -> tuple[tuple[str, str], ...]:
    """
    Every number-with-unit in a string, sorted: "12oz Paper Cups (1000ct)" ->
    (("1000", "ct"), ("12", "oz")).

    This exists because string similarity is blind to exactly the character
    that distinguishes two products. Measured on the real implementation:
    "Colombian Whole Bean 5lb" scores 0.96 against "Colombian Whole Bean 2lb"
    (a different SKU) and 0.96 against "Columbian Whole Bean 5lb" (the same
    SKU, misspelled). No threshold can separate those two, so the numbers are
    compared separately and exactly -- see D-066.
    """
    if not value:
        return ()
    return tuple(sorted((m.group(1), m.group(2)) for m in _MEASURE.finditer(normalize_description(value))))


def measures_agree(left: str | None, right: str | None) -> bool:
    """
    True when two strings carry the same measures. A pair that fails this can
    still be surfaced as a candidate with its real score -- it simply can
    never be auto-applied, whatever it scores.
    """
    return measure_signature(left) == measure_signature(right)


def _as_score(raw: float) -> Decimal:
    """rapidfuzz returns 0-100 as a float; every score this module stores or
    compares is a 4-dp Decimal (Section 7.1 -- no float reaches a NUMERIC
    column)."""
    return Decimal(repr(raw / 100)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def description_similarity(left: str | None, right: str | None) -> Decimal:
    """
    `token_sort_ratio` over the description keys: word order is noise on a
    purchase order ("Syrup, Vanilla 750ml" is "Vanilla Syrup 750ml"), but a
    changed or added word is signal.

    `token_set_ratio` is rejected here for a stronger version of the reason
    D-055 rejected it for buyer names: it scores "Colombian Whole Bean 5lb"
    against "Colombian Whole Bean 5lb Organic" at 1.00, and those are two
    different SKUs that will ship two different products.
    """
    left_key = normalize_description(left)
    right_key = normalize_description(right)
    if not left_key or not right_key:
        return Decimal("0.0000")
    return _as_score(fuzz.token_sort_ratio(left_key, right_key))


def sku_similarity(left: str | None, right: str | None) -> Decimal:
    """
    Plain `ratio` over the SKU comparison keys. A SKU is one token whose
    character *order* is the whole meaning, so the token-reordering scorers
    used for descriptions would be wrong here.
    """
    left_key = sku_comparison_key(left)
    right_key = sku_comparison_key(right)
    if not left_key or not right_key:
        return Decimal("0.0000")
    return _as_score(fuzz.ratio(left_key, right_key))


# ── Catalog ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogItem:
    item_id: UUID
    sku: str
    description: str | None
    unit_of_measure: str | None


class Catalog:
    """
    One tenant's live catalog, loaded once per document rather than once per
    line. Holds the exact-SKU index and knows which normalized SKUs are
    ambiguous.

    Ambiguity is real: the partial unique index on `items` is on the *raw*
    SKU, so a catalog carrying both "CF-1001" and "cf-1001 " (padded) has two
    live rows that normalize to one key. That is not an exact match to
    anything -- it is a question for a human.
    """

    def __init__(self, items: list[CatalogItem]):
        self.items = items
        self._by_id = {item.item_id: item for item in items}
        by_sku: dict[str, list[CatalogItem]] = {}
        for item in items:
            key = normalize_sku(item.sku)
            if key:
                by_sku.setdefault(key, []).append(item)
        self._by_sku = by_sku

    def get(self, item_id: UUID) -> CatalogItem | None:
        return self._by_id.get(item_id)

    def exact_sku(self, raw_sku: str | None) -> CatalogItem | None:
        """The one live item with this SKU, or None -- including when more
        than one live item shares the normalized SKU, which is ambiguous and
        therefore not an exact match."""
        matches = self._by_sku.get(normalize_sku(raw_sku), [])
        return matches[0] if len(matches) == 1 else None

    def ambiguous_sku(self, raw_sku: str | None) -> list[CatalogItem]:
        matches = self._by_sku.get(normalize_sku(raw_sku), [])
        return matches if len(matches) > 1 else []

    def __len__(self) -> int:
        return len(self.items)


# ── Match results ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MatchCandidate:
    """One scored alternative shown to the reviewer. `eligible` is False when
    a guard disqualified it from ever being auto-applied; it is still shown,
    with its real score and the reason."""

    item_id: UUID
    sku: str
    description: str | None
    score: Decimal
    matched_on: str  # "description" | "sku"
    eligible: bool = True
    blocked_reason: str | None = None  # "measure_mismatch" | None

    def as_json(self) -> dict:
        return {
            "item_id": str(self.item_id),
            "sku": self.sku,
            "description": self.description,
            # String in JSON transport, never a float (Section 7.1).
            "score": str(self.score),
            "matched_on": self.matched_on,
            "eligible": self.eligible,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class LineMatch:
    item_id: UUID | None = None
    method: str | None = None  # 'learned_rule' | 'exact_sku' | 'fuzzy'
    score: Decimal | None = None
    provenance: str | None = None
    learned_rule_id: UUID | None = None
    candidates: list[MatchCandidate] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.item_id is not None


@dataclass(frozen=True)
class SkuRule:
    """An active `sku_mapping` learned rule, already resolved to a live
    catalog item."""

    rule_id: UUID
    buyer_scoped: bool
    item_id: UUID


@dataclass(frozen=True)
class UomRule:
    rule_id: UUID
    buyer_scoped: bool
    unit_of_measure: str


@dataclass
class MatchingSummary:
    lines_considered: int = 0
    lines_matched: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    skipped_human_edited: int = 0


# ── The pure resolution step ────────────────────────────────────────────────


def score_candidates(
    raw_sku: str | None, raw_description: str | None, catalog: Catalog
) -> list[MatchCandidate]:
    """
    Every catalog item scored against this line, best first, truncated to
    `MAX_CANDIDATES`. Each item contributes its better of (description score,
    SKU score); the measure guard is evaluated on whichever field won, so the
    reason attached to a blocked candidate is the reason for the score shown.
    """
    scored: list[MatchCandidate] = []
    for item in catalog.items:
        desc_score = description_similarity(raw_description, item.description)
        sku_score = sku_similarity(raw_sku, item.sku)
        left: str | None
        right: str | None
        if sku_score > desc_score:
            score, matched_on, left, right = sku_score, "sku", raw_sku, item.sku
        else:
            score, matched_on, left, right = desc_score, "description", raw_description, item.description
        if score <= 0:
            continue
        agree = measures_agree(left, right)
        scored.append(
            MatchCandidate(
                item_id=item.item_id,
                sku=item.sku,
                description=item.description,
                score=score,
                matched_on=matched_on,
                eligible=agree,
                blocked_reason=None if agree else "measure_mismatch",
            )
        )
    # Deterministic order: score desc, then SKU, so the same input always
    # produces the same stored candidate list.
    scored.sort(key=lambda c: (-c.score, c.sku))
    return scored[:MAX_CANDIDATES]


def _fuzzy_match(candidates: list[MatchCandidate]) -> MatchCandidate | None:
    """
    The winner, or None. Three conditions, all of which must hold -- this is
    the function Section 10's "never auto-apply a sub-threshold match" lives
    in:

      * the best candidate is at or above `FUZZY_MATCH_THRESHOLD`;
      * it cleared the measure guard;
      * the runner-up is more than `FUZZY_AMBIGUITY_MARGIN` behind it.

    The runner-up test counts blocked candidates too. A disqualified
    near-neighbour scoring just as well means the catalog holds a confusable
    sibling, which is precisely when a person should look.
    """
    if not candidates:
        return None
    best = candidates[0]
    if not best.eligible or best.score < FUZZY_MATCH_THRESHOLD:
        return None
    if len(candidates) > 1 and best.score - candidates[1].score <= FUZZY_AMBIGUITY_MARGIN:
        return None
    return best


def resolve_line(
    *,
    raw_sku: str | None,
    raw_description: str | None,
    catalog: Catalog,
    sku_rules: dict[str, SkuRule],
) -> LineMatch:
    """
    The Section 7.6 pipeline for one line, with no database access and no
    side effects, so the precedence and the thresholds are testable without
    a connection. `sku_rules` is keyed by `normalize_description`.
    """
    # 1. Learned rule -- a human already answered this exact question.
    rule = sku_rules.get(normalize_description(raw_description))
    if rule is not None and catalog.get(rule.item_id) is not None:
        return LineMatch(
            item_id=rule.item_id,
            method="learned_rule",
            score=LEARNED_RULE_MATCH_SCORE,
            provenance=f"learned_rule:{rule.rule_id}",
            learned_rule_id=rule.rule_id,
        )
    # A rule pointing at an item that has since been retired is not applied;
    # the line falls through to the steps below and is surfaced normally.
    # (Section 10 requires the *import* to flag this before it happens --
    # that is the Phase 5 catalog screen. Here we simply never apply a rule
    # whose target no longer exists.)

    # 2. Exact SKU -- an identity, not an estimate.
    exact = catalog.exact_sku(raw_sku)
    if exact is not None:
        return LineMatch(
            item_id=exact.item_id,
            method="exact_sku",
            score=EXACT_MATCH_SCORE,
            provenance=PROVENANCE_EXACT,
        )

    # 3. Fuzzy -- candidates are recorded whether or not one is applied.
    candidates = score_candidates(raw_sku, raw_description, catalog)
    winner = _fuzzy_match(candidates)
    if winner is None:
        return LineMatch(candidates=candidates)
    return LineMatch(
        item_id=winner.item_id,
        method="fuzzy",
        score=winner.score,
        provenance=PROVENANCE_FUZZY,
        candidates=candidates,
    )


def resolve_uom(raw_unit: str | None, uom_rules: dict[str, UomRule]) -> UomRule | None:
    """A tenant's `uom_alias` rule for this unit, if a human confirmed one.
    Returns the rule rather than the string so the caller records its ID as
    provenance (Section 7.13)."""
    return uom_rules.get(normalize_description(raw_unit))


def uom_mismatch(effective_unit: str | None, item_unit: str | None) -> bool:
    """
    True when the line's unit and the catalog item's unit disagree and both
    are present. This is only ever *reported* -- Section 7.6 forbids
    normalizing a unit silently, and nothing in this module writes to
    `document_lines.unit`.
    """
    left = normalize_description(effective_unit)
    right = normalize_description(item_unit)
    if not left or not right:
        return False
    return left != right


# ── Database access (tenant-scoped session required) ────────────────────────


def load_catalog(session: Session, tenant_id: UUID) -> Catalog:
    """
    The tenant's live catalog, read once per document. Retired (soft-deleted)
    SKUs are excluded: a retired item must never become a new match, and
    Section 7.15.2 keeps it in the table only so history and rules can still
    reference it.
    """
    rows = session.execute(
        text(
            "SELECT id, sku, description, unit_of_measure FROM items "
            "WHERE tenant_id = :tenant_id AND deleted_at IS NULL"
        ),
        {"tenant_id": str(tenant_id)},
    ).mappings().all()
    return Catalog(
        [
            CatalogItem(
                item_id=UUID(str(row["id"])),
                sku=row["sku"],
                description=row["description"],
                unit_of_measure=row["unit_of_measure"],
            )
            for row in rows
        ]
    )


def _load_rules(session: Session, tenant_id: UUID, buyer_id: UUID | None, rule_type: str) -> list[dict]:
    """
    Active, non-deleted rules for this tenant that are either scoped to this
    buyer or tenant-wide. Buyer-scoped rows come first so the caller's
    `setdefault` gives the specific rule precedence over the general one
    (D-065).

    `buyer_id = :buyer_id` evaluates to NULL when no buyer is bound, so the
    OR falls through to the tenant-wide rules alone -- a document whose buyer
    could not be identified still gets the tenant's general rules, and gets
    no other buyer's.

    The cast is written `CAST(:buyer_id AS uuid)` rather than
    `:buyer_id::uuid`: SQLAlchemy's `text()` treats `:` as the start of a
    bind parameter, so the Postgres `::` cast operator is a syntax error
    there (caught by test_load_sku_rules_* against the real database).
    """
    rows = session.execute(
        text(
            """
            SELECT id, buyer_id, match_key, match_value
            FROM learned_rules
            WHERE tenant_id = :tenant_id
              AND rule_type = :rule_type
              AND status = 'active'
              AND deleted_at IS NULL
              AND (buyer_id IS NULL OR buyer_id = CAST(:buyer_id AS uuid))
            ORDER BY (buyer_id IS NULL), created_at DESC
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "rule_type": rule_type,
            "buyer_id": str(buyer_id) if buyer_id else None,
        },
    ).mappings().all()
    return [dict(row) for row in rows]


def load_sku_rules(session: Session, tenant_id: UUID, buyer_id: UUID | None) -> dict[str, SkuRule]:
    """Active `sku_mapping` rules keyed by their normalized match key."""
    rules: dict[str, SkuRule] = {}
    for row in _load_rules(session, tenant_id, buyer_id, "sku_mapping"):
        value = row["match_value"] or {}
        item_id = value.get("item_id")
        if not item_id:
            continue
        # The key is normalized again on read, not trusted as stored: a rule
        # inserted by the Console (or a migration) must behave identically to
        # one created by `confirm_sku_mapping`. `normalize_description` is
        # idempotent, so this is free for keys that were already normalized.
        rules.setdefault(
            normalize_description(row["match_key"]),
            SkuRule(
                rule_id=UUID(str(row["id"])),
                buyer_scoped=row["buyer_id"] is not None,
                item_id=UUID(str(item_id)),
            ),
        )
    return rules


def load_uom_rules(session: Session, tenant_id: UUID, buyer_id: UUID | None) -> dict[str, UomRule]:
    rules: dict[str, UomRule] = {}
    for row in _load_rules(session, tenant_id, buyer_id, "uom_alias"):
        value = row["match_value"] or {}
        unit = value.get("unit_of_measure")
        if not unit:
            continue
        rules.setdefault(
            normalize_description(row["match_key"]),
            UomRule(
                rule_id=UUID(str(row["id"])),
                buyer_scoped=row["buyer_id"] is not None,
                unit_of_measure=unit,
            ),
        )
    return rules


def _bump_times_applied(session: Session, counts: dict[UUID, int]) -> None:
    """
    `learned_rules.times_applied` is what the Section 7.13 "mapping reuse
    rate" metric is built on, so it is incremented every time a rule actually
    fires -- not when it is merely loaded.
    """
    for rule_id, count in counts.items():
        session.execute(
            text(
                "UPDATE learned_rules SET times_applied = times_applied + :count, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": str(rule_id), "count": count},
        )


def _human_edited(provenance: dict | None, key: str) -> bool:
    """Section 10: "Never overwrite a human correction with a machine
    value." A reviewer's own choice of item is exactly that."""
    value = (provenance or {}).get(key)
    return isinstance(value, str) and value.startswith(PROVENANCE_HUMAN)


def match_document_lines(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    buyer_id: UUID | None = None,
) -> MatchingSummary:
    """
    Run the Section 7.6 pipeline over every line of one document and record
    what it concluded. `session` must already be tenant-scoped.

    Idempotent: re-running replaces the machine-derived conclusion on every
    line except those a human has already answered, which are skipped
    untouched.
    """
    summary = MatchingSummary()

    rows = session.execute(
        text(
            "SELECT id, sku, description, unit, field_provenance FROM document_lines "
            "WHERE document_id = :document_id AND deleted_at IS NULL ORDER BY line_number"
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    if not rows:
        return summary

    catalog = load_catalog(session, tenant_id)
    sku_rules = load_sku_rules(session, tenant_id, buyer_id)
    uom_rules = load_uom_rules(session, tenant_id, buyer_id)
    applied: dict[UUID, int] = {}

    for row in rows:
        summary.lines_considered += 1
        provenance = dict(row["field_provenance"] or {})
        if _human_edited(provenance, PROVENANCE_FIELD_MATCH):
            summary.skipped_human_edited += 1
            continue

        result = resolve_line(
            raw_sku=row["sku"],
            raw_description=row["description"],
            catalog=catalog,
            sku_rules=sku_rules,
        )

        # A `uom_alias` rule produces a *suggestion* in its own column. The
        # extracted `unit` is never touched (Section 7.6).
        matched_uom: str | None = None
        if not _human_edited(provenance, PROVENANCE_FIELD_UOM):
            uom_rule = resolve_uom(row["unit"], uom_rules)
            if uom_rule is not None:
                matched_uom = uom_rule.unit_of_measure
                provenance[PROVENANCE_FIELD_UOM] = f"learned_rule:{uom_rule.rule_id}"
                applied[uom_rule.rule_id] = applied.get(uom_rule.rule_id, 0) + 1
            else:
                provenance.pop(PROVENANCE_FIELD_UOM, None)

        item = catalog.get(result.item_id) if result.item_id else None
        mismatch = uom_mismatch(matched_uom or row["unit"], item.unit_of_measure) if item else False

        if result.matched:
            provenance[PROVENANCE_FIELD_MATCH] = result.provenance
            summary.lines_matched += 1
            method = result.method or "unknown"
            summary.by_method[method] = summary.by_method.get(method, 0) + 1
            if result.learned_rule_id is not None:
                applied[result.learned_rule_id] = applied.get(result.learned_rule_id, 0) + 1
        else:
            provenance.pop(PROVENANCE_FIELD_MATCH, None)

        session.execute(
            text(
                """
                UPDATE document_lines
                SET matched_item_id = :matched_item_id,
                    match_method = :match_method,
                    match_score = :match_score,
                    matched_uom = :matched_uom,
                    uom_mismatch = :uom_mismatch,
                    match_candidates = :match_candidates,
                    field_provenance = :field_provenance,
                    matched_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(row["id"]),
                "matched_item_id": str(result.item_id) if result.item_id else None,
                "match_method": result.method,
                # str(), not float(): the driver binds this to NUMERIC exactly.
                "match_score": str(result.score) if result.score is not None else None,
                "matched_uom": matched_uom,
                "uom_mismatch": mismatch,
                "match_candidates": [c.as_json() for c in result.candidates],
                "field_provenance": provenance,
            },
        )

    _bump_times_applied(session, applied)
    return summary


# ── Creating a learned rule from a human confirmation ───────────────────────


def _upsert_sku_mapping_rule(
    session: Session,
    tenant_id: UUID,
    *,
    buyer_id: UUID | None,
    match_key: str,
    match_value: dict,
    confirmed_by: UUID | None,
    source_document_id: UUID | None,
    acting_as_tenant_id: UUID | None = None,
) -> UUID:
    """
    Insert or refresh the one active `sku_mapping` rule for
    (tenant, buyer, key). Re-confirming the same wording against a different
    item updates the existing rule rather than creating a second one that
    would silently shadow it.

    Two conflict targets because `learned_rules` has two partial unique
    indexes -- Postgres treats NULLs as distinct, so buyer-scoped and
    tenant-wide rules cannot share one (DECISIONS.md D-053). Both strings are
    module-local literals chosen by a boolean; no caller input reaches the
    statement text.

    Separated from `confirm_sku_mapping` so this SQL can be exercised against
    the real database before 0005's `document_lines` columns exist.
    """
    conflict = (
        "(tenant_id, buyer_id, rule_type, match_key) WHERE buyer_id IS NOT NULL AND deleted_at IS NULL"
        if buyer_id
        else "(tenant_id, rule_type, match_key) WHERE buyer_id IS NULL AND deleted_at IS NULL"
    )
    row = session.execute(
        text(
            f"""
            INSERT INTO learned_rules
                (id, tenant_id, buyer_id, rule_type, match_key, match_value, status,
                 confirmed_by, acting_as_tenant_id, source_document_id, created_at, updated_at)
            VALUES
                (:id, :tenant_id, :buyer_id, 'sku_mapping', :match_key, :match_value, 'active',
                 :confirmed_by, :acting_as_tenant_id, :source_document_id, now(), now())
            ON CONFLICT {conflict} DO UPDATE
                SET match_value = excluded.match_value,
                    status = 'active',
                    confirmed_by = excluded.confirmed_by,
                    acting_as_tenant_id = excluded.acting_as_tenant_id,
                    source_document_id = excluded.source_document_id,
                    updated_at = now()
            RETURNING id
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "buyer_id": str(buyer_id) if buyer_id else None,
            "match_key": match_key,
            "match_value": match_value,
            "confirmed_by": str(confirmed_by) if confirmed_by else None,
            # Section 7.15.1: a rule the founder confirmed in the Console
            # shows as DocFlow support in the tenant's own view.
            "acting_as_tenant_id": str(acting_as_tenant_id) if acting_as_tenant_id else None,
            "source_document_id": str(source_document_id) if source_document_id else None,
        },
    ).mappings().first()
    if row is None:  # pragma: no cover -- RETURNING always yields a row here
        raise RuntimeError("learned rule upsert returned nothing")
    return UUID(str(row["id"]))


def confirm_sku_mapping(
    session: Session,
    tenant_id: UUID,
    *,
    document_line_id: UUID,
    item_id: UUID,
    confirmed_by: UUID | None,
    tenant_wide: bool = False,
    review_action_id: UUID | None = None,
    acting_as_tenant_id: UUID | None = None,
) -> UUID | None:
    """
    The Section 7.6 learning step: a reviewer said "this line means that
    catalog item", so record it as an active `sku_mapping` rule and apply it
    to the line.

    This is the *only* function in the codebase that creates an active
    learned rule (Section 10: "never activate a learned rule without a human
    confirmation"), which is why `confirmed_by` and the source document are
    arguments rather than optional bookkeeping.

    Scope is derived, never passed in: the rule is keyed on the line's own
    raw description and scoped to the buyer the line's document is already
    linked to, so a caller cannot file a correction under the wrong buyer.
    `tenant_wide=True` is the founder's deliberate "this applies to every
    buyer" choice.

    Returns the rule ID, or None when the line has no description to key on
    (nothing to learn -- the line is still matched to the item).
    """
    row = session.execute(
        text(
            """
            SELECT dl.id, dl.document_id, dl.description, dl.unit,
                   dl.field_provenance, dh.buyer_id
            FROM document_lines dl
            LEFT JOIN document_headers dh ON dh.document_id = dl.document_id
            WHERE dl.id = :id AND dl.deleted_at IS NULL
            """
        ),
        {"id": str(document_line_id)},
    ).mappings().first()
    if row is None:
        raise LookupError("document line not found in this tenant")

    item = session.execute(
        text(
            "SELECT id, sku, unit_of_measure FROM items "
            "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
        ),
        {"id": str(item_id), "tenant_id": str(tenant_id)},
    ).mappings().first()
    if item is None:
        # Either the item belongs to another tenant (RLS already hid it) or
        # it is retired. Neither is something to learn from.
        raise LookupError("catalog item not found in this tenant")

    provenance = dict(row["field_provenance"] or {})
    provenance[PROVENANCE_FIELD_MATCH] = (
        f"{PROVENANCE_HUMAN}:{review_action_id}" if review_action_id else PROVENANCE_HUMAN
    )
    session.execute(
        text(
            """
            UPDATE document_lines
            SET matched_item_id = :item_id,
                match_method = 'human_confirmed',
                match_score = :score,
                uom_mismatch = :uom_mismatch,
                field_provenance = :field_provenance,
                matched_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": str(document_line_id),
            "item_id": str(item_id),
            "score": str(HUMAN_CONFIRMED_MATCH_SCORE),
            "uom_mismatch": uom_mismatch(row["unit"], item["unit_of_measure"]),
            "field_provenance": provenance,
        },
    )

    match_key = normalize_description(row["description"])
    if not match_key:
        return None

    # `match_value` keeps the description exactly as the document printed it
    # alongside the resolved item, so the rule can be audited against the
    # source without re-reading the document (Section 7.13: auditable).
    return _upsert_sku_mapping_rule(
        session,
        tenant_id,
        buyer_id=None if tenant_wide else row["buyer_id"],
        match_key=match_key,
        match_value={
            "item_id": str(item_id),
            "sku": item["sku"],
            "raw_description": row["description"],
        },
        confirmed_by=confirmed_by,
        source_document_id=row["document_id"],
        acting_as_tenant_id=acting_as_tenant_id,
    )
