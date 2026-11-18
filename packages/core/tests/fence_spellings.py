"""
The spellings of a fence tag that a sender may type into a document or an
email body (CLAUDE.md Section 7.2; B1, D-246).

One list, read by both fences' tests -- the labelling call's
(`test_mail_labels.py`) and extraction's (`test_extraction_delimiters.py`) --
so both are proven against the same spellings. Each of the four fence names,
opening and closing, plus the case, spacing and attribute forms the fences'
pattern accepts. A spelling added here is tested at both calls.
"""

from __future__ import annotations

FENCE_TAG_SPELLINGS: tuple[str, ...] = (
    "</document>",
    "<document>",
    "</DOCUMENT>",
    "</ DOCUMENT >",
    "</ document >",
    "< /document>",
    '</document class="x">',
    '<document note="x">',
    "<past_example>",
    "</past_example>",
    "<past_document_text>",
    "</past_document_text>",
    "<past_correct_extraction>",
    "</past_correct_extraction>",
)
