# DocFlow — Independent Review at the End of Phase 5

**Date:** 2026-09-25 · **Scope:** the whole repository at `838830b` (local `main`), plus the live `docflow-staging` database, GitHub, and the Anthropic API (two live calls).
**Mode:** read-only. No code, config, test or migration was changed. This file is the only thing written.

**How to read this.** Section 1 is the answer in plain English. Sections 2–4 score the system and check it against the build prompt. Section 5 is every defect found, worst first, each with the file and line. Section 6 is what I'd do beyond the spec. Sections 7–9 say what to fix when, what conflicts I found, and the order to fix things in.

**Evidence labels.** **Verified** means I ran it or saw it happen (a command, a query, a test). **Inferred** means I'm reading it from the code and did not trigger it. **Could not verify** means something I needed was missing, and I say what.

---

## 1. Verdict

(a) **Trust a real customer's data after Phase 6? Not with Phase 6 as currently scoped. Yes, once the section 9 fixes are done first.** The database silently rounds prices to 4 decimal places and line totals to 2, so a unit price printed as `0.00345` is approved and exported as `0.0035`. This is exactly the "wrong number in an export" the product exists to prevent, and nothing in the Phase 6 list catches it.

(b) **Would a senior engineer be comfortable inheriting it? Mostly yes on the code, no on the safety net.** The code is well layered, carefully reasoned and unusually well explained. But `main` isn't protected, CI has been red on GitHub for six days, 14 commits were never pushed, and 89% of the API tests never run in CI.

(c) **Is the foundation sound enough to build Phase 6 on without restructuring? Yes for the database, API, Console and review flow. No for the document worker and file storage.** Documents are still stored on the local disk (a Phase 1 stand-in). The worker has no isolation limits, no per-tenant fairness, and its matching step takes about 1.7 seconds per line against a 50,000-item catalog. Those need restructuring before Phase 6's load test and production setup.

---

## 2. Scorecard

| Area | Score | One-line justification |
|---|---|---|
| A. Data integrity | **5/10** | The design is excellent (immutable snapshots, provenance, warnings from the catalog, examples that can't leak). But the database silently rounds extracted values, validation can fail silently, and a human edit is never re-checked. |
| B. Tenant isolation & Console | **7/10** | Verified live: RLS on all 35 tables, a role that can't bypass it, an audited admin layer, correct acting-as. Weak points: the admin "bypass" is a setting the app role can turn on itself, there's no re-authentication before destructive actions, and the isolation tests never run in CI. |
| C. Lifecycle & billing | **6/10** | Effective dates are correct and go-live is retry-safe. But a failed Stripe webhook update is lost for good, event order is ignored, and a suspended tenant can still upload. |
| D. Allowance, quarantine, abuse | **6/10** | The one-gate, never-discard design is right. But the email webhook's only secret is the token in the public intake address, so every SPF/DKIM/DMARC and velocity defence can be bypassed by posting directly to the API. |
| E. Parsing hardening & web baseline | **4/10** | Pre-parse validation is strong: magic bytes, zip-bomb, XXE and encrypted-file checks, one allowlist module. The isolation itself (memory, CPU and time limits, no network) doesn't exist. There's no CSP or security headers yet (Phase 6). |
| F. Error catalog | **8/10** | Catalog-first everywhere, including a crash-to-SYS-001 middleware. Only a handful of bare strings remain. |
| G. Scale envelope (5.1) | **3/10** | No per-tenant fairness, no `pg_trgm`, matching measured at ~1.7 s per line against a 50k catalog, offset (not cursor) pagination. Pooling and indexes are fine. |
| H. Maintainability for a solo founder | **5/10** | Superb docstrings and a full decision log. But SETUP.md stops at migration 0009 (there are 25), nine settings do nothing, there's no record of which migrations are applied, and the checkpoints report local results as CI results. |
| I. Architecture & engineering quality | **6/10** | Clean layering: pure rule functions, session-injected data layers, one catalog, one upload path. The document pipeline is the weak spot: steps commit separately and swallow failures, tasks aren't idempotent, and one worker does parsing, Stripe and email. |

**The three weakest parts of the system, regardless of score:**

1. **The document worker pipeline** (`apps/worker/app/tasks/parse_and_extract.py`). It marks an order ready for review before it has been checked. A crash or redelivery can put an approved order back into `processing`. It has no resource limits and no fairness, and matching is too slow for a large catalog.
2. **The verification loop.** Main is unprotected, CI is red remotely and hasn't seen the last 14 commits, and CI runs 43 of 391 API tests. The build's own lesson ("a green suite is evidence about the layer it covers") applies to CI itself.
3. **Numeric fidelity at the database boundary.** Every money and quantity column has a fixed scale that Postgres rounds to without complaint, and the tests' sample values never have more decimal places than the columns hold.

---

## 3. Phase 5 exit criteria

| Criterion | Status | Evidence |
|---|---|---|
| All nine onboarding steps from the Console against a fake prospect, ending in a live tenant whose owner logs in via invite | **Partial** | The API tests exercise every step against staging (`apps/api/tests/test_onboarding_api.py`, `test_catalog_import_api.py`, `test_console_api.py`: all passed in the 391/391 run, **Verified**). CHECKPOINTS.md says the founder walked it on 18 Sept. **Could not verify** end to end myself: no founder credentials, and the invite link is held in the outbox because no email provider is configured. |
| Console reuses tenant parse/extract/review code, proven by a module-dependency check | **Met** | `apps/api/tests/test_console_shared_paths.py` parses the source and checks that each acting-as route is the *same endpoint function* as the tenant's (`:113-125`). This is a real check. |
| Cancel → suspend → export window → reactivate, and cancel → delete, for all three reasons with correct effective dates | **Partial** | Effective-date rules are correct (`packages/core/docflow_core/lifecycle.py:131-168`), and an earlier override is refused (`:202-204`). But a suspended tenant can still **upload** documents (finding H10), which 7.14 forbids. The Stripe webhook has never run live (`STRIPE_WEBHOOK_SECRET` is empty on staging). The browser walk used one reason. |
| Dashboard KPI cards match a hand-written SQL query on staging | **Met, with a defect nearby** | `test_every_kpi_equals_an_independent_query` (passed: full API suite 391/391 against staging, **Verified**). The rollup's own stale-alert path crashes (finding M6). |
| Example prompting on for a buyer with 10+ approved orders: golden fixture still exact, contamination test passes live | **Met** | **Verified today:** the live contamination test passed. The recorded "golden with examples" replay passes in the suite. I didn't re-run the live golden-with-examples call (outside my one-golden, one-contamination budget); CHECKPOINTS.md records it passing on 25 Sept. |

---

## 4. Section 12 definition-of-done gap table

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Launch criteria in the build timeline | Not started (Phase 6) | — |
| 2 | UAT plan executed, zero open Critical/High | Not started (Phase 6) | This review opens 1 Critical and 11 High. |
| 3 | Golden fixture extracts exactly | **Done** | **Verified live today:** `test_live_extraction_matches_section_8_3` passed. |
| 4 | Cross-tenant access tests fail closed | **Partial** | They pass against staging (passed: full API suite 391/391 against staging, **Verified**) but **never run in CI**: with no `DATABASE_URL`, 348 of 391 API tests skip (**Verified**: "43 passed, 348 skipped"). |
| 5 | Export round-trip, all four formats | **Done** | Core suite passed 451/451 (**Verified**). The round-trip compares against a snapshot that is already rounded (C1). |
| 6 | No outbound calls except model, Supabase, Stripe, email | Not started (Phase 6) | The code only calls those hosts. Nothing enforces it; `worker_egress_allowlist` is never read. |
| 7 | CI green on main, every push, blocks merges, includes audit | **Failing** | **Verified via GitHub API:** `main` has `protected: false`, and the last 5 runs on `main` failed (api `mypy`, core `ruff`). Local `main` is 14 commits ahead of origin. |
| 8 | Separate staging and prod; prod migrations applied to staging first | Not started (Phase 6) | There's no migration record table (**Verified**: `supabase_migrations.schema_migrations` doesn't exist), so "applied to staging first" can't be proven. |
| 9 | Restore drill | Not started (Phase 6) | Documents live on local disk, outside any Supabase backup (H6). |
| 10 | Hostile-file tests, worker healthy afterwards | **Partial** | The tests exist and pass (core 451, worker 81, **Verified**). "Worker healthy" is only true in-process: there's no time or memory limit, so a file that hangs a parser holds a worker forever (H5). |
| 11 | A real PO in every Tier 1 and 2 format reaches extraction | **Done** | Worker suite 81 passed, 0 skipped, `.doc` included (**Verified**). The fixtures are synthetic. |
| 12 | Founder alerting on breaker trip and stuck document | **Partial** | The breaker alert exists. The stuck-document alert is Phase 6 (`STUCK_PROCESSING_TIMEOUT_MIN` is unused). With no email provider, alerts reach the screen only. |
| 13 | Load test at 2× with fairness and p95 recorded | Not started (Phase 6) | **Predicted to fail** on fairness and matching speed (H4). |
| 14 | Full lifecycle end to end, suspended keeps read/export | **Partial** | Read/export is kept. Upload isn't blocked (H10). |
| 15 | Example prompting: contamination + live golden + token overhead in cost log | **Done** | Live contamination **verified today**. `extraction_runs.example_input_tokens` is recorded. |
| 16 | CLAUDE, SETUP, DECISIONS, RUNBOOK, .env.example, README exist and are current | **Failing** | RUNBOOK.md is missing (Phase 6). SETUP.md stops at migration 0009 (`SETUP.md:78-88`). README says "Phase 0 — in progress" (`README.md:9`). `.env.example` omits `STORAGE_ROOT`. |
| 17 | Founder runs all nine steps without touching code | **Partial** | See section 3. |
| 18 | `/signup` 404; `/admin/*` 404 to tenant owner; `admin_actions` for every Console call; boundary test passes | **Partial** | API `/admin` returns 404 (tests). **But the web app returns HTTP 200 for `/admin` to an unauthenticated visitor** (**Verified** with `next start` + curl; `/signup` is 404). The boundary test passes but scans only `apps/api/app`. |
| 19 | Console exercised only through tenant modules | **Done** | Dependency test. |
| 20 | Three reasons give correct dates; for_cause immediate; customer_requested not before period end | **Done** | `lifecycle.py:131-168`, tests. |
| 21 | KPIs equal hand SQL; rollup idempotent; excludes test batch | **Partial** | Correct and idempotent (`metrics.py:52-135`, `ON CONFLICT`). The stale alert crashes the rollup (M6). |
| 22 | Every `founder_alerts` type triggered in staging and emailed | **Not started** | Review-backlog, confidence-drift and staging-TTL alerts were never built (M7). There's no email provider. |
| 23 | Allowance metering, banners and emails once per month, ceilings pause, quarantine tests, rotation | **Partial** | Built and tested. The banner shows at 90%, not 80% (D-129, a founder decision). The ceilings can be bypassed through the forgeable webhook (H8). |
| 24 | Error-catalog tests: every user-reachable site has a code, snapshot committed | **Partial** | The snapshot and code-existence tests exist. The "every throw site references a code" test doesn't (Phase 6). |
| 25 | UAT cases TC-44 onward proposed and accepted | Not started | Not found anywhere in `docs/`. |

---

## 5. Findings, ranked by severity

Format: **what's wrong** · where · why it matters here · fix · effort · status.

### Critical

**C1. The database silently rounds extracted and human-entered money and quantities.**
- **Where:** `supabase/migrations/0002_documents.sql:68` (`order_total numeric(12,2)`), `:98` (`quantity numeric(14,4)`), `:100` (`unit_price numeric(14,4)`), `:101` (`line_total numeric(14,2)`). The values are written as strings in `apps/worker/app/tasks/parse_and_extract.py:700,728-731` and `packages/core/docflow_core/review.py:593-602`, and nothing checks their scale first.
- **Why it matters:**
  - Postgres rounds on insert instead of refusing. **Verified on staging:** `'0.00345'::numeric(14,4)` → `0.0035`, `'12.345'::numeric(14,2)` → `12.35`.
  - Sub-cent unit prices are normal in fasteners, packaging and chemicals. The rounded value goes into the approved snapshot and every export, and the round-trip test passes because it compares against the already-rounded snapshot.
  - The audit trail records the reviewer's typed `after` value, which now disagrees with what was stored.
  - The math-check tolerance (D-096) reads "printed precision" from the stored, rounded value, so it masks the error rather than flagging it.
  - This is an unauthorised alteration of an extracted value (Section 10) and a wrong number reaching an export.
- **Fix:**
  - Widen `quantity` and `unit_price` to at least `numeric(18,6)` and totals to `numeric(16,4)`.
  - Before writing, check each value's scale against its column. If it doesn't fit, store nothing lossy: keep the raw string and raise a warning.
  - Add a golden-style test with a 5-decimal price and a 3-decimal line total that runs through the worker's real DB write and through `apply_edits`.
- **Effort:** S–M (one additive migration plus a check). **Status:** Verified.

### High

**H1. An order is marked "needs review" before it has been matched, de-duplicated or validated, and a failure in those steps is silent.**
- **Where:** `parse_and_extract.py:646-673` commits `status='needs_review'`. Matching, duplicate detection, validation and the digest then run in separate transactions whose exceptions are only logged (`:745-837`).
- **Why it matters:**
  - If validation raises, the order sits in the queue with **zero warnings** and can be approved. The acknowledgement gate is simply absent, and nobody is alerted.
  - There's also a window of several seconds (or ~70 s at scale, see H4) in which a reviewer can approve before warnings exist.
- **Fix:** Commit `needs_review` only after the post-processing steps finish, or record a `pipeline_state` and refuse approval (a REV code) until validation has run. Raise a founder alert when a step fails.
- **Effort:** S–M. **Status:** Inferred (code path read end to end).

**H2. A human edit is never re-validated.**
- **Where:** `apps/api/app/routers/review.py:490-517` calls `apply_edits` and nothing else. D-115 noted this and it was never closed.
- **Why it matters:** A reviewer who types `4750` for `47.50` gets no math warning and can approve and export it. Existing warnings also stay open after the value that caused them is fixed.
- **Fix:** Call `validate_document` in the same transaction after `apply_edits`. D-074's fingerprinting already keeps prior acknowledgements.
- **Effort:** S. **Status:** Verified (by reading; no call exists).

**H3. The extraction task isn't idempotent and can knock an approved order back to `processing`.**
- **Where:** `apps/worker/app/celery_app.py:63` (`task_acks_late=True`) combined with `parse_and_extract.py:552-554`, which sets `status='processing'` unconditionally. Only `staged` is refused. The header insert fails on a second run because `document_headers.document_id` is the primary key (`0002_documents.sql:56`).
- **Why it matters:** If the worker dies after the main transaction commits (a deploy restart during a 70-second matching run is enough), Redis redelivers the task within an hour. The redelivered run sets a possibly already-approved order to `processing`, pays for a second extraction, fails on the insert, and leaves the order stuck and out of the queue. No alert fires.
- **Fix:** Use a conditional claim, `UPDATE … SET status='processing' WHERE id=:id AND status IN ('pending','processing') RETURNING id`, and exit if nothing is returned. Make the header and line write idempotent. Set a Redis `visibility_timeout` longer than the longest task.
- **Effort:** S. **Status:** Inferred.

**H4. The Section 5.1 scale envelope isn't met: no per-tenant fairness, and matching is far too slow.**
- **Where:**
  - `celery_app.py:13-17` defers fairness to "Phase 1", and only two FIFO queues exist. Every upload and email goes to `interactive` (`documents.py:173`, `email_intake.py:808-811`).
  - Matching scores every catalog item in Python for every line (`packages/core/docflow_core/matching.py:365-399`, `502-526`).
  - `pg_trgm` isn't installed (**Verified**: the only extensions are plpgsql, pg_stat_statements, uuid-ossp, pgcrypto, supabase_vault).
- **Why it matters:**
  - **Verified benchmark:** `resolve_line` against a 50,000-item synthetic catalog takes **1.74 s per unmatched line**. A 40-line PO spends about 70 s in matching, inside an open database transaction.
  - A 500-document backfill at 20 lines each is about 4.8 worker-hours, all ahead of every other tenant's orders in the same FIFO queue.
  - D-063's "well under a second" is off by two orders of magnitude.
  - The Phase 6 load test will fail on this, and the fix is structural.
- **Fix:**
  - Generate candidates in SQL: an exact-SKU index lookup, then `pg_trgm` top-K (e.g. 50) using a GIN index on the normalised description. Keep the existing Python scorers and measure guard for those K.
  - For fairness, add a per-tenant dispatcher (per-tenant queues or a DB-backed round-robin claim) and send backfills of more than N documents to `bulk`.
- **Effort:** M–L. **Status:** Verified (benchmark, extension list, code).

**H5. Parsing isolation (7.11) exists only as intent.**
- **Where:** No `setrlimit`, memory cap, Celery `time_limit`/`soft_time_limit`, unprivileged user or network restriction anywhere (**Verified** by grep). Only LibreOffice has a timeout (`apps/worker/app/conversion.py:405`). `worker_egress_allowlist` (`packages/core/docflow_core/config.py:68`) is never read.
- **Why it matters:**
  - pdfplumber, python-docx, openpyxl, Pillow, xlrd, olefile and the in-house RTF reader all run inside the Celery worker. That worker also holds the database URL, the Anthropic key and the Stripe key (one root `.env`) and makes Stripe calls (`apps/worker/app/tasks/lifecycle_sweep.py`).
  - One file that hangs a parser occupies a worker slot forever, and with `prefetch=1` a few such files stop processing for everyone.
  - D-003's controls ("setrlimit, SIGKILL timeout, unprivileged user, one subprocess per file", confirmed by the founder) were never built.
- **Fix:**
  - Short term: Celery `task_time_limit` and `soft_time_limit` per task, plus a worker memory limit.
  - Structurally: a dedicated parse queue and worker service that holds no Stripe or Anthropic keys, running parsers in a resource-limited subprocess (Linux `resource`), with egress denied at the platform.
- **Effort:** M. **Status:** Verified (absence).

**H6. Documents live on the local filesystem.**
- **Where:** `packages/core/docflow_core/storage.py:7-12`, `config.py:78-86` ("a real deploy needs shared/object storage — tracked as a TODO"). D-019, from Phase 1, is still open.
- **Why it matters:**
  - The architecture (D-003) puts the worker on a separate service, and two services can't share a disk.
  - Uploaded POs, previews, exports and example text sit outside every Supabase backup, so the Phase 6 restore drill cannot restore them.
  - Losing the host loses every customer document.
- **Fix:** Implement the same `save_file`/`read_file` interface on Supabase Storage (a private bucket, service-role access only in the worker and API, keys prefixed `tenants/{id}/`), and migrate the files on staging.
- **Effort:** M. **Status:** Verified.

**H7. CI doesn't protect `main`, is red, hasn't seen the last 14 commits, and skips almost every database test.**
- **Where:** **Verified via the GitHub API:**
  - `branches/main` has `protected: false` and enforcement `off`.
  - The last five runs on `main` (61295b7…e81f1ec, 19 Sept) all failed: api `Run mypy app`, core `Run ruff check .`.
  - `git status` shows `main…origin/main [ahead 14]`.
  - `.github/workflows/ci.yml:62,86` run pytest without `DATABASE_URL`, which **Verified** gives 43 passed / 348 skipped.
  - `packages/core` (19k lines) is never type-checked; locally `mypy docflow_core` reports 24 errors, mostly typing noise.
  - The core job installs unpinned `ruff>=0.5` and `mypy>=1.10` (`packages/core/pyproject.toml:39-41`) from ranges, not a lockfile.
- **Why it matters:**
  - Section 10: "Merge to main with CI red". Section 5: "Merges to main are blocked on green".
  - The Phase 0 exit criterion ("CI blocks a deliberately failing test") and the Phase 3/4 "CI green" claims are no longer true.
  - Tenant isolation, "the one thing that cannot ever fail", is tested on the founder's machine only.
- **Fix:**
  - Push the 14 commits and turn on branch protection with the four jobs required.
  - Add a `postgres` service container that applies the migrations and creates `docflow_app` (NOBYPASSRLS), so the RLS and isolation tests run on every push.
  - Pin the lint and type-check tools, and add mypy for core.
- **Effort:** M. **Status:** Verified.

**H8. The email webhook can be forged by anyone who has the intake address.**
- **Where:**
  - `apps/api/app/routers/email_intake.py:8-11,27-59`: "the per-tenant token in the URL path IS the authentication".
  - The token is the local part of the public address: `orders+{token}@…` (`packages/core/docflow_core/admin_data_access.py:771`).
- **Why it matters:**
  - Once a tenant gives the address to its buyers (the whole point of it), anyone can POST a Postmark-shaped JSON body directly to `/intake/email/{token}` with any `From` and `Authentication-Results: spf=pass dkim=pass dmarc=pass`.
  - That defeats the auth-fail quarantine, the unknown-sender velocity rule (a forged known buyer is spared) and sender-based example selection.
  - 7.16.3's layers assume the headers came from the mail provider.
- **Fix:** Authenticate the provider separately from the address. Use Postmark's HTTP Basic Auth credentials on the webhook URL (a secret not derivable from the address), checked in constant time. Add an optional source-IP allowlist.
- **Effort:** S. **Status:** Inferred (code read; not attempted against staging).

**H9. There's no fresh re-authentication before destructive Console actions.**
- **Where:** No re-auth, `auth_time` or MFA check anywhere (**Verified** by grep; the only hit is CLAUDE.md:206). Affected routes: `apps/api/app/routers/admin.py:1021` (hard delete), `:1487` (quarantine clear), `:900` (cancel).
- **Why it matters:** 7.15.1 requires it. A stolen or left-open founder session can hard-delete a tenant after the name is typed.
- **Fix:** Require a token issued within the last N minutes (check `iat`/`auth_time`) or Supabase `aal2` (MFA), and have the Console prompt for re-login before these routes.
- **Effort:** S–M. **Status:** Verified (absence).

**H10. A suspended or pending-deletion tenant can still upload documents, and they are extracted at DocFlow's cost.**
- **Where:** `apps/api/app/routers/documents.py:59-191` (`ingest_upload`) and `packages/core/docflow_core/intake_gate.py` never read `tenants.status`. Only email intake checks it (`packages/core/docflow_core/email_intake.py:698-723`). No test covers this.
- **Why it matters:** 7.14: "the upload endpoint and API return a clear error". A cancelled customer keeps generating model spend, and the Phase 5 lifecycle exit criterion is only partly met.
- **Fix:** Refuse in `ingest_upload` with the existing `INT-006`, and add a test.
- **Effort:** S. **Status:** Verified (by reading; no gate exists).

**H11. A Stripe webhook that fails mid-processing is lost for good, and event order is ignored.**
- **Where:**
  - `packages/core/docflow_core/billing_webhooks.py:43-52` commits the event ID as processed in one transaction; `_sync_subscription` (`:82-114`) runs in another.
  - No `event.created` comparison.
  - `first_past_due_at` uses receive time (`:94`), and an `unpaid` event clears it (`:95-96`).
- **Why it matters:**
  - A database blip during the sync makes Stripe's retry come back as a "duplicate", so the status is never updated.
  - An older `subscription.updated` arriving late overwrites a newer `past_due`.
  - Subscription status drives MRR, the badge, the past-due alert and the non-payment effective date.
- **Fix:** Insert the event ID in the same transaction as the update, after it succeeds. Ignore events older than the last applied one (store `stripe_status_event_at`). Take `first_past_due_at` from the event.
- **Effort:** S. **Status:** Inferred. **Could not verify live:** `STRIPE_WEBHOOK_SECRET` is empty on staging.

### Medium

**M1. POs with more than about 50–60 lines fail every time.** `packages/core/docflow_core/extraction.py:432` sets `max_tokens=4096` and nothing checks `stop_reason`. A golden line is 178 JSON characters (~60 tokens), so large POs truncate, become `DOC-009`, and the customer is told "DocFlow has been alerted". Fix: raise the limit (the model allows far more), map `stop_reason == "max_tokens"` to its own catalog code, and add a 100-line fixture. S. Inferred.

**M2. A non-conforming number is silently dropped.** `extraction.py:338-344`: `"1,356.00"` or `"47.50 USD"` becomes `None` with no warning, and `Decimal` also accepts `NaN`, `Infinity`, `1E+3`, negatives and padded whitespace. Fix: enforce `^\d+(\.\d+)?$`; if a value doesn't match, keep the raw string and raise a VAL warning. S. Inferred.

**M3. An out-of-range confidence strands the document.** The model's confidence is never clamped. A value like `95` overflows `numeric(4,3)` (**Verified**: `DataError`) inside the uncaught main write (`parse_and_extract.py:643-735`). The paid extraction is lost, the document stays `processing`, and no alert fires. Fix: clamp or flag, and wrap the main write so it ends in `failed` with a code. S. Verified (cast) / Inferred (path).

**M4. The recorded acknowledgement "warning text" comes from the client.** `apps/api/app/routers/review.py:129-133,528` accepts `code` and `text` from the browser, which sends `code (k: v, …)` (`apps/web/src/components/review/ReviewDocumentScreen.tsx:653-659`), not the catalog wording the reviewer read. It can also be empty or forged. Fix: build the text on the server from the catalog entry plus `detail`. S. Verified.

**M5. Approval isn't tied to what the reviewer saw.** `review.py:520-547` has no `expected_version`, and `approve_document` takes no row lock (`packages/core/docflow_core/review.py:706`). Reviewer A can approve values that reviewer B changed after A loaded the page. Fix: require the version token on approve and lock the row. S. Inferred.

**M6. The `rollup_stale` alert crashes the rollup and keeps it stale.** `packages/core/docflow_core/metrics.py:411-425` raises the type `rollup_stale`, which isn't in `ALERT_TYPES` (`packages/core/docflow_core/founder_alerts.py:40-58`). The result is `ValueError` (**Verified**). It happens inside the `finally` block (`metrics.py:376-397`), which rolls back the run's own completion record, so the next nightly run is "stale" again and fails the same way until someone presses Recompute. D-121's claim is untrue, and the test only checks `is_stale`. Fix: add the type, raise it outside the completion transaction, and test that path. S. Verified.

**M7. Three Phase 5 alert types were never built.** Review backlog, confidence drift and staging-TTL: `REVIEW_BACKLOG_ALERT_DAYS`, `CONFIDENCE_DRIFT_MARGIN` and `STAGING_TTL_DAYS` have zero readers (**Verified**). These are 7.15.2 and 7.15.3 requirements. S–M. Verified.

**M8. Logs can carry customer values.** `packages/core/docflow_core/db.py:97` creates the engine without `hide_parameters=True`, so any SQLAlchemy error that escapes (Celery logs the full traceback of an uncaught task error, e.g. M3) prints bound parameters: PO numbers, names, raw JSON. `logger.exception` at `parse_and_extract.py:486,593` and `founder_alerts.py:202` does the same. (7.10.) S. Inferred.

**M9. The Console's existence can be detected from the web app.** **Verified:** the built web app returns HTTP 200 for `/admin` and `/admin/tenants/new` to a signed-out visitor, and 404 for `/signup` and unknown paths. The gate is client-side (`apps/web/src/app/admin/layout.tsx:23-57`; D-014's follow-up was only partly done in D-127). The API correctly returns 404, so no data leaks, but 7.15.1 says a stranger can't "confirm the surface exists". Fix: server-side gating (Next middleware with a cookie-based Supabase session). M.

**M10. Configuration that silently does nothing.** Nine settings are never read: `DOCFLOW_EXTRACTION_MODEL`, `DOCFLOW_ROUTING_MODEL`, `SESSION_SECRET`, `SENTRY_DSN`, `LLM_OBSERVABILITY_API_KEY`, `WORKER_EGRESS_ALLOWLIST`, server-side `SUPABASE_ANON_KEY`, `STRIPE_PUBLISHABLE_KEY`, `API_BASE_URL` (**Verified**). The model IDs are hardcoded (`extraction.py:36,45`), and the routing ID differs between config and code (`claude-haiku-4-5-20251001` vs `claude-haiku-4-5`). Changing a model in `.env` does nothing, which undermines "pin and log the model ID". Fix: have code read config, or delete the settings. S. Verified.

**M11. A founder can't set the project up from zero using SETUP.md, and there's no record of which migrations are applied.**
- SETUP.md lists migrations 0001–0009 only (`SETUP.md:78-88`), and staging has 25.
- It has no Redis/Memurai install, no `celery beat`, no Supabase redirect-URL step (D-105) and no Stripe webhook step. Step 6 is written as if the reader is an agent ("I'll run a quick check").
- There's no migration-tracking table (**Verified**), so "every prod migration was applied to staging first" can't be shown.
- Fix: rewrite SETUP.md and add a `schema_migrations` table written by each migration (or adopt the Supabase CLI).
- M. Verified.

**M12. There are no request-size limits.** The tenant upload reads the whole body into memory before the 25 MB check (`documents.py:55`). The Console uploads read cap+1, but Starlette has already spooled the full multipart body to disk. The email webhook accepts any JSON size. (Overlaps Phase 6 rate limiting.) S. Inferred.

**M13. Pagination uses OFFSET, not cursors.** The review queue (`apps/api/app/routers/review.py:187`), activity (`packages/core/docflow_core/tenant_home.py:272`) and Console audit (`admin_data_access.py:1592`) all use `OFFSET`; 5.1 requires cursor pagination for the queue and audit log. S–M. Verified.

**M14. The worker's main path is never tested against a real database.** `apps/worker/tests/test_parse_and_extract.py:144-307` monkeypatches `tenant_session`, `read_file` and `extract_document`, so the worker's SQL (where C1, H3 and M3 live) has no real-DB test, and the API tests never run the task. M. Verified.

**M16. Public signup is enabled on the staging Supabase project, although the code says it's disabled.** **Verified:** `GET {SUPABASE_URL}/auth/v1/settings` returns `disable_signup: false`. `apps/api/app/routers/auth.py:5` says "public signup is disabled at the Supabase project level (SETUP.md)", but SETUP.md never says to disable it. Anyone with the anon key (it ships in the browser bundle) can create Supabase Auth accounts. They reach no tenant data (the API finds no local user and returns 403/404), but it's an open door, an email-spam vector through the project's confirmation mailer, and contrary to Section 3's "no public signup". Fix: Supabase → Authentication → turn off "Allow new users to sign up" on staging now (and on prod at creation), add the step to SETUP.md, and add a startup or CI check against `/auth/v1/settings`. S.

**M15. The cost breaker is only checked at intake.** `packages/core/docflow_core/intake_gate.py:55-69` checks it on arrival. Everything already queued, such as a 900-document burst admitted before any spend is recorded, is processed regardless, and the worker never checks before calling the model. S. Inferred.

### Low

- **L1. Floats on money in Console display.**
  - Where: `apps/web/src/components/admin/KpiCards.tsx:20,78-79` (margin computed with `Number`), `TenantTable.tsx:29,63`, `HealthStrip.tsx:40`, `TestBatchPanel.tsx:105`.
  - This is founder display only, never customer data or exports, but it's a literal Section 10 "must not". S.
- **L2. Non-catalog strings a tenant can reach.**
  - Where: `apps/api/app/actor.py:57`, `apps/api/app/deps.py:219`, `apps/api/app/routers/review.py:166`, and the "Something needs your attention" fallback (`ReviewDocumentScreen.tsx:642`). S.
- **L3. Any reviewer can create a tenant-wide rule.**
  - `MappingBody.tenant_wide` (`routers/review.py:147`) is settable by any reviewer, although `matching.py:829` calls it "the founder's deliberate choice". S.
- **L4. UI copy hardcodes a constant.**
  - `apps/web/src/app/admin/tenants/[id]/quarantine/page.tsx:268` says "30 days" instead of reading `ROTATED_ADDRESS_GRACE_DAYS`. S.
- **L5. Dead code and a missing prefix check in storage.**
  - `packages/core/docflow_core/validation.py:449/455` defines `_is_blank` twice.
  - `storage.read_file` doesn't assert the tenant prefix (`storage.py:92`). The paths come from RLS-scoped rows, so this is defence in depth only.
  - `delete_tenant_storage` uses `rmtree(ignore_errors=True)` (`:145`), so a partial delete is silent. S.
- **L6. `tenant_usage_monthly` (Section 9) was never built.** Usage is computed live instead, which meets the intent, but there's no DECISIONS entry. S.
- **L7. Records and cleanup gaps.**
  - CHECKPOINTS.md has no Phase 0–2 entries.
  - The Stripe customer isn't handled at hard delete (7.14).
  - `get_tenant` returns `200 null` for an unknown ID (`routers/admin.py:260-266`). S.
- **L8. The live golden tests skip when the API key is missing** (`apps/api/tests/test_golden_fixture.py:182`), where Section 5 says "neither may be skipped". S.
- **L9. The DB docstring overstates the admin boundary.** `db.py:25-30` says the admin flag "has a matching database-level control". In fact any connection as `docflow_app` can set it (**Verified**: after `set_config('app.is_platform_admin','true')` the role sees all 8 tenants). The real control is the import boundary. See B-5 in section 6. S (docs).

**Done well, one line each:**
- Snapshots are append-only and superseded, never deleted.
- Learned rules skip human-edited fields.
- Example prompting filters by tenant, buyer and approved status and re-checks the storage prefix (`packages/core/docflow_core/example_prompting.py:246-292`).
- The go-live order (Stripe, then invite, then one transaction) is retry-safe.
- The hard-delete purge is complete. **Verified** against `pg_constraint`: every tenant table is purged or cascades, and only the lifecycle log survives.
- Exports are deterministic, verified at runtime, and never floats.
- The signed URLs are sound.
- There's one allowlist module used by every intake path.

---

## 6. BEYOND SPEC: independent recommendations

These are not defects against the spec. They're ranked by impact.

1. **BEYOND SPEC: run the document pipeline as a durable state machine.** Give each document explicit step states (`parsed`, `extracted`, `matched`, `validated`), each step idempotent and resumable, a `pipeline_error` that blocks approval and alerts, and one "reprocess" entry point that the Console exposes.
   - *Why:* H1, H3 and M3 are three symptoms of steps that commit independently with no record of which ran.
   - *Effort:* M.
   - *If deferred:* the first worker restart under load leaves orders in states nobody can see or recover without SQL.
2. **BEYOND SPEC: split the worker into a sandboxed parse service and a trusted ops service, on object storage.**
   - The parse worker gets bytes from storage and writes text and images back. It holds no database, Stripe or Anthropic credentials and has no egress.
   - The ops worker does extraction, matching, Stripe and email.
   - *Why:* this is the only design where 7.11's "no network" and D-003's intent become real, and it removes the blast radius of a parser exploit.
   - *Effort:* M–L, and it depends on H6.
   - *If deferred:* a single malicious TIFF or PDF CVE in the worker exposes every tenant's database and the Stripe account.
3. **BEYOND SPEC: CI against a real Postgres, plus a migration ledger.** Spin up Postgres in CI, apply `supabase/migrations/*` in order, create `docflow_app`, and run the full API suite. Record each migration in a `schema_migrations` table.
   - *Why:* every serious defect this build has found so far (D-016, D-080, D-087, D-088, D-124) sat between layers that CI never exercised.
   - *Effort:* M.
   - *If deferred:* the Phase 6 prod creation has no way to prove parity, and RLS regressions ship silently.
4. **BEYOND SPEC: store each extracted value's raw string alongside its parsed number.** Add `raw_text` per numeric field, next to a `numeric` with generous scale.
   - *Why:* the reviewer and the audit trail should always be able to see exactly what the document printed, whatever the parsing did.
   - *Effort:* S–M.
   - *If deferred:* C1-class bugs stay invisible.
5. **BEYOND SPEC: give the Console its own database role.** Use a separate connection role (for example `docflow_admin`, or `SET ROLE` inside `platform_session`) whose credentials exist only in the API's Console process, instead of a GUC the tenant role can set on itself.
   - *Why:* today the database can't tell an app bug or an SQL injection from a legitimate admin call.
   - *Effort:* M.
   - *If deferred:* isolation stays exactly as strong as the Python import discipline.

**Where I'd have decided differently, against the docs:**
- **D-063 (match in Python, no trigram).** I'd have used `pg_trgm` candidate generation from day one, as Section 5.1 required. The measured cost shows the "rounding error next to the model call" assumption was never checked at the stated catalog size.
- **D-019 (local-disk storage "for this slice").** I'd have used Supabase Storage from Phase 1. The spec names it, and five phases were built on a stand-in that can't be deployed as designed.
- **D-058 (post-extraction steps in their own transactions, failures logged).** I agree extraction must never be lost, but I'd have recorded the failure on the document and blocked approval rather than only logging it.
- **Numeric column scales in 0002.** An extraction product shouldn't pick column scales that are narrower than what documents print.
- **D-086 (browser tests stub the API).** I'd keep it, but add one real-stack smoke test in CI (sign in, open, approve, export) against the CI Postgres. D-089 showed why.
- **D-013's single role for everything** (see recommendation 5).

---

## 7. Triage

**(a) Fix before Phase 6 starts**

| Item | Reasoning |
|---|---|
| C1 | Wrong numbers in exports. The only Critical. |
| H1 | The approval gate can silently vanish. |
| H2 | Human typos bypass every math check. |
| H3 | Approved orders can be corrupted by a restart. |
| H7 | Every fix below needs a CI that runs the database tests and blocks red. |
| H8 | Trivial to fix; exposes every abuse defence. |
| H10 | 7.14 breach; one check. |
| H11 | Billing state can be lost. |
| H9 | 7.15.1 requirement, and delete is irreversible. |
| H6 | Phase 6's prod setup, restore drill and load test all assume real storage. |
| H4 | Phase 6's load test is an exit criterion and will fail without it. |
| H5 (the Celery time and memory limits part) | Cheap, and stops one bad file from freezing the queue. |
| M1 | One-line limit that fails every large PO. |
| M2, M3 | Silent data loss and stranded documents at the same boundary as C1. |
| M4, M5 | Approval audit integrity. |
| M6 | Small bug in a Phase 5 feature. |
| M10 | The model-ID setting must actually pin the model before prod. |
| M11 | Phase 6 creates prod from these instructions and migrations. |
| M14 | Needed to prove the fixes to C1, H3 and M3. |
| M16 | One dashboard toggle; closes a door the code already claims is shut. |

**(b) Fold into Phase 6 (already on its list)**

| Item | Reasoning |
|---|---|
| H5 (the full sandbox split) | Part of "hardening"; see section 6 item 2. |
| M8 | "Log redaction verified" is on the list. |
| M9 | Part of the "web-baseline checks in 7.12". |
| M12 | Part of "rate limiting". |
| M7 | Part of "founder alerting". Small, but the backlog and drift alerts are really Phase 5 scope. |
| M13 | Pagination is a 5.1 item the load test will measure. |
| M15 | Part of "per-tenant cost circuit breaker". |
| L2, L8 | "Error catalog completed and enforced". |
| RUNBOOK, UAT TC-44 onward | Explicitly Phase 6. |

**(c) Defer**

| Item | Reasoning |
|---|---|
| L1 | Founder-only display. Still fix it when touching those files, since it's a literal Section 10 rule. |
| L3, L4, L5, L6, L7, L9 | Polish and documentation. |
| Section 6 items 4 and 5 | Valuable, not blocking. |

---

## 8. Conflicts and open questions

**Claims in the docs that the code or live state contradicts:**
- **CHECKPOINTS Phase 3/4 "CI green" and the Phase 5 "ruff clean, mypy clean".** True locally only. GitHub `main` has been red since 5.3, and 5.6–5.10 were never pushed (H7).
- **Phase 0 exit criterion "CI blocks a deliberately failing test".** `main` is unprotected.
- **D-003 "confirmed by founder": per-file `setrlimit`, SIGKILL, unprivileged user, egress allowlist.** Not implemented (H5).
- **D-019 "tracked follow-up".** Still open five phases later. Section 3 and the architecture doc name Supabase Storage (H6).
- **D-063 "well under a second" for a 50,000-row catalog.** Measured at 1.74 s per line (H4).
- **D-121 "raises the `rollup_stale` alert the section asks for".** It crashes (M6).
- **D-115's open item (edits don't re-validate).** Never closed (H2).
- **`db.py` docstring "matching database-level control".** Overstated (L9).
- **Section 9's `tenant_usage_monthly`** was replaced by live computation with no decision recorded (L6).
- **Section 5.1's "cursor-based" pagination.** Offset is used (M13).
- **7.16.1's banner at 80%.** Deliberately 90% (D-129, founder decision). Not a defect, listed for completeness.

**Open questions for you:**
1. What's the most decimal places your verticals print on a unit price or a quantity? I suggest designing for 6 (C1).
2. Where will the worker run in production (Railway, Render, a VM)? That decides how H5's isolation and egress control are enforced.
3. For destructive Console actions, do you want "sign in again within the last 5 minutes" or Supabase MFA (H9)?
4. Do you agree that **H4** belongs before Phase 6 rather than being discovered by the Phase 6 load test? It's the largest item in section 9.
5. Is it OK to add a Postgres service to CI (free on GitHub)? It means test data lives in CI, not only on staging.

**Could not verify, and what was missing:**
- The owner's invite sign-in: no email provider is configured, and I don't have the founder's credentials.
- The live Stripe webhook: `STRIPE_WEBHOOK_SECRET` is empty.
- `EXPLAIN` on a 2.5M-row items table: none is seeded, and I didn't write millions of rows to staging.
- Branch-protection settings beyond the public API view: `gh` isn't installed and there's no token. The public API already shows `protected: false`.
- Prospect-list company names: the prospect spreadsheet isn't in `docs/`. None of the names I checked appear.
- The live "golden with examples" call: outside the agreed budget.

---

## 9. Proposed fix plan for bucket (a)

In execution order, with dependencies. Not executed.

1. **Restore the safety net (H7, part 1).** Push the 14 local commits. Make CI green by pinning `ruff` and `mypy` in core and fixing what that shows. Protect `main` with the four jobs required. *No dependencies. S.*
2. **Put the database tests in CI (H7 part 2, M14).** Add a Postgres service, apply migrations 0001–0025, create `docflow_app` NOBYPASSRLS, and run the full API suite. Add one real-database integration test that runs `parse_and_extract` with a stubbed model client. *Depends on 1. M.*
3. **Numeric fidelity (C1, M2, M3).**
   - An additive migration widening the numeric scales.
   - A strict numeric-string check in `extraction.py`.
   - A scale check that raises a VAL warning instead of rounding.
   - Confidence clamping.
   - A catch around the main write that ends in `failed` with a code.
   - Tests with 5-decimal prices through both the worker and `apply_edits`.
   - *Depends on 2, so the tests run in CI. S–M.*
4. **Pipeline integrity (H1, H3, M1).** A conditional claim on `processing`, idempotent header and line writes, `needs_review` only after validation (or a blocking pipeline state), a founder alert on step failure, a larger `max_tokens` with a `stop_reason` code, and a 100-line fixture. *Depends on 2. S–M.*
5. **Review integrity (H2, M4, M5).** Re-validate after edits, build acknowledgement text on the server, and require a version token on approve with a row lock. *Depends on 2. S.*
6. **Intake and billing holes (H8, H10, H11).** Postmark basic-auth secret, the suspended-tenant upload refusal, and Stripe event recording in the same transaction with an ordering guard. *Independent. S each.*
7. **Console safety (H9, M6, M10, M16).** Recent-login or MFA checks on delete, clear and cancel. Fix the `rollup_stale` alert. Make the model IDs come from config, or delete the settings. Turn off Supabase public signup on staging. *Independent. S–M; M16 is a dashboard toggle you do yourself.*
8. **Real storage (H6).** Supabase Storage behind `storage.py`'s existing interface, a staging file migration, and the tenant-prefix assertion in `read_file`. *Depends on 2. M.*
9. **Worker limits (H5, first part).** Celery `task_time_limit`/`soft_time_limit`, a worker memory cap, and routing parse tasks to their own queue. *Independent of 8, but do it after 4. S.*
10. **Scale (H4).** Enable `pg_trgm` plus a GIN index on the normalised description, exact-SKU via index, SQL top-K candidate generation feeding the existing Python scorers, and per-tenant fair dispatch for large batches. Re-run the matching benchmark: target under 50 ms per line at 50k items. *Depends on 2 and 4. M–L.*
11. **Reproducible setup (M11).** Rewrite SETUP.md for migrations 0001–0025, Redis, beat, redirect URL and Stripe webhook. Add the migration ledger. Update the README status. *Last, so it documents what 1–10 produced. S–M.*

After step 11, re-run the full suites, the live golden and contamination tests, and the matching benchmark, and write the Phase 5 addendum to CHECKPOINTS.md. Only then start Phase 6.

---

### Appendix: what was run

| Check | Result |
|---|---|
| `packages/core` pytest (against staging) | **451 passed** |
| `apps/worker` pytest | **81 passed, 0 skipped** |
| `apps/api` pytest (against staging, live tests deselected) | **391 passed**, 0 skipped, 3 live deselected (26 min) |
| `apps/api` pytest with no `DATABASE_URL` (what CI runs) | 43 passed, **348 skipped** |
| ruff (core, api, worker, repo root) | clean |
| mypy api / worker | clean |
| mypy `packages/core` (not in CI) | 24 errors |
| web: eslint / tsc / build | clean / clean / ok |
| web: vitest / playwright | 42/42 / 63/63 |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| `pip-audit` on api and worker lockfiles (scratch venv) | no known vulnerabilities |
| Live golden fixture | **passed** |
| Live contamination test | **passed** |
| GitHub `main` protection | `protected: false` |
| Last 5 CI runs on `main` | all failed |
| Local vs remote `main` | 14 commits ahead |
| Live DB role | `docflow_app`, NOBYPASSRLS, owns 0 tables; 35/35 tables with RLS |
| `pg_trgm` installed | no |
| Migration ledger | none |
| Matching benchmark (50k synthetic catalog) | 1.74 s per unmatched line |
| Web `/admin` to a signed-out visitor | HTTP 200 (`/signup` is 404) |
| Supabase `/auth/v1/settings` on staging | `disable_signup: false` (public signup enabled) |
| Numeric casts on staging | `0.00345` → `0.0035`; `12.345` → `12.35`; `95` into `numeric(4,3)` → `DataError` |
| Red-flag greps | no `dangerouslySetInnerHTML`/`innerHTML`/`eval`; one sandboxed iframe; no stray TODO/FIXME; no `os.environ` outside config; no secrets tracked; no deferred-feature code (no signup route, auto-approve, ERP or webhook delivery) found |
