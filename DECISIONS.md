# DocFlow — Decisions Log

Every judgment call made during the build: what was ambiguous, what was chosen, why, and which document it relates to. Reviewed by the founder at each phase checkpoint. Append-only in spirit — don't rewrite history, add a new entry if a decision changes.

---

## D-001 — Architecture: Path B (Next.js + FastAPI monorepo)

**Date:** Phase 0 kickoff.
**Context:** Build prompt Section 4 asked for a recommendation between an all-TypeScript Next.js/Supabase monolith (Path A) and a Next.js + FastAPI split (Path B).
**Decision:** Path B, confirmed by the founder.
**Why:** `docflow-architecture.docx` and `docflow-tech-stack-costs.docx` both already describe this exact split independently. Section 7.11's hostile-file defense and Section 7.1's extraction pipeline are the highest-stakes, most proven-fragile parts of the system, and Python's library ecosystem (`pdfplumber`, `python-docx`, `openpyxl`, `pillow`/`pillow-heif`, `extract-msg`, `defusedxml`) is materially stronger here than the TypeScript equivalents. The existing proof-of-concept (`parse_pos.py`) is Python and already proven on the golden fixture.
**Related:** Section 4, `docflow-architecture.docx`, `docflow-tech-stack-costs.docx`.

## D-002 — Job queue: Celery + Redis

**Decision:** Celery + Redis, not Inngest/Trigger.dev.
**Why:** Section 5.1 requires per-tenant fairness and priority (interactive re-runs over bulk backfills). Celery has mature multi-queue/priority routing to build this on directly; a lighter runner would need it custom-built. Keeps the whole backend in one language (Python).

## D-003 — Isolated parsing worker: practical network-isolation substitute

**Context:** Section 7.11 requires the parsing/conversion worker to have "no network access." True per-job network-namespace denial needs `CAP_NET_ADMIN`, which typical PaaS containers (Railway/Render) don't grant.
**Decision (confirmed by founder):** Enforce this as a service-level egress firewall (the worker service can reach only the Anthropic API, Supabase, and the email provider's API — nothing else), layered with per-file subprocess limits: hard memory/CPU ceiling (`resource.setrlimit`), a hard wall-clock timeout with SIGKILL, an unprivileged OS user, and one subprocess per file (never reused).
**What this gives up:** this is weaker than true per-job network denial — a compromised parsing subprocess could still, in principle, reach the small allowlisted set of hosts. Documented here explicitly rather than silently substituted, per the founder's own request to see this named plainly.
**Related:** Section 7.11.

## D-004 — `users.tenant_id` made nullable for platform-admin-only accounts

**Context:** `platform_admins.user_id` references `users(id)`, but the base schema has `users.tenant_id NOT NULL`. The founder is explicitly "not a tenant role" (Section 3) and needs an ordinary `users` row to log in via `/login`.
**Decision (confirmed by founder):** `users.tenant_id` is nullable. A platform-admin-only account has `tenant_id IS NULL`. A partial unique index (`idx_users_email_global`) enforces email uniqueness across all `tenant_id IS NULL` rows, since the existing `UNIQUE (tenant_id, email)` constraint would otherwise allow duplicate emails across different NULL rows (Postgres treats NULLs as distinct).
**Why over the alternative:** a fake "internal" tenant for the founder risks the founder leaking into tenant-scoped lists, KPI rollups, or revenue totals if a filter is ever missed. A `NULL`-tenant account can never match a `tenant_id = ...` RLS policy, so it fails closed by construction.
**Related:** Section 7.15.1, Section 9, `docflow-database-schema.docx`.

## D-005 — Setup fee billing default: charge via Stripe automatically

**Decision (confirmed by founder):** The go-live action defaults to charging the setup fee via Stripe immediately, same transaction as creating the monthly subscription. Per-tenant override to "invoiced manually" remains available.
**Why:** Section 7.15.2 explicitly asked for a proposed default. Automatic charging removes a manual step the founder could forget; the override exists for the "Complex" setup-fee cases in `docflow-pricing.docx` that may need a negotiated, non-standard amount.
**Related:** Section 7.15.2, `docflow-pricing.docx`.

## D-006 — `customer_item_mappings` fully replaced by `learned_rules`, no compatibility view

**Decision:** Drop `customer_item_mappings` outright; migrate its concept into the generalized `learned_rules` table (Section 7.13/9).
**Why:** No production data exists yet, so there's nothing a compatibility view would be protecting. A view would be permanent complexity for zero benefit.
**Related:** Section 9.

## D-007 — Missing/renamed reference documents

**Context:** `docflow-mvp-features.docx` and `docflow-build-timeline.docx` were initially absent from `docs/`, and `extracted_excel.csv` was supplied as `extracted_excel.txt` (same content, different extension — confirmed by the founder as a copy-paste substitution because the original `.csv` couldn't be found). All three were subsequently supplied and read in full.
**Resolution:** No open gap remains. `docflow-mvp-features.txt` confirms the MVP scope matches Section 3's deferred list. It does **not** list "detect and split multiple POs in one file" (mentioned only in `docflow-processes.docx` §6 and UAT TC-35) as in-scope — so that capability is *not* built as an active feature; the existing graceful-degradation fallback ("flag for manual handling") is what ships.
**Related:** Section 2, Section 3.

## D-008 — Build-timeline Phase 5 self-serve wording confirmed superseded

**Context:** `docflow-build-timeline.docx` Phase 5 describes a "guided onboarding wizard" and an exit criterion of "a new customer can go from signup to first export without help" — this is a literal self-serve signup description.
**Resolution:** Confirmed present as expected. Per the build prompt's own instruction, Section 6 of the build prompt is authoritative for Phase 5 (the founder-operated Console); this document's wording is stale and superseded, not a live conflict requiring a decision.
**Related:** Section 2, Section 6.

## D-009 — ToS vs. Offboarding notice-period discrepancy

**Context:** `docflow-terms-of-service.docx` §4 states 30 days' written notice for termination for convenience. `docflow-offboarding-process-v2.docx` uses "end of current paid period" for the same case. Both source documents flag this themselves as unresolved.
**Resolution:** Implementing the Offboarding doc's rule (`current_period_end` as the default `customer_requested` effective date) as the single source of truth, per Section 7.15.4's explicit instruction not to stop and ask. The notice-period computation lives in one function so it can be changed in one place once an attorney finalizes the real ToS number.
**Related:** Section 7.15.4.

## D-010 — Proof-of-concept file-format support does not match its own documentation

**Context:** `DocFlow-Master-Spec.docx` and `docflow-technical-spec.docx` both claim `.docx`, `.xlsx`, and `.rtf` are "confirmed working in the proof-of-concept," and `extracted_excel.txt` (formerly `.csv`) shows a `PO1.docx` source file with fully correct extracted values. However, `docs/parse_pos.py`'s `build_message_content` has no branch for `.docx`/`.xlsx`/`.rtf` — those extensions fall through to `return None, "unsupported"` and are silently skipped. `requirements.txt` lists only `anthropic` and `pdfplumber` — no `python-docx`, `openpyxl`, or RTF library.
**Resolution:** The extraction *prompt and schema* (Section 8.2) are proven and reused verbatim in spirit. The *file-reading* layer for `.docx`/`.xlsx`/`.rtf` (and everything else on the Section 7.11 allowlist beyond what `parse_pos.py` actually handles: text/eml/html/csv/md, PDF, images) is being built fresh in Phase 1, not ported from working code. The `extracted_excel.txt` values are being kept as a second reference fixture for the `.docx` positive-format test required by Section 7.11, not as evidence the code path already exists.
**Related:** Section 1, Section 7.11, Section 8.2.

## D-011 — Golden fixture currency-inference assertion added

**Context:** `docs/sample_po.txt` never states a currency code explicitly — "USD" is only inferable from `$` symbols on the line items and total.
**Decision:** The golden fixture test (Section 8.3) additionally asserts `currency_inferred = true` and `header_confidence.currency <= 0.6`, per the Section 7.1 rule that symbol-inferred currency must have capped confidence and a warning. This wasn't listed explicitly in Section 8.3's fixture spec but follows directly from Section 7.1.
**Related:** Section 7.1, Section 8.3.

## D-012 — `users.auth_user_id` added to link Supabase Auth identities to local user rows

**Context:** Neither `docflow-database-schema.docx` nor the build prompt's Section 9 additions include a column linking a `users` row to its corresponding Supabase Auth identity (`auth.users.id`). Without one, the backend has no way to go from a verified Supabase session JWT (which carries the Supabase auth user's UUID in its `sub` claim) to the correct local `users` row and its `tenant_id`/`role`.
**Decision:** Added `users.auth_user_id UUID UNIQUE` (nullable until the user completes their invite). Populated when a user accepts their invite and creates their Supabase Auth account.
**Related:** Phase 0 auth implementation, `apps/api/app/deps.py`.

## D-013 — Backend connects as a dedicated non-bypassing Postgres role, not the default `postgres` user

**Context:** Supabase's default "Connection Pooling" connection string (the one SETUP.md originally pointed at) authenticates as the `postgres` role, which has `BYPASSRLS`. If the backend used that connection directly, every `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY` statement in the schema would be silently inert for all of the backend's own queries — RLS would only ever protect access through Supabase's PostgREST/JS client (which this backend doesn't use), not through our SQLAlchemy connection. That would make Section 7.5's tenant isolation app-level convention only, not a real database-enforced guarantee, for the one thing the build prompt calls "the one thing that cannot ever fail."
**Decision:** `DATABASE_URL` connects as a dedicated Postgres role (`docflow_app`) created explicitly with `NOBYPASSRLS`, granted only the privileges it needs on the `public` schema. `SET LOCAL app.tenant_id` in `tenant_session()` (`packages/core/docflow_core/db.py`) is only a real enforcement mechanism because of this role choice — see `SETUP.md` Step 1 for the exact SQL to run once `docflow-staging` exists.
**Why over the alternative:** using the default `postgres` connection was tempting because it's what Supabase's dashboard shows by default, but it would have made every RLS policy in the schema a no-op for the backend's own traffic — the single most severe kind of bug this build could ship, since it would look correct in every test that doesn't specifically check it.
**Related:** Section 7.5, `SETUP.md` Step 1, `supabase/migrations/0001_foundations.sql`.

## D-014 — `/admin/*` frontend gating is client-side for Phase 0; the real boundary is the backend

**Context:** `apps/web/src/app/admin/layout.tsx` checks `GET /auth/me` in a `useEffect` and calls `notFound()` if the caller isn't a platform admin. This is a client component, so there's a brief moment before the check resolves where nothing renders, and the check itself runs in the browser rather than being enforced before any HTML ships.
**Decision:** Accepted for Phase 0. The actual security boundary — `require_platform_admin` in `apps/api/app/deps.py`, which 404s the API itself for anyone without an active `platform_admins` row, including unauthenticated requests — is fully server-side and is what CLAUDE.md Section 7.15.1's required tests check. The frontend gate is defense-in-depth/UX polish on top of that, not the thing standing between a stranger and tenant data.
**Follow-up:** Phase 5, when the Console gets its real layout, should revisit this with a proper server-rendered check (e.g. via `@supabase/ssr` reading the session from cookies before rendering) so a non-admin gets an instant 404 with no client-side round trip at all.
**Related:** Section 7.15.1, `apps/web/src/app/admin/layout.tsx`.

## D-015 — Supabase project uses the new key system; JWT verification supports both a shared secret and JWKS

**Context:** The founder's `docflow-staging` project surfaces Supabase's newer `sb_publishable_...` / `sb_secret_...` API keys rather than only the legacy JWT-based `anon`/`service_role` keys. Projects on this newer system typically sign session tokens with asymmetric keys (ES256/RS256) rather than a shared HS256 secret, and the string initially found on the "JWT Secret" field was a UUID — almost certainly a Key ID, not a usable secret. The founder subsequently found the project's actual "Legacy JWT Secret," a real base64-looking random value, which Supabase still exposes even on newer projects.
**Decision:** `apps/api/app/deps.py` supports both verification paths: if `SUPABASE_JWT_SECRET` is set, verify with HS256 against that shared secret (fast, no network call, and what `docflow-staging` actually uses per the Legacy JWT Secret found above). If it's unset, fall back to fetching the project's JWKS (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) via `PyJWKClient` and verifying with ES256/RS256. `packages/core/pyproject.toml` now depends on `pyjwt[crypto]` (pulls in `cryptography`) to support the JWKS path even though the shared-secret path is what's actually in use right now.
**Open item, not yet confirmed:** the Legacy JWT Secret is used as-is (its literal string bytes, UTF-8 encoded) as the HMAC key, matching PyJWT's default behavior for a string secret. It is *not* base64-decoded first. If sign-in verification fails once real login is tested, this is the first thing to check — the alternative is that Supabase expects the base64-decoded 32 raw bytes as the HMAC key instead of the literal string.
**Related:** `apps/api/app/deps.py`, `SETUP.md` Step 1, `.env.example`.

## D-016 — Three fixes required to make `docflow_app` writes work correctly through Supabase's pooled connection

**Context:** Once `DATABASE_URL` (the pooled, transaction-mode connection on port 6543) was actually wired up and tested against real `docflow-staging` schema, three distinct bugs surfaced that unit tests against a mock could never have caught — this is exactly the class of bug Section 0 rule 3's "real test suite at every checkpoint" exists to catch:
1. **RLS policies crashed instead of denying.** `current_setting('app.tenant_id', true)::uuid` assumes an unset setting reads back as SQL `NULL`. On this pooled connection, issuing Postgres's `RESET app.tenant_id` (needed as a defense described in point 3) actually restores a custom GUC to an **empty string**, not `NULL`, and casting `''::uuid` raises an error rather than evaluating to `false`. Every tenant-scoped RLS policy's `USING` clause was rewritten from `current_setting(...)::uuid` to `nullif(current_setting(...), '')::uuid`, which treats "never set" and "explicitly reset" identically. Fixed in both `supabase/migrations/0001_foundations.sql` and (already-applied) via a patch SQL script run directly against `docflow-staging`.
2. **`dict`/`list` values passed for `jsonb` columns failed to adapt.** psycopg3 has no built-in knowledge that a bare Python `dict` (e.g. `admin_actions.payload`) should serialize as `jsonb` — it raised `cannot adapt type 'dict'`. Fixed once, globally, in `docflow_core/db.py` via `psycopg.adapters.register_dumper(dict, JsonbDumper)` (and the binary variant, and `list`), rather than wrapping every call site in `Jsonb(...)`.
3. **Custom `app.*` session settings need active defense against connection reuse.** `DATABASE_URL` is Supabase's transaction-mode pooler: a single logical connection can be used by different transactions that land on different backend Postgres connections over its lifetime. `tenant_session()`, `platform_session()`, and `identity_lookup_session()` each now call a shared `_reset_rls_settings()` first, which explicitly `RESET`s all three `app.*` settings before setting the one(s) that transaction actually needs — rather than assuming a freshly-acquired connection has no ambient state from someone else's prior transaction. This is what originally surfaced bug #1 above. Also disabled psycopg3's automatic server-side statement preparation (`connect_args={"prepare_threshold": None}`) per Supabase's own guidance for transaction-mode pooling, as a defensive measure, even though it was ruled out as the cause of any specific symptom seen during this debugging session.
**Why this matters beyond Phase 0:** all three bugs were invisible to the existing test suite before `DATABASE_URL` pointed at a real database, and RLS in particular is Section 7.5's "the one thing that cannot ever fail." The lesson generalizes: any future schema change touching RLS policies on custom GUCs, or any new jsonb column, should be checkpoint-tested against real `docflow-staging`, not just unit-tested with a mock connection.
**Related:** Section 7.5, `packages/core/docflow_core/db.py`, `supabase/migrations/0001_foundations.sql`, `apps/api/tests/test_admin_access.py`.

## D-017 — `supabase/migrations/0002_documents.sql` cannot be applied programmatically; same manual process as 0001

**Context:** Verified live against `docflow-staging` (`has_schema_privilege(current_user, 'public', 'CREATE')` returns `false` for the `docflow_app` role). This is by design (D-013): `docflow_app` was granted `USAGE` and blanket `GRANT ALL ON ALL TABLES`/`SEQUENCES`, but never `CREATE` on the schema itself, so it can read/write existing tables but cannot create new ones.
**Decision:** `0002_documents.sql` is written and tested for syntax/shape consistency with `0001` but must be applied by the founder via the Supabase SQL Editor (as the `postgres` owner role), exactly as `SETUP.md` Step 5 already documents for `0001`. `SETUP.md` Step 5 is updated with this instruction. Every test that touches the real `documents`/`document_headers`/`document_lines`/`intake_rejections` tables is gated on a new `requires_documents_schema` skip marker (in both `apps/api/tests/conftest.py` and `apps/worker/tests/conftest.py`) that probes `SELECT 1 FROM documents LIMIT 0` and skips cleanly if it fails, rather than failing with a confusing "relation does not exist" error.
**Verification status:** all logic that doesn't require the live table (file_types, extraction, content-block building, the golden fixture CI replay and live-API test) is fully tested and green. The four DB-integration tests in `apps/api/tests/test_documents_upload.py` that need the real tables are written and will run once the founder applies the migration; they currently report as skipped, not passing, and should be re-run at the next checkpoint after that.
**Related:** Section 7.5, `SETUP.md` Step 5, D-013, `supabase/migrations/0002_documents.sql`.

## D-018 — File size cap: 25 MB

**Decision:** `MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024` in `packages/core/docflow_core/file_types.py`.
**Why:** CLAUDE.md Section 7.11 asks for "a per-file size cap" as a named constant without specifying a number. 25 MB comfortably covers any real PO or catalog file (the proof-of-concept and onboarding samples are all well under 1 MB) while still bounding worst-case memory/CPU for the isolated parsing worker. Revisit if a real catalog upload (Phase 5) turns out to need more.
**Related:** Section 7.11.

## D-019 — Blob storage: local filesystem stand-in, not real Supabase Storage

**Context:** No Supabase Storage bucket or client wiring exists anywhere in the codebase as of Phase 0. Standing up real object storage (bucket creation, service-role client, signed URLs) is a larger piece of work than this slice's scope, and CLAUDE.md Section 7.5 only requires that storage paths follow the `tenants/{tenant_id}/...` convention and that path enforcement live in one place.
**Decision:** `packages/core/docflow_core/storage.py` implements that same path convention and prefix enforcement against the local filesystem (a new `storage_root` setting, default `./storage`, gitignored). `save_file`/`read_file` are the only two functions any caller uses, so swapping in real Supabase Storage later is a change to this one module, not to the upload endpoint or the worker task.
**What this gives up:** the API and worker processes must share a filesystem (true in local dev and in a single-host deploy; a real multi-host production deploy needs real object storage). Tracked as a follow-up, not silently glossed over.
**Related:** Section 7.5, Section 7.11, `packages/core/docflow_core/storage.py`.

## D-020 — Duplicate detection: surfaced in the response only, not yet a stored relationship

**Context:** CLAUDE.md Section 7.8 requires that same-content uploads for the same tenant are "linked to the existing document and surfaced as a possible duplicate," never silently re-ingested. The `documents` table this migration creates has no `duplicate_of_document_id` (or similar) column — Section 8's Phase 1 schema list for `documents` doesn't include one.
**Decision:** `POST /documents/upload` still computes `content_sha256`, still checks for an existing document with the same hash *for that tenant* (RLS-scoped, so this can never cross a tenant boundary), and still creates the new document row and enqueues it for extraction as normal — no document is ever silently dropped or merged. The match, if found, is returned to the caller as `possible_duplicate_of` but is not persisted as a foreign key or surfaced in the review UI (which doesn't exist yet). Full duplicate-linking (a stored relationship, review-UI surfacing, and the accompanying schema column) is deferred to whichever later phase builds the review UI and export path that would actually consume it.
**Related:** Section 7.8, `apps/api/app/routers/documents.py`.

## D-021 — No `temperature` parameter on the extraction call

**Context:** CLAUDE.md Section 3 says "Temperature 0." The installed `anthropic` SDK (1.6.0) raises a `TypeError` for an unexpected keyword when `temperature` is passed to `messages.create()` for `claude-sonnet-5` — the parameter isn't in the method signature at all for this model generation, confirmed by direct inspection and a live call before/after removing it.
**Decision:** Drop the `temperature` argument entirely from `docflow_core.extraction.extract_document`. There is no sampling-control knob available for this model/SDK combination; determinism and "never invent a value" are enforced instead by structured outputs (a strict JSON schema, `output_config.format`) plus the system prompt's explicit null-for-missing instruction, which is what Section 7.1 actually relies on for correctness (temperature 0 was never a data-integrity mechanism by itself).
**Related:** Section 3, Section 7.1, `packages/core/docflow_core/extraction.py`.

## D-022 — Extraction JSON schema: `document_notes` made non-nullable to fit the structured-outputs union-type cap

**Context:** A live call against the real API returned `400 invalid_request_error`: "Schemas contains too many parameters with union types ... limit: 16 parameters with unions." The Section 8.1 schema, built literally (every field nullable via `["string", "null"]`), has exactly 17 such fields: 10 header + 6 line-item + `document_notes`.
**Decision:** Every header field and every line-item field stays nullable (16 fields — these are the fields CLAUDE.md Section 7.1 is centrally about: "never guess, infer... return null"). `document_notes`, the one remaining document-level free-text field, uses `""` (empty string) as its "nothing to report" sentinel instead of `null`, and `extract_document` normalizes `""` back to `None` before it reaches any caller — so every consumer of `ExtractionResult.document_notes` sees exactly the same `str | None` shape Section 8.1 specifies; only the wire schema differs.
**Why this field:** it's the lowest-stakes field to give a placeholder sentinel to — it's prose for a human reviewer, not a value matching/validation logic ever reads, unlike a null SKU or null quantity which drive real downstream decisions.
**Related:** Section 8.1, Section 7.1, `packages/core/docflow_core/extraction.py`.

## D-023 — Confidence score schema: no `minimum`/`maximum` JSON Schema constraints

**Context:** A live call returned `400`: `output_config.format.schema: For 'number' type, properties maximum, minimum are not supported`. The current structured-outputs schema compiler doesn't support numeric range keywords.
**Decision:** Confidence fields are typed as plain `{"type": "number"}`; the 0.0–1.0 range is enforced by prompt instruction only, not the schema. The currency-inferred confidence cap (≤ 0.6, Section 7.1) is additionally enforced in code (`_apply_currency_confidence_cap`) regardless of what the model returns, so that one specific guarantee doesn't depend on the model following instructions.
**Related:** Section 7.1, Section 8.1.

## D-024 — Overall document confidence scoped to header fields only for this slice

**Context:** CLAUDE.md Section 7.1: "Overall document confidence is the minimum of required-field confidences, not an average." No per-tenant field schema (Section 7.13, "required/optional/hidden") exists yet — that's later-phase work — so there is no authoritative "required fields" list to minimize over yet.
**Decision:** `apps/worker/app/tasks/parse_and_extract.py:_overall_confidence` takes the minimum across all header-field confidences (all 10 are effectively required for a usable PO in this slice). Line-item confidences are stored per-line on `document_lines.confidence` and remain visible to a future review UI, but don't currently feed `documents.overall_confidence`. Revisit once Section 7.13's per-tenant schema exists and can say which fields are actually required for a given tenant/buyer.
**Related:** Section 7.1, Section 7.13.

## D-025 — Tier 2/3 handling in this slice: recognized-but-unsupported, not converted or richly messaged

**Context:** This slice's scope is Tier 1 only (native PDF/image/text/docx/xlsx). CLAUDE.md Section 7.11's full three-tier allowlist (LibreOffice conversion for `.doc`/`.xls`/`.tiff`/`.heic`/`.msg`, and detailed Tier 3 rejection messaging for archives/CAD/etc.) is out of scope per this task's own instructions.
**Decision:** `file_types.py` recognizes Tier 2 extensions (`.doc`, `.xls`, `.tif`/`.tiff`, `.heic`/`.heif`, `.msg`, `.odt`/`.ods`) by extension only (no conversion, no magic-byte table for them) so a rejection can at least name the format correctly via `DOC-004` ("recognized but not yet supported") rather than a generic "unknown format." True Tier 3 detection (archives, CAD, encrypted PDFs, etc.) collapses to the same `DOC-004` catalog entry for now rather than the full per-format catalog codes Section 7.11's table calls for — that richer messaging is follow-up work alongside the actual Tier 2 conversion worker.
**Related:** Section 7.11.

## D-026 — Password-protected PDF detection (`DOC-001`) not wired in this slice

**Context:** The task instructions explicitly allowed writing the `DOC-001` catalog entry now while treating detection as a follow-up "if it's cheap to add alongside your PDF handling, otherwise note it." `pdfplumber`'s handling of an encrypted PDF raises a library-specific exception during `pdfplumber.open()`; distinguishing "this is password-protected" cleanly from "this is corrupt" from the exception raised is a small but real feature, not a one-line add, and doing it hastily risks either false-positiving a merely-corrupt file as "password protected" or leaking a raw library exception to the user (forbidden by Section 7.16.5).
**Decision:** `DOC-001`'s catalog entry exists. `apps/worker/app/tasks/parse_and_extract.py`'s PDF-parsing path currently treats any `pdfplumber.open()`/extraction failure as a generic parse failure (`DOC-005`, "This file appears to be corrupted"), not specifically `DOC-001`. Wiring real password-protection detection is a named follow-up, not silently dropped.
**Related:** Section 7.16.5, `packages/core/docflow_core/errors.py`, `apps/worker/app/tasks/parse_and_extract.py`.

## D-027 — Inbound email provider: Postmark

**Decision:** Postmark's inbound webhook, not Resend. `docflow-tech-stack-costs.docx` left this undecided ("Resend or Postmark + inbound parsing").
**Why:** Postmark's inbound JSON payload shape is stable, well-documented, and already includes exactly what CLAUDE.md Section 7.16.3's abuse defenses need in one place: `Headers` (including `Authentication-Results`, where SPF/DKIM/DMARC verdicts live), `Attachments` (base64 content inline, no second fetch), and `MessageID`. The Postmark-specific parsing is isolated behind one function, `docflow_core.email_intake.parse_postmark_payload`, so switching providers later is a change to that one function, not to any abuse-defense logic downstream of it.
**Status:** No Postmark account exists yet. Creating one and pointing its inbound webhook at `POST /intake/email/{token}` is a manual step for later, once there's a real domain to receive mail at — this slice is fully testable with synthetic Postmark-shaped JSON payloads (see `apps/api/tests/test_email_intake.py`). See `SETUP.md` Step 5.
**Related:** Section 7.2, Section 7.16.3, `packages/core/docflow_core/email_intake.py`.

## D-028 — `documents.message_id` is indexed, not unique; true dedupe lives on `raw_emails`

**Context:** CLAUDE.md Section 7.8 requires Message-ID + attachment-hash dedupe, but `documents.message_id` must remain `NULL` for non-email documents, and one email can legitimately produce several `documents` rows (one per attachment) sharing the same `message_id`.
**Decision:** `documents` gets a plain (non-unique) index on `(tenant_id, message_id)` for query performance only. Real one-row-per-email dedupe is enforced where it actually belongs: a unique partial index on `raw_emails(tenant_id, message_id) WHERE message_id IS NOT NULL`, since `raw_emails` is one row per inbound email by construction. `docflow_core.email_intake.process_inbound_email` checks for an existing `raw_emails` row before doing anything else and returns an idempotent `"duplicate"` outcome if found, never reprocessing.
**Related:** Section 7.8, `supabase/migrations/0003_email_intake.sql`.

## D-029 — New `token_lookup` RLS policy + `token_lookup_session` for pre-tenant-context token resolution

**Context:** The webhook route must resolve a tenant from the URL's intake token before any `tenant_id` is known. Neither `tenant_session()` (needs `tenant_id` already) nor `platform_session()` applies — CLAUDE.md Section 7.15.1 explicitly restricts `platform_session()`/`admin_data_access` to `/admin/*` route handlers and the rollup job, and this webhook is neither.
**Decision:** Added a narrow `token_lookup` RLS policy on `intake_addresses` (`token = nullif(current_setting('app.intake_token', true), '')`) and a matching `docflow_core.db.token_lookup_session(token)` context manager, mirroring the existing `self_lookup` policy/`identity_lookup_session()` precedent built for auth-token→user resolution in Phase 0. It can only ever see the one `intake_addresses` row matching an exact token, nothing else — not a general-purpose bypass. `app.intake_token` was also added to `_reset_rls_settings()`'s defensive RESET list (D-016's lesson: never trust a pooled connection's ambient `app.*` state).
**Related:** Section 7.15.1, Section 7.5, `packages/core/docflow_core/db.py`, `supabase/migrations/0003_email_intake.sql`.

## D-030 — DMARC-policy-presence heuristic

**Context:** CLAUDE.md Section 7.16.3 says to quarantine "a DMARC fail, or an SPF fail specifically from a domain that has a DMARC policy," but this pipeline has no independent DNS lookup of a sending domain's real DMARC record — it only sees what the receiving mail server already wrote into `Authentication-Results`.
**Decision:** The presence of a `dmarc=` verdict at all in `Authentication-Results` (regardless of its value) is treated as evidence the domain has a DMARC policy the receiving server evaluated. If no `dmarc=` key is present, an SPF fail alone does not trigger quarantine (matches the "none/neutral results are processed normally" spirit for domains DocFlow has no DMARC signal for at all). Implemented in `should_quarantine_for_auth`.
**Related:** Section 7.16.3, `packages/core/docflow_core/email_intake.py`.

## D-031 — "Known sender" scoped to prior accepted `documents` rows only; buyer records don't exist yet

**Context:** CLAUDE.md Section 7.16.3 defines a known sender as one matching "an existing buyer or a prior approved document for this tenant." No buyer/customer records exist yet (Phase 2).
**Decision:** For this slice, `is_known_sender` checks only "has a prior `documents` row for this tenant with this `sender_email` and `status != 'quarantined'`." Revisit once Phase 2 lands buyer records, to also treat a sender matching a known buyer's email as known even before their first document.
**Related:** Section 7.16.3, Section 7.6 (deferred to Phase 2), `packages/core/docflow_core/email_intake.py`.

## D-032 — Unknown-sender-velocity counting query: "first-time sender in the trailing hour," not "any email-sourced document in the trailing hour"

**Context:** CLAUDE.md Section 7.16.3's rule is per-sender ("if unknown-sender documents... exceed the limit... further unknown-sender mail... is quarantined"), not a flat count of all email traffic — known senders must never be counted against it or affected by it.
**Decision:** `count_unknown_sender_documents_last_hour` counts `documents` rows created in the trailing hour whose sender had no earlier non-quarantined document at the time that row was created (a `NOT EXISTS` self-join on `sender_email` + `created_at <`), i.e. senders that were themselves unknown at the moment they were processed. A brand-new sender's very first document counts; a sender who already had an accepted document (at any point in the past, not just within the hour) never does, regardless of how many more emails they send.
**Related:** Section 7.16.3, `packages/core/docflow_core/email_intake.py`.

## D-033 — Message-ID dedupe runs first, before the numbered abuse-defense checks

**Context:** This slice's own spec lists the pipeline layers in an order that puts dedupe (step 6) after the no-attachment, attachment-cap, auth, and velocity checks (steps 2–5).
**Decision:** `process_inbound_email` checks for a duplicate `raw_emails` row immediately, before any other check or write. Running the numbered checks first on a retried webhook delivery would risk creating a second `raw_emails` row, a second set of quarantined/rejected documents, or double-counting toward the velocity window — exactly what "idempotent, don't reprocess" (Section 7.8) rules out. Deduping first is a strictly safer reading of the same requirement, not a shortcut.
**Related:** Section 7.8, Section 7.16.3, `packages/core/docflow_core/email_intake.py`.

## D-034 — Mixed-outcome `raw_emails.outcome` tri-state: "processed" wins over partial per-attachment rejection

**Context:** `raw_emails.outcome` is a three-value enum (`processed | quarantined | rejected`), but a single email can have some attachments accepted and others individually rejected for a bad format (e.g. one PDF and one `.zip` in the same email) — a case the tri-state doesn't perfectly capture, as this slice's own instructions anticipated.
**Decision:** `outcome = "processed"` if at least one attachment was accepted, regardless of how many others were individually rejected. `outcome = "rejected"` only when zero attachments were accepted (either because there were none, or every one failed `file_types.validate_upload`). `outcome = "quarantined"` applies to the whole email atomically (attachment-cap/auth-fail/velocity are email-level decisions, never per-attachment) and short-circuits per-attachment processing entirely. Per-attachment detail (which ones failed and why) is still fully recorded in `intake_rejections`, just not reflected in this one summary column.
**Related:** Section 7.16.3, `packages/core/docflow_core/email_intake.py`.

## D-035 — Quarantined attachments are stored without re-running `file_types.validate_upload`

**Context:** CLAUDE.md Section 7.16.4: quarantined items are "never sent to the model" — but this slice's spec doesn't ask for full Section 7.11 file-hardening (magic-byte/size/decompression-bomb validation) to run before *storing* a quarantined attachment, only before it's processed for extraction.
**Decision:** `_insert_quarantined_document` writes the attachment's raw bytes to storage as-is, without calling `validate_upload` first. This means a maliciously oversized or zip-bomb-shaped attachment could still be written to disk while quarantined (bounded only by whatever request-body size limit sits in front of the webhook, which this slice does not add). Full 7.11 hardening is applied at the moment a document actually moves toward extraction (`parse_and_extract`'s own re-validation), never before. Bounding quarantine-time storage more tightly is a named follow-up, alongside the Quarantine TTL cleanup job (Section 7.16.3/7.16.4), not silently dropped.
**Related:** Section 7.11, Section 7.16.4, `packages/core/docflow_core/email_intake.py`.

## D-036 — `docflow_core.email_intake` owns its own Celery producer, separate from `apps/api`'s and `apps/worker`'s

**Context:** `docflow_core` is shared by both `apps/api` and `apps/worker` and must not import from either app-specific package (there is no `apps.api` dependency direction into `packages/core`). The email-intake pipeline's orchestration (validate → store → create `documents` row → enqueue) needed to live in `docflow_core.email_intake` per this slice's own instructions, so it needs its own way to enqueue `docflow.parse_and_extract`.
**Decision:** `email_intake.py` defines its own bare `Celery(...)` producer (`email_intake.celery_client`), pointed at the same `REDIS_URL` broker and sending to the same named `"interactive"` queue via an explicit `queue=` argument on every `send_task` call — mirroring `apps/api/app/celery_client.py`'s own shape exactly, just as a second instance rather than a shared import. Tests monkeypatch `docflow_core.email_intake.celery_client` the same way `apps/api/tests/test_documents_upload.py` monkeypatches `app.routers.documents.celery_client`.
**Related:** Section 7.11, `packages/core/docflow_core/email_intake.py`, `apps/api/app/celery_client.py`.

## D-037 — `supabase/migrations/0003_email_intake.sql` also requires the founder's manual Supabase SQL Editor step

**Context:** Same constraint as D-017/D-013: the `docflow_app` role has no `CREATE` privilege on the `public` schema, so this migration (new `raw_emails` table, new `documents`/`intake_rejections` columns, new `intake_addresses` policy) cannot be applied programmatically.
**Decision:** `0003_email_intake.sql` is written and its shape is exercised by unit tests that don't need the live table (all of `packages/core/tests/test_email_intake.py`, plus the one `apps/api/tests/test_email_intake.py` case that only needs `intake_addresses`/`tenants` from 0001). The eight tests needing the real `raw_emails` table and new `documents` columns are gated on a new `requires_email_intake_schema` skip marker (`apps/api/tests/conftest.py`), mirroring `requires_documents_schema`, and currently report as skipped (confirmed live against `docflow-staging`: `SELECT 1 FROM raw_emails LIMIT 0` raises `UndefinedTable`), not passing. `SETUP.md` Step 5 is updated with the instruction to apply `0003_email_intake.sql` the same manual way as `0001`/`0002`. Re-run at the next checkpoint after that's done.
**Related:** Section 7.5, D-013, D-017, `SETUP.md` Step 5, `supabase/migrations/0003_email_intake.sql`.

## D-038 — Tier 2 conversion runs on local, mostly pure-Python converters; LibreOffice only for `.doc`

**Context:** Section 7.11 names "LibreOffice headless for office formats, a pinned image library for TIFF/HEIC, a pure-parser for `.msg`" — and Section 7.10/Section 10 forbid sending a customer document to any third-party conversion or OCR service. LibreOffice is **not installed on the founder's machine** (checked: no `soffice.exe` in either `Program Files` location, nothing on `PATH`).
**Decision:** Each Tier 2 format gets the smallest local converter that can do the job correctly, all inside `apps/worker/app/conversion.py` (never `apps/api`):

| Format | Converter | LibreOffice needed |
|---|---|---|
| `.tif`/`.tiff` | Pillow (libtiff present in this build), one image per page | no |
| `.heic`/`.heif` | `pillow-heif` | no |
| `.xls` | `xlrd` 2.x (pure-Python BIFF reader; 2.x reads `.xls` only) — see D-039 | no |
| `.odt`/`.ods` | zip + `defusedxml` parse of `content.xml` — see D-040 | no |
| `.msg` | `olefile` + a ~60-line MAPI property reader — see D-042 | no |
| `.doc` | LibreOffice headless `--convert-to docx` — see D-041 | **yes** |

**Why:** every one of these libraries is an attack surface that runs on hostile input, so the smallest dependency that is *correct* beats the most capable one. Only the legacy binary Word format genuinely has no sound pure-Python reader; hand-rolling a Word 97 FIB/piece-table extractor would risk silently wrong text feeding the model, which Section 7.1 makes the worst possible failure. A hosted converter was never an option.
**Related:** Section 7.11, Section 7.10, Section 10, `apps/worker/app/conversion.py`, `apps/worker/requirements.txt`.

## D-039 — `.xls` is read by `xlrd`, not converted by LibreOffice

**Decision:** Legacy Excel is read directly with `xlrd>=2.0.1` and rendered to text, exactly as `.xlsx` is rendered by `openpyxl`. No external process, no LibreOffice dependency, deterministic output.
**Why:** `xlrd` 2.x is pure Python, reads only cell records (it never touches a VBA/macro stream, satisfying "no active content, ever"), and removes `.xls` from the list of formats that break when LibreOffice is absent. Legacy `.xls` POs are common enough in this industry that making them depend on an optional binary would have been the wrong trade.
**Caveat (recorded deliberately):** BIFF stores every number as a binary double, so a cell printed as `47.50` arrives as the float `47.5`. `conversion.format_cell_value` renders it through `Decimal(repr(value))` in plain notation, so the model sees `47.5` (numerically identical, one less trailing zero) and never `47.500000000000004` or `4.75E+1`. No float ever reaches a money column — the conversion to `Decimal` still happens at the extraction boundary (Section 7.1). The same renderer is now used for `.xlsx`, which previously used `str(cell)`.
**Related:** Section 7.1, Section 7.11.

## D-040 — `.odt`/`.ods` are parsed directly as zip + XML, not converted

**Decision:** OpenDocument files are read in-process: the zip has already passed the Section 7.11 bomb/traversal/encryption/XXE checks in `file_types`, and `content.xml` is then parsed with `defusedxml` (DTD processing and external entity resolution off by construction). Table rows are rendered as comma-joined lines so a PO's line items survive.
**Why:** ODF is an open, documented zip+XML format; routing it through a 400 MB office suite would add the largest CVE surface in the stack for no gain, and would make `.odt` fail on a machine where `.doc` already fails.
**Related:** Section 7.11.

## D-041 — `.doc` requires a local LibreOffice install; without it the document fails cleanly with `DOC-017`

**Context:** LibreOffice is not installed on the current machine, and installing it silently is not this slice's call to make.
**Decision:** The conversion path is implemented in full (`conversion.convert_with_libreoffice`: isolated throwaway user profile via `-env:UserInstallation`, fixed argv with a server-generated filename, no stdin, `--headless --invisible --norestore --nolockcheck --nodefault`, a `LIBREOFFICE_TIMEOUT_SECONDS = 120` wall-clock kill, output read from a temp dir that is deleted after). `conversion.find_libreoffice()` looks at the new `LIBREOFFICE_PATH` setting, then `PATH`, then the usual install locations. When it finds nothing, a `.doc` document fails with catalog code `DOC-017` ("We couldn't convert this older file" — with the fix: re-save as .docx or PDF), the founder-facing half of which says what is actually wrong. **Nothing is faked and nothing degrades silently.**
**Founder action item:** install LibreOffice (`winget install TheDocumentFoundation.LibreOffice`, or the Linux package on the deployed worker) and, if it lands somewhere unusual, set `LIBREOFFICE_PATH`. Until then, a buyer sending a legacy `.doc` gets a clear, catalog-coded failure telling them to send `.docx` or PDF instead — the one Tier 2 format that does not work end-to-end. Recorded in `SETUP.md` Step 7.
**Note:** RTF saved with a `.doc` extension (which Word does often) is detected by content and reads fine without LibreOffice — see D-043.
**Related:** Section 7.11, Section 7.16.5, `SETUP.md`, `packages/core/docflow_core/config.py`.

## D-042 — `.msg` read with `olefile` plus an in-repo MAPI property reader, not `extract-msg`

**Context:** `extract-msg` is the obvious "pure parser for `.msg`", but it pulls in `oletools`, `RTFDE`, `lark`, `beautifulsoup4`, `compressed-rtf`, `msoffcrypto-tool`, `easygui` and `win_unicode_console` — eight further parsers (and a GUI toolkit) into the one process that is deliberately locked down because it handles hostile input.
**Decision:** `.msg` is read with `olefile` (a single pure-Python module that reads named streams out of an OLE container and executes nothing) plus ~60 lines in `conversion.unwrap_msg` that pull the four MAPI properties actually needed: subject `0037`, body `1000`, sender `0C1F`, and per-attachment long filename `3707` and data `3701`. Unsupported/embedded-message attachments are skipped, not guessed at.
**Why:** dependency surface is the risk being managed here, and Section 7.11 makes keeping these parsers patched an ongoing operational duty — eight fewer of them is eight fewer CVE clocks. If a real customer `.msg` ever needs a property this reader doesn't handle, revisiting `extract-msg` is a one-file change.
**Test cost, recorded honestly:** no Python library *writes* `.msg`, so `apps/worker/tests/fixture_builders.py` contains a minimal MS-CFB writer (every stream padded past the 4096-byte mini-stream cutoff so no mini-FAT is needed). Its output is verified by round-tripping through `olefile` in the tests themselves. It is test-only code and is never imported by the worker.
**Related:** Section 7.11, `apps/worker/app/conversion.py`, `apps/worker/tests/fixture_builders.py`.

## D-043 — OLE and container formats are classified by content, inside the allowlist module, without a parser

**Context:** `.doc`, `.xls` and `.msg` share one magic-byte signature (the OLE compound-file header), so magic bytes alone cannot tell them apart — and `file_types` must not become a parser, since it runs in the web process.
**Decision:** `file_types._classify_ole` scans the raw bytes for the UTF-16LE stream names that appear in an OLE file's directory sectors: `__substg1.0_` → `.msg`, `EncryptedPackage` → encrypted Office (`DOC-013`), `WordDocument` → `.doc`, `Workbook`/`Book` → `.xls`, `PowerPoint Document`/`Visio` → `DOC-004`. Order matters and is asserted by a test: a `.msg` carrying an `.xls` attachment contains that attachment's own directory bytes, so the `.msg` marker is checked first. Two further content-level rules were added at the same layer: `.rtf` is accepted when a file claims `.doc` (Word writes RTF as `.doc` often enough that refusing it would reject a readable purchase order), and `.xlsx` content is accepted when a file claims `.xls`. A `.exe` renamed `.pdf` still matches nothing and is refused.
**Why:** this is signature matching, not parsing — no library opens the file, and the work is bounded by the 25 MB size cap that already ran.
**Related:** Section 7.11, Section 10, `packages/core/docflow_core/file_types.py`.

## D-044 — Page-count and pixel caps: `MAX_DOCUMENT_PAGES = 100`, `MAX_IMAGE_PIXELS = 50M`, `MAX_IMAGE_DIMENSION = 20000`

**Context:** Section 7.11 requires a PDF page-count cap, the same cap on multi-page TIFF, and pixel-dimension caps before decode. **None of these existed before this slice** — the Tier 1 PDF path had no page limit at all, so this retroactively closes a Tier 1 gap as well as enabling the Tier 2 TIFF row.
**Decision:** all three are named constants in `file_types` (one definition, read by both the PDF and the TIFF path). 100 pages matches the extraction model's own per-document page limit, so a document above it could not be extracted anyway; 50 MP is roughly 4x a 600-dpi A4 scan and 4x a modern phone photo. Enforcement lives where the number can actually be known: the page cap when the parser/converter opens the document (`DOC-016`), the pixel caps before any decode (`DOC-018`), with `Image.MAX_IMAGE_PIXELS` set explicitly rather than left at Pillow's default.
**Related:** Section 7.11, `packages/core/docflow_core/file_types.py`, `apps/worker/app/conversion.py`.

## D-045 — XXE defense is a pre-parse scan in the allowlist module, because the XML library's defaults are not enough

**Context:** Section 7.11 says to parse "with external entity resolution and DTD processing disabled (`defusedxml` or the language equivalent)". Verified on this codebase's pinned versions rather than assumed: `openpyxl` reads through `lxml`, and **lxml's default parser expands internal entities** (`<!DOCTYPE r [<!ENTITY x "BOOM">]><r>&x;</r>` returns `BOOM`). Relying on library defaults would therefore have left `.xlsx` exposed.
**Decision:** `file_types._scan_zip_xml_for_entities` reads a bounded prefix (16 KB, 8 MB total budget) of every `.xml`/`.rels` entry in a zip-based format *before* any XML library sees it and refuses the file with `DOC-015` if it declares a DTD or an entity — a DTD subset must precede the document element, so a prefix scan finds every declaration there can be. `.odt`/`.ods` are additionally parsed with `defusedxml` in the worker. An `.html` file's `<!DOCTYPE html>` is unaffected (it is not zip XML and is never parsed as XML) — there is a test for exactly that.
**Boundary call:** reading a bounded, size-capped prefix of a zip entry in the web process is treated as *validation*, in the same category as magic-byte sniffing — no XML parser, no office library, no unbounded decompression. The existing Tier 1 code already read the ODF `mimetype` entry this way. All real parsing still happens only in the worker.
**Related:** Section 7.11, Section 10, `packages/core/docflow_core/file_types.py`.

## D-046 — Tier 3 gets per-case catalog codes (`DOC-010`…`DOC-019`); supersedes D-025 and closes D-026

**Context:** D-025 deliberately collapsed every unsupported format into `DOC-004`, and D-026 left `DOC-001` (password-protected PDF) written but unwired. Section 7.11 requires "Tier 3 entries each get a catalog code" and that "every rejection names the format and the fix."
**Decision:** new catalog entries, each naming the format and the fix: `DOC-010` archives (`.zip`/`.rar`/`.7z` — never auto-extracted, which is a decompression-bomb vector and an ambiguous request), `DOC-011` Apple iWork, `DOC-012` CAD/EDI, `DOC-013` encrypted/password-protected non-PDF files, `DOC-014` "matched no signature on the allowlist at all" (the renamed-`.exe` case, which previously returned `DOC-004`), `DOC-015` unsafe XML, `DOC-016` too many pages, `DOC-017` Tier 2 conversion failed, `DOC-018` image too large to decode, `DOC-019` attachment nested more than one level deep. `DOC-004` is retained with its code stable but re-scoped and reworded to what it now means: an Office format DocFlow doesn't read (PowerPoint, Visio, an unrecognized OLE container) — it no longer says "not yet supported", because Tier 2 now is.
**`DOC-001` is now wired** (closing D-026), twice: `file_types.pdf_looks_encrypted` matches an `/Encrypt` reference in the PDF trailer (a bounded tail scan, no document opened), and the worker's PDF path maps `pdfminer`'s `PDFPasswordIncorrect` to the same code for encrypted PDFs whose trailer sits in a compressed xref stream. Never brute-forced, never passed to the model.
**Related:** Section 7.11, Section 7.16.5, D-025, D-026, `packages/core/docflow_core/errors.py`.

## D-047 — `.rtf` and `.eml` added to the Tier 1 allowlist (they were missing, and Section 7.11 lists both)

**Context:** Section 7.11's Tier 1 row includes `.rtf` and `.eml`; the first Tier 1 slice implemented neither, and neither was recorded as a gap. Section 10 forbids rejecting a format that is on the allowlist, so this was a live defect, not a future feature.
**Decision:** both are now on the allowlist and handled. `.rtf` is detected by its `{\rtf` signature and read by a small in-repo reader (`_extract_rtf_text`) that walks control words and drops metadata/picture/object destination groups — a reader, not an interpreter, so there is no path that touches an embedded object. `.eml` is detected by its header block and unwrapped with the standard library's `email` parser, sharing the exact one-level attachment bound as `.msg`.
**Why in this slice:** `.eml` unwrapping is the same code path as `.msg` (which is in scope), and an unimplemented allowlist row is a purchase order a customer has to key by hand — the failure the product exists to prevent.
**Related:** Section 7.11, Section 10.

## D-048 — What the model receives after conversion: one `<document>` envelope, normalized images, body *and* attachments

**Decision:** three shape choices, all made once in `build_content_blocks_for_artifacts` / `conversion`:
1. **One envelope.** However many Tier 1 artifacts a file becomes (a 3-page TIFF → 3 images; a `.msg` → body + attachment), they are wrapped in a single `<document>…</document>` pair via the new `extraction.wrap_document_content`. Several separate envelopes would read to the model as several separate documents. *No change was made to `SYSTEM_PROMPT`, `RESPONSE_SCHEMA`, `SCHEMA_VERSION` or `EXTRACTION_MODEL` — `prompt_hash` is unchanged, so no new live golden run is triggered by this slice.*
2. **Converted images are normalized:** RGB JPEG, quality 90, long edge capped at 1568px (the largest edge the model uses before downsampling anyway). Without this, a converted 600-dpi fax page could exceed the 25 MB cap and be refused by the re-validation step — a legitimate document lost to our own converter. The original file in storage is never modified.
3. **A `.msg`/`.eml` contributes its body text *and* its attachments,** per Section 7.11's wording ("the body is read as text and every attachment is re-validated"), rather than picking one. An attachment that fails re-validation is dropped and logged by code (never by content); if nothing readable survives, the document fails with the first rejection's code. Attachments are capped at `MAX_EMBEDDED_ATTACHMENTS` (10). **Open item for Phase 2:** an email carrying two *different* POs will currently be extracted as one document; splitting one upload into several documents needs a `documents`-level relationship that doesn't exist yet (email intake already splits per attachment, so this only affects a `.msg`/`.eml` uploaded as a file).
**Related:** Section 7.2, Section 7.11, Section 5 (golden-run trigger), `packages/core/docflow_core/extraction.py`.

## D-049 — Per-file resource limits: wall-clock for the subprocess converter, caps for the in-process ones

**Context:** D-003 describes per-file subprocess limits (`resource.setrlimit`, SIGKILL timeout, one subprocess per file). The Tier 2 converters added here are mostly in-process Python, and `resource.setrlimit` does not exist on Windows.
**Decision:** the LibreOffice converter gets a real wall-clock kill (`LIBREOFFICE_TIMEOUT_SECONDS`) and a throwaway working directory per file. The in-process converters are bounded instead by the limits that actually stop the known attacks — the 25 MB size cap, the zip entry-count/ratio/total-size caps, the page-count cap, the pixel caps, and Pillow's explicit `MAX_IMAGE_PIXELS` — plus, in production, Celery's own task time limit and the worker service's memory ceiling from D-003.
**What this gives up:** a pathological file that is small, low-page-count and still slow to parse would occupy one worker slot until Celery's task timeout, rather than being killed per-file. Named here rather than silently substituted; revisit if a real document ever hits it.
**Related:** Section 7.11, D-003.

## D-050 — `.doc` is the one format with no positive fixture, because generating one needs LibreOffice too

**Context:** Section 7.11 requires "a positive fixture for every Tier 1 and Tier 2 format — a real PO in each."
**Decision:** every Tier 1 format and every Tier 2 format except `.doc` has a programmatically generated PO fixture (`apps/worker/tests/fixture_builders.py`) that is asserted to reach extraction. For `.doc`, nothing on this machine can *write* a legacy binary Word file either, so the end-to-end test is marked `requires_libreoffice` and currently skips, with the skip reason naming D-041. What is still tested without LibreOffice: that a `.doc` is detected and accepted by the allowlist, that a malformed one fails cleanly with `DOC-017`, and that a missing LibreOffice degrades to `DOC-017` rather than crashing. **No fake `.doc` file was committed** — a fixture that doesn't exercise the path is worse than a visible skip.
**Related:** Section 7.11, D-041.

## D-051 — The buyer table is `buyers`, not `customers`; Section 9's `learned_rules.customer_id` becomes `buyer_id`

**Context:** `docflow-database-schema.docx` calls the table that holds the businesses sending purchase orders `customers`. CLAUDE.md and the master build prompt call them *buyers* everywhere it matters (Section 7.6 "Buyer auto-creation", Section 7.13's `buyer_alias` rule type, Section 7.15.2 Step 5's `/admin/tenants/{id}/buyers/import`), while Section 9's `learned_rules` spec still says `customer_id`. Two documents, two names, one table.
**Decision:** `buyers`, used consistently for the table, every foreign key (`buyers.id`, `document_headers.buyer_id`, `learned_rules.buyer_id`, `buyer_merge_candidates`), and every function and variable in `docflow_core.buyers`. Section 9's `customer_id` is implemented as `buyer_id`; the spelling is the only thing that changed, the column's meaning and the `UNIQUE (tenant_id, <buyer>, rule_type, match_key)` constraint are exactly as specified.
**Why:** "customer" is genuinely ambiguous in this product and would stay ambiguous in every query written for the rest of the build. DocFlow's customers are the tenants (the distributors who pay for it); the tenants' customers are the buyers who send them POs. A `customers` table that means the second thing sits one join away from a `tenants` table that means the first, and `SELECT ... FROM customers WHERE tenant_id = ...` reads as if it answers the wrong question. CLAUDE.md is also the authority the build follows when documents disagree (Section 0 rule 8), and it says buyers.
**Pending document update (not a question for the founder, per Section 0 rule 8's logging requirement):** `docflow-database-schema.docx` should be updated to rename `customers` → `buyers`, and Section 9's `learned_rules.customer_id` → `buyer_id`, so the schema doc and the code stop disagreeing.
**Related:** Section 7.6, Section 7.13, Section 9, `docflow-database-schema.docx`, `supabase/migrations/0004_matching_foundations.sql`, `packages/core/docflow_core/buyers.py`.

## D-052 — `learned_rules` is created fresh; the "keep `customer_item_mappings` as a view or migrate it" question is moot

**Context:** Section 9 says "Keep `customer_item_mappings` as a view or migrate it into this table — propose which", and D-006 already chose "drop it outright".
**Decision:** neither, because there is nothing to keep or migrate: `customer_item_mappings` exists only in `docflow-database-schema.docx`. It was never created by `0001`, `0002` or `0003`, so no table, no data and no dependent code exists. `0004` creates `learned_rules` as a new table and that closes the question; D-006 stands, with this as the concrete confirmation rather than a forward-looking intent.
**Related:** Section 9, D-006, `supabase/migrations/0004_matching_foundations.sql`.

## D-053 — `learned_rules` uniqueness is two partial indexes, because NULL `buyer_id` defeats a single one

**Context:** Section 9 specifies `UNIQUE (tenant_id, customer_id, rule_type, match_key)`, and `buyer_id IS NULL` is the meaningful value that marks a tenant-wide rule (a `uom_alias` is explicitly tenant-level per Section 7.13).
**Decision:** two partial unique indexes instead of one constraint — `(tenant_id, buyer_id, rule_type, match_key) WHERE buyer_id IS NOT NULL AND deleted_at IS NULL` and `(tenant_id, rule_type, match_key) WHERE buyer_id IS NULL AND deleted_at IS NULL`.
**Why:** Postgres treats NULLs as distinct in a unique index, so the single constraint as written would enforce nothing at all for tenant-wide rules — unlimited duplicate `uom_alias` rows for the same `match_key` could exist, and "which one wins" would become a silent correctness question at apply time. The `deleted_at IS NULL` predicate is the same treatment `items` and `buyers` get: a soft-deleted rule must not block re-creating the same rule later. Same pattern as the existing `idx_users_tenant_email` / `idx_users_email_global` pair from `0001` (D-004).
**Related:** Section 9, Section 7.13, D-004.

## D-054 — Two buyer-name normalizations: a match key that keeps legal suffixes, a comparison key that drops them

**Context:** Section 7.6 requires both "the same buyer written slightly differently is the same record" and "a near-duplicate is flagged, never merged". One normalization cannot do both: whatever it collapses becomes an automatic merge, and whatever it keeps becomes a duplicate record.
**Decision:** `normalize_buyer_name` produces the **match key** (lowercase, apostrophes removed, all other punctuation to spaces, whitespace collapsed) and is what decides identity — it is stored on `buyers.normalized_name` and carries the partial unique index. `buyer_similarity_key` additionally strips trailing legal suffixes (`inc`, `llc`, `ltd`, `corp`, `co`, `gmbh`, …) and is used **only** for similarity scoring.
**Why the match key deliberately keeps suffixes:** "Acme Test Inc" and "Acme Test LLC" can be two genuinely different legal entities, and in a distributor's buyer list often are. Stripping the suffix at the identity layer would silently file one company's POs under another's — an auto-merge by another name, which Section 10 forbids outright. Keeping them means two rows plus a flagged candidate scoring 1.0000, which is exactly the outcome Section 7.6 asks for: surfaced, scored, and left to the human.
**Apostrophes are deleted, not spaced:** "Bella's" and "Bellas" are one word spelled two ways; turning the apostrophe into a space produces the token pair `bella s`, which scores *worse* against both spellings than either does against the other.
**Related:** Section 7.6, Section 10, `packages/core/docflow_core/buyers.py`.

## D-055 — `rapidfuzz` in `packages/core`; `token_sort_ratio` with a 0.88 flagging threshold, measured not guessed

**Decision:** `rapidfuzz>=3.9` (pinned `3.14.6` in both lock files) is a dependency of `packages/core`, not of the worker alone, because the same one scoring function is called by the worker today and by the matching/Console code in the following slices; a second copy of a similarity rule is how two parts of the product start disagreeing about what "the same buyer" means. `NEAR_DUPLICATE_THRESHOLD = 0.88` (a `Decimal`), scorer `fuzz.token_sort_ratio` over the comparison keys from D-054.
**Why these two choices, with the numbers that produced them** (measured on the real implementation before the threshold was picked, not assumed):

| Pair | score | flagged at 0.88 |
|---|---|---|
| `Bella's Coffee House` / `Bella's Coffee House, LLC` | 1.00 | yes — correct |
| `Bella's Coffee House` / `Bellas Coffee House Inc` | 1.00 | yes — correct |
| `Acme Test Distributor` / `Acme Test Distributors` | 0.98 | yes — correct |
| `Northwind Test Supply Co` / `Northstar Test Supply Co` | 0.81 | no — correct, different companies |
| `Bella's Coffee House` / `Bella's Tea House` | 0.77 | no — correct, different companies |
| `Acme Test Distributor` / `Acme Test Manufacturing` | 0.59 | no — correct |
| `Bella's Coffee House` / `Riverbend Test Hardware` | 0.33 | no — correct |

`token_sort_ratio` (word order irrelevant, changed words still penalized) was chosen over `token_set_ratio`, which scores `Acme Test Distributor` vs `Acme Test Distributor - West Branch` at 1.00 — a containment match that would flag every buyer whose name is a prefix of another's. The cost of that choice, recorded honestly: a genuine branch/division variant scores 0.78 and is **not** flagged. That is the deliberate direction to err in — an unflagged pair is two correct records the founder can still merge manually, while a stream of false pairs is how a wrong merge eventually gets clicked.
**This is a flagging threshold, never an auto-apply threshold.** There is no score at which any code path merges two buyers (Section 7.6, Section 10).
**Related:** Section 7.6, Section 10, `packages/core/docflow_core/buyers.py`, `packages/core/tests/test_buyers.py`.

## D-056 — Contact email outranks name for identification; a matched buyer's empty email is filled, never overwritten

**Context:** this slice's brief asks how an exact `buyer_contact_email` match is weighed against a name match.
**Decision:** three rules. (1) An exact, case-insensitive `contact_email` match on a live buyer wins outright — it is checked first, and when it hits, no name comparison happens and nothing is created. (2) A buyer matched by name whose `contact_email IS NULL` has it filled in from the document; a buyer that already has one is never overwritten (Section 10: "never overwrite a human correction with a machine value" — this module cannot tell a founder-entered address from a machine-derived one, so it treats every existing value as if a human put it there). (3) An email alone never creates a buyer: `buyers.name` is `NOT NULL` and a name is not something this module may invent (Section 7.1).
**Why email first:** an ordering address is a unique, machine-generated string that a buyer's systems reproduce exactly, while the company name printed on a PO is free text that varies between the buyer's own documents. When the two disagree, the email is the stronger evidence.
**Deliberate omission, recorded rather than built:** when the email matches buyer X but the name normalizes onto a *different* existing buyer Y, this slice links to X and does **not** open a merge candidate for the X/Y pair. It is a real signal, but the two names may score arbitrarily low against each other, and flagging a pair whose stored `similarity_score` doesn't explain the flag would make the founder's merge queue harder to trust, not easier. Revisit with the Console's merge screen (Phase 5), where the flag can carry its own reason.
**Related:** Section 7.1, Section 7.6, Section 10, `packages/core/docflow_core/buyers.py`.

## D-057 — `deleted_at` added to Phase 1's tables too; append-only tables deliberately excluded

**Context:** Section 9 says "Add `deleted_at` to every business table for soft delete." Phase 1's tables (`documents`, `document_headers`, `document_lines`, `intake_rejections`, `raw_emails`, `intake_addresses`) were created before that instruction was in scope.
**Decision:** `0004` adds `deleted_at` to all of them alongside the new tables, so the rule holds uniformly rather than only for tables created after it was noticed. `extraction_runs` is excluded because Section 9 defines it as append-only — the same treatment `admin_actions`, `tenant_lifecycle_events` and `platform_admins` already get in `0001`; a soft-delete column on an audit log invites exactly the edit the log exists to prevent.
**Recorded honestly:** nothing sets these columns yet and no query filters on them, so behavior is unchanged today. The filter goes in with the slice that builds a delete path (Section 7.10's soft-delete-everywhere rule), and the `buyers`/`items`/`learned_rules` queries written in this slice already filter `deleted_at IS NULL` because their soft-delete semantics are load-bearing from the start (a retired SKU, a disabled rule).
**Related:** Section 7.10, Section 9, `supabase/migrations/0004_matching_foundations.sql`.

## D-058 — Buyer identification runs in its own transaction, after the extraction transaction commits

**Context:** `parse_and_extract` writes `documents`, `document_headers` and `document_lines` in one `tenant_session()` transaction. Buyer identification could have joined it.
**Decision:** it runs in a second `tenant_session()` immediately afterwards, wrapped in a `try/except` that logs and returns.
**Why:** extraction is the expensive, irreplaceable work — one model call the tenant has already paid for. A failure while creating a buyer (a unique-index race, a transient pooler error) inside the same transaction would roll back the header and lines with it and cost the document its extraction. Failing separately leaves the document exactly as `needs_review` with `buyer_id IS NULL`, which is the same, entirely valid state a document with no extracted buyer name has (Section 7.1: every field is nullable); a re-run links it. Nothing downstream in this slice depends on the link existing.
**Logging:** the handler logs `document_id` and `type(exc).__name__` only — never `str(exc)`. A SQLAlchemy/psycopg error message can carry the bound parameters, which here are a buyer's name and email, and Section 7.10 forbids customer data in application logs.
**Related:** Section 7.1, Section 7.6, Section 7.10, `apps/worker/app/tasks/parse_and_extract.py`.

## D-059 — `field_provenance` is written now, with `extracted` for every present field; `buyer_id` gets no provenance entry yet

**Decision:** `0004` adds `field_provenance` JSONB to `document_headers` and `document_lines`, and `parse_and_extract` populates it on write: every field the model returned a non-null value for is recorded as `extracted`. A field the model returned as null has no value and therefore no provenance entry. `learned_rule:<id>` and `human_edit:<review_action_id>` are written by the slices that produce them (matching, review).
**Why populate it in this slice at all:** an always-empty provenance column is indistinguishable from a broken one, and Section 7.1 requires provenance "for every value" — the base case is the one that has to be right before the interesting cases are added.
**`document_headers.buyer_id` deliberately has no provenance entry:** it is not an extracted field. It is the output of deterministic matching, and its provenance vocabulary (which buyer rule or match produced it) belongs with the matching slice that introduces the other match types. `BuyerIdentification.matched_on` already carries that information in-process; it simply has nowhere to be stored yet.
**Related:** Section 7.1, Section 9, `apps/worker/app/tasks/parse_and_extract.py`.

## D-060 — Near-duplicate flags live in a `buyer_merge_candidates` table, with the new buyer always on the left

**Context:** Section 7.6 requires near-duplicate buyer names to be "flagged for founder merge" but names no table.
**Decision:** a tenant-scoped, RLS'd `buyer_merge_candidates` table: `buyer_id` (always the newly created buyer), `existing_buyer_id` (the older one it resembled), `similarity_score NUMERIC(5,4)`, `status` (`open` | `dismissed` | `merged`), `detected_from_document_id`, and the resolver/resolved-at columns the Phase 5 merge action will fill. A partial unique index on `(tenant_id, buyer_id, existing_buyer_id) WHERE deleted_at IS NULL` plus `ON CONFLICT DO NOTHING` means re-processing the same document never re-flags a pair a founder already dismissed.
**Why a table rather than a boolean or a flag on `buyers`:** a near-duplicate is a property of a *pair*, and one new name can resemble several existing ones. A column on `buyers` could not hold the score, could not hold more than one candidate, and would have nowhere to record that a founder looked at it and said no.
**Fixed pair order, not a `LEAST/GREATEST` index:** candidates are only ever created at the moment the newer buyer is created, so the order is already deterministic and a plain column-pair index enforces uniqueness without an expression index over functions whose immutability would need checking.
**Scope:** flagging only. Executing a merge (re-pointing foreign keys in a transaction, logged) is Phase 5 Console work and is explicitly not built here — `status` has the `merged` value it will one day be set to, and nothing sets it.
**Similarity score is `NUMERIC`, bound as a string:** it is a stored, founder-visible number, so it gets the same no-float discipline as money (Section 7.1) rather than an exception carved out for it.
**Related:** Section 7.6, Section 10, `supabase/migrations/0004_matching_foundations.sql`.

## D-061 — `items`, `learned_rules` and `extraction_runs` are created in `0004` but written by nothing in this slice

**Context:** Section 0 rule 3 (work phase by phase, never run ahead) versus the practical fact that the founder applies each migration by hand in the Supabase SQL Editor (D-013/D-017/D-037).
**Decision:** all of Phase 2's tables land in one migration, but only `buyers` and `buyer_merge_candidates` have code that writes to them in this slice. `items` (catalog), `learned_rules` (Section 7.13's rule family) and `extraction_runs` (append-only per-model-call log), plus `documents.current_extraction_run_id` and the three duplicate/change-order columns, are created and left empty for the slices that own them — SKU matching, then duplicate/change-order detection.
**Why not split them across three migrations:** each additional migration is another manual apply-and-verify round-trip for a solo founder, and the schema is far easier to review as one coherent object graph (`learned_rules.buyer_id` → `buyers`, `extraction_runs.document_id` → `documents`) than as three partial ones. No code path can use an empty table by accident, and the tables' emptiness is asserted by nothing behaving differently.
**Related:** Section 0 rule 3, Section 7.13, D-037.

## D-062 — `0004_matching_foundations.sql` needs the founder's manual Supabase SQL Editor step; 8 tests skip until then

**Context:** identical to D-017/D-037 — `DATABASE_URL` connects as `docflow_app`, which has no `CREATE` privilege on `public` by design, so RLS is genuinely enforced (D-013).
**Decision:** the migration is written but **not applied**. The eight database-dependent buyer tests in `apps/api/tests/test_buyers.py` are gated on a new `requires_matching_schema` marker (`apps/api/tests/conftest.py`, checking `buyers`, `buyer_merge_candidates` and `document_headers.buyer_id`) and currently report as **skipped**, not passing. The 16 normalization/scoring tests in `packages/core/tests/test_buyers.py` need no database and pass now.
**What is therefore unverified until the founder applies it:** the SQL itself (syntax, the `ON CONFLICT (…) WHERE deleted_at IS NULL` inference against the partial unique indexes, the new RLS policies), and every runtime behavior that needs real rows — auto-creation, re-link on second sighting, the merge-candidate flag, the email match, the null-name case, and the Section 7.5 cross-tenant isolation assertion. Re-run all three suites at the next checkpoint once `0004` is applied.
**Related:** Section 7.5, D-013, D-017, D-037, `SETUP.md` Step 5.
**Closed:** `0004` was applied to `docflow-staging` before the SKU-matching slice; all eight tests now pass.

## D-063 — Matching results live in new columns on `document_lines`, not a new table; the catalog is loaded once per document

**Context:** Section 7.6 requires that a fuzzy match "always surface candidate + score" and that a sub-threshold result be recorded as a suggestion. `document_lines` (0002) had nowhere to put any of that, so this slice needs `supabase/migrations/0005_sku_matching.sql`.
**Decision:** seven columns on `document_lines` and no new table: `matched_item_id`, `match_method`, `match_score`, `matched_uom`, `uom_mismatch`, `match_candidates` (jsonb), `matched_at`, plus a `CHECK ((matched_item_id IS NULL) = (match_method IS NULL))` so a row can never claim a method without a match. `document_lines` already carries `tenant_id` and both RLS policies from 0002, so this adds no new isolation surface — a separate `line_matches` table would have needed its own `tenant_id`, its own policies and a join on every read, for a strictly 1:1 relationship.
**`match_candidates` is deliberately independent of `matched_item_id`.** A line with five scored candidates and no match is the *normal* below-threshold outcome, not an error state, and the schema has to be able to say that.
**Catalog loading — one query per document, scored in Python.** `load_catalog` reads the tenant's live items once per document and `rapidfuzz` scores every line against them in memory, rather than pushing a normalized comparison into SQL. **Why:** the normalization is the correctness-critical part (D-066, D-068), and having one Python implementation that the DB-free unit tests exercise directly is worth more than an index-backed exact lookup whose SQL expression could silently drift from it. **The cost, measured honestly:** a 50,000-row catalog times ~10 lines is ~500k short-string comparisons per document — well under a second, and a rounding error next to the model call that precedes it. If a tenant's catalog ever makes this matter, the exact-SKU step is the one to push into SQL first, behind the existing `idx_items_tenant_sku`.
**Related:** Section 7.5, Section 7.6, `supabase/migrations/0005_sku_matching.sql`, `packages/core/docflow_core/matching.py`.

## D-064 — A learned mapping raises the *match* score to a fixed 0.99; it never touches the line's extraction confidence

**Context:** Section 7.6 says "Applying a learned mapping raises confidence to a high fixed value." `document_lines` has two numbers that could be called confidence: `confidence` (the model's per-line extraction confidence, from 0002) and the new `match_score`.
**Decision:** `LEARNED_RULE_MATCH_SCORE = Decimal("0.9900")` is written to `match_score`. `document_lines.confidence` is never modified by this module.
**Why:** the two numbers answer different questions. `confidence` answers "did the model read this line off the page correctly"; `match_score` answers "is this the right catalog item". A human confirming a mapping is evidence about the second and no evidence at all about the first — raising `confidence` on the strength of it would hide a poorly-scanned line behind a confident mapping, which is the precise direction Section 7.1 forbids erring in. It would also be a machine value overwriting a model output, which nothing in this codebase does.
**Why 0.99 and not 1.00:** `EXACT_MATCH_SCORE = 1.0000` is reserved for "the printed SKU and the catalog SKU are the same string", which is an identity. A human-confirmed mapping is the strongest evidence this system has about *meaning*, but it is still a person's assertion about a buyer's wording and can be wrong. Keeping the two distinguishable costs nothing and keeps "certain" meaningful. Precedence between the two is decided by the pipeline order in Section 7.6, not by comparing these numbers, so the gap has no behavioral effect — it is there to be read.
**Related:** Section 7.1, Section 7.6, `packages/core/docflow_core/matching.py`.

## D-065 — A buyer-scoped learned rule beats a tenant-wide one for the same key

**Context:** Section 7.13 makes rules "tenant-scoped, optionally buyer-scoped" and 0004 encodes tenant-wide as `buyer_id IS NULL`, but nothing states which wins when both exist for one `match_key`. This slice has to pick.
**Decision:** the buyer-scoped rule wins; the tenant-wide rule is the fallback. Implemented by ordering the single rule query `ORDER BY (buyer_id IS NULL)` and building the lookup with `setdefault`, so the specific rule is seen first and the general one cannot displace it.
**Why:** the alternative makes a whole feature unreachable. A tenant-wide rule exists to say "in this business, this wording means that SKU"; a buyer-scoped rule exists to say "except when *this* buyer says it". If general beat specific, no exception could ever be expressed, and the founder's only way to record one would be to delete the general rule — losing a human confirmation to record another. Specific-over-general is also the only ordering under which adding a rule can never change the behavior of an existing, narrower one.
**A document whose buyer could not be identified is not a special case:** the predicate `(buyer_id IS NULL OR buyer_id = CAST(:buyer_id AS uuid))` evaluates the second branch to NULL when no buyer is bound, so such a document gets the tenant-wide rules alone and no other buyer's. Matching therefore never depends on buyer identification having succeeded.
**Related:** Section 7.6, Section 7.13, D-053, `packages/core/docflow_core/matching.py`.

## D-066 — `token_sort_ratio` at 0.90, plus a hard measure guard, because no similarity threshold can separate these pairs on its own

**Context:** this slice's brief asked whether D-055's rejection of `token_set_ratio` holds or inverts for product descriptions, and for measured pairs rather than a guess. Both were measured on the real implementation before anything was chosen.
**The finding that shaped everything else:** string similarity is blind to the single character that distinguishes two products. Measured, after normalization:

| Pair | ratio | token_sort | token_set | correct answer |
|---|---|---|---|---|
| `Colombian Whole Bean 5lb` / `Colombian Whole Bean 5 lb` | 1.00 | **1.00** | 1.00 | match |
| `Syrup, Vanilla 750ml` / `Vanilla Syrup 750ml` | 0.68 | **1.00** | 1.00 | match |
| `12 oz Paper Cups 1000 ct` / `12oz Paper Cups (1000ct)` | 1.00 | **1.00** | 1.00 | match |
| `Colombian Whole Bean 5lb` / `Columbian Whole Bean 5lb` (typo) | 0.96 | **0.96** | 0.96 | match |
| `Colombian Whole Bean, 5lb Bag` / `Colombian Whole Bean 5lb` | 0.92 | **0.92** | 1.00 | match |
| `Colombian Whole Bean 5lb` / `Colombian Whole Bean 2lb` | 0.96 | **0.96** | 0.96 | **different SKU** |
| `12oz Paper Cups (1000ct)` / `16oz Paper Cups (1000ct)` | 0.95 | **0.95** | 0.95 | **different SKU** |
| `Vanilla Syrup 750ml` / `Vanilla Syrup 1L` | 0.86 | **0.86** | 0.90 | **different SKU** |
| `Colombian Whole Bean 5lb` / `Colombian Whole Bean 5lb Organic` | 0.86 | **0.86** | **1.00** | **different SKU** |
| `Vanilla Syrup 750ml` / `Vanilla Syrup 750ml Sugar Free` | 0.78 | **0.78** | **1.00** | **different SKU** |
| `Colombian WB 5lb` / `Colombian Whole Bean 5lb` | 0.80 | **0.75** | 0.90 | same SKU, but abbreviated |
| `Colombian Whole Bean 5lb` / `Colombian Ground 5lb` | 0.73 | **0.68** | 0.79 | **different SKU** |
| `Vanilla Syrup 750ml` / `Hazelnut Syrup 750ml` | 0.72 | **0.56** | 0.73 | **different SKU** |
| `CF-1001` / `CF-1002` | 0.86 | 0.86 | 0.86 | **different SKU** |

Rows 4 and 6 score **0.96 on every scorer** and have opposite correct answers. There is no threshold that admits a misspelling and rejects a pack size.
**Decision — three mechanisms, not one threshold:**
1. **Scorer: `fuzz.token_sort_ratio`** over descriptions (word order is noise on a PO; a changed or added word is signal), `fuzz.ratio` over SKU comparison keys (a SKU's character order *is* its meaning). **D-055's reasoning holds and gets stronger, it does not invert:** `token_set_ratio` scores both containment rows at 1.00, and "Organic" / "Sugar Free" are two different shipments.
2. **A hard measure guard.** `measure_signature` extracts every number-with-unit from both strings and they must match exactly. A candidate that fails it can never be auto-applied *whatever it scores*; it is still surfaced with its real score and `blocked_reason: "measure_mismatch"`. This is what catches rows 6, 7, 8 and the `CF-1001`/`CF-1002` row, all of which clear or nearly clear any usable threshold.
3. **`FUZZY_MATCH_THRESHOLD = 0.90`**, which now sits in a clean measured gap: the worst genuine variant that survives the guard scores **0.92**, the best wrong pair that survives it scores **0.86**.

**What this deliberately gives up, recorded honestly:** `Colombian WB 5lb` is the same product as `Colombian Whole Bean 5lb` and scores 0.75, so it stays a **suggestion** with the right candidate ranked first. That is the correct direction to err — a reviewer confirms it once and D-070's learned rule matches it forever afterwards, whereas a wrongly auto-matched line is a wrong shipment nobody looked at. A second known limitation: a description that embeds its own quantity ("24 Vanilla Syrup 750ml") carries a measure the catalog row does not, so the guard blocks it and it degrades to a suggestion. PO line descriptions normally do not repeat the quantity column, and the failure mode is the safe one.
**Related:** Section 7.6, Section 10, D-055, `packages/core/docflow_core/matching.py`, `packages/core/tests/test_matching.py`.

## D-067 — Matching provenance uses its own `matched_item_id` key, with `learned_rule:<id>` / `mapped:*` / `human_edit` values

**Context:** D-059 established `field_provenance` on `document_lines` with `extracted` for every present field, and reserved `learned_rule:<id>` and `human_edit:<review_action_id>` for the slices that produce them. Section 7.1's vocabulary is "extracted, edited-by-human, or mapped".
**Decision:** matching writes one key, `matched_item_id`, which is not an extracted field but a conclusion *about* the line — so it gets its own key rather than overloading `sku` or `description`, whose provenance must keep saying `extracted` because that is still exactly what they hold. Values: `learned_rule:<uuid>`, `mapped:exact_sku`, `mapped:fuzzy`, `human_edit` (or `human_edit:<review_action_id>` once Phase 3 has review actions to reference). A `uom_alias` rule writes `matched_uom: learned_rule:<uuid>` the same way.
**Why this shape:** Section 7.15.3's "mapping reuse rate" is defined as "the share of matched line items whose provenance is `learned_rule:*`", so the rollup is a prefix test on one key rather than a join. Section 7.13's "every learned rule that fires is recorded as provenance on the affected field, so a reviewer can... disable the rule from the review screen" needs the rule's ID to be *in* the provenance value, which it is.
**Unmatching clears the key rather than writing a "no match" value.** Re-running after a SKU is retired removes `matched_item_id` from the map entirely, so "this line has no provenance for a match" and "this line has no match" are the same statement instead of two that could disagree.
**Related:** Section 7.1, Section 7.13, Section 7.15.3, D-059.

## D-068 — Two SKU keys and one description key, all derived and never written back to the document

**Context:** the brief flagged that normalizing the *catalog's* SKU and normalizing the *document's* SKU are different acts, and that Section 7.15.2 Step 4 expects catalogs to carry "leading/trailing whitespace and invisible characters in SKUs".
**Decision:** three normalizations, applied symmetrically to both sides at comparison time only. Nothing in this module writes to `document_lines.sku`, `.description` or `.unit`, and nothing writes to `items.sku` — the stored values stay exactly as the document printed them and as the catalog was uploaded (Section 7.1). This mirrors D-054's two-key pattern for buyer names.

| Key | Used for | What it removes |
|---|---|---|
| `normalize_sku` | **exact** SKU identity | padding, invisible characters (zero-width, BOM, NBSP), internal whitespace runs, letter case |
| `sku_comparison_key` | SKU **similarity** only | the above, plus every remaining non-alphanumeric character |
| `normalize_description` | description similarity and every rule `match_key` | case, apostrophes, punctuation, whitespace runs; and joins a spaced measure (`5 lb` → `5lb`) |

**Why `normalize_sku` deliberately keeps punctuation:** `CF-1001` and `CF1001` can be two different products in a real catalog, and collapsing them at the *identity* layer would invent a match — the same reasoning that keeps legal suffixes in `normalize_buyer_name` (D-054). A buyer who drops the hyphen still resolves, but through the fuzzy path where the measure guard and the threshold both apply, which is a suggestion-grade answer arriving by a suggestion-grade route.
**Trimming invisible characters is not "fixing the catalog":** the import screen that cleans and reports them is Phase 5 Step 4. This is matching refusing to be defeated by a character nobody can see, in the meantime.
**Why the measure glue is limited to short alpha tokens:** `5 lb` and `1000 ct` join, `24 Vanilla` does not. A four-character ceiling covers every unit a PO uses and no real word.
**Related:** Section 7.1, Section 7.15.2, D-054, `packages/core/docflow_core/matching.py`.

## D-069 — An ambiguity margin of 0.02, and candidates recorded for the fuzzy path only

**Context:** Section 7.15.2 Step 4 states outright that a tenant's catalog containing "duplicate description with different SKUs" is a warning, not a blocker — so a catalog where two live items score identically against one line is a catalog a real tenant has.
**Decision:** `FUZZY_AMBIGUITY_MARGIN = Decimal("0.0200")`. If the runner-up is within that of the winner, nothing is auto-applied and every candidate is surfaced. The runner-up test counts measure-blocked candidates too: a disqualified near-neighbour scoring just as well means the catalog holds a confusable sibling, which is exactly when a person should look. Separately, an exact SKU lookup that resolves to more than one live item (possible, because the partial unique index on `items` is on the *raw* SKU, so `CF-1001` and a padded `CF-1001 ` are two rows) is not treated as an exact match at all.
**Why so narrow:** this guards arbitrary tie-breaking, not close-but-clear calls. Picking one of two 1.00-scoring items by sort order would be an answer the system invented; a 0.96 winner over a 0.86 runner-up is a genuine decision the threshold already sanctioned.
**Candidates are recorded only when the fuzzy step ran.** Section 7.6's "always surface candidate + score" sits in its fuzzy bullet, and a learned-rule or exact-SKU match has no meaningful alternatives to offer — both are unique by construction (the two partial unique indexes on `learned_rules`, and the exact-SKU uniqueness above). Recording five fuzzy also-rans beside a certain answer would add noise to the review row and to the jsonb without adding information. `MAX_CANDIDATES = 5` covers a typical size/pack family without turning one review row into a scrolling list.
**Related:** Section 7.6, Section 7.15.2, Section 10, `packages/core/docflow_core/matching.py`.

## D-070 — Units of measure: a `matched_uom` suggestion and a `uom_mismatch` flag now; warnings proper deferred to the validation slice

**Context:** Section 7.6 says "Never silently 'normalize' units of measure or quantities. Suggest, flag, let the human decide", and Section 7.13 gives `uom_alias` as a tenant-level rule type. Warnings as first-class rows are the next slice, so this slice had to decide how much to record now.
**Decision:** two columns and nothing more. A firing `uom_alias` rule writes its canonical unit to **`matched_uom`**, a separate column, with `learned_rule:<id>` provenance — `document_lines.unit` keeps what the document printed, forever. A disagreement between the line's effective unit and the matched item's `unit_of_measure` sets **`uom_mismatch = true`**, which changes no value and never unmatches the line.
**Why a separate column rather than rewriting `unit`:** if the alias wrote into `unit`, applying a learned rule and correcting the document would be the same operation, and a reviewer could no longer see what the buyer actually sent. The whole point of Section 7.6's sentence is that those must stay distinguishable.
**What is deferred, deliberately:** `uom_mismatch` is a boolean, not a warning row with a catalog code and an acknowledgement trail. Section 7.3 requires unresolved warnings to be acknowledged at approval and recorded in `review_actions`, and that machinery belongs with the validation slice that builds it for every rule at once. Building a one-off warning path here would guarantee a second one exists a week later. The boolean is the fact that slice will read.
**Related:** Section 7.3, Section 7.6, Section 7.13, `supabase/migrations/0005_sku_matching.sql`.

## D-071 — `0005_sku_matching.sql` needs the founder's manual Supabase SQL Editor step; 20 tests skip, and what was verified anyway

**Context:** identical to D-017/D-037/D-062 — `DATABASE_URL` connects as `docflow_app`, which has no `CREATE` privilege on `public` by design (D-013).
**Decision:** the migration is written but **not applied**. The 20 database-dependent tests in `apps/api/tests/test_matching.py` that need the new `document_lines` columns are gated on a new `requires_sku_matching_schema` marker (`apps/api/tests/conftest.py`) and report as **skipped**, not passing.
**What was deliberately structured so it could be verified now anyway:** the SQL with the most in it does not touch 0005's columns, so five tests run against the real `docflow-staging` today — `load_catalog` (live-items-only and cross-tenant invisibility), `load_sku_rules` (buyer-scoped-over-tenant-wide precedence, `proposed`/other-buyer exclusion, cross-tenant invisibility), `load_uom_rules`, and the `INSERT … ON CONFLICT` rule upsert against both partial unique indexes from 0004. The upsert was extracted into `_upsert_sku_mapping_rule` specifically so it could be exercised ahead of the migration rather than reviewed by eye. **This already caught a real bug:** `text()` treats `:` as a bind-parameter marker, so `:buyer_id::uuid` was a Postgres syntax error; it is now `CAST(:buyer_id AS uuid)`.
**What remains unverified until the founder applies it:** the migration SQL itself, the `document_lines` UPDATE in `match_document_lines` and `confirm_sku_mapping`, the `CHECK ((matched_item_id IS NULL) = (match_method IS NULL))` constraint against real writes, `numeric(5,4)` round-tripping of the scores, jsonb round-tripping of `match_candidates`, and every end-to-end behavior including the Phase 2 exit criterion and the two Section 7.5 isolation assertions. The 35 DB-free tests in `packages/core/tests/test_matching.py` cover all of the precedence, threshold, guard and normalization logic and pass now. Re-run all three suites at the next checkpoint once `0005` is applied.
**Related:** Section 7.5, D-013, D-017, D-037, D-062, `SETUP.md` Step 5.

## D-072 — Validation warnings are error-catalog entries, not free-text strings on the document

**Context:** Section 7.7 requires a failed rule to produce "a warning on the document". Section 7.16.5 requires that "no user-facing string that describes a failure exists outside" the error catalog. A warning is user-facing text describing something wrong with a document, so the two sections meet here — and the obvious shortcut, a `message` column on a warnings table, would have put warning prose outside the catalog on day one.
**Decision:** a warning row stores a **catalog code** (`VAL-001` through `VAL-013`) and a `detail` jsonb payload, and nothing else a human reads. The catalog holds the what/why/what-next prose; `detail` holds this occurrence's specifics (which field, which line, which two numbers disagreed, the tolerance applied). The review UI renders the catalog entry and interpolates the detail.
**Why this way round:** changing a warning's wording becomes a code change reviewed as a diff by `test_catalog_snapshot_matches`, rather than an UPDATE across customer data that nobody reviews. It also means one warning cannot say different things to two tenants.
**Consequence accepted:** `severity` is stored per occurrence rather than read from the catalog, because a money discrepancy is escalated by magnitude (D-073) and a total that is out by $4,000 must not look like a missing payment term. The catalog entry's severity is the default, not the only value.
**Related:** Section 7.3, Section 7.7, Section 7.16.5, `packages/core/docflow_core/validation.py`, `supabase/migrations/0006_validation_and_duplicates.sql`.

## D-073 — The validation tolerances, and why each number is what it is

**Context:** Section 7.7 says `line_total ≈ quantity × unit_price` "within a stated tolerance" and leaves the number to the build. The failure mode that matters is not a missed discrepancy; it is a rule that fires on every correct 40-line purchase order, because reviewers learn to click past it — and the first warning a reviewer learns to ignore is the last warning that ever protects them.
**Decision:** tolerances are `Decimal` constants in one place at the top of `validation.py`, and a discrepancy must beat **both** an absolute and a relative term to be reported.
- **Line total:** `LINE_TOTAL_TOLERANCE_ABS = 0.01` absorbs rounding of the computed product into a 2-decimal-place column; `LINE_TOTAL_TOLERANCE_REL = 0.005` absorbs a unit price the document printed already rounded (1,000 units at a true 0.12345 printed as "0.1235").
- **Header total:** `HEADER_TOTAL_PER_LINE_ALLOWANCE = 0.01` scales with line count because per-line rounding accumulates — 40 legitimately rounded lines can drift 40 cents from a total the originating system computed from unrounded values, and a fixed one-cent tolerance would fire on every one of those documents. Plus `HEADER_TOTAL_TOLERANCE_REL = 0.005` and a `0.01` floor.
- **Materiality:** above `MATERIAL_DISCREPANCY_ABS = 100.00` or `MATERIAL_DISCREPANCY_REL = 0.05`, a discrepancy stops being a rounding artifact and the warning is raised from `warning` to `high`.
**What this deliberately gives up:** a real error smaller than a cent per line goes unreported. That is the correct direction to err — the alternative trains reviewers to dismiss the warning that matters.
**Related:** Section 7.7, D-072, `packages/core/docflow_core/validation.py`.

## D-074 — Re-validation reconciles by fingerprint, so a human's acknowledgement survives re-processing

**Context:** Section 7.3 requires that an unresolved warning be explicitly acknowledged at approval and the acknowledgement recorded. Section 10 forbids overwriting a human correction with a machine value. Documents are re-validated (re-extraction, a correction, a re-run), so the naive "delete all warnings and re-insert" would destroy every acknowledgement on every re-run.
**Decision:** every warning carries a **fingerprint** — a hash over `(code, field, line, detail)` — and `sync_warnings` reconciles by it: computed-and-not-stored becomes a new `open` row; computed-and-stored is **left completely untouched**; stored-and-no-longer-computed is resolved and soft-deleted, keeping who acknowledged it and when. A unique partial index on `(document_id, fingerprint) WHERE deleted_at IS NULL` makes the database enforce this rather than the application remembering to.
**Why the fingerprint covers the compared values:** an acknowledgement of "this total is off by two cents" must not silently carry over to "this total is off by four thousand dollars". Changing the numbers resolves the old warning and raises a new, unacknowledged one.
**The consequence for date rules:** plausibility is measured against the document's own `created_at`, never against "today". Otherwise a document would slowly acquire and lose warnings as the calendar moved, and re-validation would not be idempotent.
**Related:** Section 7.3, Section 10, D-072, `packages/core/docflow_core/validation.py`.

## D-075 — ISO 4217 is a hardcoded frozenset, not a dependency

**Context:** Section 7.7 requires "currency is a valid ISO code". The obvious move is a currency package.
**Decision:** ~180 three-letter strings, defined once in `validation.py`. The list changes roughly once a decade, and the isolated parsing worker (Section 7.11) is the last place in this system that wants another pinned package to audit for CVEs every time one lands.
**Two deliberate contents decisions:** codes withdrawn in the last few years (ANG, HRK, SLL, VEF, ZWL, MRO, STD, BYR) are **included**, because a backdated or archived purchase order can legitimately carry one and warning on a historically correct document is exactly the false positive this module exists not to produce. `XXX` ("no currency involved") and `XTS` (reserved for testing) are **excluded**: both are valid ISO 4217 and neither is a valid answer to "what money is this order in".
**Related:** Section 7.7, Section 7.11, `packages/core/docflow_core/validation.py`.

## D-076 — Ingest-time duplicate detection now stores the link, closing D-020

**Context:** D-020 recorded that `POST /documents/upload` computed the hash and surfaced a possible duplicate **in the HTTP response only**, because the `documents` table had no column to store the relationship. 0004 added `is_possible_duplicate` and `duplicate_of_document_id`, so the reason for that compromise is gone.
**Decision:** both intake paths — the upload endpoint and email intake — call the same `find_content_duplicate_at_ingest` before inserting, and write the result **into the INSERT** rather than issuing a second UPDATE. One function, so the two paths cannot disagree about what a duplicate is.
**What does not change:** nothing is rejected or delayed. Both documents exist and both process normally (Section 7.8: "duplicate/change-order handling never deletes or overwrites the earlier document").
**Related:** Section 7.8, D-020, `apps/api/app/routers/documents.py`, `packages/core/docflow_core/email_intake.py`.

## D-077 — The newer document points at the earliest match, not at its immediate predecessor

**Context:** Section 7.8 requires the relationship to be recorded but does not say which way it points, or what happens on the third, fourth and fifth resend.
**Decision:** direction is fixed and never reversed — the **newer** document points at the **earliest** matching older one. Ordering is `(created_at, id)`, so a tie in timestamps still resolves deterministically.
**Why earliest rather than immediate predecessor:** five resends of the same PO all point at the original, instead of forming a linked list nobody can follow. A reviewer opening the fifth sees the document it duplicates, not a chain to walk.
**Why never reversed:** the earlier document is read and never written. That is what makes Section 10's "never overwrites the earlier document" structurally true — there is no UPDATE in `duplicates.py` whose WHERE clause can name a document other than the one being detected on.
**Related:** Section 7.8, Section 10, `packages/core/docflow_core/duplicates.py`.

## D-078 — An unknown buyer on either side still flags a change order

**Context:** the same PO number with different content is a revision — unless the two documents come from different buyers, because "PO-1001" is the thousand-and-first order a business ever placed and two of a distributor's buyers reaching that number independently is ordinary. Buyer identity is often unknown at detection time.
**Decision:** a candidate is excluded only when **both** buyers are known and different. An unknown buyer on either side still flags.
**Why this errs the opposite way from D-055:** the buyer-merge threshold is deliberately conservative because a wrong merge re-points foreign keys. Nothing here can be auto-applied — a change-order flag is a flag and a warning row, and it changes no value. A false flag costs a reviewer one glance at two documents; a missed revision ships the wrong order.
**One further rule:** identical content is a duplicate and **never also** a change order. The same bytes carry the same PO number by definition, and "this is a copy of that exact document" is the stronger statement. If 1 is revision A, 2 is revision B and 3 is a resend of B, then 3 points at 2 as a duplicate and 2 already carries the change-order flag — the chain is intact without claiming 3 revises 1.
**Related:** Section 7.6, Section 7.8, D-055, D-077, `packages/core/docflow_core/duplicates.py`.

## D-079 — `0006_validation_and_duplicates.sql` needs the founder's manual Supabase SQL Editor step; 33 tests skip

**Context:** identical to D-017/D-037/D-062/D-071 — `DATABASE_URL` connects as `docflow_app`, which has no `CREATE` privilege on `public` by design (D-013).
**Decision:** the migration is written but **not applied**. The 33 database-dependent tests that need `document_warnings` and `documents.change_order_of_document_id` — 17 in `apps/api/tests/test_validation.py` and 16 in `apps/api/tests/test_duplicates.py` — are gated on the `requires_validation_schema` marker (`apps/api/tests/conftest.py`) and report as **skipped**, not passing.
**What the migration deliberately does not contain:** no column anywhere in it can hold a corrected value. There is no "suggested total", no "reconciled amount", no "normalized date". Section 10 forbids altering a model-extracted value to make validation pass, and the cheapest way to keep that true forever is for the schema to have nowhere to put such a value.
**What is verified now anyway:** the DB-free tests in `packages/core/tests/test_validation.py` and `packages/core/tests/test_duplicates.py` cover every tolerance, every rule, the fingerprint, the `relationship_to` decision table and the PO-number key, and pass today.
**What remains unverified until the founder applies it:** the migration SQL itself, the `document_warnings` writes in `sync_warnings`, the unique partial index on `(document_id, fingerprint)` against real writes, jsonb round-tripping of `detail`, the expression index on the PO key, and every end-to-end behavior including both Phase 2 exit criteria and the Section 7.5 isolation assertions. Re-run all three suites at the checkpoint once `0006` is applied.
**Resolved 2026-09-17:** the founder applied `0006` to `docflow-staging`. All 33 tests now run: `apps/api` is 91 passed with zero skips. Two failures surfaced on the first real run and both are recorded in D-080 -- one a money-rendering inconsistency in the `detail` payload, one a test-fixture defect. Neither was a validation error.
**Related:** Section 7.5, Section 10, D-013, D-017, D-037, D-062, D-071, `SETUP.md` Step 5.

## D-080 — Computed money in a warning's `detail` renders at money scale; extracted values render exactly as stored

**Context:** applying `0006` ran the 33 database-backed validation and duplicate tests for the first time, and one failed on a string comparison: `detail["expected"]` came back as `"570.00000000"` where the test expected `"570.00"`. Nothing was wrong with the number. `quantity` and `unit_price` are both `numeric(14,4)`, so `quantity × unit_price` is a `Decimal` with eight decimal places, while a sum of `numeric(14,2)` line totals has two. The same amount of money was being written two different ways in the same payload depending on which columns it came from — `"570.00000000"` for a product, `"570.00"` for a sum, `"2.8500000000"` for a tolerance.

**Decision:** two helpers in `validation.py`, and the distinction is which side of Section 10 the value falls on.
- `_money()` — an **as-extracted** value (`quantity`, `unit_price`, `line_total`, `order_total`). Rendered exactly as the database handed it over, never re-scaled. A `unit_price` is `numeric(14,4)` because a price legitimately carries four decimal places, and a reviewer comparing the payload against the document must see the digits the document printed.
- `_derived_money()` — a value **DocFlow computed** for display (`expected`, `sum_of_lines`, `difference`, `tolerance`). Quantized to two places, `ROUND_HALF_UP`.

**Why this is not "altering a model-extracted value":** Section 10 forbids changing an extracted value to make validation pass. A derived amount is not an extracted value, no rule's outcome depends on the rendering (every comparison happens on the full-precision `Decimal` before the payload is built), and no extracted column is written. The rounding is presentation of a number DocFlow itself calculated.

**The second reason, which matters more than presentation:** `detail` is hashed into the warning fingerprint (D-074). With raw `str(Decimal)`, the fingerprint inherits the *column scale* of the operands — so widening `quantity` to `numeric(14,6)` in some future migration would silently change every fingerprint on every existing warning, resolving them all and re-raising them as new, unacknowledged rows. A reviewer's acknowledgements would evaporate on a migration that had nothing to do with them. Fixing derived money at two places makes the fingerprint depend on the amount rather than on a column definition the reviewer cannot see.

**Evidence this was an oversight rather than a deliberate choice:** three assertions across the test suite expect two-decimal money strings. Two of them passed only because their values happened to come from `numeric(_,2)` columns; the third was the one that failed. The intent was uniform, the implementation was accidental.

**Also fixed alongside:** `test_the_loaded_snapshot_carries_decimals_not_floats` asserted `evaluate_document(snapshot) == []` while building a document with the fixture's default `order_total` of `100.00` against a single `570.00` line. That document genuinely does not reconcile, so the assertion was testing VAL-002 rather than Decimal round-tripping. The header total is now set to match the line. **This was a test defect, not a product one — validation was right.**

**Related:** Section 7.1, Section 10, D-072, D-074, D-079, `packages/core/docflow_core/validation.py`.

## D-081 — `review_actions` gets a `tenant_id` and RLS, departing from the schema document

**Context:** `docflow-database-schema.docx` defines `review_actions` with `document_id`, `user_id`, `action`, `changes`, `created_at` — and no `tenant_id`. Isolation would come from the join to `documents`.
**Decision:** the table gets `tenant_id NOT NULL` and both RLS policies from the statement that creates it, exactly like every other tenant-scoped table.
**Why the document is overridden rather than followed:** Section 10 forbids adding a table without `tenant_id` and RLS, and Section 7.5 requires RLS "on every one of them, enabled from the migration that creates the table, never added later". An audit trail is the worst possible table to make an exception for — it is the record that proves who did what, and a join-dependent policy is one refactor away from leaking across a tenant boundary. `document_headers` (0002) and `document_warnings` (0006) denormalize `tenant_id` for the same reason.
**Cost accepted:** `tenant_id` is denormalized and could in principle disagree with `documents.tenant_id`. It is written from the same tenant-scoped session that wrote the document, and a test asserts Tenant B sees none of Tenant A's rows.
**Related:** Section 7.5, Section 10, `docflow-database-schema.docx`, `supabase/migrations/0007_review_and_approval.sql`.

## D-082 — Approved snapshots live in their own append-only table, not only in `documents.approved_json`

**Context:** Section 7.3 asks for two things that a single column cannot both satisfy: "On approval, freeze a complete snapshot ... This snapshot is immutable" and "If someone edits after approval, the document reverts to `needs_review` and must be re-approved — **the old snapshot is retained**." Re-approval would have to overwrite `approved_json`, and the retained snapshot would be gone. Section 9 separately calls `approved_json` "immutable once set", which is only true of an individual snapshot, not of the column.
**Decision:** a `document_snapshots` table, append-only, one row per approval, each with its own `snapshot_sha256` and the `review_action_id` that produced it. A partial unique index allows exactly one live (`superseded_at IS NULL`) snapshot per document, so re-approval must supersede the previous one in the same transaction. `documents.approved_json` and `approved_snapshot_hash` keep a copy of the current snapshot for the readers that only ever want the latest — Section 7.13's example prompting reads exactly that, and 7.4's exports name the hash.
**Why append-only matters beyond tidiness:** Section 7.4 requires every `exports` row to record "the snapshot hash it was built from", and the mandatory round-trip test compares a parsed export against the snapshot. An export generated last March has to stay explicable in terms of a snapshot that still exists. Nothing in the application updates a snapshot row except to set `superseded_at`.
**Related:** Section 7.3, Section 7.4, Section 7.13, Section 9, D-081.

## D-083 — `0007_review_and_approval.sql` needs the founder's manual Supabase SQL Editor step; 19 tests skip

**Context:** identical to D-017/D-037/D-062/D-071/D-079 — `DATABASE_URL` connects as `docflow_app`, which has no `CREATE` privilege on `public` by design (D-013).
**Decision:** the migration is written but **not applied**. The 19 database-dependent tests in `apps/api/tests/test_review.py` are gated on a new `requires_review_schema` marker (`apps/api/tests/conftest.py`) and report as **skipped**, not passing.
**What is verified now anyway:** the 26 DB-free tests in `packages/core/tests/test_review.py` cover the editable-field allowlist, the `{field, before, after}` diff (including that retyping the same value is not an edit, and that a `Decimal` reaches the trail as a string with its scale intact), the acknowledgement payload, and the determinism of the snapshot hash. One of them asserts that `review.PROVENANCE_HUMAN` still equals `matching.PROVENANCE_HUMAN`, because if those two strings ever drift, re-matching silently starts overwriting human corrections.
**What remains unverified until the founder applies it:** the migration SQL itself; that approving freezes a snapshot and sets all four approval columns; that `documents_approved_is_attributable` really rejects an unattributed approval; that an unacknowledged warning blocks approval; that editing an approved document reopens it and supersedes rather than deletes the old snapshot; that the partial unique index permits exactly one live snapshot; `review_started_at` being set once and never reset; and the two Section 7.5 isolation assertions.
**Explicitly noted, because it already cost a day once:** D-080 recorded that applying `0006` immediately surfaced two defects the skip markers had been hiding. The same applies here — until `0007` is applied, this slice is written, not proven, and the suite being green means nothing about it.
**Resolved 2026-09-17:** the founder applied `0007` to `docflow-staging`. All 19 tests now run and pass; `apps/api` is 110 passed with zero skips. One failure surfaced on the first real run and it was a test assertion, not a defect: the reopen test expected the trail to read [edited, reopened] and forgot that the approval is itself part of the trail. The behaviour was right -- [approved, edited, reopened], with the edit preceding the reopen it caused. Everything the test actually guards (status reverted, all four approval columns cleared, the old snapshot superseded rather than deleted, its hash unchanged) passed unaltered.
**Related:** Section 7.3, Section 7.5, D-013, D-017, D-079, D-080, D-081, D-082, `SETUP.md` Step 5.

## D-084 — The audit trail is ordered by a monotonic sequence, not by `created_at`

**Context:** 0007 read the review trail with `ORDER BY created_at, id`. Postgres `now()` is **transaction start time**, not statement time. An edit to an already-approved document writes two rows in one transaction — the `edited` action and the `reopened` action it causes (Section 7.3's "if someone edits after approval, the document reverts to `needs_review`") — so both carry an identical `created_at`, and the tiebreaker was `gen_random_uuid()`.

**The trail could therefore render the reopen before the edit that caused it, at random, document by document.** It was found when a test that had passed on the first run failed on a later one with no relevant code change in between. The test had been passing by luck, roughly half the time.

**Decision:** `review_actions` gains a `bigserial` `sequence` column (migration 0008) and `review_trail` orders by it alone. A unique index on `(document_id, sequence)` backs the read.

**Why not `clock_timestamp()`,** which was the cheaper fix: it would make `created_at` on this table mean something different from `created_at` on every other table, where it is the transaction timestamp — and it still ties at microsecond resolution under a fast enough insert pair. "These happened in this order" is what a sequence means; a timestamp only approximates it.

**Why this mattered enough to add a migration rather than accept it:** the Phase 3 exit criterion is "the audit trail shows exactly what changed". A trail that reorders cause and effect does not meet it. Worse, an audit trail that is *usually* right is more dangerous than one that is obviously broken, because nobody goes looking — and this one would only ever be read when something had already gone wrong.

**What is not recoverable:** rows written before 0008 are backfilled in arbitrary order, because for rows that already tied there is no information left to recover the true order from. At the time of writing those are test fixtures on staging and nothing else.

**The general lesson, recorded because it will recur:** a passing test proves the behaviour was right *that time*. Where an assertion depends on an ordering, a uuid, a hash or a timestamp, prefer an assertion that pins the mechanism — the replacement test asserts both that the two timestamps tie and that the sequence separates them, so it fails for the right reason rather than at random.

**Related:** Section 7.3, D-081, D-083, `supabase/migrations/0008_review_action_sequence.sql`.
