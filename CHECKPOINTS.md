# Checkpoints

One entry per phase, written at the end of it, per `CLAUDE.md` Section 0
rule 3: "run the full test suite, write a checkpoint summary (what was built,
what was assumed, what's open), and wait for 'go' before starting the next
phase."

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
