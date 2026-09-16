"""
DocFlow — PO extraction test script (validation stage, Days 1-30)

Drop purchase orders into ./inbox, run this script, and get:
  - ./output/<filename>.json   structured data per document
  - ./output/extracted.csv     one row per line item, across all documents
  - ./output/review_needed.txt list of docs/fields that need a human look

This is deliberately a single file with no database and no web app.
It exists to prove extraction works on real documents before anything gets built.
"""

import base64
import csv
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic

# --------------------------------------------------------------------------
# 1. THE FIELD SCHEMA — this is the part you edit per customer.
#    Describe each field in plain language. No code changes needed.
# --------------------------------------------------------------------------

SCHEMA = {
    "header_fields": [
        {"name": "po_number", "type": "text",
         "description": "The purchase order number or PO reference number"},
        {"name": "order_date", "type": "date",
         "description": "The date the PO was issued. Normalize to YYYY-MM-DD."},
        {"name": "requested_delivery_date", "type": "date",
         "description": "Requested delivery/ship date if stated. Normalize to YYYY-MM-DD."},
        {"name": "buyer_name", "type": "text",
         "description": "The company placing the order (the customer/buyer)"},
        {"name": "buyer_contact_email", "type": "text",
         "description": "Email address of the person who placed the order, if present"},
        {"name": "ship_to_address", "type": "text",
         "description": "Full shipping/delivery address as one line"},
        {"name": "payment_terms", "type": "text",
         "description": "Payment terms such as Net 30, COD, etc."},
        {"name": "order_total", "type": "currency",
         "description": "Grand total of the order. Digits and decimal point only, no currency symbol."},
        {"name": "currency", "type": "text",
         "description": "Currency code such as USD, CAD, EUR. Infer from symbols if not stated."},
        {"name": "notes", "type": "text",
         "description": "Special instructions, delivery notes, or anything the warehouse needs to know"},
    ],
    "line_item_fields": [
        {"name": "sku", "type": "text",
         "description": "Product code, SKU, item number, or catalog number"},
        {"name": "description", "type": "text", "description": "Product description"},
        {"name": "quantity", "type": "number", "description": "Quantity ordered, numeric only"},
        {"name": "unit", "type": "text",
         "description": "Unit of measure such as EA, CS, BOX, LB, if stated"},
        {"name": "unit_price", "type": "currency",
         "description": "Price per unit. Digits and decimal point only."},
        {"name": "line_total", "type": "currency",
         "description": "Total for this line. Digits and decimal point only."},
    ],
}

MODEL = "claude-sonnet-5"       # accuracy-first for testing; see notes at bottom
CONFIDENCE_THRESHOLD = 0.80     # anything below this lands in review_needed.txt

INBOX = Path("inbox")
OUTPUT = Path("output")


# --------------------------------------------------------------------------
# 2. Reading documents
# --------------------------------------------------------------------------

def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_pdf_text(path: Path) -> str:
    """Extract text from a native (non-scanned) PDF."""
    try:
        import pdfplumber
    except ImportError:
        print("  ! pdfplumber not installed. Run: pip install pdfplumber")
        return ""
    chunks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def build_message_content(path: Path):
    """
    Returns the 'content' list for the API call.
    Native-text PDFs and text files are sent as text.
    Images and text-less PDFs are sent to Claude directly so it can read them visually.
    """
    suffix = path.suffix.lower()

    if suffix in {".txt", ".eml", ".html", ".htm", ".csv", ".md"}:
        return [{"type": "text", "text": read_text_file(path)}], "text"

    if suffix == ".pdf":
        text = read_pdf_text(path)
        if len(text) > 100:
            return [{"type": "text", "text": text}], "text"
        # Scanned PDF with no extractable text — send the file itself
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        return [{
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }], "pdf-visual"

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "webp": "image/webp", "gif": "image/gif"}[suffix.lstrip(".")]
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        return [{
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": data},
        }], "image"

    return None, "unsupported"


# --------------------------------------------------------------------------
# 3. The extraction prompt
# --------------------------------------------------------------------------

def build_prompt() -> str:
    header = "\n".join(
        f'  - {f["name"]} ({f["type"]}): {f["description"]}'
        for f in SCHEMA["header_fields"]
    )
    lines = "\n".join(
        f'  - {f["name"]} ({f["type"]}): {f["description"]}'
        for f in SCHEMA["line_item_fields"]
    )
    header_keys = [f["name"] for f in SCHEMA["header_fields"]]
    line_keys = [f["name"] for f in SCHEMA["line_item_fields"]]

    return f"""You are extracting structured data from a purchase order for a wholesale distributor.

Extract these order-level fields:
{header}

Extract every line item in the order, each with these fields:
{lines}

Rules:
- If a field is genuinely not present in the document, use null. Never guess or invent a value.
- Normalize all dates to YYYY-MM-DD.
- For currency and number fields, return digits and a decimal point only (no symbols, no thousands separators).
- Capture EVERY line item, including ones that continue onto another page.
- For each field, give a confidence between 0.0 and 1.0 reflecting how certain you are the value is correct and unambiguous.

Respond with ONLY a JSON object in exactly this shape, and nothing else — no preamble, no markdown fences:

{{
  "header": {{ {", ".join(f'"{k}": <value or null>' for k in header_keys)} }},
  "header_confidence": {{ {", ".join(f'"{k}": <0.0-1.0>' for k in header_keys)} }},
  "line_items": [
    {{ {", ".join(f'"{k}": <value or null>' for k in line_keys)}, "confidence": <0.0-1.0> }}
  ],
  "document_notes": "<anything unusual about this document a human should know, or null>"
}}"""


def extract(client: Anthropic, path: Path) -> dict | None:
    content, mode = build_message_content(path)
    if content is None:
        print(f"  ! Skipping unsupported file type: {path.name}")
        return None
    if mode == "text" and not content[0].get("text", "").strip():
        print(f"  ! No readable text found in {path.name}")
        return None

    print(f"  · Reading as: {mode}")
    message = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": content + [{"type": "text", "text": build_prompt()}],
        }],
    )

    raw = "".join(b.text for b in message.content if b.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ! Could not parse the model's response as JSON: {e}")
        (OUTPUT / f"{path.stem}.RAW.txt").write_text(raw, encoding="utf-8")
        print(f"  ! Raw response saved to output/{path.stem}.RAW.txt for inspection")
        return None

    usage = message.usage
    result["_meta"] = {
        "source_file": path.name,
        "read_mode": mode,
        "model": MODEL,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "est_cost_usd": round(usage.input_tokens / 1e6 * 3 + usage.output_tokens / 1e6 * 15, 4),
    }
    return result


# --------------------------------------------------------------------------
# 4. Outputs: JSON per doc, one combined CSV, and a review list
# --------------------------------------------------------------------------

def collect_review_items(result: dict) -> list[str]:
    flags = []
    src = result["_meta"]["source_file"]
    for key, conf in (result.get("header_confidence") or {}).items():
        if isinstance(conf, (int, float)) and conf < CONFIDENCE_THRESHOLD:
            val = (result.get("header") or {}).get(key)
            flags.append(f"{src} | header.{key} = {val!r} (confidence {conf:.2f})")
    for i, item in enumerate(result.get("line_items") or [], start=1):
        conf = item.get("confidence")
        if isinstance(conf, (int, float)) and conf < CONFIDENCE_THRESHOLD:
            flags.append(f"{src} | line {i}: {item.get('description')!r} (confidence {conf:.2f})")
    if result.get("document_notes"):
        flags.append(f"{src} | note: {result['document_notes']}")
    return flags


def write_csv(results: list[dict], path: Path):
    header_keys = [f["name"] for f in SCHEMA["header_fields"]]
    line_keys = [f["name"] for f in SCHEMA["line_item_fields"]]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_file"] + header_keys + line_keys)
        for r in results:
            head = [(r.get("header") or {}).get(k, "") for k in header_keys]
            items = r.get("line_items") or [{}]
            for item in items:
                writer.writerow(
                    [r["_meta"]["source_file"]] + head +
                    [item.get(k, "") for k in line_keys]
                )


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. See step 4 of the setup instructions.")

    INBOX.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)

    files = sorted(f for f in INBOX.iterdir() if f.is_file() and not f.name.startswith("."))
    if not files:
        sys.exit(f"No files found in {INBOX.resolve()}. Drop some POs in there first.")

    client = Anthropic()
    results, review, total_cost = [], [], 0.0

    print(f"\nFound {len(files)} file(s) in {INBOX}/\n")
    for f in files:
        print(f"→ {f.name}")
        try:
            result = extract(client, f)
        except Exception as e:
            print(f"  ! Failed: {e}")
            continue
        if not result:
            continue

        results.append(result)
        review.extend(collect_review_items(result))
        total_cost += result["_meta"]["est_cost_usd"]

        (OUTPUT / f"{f.stem}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")

        head = result.get("header") or {}
        n_items = len(result.get("line_items") or [])
        print(f"  ✓ PO {head.get('po_number')} · {head.get('buyer_name')} "
              f"· {n_items} line item(s) · total {head.get('order_total')}")
        print(f"    saved to output/{f.stem}.json "
              f"(~${result['_meta']['est_cost_usd']:.4f})")

    if not results:
        sys.exit("\nNothing was extracted successfully.")

    write_csv(results, OUTPUT / "extracted.csv")
    (OUTPUT / "review_needed.txt").write_text(
        "\n".join(review) if review else "Nothing flagged for review.",
        encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Documents processed : {len(results)}/{len(files)}")
    print(f"Line items extracted: {sum(len(r.get('line_items') or []) for r in results)}")
    print(f"Fields flagged      : {len(review)}  (see output/review_needed.txt)")
    print(f"Estimated API cost  : ${total_cost:.4f}")
    print(f"Combined CSV        : output/extracted.csv")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
