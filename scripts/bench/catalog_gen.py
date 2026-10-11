"""
Fake catalogs for Stage 4's measurements (D-199, D-200).

Every item is made up, from a fixed seed: the same seed, tenant number and
count always give the same catalog, on any machine. Nothing here is a real
company's part number or description; the fastener terms are the trade's
generic abbreviations.

Two kinds of family, mixed in every catalog:

* **Fastener families** (about four items in five). A type, a thread, a
  length, a grade and a finish: "1/4-20 x 1 HHCS GR5 ZP". Thousands of items
  differ from a sibling by one token, which is the worst case for a top-K
  candidate list and for the ambiguity guard, and it is what a fastener
  distributor's catalog looks like (founder, 2026-10-11).
* **General "Acme Test" families** (about one in five, and never more than
  the 25,920 there are): a product, a variant, a size and a pack, "Acme Test
  Paper Cups Kraft 12oz (1000ct)". They carry D-066's hard pairs (the same
  product in "5lb" and "2lb").

About one item in two hundred repeats another item's description under a
different SKU (7.15.2 Step 4's "duplicate description with different SKUs").

A tenant's catalog has one house style, chosen by its number: fastener
descriptions abbreviated or spelled out, SKUs with or without dashes.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

FASTENER_SHARE = 0.8
DUPLICATE_DESCRIPTION_EVERY = 200

# (abbreviation, spelled out, unit of measure)
_TYPES = (
    ("HHCS", "Hex Head Cap Screw", "EA"),
    ("SHCS", "Socket Head Cap Screw", "EA"),
    ("FHCS", "Flat Head Cap Screw", "EA"),
    ("BHCS", "Button Head Cap Screw", "EA"),
    ("CB", "Carriage Bolt", "EA"),
    ("LAG", "Hex Lag Screw", "EA"),
    ("PPMS", "Pan Phillips Machine Screw", "C"),
    ("FPMS", "Flat Phillips Machine Screw", "C"),
    ("HWTS", "Hex Washer Tapping Screw", "C"),
    ("SSS", "Socket Set Screw", "C"),
    ("HFB", "Hex Flange Bolt", "EA"),
    ("TR", "Threaded Rod", "EA"),
)
_THREADS = (
    "#4-40", "#6-32", "#8-32", "#10-24", "#10-32", "1/4-20", "1/4-28", "5/16-18", "5/16-24",
    "3/8-16", "3/8-24", "7/16-14", "7/16-20", "1/2-13", "1/2-20", "9/16-12", "5/8-11", "5/8-18",
    "3/4-10", "3/4-16", "7/8-9", "1-8", "M3-0.5", "M4-0.7", "M5-0.8", "M6-1.0", "M8-1.25",
    "M10-1.5", "M12-1.75", "M14-2.0", "M16-2.0", "M20-2.5", "M24-3.0",
)  # fmt: skip
_LENGTHS = (
    "1/4", "3/8", "1/2", "5/8", "3/4", "7/8", "1", "1-1/4", "1-1/2", "1-3/4", "2", "2-1/4",
    "2-1/2", "2-3/4", "3", "3-1/2", "4", "4-1/2", "5", "5-1/2", "6", "7", "8", "10", "12",
    "16", "20", "25", "30", "35", "40", "50", "60", "70", "80", "100",
)  # fmt: skip
_GRADES = (
    ("GR2", "Grade 2"),
    ("GR5", "Grade 5"),
    ("GR8", "Grade 8"),
    ("A307", "A307"),
    ("B7", "B7"),
    ("18-8", "18-8 Stainless"),
    ("316", "316 Stainless"),
    ("BRS", "Brass"),
    ("NYL", "Nylon"),
)
_FINISHES = (
    ("ZP", "Zinc Plated"),
    ("YZ", "Yellow Zinc"),
    ("HDG", "Hot Dip Galvanized"),
    ("PLN", "Plain"),
    ("BO", "Black Oxide"),
    ("GEO", "Geomet"),
)

_PRODUCTS = (
    "Paper Cups", "Cup Lids", "Stir Sticks", "Napkins", "Paper Towels", "Trash Liners", "Nitrile Gloves",
    "Vinyl Gloves", "Shop Rags", "Cable Ties", "Packing Tape", "Stretch Wrap", "Bubble Mailers",
    "Shipping Labels", "Corrugated Boxes", "Kraft Paper", "Whole Bean Coffee", "Ground Coffee",
    "Flavor Syrup", "Tea Bags", "Sugar Packets", "Creamer Cups", "Hand Soap", "Floor Cleaner",
    "Glass Cleaner", "Degreaser", "Sanding Discs", "Cutoff Wheels", "Drill Bits", "Safety Glasses",
)  # fmt: skip
_VARIANTS = (
    "Kraft", "White", "Black", "Clear", "Blue", "Heavy Duty", "Economy", "Premium", "Unscented",
    "Lemon", "Vanilla", "Colombian",
)  # fmt: skip
_SIZES = ("2lb", "5lb", "10lb", "8oz", "12oz", "16oz", "20oz", "750ml", "1gal", "5gal", "Small", "Large")
_PACKS = ("(12ct)", "(50ct)", "(100ct)", "(500ct)", "(1000ct)", "(Case)")

FASTENER_COMBINATIONS = len(_TYPES) * len(_THREADS) * len(_LENGTHS) * len(_GRADES) * len(_FINISHES)
GENERAL_COMBINATIONS = len(_PRODUCTS) * len(_VARIANTS) * len(_SIZES) * len(_PACKS)
MAX_ITEMS_PER_TENANT = FASTENER_COMBINATIONS  # the fastener families alone can fill a catalog


@dataclass(frozen=True)
class Item:
    sku: str
    description: str
    unit_of_measure: str
    barcode: str | None
    external_id: str | None
    raw: dict[str, str]  # the source row as a catalog upload stores it (items.raw_data)


def tenant_name(number: int, count: int) -> str:
    return f"Acme Test Bench {number:02d} ({count} items)"


def _split(count: int) -> tuple[int, int, int]:
    """How many fastener items, general items and duplicate-description
    extras make up `count`."""
    extras = count // DUPLICATE_DESCRIPTION_EVERY
    general = min(int((count - extras) * (1 - FASTENER_SHARE)), GENERAL_COMBINATIONS)
    fasteners = count - extras - general
    if fasteners > FASTENER_COMBINATIONS:
        raise ValueError(f"{count} items is more than one tenant's catalog can hold ({MAX_ITEMS_PER_TENANT})")
    return fasteners, general, extras


def _decode(index: int, *sizes: int) -> tuple[int, ...]:
    out = []
    for size in reversed(sizes):
        index, digit = divmod(index, size)
        out.append(digit)
    return tuple(reversed(out))


def _code(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def _fastener(index: int, spelled_out: bool, dashes: bool) -> tuple[str, str, str]:
    t, th, ln, g, f = _decode(index, len(_TYPES), len(_THREADS), len(_LENGTHS), len(_GRADES), len(_FINISHES))
    abbreviation, name, uom = _TYPES[t]
    grade, finish = _GRADES[g], _FINISHES[f]
    if spelled_out:
        description = f"{name} {_THREADS[th]} x {_LENGTHS[ln]} {grade[1]} {finish[1]}"
    else:
        description = f"{_THREADS[th]} x {_LENGTHS[ln]} {abbreviation} {grade[0]} {finish[0]}"
    # Fixed-width thread and length numbers: the SKU is different for every
    # combination, with or without dashes.
    parts = (abbreviation, f"{th:02d}{ln:02d}", _code(grade[0]), finish[0])
    return ("-" if dashes else "").join(parts), description, uom


def _general(index: int, dashes: bool) -> tuple[str, str, str]:
    p, v, s, k = _decode(index, len(_PRODUCTS), len(_VARIANTS), len(_SIZES), len(_PACKS))
    description = f"Acme Test {_PRODUCTS[p]} {_VARIANTS[v]} {_SIZES[s]} {_PACKS[k]}"
    parts = ("AT", f"{p:02d}{v:02d}", f"{s:02d}{k:02d}")
    return ("-" if dashes else "").join(parts), description, "CS"


def catalog(seed: int, tenant_number: int, count: int) -> Iterator[Item]:
    """`count` items for one tenant, every SKU different, in a fixed order."""
    if count <= 0:
        raise ValueError("a catalog needs at least one item")
    rng = random.Random(f"docflow-bench:{seed}:{tenant_number}")
    spelled_out = tenant_number % 2 == 0
    dashes = tenant_number % 3 != 0
    fasteners, general, extras = _split(count)

    made: list[tuple[str, str, str]] = [
        _fastener(i, spelled_out, dashes) for i in rng.sample(range(FASTENER_COMBINATIONS), fasteners)
    ]
    made += [_general(i, dashes) for i in rng.sample(range(GENERAL_COMBINATIONS), general)]
    for n, source in enumerate(rng.sample(range(len(made)), extras)):
        _sku, description, uom = made[source]
        made.append((f"ALT{'-' if dashes else ''}{n:06d}", description, uom))
    rng.shuffle(made)

    for sku, description, uom in made:
        barcode = f"{rng.randrange(10**11, 10**12)}" if rng.random() < 0.3 else None
        external_id = f"V{rng.randrange(10**6, 10**7)}" if rng.random() < 0.15 else None
        raw = {
            "Item Number": sku,
            "Description": description,
            "UOM": uom,
            "Product Group": description.split(" x ")[0] if " x " in description else "General",
            "List Price": f"{rng.randrange(5, 250000) / 100:.2f}",
        }
        if barcode:
            raw["UPC"] = barcode
        if external_id:
            raw["Vendor Part"] = external_id
        yield Item(sku, description, uom, barcode, external_id, raw)


def parse_mix(text: str) -> list[int]:
    """`250000,100000x2,50000x7` -> [250000, 100000, 100000, 50000 x 7]:
    one catalog size per tenant, largest first as written."""
    sizes: list[int] = []
    for part in text.replace(" ", "").split(","):
        size, repeated, times = part.partition("x")
        if not size.isdigit() or (repeated and not times.isdigit()):
            raise ValueError(f"can't read {part!r} in the mix {text!r}: write sizes as 250000 or 50000x7")
        if int(size) <= 0 or (repeated and int(times) <= 0):
            raise ValueError(f"{part!r} in the mix {text!r} is not a positive size")
        if int(size) > MAX_ITEMS_PER_TENANT:
            raise ValueError(
                f"{size} items is more than one tenant's catalog can hold ({MAX_ITEMS_PER_TENANT})"
            )
        sizes += [int(size)] * (int(times) if repeated else 1)
    if not sizes:
        raise ValueError("the mix is empty")
    return sizes
