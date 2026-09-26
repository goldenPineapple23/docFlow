"""
Document numbers: one definition of what one is, and one way to write it down.

The rule (DECISIONS.md D-149, review finding C1): **no value is ever rounded
between the document and the export.** Prices, quantities and totals read off
a purchase order are stored as an unconstrained `numeric`, handled as
`Decimal`, and carried as strings everywhere else -- and every conversion
between those goes through this module, so there is exactly one place that
could get it wrong.

Two traps this module exists to close:

* `str(Decimal)` switches to exponent notation for small and very precise
  values: `str(Decimal("0.0000001"))` is "1E-7". A value Postgres hands back
  as 0.0000001 would reach the snapshot, the audit trail and every export as
  "1E-7". `plain()` never does that.
* `Decimal("1,356.00")` raises, and `Decimal("47.50 USD")` raises, but
  `Decimal("NaN")`, `Decimal("Infinity")`, `Decimal("1E+3")` and
  `Decimal(" 12 ")` do not. The extraction contract (Section 8.1, 8.2) says a
  number is "digits and a decimal point only"; `parse_document_number` holds
  the model and the reviewer to exactly that (review finding M2).
"""

from __future__ import annotations

import re
from decimal import Decimal

# Digits, optionally a decimal point and more digits. Nothing else: no sign,
# no thousands separator, no currency, no exponent, no surrounding spaces.
DOCUMENT_NUMBER = re.compile(r"\d+(\.\d+)?")

# More decimal places than this is legal, and stored exactly, but unusual
# enough on a purchase order to ask a person to confirm it (VAL-015, D-149).
UNUSUAL_DECIMAL_PLACES = 6


def is_document_number(text: str) -> bool:
    return DOCUMENT_NUMBER.fullmatch(text) is not None


def parse_document_number(text: str | None) -> Decimal | None:
    """The Decimal for a conforming number string; None for anything else.

    Returning None is not the end of the story for a model value: the raw
    string stays in `documents.raw_json`, and validation raises VAL-014 for a
    field whose printed value couldn't be read as a number, so the reviewer
    sees what was printed and types the number in.
    """
    if text is None or not is_document_number(text):
        return None
    return Decimal(text)


def plain(value: Decimal) -> str:
    """Every digit, no exponent, the scale it came with: 47.50 -> "47.50",
    Decimal("1E-7") -> "0.0000001", Decimal("1E+3") -> "1000"."""
    return format(value, "f")


def plain_or_none(value: Decimal | None) -> str | None:
    return plain(value) if value is not None else None


def decimal_places(value: Decimal) -> int:
    """How many digits the value carries after the point, as stored -- trailing
    zeros included, because they are part of what was printed ("47.50" is 2)."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def exact_context_precision(*values: Decimal | None) -> int:
    """A `decimal` precision large enough that adding or multiplying these
    values is exact. Python's default is 28 significant digits, which rounds
    the product of two long numbers -- and a validation rule that rounds can
    both miss a real discrepancy and invent one."""
    digits = sum(len(v.as_tuple().digits) + abs(int(v.as_tuple().exponent)) for v in values if v is not None)
    return max(28, 2 * digits + 10)
