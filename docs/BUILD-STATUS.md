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
commit as the slice. Statuses below are as of **2026-09-30** (latest: security PR #30 and 3b (#31) merged; the 3c design proposed, below). Earlier summary, as of 2026-09-29 (Phase 5.5: Stages 0, 1 and 2 done -- 2a-2d merged (PRs #14, #15, #18, #20, plus #21 and #22), the audit-findings design (#23) and the D-170 clock PR (#24) merged, Stage 2 checkpoint written; **Stage 3 design agreed 2026-09-29; 3a merged (PR #26, D-179; `0030` on staging); the test-run lock merged (PR #27, D-180); card billing built (D-181), migration `0031` awaiting staging; 3b next**; `0029` row counts confirmed by the founder (the only difference: 52 `stripe_webhook_events` test ids from post-migration runs); D-150 settled -- Fly.io, proof spike PASSED 2026-09-28).

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
| 2 | Security and lifecycle: H8 signed email intake, H10 one lifecycle gate, H9 MFA + step-up, H11 Stripe events (record the event in the same transaction as its effect; ignore an event older than the state already saved; **an event in the same second as the saved state can't be ordered by `created` (one-second resolution), so it re-fetches the subscription from Stripe and saves that, never guesses** -- a webhook-side fetch, not a page-load one, so within 7.15.3 (founder, 2026-09-26); the Phase 6 plan-change reconcile reuses this guard). **Plus two clock items folded in from the D-170 sweep:** `first_past_due_at` written from Stripe's event time rather than the app clock, and tests that a stale and a future-dated Stripe webhook signature are both refused (the 300 s tolerance is real but untested today). **Also carries 2a's deferred `intake_webhook_refused` alert** and the `founder_alerts` insert policy it needs (the `rollup_raise` pattern from 0017), since `0029` is the migration already planned (D-171). **2a (H8) DONE (PR #14):** the inbound webhook now authenticates the provider with Postmark's HTTP Basic credentials, checked before the payload is parsed and before the token is resolved; the per-tenant token identifies the tenant and no longer authenticates the request. A blank credential refuses all inbound mail on purpose (D-171), so RUNBOOK 2.1's cutover order is a requirement: credentials set and deployed, *then* Postmark pointed at the URL carrying them. A refusal logs which reason it was, and **raises a high-severity `intake_webhook_refused` alert in 2c, not 2a** -- a tenant-less alert needs its own RLS insert policy, which needs a migration, and 2c already has `0029`; `test_rls_flags.py` caught the attempt to raise it from the router and located the right home (D-171). **Blocking condition (founder, 2026-09-27): credential enforcement must not go live on an address real customers send to until 2c's alert lands.** A refused request is, from outside, either a misconfigured cutover or an attacker, and the first means no mail arrives at all -- so until the alert exists, the only signal is a log line nobody is watching. Staging and a test address are fine; the RUNBOOK 2.1 cutover on a production intake address waits for 2c. The IP allowlist is log-only with no enforcing branch (D-155); RUNBOOK 2.3 is the confirm-then-enforce procedure and 2.2 the rotation procedure. 12 tests; 10 of them fail with the credential check disabled **2b (H10) DONE (PR #15):** a suspended or pending-deletion tenant can no longer upload -- refused with a new `INT-010` before the file is validated or stored, so it costs nothing; read and export stay open, asserted against `/home`, the order history, one order in full and its export history, in both blocked states (7.14). `cancelling` deliberately does not block. One predicate, `intake_gate.blocks_new_intake`, is shared by both intake channels so the lifecycle answer cannot drift -- which is how the defect existed. Two departures from the review's proposed fix, reasoned in D-172: a new catalog entry rather than reusing INT-006 (whose reader is a buyer whose mail bounced, not the tenant's own user), and the gate reads lifecycle status rather than `intake_address_active` (which is also false before go-live). The refused attempt is recorded in `intake_rejections` (the file is not), so a customer who keeps trying is visible -- a retention signal, not only an audit one. Three drift tests beyond the shared predicate: both real endpoints asserted to agree across four states, a structural test forbidding the status pair inside any condition, and the invariant that the suspend transition sets `status` and clears `intake_address_active` together (they are different columns and only the transition keeps them in step). 14 tests; 3 fail with the gate disabled **2c (H11, clock items #3 and #6, 2a's deferred alert) BUILT, migration `0029` not yet applied to staging:** Stripe events go through `record_stripe_subscription_event()`, a SECURITY DEFINER function called inside the tenant's own session, which records the event id in the same transaction as the status write, applies the ordering guard (older events recorded, not applied; a NULL saved time applies), and cross-checks the customer against the session's tenant. A same-second event fetches Stripe's state with no transaction open and re-checks the guard before saving. `first_past_due_at` is Stripe's event time and `unpaid` no longer resets it. No session can write `stripe_webhook_events` any more; EXECUTE is revoked from PUBLIC **and from Supabase's `anon`/`authenticated`** (which get it by default -- found while building, D-175). A refused inbound webhook now raises a high-severity `intake_webhook_refused` alert, one per reason, never changing the 401 -- **which satisfies 2a's blocking condition once 0029 is applied** (RUNBOOK 2.1). Stripe's clock against ours has one named tolerance, `STRIPE_CLOCK_TOLERANCE_SECONDS` (300 s), enforced at the signature and, on the database's clock, at the event time: an event stamped beyond it is not applied and alerts the founder (D-176). 29 new API tests (22 webhook, 7 refusal alert) plus 5 static core tests | 2a, 2b DONE; **2c DONE** (PR #18; `0029` applied to staging 2026-09-28; on `b540433`: API 477 passed / 3 deselected, core 547 passed, worker 107 passed; CI green); **2d (H9) DONE** (PR #20; lost-device drill passed on staging 2026-09-28, D-177): the Console needs an aal2 session (AUTH-006); seven destructive actions -- hard delete, clear quarantine, cancel, address rotation, buyer merge, go live, tier change -- need a TOTP challenge under 5 min old (AUTH-007, 30 s GoTrue allowance); a wrong code is AUTH-008, mirrored in the web app and kept in step by a test; `CONSOLE_MFA_ENFORCED` ships off, with a startup warning, a Console banner and a founder alert while off; enrol and add a backup at /admin/security; RUNBOOK section 4. No migration. 24 API tests (11 fail with the checks disabled), 4 Vitest, 4 e2e. **Founder enrolled 2026-09-28: two authenticators, both verified** (checked through the Supabase admin API). The backup first failed: Supabase refuses a second factor with the same name (422), and both were named after the date -- fixed in PR #21 (each new authenticator gets a name not already taken; 2 Vitest). A failed enrolment now shows its own catalog entry, **`AUTH-009`** (PR #22), mirrored in the web app and drift-tested like AUTH-008, instead of the generic "We couldn't reach DocFlow" (founder, 2026-09-28, who also set its next-step wording; 2 e2e). **The e2e suite now fails any test whose browser reaches a host other than this machine** (`apps/web/e2e/networkGuard.ts`, every spec imports it and a check fails the suite if one doesn't) -- one AUTH-009 test draft had reached real staging; shown failing with a test pointed at staging. **Second lost-device drill with enforcement on PASSED 2026-09-28 (D-178)**: backup-only sign-in, stale step-up refused, fresh one accepted, dashboard removal leaving only enrolment, re-enrol; drill admin revoked and deleted. **`CONSOLE_MFA_ENFORCED` is on** for the local API (`.env` and the running process agree). A CI timing race in `test_console_mfa.py` was found and fixed (D-178) | D-151, D-170, D-171, D-172, D-173, D-175, D-176, D-177 |
| 3 | Worker, storage, queue: H6 Supabase Storage, H5 platform-enforced parsing isolation (**host settled, D-150: Fly.io, each parse process in its own network namespace; proof spike PASSED 2026-09-28. Carried in from the spike: hide `/.fly` and `/sys` in a mount namespace, and re-run the probe against the real Upstash and API**), H4 per-tenant fairness, **F-1 separate database logins for API / worker / admin** (propose with cost and effort, then stop for approval) -- **including a login for the Stripe webhook that holds EXECUTE on 2c's event function, with EXECUTE then revoked from `docflow_app`**, which closes the residual risk D-173 names. **Also moves with it (founder, 2026-09-28): 0029's `platform_admin_read` policy on `stripe_webhook_events`** -- a flag policy on `app.is_platform_admin`, so it goes to real login separation with D-173's function grant; likewise 0029's `app.intake_refusal` policies (D-175 §8). **H6 note: signed URLs become cross-clock** -- minted and verified on the app clock today (`signed_urls.py`), one clock because one service does both; on Supabase Storage the expiry is Supabase's clock, so D-170 applies (a named tolerance and a test, or the expiry decided in one place) -- **settled 2026-09-29: the expiry is decided in one place, our own signed links with the API streaming from Storage.** **Also carried from the Stage 1 checkpoint (D-163):** a run row before the model call, so a worker killed mid-call still records the call's cost | **IN PROGRESS** -- design agreed 2026-09-29 ("Stage 3 -- agreed with the founder before building", below); order 3a -> 3e. **3b MERGED 2026-09-30 (PR #31, main `5810a54`, D-182). 3c: design approved 2026-10-01 (Q1-Q10); BUILT and CI GREEN 2026-10-01 on `0be5422` (D-183; founder's Q11-Q14 decided during the build); migration `0034` awaiting staging; the Fly staging run (RUNBOOK 8.1) owed; see "3c build".** **3a BUILT** (D-179; branch `phase55/stage3a-task-limits`, migration `0030`), including reactivation option C (**3a MERGED, PR #26**). **Card billing, between 3a and 3b: BUILT** (D-181; branch `phase55/card-billing`, migration `0031` waiting on staging; see "Card billing with a 7-day trial"). **Also (founder, 2026-09-29): a second test run against the same database refuses to start** -- an advisory lock in the API and worker suites (D-180, RUNBOOK 1.4). `0030` applied to staging 2026-09-29 after the founder's backup (`backup_0030`: documents, tenants, RLS on). Staging suites on `621141f`: worker 134 passed / 2 skipped (the two prefork tests, Linux only; they pass in CI), API 518 passed / 1 failed / 3 deselected, core 564 passed. The one API failure, `test_a_failed_tenant_creation_rolls_back_everything_including_the_file_move`, counts every tenant on staging and saw the count fall from 20 to 19 during the test -- something else was writing to staging at that moment; a failed creation cannot remove a tenant. Rerun alone: the file 13 passed, the test 3 of 3 passed. D-163 moved to 3c | D-003, D-150, D-159, D-163, D-170, D-173 |
| 4 | Matching performance (H4): `pg_trgm`, measured p50/p95 at 50k items. **Also (founder, 2026-09-29): audit the ~23 broad `except` blocks on the document path.** Each one turns *any* exception into a data outcome -- DOC-005 (parsing, conversion), DOC-021 (saving, validation) or VAL-016 (buyer identification, matching, duplicate detection) -- so a real bug in our code can be shown as a problem with the customer's file. After the audit only the exceptions each block expects get a catalog code; anything else fails loudly as our error. Found while designing 3a's timeouts, not in 3a's scope | PLANNED | D-152 |
| 5 | Remaining findings, doc/code contradictions, proposed CLAUDE.md additions; **audit every test that counts a whole table** (the `deal7` pattern) and move each one to data only that test can see, after which staging suites may run concurrently again (RUNBOOK 1.4); **robust test cleanup** (every test that creates data cleans it up in a fixture or `finally`, so a failing test still leaves nothing); **a staging sweep script** that lists tenants named "Acme Test ..." older than a day, with what each holds, and deletes one only on the founder's per-action OK (a stopped run always strands something); **triage the API suite's warnings** (425 on the 2026-09-26 run): list each kind, say which are harmless library deprecations and which point at a real problem in our code -- listed, not fixed (triage done 2026-09-26, D-163: all 439 are test-only; 438 are PyJWT's `InsecureKeyLengthWarning` from short test signing keys); **use a test JWT secret of at least 32 bytes** to clear that noise (founder); **a test that expects the database to refuse a write** must run in a transaction that is always rolled back, or on data it owns, so it can't leave a row behind when the refusal doesn't happen (D-165 incident); **two audit-trail findings from the second lost-device drill (D-178; founder: fixed before any pilot, with tests; design settled 2026-09-28)** -- (1) refused Console and step-up attempts are recorded server-side (AUTH-006/007); AUTH-008 is never browser-reported, and Supabase's database audit log was checked and records nothing on this project, so that gap is documented and Supabase's rate limit covers wrong-code guessing (its behaviour measured with the test account at build time); 5 refusals in 15 min for one account raise one high-severity founder alert per window, set only after measuring what a normal sign-in and step-up produce; (2) the outcome is a second, append-only `admin_actions` row referencing the intent row (succeeded, or failed with its code; no migration), and an intent with no outcome is shown in the Console as crashed midway, never as done; *low priority, not a blocker:* **count rows in spreadsheet and CSV orders for free before extraction** (no model call needed), so an oversized order is caught before a paid read (founder, D-163) | PLANNED | D-160, D-163, D-165, D-178 |

### Stage 3 -- agreed with the founder before building (2026-09-29)

Written here before any code, as for 2c and 2d. The founder's go for Stage 3
came on 2026-09-29: **build all of it, in the order 3a -> 3b -> 3c -> 3d -> 3e**
(ahead of schedule). Each slice is its own branch and PR. Items marked
*proposed* are mine and wait for the founder's answer before they are built.

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

**3b -- H6, Supabase Storage.** Agreed:
- **fixed per-document keys for derived files** (preview, extracted text), so
  a repeated write overwrites instead of leaving an orphan (from 3a's
  side-effects review);
- a private bucket behind the existing `save_file` / `read_file` interface,
  keys prefixed `tenants/{id}/` and the prefix enforced on read as well as
  write;
- staging's files **copied** into the bucket, the local copies kept until the
  copy is verified.

**Downloads use our own signed links, and the API streams the file from
Storage** (founder). This **settles the H6 / D-170 note**: the expiry is
minted and verified by the API on one clock, so there is no foreign clock to
tolerate, and no customer ever sees a Supabase URL or the storage host.
Storage connects through Supavisor with a pool of its own (see 3e).

**3b detailed design -- Q1-Q5 ANSWERED 2026-09-30; awaiting the founder's
final sign-off. Nothing is built until then.** The founder's answers are
recorded in each item as **Decided**.

*Where things stand today.* `docflow_core/storage.py` writes to a folder on
the machine running the code (`storage/`, 2,973 files and 3.3 MB on this
machine). It already builds every path on the server (`tenants/{id}/{area}/
{random}{ext}`, and `staging/{intake}/...` before a tenant exists), and it
checks the prefix on **write** only: `read_file(path)` takes no tenant, so
nothing stops code from reading another tenant's path. Our own signed links
and the API streaming the file are already in place (D-089), so nothing
changes for customers or the web app. The API and the worker call storage
from 11 places. On Fly the API and the worker run on different machines, so
a local folder can't work in production.

1. **How DocFlow talks to Storage: Supabase's S3-compatible endpoint, with
   an access key used only for Storage (Q1).**
   - Recommended: `boto3`, pinned, with a Storage S3 access key (Supabase
     dashboard -> Storage -> S3 access keys). That key reaches Storage and
     nothing else.
   - The alternative is the Storage REST API with
     `SUPABASE_SERVICE_ROLE_KEY`. That key also administers sign-in (it
     creates users and makes invite links) and bypasses RLS on the REST
     API. Today only the API holds it. This route would put it on the worker
     too, the process that opens hostile files, until 3c moves parsing out.
     With the S3 key, a worker compromise reaches files but not accounts.
   - Cost of the recommendation: one more dependency (`boto3`) and three
     more settings: `STORAGE_S3_ENDPOINT`, `STORAGE_S3_ACCESS_KEY_ID`,
     `STORAGE_S3_SECRET_ACCESS_KEY` (documented in `.env.example`; the
     founder creates the key). The 3c parse service still holds no storage
     key.
   - **Decided (Q1): the S3 access key.** Recorded caveat: Supabase S3 keys
     are project-wide, not per-bucket or per-tenant. A leaked key reaches
     every tenant's files, so tenant isolation for files rests entirely on
     item 3's checks in our own code, not on the key.
   - Every call: 5 s connect and 30 s read timeouts, and up to 3 tries with
     backoff. Only on requests that are safe to repeat: every write goes to a
     key nobody else writes, so a repeated PUT is the same PUT. The worst
     case, about 2 minutes, fits inside every 3a limit (the tightest is
     export at 5 minutes).
2. **One private bucket, `docflow-files`, created by migration `0033`
   (Q2).**
   - The bucket is private, with a 25 MB object limit (`MAX_FILE_SIZE_BYTES`)
     and no public URL.
   - `storage.objects` gets **no policies**. A customer's own sign-in token
     reaches no file directly; only DocFlow's S3 key does. A test signs in
     as a tenant user and asserts Storage refuses them.
   - Why a migration and not the dashboard: CI's local stack gets the same
     bucket from the same file. `0033` inserts one row into
     `storage.buckets` and touches no existing table, so **I propose no
     backup for it** (the rule is backup-first; this is the question).
   - **Decided (Q2): no backup for `0033`,** as a standing rule rather than
     a one-off: a migration that only creates Storage buckets and touches
     no existing table skips the backup. RUNBOOK 1 records the rule.
     Separately, staging has had no backups since 2026-09-30 (RUNBOOK 1.3),
     so a fresh full backup is taken before the 3b rollout starts (item
     11, step 1) as a restore point for the cutover.
   - CI: `supabase/config.toml` turns `[storage]` on (it is off today).
3. **The tenant prefix is checked on read as well as write (agreed; this is
   how).**
   - `read_file(tenant_id, path)` replaces `read_file(path)`, and every
     caller passes the tenant from its own session.
   - `read_staging_file(intake_id, path)` is the only way to read a
     `staging/` file.
   - Before any network call, a path must match
     `tenants/{that tenant}/{uploads|exports|onboarding|derived}/...`, with no
     `..`, no empty segment and no backslash. Otherwise the call raises.
   - Test: tenant A's session asks for a path under tenant B, and the call
     is refused with Storage never contacted. This joins the 7.5 isolation
     tests.
4. **Fixed keys for derived files (agreed; this is the layout).**
   - Preview: `tenants/{t}/derived/{document_id}/preview`.
   - Extracted text: `tenants/{t}/derived/{document_id}/extracted.txt`.
   - A retry overwrites the same key, so it leaves no orphan. The preview's
     media type is already stored on the document (`preview_media_type`) and
     is also set on the object. `derived` joins `STORAGE_AREAS`.
   - Originals and exports keep their random names. Each is written once,
     before its row exists, so a retry can't repeat it.
   - Existing rows keep their old paths. Nothing is renamed.
5. **The copy of staging's files into the bucket (agreed: copy, keep the
   local files, only files a row still references, report orphans). This is
   how:**
   - A script, `scripts/copy_storage_to_bucket.py`. It is a dry run unless
     given `--apply`, and running it twice is safe.
   - It collects every path a row references:
     - `documents`: `storage_path`, `preview_storage_path` and
       `extracted_text_path`;
     - `exports.storage_path`;
     - `onboarding_intake_files.storage_path`;
     - `catalog_imports.storage_path`.

     Soft-deleted rows are included, because their data is kept until a hard
     delete.
   - Each file is uploaded under **the same key**, so **no database row
     changes**.
   - Each copy is checked by reading it back from the bucket and comparing
     its SHA-256 with the local file (and with the row's own SHA-256 where
     the table has one).
   - Report: copied; already there and identical; referenced but missing
     locally (listed); and the orphan count (files no row references, left
     behind).
   - The local `storage/` folder is kept until the founder says otherwise.
     It is only on this machine.
6. **A Storage outage (Q3).**
   - **Upload from the web app:** the file wasn't saved, so the document
     doesn't exist. Proposed new catalog entry **DOC-025** "We couldn't save
     this file", audience both:
     - message: "DocFlow couldn't store your file just now, so it wasn't
       received and nothing was processed.";
     - action: "Upload it again in a few minutes. DocFlow has already been
       alerted.";
     - a founder alert, `storage_unavailable`, **at most once per hour for
       the whole platform** (Decided, Q3), not once per tenant: an outage
       hits every tenant at once, and per-tenant alerts would send N alerts
       an hour for one incident.
     - How, with no new migration (settled 2026-09-30): the open-alert
       dedupe index (`idx_founder_alerts_open_dedupe`, 0011) is unique
       across all tenants and applies regardless of RLS. Each tenant session
       raises the alert for its own tenant (the existing `tenant_raise`
       policy) with the key `storage_unavailable:<UTC hour>`, the hour taken
       from the database's clock (as `dedupe_per_utc_day` does, D-170 #7).
       The first tenant in the hour writes the row; the rest get the dedupe
       conflict and write nothing. Trade-off: the alert names the first
       tenant that hit it, not a count, because a tenant session can't update
       another tenant's row. Every failure is still logged with its tenant.
   - **Email intake:** answer the webhook with a **5xx, never a 403**
     (Postmark retries non-2xx inbound webhooks but stops on a 403), so
     Postmark sends the email again later, and raise the same alert.
     Nothing is lost. A test asserts the status is 5xx.
   - **Worker reading the original (Decided, Q3: an outage must not fail
     documents).** Today's sweep would: `MAX_PROCESSING_ATTEMPTS` is 3 and
     `STUCK_PROCESSING_TIMEOUT_MIN` is 30, so an outage longer than about
     90 minutes ends in DOC-022 "worker stopped". So in 3b a Storage read
     failure logs `storage_read_failed` and returns the document to
     `pending` **without using an attempt** (the attempt counter is put
     back in the same update). *As built: it stays `processing` with its
     attempt given back -- 0027 has no `processing -> pending`; see "3b
     build" below.* The pending sweep re-enqueues it after the
     timeout, a pending document is never failed for waiting, and the
     sweep's existing `document_stuck` alert (once per tenant per day)
     tells the founder how many are waiting. This is a narrow, Storage-only
     version of 3d's `processing -> pending` wait; 3d extends the same
     transition to provider outages. That read is outside every broad
     `except`, so it can't be relabelled DOC-005.
     - **Only an outage waits.** Connection errors, timeouts and 5xx from
       Storage return the document to `pending`. A **404 (object missing)
       is not an outage**: it is a data fault, and waiting would retry it
       forever. It fails the document loudly, with the same catalog code and
       founder alert as a hash mismatch (item 9).
     - Tests: an unreachable Storage leaves the document `pending` with its
       attempt count unchanged, three times in a row; a 404 fails it once,
       with the alert.
   - **Previews and extracted text:** best effort, unchanged. A failure
     never touches the document's status.
   - **Export and catalog import:** they fail with their existing codes and
     are retried by the user. (Q3 covers these too, if you want DOC-025's
     wording there.)
7. **Tenant hard delete: remove the files first, then the rows (Decided,
   Q4).**
   - Today the rows are deleted and committed, then the folder is removed
     with errors ignored. A failure leaves the customer's files behind with
     nothing recording it.
   - On Storage, removing a prefix means listing it and deleting in batches
     of 1,000, so a partial failure is more likely.
   - Order: delete every object under `tenants/{id}/` and check the listing
     is empty. **Only then** run the existing database transaction.
   - If the file removal fails, nothing in the database has changed: the
     tenant is still `pending_deletion` (the only state `delete_tenant`
     accepts, LIFE-006), the founder sees the error, and runs the delete
     again.
   - If the database step fails after the files are gone, running the delete
     again finishes it. The founder has already typed the name to confirm an
     irreversible delete, so files going first is the direction already
     chosen.
   - **No new `deleting` status** (considered and dropped 2026-09-30). By
     the time a delete can run, the intake gate already blocks email and
     uploads for a `pending_deletion` tenant. The one write still possible
     is an export, which 7.14 keeps available until deletion. So, after the
     database transaction commits, a **final sweep** lists the prefix again
     and removes anything written during the delete.
   - The deletion event records how many objects were removed, including
     the final sweep's count separately. A test writes an export object
     between the file step and the database step and asserts the final
     sweep removes it.
   - No new alert and no new migration.
8. **Tenant creation's staging copy uses Storage's server-side copy.** The
   rollback is unchanged: a failed creation deletes the copies, and a
   committed one deletes the staging originals.
9. **Check the original's hash on every read (Decided, Q5: yes).**
   `documents.content_sha256` already exists. The worker compares it with
   the bytes it reads before parsing. A mismatch fails the document loudly
   with a new catalog code (audience both, with a founder alert), rather
   than extracting the wrong file or only logging it. The same code covers
   an original that is missing from Storage (item 6). It costs one hash
   per read. It guards against a wrong or corrupted object, which is rare.
10. **Tests.**
    - Product code has one backend, Supabase Storage. The suites run
      against the real bucket: staging's when run from this machine, the
      local stack's in CI. Unit tests that shouldn't touch the network get
      an in-memory fake, defined in the test folders and never importable by
      product code.
    - New tests: the read-side prefix check (3); a tenant token refused by
      Storage (2); fixed keys overwritten by a retry, with one object left
      (4); the copy script's dry run, apply, verify and orphan report
      against a fake (5); DOC-025, its platform-wide alert and the 5xx to
      Postmark (6); a Storage read failure returning the document to
      `pending` without using an attempt (6); the hash mismatch failing
      loudly (9); files removed before rows in a hard delete, a failed
      removal leaving the database untouched, and the final sweep (7).
    - **The rollback-test fix carried into 3b (approved earlier):**
      `test_a_failed_tenant_creation_rolls_back_everything_including_the_file_move`
      asserts that no tenant named "Acme Test Rollback" exists and that its
      own intake is unlinked, instead of counting every tenant on staging.
      The file check moves from the local folder to the bucket.
11. **Rollout, in order.**
    1. The founder takes a fresh full backup of staging (RUNBOOK 1.1),
       since staging has none left (item 2).
    2. The founder creates the S3 access key and puts the four settings in
       the root `.env` (four as built; see "3b build" below).
    3. The founder applies `0033` on staging (it creates the bucket).
    4. I run the copy script as a dry run, then with `--apply`, and report
       the counts.
    5. The switch: the code that writes to Storage is deployed.
    6. **Delta copy:** I run the copy script with `--apply` again straight
       after the switch. Anything uploaded between step 4 and step 5 was
       written to the local folder by the old code; this second run copies
       it (running twice is safe, and it verifies by SHA-256). Its report
       must show zero "referenced but missing".
    7. The staging suites run.
    8. The walkthrough: upload, review, preview, export and download, a
       Console catalog import from an intake file, tenant creation from an
       intake, and a hard delete of a test tenant.
    9. Merge.

    From the switch onward, nothing reads the local folder.
12. **Cost.** Storage holds 3.3 MB today. At the Section 5.1 envelope
    (7.15.3 puts it at about 50,000 documents), with previews and exports,
    that is a few GB. It is covered by the plan's included storage and egress, so
    there is no new line item. I'll confirm against the Supabase pricing
    page when building.

**3b build -- IN PROGRESS 2026-09-30 (branch `phase55/stage3b-design`).**
The offline part is built and tested; what is left needs the founder's
machine (staging and the local `storage/` folder). Where the build differs
from the design above, or adds to it:

- **Four settings, not three:** Supabase's S3 endpoint needs the project's
  region, so `STORAGE_S3_REGION` joins the other three (`.env.example`).
- **Upload checksums off:** Supabase supports no S3 upload checksums and
  current boto3 sends one on every PUT, so the client sends them only when
  an operation requires one. **3 tries in all** (`total_max_attempts`; boto3's
  `max_attempts` counts retries, which would have been 4).
- **An outage keeps the document `processing`, not `pending`** (item 6).
  0027's state-machine trigger has no `processing -> pending`, and 3b adds no
  migration for it. Same guarantee: the attempt is given back and the claim
  re-stamped, so the stuck sweep retries every `STUCK_PROCESSING_TIMEOUT_MIN`
  and `decide()` never reaches the cap. The document shows as Processing
  while it waits. 3d's wait brings the real `pending` with its own migration.
- **New catalog entries beyond DOC-025** (for the founder's review):
  - **DOC-026** "The stored file doesn't match this order" -- a missing
    original (404) or a hash mismatch (items 6 and 9); alert `document_failed`.
  - **EXP-010** "This file can't be downloaded right now" -- a ready export
    Storage can't hand over; alert `storage_unavailable`.
  - **LIFE-007** "The tenant's files couldn't all be removed" -- the
    founder's error when the hard delete's file step fails (item 7).
- **Console catalog import** during an outage: the upload answers DOC-025;
  a file that can't be read back fails the import as IMP-009 ("nothing was
  imported ... start it again"). Reads elsewhere in the Console fall to the
  general SYS-001.
- **The review screen's viewer** treats any Storage failure like a missing
  file: a 404 for the file, never a 500 for the screen. **Corrected after the
  founder's walkthrough (2026-09-30):**
  - The screen used to say "a format a browser can't display", beside an
    Open button that led to a bare 404.
  - The viewer link now also returns `unavailable`, the catalog entry
    **DOC-027** "The original can't be shown", whenever nothing can be read
    (missing, refused, or Storage down). The viewer shows DOC-027 with no
    link.
  - A preview that can't be read falls back to the original.
  - An outage here raises `storage_unavailable` like every other reader, and
    shows **DOC-028** "The original can't be shown right now" ("reload in a
    few minutes"), not DOC-027 ("ask the sender"). Founder's review of
    `aefd428`: the tenant's next step differs. DOC-028 does not claim the
    file is fine: during an outage DocFlow can't know that.
- **Tenant creation cleanup is best effort:** removing the copies after a
  failed creation, or the staging originals after a committed one, never
  replaces the error (or the success) the founder sees. Anything left is an
  orphan the copy script reports.
- **Hard delete's final sweep** is recorded as its own admin action,
  `tenant_delete_final_sweep`, with its count (or its failure); the deletion
  event and `tenant_delete` carry `objects_removed` from the first step.
- **CI:** `supabase/config.toml` turns Storage on; every job with a local
  stack runs `scripts/ci/local_storage_env.py`, which points the four
  settings at the runner's own S3 endpoint and refuses anything else.
- **Test files left in the staging bucket:** the staging suites now write to
  the real bucket. The Console tests clean up their own objects; other
  suites' test tenants may leave objects behind, which the copy script's
  orphan count and a later clean-up handle. Not a data risk (test data only).

Built and tested offline: core 671 passed (+3 live-bucket tests that run
where Storage is configured), worker 103 passed, API the same 24
environment-only failures as the base branch plus the new webhook test.
Still to do, on the founder's machine: the staging suites (including
`test_storage_outage_api.py`, the rewritten Console tests and the live-bucket
tests), then the rollout in item 11.

**Rollout on staging, 2026-09-30 (founder's machine):**
- Step 1: backup schema `backup_3b` (documents 54, exports 11,
  onboarding_intake_files 2, catalog_imports 22; live = backup on all four),
  the tables whose rows point at files. The files' own backup is the
  untouched `storage/` folder, plus a zip of it outside the repo (2,973
  files).
- Steps 2-3: the key's four settings are in `.env`, and `0033` is applied.
  The bucket exists, and it refuses both a public URL and the anon key for
  an object that exists (HTTP 400 for each).
- Step 4: dry run, then `--apply`. **89 copied and verified by SHA-256**,
  0 missing locally, 0 failed, 2,877 orphans left in place. **14 rows
  flagged and left out (founder: option (b)):**
  - **7 hash mismatches.** Seed and test scripts store an altered hash on
    purpose, to dodge duplicate detection: `seed_review_walkthrough.py`
    hashes file + document id (5 rows); `seed_demo_data.py`'s "resend"
    hashes the text rather than the `.xlsx` (1 row); the
    golden-with-examples row's hash has an extra `x` (1 row). Six of the
    seven are in Acme Test Distributor.
  - **7 made-up paths with no file behind them** (`tenants/seed/po.txt`,
    `tenants/{id}/seed/...`), written by the merge-demo seed and the
    acting-edit, reapprove and M5-lock tests.
  - Consequence: these orders show no original in the viewer. The script
    therefore exits 1 in this environment, not the 0 RUNBOOK 7.2 describes
    for a clean run.
  - **Pass criterion for the delta copy:** the flagged list is exactly
    these 14 and nothing new, and "referenced but missing locally" is 0.
    The baseline dry run is saved for the comparison.
- Step 7 (staging suites), first worker run: **28 failed / 110 passed / 2
  skipped.** The 35 database-backed worker tests had never run against the
  build; the offline "103 passed" didn't include them. Two causes, both
  fixed:
  - **Product bug: the bulk delete never worked on Supabase.**
    `DeleteObjects` answered 400 "must have required property 'Body'":
    Supabase reads that body only when it is labelled `application/xml`, and
    boto3 doesn't label it. Every tenant hard delete would have stopped at
    LIFE-007. The in-memory fake couldn't show it; single-object delete and
    server-side copy were checked on staging and work. Fixed in
    `S3Backend` with a request hook. New live test,
    `test_a_hard_delete_empties_the_tenants_folder_in_the_real_bucket`:
    it fails with the hook removed and passes with it. **Watch the first CI
    run:** CI runs this test against the local stack's Storage, not the
    hosted one.
  - **Test fixture:** `WorkerTestTenant.create_pending_document` stored a
    random `content_sha256`, so the hash check (Q5) failed every order it
    made with DOC-026. It now stores the real hash. In the API suite, a
    DOC-026 failure means the same fixture problem, not a storage one.
  - The failed run's clean-up removed its test tenants' rows but not their
    files, so about 28 dead test tenants' objects remain in the staging
    bucket. The copy script's orphan count is local files only, so they
    don't affect the delta-copy check. They are listed before anything is
    removed.
- **Staging suites on the final code (`e3e8641`, 2026-09-30):**
  - core: 678 passed, 1 skipped (the customer-token live test, which needs
    `SUPABASE_JWT_SECRET`, blank since D-174; the anon-key test covers it);
  - worker: 139 passed, 2 skipped (the prefork tests, Linux only; CI runs
    them);
  - API: **568 passed, 3 deselected**, nothing failed or skipped. Then, after
    the DOC-027 viewer fix `aefd428` (new API code and 3 new API tests),
    **571 passed, 3 deselected** on `aefd428`. RUNBOOK 1.4 says 571. The
    DOC-028 follow-up commit changed one API test's expectation and the
    viewer's cause-to-code branch. It was checked with the whole review API
    file on staging (35 passed), core (678 passed, 1 skipped) and every
    linter; CI's full run covers the rest.
  - web, on `aefd428`: Vitest 59 passed; the full browser suite 73 passed
    (72 plus the DOC-027 test). The DOC-028 commit doesn't touch the web app.
  - live (`pytest -m live_api`, paid): **3 passed**. That's the golden
    fixture, the golden fixture with examples, and the example-contamination
    check, required because this stage changed the example read path
    (7.13). **The live example tests use a stand-in read:** their examples
    come from a fixture file. Reading examples from the real bucket is
    proven separately against staging (`test_example_prompting_db.py`, 10
    passed: `save_file` into the bucket, then `select_examples` with no
    stand-in). So the bucket read and the model call with examples are each
    tested, but **not end to end in one run**. *Stage 5 item (founder,
    2026-09-30):* add a real-bucket read to a live example test.
  - The first API run (11 failed) found stale `storage._resolve` imports in
    the example-prompting tests, and a refused stored path answering 500 in
    the review viewer. The fix is D-182's addendum: every reader handles a
    refused path; a path under another tenant's folder raises a critical
    `storage_path_cross_tenant` alert; IMP-010; and a build guard.
- **Founder's walkthrough (`docs/walkthroughs/3b-storage.md`), 2026-09-30:
  steps 1-6 pass.** Each step was checked against the bucket and the
  database:
  - upload: the original sits under `uploads/` with a matching hash, and its
    extracted text is at the fixed `derived/` key;
  - export: under `exports/`, with its hash matching the one recorded;
  - catalog import: under `uploads/`, with a matching hash;
  - tenant from intake: the file was copied inside Storage to `onboarding/`,
    and the staging original is gone;
  - **hard delete: the folder went from 5 objects to 0, and the deletion
    record says `objects_removed: 5`** (the bulk-delete fix, proven live);
  - a bad path: the viewer shows DOC-027 (built after step 6 first showed
    the wrong message).

  Two faults outside 3b turned up, the Next.js dev cache and seed-script
  data, both recorded here.
- **Delta copy, 2026-09-30 (after the switch at 15:21 UTC):** nothing to
  copy. The pass criterion "0 referenced but missing locally" assumed
  nothing would be written after the switch, but the tests and the
  walkthrough wrote. **Restated: nothing left to copy, and every newly
  flagged row is either already in the bucket or explained.** It holds:
  - 49 new rows are in the bucket (written after the switch);
  - 1 is a made-up test path, `tenants/seed/seed.txt`, on "Acme Test
    Examples" (goes into the seed-data item below);
  - 1 is the intake file row of the hard-deleted walkthrough tenant (see
    the retention question below).

  The original 14 flagged rows are unchanged.
- **Bucket clean-up, 2026-09-30:** 228 tenant folders; 10 belong to
  existing tenants and 218 have no `tenants` row at all (310 objects).
  Every one of the 218 was written inside one of today's recorded test
  runs:
  - worker run 1 (the failed clean-up run): 58;
  - the three full API runs: 46, 46 and 47;
  - the API "affected files" run: 17;
  - worker runs 2 and 3: 2 and 1;
  - the viewer tests: 1.

  Staging folders: none at the rollout copy (no intake file row pointed at
  `staging/`). Those created since, by the walkthrough intake and the
  Console tests, were removed by tenant creation and by the tests' clean-up.
  No row points at `staging/` now.

  **Deleted, in this order:**
  1. The founder ran the backup check for all 218 ids against
     `backup_3b.documents`. The result was empty ("No rows returned").
  2. The founder approved.
  3. The 218 folders were deleted by their saved ids, with a `tenants`-row
     re-check before each one: **218 folders, 310 files removed**, none
     skipped.

  Before: 229 folders, 450 files (the 228 plus one in-flight test folder).
  After: **10 folders, all existing tenants.**

  *Out of order:* the delete ran **during** the final API run. The founder
  had since asked for it to run after (the D-160 kind of overlap). Nothing
  outside the list was touched, since the 310 files removed are exactly the
  list's, and the run passed.
- **Second clean-up, 2026-09-30 (after the final runs):** 48 leftover test
  folders (75 files), none with a `tenants` row. The founder ran backup
  check 2 against `backup_3b.documents` (empty: "No rows returned") and
  approved. **48 folders and 75 files deleted, 0 skipped.** The bucket went
  from 58 folders and 214 files to **10 folders and 139 files**, with **no
  tenant-less folder left**.
- **3b MERGED 2026-09-30 (PR #31, main `5810a54`).** PR run green: api 571,
  core 679, worker 141, web 73, web-live 3. Left to the founder: dropping
  `backup_3b` on staging (RUNBOOK 1.3), and deleting the temporary venvs in
  `C:/Users/NK/AppData/Local/Temp/dfv`. The local `storage/` folder and its
  zip are kept.
- **Stage 5 (founder, 2026-09-30): the API suite's test clean-up never
  deletes its tenants' files.** Each full staging API run leaves about 46
  tenant folders and 73 files in the bucket. The worker suite's clean-up
  deletes them. Fix the shared API test fixtures in Stage 5; until then,
  the clean-up procedure above clears them.
- **Question for the founder (data retention, 2026-09-30, from before 3b):
  a hard delete leaves the linked onboarding intake behind.** Intakes carry
  no `tenant_id`, only `linked_tenant_id`, so the delete doesn't reach
  them.
  - The files themselves are removed, because they were moved under
    `tenants/{id}/onboarding/` at tenant creation.
  - The `onboarding_intakes` row stays, with the prospect's name and contact
    email, and so do its file rows, pointing at objects that no longer
    exist.
  - The Console's intake page reads only those rows, so it lists the files
    as if present (no error page, but a stale listing).
  - CLAUDE.md 7.14 says deletion removes the tenant's business data.
    Whether a linked intake counts is the founder's decision. It doesn't
    block 3b.
- **Stage 5 question (founder, 2026-09-30): the review viewer finding an
  order's original missing tells no one.** The worker alerts on DOC-026
  (`document_failed`), but the viewer only logs
  `viewer_stored_file_missing`. A missing original is a data-integrity
  fault, so the founder probably wants an alert, deduplicated per order.
- **Stage 5 question (founder, 2026-09-30): a catalog import failed by the
  stuck sweep raises no alert.** The sweep marks an import left in `parsing`
  failed with IMP-009 and tells no one. CLAUDE.md 7.9 requires an alert for a
  document stuck past the timeout, and a stuck import is the same silent
  failure for the founder. Exports have `exports_not_finishing` (more than 3
  in a day); imports have nothing. To decide in Stage 5, not built in 3b.
- **Known issue for Stage 5** (the sweep and robust test cleanup): seeded
  rows with a faked `content_sha256` fail DOC-026 if they are ever read
  again for extraction (the hash check, Q5). Fix the seed scripts to store
  the real hash, and repair or remove these 14 rows. **Also (founder's
  walkthrough, 2026-09-30):** `seed_merge_demo.py` inserts orders with no
  order total and no lines, and never runs validation. The review screen
  therefore shows "Everything checked" with a required field empty.
  Approval re-validates (H2) and would refuse, so nothing wrong can be
  approved. Still, seeded orders must go through the pipeline, or at least
  through validation, so they don't look clean when they aren't.
- **CI, 2026-09-30: every test passed on `58c8008`; only the dependency
  audit failed**, on two advisories published that day (PyJWT, Next.js).
  They were fixed in their own PR (#30, "Security upgrades" below), merged to
  `main` first, then `main` was merged into this branch:
  - **no conflict**; both lock files now pin `boto3==1.43.105` and
    `PyJWT==2.15.0`;
  - the lock files are plain `pip freeze` output (there is no lock tool), so
    each was checked by installing it into a fresh Python 3.13 environment
    the way CI does, then core with `--no-deps`: `pip check` clean, a fresh
    `pip freeze` identical to the lock file, `pip-audit` clean (api and
    worker);
  - web: `npm ci`, next and eslint-config-next at 16.3.8, `npm audit`
    0 vulnerabilities;
  - DOC-028 re-checked at `58c8008` (founder): it doesn't contain "The file
    itself is fine".

**3c -- H5, the full part: the parse service.** Agreed:
- **Bytes in, text and images out.** The worker sends the file, the service
  returns the parts. The service holds **no storage key, no database login
  and no model key**, and **refuses to start in production mode unless
  isolation is active**.
- **Every parse process** gets:
  - its own network namespace, and a mount namespace hiding `/.fly` and `/sys`
    (the D-150 spike's two findings);
  - an unprivileged user;
  - `setrlimit` memory and CPU caps;
  - a SIGKILL wall-clock timeout;
  - one subprocess per file, never reused.
- Tier 2 conversion (LibreOffice, image libraries, `.msg`) moves into it.
- **CI requirement (founder):** CI builds the parse service from **the same
  Linux image production uses**, runs it, and exercises **the real
  namespaces and limits**, including the hostile fixtures against the running
  service, not only unit tests of the isolation code. Dev is Windows, where
  none of this can run, so **CI is the only place isolation runs before
  production**. In dev the service runs the same code path without
  namespaces; in production mode that is a refusal to start.
- **The re-probe against the real Upstash and the real API happens in Stage
  3**, on a Fly staging deployment. It is priced first, and the founder
  approves the monthly number before anything is stood up.

**3c detailed design -- APPROVED 2026-10-01; BUILDING.** Proposed
2026-09-30 and revised twice after the founder's reviews. Q1-Q10 are
answered (end of this section), and the founder's second review added
changes 1-5 (io_uring, userfaultfd and x32 in the filter; swap; a CPU
quota as well as a CPU budget; token and alert-dedupe tests; the Fly
gate before any production deploy), all folded in below. Then: "build
3c".

*Where things stand today.* Every hostile file is opened inside the Celery
worker process, the same process that holds the database login, the
Storage S3 key and the Anthropic key. LibreOffice is the only subprocess
(a 120 s timeout, nothing else). No namespace, no `setrlimit`, no
unprivileged user: D-003's controls were never built (review H5). The
places that open a file a stranger sent:

| Where | What it opens | Library |
|---|---|---|
| worker, `parse_and_extract` | re-validation (`validate_upload`), Tier 2 conversion and `.msg`/`.eml` unwrapping (`conversion.prepare_artifacts`), Tier 1 text (`_inner_blocks`) | pdfplumber, python-docx, openpyxl, Pillow, pillow-heif, olefile, xlrd, defusedxml, LibreOffice |
| worker, `_store_preview` | TIFF/HEIC preview (`previews.build_preview`) | Pillow, pillow-heif |
| worker, `parse_import` | catalog and customer-list files (`catalog_parsing.parse_table`) | openpyxl, xlrd, csv |
| API, upload / email intake / Console staging | the gate before storing: magic bytes, the zip's entry list, the first bytes of each XML part (`validate_upload`) | Python's own `zipfile`, `zlib`, `re` |

**The founder's pre-build check on a real Fly machine (2026-09-30).** The
founder asked, before anything is built, whether the service can create a
per-job cgroup v2 memory limit on a Fly machine. Checked on a throwaway
app (`docflow-spike-cgroup-ekqxmh`: shared-cpu-1x, 1 GB, `iad`, no
services, no IP, no credential; destroyed afterwards, `fly apps list`: no
apps). Script, Dockerfile and output: `docs/spikes/3c-cgroup-check/`.
Every check that needs a control had one beside it (RUNBOOK 1.7).
- **cgroup v2 memory limits are not available as Fly delivers the
  machine.** Kernel `6.12.105-fly`, booted with `cgroup_enable=memory
  swapaccount=1`. Fly's init mounts the cgroup **v1** controllers (memory,
  pids, cpu/cpuacct and the rest) under `/sys/fs/cgroup`, and a v2
  hierarchy at `/sys/fs/cgroup/unified` with **no controllers**. A
  controller can belong to only one hierarchy, so v2 has no `memory.max`.
- **Moving `memory` to v2 failed.** Root could unmount the v1 memory
  hierarchy, but the controller was not handed to v2: `/proc/cgroups`
  still showed it on v1 hierarchy 8 with 5 cgroups lingering. Taking
  Fly's setup apart at startup would be fragile anyway.
- **The cgroup v1 memory controller works per job** (run 2, all PASS):
  one process over a 256 MiB limit was killed, while 400 MiB with no limit
  survived as the control; **4 processes x 120 MiB, together over 256 MiB,
  were stopped**; the limit held inside the full sandbox (namespaces, uid
  10001, no capabilities); the sandboxed job couldn't raise its limit or
  move out (EACCES), and with `/sys` hidden the files weren't there at
  all (ENOENT); root could still change the limit; emptied job cgroups
  were removed cleanly; a v1 `pids.max` of 20 stopped a fork loop at 19.
- **The founder's point, confirmed:** the same 4 x 120 MiB processes, each
  under its own 256 MiB `setrlimit` and no cgroup, **all survived**.
  `setrlimit` multiplies with the processes, as the founder said.
- **v1 has no "kill the whole group" switch** (v2's `memory.oom.group`):
  after the family test one process was still left in the cgroup. So the
  supervisor must kill the whole job itself on any out-of-memory kill
  (item 3).
- **seccomp works on Fly's kernel** (run 1, 2b/2c/2e PASS): every call on
  the founder's list was answered by the filter, both as root and when
  uid 10001 loaded it itself under `no_new_privs`. `clone3` got ENOSYS,
  and a normal thread and a normal fork still worked.
- **Why seccomp matters here, measured:** without the filter, uid 10001
  with no capabilities **could** create a user namespace
  (`unshare(CLONE_NEWUSER)` succeeded; `max_user_namespaces` is 3716),
  then clone a new network namespace from inside it, and could use
  `keyctl`. With the filter, none of those worked.
- **Block devices are visible on the machine:** `vda`, `vdb`, `vdc`, 8
  `loop`, 16 `nbd`, all root-only. That is the control for the new `/dev`
  test (A13).
- No swap on the machine (`/proc/swaps` empty); 985 MB of usable memory
  on a 1 GB machine.
- Method notes, stated plainly: run 1's 1l and 1m results said nothing
  about the design (the job files they probed were never created, because
  v2 had no controllers), and are superseded by run 2's A6 and A7. A
  re-check line produced from PowerShell (`controllers=[]`) was read on
  this machine, not on Fly; the evidence file says so, and the valid
  re-read is beside it.

1. **What moves into the parse service.** Everything in the first three
   rows of the table above. The worker keeps: reading the original from
   Storage and checking its SHA-256 (3b), sending the bytes, checking the
   answer (item 6), turning returned text into the text preview (string
   work, no file), the model call, and everything after it. Exports stay
   in the worker: it writes those files itself, and the round-trip check
   reads back only bytes DocFlow wrote. **The API's gate (last row) stays
   in the API (Q1: yes);** the same check runs again in the sandbox before
   anything else.
   - **Afterwards the worker and the API can't parse.** pdfplumber,
     python-docx, Pillow, pillow-heif, olefile, xlrd and defusedxml leave
     both requirement files, and LibreOffice leaves the worker's machine.
     openpyxl stays in the worker for writing `.xlsx` exports only. A
     dependency-graph test (like the `adminDataAccess` one) fails the build
     if `conversion`, `catalog_parsing` or any of those libraries is
     importable from `apps/worker` or `apps/api` product code.
2. **The service.** A new `apps/parse/`, its own requirements and lock
   file, its own Dockerfile.
   - **One image, built once.** Base `debian:trixie-slim` (Debian 13),
     pinned by digest, so Debian's own Python is 3.13 (what CI uses) and
     its `python3-seccomp` binding is available. LibreOffice and
     `python3-seccomp` are pinned by Debian package version. CI and Fly run
     the same file, and the dependency audit covers its lock file. (The
     check machine ran Debian 12; CI is the first place trixie is proven.)
   - **Private only, reached over Flycast (founder's item 4, confirmed).**
     The parse app has **no public IP address** and no `[http_service]`.
     Flycast still needs a services section for Fly's proxy to route to the
     app at all (Fly's Flycast docs: "either an `[http_service]` section or
     `[services]` sections"). So the app has a `[[services]]` block, plain
     HTTP (Flycast is HTTP-only; no TLS handler, no `force_https`), with
     `auto_stop_machines` and `auto_start_machines` on: a worker request to
     `docflow-parse-staging.flycast` starts a stopped machine. The address
     is private because the app holds only a private IPv6 address
     (`fly ips allocate-v6 --private`). Fly's docs warn that any public IP
     on the app would expose the services section to the internet, so
     **tests N1-N2 check the IP list after every deploy.**
   - **Two calls:** `POST /v1/document` (bytes in; text parts, page or
     image parts, and the preview out) and `POST /v1/table` (bytes in;
     rows of text cells out, for catalog imports). Each answer is one of
     `ok`, `rejected` (a catalog code, exactly as today: DOC-001, DOC-005,
     DOC-010 to DOC-019 and IMP-004), or `stopped` (a limit was hit,
     item 3).
   - **The supervisor never opens a file.** It is a small HTTP process
     that caps the request size (`MAX_FILE_SIZE_BYTES`), starts one
     sandboxed job per request, reads the job's answer up to a cap, checks
     the answer's shape, and replies. Everything that reads the file's
     contents runs in the job.
   - **Slots:** 2 jobs at once per machine (`PARSE_SLOTS`). A request
     that finds no free slot gets a 503 with `Retry-After`, which the
     worker treats as "wait", never as a failure.
   - **Who can call it (Q2: yes):** a shared token, `PARSE_SERVICE_TOKEN`,
     held by the worker and the supervisor only, never passed into a job.
3. **Every job.** The supervisor runs as root (it must, to create
   namespaces and cgroups) and, for each request:
   1. **creates the job's cgroups** (Q8: approved; v1 as Fly delivers it,
      v2 where the machine offers it):
      - **memory:** the job's memory limit, **and the memory+swap limit
        set to the same value** (v1 `memory.memsw.limit_in_bytes`; v2
        `memory.swap.max` = 0). v1's `memory.limit_in_bytes` doesn't count
        swap, so without this a machine with swap would let a job go past
        its limit into swap (founder's change 2). The supervisor refuses to
        start a job if the swap limit can't be set, and the canary also
        confirms the machine has no active swap;
      - **pids:** the job's process cap;
      - **cpu (founder's change 3, both):** a **quota** (v1
        `cpu.cfs_quota_us` / `cpu.cfs_period_us`; v2 `cpu.max`) of one CPU
        per job, which *slows a job down* so it can't starve the other
        slot; and a **CPU-seconds budget** read from cpuacct (v1
        `cpuacct.usage`; v2 `cpu.stat` `usage_usec`), which *kills* the
        job when its processes together pass it.

      The job's first process is moved into them before it starts
      anything, so everything it starts is born inside the limits;
   2. starts the job in new network, mount, PID, IPC and UTS namespaces
      (`unshare --net --mount --pid --ipc --uts --fork`). The network
      namespace has only a loopback that is down. Killing the PID
      namespace's first process kills every process in it, LibreOffice's
      helpers included;
   3. inside the mount namespace (founder's items 3 and Q7):
      - **the root filesystem is read-only** (Q7: yes);
      - **writable, size-capped tmpfs only:** `/work` (the input file and
        temporary files) and a separate home for LibreOffice's profile
        (`HOME`). Their pages count against the job's memory limit too;
      - an empty tmpfs over `/.fly` and `/sys` (the D-150 spike's two
        findings), which also hides the job's own cgroup files;
      - a fresh `/proc` for the new PID namespace;
      - **its own minimal `/dev`** (founder's item 3): a new tmpfs holding
        only `null`, `zero`, `full`, `random` and `urandom`, plus a
        size-capped `/dev/shm`. No block device, nothing else;
   4. `setrlimit` **as backstops** (founder: the cgroup is the primary
      cap): address space per process, CPU seconds per process, processes
      per user, open files, file size, no core dumps;
   5. an empty environment (a fixed `PATH`, `HOME`, `TMPDIR=/work`,
      `LANG`);
   6. drops to an unprivileged user with `setpriv`: no capabilities, an
      empty bounding set, `no_new_privs`, no supplementary groups, **one
      user ID per slot** (10001, 10002);
   7. execs the job's Python entry point, which **first** (founder's item
      1) sets `PR_SET_NO_NEW_PRIVS` itself and loads the seccomp filter
      (below), then checks `/proc/self/status` shows `NoNewPrivs: 1` and
      `Seccomp: 2`, and only then opens the input. If either check fails,
      it exits without reading the file and the supervisor answers as for
      an isolation failure. The filter is inherited by everything the job
      starts, LibreOffice included.

   **The seccomp filter (founder's item 1 and change 1).** Default allow,
   with these refused (each answers EPERM):
   - the founder's list: `unshare`; `clone` with any new-namespace flag;
     `setns`; `mount` and `umount2`; `ptrace`; `bpf`; `keyctl`;
     `perf_event_open`; `init_module`, `finit_module`, `delete_module`;
     `kexec_load`, `kexec_file_load`;
   - **the calls that do the same jobs by another name**, without which
     the list could be stepped around (*proposed*, inside the founder's
     intent): the newer mount calls (`fsopen`, `fsconfig`, `fsmount`,
     `fspick`, `move_mount`, `open_tree`, `mount_setattr`); the rest of
     the key family (`add_key`, `request_key`); and `ptrace`'s relatives
     `process_vm_readv` and `process_vm_writev`;
   - **founder's change 1:** `io_uring_setup`, `io_uring_enter`,
     `io_uring_register` (io_uring performs I/O in kernel threads the
     filter never sees), and `userfaultfd` (a common tool for winning
     kernel race conditions);
   - **`clone3` answers ENOSYS**, not EPERM. It passes its flags in a
     memory block the filter can't read, so it is refused in a way that
     makes the C library fall back to `clone`, whose flags the filter
     can read (Fly check 2c: threads and fork still work);
   - calls from any other CPU architecture are killed (the filter is
     built for x86_64 only, with its "wrong architecture" action set
     explicitly to kill the process), so a 32-bit call can't slip past the
     rules; **and any x32 call, a call number with the `0x40000000` bit
     set, kills the process** (founder's change 1). x32 calls report the
     same architecture as x86_64, so the architecture check alone doesn't
     catch them; the filter checks the bit itself.

   **The supervisor's own limits:**
   - a wall-clock timer that SIGKILLs the job's first process, so the
     whole PID namespace dies;
   - a watch on the job's cgroups every 100 ms while it runs. **Any
     out-of-memory kill in the job kills the whole job** (v1 kills one
     process at a time; the Fly check left one behind). So does the job's
     **total** CPU passing its budget (Q8: approved; CPU seconds multiply
     with processes exactly as memory does);
   - a cap on the answer's size (it stops reading and kills the job past
     it);
   - after the job, the cgroups are removed and `/work` and the home are
     gone, so nothing outlives a file. Every job is a new process: **one
     subprocess per file, never reused.**

   *Proposed numbers, then measured (the real Tier 1 and Tier 2 POs must
   all parse under them in CI and on Fly, D1, before they are fixed):*

   | Limit | Proposed | Kind |
   |---|---|---|
   | `PARSE_JOB_WALL_SECONDS` | 180 | supervisor; above LibreOffice's own 120 s (kept), well inside the 20-minute read budget (D-163) |
   | Job memory (cgroup) | 768 MiB | **primary**; two slots fit a 2 GB machine with room for the supervisor |
   | Job CPU quota (cfs / `cpu.max`) | 1 CPU per job | **primary**: slows, never kills; one job can't starve the other slot |
   | Job CPU budget (cpuacct) | 150 s | **primary**: kills the whole job |
   | Job memory+swap | = job memory | **primary**: no swap past the limit |
   | Job processes (cgroup pids) | 128 | **primary**; LibreOffice runs a few dozen threads, and threads count |
   | Address space per process | 2 GiB | backstop, set high on purpose: LibreOffice reserves far more than it uses |
   | CPU seconds per process | 170 | backstop |
   | Processes per slot user | 256 | backstop |
   | Open files | 256 | |
   | Largest file the job may write | 64 MiB | |
   | `/work` tmpfs | 256 MiB | counts against job memory |
   | LibreOffice home tmpfs | 128 MiB | counts against job memory |
   | `/dev/shm` | 64 MiB | counts against job memory |
   | Answer cap | 48 MiB | a 25 MB scanned PDF returned base64 is about 34 MB |

   **CI and Fly take different cgroup paths, stated plainly.** GitHub's
   runners are cgroup v2 only, and Fly is v1. The supervisor detects which
   one it has at startup: v2 memory if it is available, otherwise v1. If
   Fly ever moves to v2, the same image follows it. So CI proves the
   supervisor's logic on v2, and **the v1 path production uses is proven
   only on Fly**. That is why the B tests are required on Fly before the
   merge, not only in CI.

   **Gate (founder's change 5): the Fly B tests must pass before any
   production deploy that touches the parse service** -- its code, its
   image, its base image or packages, its `fly.toml`, or the worker's
   `parse_client` -- not only at the 3c checkpoint. The procedure: deploy
   the change to Fly staging first, run B1-B12 there, keep the evidence,
   then deploy to production. RUNBOOK section 8 holds it, and the 3c PR
   description repeats it.

   **A test-only switch that breaks a cap** (E4, if CI needs one) is
   refused whenever `FLY_APP_NAME` is set: the service exits at startup.
   Test E5 checks that.
4. **Refuses to start in production mode unless isolation is active.**
   - Production mode is on when `FLY_APP_NAME` is set (Fly sets it on
     every machine) or `DOCFLOW_ENV=production`. `PARSE_ISOLATION=off`,
     the dev setting, is refused in production mode, so a stray setting
     can't switch it off on Fly.
   - **At startup, before the port opens,** the supervisor runs a canary
     job through the same launcher. It is a fixed program built into the
     image and takes no input. It checks that:
     - every network probe fails;
     - `/.fly`, `/sys` and the block devices are hidden;
     - the root filesystem is read-only;
     - it has the slot's user, no capabilities, `NoNewPrivs: 1` and
       `Seccomp: 2`;
     - a few calls from the seccomp list are refused;
     - **a memory limit really kills** (a small allocation past a small
       cgroup limit must be stopped);
     - **the machine has no active swap** (`/proc/swaps`), and the job's
       memory+swap limit is set (founder's change 2);
     - the CPU quota is set and the CPU budget kills.

     Any unexpected success, or a cgroup or namespace that can't be
     created, exits with an error. Fly restarts it, it fails again, and the
     health check never passes, so the worker gets "unavailable" (item 6)
     and the founder an alert.
   - `/health` answers OK only after the canary passed.
   - There is **no HTTP endpoint that runs a probe or a test program.**
     The canary and the self-test programs in item 11 are fixed programs in
     the image, started through the same launcher from a shell on the
     machine (`fly ssh console`, or `docker exec` in CI), never over the
     network.
5. **(Folded into item 2: the shared token, Q2.)**
6. **The worker's side: what each answer does.** A new `parse_client`
   module. Connect timeout 5 s; read timeout `PARSE_JOB_WALL_SECONDS` +
   30 s.

   | Answer | Document | Catalog import |
   |---|---|---|
   | `ok` | parts checked (below), then the read goes ahead as today | rows checked, then as today |
   | `rejected` with a code | `failed` with that code, as today | `failed` with that code, as today |
   | `stopped` (a limit) | `failed` with **DOC-029** (Q3), founder alert. Final: no retry | `failed` with IMP-004, as today for an unreadable file |
   | **Never got in:** connection refused, DNS failure, 503 no free slot, the service unhealthy | **waits, as a Storage outage does (3b):** stays `processing`, the try given back, the sweep retries; `parse_service_unavailable` alert, at most once an hour platform-wide (Q4) | IMP-009 ("start again"), same alert |
   | **Got in, never came out:** the connection dropped or the read timed out after the file was sent | **a timeout-class try** (item 6a); the worker applies the sweep's own decision at once | IMP-009 |
   | An answer that fails the worker's checks | `failed` DOC-005 and the DOC-029 founder alert (Q3) | IMP-004 and the same alert |

   - **The worker checks every answer** (a converter's output is still
     untrusted, Section 7.11): only the part types the model call takes;
     media types from a fixed list; base64 that decodes; text and total
     sizes within caps. The worker never decodes an image or PDF to check
     it, because that would be parsing again.
   - **All of this sits outside the broad `except` blocks**, as the 3b
     Storage read does, so no service failure can be relabelled DOC-005.
   - Previews stay best effort: a preview the service can't produce never
     touches the document's status.

   **6a. How "got in, never came out" combines with 3a's timeout rule
   (founder's item 5).** Today there are two rules: a crash gets retries
   up to `MAX_PROCESSING_ATTEMPTS` (3) tries in all, and a timeout gets at
   most one retry. **Found while answering this: today's `decide()` can
   already break the 3-try cap.** Its timeout branch is checked first and
   never looks at the cap. So a crash, a crash, then a timeout on try 3
   (`processing_attempts` 3, `timeout_attempts` [3]) returns "retry" and
   allows a **4th** try (`stuck_documents.py`, `decide()`). It's a 3a
   defect, not something 3c introduces, and the founder's rule names
   exactly this case.

   *Proposed* (Q9):
   - A "got in, never came out" try is recorded as a **timeout-class
     try**, alongside 3a's hard time limit, because in both cases the file
     is the suspect.
   - **One decision for both, in `decide()`, checked in this order:**

     | State | Outcome |
     |---|---|
     | 3 tries used | fail DOC-022: cause `timeout` if any try was timeout-class, else `worker_stopped` |
     | a timeout-class try happened, and a try has run since the first one | fail DOC-022, cause `timeout` |
     | otherwise | retry |

   - **The guarantee, whatever the mix:** at most 3 tries in all, and at
     most one try after the first timeout-class try.

     | Sequence | Today | Proposed |
     |---|---|---|
     | crash, crash, crash | 3 tries, `worker_stopped` | same |
     | timeout, timeout | 2 tries, `timeout` | same |
     | crash, timeout, anything | 3 tries, `timeout` | same |
     | **crash, crash, timeout** | **4th try** | **3 tries, `timeout`** |
     | lost, lost | -- | 2 tries, `timeout` (cause detail `parse_lost`) |
     | crash, lost, crash | -- | 3 tries, `timeout` |
     | never got in (any number of times) | -- | no try used; waits |
     | stopped by a limit | -- | 1 try, DOC-029, final |

   - The worker, which is still alive after a lost try, records it and
     applies `decide()` at once, so a lost file is retried or failed in
     seconds, not after the 30-minute sweep. The sweep calls the same
     function. **One decision, one place.**
   - *Recording (Q9):* proposed, a `documents.parse_lost_attempts
     integer[]` column in `0034` beside 3a's `timeout_attempts`. Both
     count as timeout-class in `decide()`, and the DOC-022 alert's payload
     can then say which happened (`cause_detail: parse_lost` or
     `task_time_limit`). The alternative is no column: a lost try goes
     into `timeout_attempts`, and the alert can't tell the two apart.
7. **Dev on Windows.** The same service runs locally with
   `PARSE_ISOLATION=off`. It still uses one subprocess per file, still has
   the wall-clock kill, and uses LibreOffice from the existing Windows
   install. It has no namespaces, cgroups, user switch, seccomp or
   `setrlimit`, none of which Windows has. The worker reaches it at
   `PARSE_SERVICE_URL` (`http://127.0.0.1:8100` in dev). The worker's test
   suite starts it as a fixture. **None of this counts as proof of
   isolation:** CI and Fly are the only places isolation runs.
8. **The API's gate (Q1: stays).** `validate_upload` in the API reads the
   zip's entry list (no extraction) and inflates at most 16 KB from the
   start of each XML part, 8 MB in total, to look for a DOCTYPE or ENTITY.
   It uses only Python's standard library. It decides whether a file is
   stored at all, and gives the uploader an immediate answer. The same
   check runs again inside the sandbox.
9. **D-163: a run row before the model call (Q5: option 1, decided).**
   - **A "started" row, then the outcome as a second row**, as agreed for
     `admin_actions` (Stage 5, D-178). Migration `0034`:
     - `extraction_runs.run_state` (`started` | `finished`; existing rows
       become `finished`);
     - `started_run_id` (the outcome row points at its start row);
     - `succeeded` may be NULL only on a `started` row (a check
       constraint).

     Nothing is ever updated.
   - Before each paid call (routing and extraction), the worker counts the
     input tokens (`count_tokens`, free, 15 s per try, D-163) and commits
     the started row with the model ID and that count. After the call, it
     writes the outcome row as today.
   - **A started row with no outcome** is found by the stuck sweep when it
     takes the document over. The sweep writes the outcome row itself:
     `succeeded false`, error `worker_lost_during_call`, the input cost
     from the counted tokens, and `cost_complete: false` (the output tokens
     are unknown, as for a dropped stream, D-163). So the cost breaker, the
     cost per document and the KPIs see a lower bound instead of nothing.
   - Every reader that sums cost or counts runs reads outcome rows only. A
     test holds that for each of them: the breaker, the rollup, the health
     strip and cost per document.
   - Cost: one extra free API call per paid call, about 0.2-0.5 s.
   - **Backup first** (founder): `extraction_runs` and `documents` (which
     also gains `parse_lost_attempts` if Q9 is yes). Backup SQL and the
     row counts come before the PR link. `0034` deletes nothing.
10. **What else changes.**
    - A new `DECISIONS.md` entry, D-183 (3c as built).
    - `RUNBOOK.md`:
      - running and deploying the parse service;
      - the parser-upgrade process (CLAUDE.md 7.11 requires it, and it now
        means rebuilding one image: base digest, LibreOffice and
        `python3-seccomp` versions, the lock file);
      - reading the canary's startup log;
      - what to do when `parse_service_unavailable` fires;
      - stopping the staging machines at the end of a test session.
    - `CLAUDE.md`'s parsing-worker bullet says the per-file limits are
      "still to be built and tested in Stage 3". When 3c passes, I'll
      **propose** new wording to the founder; I won't edit it.
    - `SETUP.md`: starting the parse service in dev.
    - Catalog: DOC-029 (Q3, wording approved); founder-facing wording for
      the `parse_service_unavailable` alert and the DOC-029 alert.
11. **Tests, and where each runs.** See "3c test table" directly below this
    section. It has 55 tests. The first version had 33; the founder's
    additions (seccomp, `/dev`, Flycast, the retry rule, the token, the
    alert limits, the test-only switch, the CPU quota) and the cgroup
    findings added the rest.
12. **Fly staging: what is stood up, and the price.** Q6 decided: **stopped
    between test sessions**, Upstash pay-as-you-go. The founder sets the
    secrets.
    - Rates read from Fly's pricing page on 2026-09-30. The page computes
      them from shared vCPU $0.00000075/s and RAM $0.00000193/GB-s, region
      `iad` = 1.0x.

    | App | Size | If left running, per month |
    |---|---|---|
    | `docflow-parse-staging` | shared-cpu-2x, 2 GB (two 768 MiB slots) | $11.39 |
    | `docflow-worker-staging` | shared-cpu-1x, 1 GB (no parsing any more) | $5.70 |
    | `docflow-api-staging` | shared-cpu-1x, 512 MB, **private only, no public IP** | $3.19 |
    | Upstash Redis | pay-as-you-go, $0.20 per 100k commands | per use |

    - Stopped, each machine costs only its disk: $0.15 per GB per 30 days,
      about $0.50 a month for all three. Running, all three cost about
      $0.03 an hour plus Upstash's commands. **A month with 40 hours of
      testing: roughly $2-5.**
    - **Celery's Redis command count is measured in the first session and
      reported** (founder): commands before and after a timed idle hour
      and a timed busy one, read from Upstash. **If the idle hour x 720
      costs more than $10 a month, switch to the fixed $10 plan** (Q10).
    - **The spending cap: not available as far as I can find (Q10).**
      `fly redis create` and `fly redis update` have no budget or cap flag
      (flyctl v0.4.108, read 2026-09-30), and Fly's Upstash page mentions
      none: only 10,000 commands/s and 10 GB storage limits. Usage is
      billed hourly on the Fly bill. The Upstash console (`fly redis
      dashboard`) may have a budget setting; I can only see that once the
      database exists. Options are in Q10.
    - The worker on Fly runs **no Celery beat**: no scheduled sweeps or
      lifecycle jobs from Fly against staging. It only takes documents and
      imports enqueued through the Fly API. The local worker and API keep
      using the local Redis, so the two never share a queue.
    - Not included: no dedicated IPv4 (nothing public), no volumes, and
      egress at $0.02/GB (megabytes here). Builds run on Fly's remote
      builder; whether it is charged will show on the first bill (today's two
      check deploys ran under a cent of machine time).
    - **Secrets on Fly, set by the founder** (exact commands below). Each
      app gets only what its process reads. Non-secret settings
      (`DOCFLOW_ENV`, `PARSE_SERVICE_URL`, `PARSE_ISOLATION`) go in each
      app's `fly.toml`. If the build finds a process needs a setting not
      listed here, it comes back to the founder before it is set.
    - **Not in 3c:** pointing Postmark's or Stripe's webhooks at Fly, the
      web app, a public API, and production. Those are Phase 6.
13. **Order of work.**
    1. The D2 baseline: today's parser output over every fixture, saved.
    2. The `decide()` fix and its tests (Q9), on its own commit.
    3. `apps/parse/`: service, launcher, job, filter, self-test programs,
       Dockerfile. Dev mode on this machine. The worker switched to
       `parse_client`, and the parsing libraries removed from the worker
       and the API.
    4. The CI job (the A, S, B, C, D and E rows with the real image); CI
       green.
    5. D-163 and `0034`: backup SQL and row counts first, then the founder
       applies it on staging; staging suites.
    6. Fly: I create the three apps (no secrets) and the Upstash database;
       **the founder sets the secrets**; deploy; check the canary log; run
       the N, A, S, B, C, D and G rows on Fly. Evidence goes under
       `docs/spikes/3c-fly-staging/`.
    7. The machines stopped, the command count reported, the checkpoint,
       the PR.
14. **Not proposed** (so the founder knows they were considered): an
    allowlist-style seccomp filter (default deny). It is stricter, but
    LibreOffice's syscall set would have to be mapped and kept up to date
    on every upgrade. The deny list above blocks the calls that matter for
    escaping and hiding.

**Questions for the founder (3c).**

*Answered 2026-09-30:*
- **Q1** yes, the API's gate stays.
- **Q2** yes, `PARSE_SERVICE_TOKEN`.
- **Q3** yes: DOC-029 as worded, plus the founder alert.
- **Q4** yes: `parse_service_unavailable`, and the wait/count rule.
- **Q5** option 1 (a started row plus an outcome row, append-only), with
  the backup first.
- **Q6** stopped between test sessions; Upstash pay-as-you-go with a cap;
  Celery's command count measured in the first session; the founder sets
  the Fly secrets.
- **Q7** yes, a read-only root, with a writable size-capped tmpfs for
  LibreOffice's home and profile.

*Answered 2026-10-01 (raised by the pre-build check):*
- **Q8** approved: cgroup v1 memory and pids per job as the primary caps
  (v2 where offered), the whole job killed on any out-of-memory kill, the
  canary proving the cap kills before the service opens, `setrlimit` as
  backstops, and a job-wide CPU budget. Change 3 adds a CPU quota beside
  the budget.
- **Q9** approved: `decide()` checks the 3-try cap first, and
  `documents.parse_lost_attempts` goes in `0034`.
- **Q10** (a): pay-as-you-go. A spending cap is set in the Upstash console
  if one exists. **Rule (founder): after G3, if the idle-hour command
  count x 720 costs more than $10 a month at $0.20 per 100k commands
  (that is, more than about 69,400 commands in the idle hour), switch to
  the fixed $10 plan.**

**Fly secrets, the founder's commands** (PowerShell; placeholders in
`<...>`):
- `--stage` stores each secret without restarting machines; the next
  deploy applies them.
- PowerShell keeps typed commands in its history file
  (`(Get-PSReadLineOption).HistorySavePath`). Either remove these lines
  from it afterwards, or put the `NAME=value` lines in a file and pipe it
  with `Get-Content <file> | fly secrets import --app <app> --stage`,
  then delete the file.

```powershell
$fly = "$env:USERPROFILE\.fly\bin\fly.exe"

# The token: any long random string, the same value on both apps.
& $fly secrets set --app docflow-parse-staging --stage `
  PARSE_SERVICE_TOKEN=<random-token>

& $fly secrets set --app docflow-worker-staging --stage `
  PARSE_SERVICE_TOKEN=<random-token> `
  DATABASE_URL=<staging docflow_app connection string, as in the root .env> `
  REDIS_URL=<the private redis:// URL printed by `fly redis create`> `
  ANTHROPIC_API_KEY=<the staging Anthropic key> `
  STORAGE_S3_ENDPOINT=<as in the root .env> `
  STORAGE_S3_REGION=<as in the root .env> `
  STORAGE_S3_ACCESS_KEY_ID=<as in the root .env> `
  STORAGE_S3_SECRET_ACCESS_KEY=<as in the root .env>

& $fly secrets set --app docflow-api-staging --stage `
  DATABASE_URL=<staging docflow_app connection string, as in the root .env> `
  REDIS_URL=<the same Upstash URL> `
  SUPABASE_URL=<as in the root .env> `
  STORAGE_S3_ENDPOINT=<as in the root .env> `
  STORAGE_S3_REGION=<as in the root .env> `
  STORAGE_S3_ACCESS_KEY_ID=<as in the root .env> `
  STORAGE_S3_SECRET_ACCESS_KEY=<as in the root .env>

# Check: names only, never values.
& $fly secrets list --app docflow-parse-staging
& $fly secrets list --app docflow-worker-staging
& $fly secrets list --app docflow-api-staging
```

The parse app must list **only** `PARSE_SERVICE_TOKEN` (test A12 checks
this as well). The API needs `SUPABASE_URL` to check sign-in tokens (JWKS)
for the upload test. It gets no Stripe, Postmark, service-role or
Anthropic key in 3c.

**3c test table.** 61 tests (55 agreed, plus B13-B16, A15 and S4's second check, 2026-10-01).

Where each test runs:
- **L** = this Windows machine, dev mode. Logic only, never counted as
  proof of isolation.
- **CI** = a new CI job. It builds `apps/parse/Dockerfile` (the production
  image) and runs it with `--privileged`: on Fly the container is root in
  its own VM, and this is the closest a GitHub runner gets. It tests the
  running image from the runner over HTTP, and with `docker exec` for the
  self-test programs. CI's cgroups are v2.
- **Fly** = the same image on Fly staging, cgroups v1 (the production
  path).

Every isolation and limit test runs its control first: the same probe
**outside** the sandbox, or the same program without the limit. Each test
reports its own evidence. RUNBOOK 1.7 applies to every run: an isolation
failure means stop and report.

| # | Test | Control | L | CI | Fly |
|---|---|---|---|---|---|
| **N. Network placement** | | | | | |
| N1 | The parse app has exactly one IP address, a private IPv6 (`fly ips list`), checked after every deploy; the API app has no public IP either | -- | | | yes |
| N2 | From outside Fly's network (this machine, WireGuard off), the parse and API apps have no public address that answers | the same request over `fly proxy` answers | | | yes |
| N3 | A stopped parse machine is started by a worker request to `docflow-parse-staging.flycast` | the machine shows `stopped` before | | | yes |
| N4 | **The token (founder's change 4):** a request with no token and one with a wrong token are both refused (401) before the body is read; nothing starts a job | the right token parses | | yes | yes |
| **A. Isolation, per job** | | | | | |
| A1 | Internet unreachable: IPv4 and IPv6 TCP, HTTPS by name | reached outside | | yes | yes |
| A2 | DNS: the system resolver; Fly's resolver `[fdaa::3]:53` | answered outside | | yes (system) | yes (both) |
| A3 | **The real Upstash** (its private IPv6 address, resolved outside first) | reached outside | | | yes |
| A4 | **The real staging API and the real worker** (by private address) | reached outside | | | yes |
| A5 | The Fly Machines API, `_api.internal:4280` | reached outside | | | yes |
| A6 | The hosts that hold our data: the Supabase database and Storage hosts, `api.anthropic.com` | reached outside | | yes | yes |
| A7 | The supervisor's own port, from inside the job | reached outside | | yes | yes |
| A8 | `/.fly` empty (the socket isn't there, not just refused); `/sys` empty; the only interface is `lo`, down | present outside | | yes (`/sys`) | yes (both) |
| A9 | Identity: the slot's user, `CapEff` 0, empty bounding set, `NoNewPrivs` 1, no supplementary groups | root outside | | yes | yes |
| A10 | No way out: `nsenter` into the machine's namespaces, bringing up an interface, `mount` -- all refused | work as root outside | | yes | yes |
| A11 | The job sees only its own processes; two jobs running at once can't see each other's files or processes, **including `/tmp`: a marker the first job leaves in its `/tmp` is not in the second's (Q14)** | -- | | yes | yes |
| A12 | The job's environment holds none of DocFlow's setting names (database, Storage, Anthropic, Supabase, Stripe, Postmark, the parse token); on Fly, the parse app's secret list is the token alone | the supervisor's own environment has the token | | yes | yes |
| A13 | **`/dev` (founder's item 3):** no block device at all, and only `null`, `zero`, `full`, `random`, `urandom` and `shm` | block devices listed outside (CI: the runner's; Fly: `vda`-`vdc`, `loop`, `nbd`) | | yes | yes |
| A14 | **Read-only root (Q7):** writing to `/`, `/usr`, `/app`, `/etc` or `/opt` fails (EROFS); `/work` and LibreOffice's home are writable up to their caps; **`/tmp` and `/var/tmp` are writable only as the job's `/work` tmpfs (same filesystem; Q14, 2026-10-01)** | the same writes as root outside succeed (into a throwaway path) | | yes | yes |
| **S. seccomp (founder's item 1)** | | | | | |
| S1 | **The shipped rule set, with a test-only errno in place of EPERM, inside the real sandbox:** every refused call returns that errno, so the filter (not a missing privilege) answered: `unshare`, `clone` with each new-namespace flag, `setns`, `mount`, `umount2`, the newer mount calls, `ptrace`, `process_vm_readv/writev`, `bpf`, `keyctl`, `add_key`, `request_key`, `perf_event_open`, `init_module`, `finit_module`, `delete_module`, `kexec_load`, `kexec_file_load`, **`io_uring_setup`, `io_uring_enter`, `io_uring_register`, `userfaultfd`** | -- | | yes | yes |
| S2 | **The filter as shipped (EPERM), inside the real sandbox:** every call in S1 fails, io_uring and `userfaultfd` included; `/proc/self/status` shows `Seccomp: 2` and `NoNewPrivs: 1` | -- | | yes | yes |
| S3 | **What the filter alone stops:** the same calls in the sandbox **without** the filter, reported call by call (on Fly today: `unshare(CLONE_NEWUSER)`, `clone` after it, and `keyctl` succeed) | is the control | | yes | yes |
| S4 | Two checks. `S4:normal-work-under-the-filter`: `clone3` answers ENOSYS; a thread and a fork still work under the filter. **`S4:real-doc-under-the-filter-alone`** (corrected 2026-10-01: the row used to say S4 converts a `.doc`, "with D1"; it didn't): the real document code converts the committed `po.doc` under the seccomp filter and no-new-privileges alone, as the slot user, with no namespaces, no read-only root, no cgroup and no rlimits, and finds its PO number. With B13 (the full sandbox), a `.doc` failure points at the filter (S4 fails) or at the filesystem and limits (only B13 fails) | -- | | yes | yes |
| S5 | The filter allows only x86_64 (its exported form is checked), so calls from another architecture are killed; **an x32 call (`getpid` with the `0x40000000` bit set) kills the process with SIGSYS** | the same x32 call without the filter, reported (ENOSYS if the kernel has no x32) | | yes | yes |
| **B. Limits** (fixed self-test programs in the image, through the real launcher) | | | | | |
| B1 | Memory, one process: a program allocating past the job's cgroup limit is stopped as `stopped: memory`; the service answers the next request. **The job's memory+swap limit equals its memory limit, and the machine has no active swap** | the same program with no cgroup limit survives | | yes (v2) | yes (v1) |
| B2 | **Memory, the founder's point:** four processes, each under its own per-process backstop, together over the job's limit, are stopped; the swap limit is checked as in B1 | the same four with only `setrlimit` all survive | | yes (v2) | yes (v1) |
| B3 | **One out-of-memory kill ends the whole job:** no process of that job is left afterwards (v1 kills one at a time); the swap limit is checked as in B1 | -- | | yes (v2) | yes (v1) |
| B4 | **CPU budget (kills):** a family of spinning processes is killed when their CPU seconds together pass the job's budget, before the wall clock, as `stopped: cpu` | one process alone stays under it | | yes | yes |
| B5 | Wall clock: a program that ignores SIGTERM and starts children is killed at the limit, **and no process of that job user is left** | -- | | yes | yes |
| B6 | Fork bomb: stopped at the job's `pids` cap; the machine stays healthy | -- | | yes | yes |
| B7 | Answer flood: the supervisor stops reading at the cap and kills the job | -- | | yes | yes |
| B8 | Disk: `/work`, LibreOffice's home and `/dev/shm` each stop at their size, and their pages count against the job's memory; **`/tmp` counts against `/work`'s cap (with 128 MiB kept in `/work`, `/tmp` stops at what is left; Q14)** | -- | | yes | yes |
| B9 | One process per file: two jobs in a row have different processes and namespaces, and nothing of the first is left in `/work` or the home | -- | yes (process only) | yes | yes |
| B10 | The job can't raise its own limit or leave its cgroup (the files are hidden; and refused even if visible) | root can change it | | yes | yes |
| B11 | **No leak, through the real request path** (and nothing found by the after-job backstop) (founder, 2026-10-01): the service's own Service and Handler on a loopback port, 100 real POSTs (a text order, a catalog table, the committed `po.doc`), two at a time; all answer 200, none stopped or crashed; afterwards every job cgroup is gone and the slot users own no process. **While they run, every job's namespace PID 1 is sampled from outside and must be the reaper (`sandbox_init`), never the parser or LibreOffice,** and a running job must be seen as its PID 2 | -- | | yes | yes |
| B12 | **CPU quota (slows):** a job spinning on every core gets at most one CPU's worth of time over a timed window, and the cgroup reports throttling; the other slot's job still finishes | the same program with no quota uses more than one CPU | | yes | yes |
| B13 | **LibreOffice runs in the sandbox** (added 2026-10-01 after `po.doc` gave DOC-017 in the real image): the committed `po.doc`, converted inside a real job with `conversion.py`'s flags and Word 97 filter, exits 0 and produces a file; reports its exit status and stderr tail, whether `/tmp` and `/var/tmp` are writable, and the same run with LibreOffice's pipe pointed at the work directory (evidence) | -- | | yes | yes |
| B14 | **A parser's own exit code is never read as a kill** (founder, 2026-10-01): a job that exits 137 by itself, in the real sandbox, is `crashed: exit_137`, with no OOM kill and the reaper's record `{"exited": 137}`; unit tests cover every combination (`apps/parse/tests/test_classify.py`) | -- | | yes | yes |
| B15 | **Exit 70 after the hardened message is a parser failure** (founder, Q13): `crashed: exit_70`, with the confirmation `{"hardened": true, "seccomp": true}` in the evidence | -- | | yes | yes |
| B16 | **A self-reported memory error vs a real overrun** (Q13): a parser's own MemoryError (exit 71) is `crashed: self_reported_memory_error` with no OOM kill; 1200 MiB under the default 768 MiB cgroup is `stopped: memory` with an OOM kill | -- | | yes | yes |
| A15 | **noexec** (founder, 2026-10-01): a binary copied into `/work`, `/tmp`, `/var/tmp` and `/lohome` can't run, directly (EACCES) or through the dynamic loader | the same copy outside the sandbox runs | | yes | yes |
| **C. Hostile files** (through `POST /v1/document`, the real path. After each one, a known-good PO parses correctly, so the service is shown healthy) | | | | | |
| C1 | Zip bomb; XXE payload; oversized image; 500-page PDF; `.exe` renamed `.pdf`; password-protected PDF; a `.zip` holding a valid PO. Each gets the catalog code it gets today | -- | yes | yes | yes |
| C2 | A malformed file of **each** Tier 2 format (`.doc`, `.xls`, `.tif`, `.heic`, `.msg`, `.odt`, `.ods`) that kills or hangs its converter: a clean `rejected` or `stopped`, never a crash | -- | yes | yes | yes |
| **D. Real orders still parse** | | | | | |
| D1 | A real PO in every Tier 1 and Tier 2 format parses **under the limits**, which is what fixes the numbers in item 3 | -- | yes | yes | yes |
| D2 | **Same answer as today:** before anything moves, today's code runs over the whole fixture set and its parts are saved; the service must return the same parts, byte for byte | -- | yes | yes | |
| D3 | Catalog files (`.xlsx`, `.xls`, `.csv`) through `POST /v1/table` give the same rows as today | -- | yes | yes | yes |
| **E. Refusal to start** | | | | | |
| E1 | Production mode with namespaces unavailable: CI runs the same image **without** `--privileged`, so creating them really fails; the process exits and never opens its port | the privileged run starts | | yes | |
| E2 | `PARSE_ISOLATION=off` with `FLY_APP_NAME` set: refused | without `FLY_APP_NAME`, dev mode starts | yes | yes | |
| E3 | The startup log shows every canary check, line by line, ending in PASS | -- | | yes | yes |
| E4 | No working memory cap: CI runs the image with the cgroup filesystem mounted read-only, so no job cgroup can be created; the canary fails and the service never opens its port. *If Docker won't allow that mount, I come back to the founder before using any other way* | the normal run starts | | yes | |
| E5 | **Any test-only switch that weakens a cap is refused when `FLY_APP_NAME` is set:** the service exits at startup | without `FLY_APP_NAME` the switch is accepted (CI only) | yes | yes | |
| **F. The worker's side** | | | | | |
| F1 | Each row of item 6's table, against the running service: unreachable, busy, `stopped`, dropped mid-request, a malformed answer | -- | yes (dev service) | yes (real image) | |
| F2 | The dependency-graph test (as built, a static scan): no import of a parsing library (PDF, image, Word, legacy Excel, LibreOffice's bindings) in the worker's, core's or the API's product code, none in their requirements; **the XML guard** (founder, 2026-10-01): no direct lxml import and no `openpyxl.load_workbook` use in `apps/api/app` or `apps/worker/app` | -- | yes | yes | |
| F3 | D-163: a worker killed during a (stubbed) model call leaves a started row; the sweep writes its outcome with the counted input cost; every cost reader ignores started rows | -- | yes (DB, staging) | yes (DB) | |
| F4 | **The retry rule (6a):** `decide()` over every sequence in 6a's table, including crash, crash, timeout (3 tries, not 4) and lost, lost | today's code fails the crash-crash-timeout case | yes | yes | |
| F5 | A lost try against the real service (CI kills the parse container mid-request): recorded at once, and retried or failed by `decide()` within seconds; the next try after a restart succeeds | -- | | yes | |
| F6 | **DOC-029's founder alert fires at most once per tenant per UTC day** (founder's change 4): many stopped files in one tenant, one alert; another tenant gets its own | -- | yes (DB, staging) | yes (DB) | |
| F7 | **`parse_service_unavailable` fires at most once per UTC hour across all tenants:** failures from several tenants in one hour, one alert; the next hour, a new one | -- | yes (DB, staging) | yes (DB) | |
| **G. End to end on Fly staging** | | | | | |
| G1 | An upload through the staging API (over `fly proxy`), through the real Upstash, the worker on Fly and the parse service, to `needs_review`: the golden fixture, a `.doc`, and a scanned image. Paid: a few cents | -- | | | yes |
| G2 | A catalog import through the same path | -- | | | yes |
| G3 | **Celery's Redis command count** (founder): an idle hour and a busy one, reported | -- | | | yes |

The live golden run (`pytest -m live_api`, 3 tests) also runs at the 3c
checkpoint: the content sent to the model is built on a new path, even if
D2 shows it is unchanged.

**3c build -- CI GREEN 2026-10-01 on `0be5422` (branch `phase55/stage3c-design`); `0034` not yet on staging; Fly staging run owed before the merge (RUNBOOK 8.1).**
Built and tested on this machine (dev mode; never counted as proof of
isolation). Nothing is on Fly.

**First CI run (`09c68ed`, `899efdb`, 2026-10-01): failed, three causes,
none of them a sandbox finding; all three fixed in the next commit.**
1. **The image didn't build** (parse and worker jobs; `apt-get` exit 100).
   I pinned LibreOffice at `25.2.3-2+deb13u6` from the main archive without
   checking the security archive, which already had `deb13u7`; apt takes the
   security version for the writer's exact-version dependencies, so u6
   can't be installed. Pin moved to `deb13u7` (RUNBOOK 8.5's case, before
   the first build). The other pinned packages have no newer security
   version. So the self-tests, E1, E4 and the canary haven't run yet.
2. **Core: 5 xlsx export failures** (pinned digest, OS-independence, the
   hostile round trip). I took `lxml` out of the worker's and the API's
   locks with the parsing libraries. openpyxl writes the .xlsx through lxml
   when it's installed and through its own writer otherwise, and the bytes
   differ. My local venv still had lxml, so it passed here. Reproduced
   locally with `OPENPYXL_LXML=False` (5 failed, 36 passed). `lxml==6.1.3`
   restored exactly as on `main` (both locks, both requirements files,
   core's `files` extra), with a comment saying why; it is never handed a
   file. `pip-audit`: no known vulnerabilities.
3. **API: 33 failures** (catalog import, and onboarding and card billing,
   which need a committed catalog). The catalog tests call the worker's
   parse step (`catalog_import.run_parse`), which now reads through the
   parse service, and the API job started none. Locally my dev service was
   running. The API job now builds and starts the real image, as the
   worker job does.

**Second CI run (`5a00275`): the image built; the sandbox ran for the first
time.** core 699 tests, 0 failed; api 571, 0 failed; web and web-live
passed; parse unit 110, 0 failed. In the real sandbox (cgroup v2 on the
runner; Fly is v1, so the v1 path is proven only on Fly): canary 5 of 5;
S 4 of 4; A all PASS except A-net IPv6, NO-CONTROL (the runner has no IPv6
outside either, so the control can't show the block matters; Fly has IPv6);
E1 and E4 refused to start as they should. Failures:
1. **B5 and B11:** processes owned by the slot user still exist after the
   job, while the job's cgroup was empty and removed (B5: 6, B11: 8; the 100
   normal jobs in B11 added none). *Not yet diagnosed.* My guess, unproven:
   zombies of killed jobs. When the cgroup is killed, `unshare` dies with
   its child, the child is reparented to the container's PID 1 (the
   self-test program, with no init to reap it), and its `/proc` entry stays.
   The next run reports each one's state, parent and PID 1 before anything
   is changed.
2. **`po.doc` gives DOC-017 in the real image** (parse HTTP test, the
   worker's preview test, and F5, whose kill window needs a slow LibreOffice
   parse but got a 0.2 s refusal). LibreOffice fails inside the sandbox; the
   answer says only DOC-017. New self-test B13 reports LibreOffice's own
   exit status and stderr from inside a real job.
3. **18 worker database tests: documents stay in `processing`** (H1, H3, M1,
   M3, F3, cost, the time-limit tests). These need migration `0034`, so on
   this machine they skip (staging doesn't have it); CI is their first run.
   **Cause: a cascade from item 2, not 18 defects.** F5 kills the parse
   container and restarted it only after its assertions; `po.doc` came back
   in 0.2 s, an assertion failed, and the container stayed down. Every one
   of the 18 runs after F5 (they sort after it), found no service, and their
   documents were held in `processing` as designed for an unreachable
   service (`release_after_storage_outage`). F5 now restarts the container
   in a `finally`. Their real result is the next run's.

**Third and fourth runs (`bbc0932`, `fb8e538`).** With F5 restarting the
container, worker 131 tests, 2 failed, both `po.doc` (the preview test and
F5's kill window); the 18 database tests pass. The evidence:
- **B5/B11: zombies.** Every survivor (6 in B5, 8 in B11) is
  `State: Z (zombie)`, named `python` (a sandbox's PID 1), parent PID 1,
  and PID 1 is `python -m parse_service.selftest all`. Cause: killing a
  job's cgroup kills `unshare` and its child together; the child is
  reparented to PID 1, which never waits for it. **Fix (founder: reaping in
  the supervisor):** the service and the self-test make themselves the
  child subreaper (`prctl(PR_SET_CHILD_SUBREAPER)`), so on Fly, where they
  aren't PID 1, the orphans still come to them; after a killed job the
  launcher reaps what was in the job's cgroup, recording each one's state,
  cgroup and PID namespace in `evidence["reaped"]` just before. Only our own
  children can be reaped, and a live job's own child never is (a lock covers
  starting a job and reaping). B5 and B11 are unchanged: still any process
  owned by a slot user after the job fails them. The founder's cgroup /
  namespace question is answered by the next run's `reaped` evidence: a
  process outside the job's cgroup or PID namespace there is a B10 failure,
  and I stop and report it.
- **B13: `ERROR: no valid pipe path found.`**, exit 1 in 0.0 s. That is
  LibreOffice failing to find a writable directory for its IPC pipe; it
  tries `/tmp`, then `/var/tmp`, and the sandbox's root is read-only (the
  canary shows `/tmp: EROFS`). **Correction to the founder's reading:** S4
  as built doesn't convert a `.doc`; its row says "(with D1)", and D1 is
  the failing parity test. S4 checks `clone3`, a thread and a fork. So S4
  doesn't rule out the filter; B13's error is what points at the pipe
  directory. B13's variants (above) test that next run. **No fix is made:**
  whichever it is (a pipe path inside `/work`, or a writable `/tmp`) comes
  to the founder first.

**Fifth run (`749a5cd`).** core 700 tests, 0 failed; api 580, 0 failed (the
token tests run, none skipped); worker 132, 2 failed (both `po.doc`); web
and web-live passed; parse unit 110, 0 failed; parse HTTP 56, 1 failed
(`po.doc`). Self-tests: canary 5 of 5, S 4 of 4, A all PASS with A-net IPv6
reported **NOT-RUN**, B 12 of 15 (B5, B11, B13 failed). Earlier runs for the
record: `bbc0932` core 699/0, api 575/0, worker 131/20 (the F5 cascade);
`fb8e538` core 699/0, api 575/0, worker 131/2.
- **B5/B11: the founder's question answered, and a cleanup bug, not an
  escape.** The reaper took the killed jobs' sandbox PID 1s (B1 reaped pid
  101, B5's own job 165). Three zombies remained, each in **its job's own
  cgroup** (`/docflow-jobs/job-0-... (deleted)`) and **its job's own PID
  namespace** (`pid:[4026532468]` and others, never the supervisor's
  `pid:[4026532403]`); B10 passed. They came from OOM-killed jobs: a
  process the kernel has killed leaves `cgroup.procs` at once, so the
  snapshot I reaped from never listed it. **Fixed:** after every job the
  launcher reaps every orphan that is now its own child (any child that is
  not a live job's Popen child), and after any limit stop it keeps looking
  for 5 s. The checks are unchanged.
- **B13: LibreOffice ignores the pipe redirect.** `/tmp` and `/var/tmp` are
  EROFS, `TMPDIR` is `/work/tmp`; as `conversion.py` runs it, with
  `OSL_SOCKET_PATH` in the environment, and with `-env:OSL_SOCKET_PATH`, all
  three fail in 0.0 s with `ERROR: no valid pipe path found.` My reading:
  the message comes from LibreOffice's launcher (`oosplash`, which `soffice`
  runs first), which checks only `/tmp` and `/var/tmp` and exits before the
  office itself (`soffice.bin`), which honours `OSL_SOCKET_PATH`, ever
  starts. The next run adds the evidence for that: `soffice.bin` run
  directly with `OSL_SOCKET_PATH` in `/work` (retrying once on exit 81, the
  restart oosplash would do on a fresh profile). **Q14, the founder's
  decision; nothing is changed until then:**
  1. *Call `soffice.bin` directly, pipe in `/work`.* No filesystem change.
     Costs: we bypass LibreOffice's own launcher, so we take on its one job
     that matters here (restart on exit 81), and depend on an internal path
     (`/usr/lib/libreoffice/program/soffice.bin`).
  2. *Bind-mount `/work/tmp` over `/tmp` inside the sandbox.* LibreOffice
     runs as shipped. Not a second writable area: `/tmp` would be the same
     tmpfs as `/work`, under `/work`'s existing cap (B8 would add a line
     showing writes to `/tmp` count against it). It is a change to the
     sandbox's filesystem view.
  **Founder's decision (2026-10-01): option 2, decided without waiting**:
  option 1 ties DocFlow to LibreOffice internals (the binary's path, the
  exit-81 restart) that the weekly Debian snapshot can change; option 2
  runs LibreOffice as shipped, and each job's `/tmp` is its own, under
  `/work`'s cap.

**Sixth run (`974ad9e`).** core 700, 0 failed; api 580, 0 failed; worker
132, 2 failed (`po.doc`); web and web-live passed; parse unit 116, 0
failed; parse HTTP 56, 1 failed (`po.doc`). Self-tests: canary 5 of 5;
**S 5 of 5, including `S4:real-doc-under-the-filter-alone`** (the real
`po.doc` converts under the seccomp filter alone in 0.9 s, PO number found:
the filter is not the cause, the filesystem is); A all PASS, IPv6 NOT-RUN;
B: B5 PASS (nothing left after the wall-clock kill), B14 PASS
(`crashed: exit_137`, no OOM kill, record `{"exited": 137}`); B13 and B11
failed:
- **B13:** the three redirects fail as before; `soffice.bin` run directly
  with `OSL_SOCKET_PATH` asked for its restart (exit 81) and then converted
  the file (`input.docx`). That confirms the diagnosis; option 2 was chosen
  regardless.
- **B11: a sampling artifact, not a leak.** 100 requests, all 200 (txt and
  csv `ok`; `.doc` rejected, the known cause); no job cgroup, no slot-user
  process left. Every live namespace PID 1 sampled was `sandbox_init`; the
  parser was PID 2. But 6 PID 1 samples had an empty command line: a
  process caught at the instant it exits (its memory released). Fixed: the
  sampler records each one's state and names those `<exiting: State>`,
  reported, not judged.

**Built after the sixth run (founder, 2026-10-01):**
- **Q14 option 2:** `sandbox_init` bind-mounts the job's `/work/tmp` over
  `/tmp` and `/var/tmp` (`config.TMP_DIRS`), `nosuid,nodev` like `/work`,
  after the rest of the root is read-only. Tests: A14 (`/tmp` and
  `/var/tmp` writable and the same filesystem as `/work`; `/`, `/usr`,
  `/etc`, `/opt` still EROFS; the canary checks the same); B8 (`/tmp` runs
  out at `/work`'s cap, and with 128 MiB kept in `/work` at what is left);
  A11 (a concurrent job doesn't see the first job's `/tmp` marker); D1 and
  the worker's `po.doc` tests should now pass. B13 keeps only the run as
  `conversion.py` does it.
- **The reaper is event-driven (no timing window):** SIGCHLD wakes a reaper
  thread (through Python's wakeup fd, written at C level whichever thread
  takes the signal, so it never waits on a blocked main thread), which
  waits by pid for every zombie child in a job cgroup that is not a live
  job's own process. **Not `waitpid(-1)` literally:** that would also take
  the exit status of the supervisor's legitimate children (the jobs' Popen
  processes, the self-test's other subprocesses), and Python then reports a
  crashed child as exit 0. The 5-second window is gone. **The backstop:**
  after every job that ended normally (which leaves no orphans), that job's
  orphans, if any, are reaped, counted (`BACKSTOP_FOUND`) and logged; B11
  fails if any appear during its 100 requests. B5 waits (at most 30 s,
  reported) until the killed job's processes are gone and shows the latest
  SIGCHLD reaps.

Done, in the agreed order:
1. **D2 baseline** (`b819e8b`): 41 committed fixtures under
   `apps/parse/tests/fixtures/` (22 positive, 4 catalog tables, 15 hostile)
   and `baseline.json`, recorded by the worker's own code before anything
   moved.
2. **`decide()`** (`c4c8a88`, Q9): the 3-try cap first. One correction to
   the design's wording: the 4-try case was not an accident. 3a's own test
   asserted it (`(3, [3]) -> retry  # the timeout's one retry beats the
   attempt count`). The founder's Q9 reverses it.
3. **The parse service** (`apps/parse/`): supervisor, launcher, the
   `sandbox_init` root step, the job, the seccomp filter, cgroups v1/v2,
   the canary and self-test programs, the Dockerfile, and the moved parsing
   code (`git mv`, so history follows). The worker uses `parse_client`; the
   parsing libraries are out of the worker's and the API's requirements and
   lock files (only removals; versions unchanged; fresh-venv `pip check`
   clean).
4. **CI**: a new `parse` job (E1, E4, E3, the HTTP tests, the A/S/B
   self-tests, audit) and the worker job testing against the real image.
5. **D-163 and migration `0034`** (Q5): written. Not yet applied to staging.

Local results (database off where noted):
- parse 110 passed, with every positive and hostile fixture giving
  byte-identical answers to the baseline, both in-process and through the
  dev service over HTTP (56 passed);
- core 698 passed, 1 skipped;
- worker 83 passed, 46 skipped (database, prefork, and F5, which needs CI's
  container);
- API: the same 24 environment-only failures as the base branch (20
  `test_console_mfa`, 4 `test_d170_clock`), nothing else.

**Where the build differs from the design or adds to it. The founder
reviews each before the merge:**
- **A crashed job:** a job that dies some other way than our limits, such
  as a parser's segfault, or SIGSYS from the seccomp filter. The design
  covered an answer that fails the worker's checks but not this. Built the
  same way: DOC-005 to the customer, plus a founder alert, once per tenant
  per cause per day.
- **The DOC-029 alert isn't in `FAILURE_ALERTS`.** That map may only hold
  codes whose wording tells the customer "DocFlow has been alerted" (a test
  enforces it), and the founder's DOC-029 wording doesn't. So the alert is
  raised explicitly (`founder_alerts.raise_parse_alert`), with the agreed
  once-per-tenant-per-day limit.
- **The retry decision moved** to its own pure module,
  `docflow_core.retry_rules`. `stuck_documents` may be imported only by
  the sweep task (its cross-tenant session; `test_rls_flags.py`), and the
  worker's task now applies the same decision. The sweep re-exports it, so
  existing callers are unchanged.
- **`extraction_runs.counted_input_tokens`**, a column in `0034` beside
  `run_state` and `started_run_id`. The design said the started row
  carries the counted tokens, but not where. Kept apart from
  `input_tokens`, so a reader that sums tokens can never count a call
  twice.
- **The client's patience:** "never got in" (no connection, or a 503) is
  retried for up to 45 s before the document waits, because a stopped Fly
  machine is started by the first request. A 502 or 504 from Fly's proxy
  counts as "got in, never came out". A 401 (token mismatch) is "never got
  in", is not retried, and raises the hourly alert with reason
  `unauthorized`.
- **`TABLE_FORMATS` moved into `file_types`** (standard library only), so
  the parse image doesn't need `catalog_import` and SQLAlchemy.
  `catalog_import.TABLE_FORMATS` still exists.
- **The image's Debian sources (changed 2026-10-01, founder):** first built
  with exact version pins against the live archive, which broke on the first
  security update (above) and could have held a vulnerable LibreOffice in
  place. Now: the base image by digest, and apt reads only a dated
  snapshot of the archive (snapshot.debian.org, main and security,
  `DEBIAN_SNAPSHOT` in the Dockerfile, first date `20261001T000000Z`), with
  an `apt-get upgrade` so the base image's own packages also come from that
  date. No version pins: the date is the pin. A weekly workflow
  (`.github/workflows/debian-snapshot.yml`, Mondays 06:00 UTC) runs the whole
  CI workflow on today's date; green, it pushes `deps/debian-snapshot-<date>`
  with the new date and leaves the PR link as a notice; red, it pushes
  nothing and GitHub emails the failure. The founder opens the PR; 8.1 (Fly
  staging) comes before production. Recorded in D-183. *My choice, yours to
  change:* the job pushes a branch rather than only testing, because the
  date has to change in the repository for the tested image to be the one
  that ships. *Risk:* snapshot.debian.org is slower and less available than
  the live archive; apt retries 5 times, and an outage fails a build rather
  than changing what it installs.
- **lxml stays in the worker and API (founder: listed here).** openpyxl
  writes the .xlsx export through lxml when it's installed, and the bytes
  differ without it (first CI run, above), so `lxml==6.1.3` is pinned beside
  openpyxl as on `main`. It is never handed a file. **F2 as built:** a
  static scan of import statements and of the requirements files. lxml was
  never on its lists, so F2 neither changed nor failed. It never checked
  what is installed, so "no parsing library importable" overstated it. Now
  (founder, 2026-10-01): F2 keeps the import check for the real parsing
  libraries (PDF, image, Word, legacy Excel, and now LibreOffice's bindings
  `uno`/`unohelper`), and adds **the XML guard**: any direct lxml import
  (including `importlib.import_module("lxml...")`) or any
  `openpyxl.load_workbook` use, in any form, in `apps/api/app` or
  `apps/worker/app` fails CI, with a test that each form is caught. **Q11
  (founder): the guard covers `packages/core` too, with one exception named
  by file and function:** `exports.py::parse_xlsx`, which reads back the
  .xlsx `build_export` rendered a moment before (Section 7.4's round trip,
  EXP-004). The guard requires exactly its two uses there (the import and
  one `load_workbook(...)` call) and none anywhere else, so a second call
  fails the build (checked by planting one: both tests failed; file
  restored). What it is handed is checked twice: statically
  (`test_parse_xlsx_reads_only_the_bytes_just_rendered`: `load_workbook`
  gets `io.BytesIO(content)`, `content` is `render(...)`'s result in the
  same function, `PARSERS[fmt](content)` is the only call, and nothing
  outside exports.py names `parse_xlsx` or `PARSERS`) and at runtime
  (core's `test_the_xlsx_read_back_gets_only_the_bytes_just_rendered`: it
  receives the very bytes object `render()` returned, and a path string is
  a TypeError).
- **The parse token in CI (founder, 2026-10-01):** the API process never
  holds `PARSE_SERVICE_TOKEN`. In `5a00275` the API job did put it in the
  test step's environment, so the API's settings had it in that run. Now
  the step has `WORKER_HARNESS_PARSE_TOKEN` instead, read only by
  `as_the_worker` (apps/api/tests/conftest.py) around the worker's own
  `catalog_import.run_parse`. `test_parse_token_boundary.py`: the API's
  settings (environment and root .env) carry no token; a request made with
  the API's settings is refused by the real service as `unauthorized`, and
  the same request with the worker's token gets in (the control). Checked
  locally against a tokened dev service, and both tests fail when the API
  is given the token. **Q12 (founder): on Fly the API refuses to start
  holding the token**, as the parse service refuses to start without
  isolation: `parse_token_startup_refusal()` runs when `app.main` is
  imported, before the app exists, so uvicorn never opens its port.
  `fly_app_name` is a new settings field (Fly sets `FLY_APP_NAME`; never in
  .env). Tests: the rule in all four combinations, and a real `uvicorn`
  start with `FLY_APP_NAME` and the token exits non-zero, prints the refusal
  and never accepts a connection; the control (no token) opens its port.
- **Self-test additions:** B5 and B11 report each leftover process's name,
  state, parent, cgroup and PID namespace, PID 1 and the supervisor's own
  PID namespace. **B13** runs LibreOffice once inside the real sandbox, the
  way `conversion.py` does (that run decides the check), plus evidence:
  whether `/tmp` and `/var/tmp` are writable there, and the same run with
  LibreOffice's pipe pointed at the work directory (`OSL_SOCKET_PATH` as an
  environment variable, and as `-env:`). Evidence only: no limit or
  filesystem changed.
- **NOT-RUN, not NO-CONTROL (founder, 2026-10-01):** a check whose control
  didn't hold is reported as `NOT-RUN` ("proved nothing on this machine"),
  never as a pass, in the self-test output and its annotations ("parse
  self-tests: NOT RUN (no control)"). A-net IPv6 on CI is NOT-RUN: **IPv6
  isolation is unproven until the Fly run (8.1) passes it**, where the
  machine has IPv6. The startup canary's IPv6 line shows "blocked" on the
  runner too, but there it proves the same nothing; the canary is a gate,
  not proof.
- **The orphan reaper (B5/B11; founder: fix by reaping, never by filtering
  the check):** see the third run below.
- **PID 1 inside a real job is now a reaper (founder, 2026-10-01).** It was
  the parser: `sandbox_init` ran as the namespace's PID 1 and then `exec`'d
  into `setpriv` and the job, so anything LibreOffice left behind was
  reparented to a parser that never waits; the self-tests hid it, because
  their own PID 1 behaved differently. Now `sandbox_init` forks the job and
  stays PID 1 (`_reap_as_init`): it drops to the slot's user,
  no-new-privileges, non-dumpable, the job's seccomp filter, and only
  waits; when the job exits it exits, which ends every other process in the
  namespace. B11 checks it through the real request path (the row above).
- **How a job ended is decided from the supervisor's own records (founder,
  2026-10-01).** First built as "a signal N arrives as exit 128+N", which a
  parser exiting 137 by itself would imitate. Now: the supervisor's kill
  decisions (wall clock, CPU budget, answer cap) and the cgroup's OOM count
  (memory) decide; only when none applies does the parser's own end, and
  then only as a parser failure. That end comes from the reaper as one
  record on a pipe only the reaper holds (the job's copy is closed before
  it starts; the reaper is non-dumpable, so the job can't reach it through
  `/proc`): `{"exited": n}` or `{"signaled": n}`. No record, or
  `{"sandbox": "failed"}`, is the service failing to isolate. B14 and
  `test_classify.py`. **Q13 (founder, 2026-10-01): no exit code decides an
  outcome any more.**
  - **70 is replaced by a positive confirmation.** `sandbox_init`'s forked
    child does the hardening itself (no supplementary groups; empty
    bounding, inheritable and ambient capability sets; the slot's uid and
    gid; no-new-privileges; the seccomp filter), checks every one in
    `/proc/self/status`, then sends `{"hardened": true, "seccomp": ...}` on
    the private pipe, closes its copy, and only then `exec`s the parser.
    The supervisor accepts only that exact first line, for the seccomp
    setting it asked for (`seccomp` is false only for a self-test control).
    No confirmation: `isolation_failed` (the document waits; the hourly
    alert). With it, any exit is the parser's, whatever the code. This
    **replaces `setpriv`**, which did the same steps but couldn't confirm
    them before the parser started; the canary and A9 check the result as
    before. The reaper's later end record only names how a parser failed
    (`exit_N` or `signal_N`); hardened with no end record is `reaper_lost`
    (isolation failed).
  - **71 is a plain parser failure** (`crashed: self_reported_memory_error`,
    DOC-005, logged as `job_self_reported_memory_error`). Real overruns get
    DOC-029 from the cgroup: `RLIMIT_AS_BYTES` (2 GiB) is above
    `JOB_MEMORY_BYTES` (768 MiB), and a unit test keeps it that way. *One
    case to know:* a parser asking for more than 2 GiB in a single
    allocation is refused by RLIMIT_AS before touching memory, so it ends as
    the parser's own MemoryError (DOC-005), not DOC-029.
  - Tests: `test_classify.py` (12); B15 (a parser exiting 70 after the
    hardened message is `crashed: exit_70`); B16 (a self-reported
    MemoryError is `crashed`, no OOM kill; 1200 MiB under the default
    768 MiB cgroup is `stopped: memory` with an OOM kill).
- **`noexec` (founder, 2026-10-01)** on `/work` and the `/tmp`, `/var/tmp`
  bind mounts. A15: `/bin/true` copied into each can't run directly
  (EACCES) or through the dynamic loader (which must map the file
  executable); the control, the same copy outside the sandbox, runs.
  Interpreted code (`python file.py`) is not stopped by noexec. Whether
  LibreOffice still converts with it: B13, D1 and the worker's `po.doc`
  tests (all PASS on `0be5422`). **`/lohome` too (founder, 2026-10-01):** the
  profile folder holds settings, not programs; A15 covers it and B13/D1
  show `po.doc` still converts.
- **B11's exiting samples (founder):** accepted only when that same pid was
  earlier sampled live as `sandbox_init`; any other is listed and fails B11.

**Seventh run (`b013b6d`, the bind mount and the SIGCHLD reaper).** core 700,
0 failed; api 580, 0 failed; parse unit 116, 0 failed; **parse HTTP 56, 0
failed (D1 `po.doc` parity passes)**; worker 132, 1 failed (F5; **the
worker's `po.doc` preview test passes**); web and web-live passed.
Self-tests: canary 5 of 5; S 5 of 5; A all PASS (A14 and A11 with the bind
mount), IPv6 NOT-RUN; **B: B5, B11 and B13 PASS** (nothing left after the
kill or the 100 real requests; LibreOffice converts in the sandbox); B8
failed. Both failures were test bugs, fixed in the next commit:
- **F5** killed the container after a fixed 1 s; `po.doc` now parses in
  under a second, so the answer came first. Now it kills the moment
  `docker top` shows a parse job process in the container.
- **B8's new step** kept 128 MiB in `/work` as one file, over the 64 MiB
  per-file limit (RLIMIT_FSIZE): EFBIG, exit 1. Now four 32 MiB files.

**Eighth and ninth runs (`d1e1afe`, `0867e3f`).** `0867e3f`: core 700, 0
failed; api 580, 0 failed; **the parse job green for the first time**
(unit 122, HTTP 56, all 0 failed; canary 5 of 5, S 5 of 5, **B 17 of 17**,
A all PASS with IPv6 NOT-RUN; E1, E3, E4 as designed); web and web-live
passed; worker 132, 1 failed (F5). New checks all pass: A15 (`/work`:
EACCES directly, exit 127 through the loader; the control outside runs),
B13 with noexec (**LibreOffice still converts**), B14, B15 (`exit_70`), B16
(`self_reported_memory_error`, no OOM kill; the real overrun `memory`), B11
with the exiting rule.
- **F5, still a test problem:** `docker top` never showed the job process,
  so the kill waited 30 s and came after the answer. Now F5 watches the
  container's job cgroups straight from the runner (the container has its
  own cgroup namespace, so they are under its scope there): a job is seen
  the moment its cgroup is created, before the parser starts; if the
  directory isn't found, F5 says where it looked.

**`0034` applied on `docflow-staging` (founder, 2026-10-01).** Backup
`backup_0034` first (documents, extraction_runs; RLS on). Row counts before:
documents 105, extraction_runs 17; after: documents 105, extraction_runs 17;
live and backup match on both. `backup_0034` stays until 3c has merged and
run cleanly on staging for a few days, then it is dropped and recorded
(RUNBOOK 1.3).

**Departures reviewed (founder, 2026-10-01): 14 approved as written; two
changed, built in the next commit:**
- **#1: SIGSYS is its own alert,** `parse_seccomp_kill`, raised for every
  seccomp kill, never rate-limited (one row per document or import, no
  daily window), from the worker and from catalog import (which raised no
  alert for a crashed parse before). The syscall number can't be had: the
  filter answers listed calls with EPERM and kills only a call from a
  foreign architecture, and KILL_PROCESS reports nothing to anyone but the
  kernel's audit log. The alert says so (`syscall: null`,
  `syscall_unavailable`). Test:
  `test_F6_every_seccomp_kill_alerts_and_says_why_there_is_no_syscall_number`.
- **#5: the 5xx status is named.** The unavailable reason for a 503 is now
  `http_503` (was `busy_or_isolation_failed`); a lost parse's DOC-022 alert
  detail carries `last_lost_reason` (`http_502`, `http_504`, or the read
  error), so a Fly start failure can be told apart from a parser crash.
  Tests: `test_parse_client.py`, `test_retry_rules_detail.py`. RUNBOOK 8.4.

**Fly staging stood up (2026-10-01, design item 13 step 6; RUNBOOK 8.1):**
- Created, empty (no machines, no IPs, no secrets): `docflow-parse-staging`,
  `docflow-worker-staging`, `docflow-api-staging`; and the Upstash database
  `docflow-staging-redis` (pay-as-you-go, eviction disabled so the queue
  never drops a task, no replicas, ProdPack and auto-upgrade declined).
- New deploy files: `apps/parse/fly.toml` (private: `--no-public-ips`, then
  one private Flycast IPv6; stopped when idle, started on request;
  shared-cpu-2x, 2 GB), `apps/worker/{Dockerfile,fly.toml}` (Celery worker
  only, no beat, no services, no IP), `apps/api/{Dockerfile,fly.toml}`
  (private only). The worker and API images use the parse image's base digest
  and the same `DEBIAN_SNAPSHOT`, run as an unprivileged user, and the weekly
  snapshot job now moves all three dates. All three images built on Fly's
  remote builder (build only).
- Waiting on the founder: the secrets (`Desktop/3c-fly-secrets.txt`, the
  design's list). **`DOCUMENT_URL_SIGNING_SECRET`** (API only; not on the
  design's list, so asked first): **Fly staging gets its own newly generated
  value** (`secrets.token_urlsafe(48)`), not the root .env's or Supabase
  staging's, and production another, so a link signed in one environment
  never works in another; the founder generates and sets it and never
  pastes it (founder, 2026-10-01).
- **Database logins (founder, 2026-10-01): the worker and the API on Fly
  each get their own login from F-1, never `docflow_app` shared, never
  `postgres` or the service role.** F-1 (3e: `docflow_worker`,
  `docflow_api`) isn't built, so those logins don't exist on staging; the
  local `.env` connects as `docflow_app` (NOBYPASSRLS, no CREATE) through
  Supabase's transaction pooler. So for now only the parse app gets its
  secret (`PARSE_SERVICE_TOKEN`), which is all the RUNBOOK 8.1 merge gate
  needs; the worker and the API (the N2, N3 and G rows) wait for their
  logins. **Founder's call:** move N2, N3 and G to after 3e, or build F-1
  before 3c merges.
- Secrets are typed in a PowerShell window with history saving off
  (`Set-PSReadLineOption -HistorySaveStyle SaveNothing`; RUNBOOK 8.1).
- **Core on staging: `701 passed, 1 skipped`** -- the skip is
  `test_a_customers_own_signed_in_token_reaches_no_file`
  (`test_storage_bucket_live.py`): it mints a token with the project's JWT
  secret, which is blank on this machine (D-174); CI sets a throwaway one,
  so it runs there. **Worker on staging: `130 passed, 3 skipped`** -- the two
  prefork tests (Linux only) and F5 (needs CI's container); all three run in
  CI. **API on staging: `579 passed, 1 skipped, 3 deselected`** (580 in CI)
  -- the skip is `test_a_request_carrying_the_api_settings_is_refused`
  (`test_parse_token_boundary.py`): it needs a parse service holding a token
  (CI's API job); the dev service here has none. The 3 deselected are the
  `live_api` tests, excluded from every default run. The local dev parse
  service hit its 30-minute background limit partway through and was
  restarted; no test failed.

**Tenth run (`0be5422`): GREEN.** core 700 tests, 0 failed; api 580, 0 failed; worker 132, 0 failed (F5 included); parse unit 122, 0 failed; parse HTTP 56, 0 failed (D1 parity on every fixture, `po.doc` included); web and web-live passed; dependency audits clean. Self-tests in the real sandbox: canary 5 of 5, A 13 of 13 PASS with A-net IPv6 NOT-RUN, S 5 of 5, B 17 of 17; E1, E3 and E4 as designed. Recorded in D-183. Next: the
founder backs up and applies `0034` on staging (the PR text leads with it:
`Desktop/PR-stage3c-parse-service.md`), I run the staging suites, the
founder reviews the departures (above), then the Fly staging run (RUNBOOK
8.1: all the self-tests; IPv6 and cgroup v1 are proven only there).
**Founder, 2026-10-01: the Fly run comes before the merge**, not only before
production (RUNBOOK 8.1 and the PR checklist updated); and `/lohome` is
noexec too.
- **The service log gets LibreOffice's real error (founder):** a rejected
  conversion's reason, with the last 300 characters of LibreOffice's own
  stderr, goes to the job's stderr and from there to the service's log
  (`job_rejected kind=... code=... detail=...`). The answer to the worker,
  and so everything a tenant sees, still carries DOC-017 alone.
- **The seed script and the walkthrough file maker** now use the parse
  service (dev) and the parse venv.


**3d -- H4, per-tenant fairness.** Agreed design:
- Documents wait as `pending` in the database.
- A dispatcher takes turns between tenants, with a cap per tenant on
  documents already on the queue.
- `pg_trgm` matching stays in Stage 4.

**Interplay with the stuck-document sweep** (founder: written down before
building; each point that changes existing sweep behaviour is marked
**CHANGES**):

1. **CHANGES: the sweep stops enqueueing `pending` documents directly.**
   - Today every `pending` document older than
     `STUCK_PROCESSING_TIMEOUT_MIN` (30 min) is put on the queue again by the
     sweep, on the theory that its job was lost (D-095).
   - Under the dispatcher, `pending` is the normal state for a backfill still
     waiting its turn. The sweep enqueueing it would bypass the per-tenant cap
     and push a 500-document backfill onto the queue 30 minutes in, undoing
     fairness.
   - Proposed: `pending` splits into *waiting* (not yet dispatched) and
     *dispatched* (sent to the queue, not yet claimed), recorded by a
     `dispatched_at` column on `documents`, which is a migration. Only a
     document dispatched and unclaimed past the timeout counts as a lost job.
     The sweep hands it back to the dispatcher, which re-sends it within the
     cap. Nothing puts it on the queue except the dispatcher.
2. **CHANGES: the `document_stuck` "waiting" alert.**
   - Today one warning per tenant per day fires for any `pending` document
     past 30 minutes.
   - A backfill legitimately waits longer than that, so the alert would fire
     every day of every large backfill.
   - Proposed: the alert fires for a *dispatched* document unclaimed past the
     timeout (a lost job, as today). A document still *waiting* alerts only
     when the dispatcher itself has not run for a set time, which means the
     dispatcher has stopped. That would be a new constant, raised with the 3d
     design.
3. **Unchanged:** the handling of `processing` (retry, then DOC-022 and an
   alert after `MAX_PROCESSING_ATTEMPTS`). Quarantined and staged documents
   stay out of both the dispatcher and the sweep. A released document enters
   the dispatcher's turn order like a new one.
4. **To decide in 3d's design, before code:**
   - the per-tenant cap (a constant);
   - whether interactive single uploads skip ahead of a tenant's own
     backfill (Section 5.1 says interactive is always drained first);
   - what triggers the dispatcher: beat, each completion, each upload, or all
     three.

**3d also: when the model provider is down, documents wait instead of
failing.** **Required before the first pilot** (founder, 2026-09-29). Built in
3d. The requirements are the founder's; everything marked *proposed* is mine
and waits for an answer.

*Why.* Today a provider outage fails every document that arrives during it:
- the SDK retries twice in a few seconds;
- then the document is `failed` with DOC-008, and the customer has to
  upload it again;
- the founder gets one `document_failed` alert per tenant per day, which
  never says the provider is down.

Section 7.9's "retry with exponential backoff" and 7.15.3's "repeated
model-API failure" alert are not met. Found during the 2026-09-29 Anthropic
incident, which failed the Stage 2 golden run at 10:18.

1. **What waits, and what still fails at once** (founder):
   - **Waits:** HTTP 5xx, 529 (overloaded), 429 (rate limited), and
     network, connect or silent-stream timeouts, including a connection
     dropped mid-stream. Classified by status code, not by SDK class name,
     so an SDK upgrade can't silently move a code between the groups.
   - **Fails at once, as today:** every other 4xx (a bad request, a schema
     error), a malformed answer (DOC-009) and the read deadline (DOC-020).
   - *Question:* 401 and 403 mean our API key or account is broken. That
     fails every document for every tenant. *Proposed:* they still fail the
     document at once (founder's rule), but also raise the provider alert
     with cause `our_credentials`, because one alert is more use than a
     `document_failed` per tenant.
2. **Waiting is recorded in the database, not in Celery.** Celery's delayed
   tasks sit in a worker's memory and are lost with it.
   - On a waiting error, the task moves the document `processing ->
     pending`. That is a new transition in 0027's state machine, so it needs
     a migration.
   - It records, as *proposed* new columns on `documents`:
     `provider_wait_started_at` (set on the first wait, kept until the
     document leaves `pending`), `provider_retry_at`, and
     `provider_last_error` (the status code only).
   - The dispatcher skips the document until `provider_retry_at`.
   - The return to `pending` subtracts the claim from
     `processing_attempts`, so waiting never uses up the crash-retry budget
     (DOC-022) and never looks like a timeout (3a).
3. **Backoff and the maximum wait** (*proposed*):
   - Retries at 1, 2, 4, 8 and 15 minutes, then every 15 minutes
     (`PROVIDER_RETRY_MINUTES`).
   - A 429 waits at least as long as the provider's `Retry-After`.
   - **Maximum total wait: 6 hours** (`PROVIDER_MAX_WAIT_HOURS`), counted
     from the first wait. Why 6: it covers provider incidents measured in
     hours within one working day (today's was about 40 minutes of errors),
     and the "delayed" state (point 5) tells the customer all along, so a
     customer with an urgent order can enter it by hand without waiting for
     a failure. Longer keeps a same-day order in limbo; shorter fails orders
     that would have gone through.
4. **After the maximum wait: failed, with a code that blames the provider,
   not the file.** *Proposed* new catalog entry, **DOC-024** "The reading
   service was unavailable":
   - message: "DocFlow tried to read this order for 6 hours, but the service
     it uses to read orders did not respond. Nothing is wrong with the file.";
   - action: "Upload the same file again, or enter the order by hand if it
     is urgent. DocFlow has already been alerted.";
   - severity high, audience both.
   - It goes in `FAILURE_ALERTS`, so the promise of an alert is kept.
5. **The customer sees "delayed", not an error** (founder). *Proposed:* no
   new status. A `pending` document with `provider_wait_started_at` set is
   shown as **Delayed**, with a new info-level catalog entry, **DOC-023**
   "Reading delayed":
   - message: "The service DocFlow uses to read orders isn't responding
     right now, so this order is waiting. Nothing is wrong with the file.";
   - action: "Nothing to do. DocFlow retries automatically and has been
     alerted. If it is still waiting after 6 hours, this page will say so.";
   - one badge, reusing `PILL`.
   - The Console's document list shows the same.
6. **One provider-down alert across all tenants, and a recovery notice**
   (founder). *Proposed:*
   - A **global** table, `model_provider_state`, with one row per provider
     and no `tenant_id`. It is named here as a genuinely global table
     (Section 10) and written only by the worker. Its columns: status (up or
     down), `down_since`, the last error, and the last success.
   - The provider is marked **down** after 3 waiting-class failures within 5
     minutes, from any tenants, with no success between them
     (`PROVIDER_DOWN_FAILURES`, `PROVIDER_DOWN_WINDOW_MIN`).
   - Being marked down raises **one** high-severity founder alert with no
     tenant: the new type `model_api_failure` (7.9's "repeated model-API
     failure"). Its dedupe key is the outage's `down_since`, so the outage is
     one alert whatever its length. The payload carries the cause (`5xx`,
     `overloaded`, `rate_limited`, `network`, `our_credentials`) and the
     count of waiting documents.
   - The **first success** marks it up and raises an info-level
     `model_api_recovered` alert: how long it was down, how many documents
     waited, and how many reached the 6-hour limit. Both are emailed from
     their rows (7.9: one alert, one row, two channels). Acknowledging stays
     a human action; recovery does not acknowledge the down alert.
   - A tenant-less alert needs an insert policy. In 3d that is a flag
     policy, as `rollup_raise` (0017) and `intake_refusal` (0029) are, and
     3e moves it to the worker's login. **Sequencing note:** if 3e went
     first, the policy would be granted to `docflow_worker` from the start.
     The agreed order stays as is unless the founder moves it.
7. **While the provider is down, the dispatcher holds everything and
   probes.**
   - It dispatches no document except one probe every 2 minutes
     (`PROVIDER_PROBE_MINUTES`): the oldest waiting document, so there is no
     made-up request. An error costs nothing, and a success is a real order
     read.
   - On recovery the backlog goes out through the normal turn-taking, so the
     tenant with the biggest backlog does not go first.

**How it fits with the rest:**
- **Dispatcher (3d):** waiting documents are `pending` with a
  `provider_retry_at` in the future. They take their turn once it passes.
  While the provider is down, only the probe goes out. Interactive-first and
  per-tenant turn-taking are unchanged.
- **Stuck sweep:** a waiting document goes back to `pending` with
  `dispatched_at` cleared, so it counts as *waiting*, not a lost job, and
  the sweep leaves it alone. The sweep's "dispatcher has stopped" alert
  (interplay point 2) must not fire while the dispatcher is deliberately
  holding, so the dispatcher records its heartbeat on every pass, holding or
  not. `processing` handling (retry, DOC-022, 3a's timeout rule) is
  unchanged, because waiting never touches `processing_attempts` or
  `timeout_attempts`.
- **Allowance counter:** no change. Metering counts documents not `failed`
  or `quarantined`, so a waiting document counts from arrival, as `pending`
  does today, and one that ends as DOC-024 drops out of the count, as a
  failed document does today.
- **Cost circuit breaker:** errors are not billed and add nothing. A
  connection dropped after the answer began was billed for its input; it
  stays on the cost record as today (D-163) and counts. A retry pays again,
  and that is on the record too.
- **Example prompting:** a routing call that fails still means "no
  examples". If only the routing model is down, extraction goes ahead
  without examples, as today.
- **Resumed documents (H3):** a document whose answer is already saved
  makes no model call, so it never waits.
- **Quarantine and test batches:** a release or "Run extraction" puts
  documents into `pending`, and from there they follow the same path.
- **Needs a migration:** the transition, the three columns, the global table
  and the alert policy. Backup first, deletes nothing.

**Before the first pilot: measure the cost of the documents that cost the
most** (founder, 2026-09-29). The 18 documents in the Stage 2 checkpoint's
cost figures are all short, one-page text orders. On staging, measure cost
per document for:
- scanned PDFs;
- image and photo POs;
- multi-page orders (5+ pages).

Report them the same way (mean, median and max, by type), with the models
used and every call in the total. This needs paid runs, so its budget goes
to the founder first.

**Card billing with a 7-day trial -- required before the first pilot.
DECIDED (founder, 2026-09-29): D1, D3, D4, D5 and D6 as recommended below;
D2 split by customer type (below). D4's banner wording goes to the founder
before it is added. Build order: after the 3a PR merges and before 3b,
including a walkthrough in Stripe test mode.** **BUILT 2026-09-29 -- as built,
with the founder's follow-up answers and what Stripe test mode showed, in
DECISIONS.md D-181.** `0031` waits on the founder's backup and staging apply. The founder's pilot model is "customer signs, 7 days free, then Stripe charges
their card automatically". Today nothing charges a card:
- Go-live creates a `send_invoice` subscription with a 7-day trial (D-125).
  At the trial's end Stripe emails an invoice (month one plus the setup fee,
  D-113), payable within `INVOICE_DAYS_UNTIL_DUE`.
- The customer pays it on Stripe's invoice page. Unpaid, the subscription
  goes `past_due` only when the due date passes.
- Suspension is always the founder's decision: a `non_payment` cancel whose
  effective date is the first `past_due` notice plus `CURE_PERIOD_DAYS` (0,
  D-125).

What switching to `charge_automatically` would take (facts from Stripe's
documentation, read 2026-09-29; the items marked *verify* get checked in test
mode before building):

1. **Collecting the card.** There is no public signup (Section 3). The owner
   exists from tenant creation and is invited. Card details never touch
   DocFlow: Stripe's hosted pages collect them, and Stripe saves the card on
   the tenant's existing Stripe customer. Two hosted options, used together:
   - **Stripe Checkout in setup mode** for the first card. DocFlow creates the
     session server-side and sends the link with the invite or the go-live
     email. A webhook (`checkout.session.completed`) sets the card as the
     customer's default and records it on the tenant (*verify* the exact
     event fields).
   - **Stripe's Customer Portal** for later changes: an "Update card" link on
     the owner's billing page (owner and admin only). The portal must first
     be configured once in the Stripe dashboard.
   - **Decision D1: when is a card required?**
     - (i) **Recommended: before Go live is enabled.** Step 9's gate gains
       "card on file", so every trial ends with a card to charge.
     - (ii) The trial starts without a card. Stripe supports that, and ends
       the trial by pausing or cancelling the subscription if no card has
       been added. DocFlow would need a "trial ended with no card" alert, and
       its handling of a `paused` subscription.
2. **The trial.** Unchanged: `trial_end` 7 days after go-live. Stripe can
   send its own trial-ending reminder, and emits
   `customer.subscription.trial_will_end` three days before. The
   subscription is `trialing` until the first successful charge, then
   `active`. MRR already shows trials beside it, not in it (D-135).
3. **The setup fee. Decision D2 -- decided (founder, 2026-09-29): it depends
   on the customer.** Both paths:
   - **Founding customers: charged with month one when the trial ends.**
     - The card page is Stripe Checkout in **setup mode**: it saves the card
       and charges nothing.
     - At go-live the setup fee is added as a pending invoice item, exactly as
       today. Stripe puts it on the first invoice at the trial's end and
       charges it with month one, in one charge.
     - If that charge fails, the normal failed-payment path applies (item 4).
   - **Standard customers: charged at signing.**
     - "Signing" is the customer completing the card page DocFlow sends them.
       That page is Stripe Checkout in **payment mode**, for the tier's setup
       fee, and it saves the same card for later charges (*verify* the exact
       parameters in test mode).
     - A webhook records that the fee is paid and the card is on file.
     - At go-live the subscription starts with the 7-day trial and **no**
       setup-fee item, because it's already paid.
     - A declined card: Stripe's page tells the customer, and nothing is
       charged or saved. The same link can be used again, or re-sent.
   - **What this changes in the flow:**
     - Founding or standard has to be known when the card page is sent, not
       only at go-live, where the founding choice is made today. The Console
       action that sends the card link asks for it, and the go-live form shows
       what was chosen.
     - The go-live gate (D1) is "card on file" for founding customers, and
       "card on file and setup fee paid" for standard customers. Either gate
       is met by `invoiced_manually`, which stays available for both.
     - The fee amount always comes from the tenant's tier version, never
       typed in (Section 7.15.2).
   - **For the founder, not blocking:** if a standard customer pays at
     signing and never goes live, any refund is the founder's decision,
     made in Stripe. DocFlow does nothing automatically.
4. **When a charge fails.** The subscription goes `past_due` on the **first**
   failed charge. That's day 7, not day 22 as with invoices due in 15 days.
   - **Retries:** Stripe retries on the retry schedule set in the dashboard.
   - **Customer emails:** Stripe can email the customer about a failed charge
     or an expiring card (dashboard settings). For a card that needs 3D
     Secure off-session, it can email a link to authenticate.
   - **After the final retry**, Stripe does what the dashboard says: cancel,
     mark `unpaid`, or leave `past_due`. **It must not be "cancel".** A
     Stripe-side cancel bypasses DocFlow's lifecycle and leaves an active
     DocFlow tenant with no billing. This is already an open item: set it to
     "leave past due" before the first real customer.
   - **The founder sees** the existing `stripe_subscription_past_due` alert,
     as today.
   - **Decision D4: what the tenant sees.** Today, nothing in the product.
     Proposed: a banner for owners and admins while the subscription is
     `past_due`: the last payment didn't go through, update your card
     (portal link). It needs a new catalog entry, whose wording goes to the
     founder first.
   - **Decision D3: when suspension can start.** Unchanged in mechanism: the
     founder confirms a `non_payment` cancel. But with `CURE_PERIOD_DAYS = 0`
     the computed date is the first failed charge, while Stripe is still
     retrying. **Recommended: set `CURE_PERIOD_DAYS` to cover the retry
     window**, e.g. 14 days; the ToS placeholder is 15.
5. **How it meets what 3a built:**
   - **Suspension's cancel:** cancelling a subscription also stops Stripe
     collecting its unpaid invoices (documented), so no retry charges a
     suspended customer. **An owed cancel matters more with cards:** until it
     goes through, Stripe charges the card, not just emails an invoice. The
     sweep retries it every tick (D-179), and the reactivation invoice alert
     lists anything charged.
   - **Reactivation reusing a live `past_due` subscription:** Stripe keeps
     retrying its open invoices. **The card may be charged for the
     before-suspension invoices straight after reactivation.** The alert
     lists them. If the founder wants them voided first, that's done in
     Stripe before clicking Reactivate.
   - **Reactivation with a new subscription** (the old one cancelled): the
     saved card stays on the Stripe customer (cancelling a subscription
     doesn't remove it). With no trial on reactivation (D-125), the first
     month is charged at once.
   - **Decision D5: what if that first charge fails?**
     - **Recommended: refuse the reactivation.** Stripe's
       `payment_behavior=error_if_incomplete` (*verify*) makes the create
       fail. The tenant stays suspended, and the founder sees a catalog
       message saying the card was declined.
     - The alternative, an `incomplete` subscription on an active tenant,
       would lapse to `incomplete_expired` after 23 hours.
6. **Decision D6: can both methods coexist, per tenant?** Yes.
   `collection_method` belongs to each Stripe subscription, not the account.
   A subscription can be switched later (only invoices created after the
   switch use the new method, and it needs a default card first).
   - **Recommended:** a `billing_method` on the tenant (`card` | `invoice`),
     chosen at go-live, with card the default for pilots. Invoice billing
     stays for a customer who pays by bank transfer.
   - Reuse, the owed cancel and the invoice alert work the same for both.
7. **What building it touches:**
   - one migration (`tenants.billing_method`, and when a card was put on
     file);
   - `external_services`: Checkout and portal sessions, the collection method
     and payment behaviour on create;
   - the go-live gate;
   - an owner billing page (update card; the past-due banner);
   - webhook handling for the new events;
   - catalog entries (card needed, payment failed, reactivation declined);
   - Stripe dashboard settings (retry schedule, customer emails, after the
     final retry = leave past due, the portal);
   - tests, including a walkthrough in Stripe test mode with Stripe's test
     cards (one that is declined, one that needs authentication).
   About the size of slice 5.9. Stripe charges its standard card fees; no
   new service is needed.

**3e -- F-1, separate database logins.** Approved as proposed:
- **`docflow_api`**: tenant requests, plus the intake-token, sign-in-identity
  and refusal-alert policies.
- **`docflow_admin`**: the Console and the maintenance scripts, holding every
  `platform_admin_access` policy, 0029's `platform_admin_read` included.
- **`docflow_worker`**: the rollup, scheduler, pipeline-sweep and lifecycle
  policies, plus tenant sessions for jobs.
- **`docflow_stripe`**: the Stripe lookup policy and EXECUTE on
  `record_stripe_subscription_event()`, with EXECUTE revoked from
  `docflow_app`. This closes D-173's residual risk.

Counted on `main` 2026-09-29: 52 policy uses of session flags across 19
migrations, and each flag session is opened by one service only.

**Founder's condition: the pooler limits are checked before the migration is
written.** Checked 2026-09-29:
- **Each login gets its own pool.** Supabase's Supavisor FAQ: the pool size
  is the most direct connections the pooler keeps "per unique user, database,
  and mode combination"; two combinations at pool size 120 may form 120 each.
  Supabase Storage is one more combination with the same pool size.
- **Staging's limit:** `max_connections` = 60 (read from the database), with 3
  reserved for the superuser. That is Supabase's Nano or Micro size. **The
  dashboard shows Nano** (founder, 2026-09-29). My first reading, Micro from
  the memory settings, was wrong. Supabase's own services held 13
  connections at the time of reading.
- **Headroom:**
  - After 3b and 3e there are five combinations: four logins plus Storage
    (`docflow_app` is retired by F-1).
  - The worst case is 5 x the pool size, against about 44 usable connections.
  - Supabase's guidance for several combinations is to keep the pooler under
    40% of `max_connections` (24 here).
  - So four logins fit with headroom **only at a pool size of about 4-5**. The
    pool size is a dashboard setting (Database -> Settings -> Connection
    pooling -> Default Pool Size), readable by the founder only.
- **Options, for the founder:**
  - keep the compute and set the pool size to 4-5 (recommended for staging);
  - upgrade staging to Small (90 connections, about $15/mo against Nano's
    $0);
  - merge `docflow_stripe` into `docflow_api`, not recommended because it
    reopens D-173's risk.

- **Decided (founder, 2026-09-29): keep the compute (Nano) and set the pool size to 5.**
  Default Pool Size was **15** (founder, 2026-09-29). At 15, the five
  combinations could ask for 75 connections, more than the database's 60,
  which is why it comes down. **The founder changed it to 5 on 2026-09-29**,
  after the Stage 2 checkpoint's database suites had finished, so the change
  could not drop a running suite's connections. **Compute is Nano** (founder,
  from the dashboard). Five combinations at 5 is 25 connections
  at most, against about 44 usable. The first staging suite run after the
  change shows whether 5 is enough for the tests that hold one connection
  while probing with another; the result is reported.
- **Production's pool size and compute are decided in Phase 6, with real load,
  not inherited from staging** (founder).

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
#5, #7. **DONE (PR #24, merged 2026-09-29).**
One commit per item, in that order. Each makes the database's clock write what the database compares: the deletion date, the
delete guard and the reminder's day count (#2); the sweep's re-check and the "immediate" effective date (#1, which the founder
widened to include `lifecycle.py:156`); every job's `run_at` (#4); the rollup's staleness verdict (#5); and the once-a-day alert
keys' date, computed inside the INSERT (#7). Every new test runs the module's app clock off the database's (`tests/app_clock.py`),
both ways, and each was shown failing against the old code by exactly the skew (18 tests). No schema change, so no backup.
The last commit is a CI check (`packages/core/tests/test_one_clock.py`): a database-access module may not pass the app's clock
into SQL, except with a written reason on the call. It was shown failing on a planted case. It does not follow a value into a
helper function or see a comparison made in Python; those stay review questions (D-170).

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
- **F-1 (D-159): 52 RLS policy uses are keyed on `app.*` settings any
  connection can set** (counted 2026-09-29) -- enforced by code and guard tests
  today, not by the database. Stage 3e: four logins (API, admin, worker,
  Stripe), approved 2026-09-29; about 3-4 days, $0; pooler headroom checked
  (see "Stage 3 -- agreed with the founder before building").
- **Blank audit actors (D-159, D-165):** fixed by migration 0028 (applied and verified on staging 2026-09-26): the 7 staging
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
- **Before the first pilot (founder, 2026-09-29):**
  - a provider outage makes documents wait, not fail (built in 3d; design
    under "Stage 3 -- agreed with the founder before building");
  - cost per document measured on scanned PDFs, image and photo POs, and
    multi-page (5+ pages) orders, on staging;
  - **card billing with a 7-day trial**, decided 2026-09-29 (under "Card
    billing with a 7-day trial"). **MERGED 2026-09-29 (D-181; PR #28,
    migration `0031` on staging; staging suites: worker 134 passed / 2
    skipped, API 554 passed).** **Follow-up BUILT (branch
    `phase55/trial-ending-email`, migration `0032`, D-181 addendum 2):** the
    trial-ending email to card-billed owners 2 days before the trial ends;
    the cancel-by date (the day before the trial ends, in the customer's
    timezone) in the go-live and trial-ending emails; "Ask for a card" says
    the go-live email gives the date; the founding rate followed by the
    tier's list price; Reply-To = `SUPPORT_EMAIL` on every email to the
    customer's own people (`email_outbox.reply_to`, shown in the Console
    Outbox). `0032` applied on staging by the founder 2026-09-30 (backup
    `backup_0032`, counts matched). Staging on `6712214`: worker 134 passed /
    2 skipped, API 562 passed / 3 deselected; CI green on `88a84b4`.
    **MERGED 2026-09-30 (PR #29, main `07c1f1f`).** The founder then dropped
    `backup_0026` to `backup_0032` on staging (RUNBOOK 1.3), so staging holds
    no backups. The Stripe test-mode walkthrough
    (`docs/walkthroughs/card-billing.md`) is still to do.
  - **The setup fee is non-refundable for standard customers** (founder,
    2026-09-29): it covers the setup work and is charged when they add their
    card, and the "Ask for a card" email says so. **Required before the first
    pilot: the customer agreement must say the same.**
  - **A support mailbox, and `SUPPORT_EMAIL` set** (founder, 2026-09-29):
    the card-billing emails tell customers to email it to cancel; "Ask for a
    card" refuses while it is blank. RUNBOOK section 3; cancel requests and
    the refund rule, RUNBOOK section 6.
  - **Stripe's own trial-ending email off, in live mode too** (founder,
    2026-09-29; off in the sandbox): DocFlow's own trial-ending email for
    card-billed customers replaces it (built, migration `0032`). RUNBOOK
    section 3.
  - **Emails must go out on time: automatic sending, or a daily Outbox
    commitment with a stale-email alert** (founder, 2026-09-29). **Required
    before the first pilot, one or the other.** Today every email waits in
    the Console Outbox until the founder sends it by hand (D-103), and the
    trial-ending email, the past-due emails and every cancel-by date promise
    depend on it going out when it says. Either:
    - (a) **automatic sending**: an email provider with a verified sending
      domain and a sender that delivers queued rows, retries, and reports
      bounces (what it would take: D-103 addendum); or
    - (b) **the founder sends from the Outbox every day**, and an alert
      fires when an email has waited too long, so a missed day is noticed
      rather than a customer's reminder silently going out late.
    **Decided (founder, 2026-09-30): (a), automatic sending, as its own
    stage after 3e and before the first pilot; a duplicate email is
    preferred over a lost one (at-least-once).** The founder is setting up
    the Postmark sending domain now (steps given in chat, 2026-09-30), so
    DNS verification isn't on the stage's critical path.
- **Security upgrades, 2026-09-30 (branch `phase55/security-pyjwt-next`,
  founder-approved as its own PR before 3b merges). MERGED 2026-09-30 (PR
  #30, main `166896e`), before 3b.** CI's dependency audit
  began failing on every job for two advisories published after `main`'s
  last green run. Neither was introduced by any branch, and the audit was
  not weakened (no ignore entries, no skipped step).
  - **PyJWT 2.14.0 -> 2.15.0** (CVE-2026-101918: a deeply nested payload
    escaped as a raw `RecursionError`). Pinned in both lock files. Core's
    floor was raised from `>=2.8` to `>=2.15.0`, so nothing can install a
    vulnerable version.
  - **What the changelog changes for us:** nothing about how tokens are
    validated (audience, algorithms and leeway defaults are unchanged, and
    `deps.py` passes all three explicitly anyway). Two errors that used to
    escape (the recursion one, and a malformed `exp`, `nbf` or `iat`) now
    raise `PyJWTError`, which `deps.py` already treats as "not signed in".
    The signing-key fetch (`PyJWKClient`) now skips a malformed key in the
    set instead of failing the whole set; we only call
    `get_signing_key_from_jwt`.
  - **Next.js and `eslint-config-next` 16.3.5 -> 16.3.8** (GHSA-vcvr-r3jv-pc5j,
    critical: remote code execution in `next/og` `ImageResponse`). DocFlow
    doesn't use `next/og` (`apps/web/src` searched), so the path wasn't
    reachable, but it's a patch release, so no exception was made. The lock
    file changed only the Next.js packages.
  - Checked on this branch: `pip-audit` clean (api, worker); `npm audit` 0
    vulnerabilities; core 629 passed; web typecheck, lint, Vitest 59,
    `next build` on 16.3.8 and the browser suite 72 passed; the auth and
    tenant-boundary API files on staging (below); and one sign-in by hand
    each as a tenant user and as the founder.
- **CI names the step that failed when there is no test report**
  (founder-approved, 2026-09-29): a worker run whose local Supabase stack
  failed to start had read as a pytest failure. The skip check now reads the
  earlier steps' outcomes (`CI_STEPS`) and says which one failed.
- **Before the first real customer:** an email provider (the founder is setting one up with the domain). Until then every invite, notice and digest waits in the Console Outbox and must be sent by hand, and the inbound intake address cannot receive real mail.
- Digest opt-out per person: decided yes, but later (needs a settings page).
- `RUNBOOK.md` exists since Phase 5.5 with the migration backup procedure (section 1). Still to add in Phase 6: the constants (CLAUDE.md 7.15.4; `constants.py` is their single home until then), tier price changes (`scripts/new_tier_version.py`, D-137), the restore drill and the parser-upgrade process.
- **Stripe setting, before the first real customer:** the account currently cancels a subscription after 90 days of an unpaid invoice (seen on Acme Test Prospect: "Auto-cancels Dec 18"). Policy is that the founder decides suspension (D-125), so set it to leave the subscription past due, **both for invoices sent to customers and for failed card payments** (card billing, decided 2026-09-29). Only the founder can change it. Now on the RUNBOOK section 3 checklist ("Once, before the first real customer").
- Sandbox leftover: Acme Test Prospect's founding coupon was created before the invoice-count fix (D-138) and discounts one extra invoice (19 Dec). Test data only; correct it in Stripe or leave it.

- **Phase 5.5 open items (2026-09-25):**
  - `backup_0026` (document_headers, document_lines; RLS on, no policies) was kept on staging until the founder said to drop it (D-156). **Superseded 2026-09-29:** backups are dropped on staging once the migration's PR has merged, and in production 14 days after the migration is applied there, each by the founder (RUNBOOK 1.3). All of them, `backup_0026` to `backup_0032`, were dropped on staging 2026-09-30.
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
