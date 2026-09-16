"""
DB-free unit tests for docflow_core.email_intake's pure parsing/decision
logic (CLAUDE.md Section 7.16.3): the Postmark payload parsing boundary,
the Authentication-Results regex parser and its DMARC-policy-presence
heuristic (see DECISIONS.md), and evaluate_email's abuse-defense ordering.
Integration tests against the real webhook + database live in
apps/api/tests/test_email_intake.py.
"""

from __future__ import annotations

import base64

import pytest

from docflow_core.email_intake import (
    MAX_ATTACHMENTS_PER_EMAIL,
    UNKNOWN_SENDER_HOURLY_LIMIT,
    AuthResult,
    MalformedPayloadError,
    evaluate_email,
    parse_authentication_results,
    parse_postmark_payload,
    sender_domain_of,
    should_quarantine_for_auth,
)


def _pm_attachment(name: str, content: bytes) -> dict:
    return {
        "Name": name,
        "Content": base64.b64encode(content).decode("ascii"),
        "ContentType": "text/plain",
        "ContentLength": len(content),
    }


# ── parse_postmark_payload ───────────────────────────────────────────────


def test_parses_minimal_valid_payload():
    payload = {
        "From": "buyer@example.test",
        "FromFull": {"Email": "buyer@example.test", "Name": "A Buyer"},
        "Subject": "PO 123",
        "MessageID": "<abc@mail.example.test>",
        "Headers": [{"Name": "X-Foo", "Value": "bar"}],
        "Attachments": [_pm_attachment("po.txt", b"hello")],
    }
    parsed = parse_postmark_payload(payload)
    assert parsed.sender_email == "buyer@example.test"
    assert parsed.subject == "PO 123"
    assert parsed.message_id == "<abc@mail.example.test>"
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "po.txt"
    assert parsed.attachments[0].content == b"hello"


def test_falls_back_to_from_field_when_fromfull_missing():
    payload = {"From": "Display Name <buyer@example.test>", "Attachments": []}
    parsed = parse_postmark_payload(payload)
    assert parsed.sender_email == "buyer@example.test"


def test_missing_sender_raises_malformed_payload_error():
    with pytest.raises(MalformedPayloadError):
        parse_postmark_payload({"Attachments": []})


def test_non_dict_payload_raises_malformed_payload_error():
    with pytest.raises(MalformedPayloadError):
        parse_postmark_payload(["not", "a", "dict"])  # type: ignore[arg-type]


def test_invalid_base64_attachment_raises_malformed_payload_error():
    payload = {
        "From": "buyer@example.test",
        "Attachments": [{"Name": "bad.txt", "Content": "not-valid-base64!!!", "ContentType": "text/plain"}],
    }
    with pytest.raises(MalformedPayloadError):
        parse_postmark_payload(payload)


def test_no_attachments_key_defaults_to_empty_list():
    parsed = parse_postmark_payload({"From": "buyer@example.test"})
    assert parsed.attachments == []


def test_sender_domain_of():
    assert sender_domain_of("buyer@example.test") == "example.test"
    assert sender_domain_of("not-an-email") == ""


# ── parse_authentication_results / should_quarantine_for_auth ────────────


def test_parses_all_three_verdicts():
    headers = [
        {
            "Name": "Authentication-Results",
            "Value": (
                "mx.example.com; spf=pass smtp.mailfrom=x@example.test; "
                "dkim=pass header.i=@example.test; dmarc=pass"
            ),
        }
    ]
    auth = parse_authentication_results(headers)
    assert auth == AuthResult(spf="pass", dkim="pass", dmarc="pass")


def test_missing_authentication_results_header_yields_all_none():
    auth = parse_authentication_results([{"Name": "X-Other", "Value": "irrelevant"}])
    assert auth == AuthResult(spf=None, dkim=None, dmarc=None)


def test_dmarc_fail_triggers_quarantine():
    auth = AuthResult(spf="pass", dkim="pass", dmarc="fail")
    assert should_quarantine_for_auth(auth) is True


def test_spf_fail_with_dmarc_present_triggers_quarantine():
    # dmarc=pass still counts as "this domain has a DMARC policy" for the
    # presence heuristic (DECISIONS.md) -- an SPF fail alongside any dmarc=
    # verdict at all is treated as suspicious.
    auth = AuthResult(spf="fail", dkim=None, dmarc="pass")
    assert should_quarantine_for_auth(auth) is True


def test_spf_fail_with_no_dmarc_verdict_present_does_not_quarantine():
    auth = AuthResult(spf="fail", dkim=None, dmarc=None)
    assert should_quarantine_for_auth(auth) is False


def test_all_none_does_not_quarantine():
    assert should_quarantine_for_auth(AuthResult(spf=None, dkim=None, dmarc=None)) is False


def test_all_pass_does_not_quarantine():
    assert should_quarantine_for_auth(AuthResult(spf="pass", dkim="pass", dmarc="pass")) is False


# ── evaluate_email ─────────────────────────────────────────────────────────

_NO_AUTH = AuthResult(spf=None, dkim=None, dmarc=None)


def test_zero_attachments_is_reject_no_attachment():
    decision = evaluate_email(0, _NO_AUTH, sender_known=True, unknown_sender_count_last_hour=0)
    assert decision.reject_no_attachment is True
    assert decision.quarantine_reason is None


def test_over_attachment_cap_quarantines_regardless_of_other_signals():
    decision = evaluate_email(
        MAX_ATTACHMENTS_PER_EMAIL + 1, _NO_AUTH, sender_known=True, unknown_sender_count_last_hour=0
    )
    assert decision.reject_no_attachment is False
    assert decision.quarantine_reason == "attachment_cap"


def test_at_attachment_cap_is_not_over_cap():
    decision = evaluate_email(
        MAX_ATTACHMENTS_PER_EMAIL, _NO_AUTH, sender_known=True, unknown_sender_count_last_hour=0
    )
    assert decision.quarantine_reason is None


def test_auth_fail_quarantines_even_for_a_known_sender():
    dmarc_fail = AuthResult(spf=None, dkim=None, dmarc="fail")
    decision = evaluate_email(1, dmarc_fail, sender_known=True, unknown_sender_count_last_hour=0)
    assert decision.quarantine_reason == "auth_fail"


def test_unknown_sender_over_limit_quarantines():
    decision = evaluate_email(
        1, _NO_AUTH, sender_known=False, unknown_sender_count_last_hour=UNKNOWN_SENDER_HOURLY_LIMIT
    )
    assert decision.quarantine_reason == "unknown_sender_velocity"


def test_unknown_sender_under_limit_is_not_quarantined():
    decision = evaluate_email(
        1, _NO_AUTH, sender_known=False, unknown_sender_count_last_hour=UNKNOWN_SENDER_HOURLY_LIMIT - 1
    )
    assert decision.quarantine_reason is None


def test_known_sender_never_affected_by_velocity_regardless_of_count():
    decision = evaluate_email(
        1, _NO_AUTH, sender_known=True, unknown_sender_count_last_hour=UNKNOWN_SENDER_HOURLY_LIMIT + 100
    )
    assert decision.quarantine_reason is None
