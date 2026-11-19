"""
One `.eml` in, Postmark's inbound-webhook JSON shape out (B1;
docs/designs/b1.md section 9; D-246). A test tool: standard library only,
and never imported by an app.

    python scripts/eml_to_postmark.py message.eml > payload.json

The shape is Postmark's documented inbound payload: `From`, `FromFull`,
`FromName`, `To`, `ToFull`, `Cc`, `CcFull`, `ReplyTo`, `Subject`,
`MessageID`, `Date`, `MailboxHash`, `TextBody`, `HtmlBody`,
`StrippedTextReply`, `Tag`, `Headers` and `Attachments` (each `Name`,
`Content` as base64, `ContentType`, `ContentLength`, `ContentID`).

Two choices a real Postmark payload makes on its own side:
- `MessageID` is the message's own Message-ID without its angle brackets,
  so a fixture's id is fixed. Postmark sends an id of its own there and the
  original in `Headers`; the webhook reads `MessageID` either way.
- `Headers` holds every header except the ones Postmark lifts into fields
  of their own (From, To, Cc, Reply-To, Subject, Date).
"""

from __future__ import annotations

import base64
import json
import sys
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path

# Headers Postmark gives fields of their own, so they are not in `Headers`.
LIFTED_HEADERS = frozenset({"from", "to", "cc", "reply-to", "subject", "date"})


def _addresses(message: EmailMessage, name: str) -> list[dict[str, str]]:
    values = [str(v) for v in message.get_all(name, [])]
    return [
        {"Email": email, "Name": display, "MailboxHash": ""}
        for display, email in getaddresses(values)
        if email
    ]


def _joined(full: list[dict[str, str]]) -> str:
    return ", ".join(f'"{a["Name"]}" <{a["Email"]}>' if a["Name"] else a["Email"] for a in full)


def _body(message: EmailMessage, subtype: str) -> str:
    part = message.get_body(preferencelist=(subtype,))
    if part is None or part.is_attachment():
        return ""
    return str(part.get_content())


def _attachments(message: EmailMessage) -> list[dict[str, object]]:
    found = []
    for part in message.iter_attachments():
        content = part.get_payload(decode=True) or b""
        found.append(
            {
                "Name": part.get_filename() or "attachment",
                "Content": base64.b64encode(content).decode("ascii"),
                "ContentType": part.get_content_type(),
                "ContentLength": len(content),
                "ContentID": str(part.get("Content-ID") or ""),
            }
        )
    return found


def convert(raw: bytes) -> dict[str, object]:
    """The Postmark inbound payload for one message's bytes."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(message, EmailMessage)
    from_full = _addresses(message, "From")
    sender = from_full[0] if from_full else {"Email": "", "Name": "", "MailboxHash": ""}
    to_full = _addresses(message, "To")
    cc_full = _addresses(message, "Cc")
    return {
        "From": sender["Email"],
        "FromFull": sender,
        "FromName": sender["Name"],
        "To": _joined(to_full),
        "ToFull": to_full,
        "Cc": _joined(cc_full),
        "CcFull": cc_full,
        "ReplyTo": str(message.get("Reply-To") or ""),
        "Subject": str(message.get("Subject") or ""),
        "MessageID": str(message.get("Message-ID") or "").strip().strip("<>"),
        "Date": str(message.get("Date") or ""),
        "MailboxHash": "",
        "TextBody": _body(message, "plain"),
        "HtmlBody": _body(message, "html"),
        "StrippedTextReply": "",
        "Tag": "",
        "Headers": [
            {"Name": name, "Value": str(value)}
            for name, value in message.items()
            if name.lower() not in LIFTED_HEADERS
        ],
        "Attachments": _attachments(message),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: eml_to_postmark.py message.eml", file=sys.stderr)
        return 2
    json.dump(convert(Path(argv[1]).read_bytes()), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
