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
| `docs/designs/` | The design and build record of each merged slice, moved here word for word once it merged (Stage 3: 3a to 3e, and card billing). Read when a task needs one |
| `docs/status-history.md` | Earlier status paragraphs of this file, word for word, newest first. Not read at session start |

**Keeping this file current:** update it at the end of every slice, in the same
commit as the slice.

**Status, as of 2026-10-08:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done through step 6. **The first worker deploy (RUNBOOK 9.7) is done
through step 15: every gate passed, and the founder accepted the 500 + 1 run on 2026-10-08**
(D-194, D-196; CHECKPOINTS.md, Stage 3, "The first worker deploy"). The deployed worker has read
532 documents on Fly staging; recorded model spend is $6.91. The Fly worker and beat are at 0 and
the Healthchecks.io check is paused. **Next:** two PRs proposed for the founder's review, neither
merged without the founder: the recycle threshold at about 500 MiB, and a longer queue-poll
interval with task-result storage off, followed by one idle hour measured again (D-196). Now due,
the founder's actions: dropping `backup_0034`, `backup_0035`, `backup_3b` and the role
`docflow_app` (RUNBOOK 1.3; 10.2 step 7). Then the Stage 3 checkpoint. Nothing of Stage 4 is
built before its "go", and the model policy (D-195) waits for the same "go".

**That paragraph is the current state only** (founder, 2026-10-05; D-191). When it changes, the
paragraph it replaces moves, word for word, to the top of `docs/status-history.md`, which is not
read at the start of a session.

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
| 3 | Worker, storage, queue: H6 Supabase Storage, H5 platform-enforced parsing isolation (**host settled, D-150: Fly.io, each parse process in its own network namespace; proof spike PASSED 2026-09-28. Carried in from the spike: hide `/.fly` and `/sys` in a mount namespace, and re-run the probe against the real Upstash and API**), H4 per-tenant fairness, **F-1 separate database logins for API / worker / admin** (propose with cost and effort, then stop for approval) -- **including a login for the Stripe webhook that holds EXECUTE on 2c's event function, with EXECUTE then revoked from `docflow_app`**, which closes the residual risk D-173 names. **Also moves with it (founder, 2026-09-28): 0029's `platform_admin_read` policy on `stripe_webhook_events`** -- a flag policy on `app.is_platform_admin`, so it goes to real login separation with D-173's function grant; likewise 0029's `app.intake_refusal` policies (D-175 §8). **H6 note: signed URLs become cross-clock** -- minted and verified on the app clock today (`signed_urls.py`), one clock because one service does both; on Supabase Storage the expiry is Supabase's clock, so D-170 applies (a named tolerance and a test, or the expiry decided in one place) -- **settled 2026-09-29: the expiry is decided in one place, our own signed links with the API streaming from Storage.** **Also carried from the Stage 1 checkpoint (D-163):** a run row before the model call, so a worker killed mid-call still records the call's cost | **IN PROGRESS** -- design agreed 2026-09-29 ("Stage 3 -- agreed with the founder before building", below); order 3a -> 3e. **3b MERGED 2026-09-30 (PR #31, main `5810a54`, D-182). **3c MERGED 2026-10-01 (PR #32, main `085a2a5`; Fly staging run 2 passed every item, D-183).** **3d MERGED 2026-10-02 (PR #33, main `4a2b907`, D-184; `0035` on staging; the dispatch process on its own queue per the founder's condition) -- see "3d build" and "3d on staging". The 500 + 1 run, memory, fresh parse token, restart record, external monitor and the two two-at-once sweep tests gate the first worker deploy after 3e. 3e (F-1) MERGED 2026-10-02 (PR #35, `e99fbb9`; D-185; see "3e build -- as built" and "3e MERGED"); `0036` waits for the 2026-10-05 check, then the cutover (RUNBOOK 10.2).** Earlier: 3c design approved 2026-10-01 (Q1-Q10); BUILT and CI GREEN 2026-10-01 on `0be5422` (D-183; founder's Q11-Q14 decided during the build); migration `0034` awaiting staging; the Fly staging run (RUNBOOK 8.1) owed; see "3c build".** **3a BUILT** (D-179; branch `phase55/stage3a-task-limits`, migration `0030`), including reactivation option C (**3a MERGED, PR #26**). **Card billing, between 3a and 3b: BUILT** (D-181; branch `phase55/card-billing`, migration `0031` waiting on staging; see "Card billing with a 7-day trial"). **Also (founder, 2026-09-29): a second test run against the same database refuses to start** -- an advisory lock in the API and worker suites (D-180, RUNBOOK 1.4). `0030` applied to staging 2026-09-29 after the founder's backup (`backup_0030`: documents, tenants, RLS on). Staging suites on `621141f`: worker 134 passed / 2 skipped (the two prefork tests, Linux only; they pass in CI), API 518 passed / 1 failed / 3 deselected, core 564 passed. The one API failure, `test_a_failed_tenant_creation_rolls_back_everything_including_the_file_move`, counts every tenant on staging and saw the count fall from 20 to 19 during the test -- something else was writing to staging at that moment; a failed creation cannot remove a tenant. Rerun alone: the file 13 passed, the test 3 of 3 passed. D-163 moved to 3c | D-003, D-150, D-159, D-163, D-170, D-173 |
| 4 | Matching performance (H4): `pg_trgm`, measured p50/p95 at 50k items. **Also (founder, 2026-09-29): audit the ~23 broad `except` blocks on the document path.** Each one turns *any* exception into a data outcome -- DOC-005 (parsing, conversion), DOC-021 (saving, validation) or VAL-016 (buyer identification, matching, duplicate detection) -- so a real bug in our code can be shown as a problem with the customer's file. After the audit only the exceptions each block expects get a catalog code; anything else fails loudly as our error. Found while designing 3a's timeouts, not in 3a's scope | **DESIGN APPROVED WITH CHANGES 2026-10-02** ("Stage 4 detailed design": Q1-Q15 decided, two PRs with B first, the database retry in PR 2 with `0037`; DOC-030, VAL-017 and VAL-018 final; PR 1's content final at `444dc78`, blocked only on the Stage 3 checkpoint's "go"; the audit counts 34 blocks, not ~23); building waits for the Stage 3 checkpoint's "go"; `0037` after `0036`, and nothing on staging before `docflow_app` is dropped (about 2026-10-08) | D-152 |
| 5 | Remaining findings (**review findings still open for Stage 5, counted 2026-10-01: Critical 0, High 0, **Medium 3** (was 4: **M6 fixed in 3d** -- `rollup_stale` registered in `ALERT_TYPES` and raised through the rollup's own session, with a test that raises every registered type; on `main` once 3d merges) -- M7 the three missing Phase 5 alerts (D-155); M10 nine settings never read, the model IDs hardcoded; M11 SETUP.md and a migration ledger -- Low 0**: Lows are fixed only when their file is touched, D-155; the review put M8, M9, M12, M13, M15, L2 and L8 in Phase 6 and deferred L1, L3-L7 and L9), doc/code contradictions (**plus one found 2026-10-01: `celery_app.py` says interactive is always drained first, but kombu's Redis transport takes turns between queues**), proposed CLAUDE.md additions; **audit every test that counts a whole table** (the `deal7` pattern) and move each one to data only that test can see, after which staging suites may run concurrently again (RUNBOOK 1.4); **robust test cleanup** (every test that creates data cleans it up in a fixture or `finally`, so a failing test still leaves nothing); **a staging sweep script** that lists tenants named "Acme Test ..." older than a day, with what each holds, and deletes one only on the founder's per-action OK (a stopped run always strands something); **triage the API suite's warnings** (425 on the 2026-09-26 run): list each kind, say which are harmless library deprecations and which point at a real problem in our code -- listed, not fixed (triage done 2026-09-26, D-163: all 439 are test-only; 438 are PyJWT's `InsecureKeyLengthWarning` from short test signing keys); **use a test JWT secret of at least 32 bytes** to clear that noise (founder); **a test that expects the database to refuse a write** must run in a transaction that is always rolled back, or on data it owns, so it can't leave a row behind when the refusal doesn't happen (D-165 incident); **two audit-trail findings from the second lost-device drill (D-178; founder: fixed before any pilot, with tests; design settled 2026-09-28)** -- (1) refused Console and step-up attempts are recorded server-side (AUTH-006/007); AUTH-008 is never browser-reported, and Supabase's database audit log was checked and records nothing on this project, so that gap is documented and Supabase's rate limit covers wrong-code guessing (its behaviour measured with the test account at build time); 5 refusals in 15 min for one account raise one high-severity founder alert per window, set only after measuring what a normal sign-in and step-up produce; (2) the outcome is a second, append-only `admin_actions` row referencing the intent row (succeeded, or failed with its code; no migration), and an intent with no outcome is shown in the Console as crashed midway, never as done; *low priority, not a blocker:* **count rows in spreadsheet and CSV orders for free before extraction** (no model call needed), so an oversized order is caught before a paid read (founder, D-163) | PLANNED | D-160, D-163, D-165, D-178 |

### Stage 3 -- agreed with the founder before building (2026-09-29)

Written here before any code, as for 2c and 2d. The founder's go for Stage 3
came on 2026-09-29: **build all of it, in the order 3a -> 3b -> 3c -> 3d -> 3e**
(ahead of schedule). Each slice is its own branch and PR. Items marked
*proposed* are mine and wait for the founder's answer before they are built.

**Where the Stage 3 designs are (moved 2026-10-05, word for word; the founder's context
housekeeping, D-191).** Every Stage 3 slice is merged, so its design and build record now lives in
`docs/designs/`: [stage-3a.md](designs/stage-3a.md), [stage-3b.md](designs/stage-3b.md),
[stage-3c.md](designs/stage-3c.md), [stage-3d.md](designs/stage-3d.md),
[card-billing.md](designs/card-billing.md) and [stage-3e.md](designs/stage-3e.md). **Each heading
other files cite is kept below as a one-line stub**, in its original words, so a citation such as
BUILD-STATUS "3c detailed design" still finds its place. A stub's status wording ("PROPOSED",
"IN PROGRESS", "awaiting ...") is the heading as written at the time, not today's state: all of
3a to 3e and card billing are built and merged. What stays in this file, in full: the gates for
the first worker deploy, the 3d and 3e merge records with the backup dates, the open cost
measurement, and everything from 2026-10-05 on.

- **3a -- H5, the quick part: time limits on every task, and a memory cap.** Moved word for word to [docs/designs/stage-3a.md](designs/stage-3a.md).
- **3b -- H6, Supabase Storage.** Moved word for word to [docs/designs/stage-3b.md](designs/stage-3b.md).
- **3b detailed design -- Q1-Q5 ANSWERED 2026-09-30; awaiting the founder's final sign-off. Nothing is built until then.** Moved word for word to [docs/designs/stage-3b.md](designs/stage-3b.md).
- **3b build -- IN PROGRESS 2026-09-30 (branch `phase55/stage3b-design`).** Moved word for word to [docs/designs/stage-3b.md](designs/stage-3b.md).
- **3c -- H5, the full part: the parse service.** Moved word for word to [docs/designs/stage-3c.md](designs/stage-3c.md).
- **3c detailed design -- APPROVED 2026-10-01; BUILDING.** Moved word for word to [docs/designs/stage-3c.md](designs/stage-3c.md).
- **3c test table.** Moved word for word to [docs/designs/stage-3c.md](designs/stage-3c.md).
- **3c build -- CI GREEN 2026-10-01 on `0be5422` (branch `phase55/stage3c-design`);** Moved word for word to [docs/designs/stage-3c.md](designs/stage-3c.md).
- **3d -- H4, per-tenant fairness.** Moved word for word to [docs/designs/stage-3d.md](designs/stage-3d.md).
- **3d also: when the model provider is down, documents wait instead of failing.** Moved word for word to [docs/designs/stage-3d.md](designs/stage-3d.md).
- **3d detailed design -- PROPOSED 2026-10-01; nothing is built until the founder approves.** Moved word for word to [docs/designs/stage-3d.md](designs/stage-3d.md).
- **3d -- APPROVED WITH CHANGES (founder, 2026-10-01); building.** Moved word for word to [docs/designs/stage-3d.md](designs/stage-3d.md).
- **3d build -- BUILT 2026-10-01 (D-184; migration `0035` awaiting the founder's backup and staging apply).** Moved word for word to [docs/designs/stage-3d.md](designs/stage-3d.md).
- **3d on staging, and the founder's decisions on the build (2026-10-01).** Moved word for word to [docs/designs/stage-3d.md](designs/stage-3d.md).

The list that stood inside "3d on staging" stays here, unchanged (RUNBOOK 9.2 carries the same
list):

**Gates for the first worker deploy (after 3e)** (founder, 2026-10-01;
RUNBOOK 9.2 carries the same list):
- G, and A4 against the real API and worker (from 3c);
- **a fresh `PARSE_SERVICE_TOKEN`**, generated by the founder and set on the
  parse app and the worker together (agreed in 3c);
- the 500 + 1 staging run, its budget to the founder first;
- the worker memory measurement and the founder's choice (RUNBOOK 9.2);
- the restart record, built and tested (3e; threshold **approved**:
  `worker_restarting`, high, 3 or more starts in 60 minutes, at most hourly,
  raised by the launcher);
- **the external uptime monitor on `/healthz`**, alerting when the endpoint
  doesn't answer or the heartbeat is stale. It moves from Phase 6 to here:
  a worker that is down, or crash-looping because it can't reach the
  database, raises nothing itself. `dispatcher_stopped` and
  `worker_restarting` both need a running worker that reaches the database.
  **Better Stack Uptime's free plan, approved by the founder** (10 monitors,
  checks every 3 minutes at the fastest on the free plan, since 30 s is
  paid; HTTP keyword checks; e-mail/Slack). UptimeRobot's free plan is out:
  it is for "hobby and non-profit projects" and DocFlow is commercial.
  **Pass mark when tested by stopping the worker:** the e-mail within **14
  minutes + the confirmation period**. That is up to 10:00 for `/healthz` to
  read stale, up to 3:00 to the next check, the confirmation period (set
  explicitly; Better Stack's docs state no default), and about 1:00 for
  e-mail. Worked out in RUNBOOK 9.4, which also gives the recovery pass
  mark. *(Revised by the founder the same day: confirmation period 120 s,
  not immediate. A single dropped request must not page. Request timeout
  30 s, to cover a cold start of the auto-stopped API. Pass mark **17
  minutes**: 10 to stale, 3 to the first failing check, and up to 3 for the
  confirming check, since Better Stack's docs don't say whether it
  re-checks inside the confirmation period; plus 1 for e-mail.)*
- **Staging API is private-only; Better Stack can't poll `/healthz`.
  Monitor approach to be decided in 3e** (founder, 2026-10-01). Found while
  checking auto-stop: `apps/api/fly.toml` gives the API no public IP (3c,
  N1/N2).
- **the two two-at-once sweep tests** (founder, at the 3d merge): the
  scheduled-jobs sweep and the stuck sweep, each run twice at once against
  the real database, built in 3e and passing (RUNBOOK 9.3);
- **the logins cutover done and verified** (3e; RUNBOOK 10.2), and the
  external monitor is now **the Healthchecks.io heartbeat** (3e, Q1), set
  up and tested to its pass marks (RUNBOOK 9.4).

**All of these gates are met** (2026-10-06 to 2026-10-08; D-194, D-196;
CHECKPOINTS.md, Stage 3, "The first worker deploy"; the files are in
`docs/spikes/first-worker-deploy-staging/`). The list above is kept as it
was agreed.

**The plan and the budget for that deploy** (founder, 2026-10-05; D-193).
The procedure, who does each step and the four STOP points are RUNBOOK 9.7.
The list above is unchanged. Decided with it:
- **Settings:** each app's list is in RUNBOOK 9.7. Beyond 3c's list: the
  worker's `HEARTBEAT_URL` and `FOUNDER_ALERT_EMAIL`; the API's
  `ADMIN_DATABASE_URL`, `STRIPE_DATABASE_URL` and `CONSOLE_MFA_ENFORCED`.
  Left off: `SUPABASE_JWT_SECRET` (not needed) and `EMAIL_PROVIDER_API_KEY`
  (no e-mail sender is built yet).
- **The real model reads all 501 documents** of the 500 + 1 run.
- **The Fly worker and beat are stopped between sessions**, the
  Healthchecks.io check paused while they are.
- **The 500 come from a Scale-tier test tenant.**
- **A4 on the worker** aims at Fly's SSH port (22) on its private address.
  If the control cannot reach it, the worker is recorded as not tested,
  never as passed, and the API half must pass in full.
- **The restart drill** passes on the `worker_restarting` alert row and its
  held e-mail, recorded as "raised and held, not delivered".

The budget, as estimated before any measurement (Fly's prices as read on
2026-09-30; the model's cost per document from the Stage 2 checkpoint):

| Item | Basis | Expected |
|---|---|---|
| Model: G and the memory documents | About 10 documents, some heavy; the build prompt's top figure is $0.35 each | up to $3.50 |
| Model: the 500 + 1 run | 501 short text orders at $0.0170 each (measured $0.0141 to $0.0191) | $8.52 ($7.06 to $9.57) |
| Fly machines | Parse $11.39, worker $5.70, API $3.19 a month if left running, beat about $1.94: about $0.031 an hour together | $1.50 for 48 hours running |
| Redis (Upstash) | $0.20 per 100,000 commands; not known until G3 | $0 to $7 for 48 hours, or the fixed $10 plan |
| Fly builds, storage, network | Three image builds | under $1 |
| **Total** | | **about $15 to $22** |

- **Time:** 3 to 6 hours for the run at an assumed 20 to 40 seconds a
  document, until G1 measures it; one to two working days for the deploy.
- **Limits:** the Anthropic account's prepaid balance with auto-reload off
  is the hard limit on model spend ($40.97 on 2026-10-05, as the founder
  set it). Claude stops the worker and reports at $40 of total spend.
- **Before the run** the budget is redone with G1's and G3's measured
  figures, and the founder signs off that number (RUNBOOK 9.7, STOP 4).

**3d MERGED 2026-10-02 03:18 UTC (PR #33, main `4a2b907`).** At the merge
the founder decided the open pre-merge item (question 2 above): **the two
two-at-once sweep tests** (the scheduled-jobs sweep and the stuck sweep,
each run twice at once against the real database) **are built in 3e**, not
3d and not Stage 5, and they gate the first worker deploy (the list below,
RUNBOOK 9.2 and 9.3, and "3e -- F-1"). **`backup_0035`** is dropped by the
founder not before **2026-10-05 03:18 UTC**, after Claude reports the check
(RUNBOOK 1.3).

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

**Moved, as above:**

- **Card billing with a 7-day trial -- required before the first pilot.** Moved word for word to [docs/designs/card-billing.md](designs/card-billing.md).
- **3e -- F-1, separate database logins.** Moved word for word to [docs/designs/stage-3e.md](designs/stage-3e.md).

### 3e detailed design -- PROPOSED (2026-10-02); nothing built until the founder approves

Moved word for word to [docs/designs/stage-3e.md](designs/stage-3e.md) (2026-10-05).

- **3e -- APPROVED WITH CONDITIONS (founder, 2026-10-02).** Moved word for word to [docs/designs/stage-3e.md](designs/stage-3e.md).

#### 3e build -- as built (2026-10-02), branch `phase55/stage3e-design`

Moved word for word to [docs/designs/stage-3e.md](designs/stage-3e.md) (2026-10-05).

**3e MERGED 2026-10-02 17:43 UTC (PR #35, `e99fbb9`, head `f734435`).**
Then the CI runner pin, PR #36, was updated from that main, so its own run
tested 3e on `ubuntu-24.04` with the Node 24 actions before either reached
main together (founder's order); merged 17:48 UTC, main `d004186`. **Main's
push run on `d004186`** (run 37043224237), from each job's counts notice:
core `745 tests, 0 failed, 0 skipped, 0 unapproved`; api `626`; worker
`180`; parse unit `128` and HTTP `56` (all 0 failed, 0 skipped, 0
unapproved); web 73 passed; web-live 3 passed. The same counts as 3e's PR
runs. One warning, the known IPv6 control (Known open items).

`0036` is still not on staging: RUNBOOK 10.2 step 0, after the
`backup_0035` check (on or after 2026-10-05 03:18 UTC) and the founder's
confirmation. Dates unchanged: `backup_0034` check on or after 2026-10-04
20:53 UTC, `backup_0035` on or after 2026-10-05 03:18 UTC (RUNBOOK 1.3).

**Decided (founder, 2026-10-02, PR #37): the two checks run the staging
suites at `f191e27`, not at `main`.** Main now holds 3e, whose suites
connect as the four logins and test `0036`'s functions and `worker_starts`
-- none of which exist on staging until the cutover, and the cutover waits
for the `backup_0035` check, so main's suites can't pass there before it.
`f191e27` is the last main before 3e and differs from 3d's merge
(`4a2b907`) only in `RUNBOOK.md` and this file: exactly the code staging's
`0035` schema expects. **Condition:** the suites run in a separate
worktree at `f191e27`, connecting as `docflow_app`, never by moving the
main checkout back, and the report names the commit and the login beside
the counts. Main's suites run on staging first at RUNBOOK 10.2 step 5.
RUNBOOK 1.3 ("Which code the checks run") and 10.2 step 5 say so.

**The two backup checks, run 2026-10-05** (one run for both; detail in
CHECKPOINTS.md, Stage 3 draft). At `f191e27`, in a worktree, as
`docflow_app`:
- staging confirmed at `0035` from the database (none of `0036`'s roles,
  table or functions there);
- core `746 passed, 1 skipped in 53.58s`; worker `161 passed, 6 skipped in
  1232.50s (0:20:32)`; API `586 passed, 1 skipped, 3 deselected, 657
  warnings in 2561.04s (0:42:41)`, the count RUNBOOK 1.4 expects;
- no alert of any type since either merge, **which carries no weight**
  (founder): nothing processed documents on staging in either window;
- **decided (founder, 2026-10-05): `backup_0034`, `backup_0035` and
  `backup_3b` are kept until the first worker deploy has processed real
  documents on staging.** 0.20 MB, 0.24 MB (0.44 MB together) and 0.16
  MB; the database is 23.4 MB of the free plan's 500 MB;
- **the worker suite's 20:32 is its normal time:** the same suite on the
  same code took 20:43 on staging on 2026-10-01 ("3d on staging",
  above). "About 12 minutes" was Claude's outdated figure from Stage
  3a. The parse service's log shows 39.3 s of parse work in the whole
  run. Evidence in the checkpoint draft; RUNBOOK 1.4 now gives the
  suite's count and time;
- **departure, now written into RUNBOOK 1.3:** the worktree has no
  `apps/parse/.venv`, so the dev parse service was started by hand from the
  worktree's code, with the import check extended to `parse_service`.

**For Stage 4 (founder, 2026-10-05), in the checkpoint draft:** the live
golden run's cost as mean, median and maximum (a gap until the run), and
an estimate of 2.5 million catalog rows with both trigram indexes (about
1.5 to 3 GB: not on Nano's 500 MB; on disk on Micro, speed unmeasured).
**Both questions raised there are decided (founder, 2026-10-05; D-186;
Stage 4 design, "Decided (founder, 2026-10-05)"):** staging stays on
Nano; D-155's 2.5 million row CI seed becomes one manual CI job, run
before the staging run; the staging benchmark guards the database's size
at 350 MB and removes its rows at the end of the run on a typed OK.

**The web dependency audit's one exception (founder, 2026-10-05; D-187):**
PR #38's web job failed on a new high advisory against `braces`
(GHSA-vfj7-8cjw-p6xm; dev-only, no patched version). A CI-only PR (branch
`ci/web-audit-exception`) makes the audit pass exactly that advisory until
2026-11-05 and fail on any other. Its own note is under "Known open
items", added by that PR.

**The logins cutover (RUNBOOK 10.2), started 2026-10-05** on the founder's
go, given for after PRs #38, #39 and #40 had merged (main `e2f53c3`).
- **Step 1, passed (15:52 UTC):** staging's snapshot, taken read-only as
  `docflow_app`, is identical to `supabase/reverse/0036_pre_snapshot.json`:
  80 policies, 13 functions, SHA-256 `11f4ebefb1ce...`. Staging at `0035`;
  `founder_alerts` at 15 rows, the newest from 2026-09-30.
- **Stopped before step 2, on a gap in the RUNBOOK (D-188).** Step 5
  compares a second snapshot with "CI's forward state", and CI kept that
  state nowhere: `migration_roundtrip.py` held it in memory. `0036` was
  not applied. **Founder: commit the forward state.**
- **Built (CI-only PR, branch `ci/0036-post-snapshot`):**
  `supabase/reverse/0036_post_snapshot.json` (80 policies, 16 functions,
  SHA-256 `81bd33599151...`), taken from CI's own run by the same snapshot
  code; the round trip now fails unless its forward state equals that
  file and its reversed state equals the pre file, and prints both
  hashes on every run; RUNBOOK 10.2 step 5 compares by SHA-256, and
  section 1 has the pattern for later migrations that change policies or
  grants.
- **The file is from CI, so it was checked independently** against
  `0036`'s own statements: 51 policies changed, exactly the 51 named by
  its `ALTER POLICY ... TO` statements (35 to `docflow_admin`, 10 to
  `docflow_worker`, 5 to `docflow_api`, 1 to `docflow_stripe`; all were
  `public`); 29 unchanged; one expression changed (`dispatcher_raise`
  gains `worker_restarting`); none added or removed. Functions 13 to 16:
  3 added, 13 re-granted from `docflow_app`, equal to `FUNCTION_GRANTS`;
  `docflow_app` holds nothing. `test_policy_snapshots.py` keeps that
  check in CI.
- **Next:** this PR merges, then steps 2 to 4 (the founder), then step 5.
- **Steps 2 to 4, done by the founder (2026-10-05, about 16:50 UTC):**
  `0036` applied, the four logins turned on, the five URLs in the root
  `.env`. Staging is at `0036`.
- **Step 5, so far (D-189).** The second snapshot's SHA-256 is
  `81bd33599151...`, equal to the committed post file and to CI; each login
  connects as itself; no new founder alert. Suites on `9fd519a`: core
  `796 passed, 1 skipped`; worker `2 failed, 172 passed, 6 skipped`; API
  `1 failed, 624 passed, 1 skipped, 3 deselected`, then
  `625 passed, 1 skipped, 3 deselected` on a full re-run (the one failure
  was an upload to Supabase Storage timing out; it did not recur).
- **The two worker failures were the tests, not `0036`** (evidence in
  D-189): the launcher tests cleared the other logins from the environment
  but not from the root `.env`, which holds all four since step 4. The same
  evidence showed the suite committing six rows to `worker_starts` every
  run. **Fixed on branch `tests/worker-env-file-and-starts-rollback`:**
  the launcher tests read no `.env`; every recorded start is rolled back
  and the suite checks it left none; one committed start runs in CI only.
  The worker suite on that branch, on staging: `184 passed, 7 skipped in 1246.85s (0:20:46)`,
  `worker_starts` 0 before and 0 after.
- **PR #42 merged (2026-10-05, `main` `6cac1bd`).** CI on its commit: all
  six jobs green, and the worker job shows the committed-start test "ran
  and passed". `main`'s own run on `6cac1bd`: all six green (core and web
  on a re-run, after GitHub cancelled them unstarted).
- **Step 5, final, on `6cac1bd`:** worker `184 passed, 7 skipped in
  1276.50s (0:21:16)`, `worker_starts` 0 before and 0 after; core `805
  passed, 1 skipped`; the snapshot re-taken at 21:27 UTC, the same hash;
  the same 15 founder alerts. The API suite's last full run is the one on
  `9fd519a` (`625 passed, 1 skipped, 3 deselected`); PR #42 changed no
  application code, API test or migration.
- **Step 6, done.** Every place the old URL lived is listed in
  CHECKPOINTS.md (Stage 3, "The cutover"). One was not on the RUNBOOK's
  list: a copy of the whole `.env` from 2026-09-28 in a folder beside the
  repository, deleted by the founder. **`ALTER ROLE docflow_app NOLOGIN`
  at about 21:32 UTC.** The old URL kept connecting through one session
  the pooler still held, opened by Claude's check a minute earlier;
  `NOLOGIN` does not close open sessions. The founder ended it; **refused
  at 21:35:02 UTC, three of three**; then the old URL came out of the
  root `.env`. RUNBOOK 10.2 step 6 now says to end the login's sessions
  after `NOLOGIN`, and never to try the old URL before it.
- **Not done:** step 7. `docflow_app` is dropped only after the first
  worker deploy has processed real documents on staging (founder,
  2026-10-05; D-190, the backups' trigger). **The first worker deploy
  waits for the founder's go**: the cutover turned up several things that
  were not expected, each listed in CHECKPOINTS.md.
- **The one Storage timeout in the API suite is recorded as transient**
  (D-190): once in 1,252 API tests across two runs, not reproduced.
  Supabase had an incident open across the run, "Intermittent latency in
  Eastern US". A real email that meets a 503 is sent again by Postmark,
  10 times over about 10 hours 20 minutes (RUNBOOK 7.3).
- **The project's own Storage logs now back that up** (two exports by
  the founder, read 2026-10-05; D-192; CHECKPOINTS.md, "The cutover").
  Storage received the failed test's upload three times, 30 seconds
  apart, and logged each as `ABORTED RES`; it logged queue errors of its
  own from 17:30 to 17:36 UTC and none outside those minutes; the next
  test's upload was aborted once and passed on the retry; no 5xx row in
  524 requests. **One thing found on the way:** the timed-out upload
  still landed half a minute later, so one file sits in the bucket with
  no row pointing at it. Recorded, not changed; the founder's to decide
  whether more is wanted.

### Stage 4 detailed design -- APPROVED WITH CHANGES (founder, 2026-10-02; three more changes 2026-10-05); nothing built before the Stage 3 checkpoint's "go"

Branch `phase55/stage3-checkpoint-stage4-design`, from `main` `b90b1c4`,
docs only. Scope, from the Stage 4 row: **A** matching speed (review H4's
second half, D-152), and **B** the audit of the broad `except` blocks on the
document path (founder, 2026-09-29). Building waits for the Stage 3
checkpoint's "go" (CHECKPOINTS.md, draft). **The founder's answers to
Q1-Q12 are at the end ("Decided"), with three new questions (Q13-Q15)
and the DOC-030 fact Q12 asked for.** Where an answer changes the text
above it, the text is changed and marked *(decided)*.

#### A. Matching speed

**A1. Today.** For a line with no learned rule and no exact SKU, step 3
(`matching.score_candidates`) scores **every** live catalog item in Python:
`description_similarity` (rapidfuzz `token_sort_ratio` on
`normalize_description` keys) and `sku_similarity` (`ratio` on
`sku_comparison_key`), keeps the better, applies the measure guard, sorts,
keeps 5. The review measured **1.74 s per unmatched line** at 50,000 items,
so a 40-line order spends about 70 s matching, inside the transaction that
holds the document. **No command or script was kept for that figure**, and
D-152 says every performance claim carries its command, dataset size and
p50/p95. So the first thing built is the benchmark (A5), and today's code is
re-measured with it as the baseline; 1.74 s is not reused.

**A2. The change: Postgres picks the candidates, Python still scores them.**
- **Steps 1 and 2 don't change.** The learned rule and the exact SKU are
  read from `load_catalog`'s one query per document, as today. The
  benchmark measures `load_catalog` at 50k separately; if it is itself too
  slow, that is a question (Q4), not a quiet change.
- **Step 3 asks Postgres for at most K items per line**, nearest first by
  trigram distance, in two lists:
  - on `lower(description)` against the line's description;
  - on the SKU with everything but letters and digits removed, lowercased
    (`lower(regexp_replace(sku, '[^A-Za-z0-9]', '', 'g'))`), against the
    line's SKU, built the same way.

  The union (at most 2K items, normally far fewer) is then scored exactly as
  today: the same two scorers on the same Python keys, the same measure
  guard, the same threshold (0.90), the same ambiguity margin (0.02), the
  same 5 recorded candidates and the same sort. **The SQL expressions only
  choose which items get scored. They never produce a score, and nothing
  stored or shown comes from them.** No new columns and no backfill: the
  rows don't change.
- **Proposed K = 50 per list** (the review's figure), so at most 100 items
  scored per line instead of 50,000. K is a named constant, and the parity
  run (A3) decides whether 50 holds.
- **The query** runs in the document's own tenant session (as
  `docflow_worker`, the only login that matches; the API never calls
  matching). It filters `tenant_id = :tenant AND deleted_at IS NULL`, as
  `load_catalog` does, and RLS applies as for any tenant read.
- **The index (Q2, decided: GiST with `btree_gist`):** `(tenant_id, <expression>
  gist_trgm_ops)`, partial on `deleted_at IS NULL`, one per expression. GiST
  supports `ORDER BY expr <-> :q LIMIT K` as an index scan inside the
  tenant, so the K nearest come back in order with no similarity cut-off;
  the `tenant_id` column needs `btree_gist`. The alternative is GIN with the
  `%` operator, which returns only items above `pg_trgm.similarity_threshold`
  (0.3 by default) and can return fewer than K. That threshold is a second
  cut-off that the parity check would have to cover too.

**A3. The integrity risk, and how it is proven.** Top-K can change an
outcome in two ways:
1. **The winner is outside the K.** The line stays unmatched, or a
   lower-scoring item wins.
2. **The runner-up is outside the K.** This is the worse one. The
   ambiguity guard refuses a fuzzy match when the second-best item is
   within 0.02 of the best, which is how two near-identical catalog items
   (7.15.2 Step 4 expects them) are kept from being picked by sort order. If
   top-K drops the sibling, the guard never sees it, and **a fuzzy match is
   applied that today's full scan would refuse**: a wrong SKU pre-filled, at
   0.90 or above, that a reviewer may approve.

So the pass mark is **parity**. For every line in the benchmark's corpus,
the top-K path returns exactly what the full scan returns: the same matched
item (or none), method, score and provenance, and the same 5 recorded
candidates with the same scores, `eligible` and `blocked_reason`. **Zero
differences.** Any difference goes to the founder with the lines that
differ, before K changes (Q3). Parity is checked:
- in CI on every push: a generated catalog of a few thousand items with
  the corpus's hard cases, both paths in the same test;
- at 50k in the benchmark run (A5).

**What parity can't prove.** A corpus shows that these lines match the
same. It can't show that no line ever could differ. The two orderings
(trigram distance, token-sort ratio) are related but not the same. Proposed
**cut-off guard (Q3):** if the K-th item in a list is still similar
(trigram similarity at or above a named constant, say 0.5), the list was
cut while items were still close. Fetch 4K for that line, and if the
4K-th is still above it, score the whole catalog for that line, as today.
That way a sibling the guard needs can only be missed when its trigram
similarity is low, and the parity run measures how often the fallback
fires. Without the guard, parity on the corpus is the whole proof.
*(Decided, Q3: parity with zero differences **and** the cut-off guard.
**In production too**, each document's matching logs how many lines took
the fallback (counts and IDs only, 7.10), so a K that is too small shows in
the numbers, not as slow orders.)*

**A4. What doesn't change.** The thresholds, the scorers and their keys,
the precedence (learned rule, exact SKU, fuzzy), the provenance strings,
the candidate JSON, "never auto-apply below the threshold" and "never
normalize a unit". No learned rule or catalog row from another tenant can
be a candidate. `score_candidates`' full scan stays in the code, as the
fallback (if Q3 is yes) and as the parity reference in the tests.

**A5. The benchmark** (`scripts/bench/matching_bench.py`, new):
- **Data, all fake and generated from a fixed seed:** "Acme Test" product
  families, each with size, pack and flavour variants, and some duplicate
  descriptions with different SKUs (7.15.2 Step 4's case). It also
  includes the D-066 hard pairs ("5lb" against "2lb", a misspelling against
  a different size).
  - **50,000 live items** for the tenant under test.
  - **Four more "Acme Test Bench" tenants of 50,000 each**, so the tenant
    filter and the index are measured as on a shared database (250,000
    rows in all).
  - **2,000 generated lines** (Q5, decided): exact SKUs, SKU typos,
    misspellings, reordered and abbreviated descriptions, changed
    measures, lines with no SKU, and lines matching nothing;
  - **plus the golden set's real lines and its catalog** (Q5, decided),
    so some inputs weren't made up by the generator. Found when checking:
    the golden fixture is **4 lines** (`apps/api/tests/fixtures/golden/`),
    and the catalog that matches them is the 4-item fake fixture
    `apps/parse/tests/fixtures/tables/catalog.csv`. Both go in, but 4
    lines can't balance 2,000 generated ones (Q13).
- **Measured and printed:**
  - `load_catalog` at 50k;
  - per unmatched line: p50, p95 and max, for the full scan (today) and
    for top-K;
  - per 40-line order (the review's example): p50 and p95;
  - the parity counts and how often the fallback fired (if Q3 is yes);
  - the command, git commit, dataset sizes and seed, database, Postgres
    version and the run's time (D-152).
- **Pass marks (Q6, decided):** parity zero differences; p95 at or under
  100 ms per unmatched line at 50k, and a 40-line order's matching at or
  under 4 s p95, on the database of record. **Lines that take the
  fallback count in the p95**: that is what a customer would feel.

**A6. Where the 50k run happens (founder's condition: not on staging during
`docflow_app`'s 3 clean days, or a separate database named).** The 3 clean
days start at the cutover's NOLOGIN (RUNBOOK 10.2 step 6, not before the
`backup_0035` check on 2026-10-05). `docflow_app` is dropped at step 7, so
**about 2026-10-08 at the earliest.** The options (Q7):
- **(a) Staging, after step 7.** Nano, the same hardware the pilot will
  meet. 250,000 test rows go into `items` under five "Acme Test Bench"
  tenants, removed afterwards only on your per-action OK, like the other
  test tenants.
- **(b) A separate Supabase project** (`docflow-bench`, Postgres 17, Nano).
  It never touches staging and is deleted afterwards. Its cost depends on
  the organisation's plan; you would read it on the billing page before
  creating it.
- **(c) The CI runner's local Supabase stack**, in a manual
  (`workflow_dispatch`) job. Free, repeatable and away from staging, but on
  GitHub's runner hardware, so its times say nothing about Nano. Parity is
  the same anywhere.
- **Decided (Q7): parity in (c); the timing of record in (a), on staging
  after about 2026-10-08.** Nano is the hardware that matters, and (b)
  costs money to prove the same thing. Conditions: **the bench tenants are
  removed afterwards through the per-action-OK sweep**, and **the run holds
  the test lock** (D-180, RUNBOOK 1.4), so nothing else runs on staging
  at the same time. The benchmark script takes the same advisory lock the
  API and worker suites take, and refuses to start if it's held. **Nothing
  in this stage runs on staging before `docflow_app` is dropped.**
  *(Changed 2026-10-05, items 16-18 below: a manual 2.5 million row CI
  job runs first; the staging run guards the database's size; the bench
  rows are removed at the end of the run on a typed OK, not by the
  sweep.)*

**A7. Migration `0037_trigram_matching.sql` -- after `0036`.** It is
numbered after `0036`, and it isn't applied to staging until `0036`'s
cutover is verified and `docflow_app` is dropped (step 7), so no index
change lands inside the 3 clean days (Q8):
- `create extension if not exists pg_trgm with schema extensions;` and the
  same for `btree_gist` (if Q2 is GiST);
- the two partial indexes on `items`;
- `documents.wait_cause` gains a fourth value for a database wait (Q14,
  decided; the constraint only, no rows);
- `grant usage on schema extensions to docflow_worker`, **if** the build
  finds the login can't reach the operator class and the `<->` operator
  there. Nothing in `0001`-`0036` grants on `extensions` today, and
  Supabase grants it to its own roles, not to ours. The CI test runs the
  query as `docflow_worker`, so this is found in CI, not on staging.
- **No rows change.** Proposed (Q9): no backup schema (RUNBOOK 1.1's rule
  is about migrations that change data), but a reverse script and the CI
  round trip as for `0036`, with a snapshot of `items`' indexes before and
  after.
- Index build time at 250,000 rows is measured in the benchmark run. On
  staging today `items` is small, so the build takes moments there.

**A8. Tests (real database where the thing tested is the database):**
- parity, both paths on the same generated catalog (worker suite, real
  database, CI);
- **tenant isolation through the new query** (7.5's required test, "Tenant
  B's catalog is never a match candidate for Tenant A's lines"): Tenant B
  holds an item identical to Tenant A's line, and it is never among A's
  candidates, run as `docflow_worker`;
- a retired item is never a candidate;
- the D-066 hard pairs give the same outcome on both paths;
- if Q3 is yes, a catalog built so the K-th item is still close makes the
  fallback fire, and the outcome equals the full scan;
- the query plan uses the index, checked in the 50k benchmark (`EXPLAIN`),
  not in CI, where small tables make the plan unstable.

#### B. The broad-`except` audit

**B1. The count is 34, not ~23.** Blocks that catch `Exception` (or
everything) in our own code on the document path, from upload or email to
review, counted 2026-10-02 on `b90b1c4` (library code in the venvs
excluded): `parse_and_extract.py` 13, `conversion.py` 8, `extraction.py` 3,
`model_provider.py` 3, `email_intake.py` 2, `documents.py` 2, `tables.py`,
`file_types.py` and `job.py` 1 each. They fall into five kinds:

**Kind 1 -- any exception becomes "your file is the problem" (8 blocks; the
audit's target).**

| Where | Today | The risk |
|---|---|---|
| `apps/parse/parse_service/parsing/documents.py:313` | any failure while preparing the file -> DOC-005 "This file appears to be corrupted" | A bug in our own code is blamed on the customer's file. Two of ours sit behind it: the in-house RTF reader (`_extract_rtf_text`) and `UnhandledFileTypeError` (a file type the allowlist accepts but no handler covers, which is our gap, not their file) |
| `conversion.py:152, 243, 278, 285, 491, 556` | any failure opening an image, `.xls`, OpenDocument (2), `.msg`, `.eml` -> DOC-017 "We couldn't convert this older file" | The same; DOC-017's next step tells the customer to re-save as PDF |
| `apps/parse/parse_service/parsing/tables.py:156` | any failure reading a catalog or buyer spreadsheet -> IMP-004 (founder audience) | The founder is told the prospect's file is unreadable when it may be our reader |

**Kind 2 -- content dropped with no record (2 blocks).**

| Where | Today | The risk |
|---|---|---|
| `conversion.py:570` | an `.eml` body that can't be decoded becomes empty | The order may be in the body, and nothing says it was dropped |
| `conversion.py:584` | an `.eml` attachment that can't be decoded is skipped | **A forwarded PO can vanish silently.** The email is "read", and the attachment that was the order isn't |

**Kind 3 -- our step failed, said honestly (5 blocks, plus 2 narrow ones).**
`parse_and_extract.py:786` (saving the answer -> DOC-021),
`:942`, `:961`, `:977` (buyer identification, matching, duplicate
detection -> VAL-016 on the document plus an alert), and `:1041` (validation
-> DOC-021 plus an alert). DOC-021 and VAL-016 already say "DocFlow has
already been alerted" and don't blame the file. Two things remain:
- **Telling our bug from a passing fault.** A dropped database connection
  and a `TypeError` in our code both become DOC-021 today. Proposed (Q10):
  the founder alert carries the exception type and our code location
  (module and line, never the message, which can carry customer data).
  And a database connection error (`OperationalError`, `InterfaceError`)
  during these steps waits and retries, as 3d does for Storage, instead
  of failing the document. That second part changes behaviour, hence the
  question.
- `file_types.py:473` (a zip member that can't be read -> DOC-005) and
  `email_intake.py:172` (bad base64 -> a malformed-payload answer to
  Postmark) are in kind 1's spirit but narrow already (one library call
  each). The audit narrows them to the exceptions those calls raise.

**Kind 4 -- best-effort side effects: log, carry on (16 blocks).** The
preview (`parse_and_extract.py:158`, `documents.py:285`), the extracted
text store (`:189`), example planning (`:216`, falls back to no examples),
the extraction run row (`:288`), founder alerts that couldn't be raised
(`:377`, `:495`, `email_intake.py:427`), the dispatch nudge (`:587`), the
review digest (`:1026`), token counts and the started row
(`extraction.py:335, 347, 366`), and the provider's state
(`model_provider.py:134, 173, 200`). Broad is right here: none of these may
fail the document. The audit checks that each logs the exception type and
that nothing lost is a data-integrity loss. One is: **`:288`, a run row not
recorded, loses a cost record** (7.9: "persist it"). Proposed (Q11): it
raises a founder alert, as a failed `started` row should.

**Kind 5 -- fail closed (1 block).** `apps/parse/parse_service/job.py:73`:
any failure while hardening the job means the file is never read. Broad is
the point. Unchanged.

**B2. What "expected" means for kind 1, and how it's found.** For each
block, the exception types that its library raises on a malformed file.
These come from the library's source at the pinned version, and from
running every fixture we hold through each parser inside the sandbox in CI:
the 7.11 hostile set, the Tier 1 and 2 positive set, and **a mutation
corpus** (each fixture truncated at several points and with bytes flipped,
generated from a fixed seed). Every exception type seen is recorded.
- **Expected:** the catalog code it has today (DOC-005, DOC-017, IMP-004).
- **Anything else is ours:** the job answers `error`, not `rejected`. The
  document fails with a **new tenant-audience entry (proposed DOC-030,
  wording yours, Q12)** that says the problem is on DocFlow's side and the
  founder has been alerted, and never tells the customer to re-save or
  re-send. A high founder alert carries the exception type and our code
  location. The in-house RTF reader has no error type of its own today
  (it decodes with `errors="replace"` and walks the text); proposed: any
  malformed-input case it refuses raises a new `RtfParseError`, so anything
  else from it is a bug. `UnhandledFileTypeError` ("No Tier 1
  content-block builder for ...") is always ours.
- **Kind 2:** never silent. An undecodable body or attachment gets a
  warning on the document naming what was lost ("an attachment called ...
  couldn't be read"). The order is still read from what remains, and the
  reviewer sees the gap. A new catalog warning, wording yours (Q12).
- **The direction of the remaining risk is safe.** A hostile file that
  raises an exception type nobody saw before reads as "our side" and pages
  the founder; it is never blamed on the customer. The mutation corpus
  makes that rare.

**B3. Tests.**
- For each kind-1 block: an expected exception still gives its catalog
  code; an unexpected one (forced in our own code) gives DOC-030 and the
  alert, never DOC-005, DOC-017 or IMP-004.
- VAL-017 with an 80-character sanitized filename (the label's limit):
  the review screen's layout holds where the message renders (founder,
  2026-10-02).
- For kind 2: a `.eml` with an undecodable attachment and a good one gives
  the warning naming the lost one, and the good one is read.
- **A guard test lists every broad `except` on the document path with its
  kind**, and fails when one is added without a kind, as 3b's
  stored-path guard does.
- The parse service's own tests run inside the sandbox in CI, where the
  parsers really run.

#### C. Order, and what touches staging *(decided, Q1: one stage, two PRs, B first)*

A and B share nothing, and bundling them would hold B to A's schedule and
put a parse-sandbox change and an index migration in one review.
1. **PR 1, part B (the audit).** Parse-service and worker code, catalog
   wording settled first (Q12). It merges once its CI is green, never
   before the Stage 3 checkpoint's "go", and its parse-service changes
   reach the Fly parse app only after Stage 3's G and A4 have passed
   (CHECKPOINTS.md, Stage 3 draft). **Migration-free** (Q14, decided:
   the database retry is PR 2's). Also in PR 1: DOC-030, VAL-017 and
   VAL-018 once their wording is settled, and the RUNBOOK step for a
   DOC-030 (below).
2. **PR 2, part A (matching), plus kind 3's database retry** (Q14).
   CI green, parity included. After
   `docflow_app` is dropped (about 2026-10-08): `0037` on staging (no
   backup, Q9), the staging suites as the four logins, then the 50k timing
   run on staging under the test lock. The PR carries the benchmark output
   before and after, in the form D-152 asks for.

#### Decided (founder, 2026-10-02)

1. **One stage, two PRs, B first** (C above).
2. **GiST with `btree_gist`**: exactly the K nearest, no second cut-off to
   prove.
3. **Parity with zero differences, and the cut-off guard**, plus the
   fallback count logged per document in production.
4. **`load_catalog` at 50k: bring the number back first.** The exact-SKU
   lookup doesn't move on speculation.
5. **2,000 generated lines plus the golden set's real lines and catalog.**
6. **The pass marks as proposed**; fallback lines count in the p95.
7. **Parity in CI; timing on staging after about 2026-10-08**, bench
   tenants removed through the per-action-OK sweep, the run holding the
   test lock.
8. **`0037` on staging only after `docflow_app` is dropped.**
9. **No backup schema for `0037`; a reverse script and the CI round trip**,
   as for `0036`.
10. **Kind 3:**
    - The alert names the exception type and our code location (module
      and line, never the message).
    - **Connection errors are retried:** only `OperationalError` and
      `InterfaceError`, a bounded number of times (a named constant),
      then DOC-021 as today. Never indefinitely. **Never `IntegrityError`**:
      that's a real fault.
    - **The alert is deduped per exception type and code location per
      hour**, so one customer repeatedly sending an odd file doesn't page
      the founder each time. This applies to DOC-030's alert too.
11. **A run row that couldn't be recorded raises a founder alert**: a lost
    cost record is a data loss, not a side effect.
12. **Wording:** the founder drafts DOC-030 and the kind-2 warning, once
    the fact below is answered.

#### What happens to a DOC-030 document after the bug is fixed (the fact Q12 asked for)

Read from the code on `b90b1c4`:
- **Nothing retries it on its own.** `failed` is a final status: the
  state machine (`document_status.ALLOWED`) has no move out of it, and the
  database refuses one too (`0027`'s `documents_status_guard` trigger).
- **The Console can't retry it either.** No route or Console action
  re-runs a failed document. "Run extraction" on a test batch takes only
  `staged` documents (`onboarding.start_test_batch_run`).
- **The customer has to send it again.**
  - **Re-upload:** a new document, read normally. It is linked to the
    failed one as a possible duplicate (7.8: "never rejected: both
    documents exist and both process").
  - **Email:** a new message is read the same way, linked to the failed
    document. But a redelivery of the **same** message (the same
    Message-ID) is dropped as a duplicate (`email_intake.py`, the
    `raw_emails` Message-ID check). A customer can't make it count by
    resending an identical copy; a new email or a forward can.
  - **Nobody tells the customer the fix is in.** DocFlow has no "your
    document can be sent again" notice.

So today DOC-030 can honestly promise only this: DocFlow has been alerted,
and the customer can send the file again, but it will fail the same way
until the fix is deployed. That's what DOC-021 already says ("Upload the
same file again to have it read and checked again"). A "DocFlow will
re-read it once the fix is in" promise needs something that doesn't exist:
a founder-only re-run for a failed document (Q15).

#### New questions (from the answers) -- decided below

13. **A second non-generated source for the corpus.** The golden set is 4
    lines, which can't balance 2,000 generated ones. Staging holds lines
    that people typed into test orders, with the test tenants' catalogs,
    all fake. Add those approved lines and their catalogs, read once,
    read-only, into the bench data? They would be copied into bench
    tenants, never matched in place. Or are the 4 golden lines enough?
14. **Kind 3's retry needs a migration, so part B isn't migration-free.**
    `0035` limits `documents.wait_cause` to `model_provider`, `storage`
    and `parse_service`, so a database wait needs a fourth value, which is
    a check-constraint change. The options:
    - **(a)** B carries a small migration (the constraint only, no rows; a
      reverse script and the round trip as for `0036`). It then waits for
      `docflow_app`'s drop like `0037`, which removes the reason for
      putting B first.
    - **(b)** The constraint change goes into `0037`, and the
      connection-error retry is built in PR 2. PR 1 keeps everything else
      in B.
    - **(c)** The retry happens inside the task, without a status change.
      Celery re-sends the task after a delay, and the document stays
      `processing`. That avoids the migration, but the bounded retry would
      then sit beside the stuck sweep's 30-minute timeout and its tries,
      which 3a and 3d arranged carefully.

    Proposed: (b).
15. **A founder-only re-run for a failed document**, so DOC-030 (and
    DOC-021) could say "DocFlow will read it again once the problem is
    fixed"? It would be a new move out of `failed` in the state machine and
    the trigger, a Console action with its audit row, and a new
    migration. That's outside this design, and a scope question for you,
    not something to add quietly. Without it, the wording says the
    customer sends the file again.

#### Decided (founder, 2026-10-02): Q13-Q15

13. **Yes: hand-typed lines from staging go into the corpus**, because they
    are the "not made up by the generator" input Q5 asked for. Two
    conditions:
    - **Only from test tenants.** Claude lists the tenants and the lines it
      proposes to copy, and **the founder approves that list before
      anything is copied.** Never read from a customer tenant, now or when
      real customers exist.
    - **If the lines go into the repository as CI fixtures, the same list
      review covers that.** Anything committed is permanent.
14. **(b): the database retry goes in PR 2, with `0037`.** `0037` also
    adds the fourth `wait_cause` value, and the bounded retry on
    `OperationalError` and `InterfaceError` is built and tested in PR 2.
    PR 1 stays migration-free, which is the reason B goes first. (c) is
    out: a retry hidden inside a task, next to the stuck sweep's 30-minute
    timeout, is the interaction 3a and 3d were careful to avoid.
    **Until PR 2 lands, a dropped database connection during buyer
    identification, matching, duplicate detection, saving or validation
    still fails the document with DOC-021, as it does today.** PR 1 adds
    only the alert's exception type, code location and hourly dedupe to
    those blocks.
15. **No founder re-run in Stage 4.** It would reopen `failed` as a final
    status, which `0027`'s trigger enforces on purpose, and that needs its
    own design, not a rider on an audit. **During the pilot the founder
    covers the gap by hand:** a DOC-030 pages the founder, who emails the
    customer when the fix is deployed. **Moved to Phase 6**, next to the
    other "before real volume" items. If DOC-030s turn out to be common in
    the pilot, that is the evidence for building it.

#### Decided (founder, 2026-10-05): three changes to the approved design

From the database-size question at the backup checks (CHECKPOINTS.md,
Stage 3 draft, "For Stage 4"; D-186). Nothing is built; building still
waits for the Stage 3 checkpoint's "go".

16. **Staging stays on Nano for Stage 4. No upgrade.** Production's
    compute is a Phase 6 decision, made from measured numbers, not from
    the 1.5 to 3 GB estimate.
17. **D-155's item 1 is replaced.** Routine CI keeps this design's
    numbers: a few thousand items on every push, 50,000 in the manual
    parity job, 250,000 on staging. **Added: one manually triggered CI
    job that seeds 2.5 million catalog rows with the trigram indexes and
    prints the table size, the index sizes and the query timings.** It
    **runs before the staging run**, so the staging run's size comes from
    a measurement. Its timings are GitHub's runner, not Nano (A6 (c)
    already says so); its sizes are what it is for.
18. **The staging benchmark guards the database's size.** Nano turns
    read-only past 500 MB, and that would stop all of staging, not just
    the benchmark. The script:
    - reads the database size before seeding;
    - **refuses to start if the current size plus the projected size of
      250,000 rows is over 350 MB** (70% of the limit; a named constant
      in the script). The projection is the manual job's measurement,
      scaled from 2.5 million rows to 250,000;
    - reports the size after the run;
    - **removes its seeded rows at the end of the same run, on a typed
      OK:** it lists the bench tenants and their row counts, waits for
      the founder's OK at the terminal, deletes them, then reads the
      size again. **This replaces Q7's "removed afterwards through the
      per-action-OK sweep" for the bench rows** (founder, 2026-10-05:
      the OK stays per action, and moves to the end of the run).

**Found while recording, for the build (not decided):**
- **The second size read may not fall.** Postgres doesn't hand space back
  to the disk when rows are deleted: the table can shrink after a vacuum,
  the indexes generally don't without a rebuild. So the second read
  should report the bench tenants' row count (zero) beside the size, and
  the design should say what happens if the size stays high (a vacuum or
  a reindex needs the table's owner, which is the founder in the SQL
  Editor).
- **Where the guard's numbers lead.** With today's 23.4 MB, the guard
  passes while the manual job measures 2.5 million rows at about 3.2 GB
  or less. Above that it refuses, and the question comes back to the
  founder.
- **The manual job needs `0037`'s indexes**, so it is built with the
  matching work (PR 2), and it is one more step before the staging
  timing in part C's order.

#### The new catalog entries (founder's wording, 2026-10-02)

The founder's text, verbatim:

> **DOC-030** (tenant audience): Something went wrong on our end, not with
> your file. We're already on it. Please hold off on resending until we
> notify you that it's fixed, then re-upload the file or forward the
> original email.

"Forward the original email" is deliberate: re-sending an identical copy is
dropped as a duplicate (the same Message-ID).

> **Kind-2 warning, an attachment** (on the document, for the reviewer):
> Part of this email couldn't be read: the attachment "{filename}". The
> order was read from the rest of the email and may be incomplete. Check
> it against the original email before approving.

> **Kind-2 warning, the body:** The text of this email couldn't be read.
> The order was read from its attachments only. Check it against the
> original email before approving.

**To settle before PR 1 builds them (found while recording, 2026-10-02) -- settled below:**

1. **DOC-030's first sentence fails the catalog's own test.**
   `test_no_entry_uses_a_banned_empty_phrase` refuses "something went
   wrong" in any title or message: the 7.16.5 tone rule ("never say 'error
   occurred' or 'something went wrong' without the what/why/next"),
   enforced literally. The entry does carry the what, why and next, but
   the test doesn't read meaning, and relaxing it would let the empty
   form back in. Proposed: keep the test and change the opening, e.g.
   "This failed on our end, not because of your file." The founder's
   choice.
2. **Each entry has a title, a message and an action** (`ErrorCatalogEntry`;
   titles at most 8 words, tested). Proposed split, from the founder's
   sentences, with the titles mine and the founder's to change:

   | Code | Title (proposed) | Message | Action | Severity, audience |
   |---|---|---|---|---|
   | DOC-030 | "This failed on our side, not your file" (8 words) | the first two sentences (with item 1's change) | "Please hold off on resending until we notify you that it's fixed, then re-upload the file or forward the original email." | high, tenant (as DOC-021; the founder's alert is separate) |
   | VAL-017 (proposed code) | "An email attachment couldn't be read" | "Part of this email couldn't be read: the attachment "{filename}". The order was read from the rest of the email and may be incomplete." | "Check it against the original email before approving." | high, tenant |
   | VAL-018 (proposed code) | "This email's text couldn't be read" | "The text of this email couldn't be read. The order was read from its attachments only." | "Check it against the original email before approving." | high, tenant |

   **High**, like VAL-014, because an order that may be missing lines is
   at least as serious as one unreadable number. The reviewer must
   acknowledge it to approve (7.3).
3. **`{filename}` is the parse service's sanitized label**
   (`_sanitize_label`: anything but letters, digits, `.`, `_` and `-`
   becomes `_`, last 80 characters), so "Purchase Order March.pdf" shows
   as `Purchase_Order_March.pdf`. That is safe by construction. The UI
   escapes it as every document-derived value (7.12), and it is never
   logged (7.10).
4. **DOC-030's "until we notify you" is a promise the founder keeps by
   hand** (Q15). PR 1 adds the RUNBOOK step that goes with it: on a
   DOC-030 alert, the affected tenants are recorded, and when the fix is
   deployed each one is emailed. Without that step the catalog would
   promise something nobody is told to do.

**Settled (founder, 2026-10-02), on the four points above:**

1. **The test stays; DOC-030's opening changes.** The message is now:
   "We couldn't read this file because of a problem on our side, not with
   your file. We're already on it." The action is unchanged.
2. **Titles:** DOC-030's is **"We couldn't read this file"**: the title
   names the event, and the message explains it (the proposed title
   repeated the message). VAL-017's and VAL-018's titles as proposed.
   **High, tenant audience, for all three.** The final entries:

   | Code | Title | Message | Action |
   |---|---|---|---|
   | DOC-030 | We couldn't read this file | We couldn't read this file because of a problem on our side, not with your file. We're already on it. | Please hold off on resending until we notify you that it's fixed, then re-upload the file or forward the original email. |
   | VAL-017 | An email attachment couldn't be read | Part of this email couldn't be read: the attachment "{filename}". The order was read from the rest of the email and may be incomplete. | Check it against the original email before approving. |
   | VAL-018 | This email's text couldn't be read | The text of this email couldn't be read. The order was read from its attachments only. | Check it against the original email before approving. |

   VAL-017's and VAL-018's message and action are the founder's sentences
   unchanged, divided at the sentence that tells the reviewer what to do.
3. **`{filename}` is the sanitized label in PR 1.** Follow-up, not a
   blocker: show the original name if it is stored and the renderer
   escapes it, as a later change.
4. **The RUNBOOK step takes the affected tenants from the record, not from
   memory** (founder). Found while recording this: **the alert alone can't
   carry the list.** Q10's dedupe (one alert per exception type and code
   location per hour) means a repeat inside the window inserts no row
   (`founder_alerts.raise_alert` returns False), so a second tenant's
   DOC-030 in the same hour would be in no alert payload. The complete
   list is the documents themselves: every DOC-030 document is `failed`
   with `failure_code = 'DOC-030'` and its `tenant_id`, which exist today,
   so no migration. So:
   - **the alert** carries the tenant ID and document ID of the
     occurrence that raised it, plus the exception type and code location;
   - **each DOC-030 document's log line** carries its document ID, tenant
     ID, exception type and code location (IDs only, 7.10), so a document
     can be tied to the bug that failed it;
   - **the RUNBOOK step** lists, read-only, every document with
     `failure_code = 'DOC-030'` processed since the first alert, by
     tenant, and emails each tenant once the fix is deployed.

   The documents table is the authoritative list; the alert is only the
   trigger (founder, approved).

**DOC-030, VAL-017 and VAL-018 approved as final (founder, 2026-10-02).**
One check when they're built: a long sanitized filename (the full 80
characters) in VAL-017 doesn't break the layout where the message renders.
It is a test case, not a wording change (B3).

**PR 1's content is final (`444dc78`) and blocked only on the Stage 3
checkpoint's "go"** (founder, 2026-10-02). The founder's "go ahead" meant
the content was settled, not that the gate was lifted. **Nothing in Stage 4
is built, even on a branch, before that "go"**, for three reasons:
- **The checkpoint can change Stage 4.** The backup checks, the cutover and
  the first worker deploy are where something would be learned that changes
  the design.
- **An unmerged branch still costs something.** It drifts from `main` while
  the checkpoint lands fixes, and it invites "it's already built, just
  merge it" at the moment the checkpoint should be a clean decision.
- **The rule is cheap to keep and expensive to erode** (CLAUDE.md Section 0
  rule 3). If it's wrong, it is changed explicitly, not routed around.

The effort goes to what unblocks everything: the cutover, then G and A4 at
the first worker deploy (CHECKPOINTS.md, Stage 3 draft).

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
  3. Then revisit lowering `idle_in_transaction_session_timeout` for the
     four logins (`0036` sets 5 min on each, as `0028` did for
     `docflow_app`) toward 60 s, as a new migration, with
     `test_system_actors.py`'s timeout test and `test_logins_db.py`'s
     per-login check moved to the new value. *(Corrected 2026-10-02: this
     said `docflow_app` and "the CI role script", both gone since 3e.)*

- **Narrow `docflow_stripe`'s table grants to the tables the Stripe webhook
  touches** (founder, 2026-10-02, 3e Q4). In 3e all four logins share
  `docflow_tables`. `docflow_stripe` is the one with an internet-facing
  caller, so it is the one worth narrowing; the other three stay shared.
  Done with a test that lists the webhook's tables and fails when the code
  touches one not granted.
- **A founder-only re-run for a failed document** (founder, 2026-10-02,
  Stage 4 Q15). `failed` is final today (the state machine and `0027`'s
  trigger), so a DOC-030 or DOC-021 document is read again only when the
  customer sends it again. Reopening `failed` needs its own design: the
  new move, the trigger, a Console action and its audit row, a migration.
  Until then the founder emails the customer when a DOC-030's fix is
  deployed (RUNBOOK, from Stage 4 PR 1). If DOC-030s are common in the
  pilot, that is the evidence for building it.
- **Production's polled `/healthz` check** (3e, C1): production's API is
  public, and the worker heartbeat says nothing about the API. Tool and
  settings chosen when production's API exists (Healthchecks.io can't poll;
  Better Stack's free plan is labelled "personal projects").

**Phase 6 items from the first worker deploy** (2026-10-07 and 2026-10-08;
D-196 has the evidence for each; nothing here is built):

- **Watch: the rollup and the sweeps share the documents process** (founder,
  2026-10-08): "the rollup and sweeps share the documents process; the
  rollup took 2.22 s on staging data and will grow with tenants and
  history. Time it as data grows; propose a separate queue if it passes a
  few seconds." In the 500 + 1 run one document waited 4.34 s behind the
  rollup and two sweeps.
- **Watch: the documents process's memory over a long run** (founder,
  2026-10-08): it grew from 104 to 168 MiB over 500 text orders. To be read
  again after a longer run; the 500 MiB recycle threshold (proposed by its
  own PR) contains it meanwhile.
- **Production's worker is 2 GB** (founder, 2026-10-07). `apps/worker/fly.toml`
  says 1 GB today; the change, and Fly's price read again, belong here.
- **Large scans and the model's request limit:** a scanned PDF between
  about 23.9 MB and the 25 MB intake cap is accepted and then fails
  (DOC-008). The intake cap must sit below the model's limit after
  encoding, or large scans are split or downsampled before sending
  (founder). Proposed: downsample in the parse service, with a golden
  re-run; until then refuse at intake with its own catalog code. DOC-008's
  action ("upload the same file again") is wrong for this cause.
- **The worker needs the web address** so that the Console link in an
  alert e-mail is not `http://localhost:3000/admin`. A launch blocker.
- **A tenant with no tier has no abuse ceiling.** Proposed:
  `tenants.tier_id` NOT NULL, or no tier meaning the most restrictive.
- **The API stopping with a request in flight:** verify it finishes
  in-flight requests when stopped (`kill_signal`, `kill_timeout`); on
  staging an upload was cut off when Fly stopped the idle-looking machine.
- **Redis plan for production:** decided after the idle hour is measured
  again with a longer queue-poll interval and task-result storage off
  (proposed by its own PR). The rule and the corrected break-even, about
  6,850 commands an hour for the $10 plan, are in D-196 and RUNBOOK 9.7.
- **Upload time:** about 2.9 s an upload through `fly proxy` to the 512 MB
  staging API, not looked into; to be measured on production's API.
- **Open with the founder:** whether the catalog gets a price field (an
  import keeps the column per item in `items.raw_data` meanwhile); two
  rows of the UAT plan (TC-06 is stale, TC-07 should expect "reaches
  `needs_review`").

---

## Known open items (across phases)

- IIF export not yet validated against real QuickBooks Desktop (Phase 4).
- **Model policy, decided and not yet applied (founder, 2026-10-08; D-195):** routing moves from
  `claude-haiku-4-5` to `claude-haiku-5-5`; extraction stays on `claude-sonnet-5`. Model IDs move
  to one config module read from the environment, with a versioned price table, a startup check,
  a CI guard and one migration on `extraction_runs` (proposed to the founder first). No code
  changes before the founder's "go" at the Stage 3 checkpoint, and no later than 2026-11-16.
  `claude-haiku-4-5`'s retirement floor is 2026-10-15; it is not deprecated and no notice has
  been given. On or after 2026-10-16, Claude reads Anthropic's model-deprecations page again and
  reports. A notice for Haiku 4.5, or a checkpoint more than two to three weeks away, goes to the
  founder to decide on an earlier "go".
- **CI runner pinned to `ubuntu-24.04` (2026-10-02, founder's review of PR #35; merged as PR #36, main `d004186`):**
  `ubuntu-latest` moves to Ubuntu 26 from 2026-10-19, and the parse sandbox
  tests depend on the runner's cgroup v2 and network setup. Both workflows now
  name `ubuntu-24.04`; the actions moved to their Node 24 majors (checkout,
  setup-node, setup-python and upload-artifact v7, setup-buildx v4, build-push
  v7; `supabase/setup-cli` v2.1.2 already runs Node 24 inside). Moving to
  Ubuntu 26 is its own tested change, before 24.04 is retired.
  Known and approved: the parse self-test "A-net:internet-ipv6 NOT RUN (no
  control)" warning; the runner has no IPv6 (on 229cc4b's run too).
- **Web dependency audit: one named exception, review by 2026-11-05 (founder, 2026-10-05):**
  on 2026-10-05 the web job's audit began failing on a new high advisory,
  GHSA-vfj7-8cjw-p6xm, against `braces`. It is dev-only (reached through
  the lint chain `eslint-config-next` -> `@next/eslint-plugin-next` ->
  `fast-glob` -> `micromatch` -> `braces`; `npm audit --omit=dev` reports
  0), no patched `braces` exists, and npm's offered fix downgrades
  `eslint-config-next` to 14.2.35, which isn't taken. The audit now reads
  npm's report (`scripts/ci/npm_audit.py`) and fails on every high or
  critical advisory except those listed one by one, with a reason and a
  review date, in `.github/audit-exceptions.txt`. Dev dependencies stay in
  the audit. **The exception ends on its review date (founder,
  2026-10-05): the job warns on every run from 2026-10-29, and from
  2026-11-05 the web job fails until the line is removed or re-dated with
  a reason.** **An exception is held to its dev-only premise (founder,
  2026-10-05):** the job also reads `npm audit --omit=dev`, and an excepted
  advisory that appears in that runtime report fails the job, since the
  exception is keyed by advisory id and `braces` could later arrive through
  a runtime dependency. Tests:
  `packages/core/tests/test_npm_audit_exceptions.py` (npm's real report
  passes; the same report with a planted high or critical advisory fails;
  a planted runtime path for `braces` fails; a report that can't be read
  fails; the warning and the end date). **To do
  before 2026-11-05, or the web job goes red:** check for a
  patched `braces` or a Next lint chain without it, then remove the line or
  re-date it with a reason.
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
