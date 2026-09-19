"""
Validation -- warn, never auto-correct (CLAUDE.md Section 7.7).

The specification, in full:

    "Rules: `line_total ~= quantity x unit_price` (within a stated
     tolerance), header total ~= sum of line totals, quantity > 0, dates
     parse and are plausible (not 200 years off), required fields present,
     currency is a valid ISO code."
    "A failed rule produces a warning on the document. It does not change any
     value. The document is not made to 'reconcile' by altering the model's
     output -- the human decides which number is right."

**Nothing in this module writes to `documents`, `document_headers` or
`document_lines`.** The only table it writes is `document_warnings`. That is
not an implementation detail -- it is the entire point, and it is Section 10's
"never alter a model-extracted value to make validation pass" expressed as a
module boundary: there is no code path here that *could* correct a value,
because there is no UPDATE statement here that names an extracted column.

Two layers, the same shape `matching.py` and `buyers.py` use:

  * a pure layer -- `evaluate_document()` and the per-rule `check_*`
    functions -- which takes a `DocumentSnapshot` dataclass and returns a
    list of `DocumentWarning`s, with no database access and no side effects,
    so every threshold is unit-testable without a connection;
  * a database layer -- `load_snapshot()` / `sync_warnings()` /
    `validate_document()` -- which takes an already-open, tenant-scoped
    `Session` from `docflow_core.db.tenant_session()`. This module never
    opens its own connection and never accepts a tenant_id from request data
    (Section 7.5).

Every number here is a `Decimal`. No float touches a quantity, a price or a
tolerance (Section 7.1), and every number that reaches the `detail` payload
does so as a string.

Warnings carry error-catalog codes (`VAL-0xx`, `docflow_core.errors`) for the
reason Section 7.16.5 gives: "No user-facing string that describes a failure
exists outside it." A warning is user-facing text describing something wrong
with a document, so it obeys the same discipline -- the catalog holds the
what/why/what-next prose, and the `detail` payload on each row holds the
specifics (which field, which line, which two numbers disagreed). See
DECISIONS.md D-072.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.errors import Severity, get_error

# ── Tolerances (CLAUDE.md Section 7.7's "within a stated tolerance") ─────────
#
# Stated here, in one place, as Decimals. The reasoning behind each number is
# in DECISIONS.md D-073; the short version is that a rule which fires on every
# correct 40-line purchase order is worse than no rule at all, because
# reviewers learn to click past it -- and the first warning a reviewer learns
# to ignore is the last warning that ever protects them.

# `line_total ~= quantity x unit_price`. The allowance is the exact rounding
# room the printed numbers leave, and nothing more (DECISIONS.md D-096,
# replacing D-073's 0.5% relative term, which let $100 go unreported on a
# $20,000 line):
#   * the line total is printed to the cent, so it can be up to half a cent
#     from the true product -- LINE_TOTAL_ROUNDING;
#   * the unit price may itself have been printed rounded (1,000 units at a
#     true 0.12345 printed as "0.1235"), and that rounding is multiplied by
#     the quantity: quantity x half a unit of the price's last printed
#     decimal -- see `unit_price_half_step`.
LINE_TOTAL_ROUNDING = Decimal("0.01")
# Money is printed to at least the cent, so a price stored as 47.5000 is
# treated as printed "47.50", never "47.5".
MIN_PRICE_DECIMAL_PLACES = 2

# `header total ~= sum of line totals`. Both sides are printed on the
# document, so the only legitimate difference is per-line rounding: a buyer's
# system that summed unrounded line amounts can drift up to half a cent per
# line from the printed lines. One cent per line covers that with room to
# spare -- 40 cents on a 40-line order -- and nothing else is allowed
# (D-096 removed D-073's 0.5% relative term).
HEADER_TOTAL_PER_LINE_ALLOWANCE = Decimal("0.01")

# Above either of these, a money discrepancy stops being a rounding artifact
# and becomes something a reviewer must not skim past, so the warning is
# raised from `warning` to `high`. This is the answer to "a total that's off
# by $4,000 and a missing payment_terms shouldn't look the same".
MATERIAL_DISCREPANCY_ABS = Decimal("100.00")
MATERIAL_DISCREPANCY_REL = Decimal("0.05")

# CLAUDE.md Section 3: "Confidence threshold default 0.80, configurable per
# tenant later (not MVP)." Section 7.1: "Anything below threshold is visibly
# flagged in review."
CONFIDENCE_THRESHOLD = Decimal("0.80")

# ── Date plausibility windows ───────────────────────────────────────────────
#
# Measured against the document's OWN received date (`documents.created_at`),
# never against "today" -- so re-validating a two-year-old document produces
# exactly the warnings it produced when it arrived, and the idempotent
# re-validation in `sync_warnings` stays idempotent (D-074).
ORDER_DATE_MAX_PAST_DAYS = 730  # a legitimately backdated / late-keyed order
ORDER_DATE_MAX_FUTURE_DAYS = 30  # a post-dated order, or a clock skew
DELIVERY_DATE_MAX_PAST_DAYS = 30  # a rush order whose date has already passed
DELIVERY_DATE_MAX_FUTURE_DAYS = 1095  # a blanket order scheduled 3 years out

# ── Required fields ─────────────────────────────────────────────────────────
#
# The fields without which the order cannot be entered anywhere downstream.
# Deliberately short: every entry here produces a warning on a real document
# that legitimately omits it, and a per-tenant field schema (Section 7.13,
# Phase 5) is what will eventually make this configurable. Until then this is
# the base set, in one place.
REQUIRED_HEADER_FIELDS: tuple[str, ...] = ("po_number", "buyer_name", "order_total", "currency")
REQUIRED_LINE_FIELDS: tuple[str, ...] = ("quantity", "line_total")

# A line has to say *what* is being ordered, but a buyer may say it with a
# part number, a description, or both. Missing both is the warning.
LINE_IDENTITY_FIELD = "sku_or_description"

DATE_FIELDS: tuple[str, ...] = ("order_date", "requested_delivery_date")

# ── Catalog codes this module can raise ─────────────────────────────────────
CODE_LINE_TOTAL_MISMATCH = "VAL-001"
CODE_HEADER_TOTAL_MISMATCH = "VAL-002"
CODE_NON_POSITIVE_QUANTITY = "VAL-003"
CODE_UNPARSEABLE_DATE = "VAL-004"
CODE_IMPLAUSIBLE_DATE = "VAL-005"
CODE_MISSING_REQUIRED_FIELD = "VAL-006"
CODE_INVALID_CURRENCY = "VAL-007"
CODE_UOM_MISMATCH = "VAL-008"
CODE_INJECTION_SUSPECTED = "VAL-009"
CODE_LOW_CONFIDENCE = "VAL-010"
CODE_CURRENCY_INFERRED = "VAL-011"
CODE_POSSIBLE_DUPLICATE = "VAL-012"
CODE_POSSIBLE_CHANGE_ORDER = "VAL-013"

# ── ISO 4217 ────────────────────────────────────────────────────────────────
#
# A hardcoded frozenset rather than a dependency (DECISIONS.md D-075): it is
# ~180 three-letter strings that change roughly once a decade, and the
# isolated parsing worker is the last place in this system that wants another
# pinned package to audit for CVEs (Section 7.11). One definition, here.
#
# Includes codes withdrawn in the last few years (ANG, HRK, SLL, VEF, ZWL,
# MRO, STD, BYR) on purpose: a backdated or archived purchase order can
# legitimately carry one, and warning on a historically correct document is
# exactly the false positive this module is trying not to produce.
#
# Excludes `XXX` ("no currency involved") and `XTS` (reserved for testing).
# Both are valid ISO 4217 codes and neither is a valid answer to "what money
# is this order in", so DocFlow treats them as not recognized.
ISO_4217_CODES: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN
    BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BYR BZD
    CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK
    DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP
    GBP GEL GHS GIP GMD GNF GTQ GYD
    HKD HNL HRK HTG HUF IDR ILS INR IQD IRR ISK
    JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
    LAK LBP LKR LRD LSL LYD
    MAD MDL MGA MKD MMK MNT MOP MRO MRU MUR MVR MWK MXN MXV MYR MZN
    NAD NGN NIO NOK NPR NZD OMR
    PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF
    SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STD STN SVC SYP SZL
    THB TJS TMT TND TOP TRY TTD TWD TZS
    UAH UGX USD USN UYI UYU UYW UZS
    VED VEF VES VND VUV WST
    XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XUA
    YER ZAR ZMW ZWG ZWL
    """.split()
)


# ── Warnings ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DocumentWarning:
    """
    One thing a reviewer needs to look at. Named `DocumentWarning` rather than
    `Warning` so it can never be confused with Python's builtin.

    `code` is the catalog entry that supplies the what/why/what-next prose;
    `detail` carries this occurrence's specifics, every number as a string
    (Section 7.1 -- numbers are strings in transport).
    """

    code: str
    severity: Severity
    # `field_name`, not `field`: the dataclasses helper of that name is in
    # scope in this class body, and a `detail` default_factory below would
    # call the attribute instead of the function.
    field_name: str | None = None
    line_number: int | None = None
    document_line_id: UUID | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """
        The idempotency key. Two evaluations of an unchanged document produce
        the same fingerprint, so re-validating never duplicates a row and
        never discards an acknowledgement (D-074).

        It deliberately includes the compared **values**: an acknowledgement
        of "this total is off by two cents" must not silently carry over to
        "this total is off by four thousand dollars". Changing the numbers
        resolves the old warning and raises a new, unacknowledged one.
        """
        canonical = json.dumps(
            {
                "code": self.code,
                "field": self.field_name,
                "line": str(self.document_line_id) if self.document_line_id else None,
                "detail": self.detail,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _catalog_severity(code: str) -> Severity:
    """The catalog entry's severity is the default for every occurrence of
    that code; a rule may raise it (money discrepancies, a missing PO number)
    but the prose and the baseline live in one place."""
    return get_error(code).severity


def _money(value: Decimal | None) -> str | None:
    """
    An **as-extracted** value, rendered exactly as the database handed it
    over. Never re-scaled, never rounded: `unit_price` is `numeric(14,4)`
    because a price legitimately carries four decimal places, and a reviewer
    comparing the payload against the document must see the same digits the
    document printed (Section 7.1).
    """
    return str(value) if value is not None else None


def _derived_money(value: Decimal | None) -> str | None:
    """
    A **computed** money amount -- an expected product, a sum, a difference, a
    tolerance -- rendered at money scale.

    `quantity * unit_price` is `numeric(14,4) * numeric(14,4)`, so its
    Decimal carries eight decimal places and renders as "570.00000000"; a sum
    of `numeric(14,2)` line totals carries two and renders as "570.00". Both
    are the same amount of money, and a reviewer reading one warning should
    not see the two written differently because of a column scale they cannot
    see (DECISIONS.md D-080).

    This rounds a value DocFlow computed for display. It is never applied to
    an extracted value -- Section 10 forbids altering one of those, and
    `_money` above is what those go through.
    """
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ── Pure rules ──────────────────────────────────────────────────────────────


def money_severity(delta: Decimal, magnitude: Decimal) -> Severity:
    """
    `high` when a money discrepancy is material in absolute terms
    (>= $100) or relative terms (>= 5% of what it was compared against),
    `warning` otherwise. A reviewer scanning a list must be able to tell a
    rounding artifact from a real error without opening either.
    """
    delta = abs(delta)
    magnitude = abs(magnitude)
    if delta >= MATERIAL_DISCREPANCY_ABS:
        return "high"
    if magnitude > 0 and (delta / magnitude) >= MATERIAL_DISCREPANCY_REL:
        return "high"
    return "warning"


def unit_price_half_step(unit_price: Decimal) -> Decimal:
    """
    Half a unit of the last decimal place the unit price was printed with --
    the most a correctly rounded printed price can differ from the true one.
    "0.1235" -> 0.00005; "47.50" (stored 47.5000) -> 0.005.
    """
    exponent = unit_price.normalize().as_tuple().exponent
    places = max(MIN_PRICE_DECIMAL_PLACES, -exponent if isinstance(exponent, int) else 0)
    return Decimal(1).scaleb(-places) / 2


def line_total_tolerance(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """The allowance for one line, as a positive Decimal."""
    return LINE_TOTAL_ROUNDING + abs(quantity) * unit_price_half_step(unit_price)


def check_line_total(
    quantity: Decimal | None, unit_price: Decimal | None, line_total: Decimal | None
) -> tuple[Decimal, Decimal] | None:
    """
    `line_total ~= quantity x unit_price`. Returns `(expected, delta)` when
    the rule fires, else None.

    **Null handling:** any of the three missing means the rule *cannot be
    evaluated*, which is silence, not a warning. A genuinely missing
    `quantity` or `line_total` is reported once by the required-field rule;
    reporting it a second time here would train reviewers to skim. A missing
    `unit_price` is not reported at all -- contract-priced lines routinely
    omit it and there is nothing wrong with that document.
    """
    if quantity is None or unit_price is None or line_total is None:
        return None
    expected = quantity * unit_price
    delta = line_total - expected
    if abs(delta) <= line_total_tolerance(quantity, unit_price):
        return None
    return expected, delta


def header_total_tolerance(line_count: int) -> Decimal:
    """
    The allowance for the whole document: per-line rounding, accumulated.
    This is the function that keeps a correct 40-line purchase order silent
    without letting a real error hide inside a percentage of the total.
    """
    return HEADER_TOTAL_PER_LINE_ALLOWANCE * max(line_count, 1)


def check_header_total(
    order_total: Decimal | None, line_totals: list[Decimal | None]
) -> tuple[Decimal, Decimal] | None:
    """
    `header total ~= sum of line totals`. Returns `(expected_sum, delta)` when
    the rule fires, else None.

    **Null handling, deliberately:**
      * `order_total` missing -> silence here; the required-field rule says it
        once.
      * any line missing its `line_total` -> silence here, because a sum built
        from an incomplete set of lines would be compared against a total that
        legitimately includes them, and the resulting warning would be about
        DocFlow's gap rather than about the document. Each of those lines
        already carries its own required-field warning, so nothing is hidden.
      * no lines at all -> silence; there is nothing to reconcile against.
    """
    if order_total is None or not line_totals:
        return None
    if any(value is None for value in line_totals):
        return None
    expected_sum = sum((value for value in line_totals if value is not None), Decimal("0"))
    delta = order_total - expected_sum
    if abs(delta) <= header_total_tolerance(len(line_totals)):
        return None
    return expected_sum, delta


def check_quantity(quantity: Decimal | None) -> bool:
    """True when the rule fires. A null quantity is the required-field rule's
    business, not this one's."""
    return quantity is not None and quantity <= 0


def parse_iso_date(value: str | None) -> date | None:
    """
    The extraction prompt normalizes dates to YYYY-MM-DD and the column is
    `text`, so this is a strict parse of exactly that shape. It never guesses
    at another format: a date DocFlow cannot read is a warning, and a date
    DocFlow re-interprets is an invented value (Section 7.1).
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def check_date(
    value: str | None, received_on: date, *, max_past_days: int, max_future_days: int
) -> str | None:
    """
    Returns `"unparseable"`, `"implausible"`, or None.

    Plausibility is measured against the document's own received date, not
    against now (see the window constants above). A null value is silence --
    a date that is not printed is not a wrong date.
    """
    if value is None or not value.strip():
        return None
    parsed = parse_iso_date(value)
    if parsed is None:
        return "unparseable"
    delta_days = (parsed - received_on).days
    if delta_days < -max_past_days or delta_days > max_future_days:
        return "implausible"
    return None


def normalize_currency(value: str | None) -> str:
    """A derived comparison key, never a correction: the stored `currency`
    keeps whatever the document printed."""
    return (value or "").strip().upper()


def check_currency(value: str | None) -> bool:
    """True when the rule fires. A null currency is the required-field rule's
    business."""
    if value is None or not value.strip():
        return False
    return normalize_currency(value) not in ISO_4217_CODES


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _confidence_of(raw: Any) -> Decimal | None:
    """Confidence arrives as a float from the model's JSON and as a NUMERIC
    from the database. Both become a Decimal here; anything else is treated as
    absent rather than guessed at."""
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    return None


# ── Snapshots ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LineSnapshot:
    line_id: UUID
    line_number: int
    sku: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    confidence: Decimal | None = None
    uom_mismatch: bool = False
    matched_uom: str | None = None
    matched_item_uom: str | None = None


@dataclass(frozen=True)
class DocumentSnapshot:
    document_id: UUID
    received_on: date
    header: dict[str, Any] = field(default_factory=dict)
    header_confidence: dict[str, Any] = field(default_factory=dict)
    lines: list[LineSnapshot] = field(default_factory=list)
    injection_suspected: bool = False
    currency_inferred: bool = False
    is_possible_duplicate: bool = False
    duplicate_of_document_id: UUID | None = None
    is_possible_change_order: bool = False
    change_order_of_document_id: UUID | None = None


# ── The whole-document evaluation (pure) ────────────────────────────────────


def _header_warnings(snapshot: DocumentSnapshot) -> list[DocumentWarning]:
    header = snapshot.header
    warnings: list[DocumentWarning] = []

    for name in REQUIRED_HEADER_FIELDS:
        if _is_blank(header.get(name)):
            # Section 7.1: "one wrong PO number matters more than nine right
            # ones" -- a document with no PO number cannot be reconciled
            # against anything downstream, so that one field is raised.
            severity: Severity = (
                "high" if name == "po_number" else _catalog_severity(CODE_MISSING_REQUIRED_FIELD)
            )
            warnings.append(
                DocumentWarning(
                    code=CODE_MISSING_REQUIRED_FIELD,
                    severity=severity,
                    field_name=name,
                    detail={"scope": "header", "field": name},
                )
            )

    if check_currency(header.get("currency")):
        warnings.append(
            DocumentWarning(
                code=CODE_INVALID_CURRENCY,
                severity=_catalog_severity(CODE_INVALID_CURRENCY),
                field_name="currency",
                detail={"currency": header.get("currency")},
            )
        )

    # Section 7.1: "Currency inferred from a symbol rather than stated gets
    # its confidence capped ... and a warning." The cap is applied at the
    # extraction boundary; this is the warning half of that sentence.
    if snapshot.currency_inferred:
        warnings.append(
            DocumentWarning(
                code=CODE_CURRENCY_INFERRED,
                severity=_catalog_severity(CODE_CURRENCY_INFERRED),
                field_name="currency",
                detail={"currency": header.get("currency")},
            )
        )

    windows = {
        "order_date": (ORDER_DATE_MAX_PAST_DAYS, ORDER_DATE_MAX_FUTURE_DAYS),
        "requested_delivery_date": (DELIVERY_DATE_MAX_PAST_DAYS, DELIVERY_DATE_MAX_FUTURE_DAYS),
    }
    for name in DATE_FIELDS:
        max_past, max_future = windows[name]
        verdict = check_date(
            header.get(name), snapshot.received_on, max_past_days=max_past, max_future_days=max_future
        )
        if verdict == "unparseable":
            warnings.append(
                DocumentWarning(
                    code=CODE_UNPARSEABLE_DATE,
                    severity=_catalog_severity(CODE_UNPARSEABLE_DATE),
                    field_name=name,
                    detail={"field": name, "value": header.get(name)},
                )
            )
        elif verdict == "implausible":
            warnings.append(
                DocumentWarning(
                    code=CODE_IMPLAUSIBLE_DATE,
                    severity=_catalog_severity(CODE_IMPLAUSIBLE_DATE),
                    field_name=name,
                    detail={
                        "field": name,
                        "value": header.get(name),
                        "received_on": snapshot.received_on.isoformat(),
                    },
                )
            )

    reconciliation = check_header_total(
        header.get("order_total"), [line.line_total for line in snapshot.lines]
    )
    if reconciliation is not None:
        expected_sum, delta = reconciliation
        warnings.append(
            DocumentWarning(
                code=CODE_HEADER_TOTAL_MISMATCH,
                severity=money_severity(delta, expected_sum),
                field_name="order_total",
                detail={
                    "order_total": _money(header.get("order_total")),
                    "sum_of_lines": _derived_money(expected_sum),
                    "difference": _derived_money(delta),
                    "line_count": str(len(snapshot.lines)),
                    "tolerance": _derived_money(header_total_tolerance(len(snapshot.lines))),
                },
            )
        )

    for name, raw in sorted(snapshot.header_confidence.items()):
        confidence = _confidence_of(raw)
        # An optional field the document simply doesn't have is shown empty,
        # not flagged: "not confident" on a blank buyer email or ship-to was
        # most of the checks on a typical order, and a list that is mostly
        # noise trains reviewers to tick without reading (D-115). Required
        # fields, and any field with a value, are still flagged.
        if name not in REQUIRED_HEADER_FIELDS and _is_blank(header.get(name)):
            continue
        if confidence is not None and confidence < CONFIDENCE_THRESHOLD:
            warnings.append(
                DocumentWarning(
                    code=CODE_LOW_CONFIDENCE,
                    severity=_catalog_severity(CODE_LOW_CONFIDENCE),
                    field_name=name,
                    detail={
                        "scope": "header",
                        "field": name,
                        "confidence": str(confidence),
                        "threshold": str(CONFIDENCE_THRESHOLD),
                    },
                )
            )

    return warnings


def _document_warnings(snapshot: DocumentSnapshot) -> list[DocumentWarning]:
    warnings: list[DocumentWarning] = []

    # Section 7.2: an injection attempt forces `needs_review` with a visible
    # banner regardless of confidence. The status is already `needs_review`
    # for every extracted document (there is no auto-approve path in the MVP);
    # this row is the banner.
    if snapshot.injection_suspected:
        warnings.append(
            DocumentWarning(
                code=CODE_INJECTION_SUSPECTED,
                severity=_catalog_severity(CODE_INJECTION_SUSPECTED),
                detail={"injection_suspected": "true"},
            )
        )

    if snapshot.is_possible_duplicate and snapshot.duplicate_of_document_id is not None:
        warnings.append(
            DocumentWarning(
                code=CODE_POSSIBLE_DUPLICATE,
                severity=_catalog_severity(CODE_POSSIBLE_DUPLICATE),
                detail={"duplicate_of_document_id": str(snapshot.duplicate_of_document_id)},
            )
        )

    if snapshot.is_possible_change_order and snapshot.change_order_of_document_id is not None:
        warnings.append(
            DocumentWarning(
                code=CODE_POSSIBLE_CHANGE_ORDER,
                severity=_catalog_severity(CODE_POSSIBLE_CHANGE_ORDER),
                detail={"change_order_of_document_id": str(snapshot.change_order_of_document_id)},
            )
        )

    return warnings


def _line_warnings(line: LineSnapshot) -> list[DocumentWarning]:
    warnings: list[DocumentWarning] = []

    def warning(code: str, *, severity: Severity | None = None, detail: dict, field_name: str | None = None):
        return DocumentWarning(
            code=code,
            severity=severity or _catalog_severity(code),
            field_name=field_name,
            line_number=line.line_number,
            document_line_id=line.line_id,
            detail={"line_number": str(line.line_number), **detail},
        )

    if _is_blank(line.sku) and _is_blank(line.description):
        warnings.append(
            warning(
                CODE_MISSING_REQUIRED_FIELD,
                field_name=LINE_IDENTITY_FIELD,
                detail={"scope": "line", "field": LINE_IDENTITY_FIELD},
            )
        )

    for name in REQUIRED_LINE_FIELDS:
        if getattr(line, name) is None:
            warnings.append(
                warning(
                    CODE_MISSING_REQUIRED_FIELD,
                    field_name=name,
                    detail={"scope": "line", "field": name},
                )
            )

    if check_quantity(line.quantity):
        warnings.append(
            warning(
                CODE_NON_POSITIVE_QUANTITY,
                field_name="quantity",
                detail={"quantity": _money(line.quantity)},
            )
        )

    reconciliation = check_line_total(line.quantity, line.unit_price, line.line_total)
    if reconciliation is not None:
        expected, delta = reconciliation
        # check_line_total only fires when both are present.
        assert line.quantity is not None and line.unit_price is not None
        warnings.append(
            warning(
                CODE_LINE_TOTAL_MISMATCH,
                severity=money_severity(delta, expected),
                field_name="line_total",
                detail={
                    "quantity": _money(line.quantity),
                    "unit_price": _money(line.unit_price),
                    "line_total": _money(line.line_total),
                    "expected": _derived_money(expected),
                    "difference": _derived_money(delta),
                    "tolerance": _derived_money(line_total_tolerance(line.quantity, line.unit_price)),
                },
            )
        )

    # DECISIONS.md D-070 deferred "warnings as first-class rows" for units of
    # measure to this slice. This is that: the boolean the matching slice
    # wrote becomes a warning a reviewer can see and acknowledge, and still
    # nothing is corrected.
    if line.uom_mismatch:
        warnings.append(
            warning(
                CODE_UOM_MISMATCH,
                field_name="unit",
                detail={
                    "unit": line.unit,
                    "matched_uom": line.matched_uom,
                    "catalog_unit_of_measure": line.matched_item_uom,
                },
            )
        )

    if line.confidence is not None and line.confidence < CONFIDENCE_THRESHOLD:
        warnings.append(
            warning(
                CODE_LOW_CONFIDENCE,
                detail={
                    "scope": "line",
                    "confidence": str(line.confidence),
                    "threshold": str(CONFIDENCE_THRESHOLD),
                },
            )
        )

    return warnings


def evaluate_document(snapshot: DocumentSnapshot) -> list[DocumentWarning]:
    """
    Every Section 7.7 rule over one document, with no database access and no
    side effects. Deterministic order: document-level, then header, then lines
    in `line_number` order -- so the same document always produces the same
    list, which is what makes `sync_warnings` idempotent.
    """
    warnings = _document_warnings(snapshot)
    warnings.extend(_header_warnings(snapshot))
    for line in sorted(snapshot.lines, key=lambda item: item.line_number):
        warnings.extend(_line_warnings(line))
    return warnings


# ── Database layer (tenant-scoped session required) ─────────────────────────


@dataclass
class ValidationSummary:
    evaluated: int = 0
    created: int = 0
    retained: int = 0
    resolved: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)


def load_snapshot(session: Session, document_id: UUID) -> DocumentSnapshot | None:
    """
    Reads everything the rules need in two statements. Read-only by
    construction -- `validate_document` has no write path onto any of these
    columns.
    """
    row = session.execute(
        text(
            """
            SELECT d.id, d.created_at, d.injection_suspected,
                   d.is_possible_duplicate, d.duplicate_of_document_id,
                   d.is_possible_change_order, d.change_order_of_document_id,
                   h.po_number, h.order_date, h.requested_delivery_date, h.buyer_name,
                   h.buyer_contact_email, h.ship_to_address, h.payment_terms,
                   h.order_total, h.currency, h.notes, h.header_confidence, h.currency_inferred
            FROM documents d
            LEFT JOIN document_headers h
                   ON h.document_id = d.id AND h.deleted_at IS NULL
            WHERE d.id = :document_id AND d.deleted_at IS NULL
            """
        ),
        {"document_id": str(document_id)},
    ).mappings().first()
    if row is None:
        return None

    line_rows = session.execute(
        text(
            """
            SELECT l.id, l.line_number, l.sku, l.description, l.quantity, l.unit,
                   l.unit_price, l.line_total, l.confidence, l.uom_mismatch, l.matched_uom,
                   i.unit_of_measure AS matched_item_uom
            FROM document_lines l
            LEFT JOIN items i ON i.id = l.matched_item_id
            WHERE l.document_id = :document_id AND l.deleted_at IS NULL
            ORDER BY l.line_number
            """
        ),
        {"document_id": str(document_id)},
    ).mappings().all()

    header_keys = (
        "po_number",
        "order_date",
        "requested_delivery_date",
        "buyer_name",
        "buyer_contact_email",
        "ship_to_address",
        "payment_terms",
        "order_total",
        "currency",
        "notes",
    )
    raw_confidence = row["header_confidence"]
    if isinstance(raw_confidence, str):
        raw_confidence = json.loads(raw_confidence)

    return DocumentSnapshot(
        document_id=UUID(str(row["id"])),
        received_on=row["created_at"].date(),
        header={key: row[key] for key in header_keys},
        header_confidence=dict(raw_confidence or {}),
        lines=[
            LineSnapshot(
                line_id=UUID(str(line["id"])),
                line_number=line["line_number"],
                sku=line["sku"],
                description=line["description"],
                quantity=line["quantity"],
                unit=line["unit"],
                unit_price=line["unit_price"],
                line_total=line["line_total"],
                confidence=line["confidence"],
                uom_mismatch=bool(line["uom_mismatch"]),
                matched_uom=line["matched_uom"],
                matched_item_uom=line["matched_item_uom"],
            )
            for line in line_rows
        ],
        injection_suspected=bool(row["injection_suspected"]),
        currency_inferred=bool(row["currency_inferred"]),
        is_possible_duplicate=bool(row["is_possible_duplicate"]),
        duplicate_of_document_id=(
            UUID(str(row["duplicate_of_document_id"])) if row["duplicate_of_document_id"] else None
        ),
        is_possible_change_order=bool(row["is_possible_change_order"]),
        change_order_of_document_id=(
            UUID(str(row["change_order_of_document_id"])) if row["change_order_of_document_id"] else None
        ),
    )


def sync_warnings(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    warnings: list[DocumentWarning],
) -> ValidationSummary:
    """
    Reconcile the computed warning set against what is stored, by fingerprint
    (DECISIONS.md D-074):

      * a fingerprint that is computed and not stored -> a new `open` row;
      * a fingerprint that is both -> **left completely untouched**, which is
        how a human's acknowledgement survives re-processing (Section 7.3,
        Section 10: "never overwrite a human correction with a machine
        value");
      * a fingerprint that is stored and no longer computed -> resolved and
        soft-deleted. The row, including who acknowledged it and when,
        survives for the audit trail; it simply stops being a live warning.

    Nothing here writes to an extracted value. The only table this function
    names is `document_warnings`.
    """
    summary = ValidationSummary(evaluated=len(warnings))

    stored = session.execute(
        text(
            "SELECT id, fingerprint FROM document_warnings "
            "WHERE document_id = :document_id AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    stored_by_fingerprint = {row["fingerprint"]: UUID(str(row["id"])) for row in stored}

    computed: dict[str, DocumentWarning] = {}
    for warning in warnings:
        # A duplicate fingerprint inside one evaluation would mean two rules
        # produced literally the same statement about the same value; keeping
        # the first is correct and keeps the insert loop total.
        computed.setdefault(warning.fingerprint, warning)

    for fingerprint, warning in computed.items():
        if fingerprint in stored_by_fingerprint:
            summary.retained += 1
            summary.by_severity[warning.severity] = summary.by_severity.get(warning.severity, 0) + 1
            continue
        session.execute(
            text(
                """
                INSERT INTO document_warnings
                    (id, tenant_id, document_id, document_line_id, code, severity, field_name,
                     line_number, detail, fingerprint, status, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :document_id, :document_line_id, :code, :severity, :field_name,
                     :line_number, :detail, :fingerprint, 'open', now(), now())
                ON CONFLICT (document_id, fingerprint) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "document_line_id": str(warning.document_line_id) if warning.document_line_id else None,
                "code": warning.code,
                "severity": warning.severity,
                "field_name": warning.field_name,
                "line_number": warning.line_number,
                "detail": warning.detail,
                "fingerprint": fingerprint,
            },
        )
        summary.created += 1
        summary.by_severity[warning.severity] = summary.by_severity.get(warning.severity, 0) + 1

    gone = [
        warning_id
        for fingerprint, warning_id in stored_by_fingerprint.items()
        if fingerprint not in computed
    ]
    for warning_id in gone:
        session.execute(
            text(
                "UPDATE document_warnings "
                "SET status = 'resolved', resolved_at = now(), deleted_at = now(), updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": str(warning_id)},
        )
    summary.resolved = len(gone)

    return summary


def validate_document(session: Session, tenant_id: UUID, document_id: UUID) -> ValidationSummary:
    """
    The Section 7.7 pass over one document: load, evaluate, reconcile.
    `session` must already be tenant-scoped.

    Idempotent, and safe to re-run after a re-extraction or a human edit --
    see `sync_warnings` for what re-running does and does not touch.
    """
    snapshot = load_snapshot(session, document_id)
    if snapshot is None:
        return ValidationSummary()
    return sync_warnings(session, tenant_id, document_id, evaluate_document(snapshot))


def open_warnings(session: Session, document_id: UUID) -> list[dict[str, Any]]:
    """
    The live warnings for one document, most severe first -- what the review
    screen reads and what Section 7.3's approval check will count. Severity
    order is expressed in SQL rather than in Python so pagination later cannot
    reorder it.
    """
    rows = session.execute(
        text(
            """
            SELECT id, document_line_id, code, severity, field_name, line_number, detail, status,
                   acknowledged_by, acknowledged_at, created_at
            FROM document_warnings
            WHERE document_id = :document_id AND deleted_at IS NULL
            ORDER BY CASE severity
                         WHEN 'critical' THEN 0
                         WHEN 'high' THEN 1
                         WHEN 'warning' THEN 2
                         ELSE 3
                     END,
                     line_number NULLS FIRST,
                     code,
                     created_at
            """
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    return [dict(row) for row in rows]


def unacknowledged_warning_count(session: Session, document_id: UUID) -> int:
    """
    Section 7.3: "Any unresolved warning at approval time must be explicitly
    acknowledged." The approval path that enforces it is Phase 3; this is the
    one query it will ask, defined next to the rules that produce the rows.
    """
    row = session.execute(
        text(
            "SELECT count(*) AS c FROM document_warnings "
            "WHERE document_id = :document_id AND deleted_at IS NULL AND status = 'open'"
        ),
        {"document_id": str(document_id)},
    ).mappings().first()
    return int(row["c"]) if row else 0


# Guard against a silent drift between the code constants above and the error
# catalog: every code this module can raise must exist in the catalog before
# the module can even be imported (Section 7.16.5).
_ALL_CODES = (
    CODE_LINE_TOTAL_MISMATCH,
    CODE_HEADER_TOTAL_MISMATCH,
    CODE_NON_POSITIVE_QUANTITY,
    CODE_UNPARSEABLE_DATE,
    CODE_IMPLAUSIBLE_DATE,
    CODE_MISSING_REQUIRED_FIELD,
    CODE_INVALID_CURRENCY,
    CODE_UOM_MISMATCH,
    CODE_INJECTION_SUSPECTED,
    CODE_LOW_CONFIDENCE,
    CODE_CURRENCY_INFERRED,
    CODE_POSSIBLE_DUPLICATE,
    CODE_POSSIBLE_CHANGE_ORDER,
)
for _code in _ALL_CODES:
    get_error(_code)
