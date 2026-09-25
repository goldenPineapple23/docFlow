# Checkpoints

One entry per phase, written at the end of it, per `CLAUDE.md` Section 0
rule 3: "run the full test suite, write a checkpoint summary (what was built,
what was assumed, what's open), and wait for 'go' before starting the next
phase."

---

## Phase 5 — Founder Console, tenant surface, operations — COMPLETE, awaiting "go" (2026-09-25)

Built as ten slices from 18 to 25 Sept 2026, each walked by the founder
before the next began. Slice-by-slice detail is in `docs/BUILD-STATUS.md`;
the reasoning is in `DECISIONS.md` D-102 – D-142.

### Exit criteria

| Criterion (Section 6) | Result |
|---|---|
| The founder runs all nine steps of the onboarding process against a fake prospect from the Console, ending with a live tenant whose owner can sign in by invite and see their reviewed test batch | **Met in 5.3** (18 Sept): Acme Test Prospect went from intake to live entirely from the Console. Stripe test-mode subscription, setup fee on the first invoice, founding price, first-week check-in scheduled. |
| The Console never calls a parsing, extraction or review function the tenant surface doesn't, **verified by a module-dependency check** | **Met, with a check added at this checkpoint** (`apps/api/tests/test_console_shared_paths.py`). Until today this rested on the design (D-111, D-112), not a check. The test confirms each of these from the code: <br>• the admin router imports no extraction, review, matching or validation module and no worker code <br>• it sends documents to extraction only through the tenant surface's own task <br>• its test-batch upload is the tenant's `ingest_upload` <br>• every acting-as route is served by the very same endpoint function as the tenant's route <br>• there is one catalog parser, used by the one catalog import |
| A full cancel → suspend → export window → reactivate cycle, and a full cancel → delete cycle, work end to end in staging for each of the three cancellation reasons, with the correct effective date | **Met by the lifecycle tests against the staging database** (effective date for each reason, including the non-payment fallback; suspend sweep; reactivation with no data loss; typed-name delete, purge order across every foreign key). **Walked in the browser:** cancel → suspend → reactivate (23 Sept), and a real typed-name delete (Acme Test Lifecycle B, 24 Sept, which found D-133). The browser walk used one reason, not all three; the other two rest on the tests. |
| The dashboard's KPI cards match a hand-computed query on the same staging data | **Met in 5.5**: every card is checked against independent hand-written SQL (D-121). |
| With example prompting on for a test buyer with 10+ approved documents, the golden fixture still extracts exactly, and the contamination test passes live | **Met today** (details below). |

**Example prompting, the last criterion:**
- **Live model tests:** golden fixture without examples, golden fixture with examples, contamination test. All three passed.
- **Real-stack drive** on Acme Test Prospect:
  - Ten fictional Bella's Coffee House orders went through the real worker task and were approved by a real tenant user with `approve_document` (`scripts/seed_example_history.py`).
  - The feature was turned on, then `docs/sample_po.txt` was put through the real worker as an upload with no sender.
  - The Haiku routing read found the buyer from the header, and Sonnet read the order with 3 examples (2,704 example tokens).
  - Every value came back exactly the golden fixture's.
  - Two runs were recorded. The order's cost ($0.0189) is the extraction ($0.0180) plus the look-up ($0.0009).

### Verification at the checkpoint

| Suite | Result |
|---|---|
| `packages/core` | 443 passed |
| `apps/api` | **385 passing, 0 skipped.** The full run (23 min against staging): 366 passed, 10 skipped because it began before migration 0025 was applied. Those 10, plus the 9 tests added afterwards (dependency check, Audit tab), then passed on their own, along with every file changed during the run. |
| `apps/worker` | 80 passed, 0 skipped |
| `apps/web` | 42 Vitest + 60 Playwright |
| Lint / typecheck | ruff clean (repo root and api); mypy clean (api, worker); ESLint and tsc clean |
| Live model tests | golden fixture, golden with examples, contamination: all passed against the real API |

Migrations `0011` – `0025` are applied to `docflow-staging`.

### What was built

- **Console foundations (5.1):** tiers table (versioned prices, never in code), email outbox, founder alerts, invites, intake staging.
- **Catalog and customer import (5.2):** one import path with preview, column mapping, a validation report with blockers, and diff commit (retire, never delete).
- **Test batch, acting-as review, go-live (5.3):**
  - Steps 6–9 as screens.
  - The tenant's review and export routes are mounted a second time behind an audited admin gate.
  - Stripe subscription invoiced by email; scheduled jobs are table rows.
  - Deal terms and setup-fee presets.
- **Operator screens (5.4):** buyer merge becomes a `buyer_alias` rule; learned-rule management; per-tenant field settings (required, optional or hidden, versioned).
- **Founder dashboard (5.5):** attention panel, health strip, tenant list and KPI cards, all from a nightly rollup.
- **Lifecycle (5.6):** cancel with a reason-based effective date, suspend sweep, reactivate, wind-down and ready-to-delete queues, typed-name hard delete, Stripe webhook sync, 7-day billing trial.
- **Allowances and quarantine (5.7):** metering, banners and emails, abuse ceilings, the daily AI-cost breaker, intake abuse layers, quarantine screens.
- **Tenant surface (5.8):** upload page, dashboard, activity page, needs-review digest email, team page, role audit.
- **Billing (5.9):** plan change from the Console, Billing card, MRR counted as what customers actually pay.
- **Approved-example prompting (5.10, D-141):**
  - A per-tenant switch that needs a golden-run confirmation.
  - Buyer pre-identification from the sender's email or domain, else a Haiku header read.
  - Up to 3 of the buyer's newest approved orders as text plus values. The parser's text is now stored per order.
  - The contamination test, recorded and live.
  - A "read with N earlier orders" note for reviewers.
  - Cost tracked separately in the Console.
- **The tenant page's Audit tab (D-143),** found missing at this checkpoint and built into 5.10 at the founder's request: lifecycle events and Console actions, newest first, with page views on request.

### What was assumed

- **Only two roles are handed out** (founder decision, 5.8a): owner (shown as Admin) and reviewer. Viewer stays in the schema.
- **The allowance banner waits until 90%** (D-129), a deliberate deviation from 7.16.1's 80%; the 80% and 100% emails are unchanged.
- **`CURE_PERIOD_DAYS` = 0** (D-125): Net-15 invoice terms are themselves the grace period; suspension is always the founder's decision.
- **Test-batch orders count towards a buyer's 10 approved orders** for example prompting; the founder approved them in the normal review screen (D-141).
- **No second extraction pass with examples** (founder, D-141).
- **Example prompting is off for every tenant.** It was switched on for Acme Test Prospect for the drive and left on there; it is test data.

### What is open

- **Before the first real customer:**
  - an email provider (until then every invite, notice and digest waits in the Console outbox)
  - the Stripe setting that auto-cancels a subscription after 90 days unpaid (D-125)
  - `RUNBOOK.md` (Phase 6)
- **Stranded test data:**
  - Interrupted test runs (19–24 Sept) left five `console-…@example.com` platform admins. With the founder's OK they were **revoked and deactivated on 2026-09-25**, not deleted: two of them are named as the actor on the `created` events of leftover test tenants, and deleting them would rewrite that history. The founder is now the only active platform admin.
  - The same runs left three "Acme Test Distributor" tenants (two active, one in wind-down). All staging data is test data; the founder will clean up in a QA pass after all phases are built.
  - The test helper that strands them (`_Console` cleanup when a lifecycle event names its user) should be fixed in Phase 6.
- **Carried from before:** IIF against real QuickBooks Desktop; the stuck-in-processing alert (7.9, Phase 6); custom per-tenant fields (D-120); per-person digest opt-out; " -- " versus real dashes in catalog text; pending document updates listed in BUILD-STATUS.
- **Orders approved before 2026-09-25 have no stored text,** so they count towards a buyer's 10 but can't be shown as examples.

### Found at this checkpoint, not by the tests

- **The Audit tab 7.15.3 lists had never been built**; found while writing the walkthrough, built now (D-143).

- **The daily AI-cost breaker could never trip** (D-142). It and the health strip's model counts read `extraction_runs`, which nothing wrote to. Its test inserted rows by hand. Every model call is recorded now.
- **The Phase 5 dependency criterion had no check.** It held by design but nothing verified it; `test_console_shared_paths.py` does now.
- **The two runs of one order sorted in the wrong order** on the real drive: same-transaction `now()`, the D-123 trap again. Fixed with `clock_timestamp()` and a regression test.

---

## Phase 4 — Export — COMPLETE, awaiting "go" (2026-09-18)

### Exit criteria, both met

| Criterion (Section 6) | Result |
|---|---|
| Round-trip: the exported file, parsed back, equals the approved data exactly | **Passed, all four formats** — in core against hostile values (formula text, quotes, commas, line breaks, non-English text), and end to end through the API against the real database, comparing the downloaded bytes with the stored snapshot. IIF compares every field it carries and asserts its omissions are exactly the documented set (D-099). |
| Exporting twice produces byte-identical files | **Passed** — rendered twice in-process, pinned SHA-256 digests per format (stable across processes, Python environments and time), and two real exports of one snapshot record the same checksum. |

Both checks also run **at runtime** on every export: a file that is not
byte-identical on a second rendering, or does not parse back to the snapshot,
is never stored or offered (`EXP-004`).

### Verification at the checkpoint

| Suite | Result |
|---|---|
| `packages/core` | 282 passed |
| `apps/api` | 159 passed, 0 skipped (17 new export tests against the real database and RLS) |
| `apps/worker` | 72 passed, 0 skipped |
| `apps/web` | 23 Vitest + 14 Playwright |
| Lint / typecheck | clean in all four projects |
| Live golden fixture | passed against the real Anthropic API |
| Real browser, real stack | signed in, approved an order, downloaded CSV, Excel, JSON and IIF through API → Redis → worker → storage → signed link; status became "Exported to file" |
| Excel file in real office software | opened in LibreOffice: formula text stays text, amounts keep their exact decimals |

Migration `0010` is applied to `docflow-staging`.

### What was built

- **`exports` table** (0010): one row per export from the click, pinned to
  the exact approved snapshot, `pending → ready | failed` with a catalog
  code; finished rows made immutable by a trigger. RLS from creation.
- **`docflow_core/exports.py`** — pure: approved snapshot in, verified bytes
  out, for CSV, Excel, JSON and QuickBooks IIF (Estimate). No database access.
- **`docflow_core/export_jobs.py`** — records requests (API) and produces
  files (worker). The web process never imports the file-building module
  (enforced by the parsing-boundary test).
- **Worker task** `docflow.generate_export`; **API** routes to request, list,
  poll and download; downloads via purpose-bound short-lived signed links.
- **Export panel** on the review screen: four buttons, automatic download,
  per-order history with "Earlier approval" marking, catalog-coded errors.
- **Catalog SKU frozen into the approval snapshot** (D-099), so an export
  carries the tenant's own SKU as it was when the order was approved.
- Seven `EXP-0xx` catalog entries.

### What was assumed

- **D-099 IIF details** — account names `Estimates` / `Sales`, that an unknown
  customer `NAME` is created rather than refused, and that catalog SKUs match
  QuickBooks item names. Constants in one place; unverifiable without
  QuickBooks Desktop.
- **D-100** — viewers may export (the catalog already promised it).
- **One PO per file** — the founder's choice; batch export deferred.

### What is open

- **IIF against real QuickBooks Desktop (UAT TC-26).** The founder is finding
  someone with QuickBooks Desktop to try an import.
- **EXP-004 founder alert** — logged at error level until `founder_alerts`
  exists (Phase 5, 7.15.3), then wired to it (D-098).
- **Orders approved before today** export with an empty catalog SKU until
  re-approved (D-099).
- ~~"One-click Approve & Export"~~ **Done** after the checkpoint (D-101): a
  button beside Approve with a remembered format; verified on the real stack.
- **An IIF file made before the order-date rule** (the test order
  `e2e-po.docx`, BCH-2291) remains in that order's history; finished exports
  are permanent records by design.

### Found by driving the real app, not by the tests

An order with **no order date** produced an IIF file with a blank DATE, which
QuickBooks would reject. Every test passed, because no test fixture lacked a
date. IIF now refuses such an order with `EXP-006` and says to add the date
or export CSV/Excel. The Phase 3 lesson held again: drive the real thing.

### Found by CI after the checkpoint commit

The pinned Excel digest failed on CI's Linux runner: Python's zipfile
stamps each entry with the OS that wrote it, so the same approved order gave
different .xlsx bytes on Windows and Linux -- a real break of "same snapshot,
byte-identical file" once the worker runs on a Linux server. Fixed in
`3d413b7` (the field is pinned; a test builds every format as Windows, Linux
and macOS against one set of digests). CI green; core 282 passed.

---

## Phase 3 — Human review UI — COMPLETE (2026-09-17)

### Exit criteria, both met

| Criterion (Section 6) | Result |
|---|---|
| A non-technical person corrects and approves the golden fixture in under 2 minutes | **Passed.** Run by a tester who had not seen the product before. |
| The audit trail shows exactly what changed | **Passed.** The trail names each field with its before and after value. |

### Verification at the checkpoint

| Suite | Result |
|---|---|
| `packages/core` | 228 passed |
| `apps/api` | 141 passed, 0 skipped |
| `apps/worker` | 56 passed, 1 skipped (LibreOffice absent) |
| `apps/web` | 23 Vitest + 8 Playwright |
| Lint / typecheck | clean in all four projects |
| Live golden fixture | passed against the real Anthropic API |
| CI | green (first time in the project's history — see D-087) |

Migrations `0007`, `0008` and `0009` are applied to `docflow-staging`.

### What was built

**Slice 1 — review core** (`6705d6f`). `review_actions` and
`document_snapshots` tables; approval columns on `documents`; the
`documents_approved_is_attributable` CHECK; `docflow_core/review.py` with
edit / approve / reject / reopen, the editable-field allowlist, and snapshot
freezing. Five `REV-0xx` catalog entries.

**Slice 2 — review API** (`fcba9c0`). Nine endpoints over
`docflow_core.review`; first role enforcement in the codebase (`viewer`
reads, everyone else writes); `app/errors.py` as the single catalog-to-HTTP
renderer; signed short-lived URLs for the document viewer.

**Slice 3 — review UI** (`707f183`). Queue and review screens, sandboxed
document viewer, per-field confidence, editable header and lines, SKU search
with create-mapping, approve / reject, audit trail, keyboard shortcuts. First
tests `apps/web` has ever had, wired into CI.

**After the walkthrough** (`4732b44`, `8d3d5fb`, `a029cfe` and others):
everything the tester stumbled on, the document previews for formats no
browser renders, and the visual pass.

### What was assumed

- **Tolerances (D-073)** are judgement calls, not spec numbers. Tuned so a
  correct 40-line order produces zero warnings, accepting that a sub-cent
  per-line error goes unreported.
- **D-078** errs toward surfacing: an unknown buyer on either side still
  flags a change order. Nothing here auto-applies.
- **D-080** rounds *derived* money to two places for display. Extracted
  values are never re-scaled.
- **D-086** — the Playwright suite stubs the API at the network boundary.
  The API's own behaviour is proven against real Postgres separately.
- **D-092** — a preview is a convenience: a parser failure means no preview,
  never a failed document.

### What is open

- ~~Redis / Celery has never run end to end.~~ **Done** 2026-09-18
  (D-095): Memurai installed; a Word PO uploaded through the API went
  upload → Redis → worker → extraction → matching → validation →
  `needs_review` in 16 s, with its preview stored. The first real run found
  that the worker registered **no tasks** when started as documented, so
  every document would have sat in `pending` forever. Fixed and tested.
- ~~LibreOffice is not installed.~~ **Done** 2026-09-18 (D-094):
  installed, the gated `.doc` test now runs (worker: 0 skips), and it found
  that a corrupt `.doc` was "converted" into a document of garbage instead
  of failing. Fixed by pinning LibreOffice's Word import filter.
  **Still open from this item:** full-fidelity previews for Word/Excel
  (convert to PDF in the worker) -- now possible, not yet built:
  Section 7.11 already runs LibreOffice headless in the isolated
  worker for Tier 2 formats, and the same call converts `.docx`
  and `.xlsx` to PDF, which a browser renders with the layout intact. That
  replaces today's extracted-text preview for those formats with the real
  page. `.eml` / `.msg` stay as text -- an email body has little layout, and
  when a PO arrives by email the order is usually an attachment, which 7.11
  already unwraps and validates on its own. Ruled out permanently: a hosted
  conversion service (Section 7.10 and Section 10 forbid sending customer
  documents to third parties) and browser-side renderers such as SheetJS or
  docx-preview (that moves parsing of hostile files into the customer's
  browser, and 7.12 forbids rendering document-derived markup).
- ~~The line-items section is taller than it needs to be.~~ **Done** after
  the checkpoint (commit `1823147`): line items are now a table, one row per
  line, with matching state in a row beneath that opens whenever there is
  something to see.
- ~~The worker does not generate document previews.~~ **Done** after the
  checkpoint (D-093): the worker now writes a preview for every upload a
  browser cannot show, and in doing so closes two gaps the module had --
  `.msg`, `.doc`, `.xls`, `.odt` and `.ods` got no preview at all, and Word
  previews omitted tables, i.e. the line items.
- **Aesthetics** — the founder wants a further pass. The document viewer is
  explicitly liked and should be left alone.
- **Phase 5 asks already raised by the founder**, correctly scheduled and not
  built: a per-tenant dashboard (documents by status, recent activity) and
  the allowance banner (7.16.1). Phase 4 is export to downloadable files
  (CSV, Excel, JSON, IIF) -- not ERP writeback, which Section 3 rules out.

### The lesson of this phase, recorded because it cost the most

Three defects reached the end of Phase 3 having passed every suite: CI had
**never** been green (D-087), login had **never** worked on a real Supabase
project (D-088), and four more failures sat between the layers (D-089) —
missing browser configuration, no CORS, a viewer that could not
authenticate, and two headers that each independently stopped the document
rendering.

Every layer was well tested. The seams between them were tested nowhere, and
no test signs in and then looks around. The walkthrough found a dead-end
landing page in under a minute (D-090) that no amount of unit testing would
have surfaced.

**A green suite is evidence about the layer it covers, never about the
product.** Drive the real thing — real browser, real API, real database —
before calling a phase done.

---
