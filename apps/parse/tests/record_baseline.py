"""
Test D2's baseline (Stage 3c): what TODAY's parser -- the worker's code,
before it moves into the parse service -- answers for every committed
fixture. Run once, from `apps/worker`, before the move:

    .venv/Scripts/python.exe ../parse/tests/record_baseline.py

and commit `fixtures/baseline.json`. After the move, the service must give
the same answer for every fixture (tests/test_parity.py).

What is recorded per fixture:
- documents: the detected file type, then either the rejection code, or
  every part sent to the model (text in full; a PDF or image part as its
  media type, byte length and SHA-256) and the image preview, if any;
- tables: the columns, rows and header row number, or the rejection code.

Recorded on this machine (Windows, the worker's pinned libraries,
LibreOffice from the Windows install); see test_parity.py for what is
compared exactly where.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE.parents[1] / "worker"
sys.path.insert(0, str(WORKER))

from app.conversion import ConversionError, prepare_artifacts  # noqa: E402
from app.tasks.parse_and_extract import _artifact_parts  # noqa: E402
from docflow_core import file_types, previews  # noqa: E402
from docflow_core.catalog_parsing import ImportParseError, parse_table  # noqa: E402


def _binary(media_type: str, data: bytes) -> dict:
    entry = {"media_type": media_type, "length": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if media_type.startswith("image/"):
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            entry["size"] = list(image.size)
    return entry


def _part(part: dict) -> dict:
    if part["type"] == "text":
        return {"type": "text", "text": part["text"]}
    source = part["source"]
    data = base64.standard_b64decode(source["data"])
    return {"type": part["type"], **_binary(source["media_type"], data)}


def document(content: bytes, filename: str) -> dict:
    validation = file_types.validate_upload(content, filename)
    if not validation.ok:
        return {"outcome": "rejected", "code": validation.error_code}
    name = validation.file_type.name
    try:
        parts = _artifact_parts(prepare_artifacts(validation.file_type, content))
    except ConversionError as exc:
        return {"outcome": "rejected", "code": exc.error_code, "file_type": name.value}
    preview = None
    if previews.needs_preview(name) and previews.is_image_like(name):
        built = previews.build_preview(content, name)
        if built is not None:
            preview = {"kind": built.kind, **_binary(built.media_type, built.content)}
    return {
        "outcome": "ok",
        "file_type": name.value,
        "parts": [_part(p) for p in parts],
        "image_preview": preview,
    }


def table(content: bytes, filename: str) -> dict:
    validation = file_types.validate_upload(content, filename)
    if not validation.ok:
        return {"outcome": "rejected", "code": validation.error_code}
    try:
        parsed = parse_table(content, validation.file_type.name.value)
    except ImportParseError as exc:
        return {"outcome": "rejected", "code": exc.code}
    return {
        "outcome": "ok",
        "columns": parsed.columns,
        "rows": parsed.rows,
        "header_row_number": parsed.header_row_number,
    }


def main() -> None:
    fixtures = HERE / "fixtures"
    out: dict[str, dict] = {"documents": {}, "tables": {}, "inputs": {}}
    for folder in ("positive", "hostile"):
        for path in sorted((fixtures / folder).iterdir()):
            content = path.read_bytes()
            out["inputs"][f"{folder}/{path.name}"] = hashlib.sha256(content).hexdigest()
            out["documents"][f"{folder}/{path.name}"] = document(content, path.name)
    for path in sorted((fixtures / "tables").iterdir()):
        content = path.read_bytes()
        out["inputs"][f"tables/{path.name}"] = hashlib.sha256(content).hexdigest()
        out["tables"][f"tables/{path.name}"] = table(content, path.name)
    target = fixtures / "baseline.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for key, value in out["documents"].items():
        print(f"{key:32} {value['outcome']:9} {value.get('code') or value.get('file_type')}")
    for key, value in out["tables"].items():
        print(f"{key:32} {value['outcome']:9} {value.get('code') or len(value['rows'])}")


if __name__ == "__main__":
    main()
