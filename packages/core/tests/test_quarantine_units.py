"""
DB-free tests for the pure parts of slice 5.7 (CLAUDE.md Section 7.16; D-126):
the intake decision order with the new ceilings and allowlist, who may release
what, the allowance wording, the constants, and the email templates. The
behaviour against the real database is in apps/api/tests/test_quarantine_api.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from docflow_core import allowance, constants, email_outbox, quarantine, usage
from docflow_core.email_intake import (
    AuthResult,
    evaluate_email,
    sender_is_allowed,
)
from docflow_core.errors import CATALOG, get_error, render_error

CLEAN = AuthResult(spf="pass", dkim="pass", dmarc="pass")


# ── the order of the intake checks ──────────────────────────────────────────


def test_a_ceiling_outranks_every_other_reason():
    for ceiling in ("abuse_ceiling", "cost_breaker"):
        decision = evaluate_email(11, AuthResult("fail", "fail", "fail"), False, 99, ceiling, False)
        assert decision.quarantine_reason == ceiling


def test_the_rest_of_the_order_is_unchanged_and_the_allowlist_slots_in_before_velocity():
    assert evaluate_email(11, CLEAN, True, 0).quarantine_reason == "attachment_cap"
    assert evaluate_email(1, AuthResult("pass", "pass", "fail"), True, 0).quarantine_reason == "auth_fail"
    assert evaluate_email(1, CLEAN, False, 99, None, False).quarantine_reason == "sender_not_allowed"
    assert evaluate_email(1, CLEAN, False, 20, None, True).quarantine_reason == "unknown_sender_velocity"
    assert (
        evaluate_email(1, CLEAN, True, 500).quarantine_reason is None
    )  # a known sender is never velocity-held


def test_an_email_with_no_attachment_is_rejected_even_while_a_ceiling_is_tripped():
    assert evaluate_email(0, CLEAN, False, 0, "abuse_ceiling").reject_no_attachment is True


def test_the_allowlist_matches_an_address_or_a_domain_case_insensitively():
    listed = ["Buyer@Example.test", "trusted.example.test"]
    assert sender_is_allowed("buyer@example.test", listed)
    assert sender_is_allowed("ap@trusted.example.test", listed)
    assert not sender_is_allowed("other@example.test", listed)
    assert not sender_is_allowed("", listed)
    assert not sender_is_allowed("x@sub.trusted.example.test", listed)  # exact domain, not a suffix


# ── who may release what ────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["attachment_cap", "unknown_sender_velocity", "sender_not_allowed"])
def test_the_owner_and_admin_may_release_their_own_calls(reason):
    assert quarantine.can_release("owner", reason) and quarantine.can_release("admin", reason)


@pytest.mark.parametrize("reason", ["abuse_ceiling", "cost_breaker", "auth_fail", "manual"])
def test_only_the_founder_may_release_the_rest(reason):
    for role in ("owner", "admin", "reviewer", "viewer"):
        assert not quarantine.can_release(role, reason)
    assert quarantine.can_release(None, reason)  # None is the founder in the Console


def test_a_reviewer_or_viewer_can_release_nothing():
    for reason in quarantine.TENANT_RELEASABLE:
        assert not quarantine.can_release("reviewer", reason)
        assert not quarantine.can_release("viewer", reason)


def test_every_reason_the_database_allows_has_plain_english():
    import re
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[3] / "supabase" / "migrations" / "0021_allowance_quarantine.sql"
    ).read_text()
    block = sql.split("check (quarantine_reason in")[1].split(");")[0]
    reasons = re.findall(r"'([a-z_]+)'", block)
    assert len(reasons) == 7
    for reason in reasons:
        assert quarantine.reason_entry(reason).message


# ── the constants ───────────────────────────────────────────────────────────


def test_the_daily_ceiling_is_fifty_dollars_and_money_is_never_a_float():
    assert constants.DAILY_AI_COST_CEILING_USD == Decimal("50")
    assert isinstance(constants.DAILY_AI_COST_CEILING_USD, Decimal)
    assert constants.ABUSE_CEILING_MULTIPLIER == 3
    assert constants.ALLOWANCE_THRESHOLDS == (0.8, 1.0)


# ── the allowance wording comes from the catalog ────────────────────────────


def _a(used: int, nxt: bool) -> usage.Allowance:
    return usage.Allowance(
        used=used,
        allowance=300,
        tier_name="Starter",
        next_tier_name="Growth" if nxt else None,
        next_tier_allowance=1000 if nxt else None,
        month="2026-09",
    )


def test_the_100_percent_wording_names_the_numbers_the_tier_and_the_next_tier():
    entry = allowance._entry(_a(312, True), 100)
    assert entry.code == "LIM-002"
    assert "312 of 300" in entry.message and "Starter" in entry.message
    assert "Documents continue to process normally" in entry.message
    assert "Growth includes 1,000 documents per month" in entry.action


def test_the_top_tier_has_nothing_to_upgrade_to_so_it_gets_its_own_wording():
    assert allowance._entry(_a(250, False), 80).code == "LIM-003"
    assert allowance._entry(_a(312, False), 100).code == "LIM-004"
    assert "upgrade" not in allowance._entry(_a(312, False), 100).action.lower()


def test_rendering_fills_every_placeholder_and_leaves_none_behind():
    for code in ("LIM-001", "LIM-002", "LIM-003", "LIM-004", "INT-008"):
        rendered = render_error(
            code,
            used="1",
            allowance="2",
            tier="T",
            next_tier="N",
            next_allowance="3",
            tenant_name="Acme Test",
        )
        assert "{" not in rendered.message + rendered.action + rendered.title, code


def test_the_raw_catalog_keeps_the_placeholders_for_the_snapshot():
    assert "{used}" in get_error("LIM-002").message


# ── the new catalog entries follow the tone rules ───────────────────────────


def test_every_new_entry_says_what_why_and_what_next():
    for code in (
        "LIM-001",
        "LIM-002",
        "LIM-003",
        "LIM-004",
        "INT-007",
        "INT-008",
        "INT-009",
        "QUA-001",
        "QUA-002",
        "QUA-003",
        "QUA-004",
        "QUA-005",
    ):
        entry = CATALOG[code]
        assert entry.message.strip() and entry.action.strip(), code
        assert len(entry.title.split()) <= 8, code


def test_no_customer_sees_which_meter_tripped():
    # The customer needs to know their documents are safe, not what our cost was.
    for reason in ("abuse_ceiling", "cost_breaker"):
        text = quarantine.reason_entry(reason)
        blob = f"{text.title} {text.message} {text.action}".lower()
        assert "$" not in blob and "cost" not in blob and "token" not in blob


# ── the emails ──────────────────────────────────────────────────────────────


def test_the_new_templates_render_and_carry_the_catalog_wording():
    subject, body = email_outbox.render(
        "allowance_notice",
        {"tenant_name": "Acme Test", "title": "T", "message": "You've used 1 of 2.", "action": "Do X."},
    )
    assert subject == "DocFlow: T" and "You've used 1 of 2." in body and "Do X." in body
    _, held = email_outbox.render("intake_held", {"tenant_name": "Acme Test", "message": "Held."})
    assert "Held." in held
    _, changed = email_outbox.render(
        "intake_address_changed", {"tenant_name": "Acme Test", "message": "M", "action": "A"}
    )
    assert "M" in changed and "A" in changed
    _, rotated = email_outbox.render(
        "intake_address_rotated",
        {"tenant_name": "Acme Test", "new_address": "orders+abc@x.test", "grace_ends": "2026-10-23"},
    )
    assert "orders+abc@x.test" in rotated and "2026-10-23" in rotated
