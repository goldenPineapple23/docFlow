# Stage 3a: time limits on every task, and a memory cap (H5, the quick part)

Moved here word for word from `docs/BUILD-STATUS.md` on 2026-10-05 (the founder's context
housekeeping, D-191). Nothing below the marker was edited: it is the text as it stood under
"Stage 3 -- agreed with the founder before building", 3a merged 2026-09-29 (PR #26, D-179). It keeps its
original wording, including statuses that were true when each part was written.

Other files cite these sections as BUILD-STATUS "<heading>". Each cited heading is still in
`docs/BUILD-STATUS.md`, as a one-line stub pointing here. "Above" and "below" in this text
refer to the order the blocks had there: 3a, 3b, 3c, 3d, card billing, 3e.

<!-- moved text starts on the next line -->
**3a -- H5, the quick part: time limits on every task, and a memory cap.**
Agreed 2026-09-29 (founder's answers to my proposal):

- **The document task: a hard limit of 27 minutes, no soft limit** (founder
  approved hard-only over the earlier 25 soft / 27 hard).
  - 27 minutes is above the 20-minute read budget
    (`EXTRACTION_DEADLINE_SECONDS`) and below the 30-minute stuck timeout. So
    no document task outlives its claim, and the sweep can never hand a
    document to a second worker while the first is still on it. Today
    matching after the read has no bound. A test holds the constants in that
    order.
  - Why no soft limit: Celery raises it inside the task as an ordinary
    exception, and about 23 broad `except` blocks on the document path would
    relabel it (DOC-005 while parsing, DOC-021 while saving, VAL-016 in the
    post-processing steps). With hard-only, "never DOC-005" is true because
    the task can't catch anything.
- **The timeout is recorded by Celery's main process**, through a custom
  `Request` class whose `on_timeout` runs there for every timeout (Celery
  5.6.3). A parser hung in C code never sees a soft signal, and the killed
  child can't write anything.
  - It appends the attempt number to a new column,
    **`documents.timeout_attempts integer[]`** (migration `0030`, approved,
    backup first, deletes nothing). The append is idempotent.
  - If the write fails, it is logged and the document gets today's
    dead-worker handling.
- **The sweep decides, in one place**, for a document stuck in `processing`
  past the timeout:

  | State | Outcome |
  |---|---|
  | First timeout was on the latest attempt | Retry once |
  | A try has already run since the first timeout | **DOC-022, cause timeout** |
  | No timeout, `MAX_PROCESSING_ATTEMPTS` used | DOC-022, cause worker stopped (unchanged) |
  | Otherwise | Retry (unchanged) |

  **A timeout gets at most one retry** (founder): a file that hangs the parser
  will hang it again.
- **The alert says the cause.** It is the same `document_stuck` alert and the
  same DOC-022 entry; DOC-022's wording is unchanged (founder). The payload
  gains three fields:
  - `cause`: `timeout` or `worker_stopped`;
  - the tries that timed out;
  - the number of tries.

  They are ids and numbers only (7.10), and the founder email already lists
  every payload field. **Deduplication becomes one DOC-022 alert per tenant
  per cause per day** (founder, approved), so a timeout is never hidden inside
  a dead-worker alert from the same day.
- **Memory:** Celery's `worker_max_memory_per_child`, which replaces a worker
  process after the task that pushed it over. This is not a cap during a task:
  the machine's memory is the ceiling until 3c's per-file `setrlimit`. The
  value follows the worker machine size.
- **Production runs Celery's prefork pool.** Time limits do nothing under the
  `solo` pool used on Windows.
- **The six other tasks: hard limits only, approved by the founder
  2026-09-29 from these measurements.**
  - How the numbers were taken: from this machine against staging, where
    each database transaction costs about 300 ms over the internet (less on
    Fly, in the database's region). Staging's tenants hold little data, so
    the rollup at full per-tenant volume is left to the Phase 6 load test.

  | Task | Measured | Estimated at 50 tenants | Hard limit |
  |---|---|---|---|
  | Export (one document, 1,000 lines) | `.xlsx` 0.83 s, others 0.01 s | about 2 s | 5 min |
  | Catalog import (50,000 rows) | parse `.xlsx` 5.16 s, `.xls` 0.61 s, `.csv` 0.24 s; save 1.01 s | about 7 s | 5 min |
  | Rollup (2 days) | 7.86 s for 18 tenants | about 22 s | 15 min |
  | Scheduled jobs (up to 25) | 0.3 s per transaction | under 1 min | 10 min (must stay below the 30-minute release of `running` jobs) |
  | Lifecycle sweep | queries 0.40 s; Stripe 516-844 ms per call, 15 s timeout, up to 3 calls per suspend | about 15 s; about 5 min with 100 due at once; about 75 min if Stripe hangs | 10 min, plus a 4-minute time box |
  | Stuck sweep | 322 ms per tenant | about 16 s | 4 min (it runs every 5 min) |

  - Hard-only for the same reason as the document task: the sweeps' broad
    `except` blocks ("one tenant never stops the sweep") would catch a soft
    limit.
- **What a kill left behind, and the fixes (founder, 2026-09-29, all in 3a):**
  - **Lifecycle sweep: a tenant suspended in DocFlow, but still billed by
    Stripe.** The suspension commits first and Stripe is called after, so a
    kill or crash between them left the subscription active: charged after
    suspension, no alert, never retried.
    - **`tenants.stripe_cancel_pending_at`**, in migration `0030` with
      `timeout_attempts`. Both tables are backed up first, and both row
      counts go in the PR message. It is set in the same transaction as the
      suspension.
    - Every sweep retries pending cancels; a cancel is idempotent. The
      existing `stripe_cancel_failed` alert covers failures.
    - **Reactivation clears the mark in the same transaction as the status
      change.**
    - **The retry re-checks, under a row lock and immediately before calling
      Stripe, that the tenant is still in a cancelled state**, and skips and
      clears the mark if not. The lock is held across the Stripe call on
      purpose: releasing it first would let a reactivation commit between
      the check and the call, and the cancel would then hit the resumed
      subscription. Holding it is bounded by the Stripe timeouts (3 x 15 s),
      well inside the 5-minute idle-transaction cap (0028). A reactivation
      arriving during that window waits for it.
    - Test (founder): suspend -> the cancel fails -> reactivate -> the next
      sweep does **not** cancel.
  - **Lifecycle sweep: a 4-minute time box**
    (`LIFECYCLE_SWEEP_TIME_BOX_SECONDS`). The sweep takes no new tenant after
    4 minutes and leaves the rest to the next tick, so the 10-minute limit
    can't land in the middle of a tenant in normal running. Test (founder):
    a sweep over the cap stops taking tenants, and the next tick picks up the
    rest.
  - **Exports left `pending` and imports left `parsing`** are marked failed
    by the stuck sweep, with a catalog code. **Approved and built
    (founder, 2026-09-29):** **EXP-009** "This export didn't finish"
    (tenant) and **IMP-009** "Reading this file didn't finish" (founder;
    imports are Console-only, confirmed: the import task is enqueued only
    from `admin.py`). **30 minutes from creation**, not the 5-minute job
    limit, because `pending` includes waiting in the queue. No alert per
    export; the day's EXP-009 count is on the health strip ("Exports stopped
    today"), and **more than 3 in a UTC day for one tenant raises one
    `exports_not_finishing` alert** that day. A late export no longer marks
    the document `exported` after the sweep has failed it. D-179.
  - **Reactivation and a cancel that never went through (founder:
    option C, 2026-09-29).** Correction: reactivation already reuses the
    tenant's Stripe subscription unless Stripe has it `canceled` or
    `incomplete_expired` (`external_services.start_subscription`); it never
    creates a second one alongside a live one. **Decided and built
    (founder, 2026-09-29):** reuse any status Stripe hasn't ended (`active`,
    `trialing`, `past_due`, `unpaid`). When an old subscription is reused
    after a suspension, one high-severity
    `reactivation_invoices_to_review` alert lists its invoices in two
    sections, each with amounts and a total: **"Before suspension (service
    delivered, collect)"** (open invoices) and **"During suspension (void
    drafts/open, refund paid)"** (draft, open and paid ones). DocFlow changes
    nothing at Stripe. If Stripe's list can't be read, the alert says so
    and the reactivation still goes ahead. **Also fixed:** go-live and
    reactivation shared one Stripe idempotency key per tenant, so a
    reactivation within 24 hours of go-live was refused. Each action now has
    its own key. Tests: no second subscription is created, the alert's
    sections and totals, and reactivating within 24 hours of go-live
    succeeds. **Two partial indexes on `exports`** (pending, and EXP-009)
    were added to `0030`. D-179.
  - Rollup, scheduled jobs and the stuck sweep leave nothing that needs
    fixing: the rollup is overwritten by its next run, a job's writes commit
    with its "done" mark, and a repeated enqueue is harmless.
- **Tests:**
  - **Linux CI, a real prefork worker** (the CI worker job already has Redis),
    with shortened limits. A task that hangs and ignores signals must be:
    - killed, with its attempt recorded;
    - followed by the worker taking the next task;
    - retried once by the sweep;
    - ended by the second hang in DOC-022, with the cause `timeout` in the
      alert.

    A separate test kills a worker with no timeout and asserts the
    worker-stopped path is unchanged.
  - **On any OS:** the sweep's decision table; the hook recording one
    attempt once even when it fires twice; the order of the limit constants.
  - **The document task's effective limits** (founder, 2026-09-29): read from
    the finalized Celery app, its soft limit is `None` and its hard limit is 27
    minutes. Celery treats a task's `None` as "not set", so a global
    `task_soft_time_limit` added later would override it; this test fails when
    that happens.
- **The founder's condition on hard-only: nothing done before the commit is
  repeated wrongly by a kill and a retry.** Checked 2026-09-29; the three
  fixes below were approved by the founder the same day:
  - **Paid model calls: never repeated.** Every model call ends by the
    20-minute read budget counted from the claim (existing test
    `test_M1_the_whole_read_ends_well_inside_the_stuck_timeout`), so a kill
    at 27 minutes can't land during one. Once the answer is saved, a retry
    resumes from it without a model call (H3). A parse hang is killed before
    any paid call is made.
  - **Emails and alerts: never duplicated.** They are outbox and alert rows
    written in the same transaction as the state change, with dedupe keys.
  - **Buyer creation and merge flags: safe to repeat** (`ON CONFLICT`).
  - **Not safe: `learned_rules.times_applied`.** Matching (`matching.py`) and
    buyer-alias identification (`buyers.py`) add to it in their own
    transactions, before the move to review, so a kill between them and that
    move counts a document's rule uses twice on the retry. This already
    happens today on the dead-worker resume path; 3a makes it more reachable
    until Stage 4 speeds up matching. It inflates the Console Rules page's
    "times applied"; the mapping-reuse KPI counts lines, not this counter, so
    it is unaffected (`matching.py`'s docstring saying otherwise is out of
    date). **Fix, in 3a:** add to the counter once, inside the transaction
    that moves the document to `needs_review`, from the rules recorded on the
    document's lines and header. That transition happens exactly once.
    - Checked first (founder's condition): **nothing on the document path
      reads `times_applied` for a decision.** Its only reader is
      `learned_rules.list_rules` for the Console Rules page, which sorts by
      `created_at`. Matching and buyer identification load rules without it.
    - What changes: the counter comes to mean "fired on documents that
      reached review". A document that fails validation (DOC-021) or is taken
      over by the sweep no longer adds to it.
    - **Existing staging counts are not corrected** (founder).
  - **Previews and extracted text: repeated writes, orphaned files.** Each
    write gets a new random file name, so a retry leaves the earlier file
    behind; the document always points to the latest. **Fix, in 3b:** a fixed
    key per document that a repeat overwrites, built with the storage
    rewrite. **3b's copy of staging's files into Storage copies only files a
    document still references**; orphans are left behind and their number is
    reported (founder).
  - **The needs-review digest note is written after the commit.** A kill in
    that gap of milliseconds leaves the order out of the digest email (it is
    still in the review queue); it is never duplicated. **Fix, in 3a:** the
    same transaction behind a savepoint, which keeps D-131's rule that a
    notification can never cost the document its checks.

**Carried from the Stage 1 checkpoint and missing from the row above: a run
row before the model call (D-163).** A worker that dies during the model call
(a crash, a lost machine; not 3a's hard limit, which can't land there)
records nothing, so that call's cost is lost. `extraction_runs` is append-only
with `succeeded NOT NULL`, so the fix needs a migration. **Agreed: its own item
in 3c, designed and asked about before building.**

