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

from decimal import Decimal
from typing import Any

# ── Lifecycle (Section 7.14 / 7.15.4) ───────────────────────────────────────
# Added on top of the first `past_due` notice before a non-payment
# cancellation's computed effective date. 0 (D-125, 22 Sept 2026): Net terms
# (INVOICE_DAYS_UNTIL_DUE) are themselves the grace period -- Stripe doesn't
# mark a subscription past_due until an invoice is already that many days
# overdue, so a customer already gets the full Net-15 window before this
# clock even starts. Stacking a second cure period on top was the original
# ToS-placeholder default; kept here, at 0, as the one constant to change if
# a future ToS wants a second window.
CURE_PERIOD_DAYS = 0
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
# The "needs review" digest (slice 5.8c, D-131): at most one email per tenant
# per this many minutes, sent this long after the first new order that starts
# it. A 500-document backfill is one or two emails, not 500.
REVIEW_DIGEST_INTERVAL_MIN = 15

# ── Limits and abuse (Section 7.16) ─────────────────────────────────────────
ABUSE_CEILING_MULTIPLIER = 3
MAX_ATTACHMENTS_PER_EMAIL = 10
UNKNOWN_SENDER_HOURLY_LIMIT = 20
QUARANTINE_TTL_DAYS = 30
ROTATED_ADDRESS_GRACE_DAYS = 30
ALLOWANCE_THRESHOLDS = (0.8, 1.0)
# The thresholds above are when the account's admin is *told* -- one email per
# threshold per month, and the founder's sales signal at 100%. The banner on
# the tenant's own screens is held back until this point (D-129): a reviewer
# works the queue all day and does not need the month's running total, only a
# word when the plan is nearly spent. The numbers themselves live on the
# admin's dashboard, where someone who can act on them will look.
ALLOWANCE_BANNER_THRESHOLD = 0.9
# The per-tenant daily AI-spend circuit breaker (Section 7.9, 7.16.2; D-126).
# Estimated model cost for one tenant in one UTC day. Founder-chosen; a heavy
# legitimate day at the Scale tier (~100 documents at up to ~$0.35) is ~$35.
# Money is a Decimal, never a float (Section 7).
DAILY_AI_COST_CEILING_USD = Decimal("50")
# Backstop on the same day's input + output tokens, so a change in model
# pricing can't quietly make the dollar figure meaningless.
DAILY_TOKEN_CEILING = 20_000_000

# ── Approved-example prompting (Section 7.13; slice 5.10, D-141) ────────────
# A buyer needs this many approved (or exported) orders before any of them is
# ever shown to the model as an example. The build prompt's number.
EXAMPLE_MIN_APPROVED_DOCS = 10
# Never more than this many examples in one prompt (Section 10: "more than 3").
EXAMPLE_MAX_PER_PROMPT = 3
# Each example's document text is cut to this many characters (~1,500 tokens)
# before it goes in the prompt -- "truncated to a fixed token budget".
EXAMPLE_TEXT_CHAR_BUDGET = 6000
# The routing pass reads only the top of a text document: the buyer's name is
# in the header, never on page 40.
ROUTING_TEXT_CHAR_BUDGET = 3000
# The routing pass's buyer must be at least this certain, the same bar as the
# review threshold (Section 3: confidence threshold default 0.80).
ROUTING_MIN_CONFIDENCE = 0.80

# ── Billing (slice 5.3, D-113; trial delay D-125) ───────────────────────────
# Stripe invoices go to the customer with this many days to pay (Net 15).
# Not named by the build prompt; a founder decision, kept here with the rest.
INVOICE_DAYS_UNTIL_DUE = 15

# Days after go-live before the first Stripe invoice is generated -- the
# "try it for a week before we bill you" window (D-125). The Stripe
# subscription is created at go-live (status "trialing"); DocFlow itself is
# fully live and usable from day zero, but nothing is billed until this
# trial ends, at which point Stripe combines the first month's charge and
# the setup fee onto one invoice, due INVOICE_DAYS_UNTIL_DUE days later.
TRIAL_PERIOD_DAYS = 7


def constants_in_effect(*names: str) -> dict[str, Any]:
    """The named constants' current values, for a lifecycle event's
    `constants_in_effect` column."""
    values = globals()
    return {name: values[name] for name in names}
