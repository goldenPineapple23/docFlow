# DocFlow — Build Status by Phase

The one-page map of the whole build: every phase and slice, what it covers,
where it stands, and which decisions and migrations belong to it. Use this to
find your way around; go to the linked file for the detail.

| Where to look | What it holds |
|---|---|
| `CLAUDE.md` | The binding rules (Sections 0, 3, 7, 10 of the build prompt, verbatim) |
| `docs/docflow-claude-code-build-prompt-v2.docx` | The full master prompt, including Sections 5, 6, 8, 9 (phase plan, schema, extraction contract) |
| `DECISIONS.md` | Every judgment call, D-001 onward, with context and reasoning |
| `CHECKPOINTS.md` | End-of-phase summaries: built / assumed / open, with test results |
| `supabase/migrations/` | Database changes, applied to `docflow-staging` by hand, in order |
| this file | Phase and slice status, and what is planned next |

**Keeping this file current:** update it at the end of every slice, in the same
commit as the slice. Statuses below are as of **2026-09-28** (Phase 5.5: Stage 0 done, Stage 1 checkpoint done and walked, Stage 2a, 2b and 2c merged (2c: PR #18, `0029` applied to staging 2026-09-28), 2d built and verified on staging, ready to merge; `0029` row counts confirmed by the founder (the only difference: 52 `stripe_webhook_events` test ids from post-migration runs); D-150 settled -- Fly.io, proof spike PASSED 2026-09-28).

**Status key:** DONE = built, tested, committed. BUILT = built and tested but
not yet committed. PLANNED = agreed, not started. Exit criteria are quoted from
Section 6 of the build prompt.

---

## Phase 0 — Foundations · DONE

Repo scaffold; `docflow-staging` Supabase project wired; tenants, users,
platform_admins, admin_actions tables with RLS; invite-only auth (no signup
route); tenant-scoped data-access layer plus the separate audited admin layer;
tenant creation from the Console; tenant-prefixed file storage; CI (lint,
types, tests, dependency audit); CLAUDE.md, SETUP.md, DECISIONS.md, `.env.example`;
the golden fixture with its recorded model response.

- Commits: `d4a108f` (initial), `3b08b50` (Phase 0), `34d0fc7`, `aaca780`
- Decisions: D-001 – D-017 (architecture, queue, worker isolation, nullable
  `users.tenant_id`, Supabase key/JWT handling, pooled-connection fixes)
- Migrations: `0001_foundations`
- **Exit:** founder creates a tenant from the Console and a customer logs in via
  invite; `/signup` is 404; cross-tenant read fails for a tenant user and
  succeeds (with an `admin_actions` row) for a platform admin; CI blocks a
  failing test. (CI ran from Phase 0, but nothing made it block a merge until
  `main` was protected on 2026-09-25 -- see "CI and branch protection" below.)

## Phase 1 — Intake + extraction · DONE

Upload endpoint with full Section 7.11 hardening (magic bytes, size,
decompression and XML defenses, isolated parsing worker); email intake with the
7.16.3 layers and the `quarantined` status; error catalog started; extraction
with structured outputs, raw response stored immutably; status flow
`pending → processing → needs_review | failed | quarantined`; Tier 2 conversion
(`.doc`, `.xls`, `.tif`, `.heic`, `.msg`, `.odt`, `.ods`).

- Commit: `eb76329`
- Decisions: D-018 – D-050 (file cap, storage stand-in, extraction schema
  limits, Postmark, email abuse-layer choices D-027 – D-037, Tier 2 converters
  D-038 – D-050)
- Migrations: `0002_documents`, `0003_email_intake`
- **Exit:** golden fixture extracts exactly; scanned PDF and image reach
  `needs_review`; `.doc` and multi-page `.tif` convert and extract; `.zip`
  gives a coded error; zip bomb and oversized file rejected with worker alive;
  quarantine tests pass.

## Phase 2 — Matching + post-processing · DONE

Buyer identification and auto-creation; SKU matching (exact, then fuzzy with
candidate and score); learned mappings applied first; validation producing
warnings, never corrections; duplicate and change-order detection.

- Commit: `95fbee9`
- Decisions: D-051 – D-080 (buyers not customers, `learned_rules`, name
  normalisation, similarity thresholds, validation tolerances, duplicate links)
- Migrations: `0004_matching_foundations`, `0005_sku_matching`,
  `0006_validation_and_duplicates`
- **Exit:** a corrected mapping on document 1 auto-matches document 2; same PO
  number twice is flagged; non-reconciling totals warn and are not corrected.

## Phase 3 — Human review UI · DONE (2026-09-17)

Side-by-side viewer and editable data; per-field confidence; SKU search and
create-mapping; approve / reject; every edit recorded before/after; immutable
approved snapshot. Plus the fixes found by driving a real browser and the
pre-Phase-4 clean-up.

- Commits: `6705d6f`, `fcba9c0`, `707f183`, `fa43b45` (checkpoint), and the
  polish commits through `58039ab`
- Decisions: D-081 – D-097
- Migrations: `0007_review_and_approval`, `0008_review_action_sequence`,
  `0009_document_previews`
- Checkpoint: `CHECKPOINTS.md` → Phase 3
- **Exit (met 2026-09-17):** a non-technical tester corrected and approved the
  golden fixture in under two minutes; the audit trail showed exactly what
  changed.

## Phase 4 — Export · DONE (2026-09-18)

CSV, Excel, JSON, IIF from the approved snapshot; `exports` records with
SHA-256; signed short-lived downloads; round-trip and byte-identical checks that
also run at runtime (`EXP-004`); one-click "Approve & export".

- Commits: `c1c8f58`, `3d413b7`, `85efeff`, `ba2983a`
- Decisions: D-098 – D-101
- Migrations: `0010_exports`
- Checkpoint: `CHECKPOINTS.md` → Phase 4
- **Exit:** round-trip passes for all four formats; two exports of one snapshot
  are byte-identical.
- **Open:** IIF not yet checked against real QuickBooks Desktop.

## Phase 5 — Founder Console + tenant surface + ops · COMPLETE, awaiting walkthrough and "go" (checkpoint in `CHECKPOINTS.md`)

The largest phase, built as ten slices with a check-in after each. Phase exit
(Section 6): all nine onboarding steps run from the Console against a fake
prospect; Console never calls a parsing/extraction/review function the tenant
surface doesn't (checked by dependency test); full cancel → suspend →
export-window → reactivate and cancel → delete cycles work in staging for all
three cancellation reasons; dashboard KPIs match hand-computed SQL; example
prompting passes the golden fixture and the contamination test live.

| Slice | What | Status | Commits | Decisions | Migrations |
|---|---|---|---|---|---|
| 5.1 | Console foundations: tiers table, email outbox, founder alerts, invites, intake staging | DONE | `a0b9774`, `c150d61`, `e12c599` | D-102 – D-107 | `0011` |
| 5.2 | Catalog and customer-list import (preview, mapping, validation, diff commit) | DONE | `c16be30`, `513e442`, `dc7b27f` | D-108 – D-110 | `0012` |
| 5.3 | Test batch, acting-as review, go-live (Steps 6 – 9) | DONE | `61295b7` | D-111 – D-116 | `0013` |
| — | Deal terms at Create tenant; Console exports | DONE | `f5e763d` | D-117, D-118 | `0014` |
| 5.4 | Operator screens: buyer merge, learned rules (part 1); per-tenant field settings (part 2) | DONE | `f7c6db5`, `c1d81a8` | D-119, D-120 | `0015`, `0016` |
| 5.5 | Founder dashboard from the nightly rollup: attention panel, health strip, tenant list, KPI cards | DONE | `e81f1ec` | D-121 | `0017` |
| 5.6 | Lifecycle: cancel, reactivate, suspend sweep, wind-down and ready-to-delete queues, hard delete; Stripe webhook sync; 7-day billing trial | DONE (walked in the browser 2026-09-23: cancel, suspend, reactivate, queues OK; the final typed-name delete step was not completed; see `docs/walkthroughs/5.6-lifecycle.md`) | "Phase 5 slice 5.6" (hash: see `git log`) | D-122 – D-125 | `0018`, `0019`, `0020` |
| 5.7 | Allowances and quarantine (7.16): plan in `docs/plans/5.7-allowance-quarantine.md`. Also: the customer portal header shows the company name; notices are on the Purchase orders list only, each dismissible; the allowance banner waits until 90% (D-129) while the 80%/100% emails are unchanged | DONE (walked in the browser 2026-09-23 as founder, tenant owner and reviewer: all parts OK; final wording tweaks made from that walk) | "Phase 5 slice 5.7" (hash: see `git log`) | D-126, D-127, D-129 | `0021` (applied) |
| 5.8 | Tenant surface, in four parts. **a: upload page, navigation, customer dashboard, role audit — DONE. b: Activity page (paged, filtered, reviewer + admin) — DONE. c: needs-review digest email (at most one per tenant per 15 min, counts only, owner/admin/reviewer) — DONE. d: Team page for the account's admin (option A: list, invite reviewer, resend, remove) — DONE.** Plan: `docs/plans/5.8-tenant-surface.md` | DONE: all four parts, walked by the founder (5.8d on 2026-09-24) | "Phase 5 slice 5.8a", "5.8b", "5.8c", "5.8d" (hashes: see `git log`) | D-128, D-130, D-131, D-132 (plus fixes D-133, D-134) | `0022` for 5.8c, `0023` for 5.8d (both applied) |
| 5.9 | Billing: plan change from the Console only (Stripe first, prorated; a founding customer keeps the new tier's founding price for the invoices still owed), Billing card with a link to the customer in Stripe, MRR = what paying customers actually pay (founding prices included, trials shown separately), tier price changes by a new version through `scripts/new_tier_version.py` (no editing screen yet). Also: server errors answer SYS-001 instead of "couldn't reach"; the customer header shows "Powered by" + the DocFlow logo and no longer jumps between pages; Redis at 127.0.0.1 (a 2 s IPv6 delay per call on Windows) | DONE (walked by the founder 2026-09-24, including a real plan change in Stripe test mode) | "Phase 5 slice 5.9" (hash: see `git log`) | D-135 – D-140 | `0024` (applied) |
| 5.10 | Approved-example prompting (7.13): off by default, the founder switches it on per tenant in the Console after confirming a live golden run (EXM-001). The buyer is found before extraction from the sender's email or company domain, otherwise from one cheap header read (`claude-haiku-4-5`, only if some buyer could qualify). A buyer needs 10 approved orders; up to 3 of the newest are sent as text plus approved values, never an image or file. The parser's text is now kept per order for this. The reviewer sees "read with N earlier orders". No second pass (founder). Also D-142: every model call is now an `extraction_runs` row -- the daily cost breaker and the health strip's model counts had been reading an empty table. Plus the Phase 5 module-dependency check (`test_console_shared_paths.py`), and the tenant page's **Audit** tab (7.15.3, D-143: lifecycle events and Console actions, newest first; page views on request) | DONE (golden fixture and contamination test passed live; real-stack drive passed on Acme Test Prospect; founder walkthrough passed 2026-09-25 -- one follow-up: the Example prompting page's customer table now centres its Approved, Usable as examples and Gets examples columns) | "Phase 5 slice 5.10" (hash: see `git log`) | D-141 – D-143 | `0025` (applied) |

### Slice 5.7 scope — allowances and quarantine (Section 7.16)

Already in place from earlier phases: the email-intake quarantine rules
(auth failure, attachment cap, unknown-sender velocity — D-030 – D-035) and the
"used / allowance" column on the Console tenant list.

To build:

1. **Metering (7.16.1).** Count documents per tenant per calendar month in the
   tenant's timezone, excluding test-batch, `failed` and `quarantined`; linked
   duplicates count once. Allowances come from `tiers.document_allowance`.
2. **Banners and emails.** Non-blocking banner at 80% and 100% naming the
   number, the tier and the next tier; one owner email per threshold per month;
   an `allowance_reached` founder alert (severity info) at 100%. Documents keep
   processing past 100%.
3. **Abuse ceilings (7.16.2).** `ABUSE_CEILING_MULTIPLIER` (3×) times the
   allowance, and the daily cost circuit breaker. Tripping either quarantines
   new documents (stored, never sent to the model), auto-replies "received and
   held", shows a tenant banner and raises a high-severity alert. In-flight
   documents finish normally.
4. **Intake abuse layers (7.16.3).** Remaining pieces: `intake_rejections` and
   the tenant's "ignored mail" list, quarantine TTL surfacing in the attention
   panel, intake-address rotation with a grace-period auto-reply, opt-in strict
   sender-allowlist mode.
5. **Quarantine screens (7.16.4).** `quarantine_reason` and `quarantined_at`;
   tenant "Held for review" section (count and plain-English reason; owner can
   release `attachment_cap` and `unknown_sender_velocity` only); Console
   per-tenant list with bulk release and type-to-confirm bulk clear; release
   re-runs held documents in received order and only then counts them.
6. **Error catalog (7.16.5).** New `LIM-` / `INT-` entries; the snapshot test
   file changes.

Required tests: 11 attachments quarantines all 11 and nothing else; the 21st
unknown-sender email in an hour is quarantined while a known sender's email at
the same moment is not; tripping the abuse ceiling quarantines the next
document, auto-replies, alerts, and leaves in-flight documents alone; release
keeps received order and then counts against the allowance.

Agreed split with 5.8: 5.7 builds the API, data and a minimal tenant banner;
the fuller tenant dashboard stays in 5.8.

## Phase 5.5 — Remediation of the end-of-Phase-5 review · IN PROGRESS

Inserted by the founder on 2026-09-25 between Phase 5 and Phase 6. Scope:
everything in bucket (a) of `docs/REVIEW-PHASE5.md`, restructuring the
document worker and file storage, matching speed and per-tenant queue
fairness, and the test gaps that let these defects through. Phase 6 does not
start until the founder signs off on 5.5. Every change is a branch and a pull
request; each stage ends with tests green locally and in CI, a checkpoint,
and the founder's "go". The founder's answers to the review's five questions
are D-149 – D-153.

| Stage | What | Status | Decisions |
|---|---|---|---|
| 0 | Safety net: push, CI green, `main` protected (done before 5.5 began); **CI database and the unapproved-skip check** (H7 part 2); core type-checked and pinned in CI | DONE (PR #3, merged 2026-09-25) | D-148 |
| 1 | Data integrity: C1 numeric fidelity end to end (with M2, M3, M14), H1 pipeline ordering, H2 re-validation, H3 guarded status transitions and idempotent jobs, stuck documents. **Plus the two defects the founder's walkthrough found on the real stack (2026-09-27): the review screen said nothing when an edit raised a check (D-166), and a session token one second ahead of this clock was refused as "signed out" (D-167).** | **CHECKPOINT DONE 2026-09-26, awaiting "go"** (`CHECKPOINTS.md`: C1, H1, H3, M1, M3, H2, M4, M5 all closed). 1a DONE (PR #4, D-156); 1b DONE (PR #6, migration `0027`, D-158 – D-160); 1c DONE (PR #7: golden rename, M1 streaming measured, H2/M4/M5, one read budget, every paid call costed, Audit tab on one clock; D-159, D-161 – D-164); named system actors DONE (PR #8, migration `0028` applied and verified on staging 2026-09-26: 3 system actors, no blank lifecycle actor, idle-transaction cap 5 min; D-165); Stage 1 checkpoint run on `b04f16d`; walkthrough fixes DONE (D-166 the review screen, D-167 clock skew, D-169 the line table marking the rows and numbers a check is about, and the live end-to-end suite that catches this class of defect) | D-149, D-154 – D-169 |
| 2 | Security and lifecycle: H8 signed email intake, H10 one lifecycle gate, H9 MFA + step-up, H11 Stripe events (record the event in the same transaction as its effect; ignore an event older than the state already saved; **an event in the same second as the saved state can't be ordered by `created` (one-second resolution), so it re-fetches the subscription from Stripe and saves that, never guesses** -- a webhook-side fetch, not a page-load one, so within 7.15.3 (founder, 2026-09-26); the Phase 6 plan-change reconcile reuses this guard). **Plus two clock items folded in from the D-170 sweep:** `first_past_due_at` written from Stripe's event time rather than the app clock, and tests that a stale and a future-dated Stripe webhook signature are both refused (the 300 s tolerance is real but untested today). **Also carries 2a's deferred `intake_webhook_refused` alert** and the `founder_alerts` insert policy it needs (the `rollup_raise` pattern from 0017), since `0029` is the migration already planned (D-171). **2a (H8) DONE (PR #14):** the inbound webhook now authenticates the provider with Postmark's HTTP Basic credentials, checked before the payload is parsed and before the token is resolved; the per-tenant token identifies the tenant and no longer authenticates the request. A blank credential refuses all inbound mail on purpose (D-171), so RUNBOOK 2.1's cutover order is a requirement: credentials set and deployed, *then* Postmark pointed at the URL carrying them. A refusal logs which reason it was, and **raises a high-severity `intake_webhook_refused` alert in 2c, not 2a** -- a tenant-less alert needs its own RLS insert policy, which needs a migration, and 2c already has `0029`; `test_rls_flags.py` caught the attempt to raise it from the router and located the right home (D-171). **Blocking condition (founder, 2026-09-27): credential enforcement must not go live on an address real customers send to until 2c's alert lands.** A refused request is, from outside, either a misconfigured cutover or an attacker, and the first means no mail arrives at all -- so until the alert exists, the only signal is a log line nobody is watching. Staging and a test address are fine; the RUNBOOK 2.1 cutover on a production intake address waits for 2c. The IP allowlist is log-only with no enforcing branch (D-155); RUNBOOK 2.3 is the confirm-then-enforce procedure and 2.2 the rotation procedure. 12 tests; 10 of them fail with the credential check disabled **2b (H10) DONE (PR #15):** a suspended or pending-deletion tenant can no longer upload -- refused with a new `INT-010` before the file is validated or stored, so it costs nothing; read and export stay open, asserted against `/home`, the order history, one order in full and its export history, in both blocked states (7.14). `cancelling` deliberately does not block. One predicate, `intake_gate.blocks_new_intake`, is shared by both intake channels so the lifecycle answer cannot drift -- which is how the defect existed. Two departures from the review's proposed fix, reasoned in D-172: a new catalog entry rather than reusing INT-006 (whose reader is a buyer whose mail bounced, not the tenant's own user), and the gate reads lifecycle status rather than `intake_address_active` (which is also false before go-live). The refused attempt is recorded in `intake_rejections` (the file is not), so a customer who keeps trying is visible -- a retention signal, not only an audit one. Three drift tests beyond the shared predicate: both real endpoints asserted to agree across four states, a structural test forbidding the status pair inside any condition, and the invariant that the suspend transition sets `status` and clears `intake_address_active` together (they are different columns and only the transition keeps them in step). 14 tests; 3 fail with the gate disabled **2c (H11, clock items #3 and #6, 2a's deferred alert) BUILT, migration `0029` not yet applied to staging:** Stripe events go through `record_stripe_subscription_event()`, a SECURITY DEFINER function called inside the tenant's own session, which records the event id in the same transaction as the status write, applies the ordering guard (older events recorded, not applied; a NULL saved time applies), and cross-checks the customer against the session's tenant. A same-second event fetches Stripe's state with no transaction open and re-checks the guard before saving. `first_past_due_at` is Stripe's event time and `unpaid` no longer resets it. No session can write `stripe_webhook_events` any more; EXECUTE is revoked from PUBLIC **and from Supabase's `anon`/`authenticated`** (which get it by default -- found while building, D-175). A refused inbound webhook now raises a high-severity `intake_webhook_refused` alert, one per reason, never changing the 401 -- **which satisfies 2a's blocking condition once 0029 is applied** (RUNBOOK 2.1). Stripe's clock against ours has one named tolerance, `STRIPE_CLOCK_TOLERANCE_SECONDS` (300 s), enforced at the signature and, on the database's clock, at the event time: an event stamped beyond it is not applied and alerts the founder (D-176). 29 new API tests (22 webhook, 7 refusal alert) plus 5 static core tests | 2a, 2b DONE; **2c DONE** (PR #18; `0029` applied to staging 2026-09-28; on `b540433`: API 477 passed / 3 deselected, core 547 passed, worker 107 passed; CI green); **2d (H9) DONE** (PR #20; lost-device drill passed on staging 2026-09-28, D-177): the Console needs an aal2 session (AUTH-006); seven destructive actions -- hard delete, clear quarantine, cancel, address rotation, buyer merge, go live, tier change -- need a TOTP challenge under 5 min old (AUTH-007, 30 s GoTrue allowance); a wrong code is AUTH-008, mirrored in the web app and kept in step by a test; `CONSOLE_MFA_ENFORCED` ships off, with a startup warning, a Console banner and a founder alert while off; enrol and add a backup at /admin/security; RUNBOOK section 4. No migration. 24 API tests (11 fail with the checks disabled), 4 Vitest, 4 e2e. **Founder enrolled 2026-09-28: two authenticators, both verified** (checked through the Supabase admin API). The backup first failed: Supabase refuses a second factor with the same name (422), and both were named after the date -- fixed in PR #21 (each new authenticator gets a name not already taken; 2 Vitest). A failed enrolment now shows its own catalog entry, **`AUTH-009`**, mirrored in the web app and drift-tested like AUTH-008, instead of the generic "We couldn't reach DocFlow" (founder, 2026-09-28, who also set its next-step wording; 2 e2e). **The e2e suite now fails any test whose browser reaches a host other than this machine** (`apps/web/e2e/networkGuard.ts`, every spec imports it and a check fails the suite if one doesn't) -- one AUTH-009 test draft had reached real staging; shown failing with a test pointed at staging. **Next: RUNBOOK 4.1 steps 4-5, switch `CONSOLE_MFA_ENFORCED` on** | D-151, D-170, D-171, D-172, D-173, D-175, D-176, D-177 |
| 3 | Worker, storage, queue: H6 Supabase Storage, H5 platform-enforced parsing isolation (**host settled, D-150: Fly.io, each parse process in its own network namespace; proof spike PASSED 2026-09-28. Carried in from the spike: hide `/.fly` and `/sys` in a mount namespace, and re-run the probe against the real Upstash and API**), H4 per-tenant fairness, **F-1 separate database logins for API / worker / admin** (propose with cost and effort, then stop for approval) -- **including a login for the Stripe webhook that holds EXECUTE on 2c's event function, with EXECUTE then revoked from `docflow_app`**, which closes the residual risk D-173 names. **Also moves with it (founder, 2026-09-28): 0029's `platform_admin_read` policy on `stripe_webhook_events`** -- a flag policy on `app.is_platform_admin`, so it goes to real login separation with D-173's function grant; likewise 0029's `app.intake_refusal` policies (D-175 §8). **H6 note: signed URLs become cross-clock** -- minted and verified on the app clock today (`signed_urls.py`), one clock because one service does both; on Supabase Storage the expiry is Supabase's clock, so D-170 applies (a named tolerance and a test, or the expiry decided in one place) | PLANNED | D-003, D-150, D-159, D-170, D-173 |
| 4 | Matching performance (H4): `pg_trgm`, measured p50/p95 at 50k items | PLANNED | D-152 |
| 5 | Remaining findings, doc/code contradictions, proposed CLAUDE.md additions; **audit every test that counts a whole table** (the `deal7` pattern) and move each one to data only that test can see, after which staging suites may run concurrently again (RUNBOOK 1.4); **robust test cleanup** (every test that creates data cleans it up in a fixture or `finally`, so a failing test still leaves nothing); **a staging sweep script** that lists tenants named "Acme Test ..." older than a day, with what each holds, and deletes one only on the founder's per-action OK (a stopped run always strands something); **triage the API suite's warnings** (425 on the 2026-09-26 run): list each kind, say which are harmless library deprecations and which point at a real problem in our code -- listed, not fixed (triage done 2026-09-26, D-163: all 439 are test-only; 438 are PyJWT's `InsecureKeyLengthWarning` from short test signing keys); **use a test JWT secret of at least 32 bytes** to clear that noise (founder); **a test that expects the database to refuse a write** must run in a transaction that is always rolled back, or on data it owns, so it can't leave a row behind when the refusal doesn't happen (D-165 incident); *low priority, not a blocker:* **count rows in spreadsheet and CSV orders for free before extraction** (no model call needed), so an oversized order is caught before a paid read (founder, D-163) | PLANNED | D-160, D-163, D-165 |

### Stage 2c and 2d -- agreed with the founder before building (2026-09-27)

Written here so they survive a context reset; until now they lived only in the
working conversation.

**2c (H11, plus clock items #3 and #6, plus 2a's deferred alert):**

- **A SECURITY DEFINER function records the event, checks the ordering guard and
  applies the update, in one transaction** (founder's proposal, adopted over a
  tenant-scoped RLS policy on `stripe_webhook_events`). Why: under the policy
  design any tenant-scoped code could insert an event id for its tenant, marking
  a real Stripe event as seen so Stripe's retry becomes a no-op -- a way to
  suppress a billing event. With the function, only one audited function writes
  the idempotency table. It is also the F-1 direction: a flag policy is enforced
  by our code (the app role can set any flag, D-159), a function grant by the
  database (as 0028 did).
  - Called from **inside the tenant session**, after the existing SELECT-only
    lookup resolves the tenant, so the past-due alert is raised by the existing
    Python `raise_alert` in the same transaction under the existing
    `tenant_raise` policy. The function cross-checks the Stripe customer id
    against the session's tenant and never takes a tenant id from the caller
    (7.5).
  - `SET search_path` on the function; `REVOKE EXECUTE FROM PUBLIC`; the grant to
    `docflow_app` wrapped in 0028's `if exists (pg_roles ...)` guard because CI
    creates that role after migrations run, mirrored in
    `scripts/ci/create_app_role.py` and its agreement test.
  - Test: a tenant session **cannot** insert into `stripe_webhook_events`, run in
    a transaction that is always rolled back (D-165).
- **The same-second Stripe fetch happens outside any database transaction**:
  a short transaction decides (and on same-second records nothing), the fetch
  runs with no transaction open, a second short transaction re-reads and saves.
  That is the `change_tier` bug, not repeated. Tests: a lock probe (another
  connection takes `FOR UPDATE NOWAIT` on the tenant row during the fake fetch),
  a crash between fetch and save (event not recorded, status unchanged, replay
  applies), and a negative check that moving the fetch inside the transaction
  makes the probe fail.
- **The second transaction re-checks the ordering guard, not just the saved
  state** (founder): if a newer event was applied while the fetch was in flight,
  the fetched state is not written over it -- the event id is recorded as seen.
  When the write does happen, the event id is recorded in the **same transaction
  as the status write**. Test: a newer event applied during the fetch is not
  overwritten.
- **Residual risk, named (D-173):** while `docflow_app` holds EXECUTE, any code
  running as it can call the function with a fabricated event id. Closed by F-1
  in Stage 3, below.
- A NULL `stripe_status_event_at` (every existing tenant) **applies** the first
  event, and a test says so.
- `first_past_due_at` from Stripe's event time, and **not reset by `unpaid`**:
  today an `unpaid` event clears it, restarting the cure clock just as retries
  run out.
- Stale and future-dated Stripe signature timestamps are refused, with tests
  (the 300 s tolerance is real and untested today).
- Migration `0029` touches **four** tables -- `tenants`, `stripe_webhook_events`,
  `founder_alerts`, `email_outbox` -- so the backup covers all four, including
  the two that only gain a policy (the founder's standing rule). Deletes
  nothing. The backup SQL, row-count check and "don't merge until verified on
  staging" lead 2c's message, before the PR link.

**2d (H9, MFA and step-up per D-151):**

- A RUNBOOK procedure for a **platform admin who loses their TOTP device**,
  tested on staging, not only written.
- Step-up enforcement switches on **only after the founder's own account is
  enrolled and has passed a challenge** -- never before, so the founder cannot
  lock themselves out of the Console by merging it.
- If enrolling the founder's existing account needs a manual Supabase dashboard
  step, **stop and tell the founder** before building any workaround (D-151).
- The 5-minute freshness check compares GoTrue's time with ours, so it is a
  foreign clock under D-170: a named tolerance, its cost stated, and a test.
  Which claim carries the challenge time is read from a real token after a real
  challenge, not assumed (RUNBOOK 1.6).

### D-150 proof spike -- Fly.io, before Stage 3 (founder, 2026-09-28) -- DONE, PASS

**Result (2026-09-28): PASS.** All 13 probes that had a positive control
passed. The sandbox reached nothing: not the internet (IPv4, IPv6, HTTPS),
DNS, the private-network API and Redis stand-ins, the Fly Machines API or
`/.fly/api`. `169.254.169.254` had nothing listening even from the machine.
Run 1's interfaces FAIL was a measurement flaw (it read `/sys`, the
machine's view) and was re-run rather than stopped on -- stated in D-150.
Two findings go to Stage 3: `/.fly/api` is blocked by file permission, not
the namespace, and `/sys` shows the machine's interface names -- hide both
in a mount namespace. Both apps were destroyed. Evidence:
`docs/spikes/d150-fly-netns/`. **Follow-up (2026-09-28):** the fixed
interfaces check was run against its positive control -- FAIL on the
machine, PASS in the sandbox (run 3) -- so the PASS stands; RUNBOOK 1.7 now
holds the stop-and-report rule, and `CLAUDE.md`'s parsing-worker bullet says
what the spike proved and what Stage 3 still owes.

The plan as agreed:

Runs in parallel with 2c or right after it, not at the start of Stage 3. Full
wording and reasoning in D-150.

- A throwaway Fly app. A parse process launched in its own network namespace
  (`unshare --net` or equivalent) must **fail** to reach: the public internet;
  Fly's private network (other apps' `.internal` addresses, including Redis and
  the API); the metadata and DNS endpoints. The test reports its own evidence.
- Isolation is per parse process, not per machine: the parsing machine itself
  sits on Fly's private network.
- Proposed additions (founder can strike): a positive control for every probe
  (the same probe succeeds from outside the namespace); IPv6 as well as IPv4;
  Fly's local API Unix socket, if the machine has one; no way back out
  (unprivileged user, cannot `nsenter` or bring up an interface).
- **If any part fails: stop and report. The fallback is Cloud Run.**
- The apps hold no customer data and no DocFlow credential, and are deleted
  after the report.

**Then, before any real tenant:** the D-170 clock PR -- #2 first, then #1, #4,
#5, #7.

### Open items parked during the Stage 1 walkthrough (2026-09-27)

Both came out of the founder approving a line whose arithmetic does not
reconcile (`qty 2 x 8.25` against a printed `198.00`) by ticking its check.
That behaviour is correct as specified -- Section 7.3's gate is
acknowledge-and-proceed, and Section 7.7 forbids DocFlow deciding which of
the three numbers is wrong -- so neither of these is a defect.

- **A typed note when acknowledging a high-severity check.** Agreed, not in
  this PR (founder, 2026-09-27). The API already takes a note per
  acknowledgement and the UI sends `null`; requiring one for `high` and
  `critical` would make a deliberate override cost a sentence, recorded in
  `review_actions` with the warning text. Needs a `DECISIONS.md` entry when
  it is built.
- **Whether an export should flag a non-reconciling snapshot.** Parked
  pending evidence, not judgement: the requirement is added to **UAT TC-26**
  under the Phase 5.5 open items below, and the decision follows what real
  QuickBooks Desktop does with such a line. (`EXP-008` today is about
  decimal places QuickBooks cannot hold, not about reconciliation.)

## Phase 6 — Hardening · NOT STARTED

Error catalog completed and enforced by tests; rate limiting; cost circuit
breaker; retry with backoff; dead-letter queue; founder alerting; monitoring
hooks; RLS audit; log-redaction check; web-baseline checks (7.12);
`docflow-prod` created with migrations applied staging-first; backup restore
drill; `RUNBOOK.md`; the full UAT plan run and recorded.

- **Retire HS256 (D-174):** remove the HS256 verification branch and `SUPABASE_JWT_SECRET`,
  move the tests to a local ES256 key pair, then the founder revokes the legacy secret -- in that order.
- **Exit:** every test in `docflow-uat-plan.docx` executed; zero open
  Critical/High defects; restore drill succeeded; the Section 12 checklist is
  fully true.
- Some Phase 6 items already have early versions (alerts table, outbox, cost
  breaker). Phase 6 finishes and audits them; it does not start from zero.

**Phase 6 items added by the founder:**

- **Assert that secrets never reach Sentry, once Sentry exists** (founder's
  question on 2a, 2026-09-27; D-171). Sentry is a `sentry_dsn` setting and
  nothing else today -- no SDK, no `init`, no `before_send` anywhere in the
  codebase -- so `test_email_intake_auth.py` can assert the inbound webhook's
  credentials never reach a log line or a response, and cannot honestly assert
  anything about Sentry. When Sentry is initialised, add a `before_send`
  scrubber and the matching assertion, and extend it to every secret the
  settings hold, not only the webhook credentials.

- **Console plan changes must not hold a database transaction across Stripe
  calls** (founder, 2026-09-26; D-138, D-165). **Do this early in Phase 6:**
  while the lock is held, anything else that needs the tenant row waits up to
  about 90 s. Today `change_tier`
  (`packages/core/docflow_core/admin_data_access.py:967`) locks the tenant row
  (`FOR UPDATE`) and keeps that transaction open while
  `external_services.change_subscription_tier` makes its Stripe requests
  (about 90 s in the worst case). That is why the app role's
  idle-transaction cap is 5 minutes, not 60 s. The item:
  1. Move the Stripe calls outside the transaction. Record the intended change
     first, call Stripe with an idempotency key derived from it, then write the
     result in a short second transaction.
  2. Reconcile: a change Stripe made that DocFlow didn't record (a crash
     between the call and the write) is found and completed, never left
     disagreeing. Tested by killing the process between the two.
     **What calls it:** a scheduled sweep (celery beat, every 5 minutes, like
     the stuck-document sweep) for any recorded change older than a few
     minutes, and Stripe's `customer.subscription.updated` webhook, which
     completes it as soon as it arrives. Never on a read: the tenant page
     shows "plan change pending" but never calls Stripe on page load
     (Section 7.15.3). A change still unresolved after the sweep's retries is
     a `founder_alerts` row.
     **Webhooks arrive twice or out of order.** A repeat is already a no-op
     (`billing_webhooks.py:47`, `ON CONFLICT (id) DO NOTHING` on the event
     id). Ordering is not handled today; that is review finding H11, fixed
     in Phase 5.5 Stage 2 (an event older than the state already saved is
     ignored, and the event is recorded in the same transaction as its
     effect). This item reuses that guard; it does not build its own.
  3. Then revisit lowering `idle_in_transaction_session_timeout` for
     `docflow_app` (migration 0028 sets 5 min) toward 60 s, as a new
     migration plus the CI role script and its agreement test.

---

## Known open items (across phases)

- IIF export not yet validated against real QuickBooks Desktop (Phase 4).
- Stuck-in-processing alert (7.9): built in Phase 5.5 Stage 1b, watching
  `pending` too (D-095, D-158).
- **Very long orders (D-161):** measured and streamed in Stage 1c. Before, a non-streaming
  call failed as DOC-008 past 65-80 lines (a 60-second idle connection drop, not the token
  cap). Now streamed at 128,000 tokens with a 20-minute deadline inside the stuck timeout:
  300 and 600 lines read exactly (249 s / $0.41, 482 s / $0.81); ceiling about 1,000 lines.
  DOC-020 reworded ("enter this order by hand for now", D-162). No fail-fast past ~1,000
  lines: a Haiku pre-count guessed round numbers (600 -> 1,000), D-163; the RUNBOOK onboarding
  checklist covers it instead (founder). Chunking deferred.
- ~~Golden fixture uses a possibly real business name~~ DONE in Stage 1c (2026-09-26):
  renamed to "Acme's Test Coffee House" / `acmetestcoffee.example` in a copy under
  `apps/api/tests/fixtures/golden/`; answers re-recorded, live golden + contamination pass;
  a guard test keeps the old name out of the code (D-159). Staging rows holding the old name
  in their stored model answers wait for the end-of-build cleanup.
- **F-1 (D-159): about 50 RLS policies are keyed on `app.*` settings any
  connection can set** -- enforced by code and guard tests today, not by the
  database. Stage 3: separate logins for API, worker and admin path, with
  policies granted to those roles (about 2-3 days, $0).
- **Blank audit actors (D-159, D-165):** fixed by migration 0028 (not yet applied): the 7 staging
  rows get named system actors, a purged person's events name `deleted-account`, and the actor
  is required from then on.
- **Four** stranded test tenants on staging, all 2026-09-26 (the fourth, "Acme Test M5 Lock"
  `c0f43325…` with 1 document, from a pooled connection dropped mid-test in Stage 1c, D-162), left because a run
  ended before its own cleanup: "Acme Test Sweep A" / "Acme Test Sweep B"
  (worker suite, the pooled-connection test's cleanup bug fixed in `21550bd`)
  and "Acme Test Distributor -- acting edit" with its 1 document (API suite,
  `test_acting_as.py`, run stopped mid-test). They are the whole gap between
  backup_0027 (46 documents / 8 tenants) and the post-0027 count (47 / 11).
  Left for the end-of-build cleanup; delete only on the founder's OK.
- Custom per-tenant fields deferred until a prospect needs one (D-120).
- Error-catalog messages use ASCII " -- " instead of real dashes; a switch was
  offered.
- **Hosting documents out of date after D-150 (2026-09-28):** `docflow-deployment-hosting.docx`
  and `docflow-tech-stack-costs.docx` still say Railway or Render; the build prompt's deploy line
  says the same (founder's document). `CLAUDE.md`'s parsing-worker bullet was updated in the
  founder's wording (D-150 follow-up PR).
- **HS256 retired (founder, 2026-09-28, D-174):** both keys in use are the new `sb_` kind and
  `SUPABASE_JWT_SECRET` is now blank on staging (real ES256 sign-in verified with it blank). Phase 6
  removes the HS256 branch and moves the tests to a local ES256 key pair; the founder revokes the
  legacy secret in the dashboard **last**.
- **M16 closed on staging (2026-09-28):** the founder turned off "Allow new users to sign up";
  `/auth/v1/settings` now returns `disable_signup: true`. SETUP.md Step 1 now says to do it, so
  `docflow-prod` gets it at creation. The automated check the review suggested (startup or CI
  against `/auth/v1/settings`) is not built.
- Pending document updates recorded in `DECISIONS.md`: ToS-vs-offboarding
  notice period (D-009), pricing doc missing allowances, build-timeline Phase 5
  wording (D-008), features doc missing approved-example prompting.
- `RUNBOOK.md` started in Phase 5.5 (migration backups); Phase 6 completes it -- several constants and the
  parser-upgrade process must be documented there.
- The worker's local venv was missing `httpx` (declared by core); installed 2026-09-23, worker suite is now 74 of 74. Run it with the worker's own venv, not the API's (which lacks `xlwt`, `pillow_heif`).
  That fix was local only: `httpx` never reached `apps/worker/requirements.lock.txt`
  (anthropic 1.6 switched to `httpx2`, so it stopped arriving transitively), and CI
  installs core with `--no-deps` -- the worker CI job was red from then on
  (`test_celery_app`, `No module named 'httpx'`). Fixed 2026-09-25: `httpx` declared
  in the worker's `requirements.txt` and pinned in its lock (same pins as the API).
  Verified in a clean venv built exactly as CI builds it: worker 81 passed.
- CI and branch protection (2026-09-25, review `docs/REVIEW-PHASE5.md` H7 part 1):
  GitHub CI is green on `main` again -- run #39 (`4755eeb`, the httpx fix) and
  run #40 (`07f2912`) passed all four jobs; it had been red since 2026-09-19.
  `main` is now protected: pull request required (no approval count, so the
  founder can merge their own), all four jobs required -- `api (lint, typecheck,
  test, audit)`, `core (lint, test, audit)`, `web (lint, typecheck, test, build,
  e2e, audit)`, `worker (lint, typecheck, test, audit)` -- branch must be up to
  date, no bypass for administrators, no force pushes or deletions. Every change
  now goes through a branch and a pull request. H7 part 2 (Phase 5.5 Stage 0,
  D-148): CI now runs the core, api and worker suites against a local Supabase
  stack with every migration applied, as `docflow_app` (NOBYPASSRLS), and fails
  on any skipped test not approved in `.github/approved-skips.txt` (none are).
  A **fifth job, `web-live`**, was added 2026-09-27 (D-166): the review screen
  in a real browser against the real API, real Postgres and a real sign-in,
  with nothing stubbed. It is the only suite that can fail when the screen and
  the server disagree after a write, which is the gap D-086 knowingly left and
  the Stage 1 walkthrough fell into. **The founder needs to add it to the
  required checks on `main` in the GitHub branch-protection settings** -- the
  other four were added by hand and this one has to be too.
- Browser walkthroughs: `docs/walkthroughs/README.md` is the index, one file
  per phase or slice from Phase 0 to 5.10, to walk in order before Phase 6
  (QA/UAT). Files for Phases 0–4, 5.1–5.5 and 5.8a–c were added 2026-09-25;
  `scripts/make_walkthrough_files.py` generates their test files (good orders
  in all 21 formats, 15 hostile files, extras) into `DocFlow/walkthrough-files/`.
  Problems found while writing them, fixed the same day (no migration):
  D-144 "Reopen for review" for approved/exported/rejected orders (the screen
  had locked them while its hint promised editing would reopen them); D-145 a
  failed order shows its catalog reason, and every catalog message that says
  "DocFlow has been alerted" now really raises a founder alert
  (`document_failed`, `unsafe_file_refused`, `unverified_sender_held`, guarded
  by `test_alert_promises.py`); D-146 a damaged .doc/.xls/.msg is DOC-005, not
  "PowerPoint or Visio"; D-147 the Console tenant page links to its orders.
  Also: the two review seed scripts now pick the oldest of the same-named
  "Acme Test Distributor" tenants. Suites after: core 451, worker 81, web 42 +
  63 browser; the affected API files 89 passed. Full API suite re-run
  2026-09-25 after the 5.10 walkthrough fix (Example prompting page's customer
  table columns centred): 391 passed, 3 deselected.
- Latest suites (2026-09-25, Phase 5 checkpoint): core 443, api 385 (0 skipped), worker 80, web 42 unit + 60 browser; live golden, golden-with-examples and contamination tests passed. mypy (api, worker), web lint and typecheck clean; ruff clean (the 3 old findings in the proof-of-concept `docs/parse_pos.py` fixed in a follow-up commit).
- Fixed 2026-09-24 from the founder's walkthrough: the Console's typed-name delete failed for any tenant whose rows point at each other (D-133, now guarded by a live-schema test); a signed-out visitor saw "We couldn't reach DocFlow" instead of being sent to sign in (D-134).
- Fixed in 5.9: a server error (500) now answers SYS-001 in the catalog's words instead of "We couldn't reach DocFlow" (D-136).

- 5.7 not yet built (deliberately): a per-tenant override of the daily AI-cost ceiling (global constant for now), and the full tenant dashboard, audit-log view and roles UI (slice 5.8).
- Tenant B ("Acme Test Lifecycle B") still sits in the wind-down queue from the 5.6 walkthrough; finish or leave it.

- Test data: everything in staging is test data (leftover test tenants, five revoked test platform admins). The founder will clean up in a QA pass after all phases, alongside walkthroughs of every phase and slice.
- Example prompting (D-141): orders approved before 2026-09-25 have no stored text, so they count towards a buyer's 10 but can't be shown as examples; `scripts/seed_example_history.py` builds a test buyer's history through the real pipeline. A second pass with examples for low-confidence orders was left out by founder decision.
- **Before the first real customer:** an email provider (the founder is setting one up with the domain). Until then every invite, notice and digest waits in the Console Outbox and must be sent by hand, and the inbound intake address cannot receive real mail.
- Digest opt-out per person: decided yes, but later (needs a settings page).
- `RUNBOOK.md` exists since Phase 5.5 with the migration backup procedure (section 1). Still to add in Phase 6: the constants (CLAUDE.md 7.15.4; `constants.py` is their single home until then), tier price changes (`scripts/new_tier_version.py`, D-137), the restore drill and the parser-upgrade process.
- **Stripe setting, before the first real customer:** the account currently cancels a subscription after 90 days of an unpaid invoice (seen on Acme Test Prospect: "Auto-cancels Dec 18"). Policy is that the founder decides suspension (D-125), so set Settings → Billing → Subscriptions and emails → failed/past-due invoices to leave the subscription past due. Only the founder can change it.
- Sandbox leftover: Acme Test Prospect's founding coupon was created before the invoice-count fix (D-138) and discounts one extra invoice (19 Dec). Test data only; correct it in Stripe or leave it.

- **Phase 5.5 open items (2026-09-25):**
  - `backup_0026` (document_headers, document_lines; RLS on, no policies) is kept on staging until the founder says to drop it (D-156).
  - **Postmark IP allowlist: log-only until confirmed.** When the Stage 2 webhook authentication ships, the allowlist records source addresses but doesn't refuse. Trigger to switch to enforcing: the first real inbound mail after the Postmark account exists; confirm the observed addresses against Postmark's published list using the RUNBOOK procedure, then flip enforcement (D-155).
  - RUNBOOK.md still needs, with Stage 2: the IP-confirmation-and-enforce procedure and the webhook credential rotation procedure.
  - UAT TC-26 now requires a real QuickBooks Desktop import of high-precision amounts and rates; refuse-vs-warn (EXP-008) is decided on that evidence (D-157). **It also requires importing a non-reconciling line** (`qty 2 / rate 8.25 / amount 198.00`) and recording what QuickBooks does with it -- recalculates the amount, rejects the line, or keeps it as given. Whether an export should flag an acknowledged non-reconciling snapshot is decided on that evidence (founder, 2026-09-27).
  - `scripts/seed_demo_data.py` and `scripts/seed_merge_demo.py` create reviewable orders with no model answer (28 on staging, D-156); Stage 1b adds the database rule and fixes the scripts.
  - **Clock sweep -- rule accepted, work scheduled (D-170, founder 2026-09-27).** The rule: a timestamp the database will later compare is written by the database; a foreign clock (GoTrue, Stripe, the browser) gets a named tolerance and a test. Order, set by the founder: **#2 `deletion_scheduled_at` first, ahead of #1, because it guards an irreversible action**; #3 (`first_past_due_at`) and #6 (Stripe signature-timestamp tests) fold into Stage 2; #1, #2, #4, #5, #7 are a separate PR right after Stage 2 that **must land before any real tenant is onboarded**; display-only items no action. Full list and reasoning in D-170.

## Deferred by decision (Section 3 — do not build)

Native ERP integrations, invoice module, advanced change-order diff UX,
webhook/API delivery, formal customer-list management, auto-accept,
secondary-model verification, rule proposals, analytics, mobile, roles beyond
the four, non-English support, EDI/portal intake, multi-currency edge cases,
multi-entity refinements.
