"""
The pure half of the labelling task (B1; docs/designs/b1.md 2.2; D-246):
which part is read, and the cut, the claim, the one call and the decision.
The database half is apps/worker/tests/test_label_mail_body_db.py.

The client here is a STUB: its answers are written in this file, not
recorded from the model. It covers what a recording can't give: the claim
refused, and a text past the budget. Real recordings come with the fixture
emails (slice 4, commit 7; founder, 2026-11-18, gap 5). All data is
fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import pytest

from docflow_core import mail_bodies
from docflow_core.constants import MAIL_LABEL_TEXT_CHAR_BUDGET
from docflow_core.mail_bodies import call_and_decide, choose_read_text, visible_characters
from docflow_core.mail_labels import REASON_NO_ORDER_LINES, REASON_ORDER_LINES, REASON_TEXT_CUT, mail_text
from tests.fence_spellings import FENCE_TAG_SPELLINGS

COVER_NOTE = {
    "label": "not_an_order",
    "label_confidence": 0.99,
    "contains_order_lines": False,
    "order_lines_confidence": 0.99,
    "injection_suspected": False,
}
ORDER_TEXT = "Subject: PO TEST-PO-7\n\n4 x TEST-100"
ORDER = {
    "label": "order",
    "label_confidence": 0.97,
    "contains_order_lines": True,
    "order_lines_confidence": 0.96,
    "injection_suspected": False,
}


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Block:
    type: str
    text: str


@dataclass
class _Message:
    content: list
    usage: _Usage


class StubLabelClient:
    """A STUB, not a recording: answers every label call with `payload`."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []
        self.counted: list[dict] = []
        self.messages = self

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Message([_Block("text", json.dumps(self.payload))], _Usage(1000, 40))

    def count_tokens(self, **kwargs):
        self.counted.append(kwargs)
        return _Usage(990, 0)


def _claims(answer):
    seen: list = []

    def claim(counted):
        seen.append(counted)
        return answer

    return claim, seen


def _sent_text(client: StubLabelClient) -> str:
    sent = client.calls[0]["messages"][0]["content"][0]["text"]
    assert sent.startswith("<document>\n") and sent.endswith("\n</document>")
    return sent[len("<document>\n") : -len("\n</document>")]


# ── which part is read ──────────────────────────────────────────────────────


def test_visible_characters_leave_out_whitespace():
    assert visible_characters(" a b\r\n\tc ") == 3


@pytest.mark.parametrize(
    ("text_part", "html_part", "expected"),
    [
        ("4 x TEST-100", None, ("text", "4 x TEST-100")),
        (None, "4 x TEST-100", ("html", "4 x TEST-100")),
        (None, None, None),
        # A stub text part beside a full HTML order: the HTML part.
        ("View in HTML", "4 x TEST-100 hex bolt, 2 x TEST-200 washer, PO TEST-PO-7", "html"),
        # Exactly half is not less than half: the text part.
        ("abcde", "abcdefghij", "text"),
        ("abcd", "abcdefghij", "html"),
        # A text part as full as the HTML one: the text part.
        ("4 x TEST-100", "4 x TEST-100", "text"),
        # A part that couldn't be read loses to one that could.
        ("", None, ("text", "")),
    ],
)
def test_the_text_part_is_read_unless_it_has_less_than_half_the_html_parts_characters(
    text_part, html_part, expected
):
    chosen = choose_read_text(text_part, html_part)
    if isinstance(expected, str):
        assert chosen is not None and chosen[0] == expected
    else:
        assert chosen == expected


# ── the claim: one call at most ─────────────────────────────────────────────


def test_a_refused_claim_makes_no_call():
    client = StubLabelClient(ORDER)
    claim, seen = _claims(None)
    outcome = call_and_decide(client, ORDER_TEXT, has_attachment=False, claim=claim)
    assert outcome.claimed is False and outcome.result is None and outcome.decision is None
    assert client.calls == []
    assert seen == [990]  # the started row would have carried the counted input


def test_a_claimed_text_is_labelled_once_and_decided():
    client = StubLabelClient(ORDER)
    run_id = uuid4()
    claim, seen = _claims(run_id)
    outcome = call_and_decide(client, ORDER_TEXT, has_attachment=False, claim=claim)
    assert outcome.claimed and outcome.started_run_id == run_id
    assert len(client.calls) == 1 and len(seen) == 1
    assert outcome.decision is not None and outcome.decision.make_document
    assert outcome.decision.reason == REASON_ORDER_LINES


def test_the_count_comes_before_the_claim_and_the_claim_before_the_call():
    order: list[str] = []
    client = StubLabelClient(COVER_NOTE)
    real_count, real_create = client.count_tokens, client.create
    client.count_tokens = lambda **kw: (order.append("count"), real_count(**kw))[1]
    client.create = lambda **kw: (order.append("call"), real_create(**kw))[1]

    def claim(counted):
        order.append("claim")
        return uuid4()

    call_and_decide(client, "Subject: Hello\n\nOur order is attached.", has_attachment=True, claim=claim)
    assert order == ["count", "claim", "call"]


# ── the budget (founder, 2026-11-18, item c) ────────────────────────────────


def test_a_text_one_character_past_the_budget_sends_exactly_the_budget_and_becomes_a_document():
    full = "x" * (MAIL_LABEL_TEXT_CHAR_BUDGET + 1)
    client = StubLabelClient(COVER_NOTE)  # "not an order", 0.99: the cut still wins
    outcome = call_and_decide(client, full, has_attachment=False, claim=_claims(uuid4())[0])
    assert outcome.text_was_cut and outcome.sent_characters == MAIL_LABEL_TEXT_CHAR_BUDGET
    assert _sent_text(client) == "x" * MAIL_LABEL_TEXT_CHAR_BUDGET
    assert outcome.decision is not None and outcome.decision.make_document
    assert outcome.decision.reason == REASON_TEXT_CUT


def test_a_text_at_the_budget_is_sent_whole_and_decided_by_its_label():
    full = "x" * MAIL_LABEL_TEXT_CHAR_BUDGET
    client = StubLabelClient(COVER_NOTE)
    outcome = call_and_decide(client, full, has_attachment=True, claim=_claims(uuid4())[0])
    assert not outcome.text_was_cut and _sent_text(client) == full
    assert outcome.decision is not None and not outcome.decision.make_document
    assert outcome.decision.reason == REASON_NO_ORDER_LINES


def test_the_header_lines_count_toward_the_budget():
    header_and_text = mail_text(
        subject="PO TEST-PO-7",
        sender="buyer@acmetest.example",
        date=None,
        body_text="y" * MAIL_LABEL_TEXT_CHAR_BUDGET,
    )
    client = StubLabelClient(COVER_NOTE)
    outcome = call_and_decide(client, header_and_text, has_attachment=False, claim=_claims(uuid4())[0])
    assert outcome.text_was_cut
    assert _sent_text(client).startswith("Subject: PO TEST-PO-7\nFrom: buyer@acmetest.example\n\n")
    assert len(_sent_text(client)) == MAIL_LABEL_TEXT_CHAR_BUDGET


@pytest.mark.parametrize("tag", FENCE_TAG_SPELLINGS)
@pytest.mark.parametrize("start", range(MAIL_LABEL_TEXT_CHAR_BUDGET - 12, MAIL_LABEL_TEXT_CHAR_BUDGET + 1))
def test_a_fence_tag_straddling_the_cut_leaves_one_envelope_and_nothing_that_reads_as_a_tag(tag, start):
    """Founder, 2026-11-18: a tag that the cut would split (e.g. "</document>"
    starting at character 11,995) must not leave a part-tag at the end of
    the sent text. The tags are rewritten before the cut."""
    full = "x" * start + tag + "y" * 40
    client = StubLabelClient(COVER_NOTE)
    call_and_decide(client, full, has_attachment=False, claim=_claims(uuid4())[0])
    sent = client.calls[0]["messages"][0]["content"][0]["text"]
    assert sent.count("<document>") == 1 and sent.count("</document>") == 1
    inner = _sent_text(client)
    assert "<" not in inner and ">" not in inner
    assert len(inner) <= MAIL_LABEL_TEXT_CHAR_BUDGET


def test_a_from_line_with_angle_brackets_reaches_the_label_request_unchanged():
    """Founder, 2026-11-18: rewriting only touches the fences' own tag names,
    so an address in angle brackets is sent as written."""
    full = mail_text(
        subject="PO TEST-PO-7", sender="Ann <ann@example.com>", date=None, body_text="4 x TEST-100"
    )
    client = StubLabelClient(ORDER)
    call_and_decide(client, full, has_attachment=False, claim=_claims(uuid4())[0])
    assert _sent_text(client) == "Subject: PO TEST-PO-7\nFrom: Ann <ann@example.com>\n\n4 x TEST-100"


# ── the names the database half and the log lines use ───────────────────────


def test_the_no_label_reasons_and_the_ignored_mail_code_are_fixed():
    assert mail_bodies.NO_ORDER_FOUND_CODE == "INT-012"
    assert {
        mail_bodies.NO_LABEL_CALL_FAILED,
        mail_bodies.NO_LABEL_HASH_MISMATCH,
        mail_bodies.NO_LABEL_TEXT_UNREADABLE,
        mail_bodies.NO_LABEL_STUCK_SWEEP,
    } == {"call_failed", "hash_mismatch", "text_unreadable", "stuck_sweep"}
