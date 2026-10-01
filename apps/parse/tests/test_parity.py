"""
Tests D1, D2, D3, C1 and C2 of the 3c test table.

D2 -- the same answer as before the move: `fixtures/baseline.json` was
recorded by the worker's own code before anything moved
(tests/record_baseline.py, commit b819e8b). Every fixture must give the same
outcome, file type and parts:
- text parts: identical, character for character, everywhere;
- an original passed through unchanged (a PDF or image sent to the model
  as it arrived): identical bytes (SHA-256), everywhere;
- an image the parser re-encodes (TIFF and HEIC pages, the image preview):
  identical bytes where the baseline was recorded (this machine);
  elsewhere, with PARITY_REENCODED=size, the same media type and pixel size.
  The encoder library's build can differ by platform; the image is the
  same page.

D1 -- a real PO in every Tier 1 and Tier 2 format parses: the positive set,
under the production limits when run against the service.
C1/C2 -- the hostile set gets exactly the catalog code it got before, and
against the service a known-good PO parses right after each one.
D3 -- catalog tables give the same rows.
"""

from __future__ import annotations

import hashlib
import io
import os

import pytest

from tests.conftest import FIXTURES, answer, decoded, via_http

REENCODED_MODE = os.environ.get("PARITY_REENCODED", "exact")
REENCODING_TYPES = {"tiff", "heic"}

POSITIVE = sorted(p.name for p in (FIXTURES / "positive").iterdir())
HOSTILE = sorted(p.name for p in (FIXTURES / "hostile").iterdir())
TABLES = sorted(p.name for p in (FIXTURES / "tables").iterdir())


def _size(data: bytes) -> list[int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return list(image.size)


def test_the_fixtures_are_the_ones_the_baseline_was_recorded_from(baseline):
    for key, expected in baseline["inputs"].items():
        assert hashlib.sha256((FIXTURES / key).read_bytes()).hexdigest() == expected, key
    assert len(baseline["inputs"]) == len(POSITIVE) + len(HOSTILE) + len(TABLES)


def _compare_binary(name: str, got: bytes, media_type: str, want: dict, reencoded: bool) -> None:
    assert media_type == want["media_type"], name
    if reencoded and REENCODED_MODE == "size":
        assert _size(got) == want["size"], name
        return
    assert len(got) == want["length"], name
    assert hashlib.sha256(got).hexdigest() == want["sha256"], name


@pytest.mark.parametrize("name", POSITIVE)
def test_every_format_parses_and_matches_the_baseline(name, baseline):
    want = baseline["documents"][f"positive/{name}"]
    got = answer("document", FIXTURES / "positive" / name)
    assert got["outcome"] == want["outcome"] == "ok", (name, got.get("outcome"), got.get("code"), got.get("cause"))
    assert got["file_type"] == want["file_type"]
    assert len(got["parts"]) == len(want["parts"]), name
    reencoded = want["file_type"] in REENCODING_TYPES
    for index, (part, expected) in enumerate(zip(got["parts"], want["parts"])):
        assert part["type"] == expected["type"], (name, index)
        if part["type"] == "text":
            assert part["text"] == expected["text"], (name, index)
        else:
            _compare_binary(f"{name}[{index}]", decoded(part), part["source"]["media_type"], expected, reencoded)
    if want["image_preview"] is None:
        assert got["image_preview"] is None, name
    else:
        import base64

        preview = got["image_preview"]
        assert preview["kind"] == want["image_preview"]["kind"]
        _compare_binary(
            f"{name}[preview]",
            base64.standard_b64decode(preview["data"]),
            preview["media_type"],
            want["image_preview"],
            reencoded=True,
        )


@pytest.mark.parametrize("name", HOSTILE)
def test_every_hostile_file_gets_the_code_it_got_before(name, baseline):
    want = baseline["documents"][f"hostile/{name}"]
    got = answer("document", FIXTURES / "hostile" / name)
    assert (got.get("outcome"), got.get("code")) == (want["outcome"], want["code"]), (name, got)
    if via_http():
        # The service is healthy afterwards: a known-good PO parses at once.
        follow = answer("document", FIXTURES / "positive" / "po.txt")
        assert follow["outcome"] == "ok"


@pytest.mark.parametrize("name", TABLES)
def test_every_catalog_table_gives_the_same_rows(name, baseline):
    want = baseline["tables"][f"tables/{name}"]
    got = answer("table", FIXTURES / "tables" / name)
    assert got == want, name
