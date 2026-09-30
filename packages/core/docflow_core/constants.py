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
# cancellation's computed effective date. 14 (founder, 2026-09-29, with card
# billing): a card subscription goes past_due on the FIRST failed charge,
# while Stripe is still retrying, so the cure period has to cover the retry
# window -- Stripe's retry schedule is set so the final retry lands before
# day 14 (RUNBOOK section 3). The ToS placeholder ("uncured after [14] days'
# notice") was changed to match. It applies to invoice billing too, where
# past_due already means the invoice is INVOICE_DAYS_UNTIL_DUE days late.
# Was 0 (D-125) while invoice billing was the only method.
CURE_PERIOD_DAYS = 14
EXPORT_WINDOW_DAYS = 30  # ToS placeholder
REMINDER_DAYS = (1, 15, 25)

# ── Clocks (D-170) ───────────────────────────────────────────────────────────
# How far Stripe's clock may disagree with ours (D-176). Every accepted webhook
# already proves the two agree to within this: its signature carries Stripe's
# send time, and a signature stamped further than this from our clock, in
# either direction, is refused. So a Stripe event time -- the cure clock's
# start (first_past_due_at) -- is at most this far off our clock, and an event
# stamped further than this into the future is not a real Stripe event and is
# not applied (it would otherwise freeze the ordering guard until our clock
# caught up). Cost: a non-payment effective date can be up to five minutes
# earlier or later than Stripe's exact instant.
STRIPE_CLOCK_TOLERANCE_SECONDS = 300

# ── Alerting and the Console (Section 7.15.3) ───────────────────────────────
REVIEW_BACKLOG_ALERT_DAYS = 3
CONFIDENCE_DRIFT_MARGIN = 0.05
ABANDONED_REVIEW_CEILING_MIN = 30
STUCK_PROCESSING_TIMEOUT_MIN = 30
# Tries before a document stuck in processing is failed with DOC-022 (D-158).
MAX_PROCESSING_ATTEMPTS = 3
# Exports and imports still unfinished STUCK_PROCESSING_TIMEOUT_MIN after they
# were started are failed by the stuck sweep (EXP-009 / IMP-009, Stage 3a).
# Counted from creation, not from the job's start: `pending` includes time
# waiting in the queue. The founder hears once a day about a tenant with more
# than this many EXP-009s that day (UTC).
EXPORTS_NOT_FINISHED_ALERT_PER_DAY = 3

# ── Worker time limits (Phase 5.5 Stage 3a, review H5) ──────────────────────
# Hard limits only: Celery kills the task's process. A soft limit is raised
# inside the task as an ordinary exception, and the broad `except` blocks on
# the document path and in the sweeps would catch it and relabel it (a
# damaged file, a failed step), so none is set anywhere. Sized from the
# worst cases measured on 2026-09-29 (docs/BUILD-STATUS.md, Stage 3a).
#
# The document task: above the 20-minute read budget (EXTRACTION_DEADLINE_
# SECONDS) and below STUCK_PROCESSING_TIMEOUT_MIN, so a task never outlives
# its claim and the sweep never hands a document to a second worker while
# the first is still on it. A test holds that order.
DOCUMENT_TASK_TIME_LIMIT_SECONDS = 27 * 60
EXPORT_TASK_TIME_LIMIT_SECONDS = 5 * 60
IMPORT_TASK_TIME_LIMIT_SECONDS = 5 * 60
ROLLUP_TASK_TIME_LIMIT_SECONDS = 15 * 60
# Below scheduled_jobs.RUNNING_TIMEOUT_MINUTES (30), for the same reason as
# the document task: a live sweep's jobs are never released to another.
SCHEDULED_JOBS_TASK_TIME_LIMIT_SECONDS = 10 * 60
LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS = 10 * 60
STUCK_SWEEP_TASK_TIME_LIMIT_SECONDS = 4 * 60
# The lifecycle sweep takes no new tenant after this long and leaves the rest
# to the next tick, so its hard limit can't land in the middle of a tenant's
# suspension in normal running: after the box closes, the most still in
# flight is one tenant's Stripe calls (3 x the 15-second timeout).
LIFECYCLE_SWEEP_TIME_BOX_SECONDS = 4 * 60
# A worker process is replaced after the task that took it past this much
# memory (Celery's worker_max_memory_per_child, in KiB). Not a cap during a
# task: the machine's memory is the ceiling until the parse service's
# per-file limits (Stage 3c). About 70% of the 1 GB worker machine priced
# for staging.
WORKER_MAX_MEMORY_PER_CHILD_KIB = 700 * 1024
STAGING_TTL_DAYS = 90
FIRST_WEEK_CHECKIN_DAYS = 7
ROLLUP_STALE_HOURS = 36
# The Console's step-up (D-151, D-177): a destructive action needs a TOTP
# challenge passed at most this long ago. Read from the session token's `amr`
# entry for `totp`, whose timestamp is GoTrue's clock.
MFA_STEP_UP_MAX_AGE_SECONDS = 300
# How far GoTrue's clock may differ from ours when judging that age (D-170).
# The same allowance as the session token's own leeway (D-167). Cost: a
# challenge up to MFA_STEP_UP_MAX_AGE_SECONDS + this old can pass (5 min 30 s),
# and one stamped up to this far in our future is accepted.
MFA_CLOCK_TOLERANCE_SECONDS = 30
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

# Card billing (founder, 2026-09-29): a card-billed owner whose subscription
# goes past due is emailed at once, and again this many days before the date
# from which the tenant may be suspended (first past-due notice +
# CURE_PERIOD_DAYS). The job skips itself if the payment has gone through.
PAST_DUE_REMINDER_DAYS_BEFORE = 3


def constants_in_effect(*names: str) -> dict[str, Any]:
    """The named constants' current values, for a lifecycle event's
    `constants_in_effect` column."""
    values = globals()
    return {name: values[name] for name in names}
