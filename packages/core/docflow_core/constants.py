"""
Every lifecycle, alerting, allowance and abuse threshold, in one place
(CLAUDE.md Section 7.15.4: "Configuration constants, not literals -- all in
one place, documented in RUNBOOK.md"). Values are the build prompt's
defaults; the ToS-derived ones are placeholders until an attorney sets them.

Nothing else in the codebase may hardcode one of these numbers (Section 10).
Every lifecycle event records `constants_in_effect()` for the values it used,
so changing a number later never rewrites history.
"""

from __future__ import annotations

from typing import Any

# ── Lifecycle (Section 7.14 / 7.15.4) ───────────────────────────────────────
CURE_PERIOD_DAYS = 15  # ToS placeholder
EXPORT_WINDOW_DAYS = 30  # ToS placeholder
REMINDER_DAYS = (1, 15, 25)

# ── Alerting and the Console (Section 7.15.3) ───────────────────────────────
REVIEW_BACKLOG_ALERT_DAYS = 3
CONFIDENCE_DRIFT_MARGIN = 0.05
ABANDONED_REVIEW_CEILING_MIN = 30
STUCK_PROCESSING_TIMEOUT_MIN = 30
STAGING_TTL_DAYS = 90
FIRST_WEEK_CHECKIN_DAYS = 7
ROLLUP_STALE_HOURS = 36

# ── Limits and abuse (Section 7.16) ─────────────────────────────────────────
ABUSE_CEILING_MULTIPLIER = 3
MAX_ATTACHMENTS_PER_EMAIL = 10
UNKNOWN_SENDER_HOURLY_LIMIT = 20
QUARANTINE_TTL_DAYS = 30
ROTATED_ADDRESS_GRACE_DAYS = 30
ALLOWANCE_THRESHOLDS = (0.8, 1.0)

# ── Billing (slice 5.3, D-113) ──────────────────────────────────────────────
# Stripe invoices go to the customer with this many days to pay. Not named by
# the build prompt; a founder decision, kept here with the rest.
INVOICE_DAYS_UNTIL_DUE = 14


def constants_in_effect(*names: str) -> dict[str, Any]:
    """The named constants' current values, for a lifecycle event's
    `constants_in_effect` column."""
    values = globals()
    return {name: values[name] for name in names}
