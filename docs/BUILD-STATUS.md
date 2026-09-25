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
commit as the slice. Statuses below are as of **2026-09-25** (slice 5.10 built; walkthrough fixes D-144 – D-147).

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
  failing test.

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

## Phase 6 — Hardening · NOT STARTED

Error catalog completed and enforced by tests; rate limiting; cost circuit
breaker; retry with backoff; dead-letter queue; founder alerting; monitoring
hooks; RLS audit; log-redaction check; web-baseline checks (7.12);
`docflow-prod` created with migrations applied staging-first; backup restore
drill; `RUNBOOK.md`; the full UAT plan run and recorded.

- **Exit:** every test in `docflow-uat-plan.docx` executed; zero open
  Critical/High defects; restore drill succeeded; the Section 12 checklist is
  fully true.
- Some Phase 6 items already have early versions (alerts table, outbox, cost
  breaker). Phase 6 finishes and audits them; it does not start from zero.

---

## Known open items (across phases)

- IIF export not yet validated against real QuickBooks Desktop (Phase 4).
- Stuck-in-processing alert (7.9) not built; when built it must watch `pending`
  too (D-095).
- Custom per-tenant fields deferred until a prospect needs one (D-120).
- Error-catalog messages use ASCII " -- " instead of real dashes; a switch was
  offered.
- Pending document updates recorded in `DECISIONS.md`: ToS-vs-offboarding
  notice period (D-009), pricing doc missing allowances, build-timeline Phase 5
  wording (D-008), features doc missing approved-example prompting.
- `RUNBOOK.md` does not exist yet (Phase 6); several constants and the
  parser-upgrade process must be documented there.
- The worker's local venv was missing `httpx` (declared by core); installed 2026-09-23, worker suite is now 74 of 74. Run it with the worker's own venv, not the API's (which lacks `xlwt`, `pillow_heif`).
  That fix was local only: `httpx` never reached `apps/worker/requirements.lock.txt`
  (anthropic 1.6 switched to `httpx2`, so it stopped arriving transitively), and CI
  installs core with `--no-deps` -- the worker CI job was red from then on
  (`test_celery_app`, `No module named 'httpx'`). Fixed 2026-09-25: `httpx` declared
  in the worker's `requirements.txt` and pinned in its lock (same pins as the API).
  Verified in a clean venv built exactly as CI builds it: worker 81 passed.
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
- `RUNBOOK.md` does not exist, though CLAUDE.md 7.15.4 says constants are documented there; `constants.py` is the single home for now. Tier price changes (`scripts/new_tier_version.py`, D-137) belong there too.
- **Stripe setting, before the first real customer:** the account currently cancels a subscription after 90 days of an unpaid invoice (seen on Acme Test Prospect: "Auto-cancels Dec 18"). Policy is that the founder decides suspension (D-125), so set Settings → Billing → Subscriptions and emails → failed/past-due invoices to leave the subscription past due. Only the founder can change it.
- Sandbox leftover: Acme Test Prospect's founding coupon was created before the invoice-count fix (D-138) and discounts one extra invoice (19 Dec). Test data only; correct it in Stripe or leave it.

## Deferred by decision (Section 3 — do not build)

Native ERP integrations, invoice module, advanced change-order diff UX,
webhook/API delivery, formal customer-list management, auto-accept,
secondary-model verification, rule proposals, analytics, mobile, roles beyond
the four, non-English support, EDI/portal intake, multi-currency edge cases,
multi-entity refinements.
