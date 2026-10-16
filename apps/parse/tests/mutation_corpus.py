"""
The mutation corpus (Stage 4, part B2; D-203, PR 1b-i).

Every committed fixture -- the 7.11 hostile set, the Tier 1 and Tier 2
positive set and the catalog tables -- broken in fixed ways: cut short at
five points, and with bytes flipped four times (twice in the first
kilobyte, where the headers are; twice anywhere). Generated at test time
from a fixed seed, so the same bytes every run and nothing extra committed.

What it is for: the exception types each parser raises on a malformed
file. Those are the expected ones, which keep their catalog code (DOC-005,
DOC-017, IMP-004). Anything else from a parser is ours: the job answers
`error` and the document fails with DOC-030. The corpus makes an unseen
type rare; when one does appear, the direction is safe (it pages the
founder, it never blames the customer's file).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SEED = "docflow-mutation-corpus-v1"
TRUNCATE_AT = (0.05, 0.25, 0.5, 0.75, 0.95)
HEADER_BYTES = 1024
HEADER_FLIPS = 8
BODY_FLIPS_MIN = 8
BODY_FLIPS_MAX = 64
BODY_BYTES_PER_FLIP = 2000

# The fixture folders and the job kind their files go to.
SOURCES = (("positive", "document"), ("hostile", "document"), ("tables", "table"))


@dataclass(frozen=True)
class Case:
    case_id: str  # "<folder>/<file>:<mutation>", stable across runs
    kind: str  # document | table
    extension: str
    content: bytes


def _flip(content: bytes, rng: random.Random, count: int, within: int) -> bytes:
    out = bytearray(content)
    for _ in range(count):
        position = rng.randrange(within)
        out[position] ^= rng.randrange(1, 256)  # never a no-op
    return bytes(out)


def mutations(name: str, content: bytes) -> list[tuple[str, bytes]]:
    """The nine broken copies of one file, in a fixed order."""
    out: list[tuple[str, bytes]] = []
    for fraction in TRUNCATE_AT:
        out.append((f"cut{int(fraction * 100):02d}", content[: max(1, int(len(content) * fraction))]))
    body_flips = min(BODY_FLIPS_MAX, max(BODY_FLIPS_MIN, len(content) // BODY_BYTES_PER_FLIP))
    for index in range(2):
        rng = random.Random(f"{SEED}:{name}:head{index}")
        out.append((f"head{index}", _flip(content, rng, HEADER_FLIPS, min(len(content), HEADER_BYTES))))
    for index in range(2):
        rng = random.Random(f"{SEED}:{name}:body{index}")
        out.append((f"body{index}", _flip(content, rng, body_flips, len(content))))
    return out


def cases() -> list[Case]:
    found: list[Case] = []
    for folder, kind in SOURCES:
        for path in sorted((FIXTURES / folder).iterdir()):
            if not path.is_file():
                continue
            name = f"{folder}/{path.name}"
            for label, content in mutations(name, path.read_bytes()):
                found.append(Case(f"{name}:{label}", kind, path.suffix.lower(), content))
    return found
