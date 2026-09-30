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
commit as the slice. Statuses below are as of **2026-09-29** (Phase 5.5: Stages 0, 1 and 2 done -- 2a-2d merged (PRs #14, #15, #18, #20, plus #21 and #22), the audit-findings design (#23) and the D-170 clock PR (#24) merged, Stage 2 checkpoint written; **Stage 3 design agreed 2026-09-29; 3a merged (PR #26, D-179; `0030` on staging); the test-run lock merged (PR #27, D-180); card billing built (D-181), migration `0031` awaiting staging; 3b next**; `0029` row counts confirmed by the founder (the only difference: 52 `stripe_webhook_events` test ids from post-migration runs); D-150 settled -- Fly.io, proof spike PASSED 2026-09-28).

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
| 3 | Worker, storage, queue: H6 Supabase Storage, H5 platform-enforced parsing isolation (**host settled, D-150: Fly.io, each parse process in its own network namespace; proof spike PASSED 2026-09-28. Carried in from the spike: hide `/.fly` and `/sys` in a mount namespace, and re-run the probe against the real Upstash and API**), H4 per-tenant fairness, **F-1 separate database logins for API / worker / admin** (propose with cost and effort, then stop for approval) -- **including a login for the Stripe webhook that holds EXECUTE on 2c's event function, with EXECUTE then revoked from `docflow_app`**, which closes the residual risk D-173 names. **Also moves with it (founder, 2026-09-28): 0029's `platform_admin_read` policy on `stripe_webhook_events`** -- a flag policy on `app.is_platform_admin`, so it goes to real login separation with D-173's function grant; likewise 0029's `app.intake_refusal` policies (D-175 §8). **H6 note: signed URLs become cross-clock** -- minted and verified on the app clock today (`signed_urls.py`), one clock because one service does both; on Supabase Storage the expiry is Supabase's clock, so D-170 applies (a named tolerance and a test, or the expiry decided in one place) -- **settled 2026-09-29: the expiry is decided in one place, our own signed links with the API streaming from Storage.** **Also carried from the Stage 1 checkpoint (D-163):** a run row before the model call, so a worker killed mid-call still records the call's cost | **IN PROGRESS** -- design agreed 2026-09-29 ("Stage 3 -- agreed with the founder before building", below); order 3a -> 3e. **3a BUILT** (D-179; branch `phase55/stage3a-task-limits`, migration `0030`), including reactivation option C (**3a MERGED, PR #26**). **Card billing, between 3a and 3b: BUILT** (D-181; branch `phase55/card-billing`, migration `0031` waiting on staging; see "Card billing with a 7-day trial"). **Also (founder, 2026-09-29): a second test run against the same database refuses to start** -- an advisory lock in the API and worker suites (D-180, RUNBOOK 1.4). `0030` applied to staging 2026-09-29 after the founder's backup (`backup_0030`: documents, tenants, RLS on). Staging suites on `621141f`: worker 134 passed / 2 skipped (the two prefork tests, Linux only; they pass in CI), API 518 passed / 1 failed / 3 deselected, core 564 passed. The one API failure, `test_a_failed_tenant_creation_rolls_back_everything_including_the_file_move`, counts every tenant on staging and saw the count fall from 20 to 19 during the test -- something else was writing to staging at that moment; a failed creation cannot remove a tenant. Rerun alone: the file 13 passed, the test 3 of 3 passed. D-163 moved to 3c | D-003, D-150, D-159, D-163, D-170, D-173 |
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
  file: a 404 for the file, never a 500 for the screen.
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
- **Stage 5 question (founder, 2026-09-30): a catalog import failed by the
  stuck sweep raises no alert.** The sweep marks an import left in `parsing`
  failed with IMP-009 and tells no one. CLAUDE.md 7.9 requires an alert for a
  document stuck past the timeout, and a stuck import is the same silent
  failure for the founder. Exports have `exports_not_finishing` (more than 3
  in a day); imports have nothing. To decide in Stage 5, not built in 3b.
- **Known issue for Stage 5** (the sweep and robust test cleanup): seeded
  rows with a faked `content_sha256` fail DOC-026 if they are ever read
  again for extraction (the hash check, Q5). Fix the seed scripts to store
  the real hash, and repair or remove these 14 rows.

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
