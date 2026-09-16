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
