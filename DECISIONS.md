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

## D-085 — Every field on the review screen is a text input, including money and dates

**Context:** the obvious markup for an order total is `<input type="number">` and for an order date `<input type="date">`. Both are wrong here, for the same underlying reason.

**Decision:** every editable field on the review screen is `<input type="text">`. Numeric fields get `inputMode="decimal"` so a phone shows the right keyboard, and nothing else.

**Why not `type="number"`:** the browser hands that value to its own numeric parser, which is an IEEE double. `"570.00"` becomes `570`, `"47.5000"` becomes `47.5`, and the scale the document printed is gone before the value reaches our code. Section 7.1 says no float ever touches money; a number input puts one directly between the reviewer and the value. It also lets a scroll wheel silently change a total that has focus.

**Why not `type="date"`:** the value a reviewer is checking is *what the document printed*. A date input reformats it to the browser's locale, or refuses it outright if the buyer wrote "28/05/25". Section 7.1 has the model return exactly what is printed and leave interpretation to a validation rule that warns; the input must not undo that by normalizing on the way in.

**Enforced, not just intended:** a component test asserts that every input the header renders has `type="text"`, so adding a number input is a failing test rather than a code review someone has to catch.

**The related guard:** a second test scans the whole web source for `dangerouslySetInnerHTML` used as a prop or key, and asserts the app contains exactly one `<iframe>` whose `sandbox` attribute is the empty string (Section 7.12, Section 10). Both patterns match *usage* rather than mention — the first version matched the bare word and failed on comments that stated the rule, which is a decent reminder that a guard asserting the wrong thing is worse than none.

**Related:** Section 7.1, Section 7.12, Section 10, `apps/web/src/components/review/`.

## D-086 — The end-to-end suite stubs the API at the network boundary

**Context:** Phase 3 adds Playwright. The question is what sits behind it: a real API with real Postgres, a worker and a Redis, or a stub.

**Decision:** Playwright drives a real browser against a real Next.js build, and the review API is stubbed at the network boundary with `page.route`. The API's own behaviour is proven separately in `apps/api/tests/test_review_api.py`, against real Postgres and real RLS.

**Why not a full stack:** the thing a browser test can prove that nothing else can is routing, rendering, focus, keyboard handling and the state machine the reviewer actually drives. Everything else it would exercise is already covered by a faster test closer to the code. A browser suite that also needed a database, a worker and a broker would be slow, would fail for reasons unrelated to the UI, and would become the suite someone disables the first time it blocks a merge — which CLAUDE.md Section 10 forbids outright ("merge to main with CI red, or disable a CI check to get a merge through"). The cheapest way to never disable a check is to build one that does not go flaky.

**What this deliberately gives up:** the stub encodes the API's contract, so a backend change that broke the contract without breaking its own tests would not be caught here. The mitigation is that both sides are generated from the same understanding and the API tests assert the response shapes the stub returns; if the two drift, that is a real risk and the honest place to catch it is a contract test, which is not MVP scope.

**Also decided here:** `@types/node` moves from `^20` to `^22`. CI runs Node 22 and so does the founder's machine, so the types were already a major version behind the runtime; vitest 5 refusing to install against `^20` surfaced a mismatch that predated it.

**Related:** Section 6 (Phase 3), Section 10, `apps/web/playwright.config.ts`, `.github/workflows/ci.yml`.

## D-087 — The web CI job builds before it typechecks, and had never passed until it did

**Context:** the `web` job ran `npm ci`, `npm run lint`, `npx tsc --noEmit`, `npm run build`, `npm audit`, in that order. It failed at the typecheck step with:

```
src/app/layout.tsx(20,50): error TS2304: Cannot find name 'LayoutProps'.
```

`LayoutProps` and `PageProps` are **global types Next generates into `.next/types` as part of the build**. On a clean checkout that directory does not exist, so a typecheck run before the build cannot resolve them. Locally the error is invisible: any previous `next build` or `next dev` has already produced `.next/types`, so `tsc` finds them.

**Decision:** `npm run build` moves ahead of `npx tsc --noEmit` in the web job. The explicit `tsc` pass is kept rather than relying on the build's own TypeScript check, because it also covers the test and e2e files that the build does not compile.

**How long this had been broken:** the web job had **never passed in CI**. The failure is present on `eb76329` (Phase 1) and `aaca780` with the identical step and the identical error, and it predates every Phase 2 and Phase 3 commit. It went unnoticed because `git push` had been failing over workflow scope earlier in the build, so nobody had watched a run to completion, and every local check was green.

**Two things worth taking from this, since the same shape will recur:**
- **A local pass and a CI pass are different claims.** The difference here was a generated directory that exists on a developer machine and never on a fresh checkout. Anything generated — types, migrations, fixtures, caches — is a candidate for the same mistake.
- **Red CI that nobody has looked at is indistinguishable from no CI.** Section 10 forbids merging with CI red; that rule only does any work if someone reads the result. Three phases shipped on top of a job that had never gone green once.

**Related:** Section 10, D-086, `.github/workflows/ci.yml`, `apps/web/src/app/layout.tsx`.

## D-088 — Session tokens are verified by the token's own algorithm, not by which setting is populated

**Context:** the founder reported that logging in had never worked — not in Phase 0, not after a password reset, and `/admin` always behaved as though the Console did not exist. Everything on the backend looked correct: the Supabase Auth account existed and was confirmed, `users.auth_user_id` was linked to it, and `platform_admins` held a matching row.

**The bug:** `_decode_bearer_token` chose its verification path from configuration rather than from the token:

```python
if settings.supabase_jwt_secret:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], ...)
    except jwt.PyJWTError:
        return None          # never falls through to JWKS
```

The project's JWKS endpoint publishes a single **ES256** key — asymmetric signing is the default for new Supabase projects — while `SUPABASE_JWT_SECRET` was also present in `.env` from the legacy-secret path added in `34d0fc7`. So every real session token took the HS256 branch, failed, and returned `None`. Sign-in succeeded at Supabase, the browser held a valid session, and then the API answered **401 to every authenticated request** and **404 to every `/admin/*` request** — the latter being correct-by-design behaviour for a non-admin (Section 7.15.1), which is precisely why it read as "the Console is missing" rather than "auth is broken".

**Decision:** read the token's `alg` header, then pick the key material for it. `HS256` verifies against the configured shared secret; `ES256`/`RS256` verify against the JWKS signing key. Anything else, including `none`, is refused. A server with neither path configured still raises, because that is a deployment mistake rather than a bad token.

**Why selecting on `alg` is safe here:** the classic algorithm-confusion attack works by handing an asymmetric *public* key to an HMAC verifier. That is unreachable in this shape — the HS256 branch only ever uses the configured shared secret, which an attacker does not have, and the asymmetric branch only ever uses JWKS public keys, which cannot forge a signature. The accepted algorithms are an explicit allowlist, and a test asserts an `alg: none` token is refused.

**Why it survived three phases of green tests:** every API test mints its own HS256 token against a test secret, because that path needs no network. The suite therefore exercised the branch that worked and never the branch a real deployment uses. The tests were not wrong; they were complete about the wrong thing. `apps/api/tests/test_token_verification.py` now covers both algorithms with a locally generated key pair, so the asymmetric path is exercised for real without reaching Supabase.

**The general lesson:** a test fixture that takes a shortcut for convenience — here, symmetric signing to avoid a network call — can quietly become the only path anything ever tests. Worth asking, of any fixture, which production path it is standing in for and whether that path is covered anywhere.

**Related:** Section 3, Section 7.5, Section 7.15.1, `apps/api/app/deps.py`, D-087.

## D-089 — Four defects between "the tests pass" and "a person can use it", found by driving a real browser

Fixing the login bug in D-088 let the app be opened for the first time. Everything behind it then failed in sequence. None of these were caught by any suite, because every suite tested a layer in isolation and the defects all lived **between** layers. They are recorded together because they share one cause: nothing had ever exercised the path a human takes.

**1. The browser never received the Supabase configuration.** DocFlow keeps one `.env` at the repo root — `docflow_core.config` resolves it by path so every Python process finds the same file. Next does not work that way: it reads `.env` only from its own project directory, so `apps/web` saw none of it, every `process.env.NEXT_PUBLIC_*` was `undefined` in the bundle, and `src/lib/supabase.ts` fell back to its placeholder URL. The browser was signing in against a Supabase project that does not exist, and the login page said "We couldn't sign you in with that email and password" — which was true, and useless. Fixed in `next.config.ts`, which reads the root `.env` and exposes **only** `NEXT_PUBLIC_`-prefixed keys. That prefix is Next's own marker for "compiled into the browser bundle", so everything selected is public by definition; a service-role key cannot reach the client through it.

**2. The API had no CORS middleware.** The web app and the API are always separate origins — `localhost:3000` and `localhost:8000` in development, different hosts once deployed — so the browser refused every call before it was sent. This had been missing since Phase 0. An explicit origin allowlist, `allow_credentials=False` because DocFlow authenticates with a bearer header rather than a cookie, and only the methods and headers actually used.

**3. The document viewer could not authenticate.** The viewer is an `<iframe>`, and an iframe's request is a plain browser GET carrying no `Authorization` header — but the content route required the session *and* the signed token, so it answered 401 to the only client that ever calls it. The token now carries the tenant inside the signed payload and the route authenticates from it alone. That is what Section 7.4's "downloads use short-lived signed URLs" means. **What this gives up, stated plainly:** a signed URL passed to someone else works until it expires. That is inherent to signed URLs; the mitigations are the five-minute TTL, the unguessable HMAC, and that the storage path never appears in the URL. The boundary that still holds absolutely is *minting*: RLS hides another tenant's document from the mint route, and editing the tenant segment invalidates the signature.

**4. Two headers each independently prevented the document from rendering.** The content was served as `application/octet-stream` — chosen so a file that lied about its type could not be rendered as something active — which a browser downloads rather than displays, so the panel was blank. And the CSP said `frame-ancestors 'self'`, which on a separate-origin API means "no page may frame this". Both are fixed: the media type now comes from magic-byte detection through a render-safe allowlist (`text/html` and `image/svg+xml` are deliberately absent, since both can carry script), and `frame-ancestors` names the web app's configured origins.

**Why every one of these passed CI.** The API tests call the API directly with a bearer header, so they never needed CORS and never met an iframe. The component tests render components with stubbed data, so they never needed real configuration. The Playwright suite stubs the API at the network boundary (D-086) — a deliberate choice, and the right one for what it covers, but it means the browser never talked to the real API. Each layer was well tested; the seams between them were tested nowhere.

**The correction to take forward:** a green suite is evidence about the layer it covers, never about the product. Before any phase is called complete, someone or something has to drive the real thing end to end — real browser, real API, real database. That is now how Phase 3 was actually verified, and it is the only reason these were found before a customer met them.

**Related:** Section 7.4, Section 7.11, Section 7.12, D-086, D-087, D-088.

## D-090 — Signing in now lands a reviewer on their queue

**Context:** the first person to attempt the Phase 3 walkthrough signed in, saw a page reading "Signed in as walkthrough@example.test / Role: reviewer", and stopped. There was no link to the review queue anywhere on it, and no way to sign out. Every screen in the app was reachable only by typing its URL.

**Decision:** a tenant user is redirected from `/` to `/review`, and every review screen carries a header with the product name, a link to the queue, the signed-in address and a sign-out button. The founder's account has no tenant, so it stays on `/` and gets the Console links instead.

**How it happened:** the root page was written in Phase 0, before a review screen existed. Slice 3 added the screens and never revisited where signing in lands you — the screens were built and tested as destinations, by someone who always arrived at them by URL.

**Why no test caught it:** nothing signs in and then looks around. The Playwright suite navigates straight to the document URL it already knows, which is the correct shape for testing the review screen and exactly the wrong shape for noticing that nobody can reach it. The new component test asserts the header offers a route to the queue and a way out; the deeper gap — "can a person who knows nothing get from the front door to the work" — is the kind only a person finds, which is what the walkthrough is for. It found it in under a minute.

**Related:** Section 6 (Phase 3), D-086, D-089.

## D-091 — The viewer says when a format cannot be shown, rather than showing nothing

**Context:** Section 7.11 admits a deliberately broad intake allowlist, including Word, Excel, raw email and TIFF. No browser renders any of those. The viewer served every original into a sandboxed iframe regardless, so those documents produced a blank panel beside the extracted data — indistinguishable from a broken screen, on the very screen whose whole purpose is side-by-side comparison.

**TIFF is the one that matters most.** It is not an exotic case: Section 7.11 calls it "fax and scanner output — common in this industry, multi-page". A product that silently fails to display the format its customers fax in would fail quietly and constantly.

**Decision:** the mint route detects the type from the file's own bytes and returns `previewable` plus a human name for the format. The viewer renders the iframe when it can, and otherwise says which format it is, that DocFlow read it fine, and offers the file itself. `text/html` and `image/svg+xml` are permanently on the not-previewable list regardless of what a browser could do with them — rendering document-derived markup is precisely what Section 7.12 forbids.

**Related:** Section 7.11, Section 7.12, D-089, `scripts/po_formats.py`.

## D-092 — Previews for unviewable formats are built in the worker, never in the API

**Context:** Section 7.11 accepts Word, Excel, raw email, RTF and TIFF on purpose, and no browser renders any of them. The review screen exists to show the original beside the extracted values, so for those formats the most important screen in the product was half empty and the reviewer's only recourse was to download the file and open it in another application. The founder asked, reasonably, whether the rule keeping parsers out of the web process could be overridden to fix it.

**It cannot, and the reason is the whole point of the section.** Section 7.11 opens: "Parsing libraries are the largest unowned attack surface in this system." These files arrive from a distributor's buyers, through a public intake address, from people DocFlow has no relationship with. A malformed TIFF that hangs, exhausts memory, or achieves execution inside the API process takes down the application for every tenant and does it inside the process holding the database credentials. The same file inside the worker takes one document to `failed`. Section 10 states it as a prohibition outright: "Parse or convert any uploaded file in the web process."

**Decision:** the preview is produced where parsing already happens — the worker for real intake, a seeding script for demo data — written to storage under the tenant prefix like any other file, and recorded on `documents` (migration 0009). The API serves stored bytes it never decoded. The feature the founder wanted ships; the rule stands untouched.

**Two kinds of preview, and the difference is shown to the reviewer rather than hidden:**
- `converted_image` — a TIFF fax re-encoded as PNG. The page as it was sent.
- `extracted_text` — the text out of a Word file, spreadsheet or email. This is **not** the original layout, and someone checking a figure against it is reading DocFlow's rendering rather than the document, so the screen says so in as many words.

**Constrained at the database, not just in code:** `preview_media_type` has a CHECK allowing only PNG, JPEG, PDF and plain text. `text/html` and `image/svg+xml` can never be stored, so this cannot become a path to rendering document-derived markup (Section 7.12).

**Enforced structurally:** `apps/api/tests/test_parsing_boundary.py` walks the AST of everything under `apps/api/app/` and fails if any file imports Pillow, python-docx, openpyxl, a PDF library, `zipfile`, or `docflow_core.previews`. A second test proves the scan catches a planted offender, so a green result means "nothing found" rather than "nothing looked at". This rule is easy to break by accident precisely because importing a parser is always the shortest path to a working screen — it was nearly broken that way here.

**Related:** Section 7.11, Section 7.12, Section 10, D-091, `packages/core/docflow_core/previews.py`.

## D-093 — Real intake gets previews, and a text preview is the text the model read

**Context:** D-092 built previews but only the demo seed script called the module, so a Word, Excel, email or TIFF arriving by real upload showed the "can't display" fallback. Wiring the module into the worker as-is would have shipped two further gaps, both found by running it over every format fixture: Outlook `.msg`, legacy `.doc`/`.xls` and OpenDocument `.odt`/`.ods` produced no preview at all, and Word previews read paragraphs only — so a Word PO's line-items table, which is where the lines almost always are, was missing from the preview beside the extracted lines.

**Decision:** the worker builds the preview itself, after conversion and parsing and before the model call. TIFF/HEIC are still re-encoded from the original (`converted_image`). Every other format's `extracted_text` preview is the text the worker already extracted and sent to the model — one extraction, two uses. That covers every format the worker can open, includes tables, and means the reviewer sees exactly what DocFlow read. A page the model read visually (a scanned PDF or image attached to an email) appears as a fixed placeholder line, never its filename (untrusted, Section 7.11). The Word-table gap in `previews._extract_text` is also fixed, since the seed script still uses it.

**Best effort by design:** a preview failure is logged by error type and never changes the document's status. It is written before the model call so a document whose extraction failed can still be looked at.

**Related:** D-092, Section 7.11, `apps/worker/app/tasks/parse_and_extract.py`.

## D-094 — LibreOffice's Word import filter is pinned for `.doc` conversion

**Context:** the LibreOffice-gated test had never run, because LibreOffice was not installed. Installed on 2026-09-18, it failed at once: a corrupt `.doc` (a valid OLE header followed by junk) did not fail conversion. LibreOffice, unable to read it as Word, silently re-read it as plain text and produced a "successful" `.docx` of garbage bytes — which would then have gone to the model as a purchase order, spending money on noise and presenting it for review as a document.

**Decision:** `convert_doc` passes `--infilter=MS Word 97`, so LibreOffice reads the file as a Word 97-2003 document or fails. Verified both ways against the installed LibreOffice: the corrupt file now exits non-zero with no output (a clean `DOC-017`), and a real `.doc` still converts. The existing test (`test_malformed_doc_fails_cleanly_whether_or_not_libreoffice_exists`) now runs and passes, and the worker suite has no skips.

**Lesson:** a test that skips when a dependency is missing is not a passing test. This one was green for three phases while guarding nothing.

**Related:** Section 7.11, `apps/worker/app/conversion.py`.

## D-095 — The worker registers its tasks itself; first end-to-end queue run

**Context:** every queue test mocked the broker, so Redis/Celery had never run for real (Memurai could not be installed from an agent session). With Memurai installed by the founder on 2026-09-18, the first real start of the worker — using the README's own command — listed **no registered tasks**. The API enqueues by name (`send_task("docflow.parse_and_extract")`) and nothing imported the task module in the worker, so every uploaded document would have been rejected by the worker as an unregistered task and sat in `pending` indefinitely: exactly the "stuck customer document nobody knows about" Section 7.9 calls a data-integrity failure.

**Decision:** `Celery(..., include=["app.tasks.parse_and_extract"])`. A test checks the registration in a fresh interpreter — inside the test run other tests have already imported the module, which would hide the gap — and was confirmed to fail without the fix. The README's worker command gains `--pool=solo` (Celery's prefork pool does not run on Windows) and `-Q interactive,bulk`.

**Verified end to end:** a Word PO uploaded through `POST /documents/upload` as the demo reviewer went upload → Redis → worker → extraction (`claude-sonnet-5`) → buyer, matching, duplicate check, validation → `needs_review` in 16 s, with 4 lines and its D-093 text preview (line-items table included) served by the API.

**Still open:** the stuck-in-processing alert (Section 7.9) would have caught this in production only if it also watches `pending`, not just `processing`. Check when that alert is built.

**Related:** Section 7.9, Section 7.11, D-093, `apps/worker/app/celery_app.py`.

## D-096 — Math-check tolerances are the rounding room of the printed numbers, not a percentage (supersedes D-073's relative terms)

**Context:** D-073 let a discrepancy through if it was within 0.5% of the amount. That is a lot of money on a large order: $100 on a $20,000 line or order total, $500 on $100,000 — silently, on the one check whose job is to catch a misread number. The founder approved tightening it on 2026-09-18.

**Decision:** each allowance is exactly the rounding the printed numbers can legitimately contain, and nothing proportional to the size of the order.
- **Line total** (`quantity × unit_price`): one cent (the line total is printed to the cent) **plus** `quantity × half a unit of the unit price's last printed decimal` — the most a correctly rounded printed price can contribute. 12 × "47.50" → 0.07; 1,000 × "0.1235" → 0.06; 100 × "200.00" → 0.51.
- **Order total** (sum of lines): one cent per line. Both sides are printed on the document, so per-line rounding is the only legitimate gap; a 40-line order still gets 40 cents.
- **Printed precision** is read from the stored value's significant digits with a floor of two places, because `unit_price` is `numeric(14,4)` and "47.50" comes back as 47.5000. A price printed with trailing zeros ("0.1200") is therefore treated as "0.12" — a slightly looser allowance, the safe direction.
- Unchanged: materiality (`high` at ≥ $100 or ≥ 5%), null handling, and the rule that a warning never changes a value (Section 7.7).

**Consequence for existing documents:** the tolerance is part of each warning's detail, so a document re-validated after this change gets fresh, unacknowledged warnings where the numbers now fail (D-074 working as intended). Nothing re-validates on its own.

**Related:** D-073, D-074, Section 7.7, `packages/core/docflow_core/validation.py`.

## D-097 — The review queue pages, and always shows the total

**Context:** asked by the founder whether a long queue paginates. It did not: the page requested the queue without a limit, the API defaulted to 50, and there were no paging controls, so an order past the 50th was **invisible** — not on a later page, nowhere. For a product whose promise is that every order is seen by a person, that is a correctness defect, not a cosmetic one.

**Decision:** `GET /review/documents` returns `total` (tenant-scoped, same filter as the rows) alongside the page. The queue shows "Showing 1–50 of N" with Previous / Next, resets to the first page when the filter changes, and steps back to the last non-empty page if approvals empty the current one. 50 per page (`QUEUE_PAGE_SIZE`).

**Tests:** an API test pages through a tenant's queue two at a time and must reach every document exactly once, with the total excluding another tenant's documents; a browser test pages through 120 orders to the 120th.

**Related:** Section 7.3, Phase 3, `apps/api/app/routers/review.py`, `apps/web/src/app/review/page.tsx`.

## D-098 — Exports are built in the worker, recorded from the click, and verified before anyone can download them

**Context:** Section 7.4 requires an `exports` row per export (format, storage path, SHA-256, generated-by/at, snapshot hash), a round-trip test for all four formats, byte-identical output for the same snapshot, and short-lived signed download URLs. Section 7.3: exports come from the approved snapshot, never the live tables.

**Decisions:**
- **Built in the worker, not the API.** Rendering and re-reading an .xlsx loads openpyxl, and `test_parsing_boundary.py` keeps it out of the web process (Section 7.11). The architecture document already lists "export generation" as a background job. `docflow_core.exports` (pure: snapshot in, bytes out) is added to the boundary test's forbidden list; the API uses `docflow_core.export_jobs`, which only records.
- **A row exists from the click** (migration 0010): `pending` → `ready` / `failed`, with `error_code` a catalog code. The row pins `snapshot_id`, so an order re-approved between the click and the worker still gets a file of the approval that was clicked; the export history marks such files "Earlier approval".
- **Verified at runtime, not only in CI.** `build_export` renders twice (bytes must match) and parses the file back (must equal the snapshot). Either failing is `EXP-004` and no file is stored. The worker also recomputes the snapshot's hash from its content before exporting it.
- **Finished rows are immutable** — a database trigger refuses any change to a `ready` or `failed` row except soft deletion. An audit record that can be edited proves nothing.
- **`exported` status** is set on the first successful file, and only if the document is still approved on the same snapshot.
- **Download links** reuse the viewer's signed-token construction with a purpose tag in the signature, so a viewer link and an export link are never interchangeable. The file is served `Content-Disposition: attachment`, `nosniff`, `no-store`, with a filename built from the PO number reduced to `[A-Za-z0-9._-]` (Section 7.11: filenames are untrusted).
- **Founder alert for EXP-004** (Section 7.16.5 says audience "both, founder alert fires"): the `founder_alerts` table is Phase 5 (7.15.3). Until then EXP-004 is logged at error level with the export id and code, for Sentry. **Open for Phase 5:** wire it to `founder_alerts`.

**Related:** Section 7.3, 7.4, 7.11, 7.16.5, D-092, `supabase/migrations/0010_exports.sql`.

## D-099 — What each export format contains

**The founder's choices (2026-09-18):** IIF becomes a QuickBooks **Estimate**; files carry **both SKUs, catalog first**; **one PO per file** (batch export deferred until customers ask).

- **Columns** are the extraction schema's field names, identical across CSV, Excel and JSON: the ten header fields, then `line_number, catalog_sku, sku, description, quantity, unit, unit_price, line_total`. `catalog_sku` is the tenant's own SKU for the matched item; `sku` is what the buyer printed.
- **The catalog SKU is frozen into the approval snapshot** (`build_snapshot` now joins `items`). Reading it from the live catalog at export time would break Section 7.3, and a SKU renamed or retired after approval would silently change an old export. Snapshots approved before this change have no `catalog_sku`; they export with the column empty until re-approved.
- **CSV:** UTF-8 with BOM (Excel otherwise mangles non-English text), CRLF, header fields on every line row (Section 3). Values starting `= + - @`, tab, CR or `'` get a leading apostrophe (OWASP CSV-injection guidance) — every value here came from a stranger's document. Plain numbers such as `-5.00` are left alone. The reader strips exactly one leading apostrophe, so the round trip is exact.
- **Excel:** same layout; **every cell is a text cell** — never a formula (openpyxl treats `=…` as one otherwise) and never a number, because an Excel number cell is a binary float (Section 7.1). Zip entries are stored uncompressed with a fixed timestamp, and document properties are pinned, so the bytes are identical on any day and on any platform (deflate output varies with the zlib build). Verified to open correctly in LibreOffice.
- **JSON:** nested `{format: "docflow.order", version: 1, document_id, snapshot_hash, header, lines}`, keys sorted, every value a string except `line_number`.
- **IIF (QuickBooks Desktop Estimate):** TRNS/SPL/ENDTRNS, tab-separated, CRLF, **Windows-1252** (what QuickBooks reads). Lines are negative amounts and quantities, per QuickBooks' convention, with signs flipped as text so `47.50` stays `47.50`. **IIF leaves out** `buyer_contact_email`, `currency`, `notes` (IIF cannot hold a line break, and notes usually have several), and per line `sku`, `unit` and `line_number`. The round-trip test compares everything else and asserts the omitted set is exactly this one. **IIF refuses, with a reason,** an order QuickBooks would reject — lines not adding up exactly to the total, no buyer name, or no order date (`EXP-006`; the date rule was found by driving the real app — an order with no date produced an IIF with a blank DATE) — and a value it cannot hold exactly: a tab or line break in a field, an address over five lines, a character outside Windows-1252 (`EXP-005`). CSV and Excel still work for those orders.
- **Pinned digests:** a test pins the SHA-256 of each format for a fixed snapshot, so any change to what customers download is a visible, deliberate diff.

**Open — verify against a real QuickBooks Desktop file (UAT TC-26):** the account names (`Estimates`, and `Sales` for lines), that an unknown `NAME` creates a customer rather than failing, and that `INVITEM` matches the tenant's QuickBooks item names (it is the catalog SKU). None can be tested without QuickBooks Desktop; they are constants in one place in `exports.py`.

**Related:** Section 3, 7.1, 7.3, 7.4, `packages/core/docflow_core/exports.py`.

## D-100 — Every member of a tenant can export, viewers included

**Context:** the architecture document's permissions matrix lists "export" without saying which roles have it; the error catalog's AUTH-002 already tells viewers they "can read orders and download exports".

**Decision:** all four tenant roles can request and download exports. Exporting reads data a person has already approved and changes none of it; the only state it touches is the `exported` status, which is bookkeeping. Editing and approving stay with owner/admin/reviewer. A founder-selected tighter rule later is a one-line change in `app/routers/exports.py`.

**Related:** Section 3, AUTH-002, `apps/api/app/routers/exports.py`.

## D-101 — "Approve & export": one click, approval still explicit

**Context:** the MVP features document lists "One-click 'Approve & Export'". After Phase 4 it was two clicks: Approve, then a format. The founder asked for the combined button on 2026-09-18.

**Decision:** a second button beside Approve, with a format picker. It runs the ordinary approval (same warning gate, same acknowledgements, same `review_actions` row) and, only if that succeeds, the ordinary export. It is a shortcut over two existing actions, not a new path: approval stays an explicit user action (Section 7.3), a failed approval exports nothing, and there is still no auto-approve anywhere. The picker remembers the last format per browser (`localStorage`, read defensively; CSV when unavailable) — a per-person convenience, not a tenant setting.

**Related:** Section 7.3, D-098, `apps/web/src/app/review/[id]/page.tsx`.

## D-102 — Tier prices live in a versioned `tiers` table; setup fees are chosen per customer

**Context:** Section 7.15.2 asks for tier names, prices, setup fees and allowances "in a versioned tiers config table (or typed config file — propose which)", with tenants keeping their version until the founder moves them.

**Decision:** a table (migration 0011), one row per `(code, version)`, one `is_current` row per code, seeded with `docflow-pricing.docx` v1: Starter $299 / Growth $399 / Scale $599 per month; founding promo $199 / $249 / $349 for 90 days; allowances 300 / 1,000 / 3,000 (Section 7.16.1). A table rather than a file because a tenant must reference the exact version it signed up on (a foreign key, `tenants.tier_id`), and MRR on the dashboard is a SQL sum over it. It is one of the named global tables (no `tenant_id`); tenants may read it (prices are public, and the allowance banner needs it) and only platform admins may write.

**Setup fees are not in the table.** The pricing document makes them a per-customer choice ($750 founding, $1,500 standard, $2,000–2,500 complex), so the go-live action (slice 5.3) records the amount chosen for that tenant, alongside whether the founding promo applies.

**Related:** Section 7.15.2, 7.16.1, `supabase/migrations/0011_console_foundations.sql`.

## D-103 — Every email goes through an outbox; with no provider it is held and shown in the Console

**Context:** invites (7.15.2 Step 3), founder alerts (7.9), go-live and lifecycle emails all need to send mail. No email provider account exists yet (`.env.example` notes Postmark as the intended provider, pending a sending domain).

**Decision:** `email_outbox` (0011). Every email is rendered from a template in `docflow_core/email_templates/` (Step 9: "templates in the repo, not hardcoded strings") and written as a row first. With `EMAIL_PROVIDER_API_KEY` unset the row is `held`: nothing is sent, and the founder reads it — invite links included — on the Console's Outbox page and each tenant's page. Nothing is silently dropped and nothing is sent without a record. Delivery (queued → sent) is added when a provider is configured. Templates use `$name` placeholders and must be given every field, so a template change cannot send a literal placeholder to a customer; a test renders every template.

**Access:** platform admins read the outbox; a tenant session may *insert* mail for its own tenant (a worker notification) but never read it — an invite body carries a sign-in link.

**Open:** a Postmark (or equivalent) account and a verified sending domain before any real customer.

**Related:** Section 7.9, 7.15.2, `packages/core/docflow_core/email_outbox.py`.

## D-104 — Founder alerts: one writer, one savepoint, deduplicated

**Context:** Section 7.9: "One alert, one row, two channels. Every alert condition writes a founder_alerts row first; the email is sent from that row, and the Console's attention panel reads the same row."

**Decision:** `founder_alerts` (0011) with `docflow_core.founder_alerts.raise_alert` as the only writer. It writes the outbox email (to `FOUNDER_ALERT_EMAIL`) and the alert row in one savepoint, and the alert names its email (`email_outbox_id`), so the two channels cannot disagree. A `dedupe_key` with a partial unique index collapses a condition that keeps firing into one open alert; after acknowledgement the next occurrence is a new alert. Alert types are an explicit table in code — each slice adds the types it starts raising, so the table is also the inventory of what is wired. Payloads carry ids, counts and codes only (Section 7.10), because they are emailed.

**Access:** a tenant session may raise an alert about its own tenant (conditions are usually detected there — the worker, an allowance crossing) but can never read one; platform admins read and acknowledge.

**First wiring:** `EXP-004` (an export failing its integrity check) now raises `export_integrity_failure`, closing the open item in D-098.

**Related:** Section 7.9, 7.15.3, 7.16.5, D-098, `packages/core/docflow_core/founder_alerts.py`.

## D-105 — Invites: DocFlow generates the Supabase link and sends it itself

**Context:** Step 3 requires a "set your password" link for the tenant's owner. Supabase can send invite emails itself, but its built-in mailer only delivers to the project's own team members, so it cannot reach customers.

**Decision:** the Console asks Supabase's admin API to *generate* the link (`type: invite`, or `recovery` for someone who already has a sign-in) without sending anything, links the returned Supabase user to the local owner row (`users.auth_user_id`, D-012) at once, and sends the link through the outbox (D-103). The link lands on `/auth/accept`, which reads the session from the link and asks for a password (at least 10 characters). It is not a signup page: without a valid link there is no session and nothing to do (Section 3). A re-send that finds the owner already linked to a *different* sign-in account is refused (`CON-005`) rather than risk handing the tenant to the wrong person.

**Founder setup step:** Supabase only redirects to allowed URLs. `http://localhost:3000/auth/accept` (and the production equivalent later) must be added under Authentication → URL Configuration → Redirect URLs.

**Related:** Section 3, 7.15.2 Step 3, D-012, `packages/core/docflow_core/external_services.py`, `apps/web/src/app/auth/accept/page.tsx`.

## D-106 — The intake page leads with the upload, not the next step

**Found by the founder's walkthrough (2026-09-18):** twice, files were never uploaded. The page's file control was the browser's bare input — plain "Choose Files No file chosen" text — directly above a large dark "Create the tenant" button, so the obvious next click skipped the upload entirely. The tests passed, because they drive the input directly.

**Decision:** the page is two numbered steps. Step 1 is a large drop area with a real "Choose files…" button (drag-and-drop too) and the uploaded list right under it. Step 2's "Create the tenant" button stays secondary until at least one file is uploaded, with a line saying so; creating a tenant with no files remains possible on purpose.

**Related:** Section 7.15.2 Steps 1–2, `apps/web/src/app/admin/intakes/[id]/page.tsx`.

## D-107 — Every password field can be shown

**Asked for by the founder (2026-09-18)** after mistyping the confirmation on the set-password page: with both fields hidden there is no way to see which one is wrong. One shared `PasswordInput` with a Show / Hide switch, hidden by default, used by every password field (sign-in and both set-password fields). The switch is a real button with an accessible label and never submits the form (unit-tested).

**Related:** `apps/web/src/components/PasswordInput.tsx`.

## D-108 — Catalog and customer-list import: parsed in the worker, committed as a diff

**Context:** Section 7.15.2 Steps 4-5: one shared upload -> preview -> column mapping (remembered per tenant) -> validation report -> commit flow, for the catalog and (optionally) the customer list; blockers fixed inline or by re-upload; every commit a `catalog_imports` row that the items it touched reference; re-uploads are diffs with retirement, never deletion; a retiring SKU used by an active learned rule flagged first.

**Decisions:**
- **The file is opened only in the worker** (`docflow_core.catalog_parsing`, forbidden in the API by the boundary test). It becomes a text table stored on the import row (`columns`, `rows`, `header_row_number`), capped at 50,000 rows × 100 columns. Everything after that — mapping, validation, fixes, diff, commit — is plain data work in `docflow_core.catalog_import`, which the API may use. Spreadsheet numbers are rendered through Decimal, so SKU `1002` stays `"1002"`.
- **One evaluation, used twice.** The preview and the commit call the same `evaluate`; the commit re-runs it against the catalog as it is at that moment and refuses while any blocker remains (IMP-005). Commits for one tenant are serialized on the tenant row.
- **Row numbers match the spreadsheet.** A title above the header and blank rows in the middle don't shift them.
- **Report codes** (founder audience): blockers CAT-001 blank SKU, CAT-002 duplicate SKU, CAT-005 too long, BUY-001 blank name, BUY-002 duplicate name, BUY-004 duplicate account number, BUY-005 too long; warnings CAT-003 one description / several SKUs, CAT-006 blank description, CAT-007 retiring a SKU a learned rule uses, BUY-003 near-duplicate of an existing customer, BUY-006 odd email; information CAT-004 / BUY-007 values cleaned of edge spaces and invisible characters. Failures IMP-001..008.
- **Inline fixes** are per-row, per-field overrides stored on the import; the file itself is never altered.
- **Catalog diff:** insert new SKUs; update changed ones; reinstate a retired SKU that reappears (the *same* item row, so learned rules pointing at it come back to life); retire live SKUs missing from the file (`deleted_at` + `retired_by_import_id`). An unmapped unit of measure is stored empty — the `items` column's `'EA'` default would be a guess the file never made.
- **Customer lists** never retire anyone (buyers are also created from POs). An existing customer (same normalized name) gets the list's account number and email filled in, never blanked. A new name close to an existing one is created and flagged in `buyer_merge_candidates` — never merged (Section 7.6).
- **The mapping is remembered by header text**, not column position, so a re-export with reordered columns still maps.
- **First catalog commit** moves `onboarding_status` from `tenant_created` to `catalog_loaded` (forward only) with a lifecycle event.
- **Acting-as:** every Console route logs its `admin_actions` row, then runs the tenant's own import code in a tenant session; the import records `created_by` (the founder) and `acting_as_tenant_id`.

**Related:** Section 7.6, 7.11, 7.15.1, 7.15.2, `supabase/migrations/0012_catalog_import.sql`.

## D-109 — Import fixes blocked by CORS; a missing column no longer blanks a field

**Context:** Founder testing of slice 5.2 in the browser. (1) Inline row fixes and column-mapping changes never saved, and Commit stayed disabled: both are `PUT`, and the API's CORS allowlist had only `GET, POST, PATCH`. The browser's preflight refused them before they were sent. Route tests use the TestClient, which never preflights, and the Playwright specs stub the API, so nothing caught it. The failure did show APP-000, but only at the top of a long page, out of sight of the table being edited. (2) The founder's second sample catalog had no UPC column, and the preview counted 17 of 17 existing items as "updated": committing it would have blanked every barcode the first file set.

**Decisions:**
- `PUT` is added to the CORS allowlist. `apps/api/tests/test_cors.py` now proves the allowlist equals the set of methods the routes use (from the OpenAPI schema), plus `OPTIONS`, so a new method can't be missed again and an unused one can't sneak in.
- The import screen's error box is sticky, so a failure is visible wherever the founder is working, and any failure that isn't catalog-coded gets the APP-000 fallback rather than being dropped.
- **A catalog field whose column is not mapped in the file is neither compared nor written** on update or reinstatement: the existing value stays. A file that has the column but an empty cell still sets that field empty, because the file said so. New items still get the unmapped field empty (D-108: no guessed `'EA'`). This matches the customer-list rule that a value is "filled in, never blanked".

**Related:** D-089 (the original CORS gap), D-108, Section 7.15.2 Step 4.
- **A row fixed inline stays in the preview and stays editable until commit** (after the problem rows, ahead of the file's start), showing what the file said and an Undo. Setting a fix back to exactly the file's value removes it, so an undone fix leaves no override behind.

## D-110 — The import screens always name the tenant

**Context:** The founder thought "Earlier imports" was shared between tenants. It wasn't: the database, the API (as a platform admin) and a browser test all showed each tenant's own list. But two tenants had near-identical names and the same test files, and the Catalog / Customer list screens never said which tenant they were on.

**Decision:** The import heading reads "Catalog · {tenant name}" (likewise Customer list), from the tenant overview. `apps/web/e2e/import-tenants.spec.ts` drives the founder's path between two tenants' catalogs and proves the second never shows the first's imports.

**Related:** D-108, D-109, Section 7.5.

## D-111 — Acting-as: the tenant's own review and export routes, mounted a second time behind an admin gate

**Context:** Section 7.15.2 Step 8 has the founder review each test-batch order "in the normal review UI, acting-as per 7.15.1". Section 10 forbids a second review component or code path for the Console; Section 7.15.1 requires the founder to act as themselves, with `acting_as_tenant_id` on every row, through audited, 404-to-everyone-else routes, and `adminDataAccess` to stay inside /admin handlers.

**Decisions:**
- **One `Actor` dependency** (`app/actor.py`) replaces the raw identity in the review and export routes: tenant, user, role, and `acting_as_tenant_id`. A tenant user's Actor comes from their own session, unchanged.
- **The same routers are mounted twice.** `main.py` also includes them under `/admin/tenants/{acting_tenant_id}/act`, with `admin.acting_as_gate` as a router dependency. The gate 404s anyone who isn't a platform admin and a tenant that doesn't exist, writes one `admin_actions` row per request (`acting_as_read` / `acting_as_write`, route template and target id only, never document data), then hands the route a founder-as-support Actor. The route runs in the tenant's own session, so RLS still confines it to that tenant.
- **It fails closed:** on the Console mount, `current_actor` accepts only the gate's Actor for the tenant named in the path, and 404s otherwise, so a mount that ever lost its gate can't fall through to anyone's session.
- **Writes carry both ids:** review actions, exports and learned rules record the founder's own `user_id` and `acting_as_tenant_id` (`learned_rules.acting_as_tenant_id` is new in 0013). The tenant's trail already labels these "DocFlow support".
- **The web app reuses the pages:** `/admin/tenants/{id}/review[/{documentId}]` render the very same queue and review screen. `src/lib/reviewScope.ts` derives the API prefix and link base from the page's address. The Console banner replaces the tenant header there. The server gate, not the client, decides access.

**Related:** Section 7.15.1, 7.15.2 Step 8, 10; `apps/api/tests/test_acting_as.py`.

## D-112 — The test batch is uploaded "staged" and runs only when the founder says so

**Context:** Steps 6 and 7 are separate: upload the 5–10 samples, then "Run extraction".

**Decisions:**
- **One upload path.** The tenant upload endpoint's body became `ingest_upload(...)`; the Console's multi-file test-batch route calls it with `is_test_batch=True`. Every 7.11 check applies, and a failing file is reported without stopping the others.
- **A new document status, `staged`** (migration 0013): stored and validated, never sent to the model, hidden from the review queue. It's a distinct status rather than `pending`, so the stuck-processing alert (D-095) never mistakes a deliberately waiting file for a stuck one. The worker also refuses a `staged` document outright.
- **"Run extraction"** moves every staged test document to `pending` under a row lock and enqueues them oldest first at interactive priority, after commit.
- **Order of steps:** no test batch before a catalog is committed (ONB-001). The batch takes more files and runs until it's marked complete (ONB-002 after). "Complete" needs every test document approved or exported (ONB-004); a rejected one doesn't count, because the step says "every test-batch document is approved".
- `onboarding_status` moves forward only (`docflow_core.onboarding.advance`), and every move writes a lifecycle event.

**Related:** Section 7.15.2 Steps 6–8, 7.11; D-095.

## D-113 — Go-live: a Stripe subscription invoiced by email, retry-safe; jobs that run later are table rows

**Context:** Step 9 bills the customer, but at go-live nobody has entered a card. Founder decisions, 18 Sept 2026: the monthly plan is a Stripe subscription with `collection_method=send_invoice` (Stripe emails the invoice with a pay link); the setup fee goes on that first invoice or is marked "invoiced manually"; the founding-customer price is a go-live checkbox applied as a coupon for the tier's promo period.

**Decisions:**
- **Prices come from `tiers`**: a Stripe Product per tier version with a fixed id (`docflow_tier_<tier id>`), the price sent as `price_data` from `tiers.monthly_price`, and the founding coupon (`docflow_founding_<tier id>`, amount off = price − promo price, repeating for ⌈promo_days / 30⌉ months). Nothing is typed into Stripe by hand or into code.
- **Retry-safe, because a database failure after Stripe succeeded must never bill twice:** fixed ids for the product and coupon; the setup fee is added only if no pending one for this tenant exists; an existing live subscription for this tenant is returned rather than duplicated; every create also carries an idempotency key. Order of work: Stripe, then the invite if it hasn't gone, then one database transaction (intake live, go-live email, first-week check-in, status `live`) that re-checks the state under a row lock. A failure before that transaction leaves the tenant not live (ONB-008).
- **Money:** the setup fee arrives as a string and must be a non-negative whole number of cents (ONB-007); fractions of a cent are refused, never rounded.
- **`INVOICE_DAYS_UNTIL_DUE = 14`**, in the new `docflow_core/constants.py`, which now holds every Section 7.15.4 constant (the two email-intake limits moved there from `email_intake.py`). Lifecycle events record the values in effect.
- **Scheduled jobs are rows** (`scheduled_jobs`, 0013), not queue messages with a countdown, so they survive a Redis restart and are visible. A Celery beat task sweeps due rows every 5 minutes through a narrow `scheduler_session` that can see only that table; each job runs in its tenant's own session. Claims use `FOR UPDATE SKIP LOCKED`; a job left running by a dead worker is released after 30 minutes; a failing job is retried twice (5 and 30 minutes later), then marked failed with a `scheduled_job_failed` founder alert. **Running locally now needs `celery beat` as well as the worker** (SETUP.md).
- **The first-week check-in** emails the owner count-only numbers for non-test documents since go-live, and raises an info-level `first_week_checkin` founder alert.
- **Open:** the go-live email has no walkthrough link yet, because no walkthrough exists. Adding one is a one-line template edit once the founder has it.

**Related:** Section 7.15.2 Step 9, 7.15.4, 7.16.1; D-005, D-102.

## D-114 — An intake address is not live until go-live

**Context:** Section 7.15.2 Step 2: the intake address "exists from this moment but is not live: until go-live (Step 9) it auto-replies 'this address is not yet active' and processes nothing". Email intake had never checked this, so an onboarding tenant's address would have processed real mail.

**Decisions:**
- `process_inbound_email` checks `tenants.intake_address_active` after the Message-ID dedupe. When it's false, the email is logged (`raw_emails`, `intake_rejections` with INT-005) and nothing is stored, parsed or sent to the model.
- **The auto-reply goes through the outbox, at most once a day per sender**, so a mail loop or a busy buyer can't turn the address into a spam source.
- **Not built:** the Step 2 "optional founder-configured allowlist for testing" before go-live. It's a later addition if the founder wants to email test orders into a not-yet-live tenant.

**Related:** Section 7.2, 7.15.2 Steps 2 and 9, 7.16.3.

## D-115 — No "not confident" check on an optional header field the document doesn't have

**Context:** The founder's first test order carried 7 checks; 5 were VAL-010 ("below our confidence threshold") on buyer email, notes, payment terms, requested delivery and ship-to, all simply absent from the document. A check list that is mostly noise trains reviewers to tick without reading, which undermines the approval gate (Section 7.3). Founder decision, 18 Sept 2026.

**Decision:** The header low-confidence rule skips a field that is both **optional** (not in `REQUIRED_HEADER_FIELDS`: PO number, buyer, order total, currency) **and empty**. The field still shows empty on the review screen. Required fields keep the check whether empty or not, as does any field with a value, and so does the line-level low-confidence rule. The one open test order was re-validated through the normal `validate_document` pass: 5 checks resolved and soft-deleted, audit rows kept.

**Noticed, not changed:**
- Overall document confidence is still the minimum over *all* header fields (a Phase 1 stand-in, `parse_and_extract._overall_confidence`), so empty optional fields still pull it down. Section 7.1 says it should be the minimum of *required*-field confidences. This is a founder decision because it moves existing numbers.
- Editing a field does not re-run validation, so a check stays until acknowledged even after the value is corrected.

**Related:** Section 7.1, 7.3, 7.7; D-074.

## D-116 — Review screen clarity, from the founder's first Console review

**Context:** Walking the first test-batch order in the Console (18 Sept 2026), the founder couldn't tell which of eight empty boxes a check referred to (checks showed a raw `currency` and a code), didn't know which fields were mandatory, found the line table squashed, found the line arrow did nothing, and was taken to the order list instead of back to onboarding.

**Decisions (UI only, no rule changes):**
- Required header fields (mirroring `REQUIRED_HEADER_FIELDS`) carry a red `*`, and the section says to leave absent fields empty rather than guess.
- Each check leads with the field's on-screen name ("Currency:", "Line 2 · Qty:") and has a **Show "…" →** link that scrolls to and focuses that box. The raw `field`/`scope` detail keys are no longer shown.
- An empty optional field shows no "Low · 0%" badge or amber fill, matching D-115.
- **Layout:** the Console review pages use the full width (not the Console's 72rem reading column). The document and fields sit side by side from 1280px at 5:7, and stack below that. The line table has a minimum width and scrolls inside its box rather than squeezing. The Console nav wraps on narrow screens, and the support banner no longer sticks over it.
- The line-details arrow opens and closes the row. It starts open on lines needing a look, where it had been forced open and so looked broken.
- In the Console, the review screen's back link is "← Tenant" (the onboarding page), and the "Approved" message links there too.

**Related:** Section 7.3, 7.12, 7.15.2 Step 8; D-111, D-115.

## D-117 — The deal is agreed before onboarding and recorded at Create tenant; go-live only bills it

**Context:** Slice 5.3's go-live form asked for the setup fee as a typed amount and the founding price as a checkbox, at Step 9. The founder (19 Sept 2026) pointed out that price is settled in the sales conversation, and discussing it on screen during the onboarding demo is awkward. `docflow-pricing.docx` (v1, Sept 2026) gives the setup fees: Founding customer $750, Standard $1,500, Complex $2,000–$2,500. Tier prices and the 90-day founding price were already in `tiers` (0011) and match the document.

**Decisions (founder-approved):**
- **Setup fees are a versioned global table, `setup_fee_presets`** (migration 0014), like `tiers` (D-102): Founding $750, Standard $1,500, Complex $2,000 by default and any amount from $2,000 to $2,500, **Waived** ($0) and **Custom** (any amount). Waived and Custom are not in the pricing document. The founder asked for them, and both require a written reason. The tenant records the preset version it was sold on (`tenants.setup_fee_preset_id`), and no price lives in code.
- **The deal is recorded at Create tenant (Step 2):** plan, founding price, setup fee preset/amount, and how the fee is billed (Stripe invoice or invoiced by hand, D-113). Ticking "Founding customer" also picks the $750 founding fee, and the founder can pick another fee afterwards. The server checks every amount against its preset's range (ONB-012) and requires the note for Waived/Custom (ONB-013).
- **Editable until go-live, locked after.** The tenant page's Deal terms card edits it. Every save writes a `deal_terms_changed` lifecycle event with before and after, plus an `admin_actions` row. Saving with the same tier or preset keeps the tenant's existing version, so editing a note never moves a tenant to a newer price (Section 7.15.2). After go-live the card is read-only (ONB-011); moving a live customer is a tier change (slice 5.9).
- **Go-live takes no price input.** It shows a one-line summary, then the same two-click confirm, and bills exactly the recorded deal. A tenant with no deal recorded is refused (ONB-010). A $0 fee never produces a $0 line on the customer's invoice. Billing still happens at go-live, as Section 7.15.2 Step 9 says; only the *choosing* moved earlier.
- The API still accepts Create tenant without a deal, so a tenant can exist before the price is settled. The Console form always sends one.
- Existing tenant: Acme Test Prospect went live under 5.3's form and keeps its recorded fee ($750, Stripe, founding). Its `setup_fee_preset_id` stays NULL, and the card shows the amount without a preset name.

**Related:** Section 7.15.2 Steps 2 and 9, Section 10 ("type any revenue or price figure into code"); D-102, D-113.

## D-118 — An export the founder makes from the Console no longer vanishes

**Context:** On the founder's walk-through of D-117 (19 Sept 2026), "Approve & export → CSV" on a test-batch order in the Console gave an error. The worker had built both files correctly (`ready`). The API then crashed reading the export back (`exports.py` `assert row is not None`). The export query inner-joined `users` for the requester's email. An acting-as export (D-111) is requested by the founder's platform-admin user, who has no tenant, so tenant RLS hides that `users` row and the join dropped the export. The same query drives the status poll, the download link and the export history, so all three failed for Console exports. No test covered an export made through the acting-as mount.

**Decision:** `LEFT JOIN users`. The export comes back with `generated_by` null and `by_docflow_support` true, and the screen already shows that as "DocFlow support", matching the review trail (Section 7.15.1). Tenant RLS on `users` is unchanged: a tenant still can't see the founder's account. New test `test_an_export_made_from_the_console_is_returned_listed_and_downloadable` fails on the old query and passes on the new one.

**Also:** The invite text on the tenant page no longer says "Step 3." The page doesn't number steps 1–3, so the label made no sense there. It now says that go-live sends the invite automatically if it hasn't gone yet.

**Related:** Section 7.4, 7.15.1; D-111.

## D-119 — Operator screens, part 1: buyer merge and learned-rule management (slice 5.4)

**Context:** Section 6 Phase 5 lists "the existing operator screens: buyer merge, learned-rule management, per-tenant field schema, per-tenant example-prompting flag". Near-duplicate buyers have been *flagged* since Phase 2 (`buyer_merge_candidates`, D-055), but nothing let anyone act on a flag. Learned rules (`sku_mapping`, `uom_alias`) have been created from reviews since Phase 3, but there was no way to see them all or switch one off. The founder gave go for 5.4 on 19 Sept 2026, "if everything checks out".

**Decisions:**
- **Merge is per flagged pair, chosen by a person.** The screen lists open candidates, most similar first, with both customers' email, account number, order count and rule count. The founder picks which one to keep (default: the older one), sees what will move, and confirms. There is still no code path that merges without a human choosing a flagged pair (Section 10).
- **One transaction** (`docflow_core.buyer_merge.merge_candidate`):
  - Document headers are re-pointed. Approved snapshots are not touched (7.3); they keep the name the order was approved with.
  - Buyer-scoped learned rules move to the kept customer.
  - The kept customer gains the other's email or account number **only where it has none**.
  - The merged customer is soft-deleted, with `merged_into_buyer_id`.
  - A **`buyer_alias` rule** is created (7.13: "a founder merge becomes a rule"). Buyer identification now checks it after email and exact name, so the next order under the merged-away name links to the kept customer (`matched_on = "buyer_alias"`, rule recorded in `field_provenance.buyer_id`, `times_applied` counted) instead of re-creating the duplicate.
  - Other open candidates naming the merged customer are re-pointed or closed.
  - A new append-only **`buyer_merges`** row logs IDs only: documents and rules moved, fields filled, who, and acting-as.
  - Every write carries the founder's own user ID and `acting_as_tenant_id` (7.15.1), plus one `admin_actions` row per request.
- **A merge that would lose a human's rule stops.** If both customers have a rule for the same wording, nothing changes (BUY-009). The founder deletes the wrong rule first. A pair already resolved, or a keeper outside the pair, gets BUY-008. "Not the same customer" dismisses the pair and leaves both customers as they are.
- **Learned rules screen:** every live rule shows its type in plain words, which customer it applies to (or all), the wording it matches, what it means (SKU and description, with a "SKU retired" flag if the catalog retired it), times used, who confirmed it ("DocFlow support" for the founder, via the D-118 LEFT JOIN), and a link to the source order.
  - **Switch off / on** changes `status`: it stops or restarts firing on the next document. Nothing already on a document changes, and no rule overrides a human edit.
  - **Delete** is a soft delete (7.10) after a confirm.
  - A `proposed` rule can't be switched on here (RUL-001). Proposals are the unbuilt 7.13 hook.
  - The screen never creates a rule.
- **Tenant page:** the "Set up" box is now "Catalog, customers and rules", with links and counts ("2 to decide", rule count).
- **Not in this part:**
  - *Per-tenant field schema* changes what the extraction model is asked for, which Section 7.1/7.13 require a live golden-set run for. It gets its own design check-in with the founder before building.
  - *Example-prompting flag* moves to slice 5.10 with the feature it switches (the column has existed since 0001). A switch with nothing behind it would mislead.
  - *Disabling a rule from the tenant's review screen* (7.13) belongs with the tenant surface slice.

**Migration 0015** (founder to apply): `buyers.merged_into_buyer_id` / `merged_at` (with checks that a merged buyer is deleted and not merged into itself) and the `buyer_merges` table with RLS.

**Related:** Section 7.6, 7.13, 7.15.1, 10; D-055, D-065, D-111, D-118.

## D-120 — Operator screens, part 2: per-tenant field settings (required / optional / hidden)

**Context:** Section 7.13 requires a per-tenant field schema, versioned, configured by the founder. It has two halves: marking the base fields required/optional/hidden, and adding custom fields (`resin_grade`, `job_number`). The founder chose (19 Sept 2026) to build the first half now and hold custom fields: the first changes only what DocFlow checks and shows, while the second changes the extraction request itself, needs a live golden-set run, and should be shaped by a real customer requirement rather than a guess. It also closes D-115's open item — an order showing 0% confidence because an optional field the document never had scored zero.

**Decisions:**
- **`tenant_field_schemas`** (migration 0016), versioned like tiers and presets. Each save is a new version; `is_current` moves. Only what differs from the built-in default is stored, so a field DocFlow adds later starts at its own default for every tenant. `documents.field_schema_version` records the version a document was read under, so an old order's checks stay explainable after a change (7.13: "Every extraction logs the schema version it ran against").
- **What the three states mean.** `required`: checked for (VAL-006), counts towards the document's confidence, marked `*` in review. `optional`: read and shown; absent raises nothing, and an empty one raises no low-confidence check (D-115). `hidden`: never shown to that tenant, no checks, no effect on confidence — **but still extracted and stored**. Hiding changes what DocFlow shows and checks, never what the model is asked for, so no prompt or schema hash changes and no golden-set run is needed to hide a field; un-hiding later shows the data that was there all along.
- **Overall confidence is now the minimum over the tenant's required header fields** (Section 7.1, as written). Before, every header field counted, which is what produced 0% on the founder's test batch.
- **Two fields are locked required for every tenant:** PO number (an order that can't be traced back to what the buyer sent) and line quantity (not an order line without one). The guard is enforced when saving *and* when resolving a stored row, so no old row can switch one off. The "a line must name a SKU or a description" rule is not configurable at all and is not listed as a field: it is a rule about the pair, not a stored field.
- **Validation takes a `FieldRules` object** (defaults = the previous constants), so the rules stay pure and testable without a database; only `validate_document` reads the tenant's current version. A tenant with no saved version behaves exactly as before D-120, which is covered by a test.
- **Saving offers to re-check open orders.** Orders in `needs_review` are re-scored and re-validated against the new version, so a change during onboarding shows on the test batch at once. Approved orders are never touched — their snapshot is immutable (7.3) and they keep the numbers they were approved with. No extracted or edited value is ever changed; only the confidence summary and `document_warnings`.
- **Console screen** at `/admin/tenants/{id}/fields`, with a version history and the reason for each change; the review screen marks and hides from the version that read the document. Customers do not edit this.
- New codes: FLD-001 (unknown field or state), FLD-002 (locked field).

**Deferred, deliberately:** custom fields. When a prospect needs one, the design is: the field joins the tenant's schema with a type, flows into the extraction request (new prompt hash + schema version → live golden-set run before shipping), is stored on the header or line, and appears as an extra column in all four export formats.

**Related:** Section 7.1, 7.7, 7.13, 7.15.2; D-102, D-115, D-119.

## D-121 — The founder dashboard, from a nightly rollup (slice 5.5)

**Context:** Section 7.15.3 specifies the Console home in four regions and is explicit that the KPIs must not be computed by scanning documents on page load. Until now `/admin` was the attention panel alone.

**Decisions:**
- **`tenant_daily_metrics`** (migration 0017): one row per tenant per day, bucketed in the **tenant's own timezone** — the same boundary the monthly allowance uses (7.16.1), so the two can never disagree about which month an order fell in. `is_test_batch` documents are in no number (7.15.2 Step 8). The row holds counts and sums, plus two arrays of plain numbers (hours to approval, cost per document) so the dashboard's median and p95 are real rather than averages of daily averages. No customer data, by construction.
- **`rollup_runs`** records every run. The dashboard shows the last one, and `is_stale` (> `ROLLUP_STALE_HOURS`, 36) both warns on screen and raises the `rollup_stale` alert the section asks for.
- **The rollup gets its own narrow session**, `rollup_session` (`app.rollup`), not the Section 7.15.1 admin bypass. It may SELECT `tenants`, write `rollup_runs`, and raise exactly one alert type about itself; every number is computed inside that tenant's own `tenant_session` under the ordinary RLS policies. It cannot read a document across tenants. `_reset_rls_settings` clears the new setting like every other.
- **Nightly at 03:15 UTC**, two days at a time (yesterday has to be re-closed in every timezone, today topped up). Re-running a day overwrites it, so a missed night is caught up by the next one and the dashboard's "Recompute" button is the same job, queued to `interactive`.
- **Health strip is live, by design.** Each number is one indexed query about right now — queue depth from the broker, worker ping, oldest waiting document, model error rate for the last hour, spend today vs yesterday. It links out to Sentry, Supabase and Stripe rather than rebuilding them (Section 10).
- **Tenant list** carries the section's columns (usage vs allowance, backlog and its age, 30-day confidence with a 7-day drift arrow, AI cost this month, last order, subscription badge) and is one component shared by the home page and the Tenants page.
- **KPI definitions are pinned by tests**, not just by code: shares are part/whole over the window (never an average of daily shares), the median is over every document's own hours, p95 is nearest-rank, and an empty window answers "—" rather than 0.
- The required test from the section exists: `test_every_kpi_equals_an_independent_query` checks every card against hand-written SQL over the documents themselves.

**Found by driving it, not by tests:** the tenant-listing query used `:tenant_id IS NULL` with a NULL parameter, which Postgres refuses as an ambiguous type; the whole rollup failed on the first real run. It now casts. The same shape was in `read_days`. A test that always passes a tenant id would never have caught it (the D-095 lesson again).

**Related:** Section 7.9, 7.15.1, 7.15.3, 7.16.1, Section 10; D-095, D-104, D-113.

## D-122 — RLS gap on `platform_admins` and `admin_actions` (Supabase security advisor)

**Context:** Supabase's security advisor flagged both tables on docflow-staging as "publicly accessible" (`rls_disabled_in_public`), 22 Sept 2026. Migration 0001_foundations.sql had knowingly left them without RLS, reasoning that no API path writes to them outside `docflow_core.admin_data_access`. That reasoning covers the app's own access path but not Supabase's auto-generated PostgREST API, which exposes every table in the `public` schema to anyone holding the project's anon/service key regardless of whether the app itself ever queries that way — RLS is the only thing that blocks it. The other genuinely-global tables added afterward (`tiers`, `founder_alerts`, `onboarding_intakes`) all got RLS plus a `platform_admin_access` policy; these two were the actual oversight, not a considered exception.

**Decision:** Migration 0018 enables RLS on both tables with the same `platform_admin_access` policy used everywhere else (`current_setting('app.is_platform_admin', true) = 'true'`). `platform_session()` (`packages/core/docflow_core/db.py`) already sets that flag for exactly these two tables, so this is a drop-in fix with no application code change. Must be applied to docflow-staging and verified before docflow-prod, per Section 10.

**Related:** Section 7.5, 7.15.1, Section 10; D-004.

## D-123 — Lifecycle actions: cancel, reactivate, the suspend sweep, hard delete (slice 5.6)

**Context:** Section 7.15.4 specifies Cancel, Reactivate, and the wind-down/ready-to-delete queues as the tenant page's Lifecycle tab. Its state diagram (Section 7.14) is `active -> cancelling -> suspended -> pending_deletion -> deleted`, but never states what triggers `suspended -> pending_deletion` specifically, and nothing before this slice kept `stripe_subscription_status` in sync after go-live at all.

**Decisions:**
- **Suspended and pending_deletion happen in the same tick** (founder decision, 22 Sept 2026, asked directly rather than guessed): the sweep that finds a `cancelling` tenant past its effective date performs both transitions in one transaction (`lifecycle.claim_for_suspend`) — intake blocked, the deletion clock started (`deletion_scheduled_at = now() + EXPORT_WINDOW_DAYS`), `tenants.status` landing directly on `pending_deletion`. A tenant is never observably "suspended" without its deletion date already set. Both are still logged as distinct `tenant_lifecycle_events` rows (`suspended`, then `pending_deletion_entered`), so the history reads exactly like the state diagram even though the database passes through the first state instantly. This was necessary for the system to function at all: nothing else in the build prompt describes a second trigger, and without one a cancelled tenant would never reach the wind-down/ready-to-delete queues Section 7.15.4 requires.
- **The Stripe webhook gap is closed.** `stripe_subscription_status`/`stripe_current_period_end` were set once at go-live and never touched again — `STRIPE_WEBHOOK_SECRET` had sat unused in config since 5.3 for exactly this. A new `POST /webhooks/stripe` (signature verified manually per Stripe's documented HMAC scheme, no `stripe` SDK dependency added) keeps them in sync on every `customer.subscription.*` event, idempotent on event id (`stripe_webhook_events`), and tracks `tenants.first_past_due_at` — set on the first `past_due` event, cleared on recovery — which the non-payment cancellation rule needs. A `stripe_subscription_past_due` founder alert fires on `past_due`/`unpaid`.
- **Cancel is DB-only; Stripe is untouched until suspension.** Cancelling just records the effective date and reason — Section 7.14 is explicit that "everything keeps working until the effective date." The three effective-date rules (7.15.4) are implemented in `lifecycle.compute_effective_at`: `customer_requested` uses `stripe_current_period_end` (or now, flagged, if no subscription yet — mid-onboarding cancellation); `non_payment` adds `CURE_PERIOD_DAYS` to `first_past_due_at` (or now, flagged, if no notice is on record yet); `for_cause` is immediate and requires a ≥20-character reason. The founder may only push the computed date later, never earlier (LIFE-003).
- **Cancel/reactivate run through the tenant's own session with `acting_as_tenant_id`**, exactly like go-live (Section 7.15.1) — `docflow_core.lifecycle` is imported directly by the admin router, the same pattern `onboarding.py` already established, not routed through `admin_data_access`. The wind-down queue, ready-to-delete queue, and hard delete are genuinely cross-tenant (a queue spanning every tenant; a delete with no single tenant session left to act "as" once its data is gone) and live in `admin_data_access.py` instead.
- **Reactivate reuses `start_subscription` outright** rather than a separate "resume" call: it already returns an existing non-cancelled subscription unchanged, or creates a fresh one otherwise — exactly what "resumes the subscription, or creates a new one on the tenant's tier version" means once the sweep has cancelled the old one at Stripe. Already covered by an existing test (`test_a_cancelled_subscription_is_not_reused`).
- **Hard delete never drops the `tenants` row.** It can't: `tenant_lifecycle_events.tenant_id` and other survivors reference it without `ON DELETE CASCADE` by design, so deletion soft-deletes (`status = 'deleted'`, `deleted_at`) and purges business data table by table (`_PURGE_TABLES` in `admin_data_access.py`, children before parents; every `documents`-child table cascades from `documents` itself). `tenant_lifecycle_events` and `admin_actions` are deliberately excluded — they must outlive the tenant (7.14: "the fact that a tenant existed and was removed is retained for accounting purposes"). Storage objects are removed via a new `storage.delete_tenant_storage`, after the DB transaction commits. Requires the tenant's exact name typed (LIFE-005) and only accepts a tenant already past its `deletion_scheduled_at` (LIFE-006) — never reachable from anywhere but the Ready-to-delete queue.
- **A real, pre-existing bug was caught while wiring the reminder schedule:** `scheduled_jobs.job_type`'s CHECK constraint (migration 0013) only ever allowed `'first_week_checkin'` — the only job type that existed at the time. Migration 0019 widens it to also allow `'pending_deletion_reminder'`; without this fix the day-1/15/25 reminder emails (7.14) would have failed to insert at all.
- **Two new RLS-narrow sessions**, matching the existing `rollup_session`/`scheduler_session` shape exactly: `lifecycle_session` (cross-tenant read on `tenants` only, for the sweep to find due tenants) and `stripe_webhook_session` (same read, plus the `stripe_webhook_events` idempotency table). Neither is the Section 7.15.1 admin bypass; every write still happens inside that tenant's own `tenant_session`.
- **Fixed in passing:** two error-catalog entries (`EXP-004`, `EXP-007`) had `severity="error"`, a value outside the declared `Severity` Literal (`info`/`warning`/`high`/`critical`) — corrected to `"high"`, no test depended on the old value.
- **RLS gap closed separately as D-122** (`platform_admins`/`admin_actions` had no RLS at all) — found via Supabase's security advisor on docflow-staging the same day, unrelated to this slice's own work but fixed alongside it.

**Related:** Section 7.12, 7.14, 7.15.1, 7.15.4, Section 10; D-004, D-104, D-111, D-113, D-121, D-122.

## D-124 — D-122's own RLS policy locked the founder out of the Console (regression, same day)

**Context:** Migration 0018 (D-122) enabled RLS on `platform_admins` with only a `platform_admin_access` policy, which requires `app.is_platform_admin = 'true'` already to be set. But the one place `platform_admins` is read on *every* authenticated request — `app/deps.py`'s `_resolve_identity`, through `docflow_core.db.identity_lookup_session` — only ever sets `app.auth_user_id`; it can't set `app.is_platform_admin` first, because that flag is exactly what it's trying to determine. With no other policy, that `SELECT` returned zero rows for everyone, including the real platform admin: every `/admin/*` route 404'd unconditionally. Caught by this session's own tests (which exercise Console auth through the real HTTP flow) the same day 0018 was applied to docflow-staging, before it reached prod.

**Decision:** Migration `0020_platform_admin_self_lookup.sql` adds a narrow `self_lookup` policy on `platform_admins`, mirroring the one `users` already has (0001): visible only into the one row matching whoever is currently authenticated (`user_id = ` the row in `users` for `app.auth_user_id`), nothing broader. A session with no `app.auth_user_id` set, or one that resolves to no user row, still sees nothing — the table isn't reopened, just made readable for the one lookup every request already needs. Applied to docflow-staging immediately; must ship in the same batch as 0018 to any environment from here on, never separately.

**Related:** Section 7.5, 7.15.1, Section 10; D-122.

## D-125 — Billing delayed 7 days past go-live: a trial, not a shadow mode

**Context:** Founder decision, 22 Sept 2026, replacing an earlier "shadow-mode trial" idea (ingest live POs for a week without telling the customer, discussed but never committed to code). The simpler version: DocFlow goes fully live at go-live as designed, but nothing is billed — no monthly charge, no setup fee — until 7 days later, giving the customer a real, visible trial instead of a hidden evaluation period.

**Decisions:**
- **A Stripe trial, not a second pipeline.** `start_subscription` gained one new parameter, `trial_end` (unix seconds). Passed at go-live only (`TRIAL_PERIOD_DAYS = 7` from now), it puts the subscription in Stripe's own `trialing` status — already anticipated by the tenant-list badge in Section 7.15.3 ("active / trialing / past_due / …"), so no schema or dashboard change was needed. A `send_invoice` subscription in trial gets no invoice from Stripe at all until the trial ends; the setup-fee pending invoice item is still added exactly as before (D-113), so Stripe itself sweeps it onto the same invoice as the first month's charge at trial end — one invoice, not two, with no new code to combine them.
- **Net 15, not Net 14.** `INVOICE_DAYS_UNTIL_DUE` moved from 14 to 15 (the founder's explicit terms: "15 days to pay via ACH or credit card"), still the one constant every invoice's due date is computed from (`docflow_core/constants.py`).
- **Reactivation gets no trial.** `start_subscription`'s reactivate call passes `trial_end=None` explicitly — a lapsed customer resuming billing is not a new customer deciding whether to try the product; giving reactivation a fresh trial would be a way to get 7 more free days by cancelling and reactivating.
- **A mid-trial cancellation can't leave money in limbo.** If a tenant cancels before day 7, the subscription is cancelled at Stripe with nothing ever invoiced (Stripe's normal behavior for a trial that never converted) — but the setup-fee item added at go-live was already sitting pending against that Stripe customer, and would otherwise wait indefinitely and could land on some unrelated future invoice. New `external_services.void_pending_setup_fee(customer_id, tenant_id)` deletes it, scoped by the same `docflow_setup_fee_for` metadata `start_subscription` already writes. Called from the lifecycle sweep (`apps/worker/app/tasks/lifecycle_sweep.py`) alongside `cancel_subscription`, using a `stripe_customer_id` now carried on `lifecycle.SuspendClaim`. Idempotent: nothing pending is a no-op, and a fee already swept onto a real invoice is no longer "pending" and isn't touched.
- **DocFlow itself is not on a delay.** The tenant is `active`, live, and fully usable from go-live, exactly as before — only the Stripe invoice is deferred. This is why the change is billing-only and touches no onboarding state machine, no intake gating, no Console flow beyond the go-live summary text and the plan response's new `trial_period_days` field.
- **Not built:** a way to shorten or extend the trial per tenant, or to disable it — it's the one constant, same as every other Section 7.15.4 threshold. A tenant-specific override is a later addition if a deal ever needs one.
- **Unpaid trial-conversion invoice: no new code, one constant changed.** First considered a dedicated trial-only cure period (a new `tenants` column tracking whether a tenant has ever paid, so a never-converted trial could suspend faster than an established customer's later non-payment) — rejected as unneeded complexity once the timeline was worked through: under Net 15, Stripe doesn't mark the day-7 invoice `past_due` until day 22 regardless of whether it's a tenant's first invoice or their fifteenth, so there's no case where the existing non-payment rule needs to tell the two apart. Founder decision, same day: reuse the existing D-123 non-payment cancellation flow (`stripe_subscription_past_due` alert → founder clicks Cancel → `non_payment` in the Console) for every non-payment case, trial or established, unchanged — and set `CURE_PERIOD_DAYS` to 0, so a founder-confirmed non-payment cancellation's computed effective date is the first past-due notice itself, not that plus another wait on top. Net terms (`INVOICE_DAYS_UNTIL_DUE`) are the only grace period; nothing stacks on them. Suspension is never automatic — the founder still has to click Cancel; the only thing that changed is what date that click computes.

**Related:** Section 7.15.2 Step 9, 7.15.4, 7.16.1, Section 10 ("type any revenue or price figure into code"); D-113, D-117, D-123.


## D-126 — Allowances and quarantine (slice 5.7): one gate for every channel, stateless ceilings, release decided in one place

**Context:** Section 7.16 asks for soft allowances with notices (7.16.1), hard ceilings that pause rather than drop (7.16.2), the remaining email-intake layers (7.16.3), quarantine as a status with release and clear (7.16.4), and catalog wording for all of it (7.16.5). Reading the code first (plan: `docs/plans/5.7-allowance-quarantine.md`) found that email quarantine for attachment cap, failed authentication and unknown-sender velocity already worked, and that four things did not exist: the per-tenant daily cost breaker (Section 7.9 asks for one and 7.16.2 depends on it), any ceiling on web uploads, any release/clear/rotate function, and a reply for a rotated address.

**Founder answers (23 Sept 2026):** the daily ceiling is **$50 per tenant per day of estimated AI cost**; the setup test batch is exempt from both ceilings; web uploads go through the same gate as email; a founder release is allowed while a ceiling is still tripped; the tenant-facing screens ship here in minimal form and the full tenant dashboard stays in 5.8.

**Decisions:**
- **One gate, both channels.** `intake_gate.hold_reason` is called by email intake and by the upload endpoint, so there is one rule. It counts documents, not emails: the monthly ceiling is `ABUSE_CEILING_MULTIPLIER` (3) x the tier allowance on documents counted for metering; the daily ceiling is `DAILY_AI_COST_CEILING_USD` ($50, a `Decimal`) or `DAILY_TOKEN_CEILING` (20M tokens, a backstop chosen by me) of today's `extraction_runs`, excluding test-batch documents. A tenant with no tier has no allowance and is never ceilinged.
- **Stateless.** The gate is recomputed on every arrival, so there is no "paused" flag that can stick on or be forgotten. The founder alert is deduplicated per tenant, reason and day (monthly for the abuse ceiling), so a flood raises one alert. Ceilings are checked before the per-email rules, because they pause the whole tenant and only the founder can lift them.
- **One definition of a counted document** (`usage.py`): this calendar month in the tenant's timezone; not test-batch, failed, quarantined, a linked duplicate, or deleted. The Console tenant list previously counted linked duplicates; it now excludes them from the *count* only (their AI cost was still spent), and a test asserts the Console and tenant numbers agree.
- **Notices:** `allowance_notices` has a unique (tenant, month, threshold), so "one email per threshold per month" is enforced by the database. At 100% an `allowance_reached` alert fires with severity `info` (a sales signal). Documents are never blocked or delayed by the allowance. The banner is derived from the numbers each time, not stored, so it disappears when the month rolls.
- **Wording lives in the catalog.** `LIM-001/002` (with a next tier) and `LIM-003/004` (top tier, nothing to upgrade to), `INT-007` (held, one wording for both ceilings: the customer needs to know their documents are safe, not which meter tripped), `INT-008` (address changed), `INT-009` (sender not on the approved list), `QUA-001..005`. Catalog messages may now carry `{placeholders}`, filled by `render_error()`; `get_error()` still returns the raw template, which is what the snapshot records. The email templates take the catalog text as parameters rather than carrying their own.
- **Release is decided once, in `quarantine.py`, and enforced by the API through it.** The tenant's owner or admin may release only `attachment_cap`, `unknown_sender_velocity` and `sender_not_allowed`; the founder (acting-as, `admin_actions` row, `acting_as_tenant_id` recorded) may release anything, including a hold whose ceiling is still tripped. **I extended "owner" to owner and admin** (Section 3 treats them together for managing a tenant); reviewers and viewers can see what is held but never release (`AUTH-002`). A request that includes any hold the role may not release fails whole (`QUA-001`) and moves nothing. Released documents keep their original `created_at`, are enqueued oldest first, and count against the allowance only from then on. Clear is founder-only, needs the tenant's exact name, and is a soft delete; nothing is auto-deleted, held documents past `QUARANTINE_TTL_DAYS` raise one alert.
- **Strict sender allowlist quarantines, never rejects,** which needed a seventh `quarantine_reason`, `sender_not_allowed` (the spec names six). It is off by default, opt-in from the Console with an explanation of what it holds, and refuses an empty list while on (it would hold every order). Entries are addresses or bare domains, compared case-insensitively, exact domain not suffix.
- **Known sender now includes buyers** (closes D-031): an address matching a buyer's contact email, or a domain matching one's, except the big public mail domains (a buyer's Gmail address must not make every Gmail sender "known").
- **Address rotation:** old address to `grace` for `ROTATED_ADDRESS_GRACE_DAYS` (auto-replies `INT-008`, rate-limited to once a day per sender, idempotent on Message-ID), then `retired` (a plain 404). A grace address past its end is treated as retired at lookup time, without waiting for the sweep. The sweep's third pass retires them and raises the retention alert. Logged as a `tenant_lifecycle_events` row with ids only; the token is a credential and never logged.
- **Migration 0021** is additive: release provenance columns, the new reason, `intake_addresses.grace_ends_at`, `tenants.strict_sender_mode/sender_allowlist`, `allowance_notices` (tenant_id, RLS from creation). `allowance_notices` is added to the hard-delete purge list. Applied to staging by hand as before; until it is, email intake reads a column that does not exist, so it must be applied before this code runs against staging.

- **Wording changes after the founder's walkthrough (23 Sept 2026):** the customer must not be led to think DocFlow staff read their documents, and the Held page must not say there is "nothing for you to do" when other groups on it are theirs to release. So `INT-004`, `INT-007` and `QUA-001` now say only that DocFlow has been alerted and will release the documents once the volume or sender is confirmed; the Held page renders the catalog action for every group and adds no wording of its own. `INT-002`, `INT-003` and `INT-009` no longer say "Open 'Held for review'" (the customer is already on that page); they say "Tick the ones you recognize and release them." `INT-003` therefore differs from the illustrative wording quoted in `CLAUDE.md` Section 7.16.5, which is an example of the standard, not a fixed string. The Console screen shows a plain label for each reason and where both ceilings stand right now, with a note when a held document's ceiling is no longer reached, so a hold that outlived its cause is visibly safe to release.
- **Where the customer's notices live (23 Sept 2026):** the allowance banner and held-documents strip appear on the Purchase orders list only, each with its own dismiss control, never in the shared review chrome and so never while reviewing an order. A dismissal is remembered in that browser only (per-person convenience, never relied on by the server) and lapses when the notice changes: a higher allowance threshold, a new month, or a different set of held documents. A source test keeps the strip out of the review screens.
**Not built here:** the full tenant dashboard, audit-log view and roles UI (slice 5.8). A per-tenant override of the daily ceiling (the constant is global for now).

**Related:** Section 3, 7.9, 7.15.1, 7.16.1-7.16.5, Section 10; D-031, D-104, D-113, D-123.

## D-127 — Two defects found while verifying 5.6: a blank Console for signed-out visitors, and lint errors that would have failed CI

**Context:** Walking slice 5.6 in a private window, `/admin/lifecycle` showed a blank page. Separately, running web lint for the first time on the 5.6 work found three `react-hooks/set-state-in-effect` errors in the lifecycle pages, so CI's lint job would have gone red.

**Decisions:**
- **Blank page:** the Console layout called `notFound()` from inside a fetch callback, where React cannot act on it, so a non-admin stayed on the empty "checking" state forever. The API had always answered 404, so nothing was exposed, but the page must look like any other missing page. The layout now records a `denied` state and calls `notFound()` while rendering (verified in a signed-out headless browser: "404 This page could not be found"). This is the follow-up D-014 anticipated.
- **Lint:** the lifecycle pages fetched through a `load()` helper that sets state. They now fetch inside the effect with state set only in the promise callbacks, and refresh by bumping a counter; the cancel form's preview and confirm step are keyed by the chosen reason instead of being reset inside the effect. Same behaviour, no synchronous setState in an effect. The two new 5.7 pages use the same pattern.
- **Process:** "lint and typecheck clean in all four projects" was true at earlier checkpoints but had not been re-run for web after 5.6. It is part of the slice checklist now.

**Related:** D-014, D-123.


## D-128 — The tenant surface, part a: upload, the customer's dashboard, and who may do what (slice 5.8a)

**Context:** Section 6's Phase 5 line asks for a "customer-facing review/status page; tenant dashboard (documents by status, recent activity); email notifications; roles enforced; audit log view", and `docflow-mvp-features.docx` opens its MVP list with "Upload PO". Reading the code first (plan: `docs/plans/5.8-tenant-surface.md`) found the tenant surface thinner than either implies: `POST /documents/upload` existed and was hardened but no customer screen called it, there was no dashboard, no notification, no way for a customer to add a colleague, and no account-wide audit view.

**Founder decisions (23 Sept 2026):**
- **Two roles are given out, not four.** The tenant's first user -- stored as `owner`, which is what `admin_data_access.create_tenant` already writes -- is shown as **Admin**, and everyone else they add is a **reviewer**. `admin` is equivalent to `owner` everywhere and stays available for a customer who later wants a manager who is not the owner; `viewer` stays in the schema and is still enforced, but no screen creates one. Keeping all four in the model was deliberate: Section 3 locks the four roles, and removing two would be a change to the permission model for no gain, where hiding them is a change to a screen.
- **Why no viewer, explicitly:** it is one fewer case to test, explain and get wrong. The cost is a customer who wants a bookkeeper who can download but not approve; that is covered for now by the Activity log showing who approved what, and the role can be exposed the day someone asks.
- **What each role gets.** Admin: everything a reviewer can do, plus the dashboard, and (later) managing people. Reviewer: review, approve, reject, export, upload, see held documents and documents that could not be read, and read the activity trail. Only the admin may add reviewers.
- **Sign-in still lands on Purchase orders, "Needs review"** -- the queue is the work; the dashboard is a place you go on purpose.
- **Upload is in scope**, for orders that arrive by post or handwritten and get photographed. Reviewers may upload, since they are the ones holding the phone.

**Decisions I made building it:**
- **The upload endpoint was open to a viewer.** It checked only that the caller had a tenant, so any signed-in member could add documents; no screen offered it, but the API allowed it. It now takes the same role as reviewing. Found by writing the role table, which is exactly what the table is for.
- **A route inventory, checked by a test.** `app/roles.py` lists every tenant-surface route against the least role that may call it, and `tests/test_roles.py` fails the build when a route is missing from it or listed but gone. The inventory is read from the generated OpenAPI schema, not `app.routes`: a router included with `include_router` does not appear there, which is the trap `tests/test_cors.py` already documents (D-109) -- a scan that silently sees nothing would pass for ever, so the helper asserts it found something. The table does not grant anything; each route still carries its own dependency, and a behavioural matrix against the real database proves the important ones.
- **A reviewer refused the dashboard gets 403 and a catalog entry (`AUTH-003`), not a 404.** The Console is 404 to a stranger because its existence is itself the secret (7.15.1); this is a page inside an account the person is legitimately signed in to, so hiding it would only confuse them. Hiding the nav link is a courtesy; the API is the control (Section 3).
- **`arrived` and `counted` are two numbers, deliberately.** A month has everything that came in, including held and failed documents, and the smaller subset the plan is measured against (7.16.1). One word covering both would have to be wrong for one of them: a customer whose buyer sent nine orders should see nine, and should also see why six are what their allowance counts. The screen labels them "Arrived" and "Counted toward your plan", and `counted` is `usage.month_used` -- the same function the allowance banner uses, so the two cannot drift.
- **The dashboard counts live, not from the nightly rollup.** The founder's cross-tenant dashboard reads `tenant_daily_metrics` because it would otherwise scan every tenant's documents (7.15.3); one tenant's own rows are a small indexed set, and a customer should not be shown yesterday's numbers about their own account.
- **Recent activity is a union of rows the product already writes** -- `review_actions`, finished `exports`, released held documents -- so there is no second record of the truth to keep in step, and the Activity page (5.8b) will page over the same query. It is ordered by `review_actions.sequence` as a tiebreaker, because `now()` is frozen per transaction and two actions written together otherwise tie (D-084, and the same trap D-123 hit). It carries no extracted value: an edit says "3 fields", never what they became (Section 7.10). Work the founder did while supporting the account is labelled "DocFlow support" and the founder's own address is not shown (7.15.1).
- **`/review?status=failed` opens that tab**, so the dashboard's tiles link to what they count. Read with `useSearchParams` during render inside a Suspense boundary -- Next's own rule -- rather than in an effect, which the `react-hooks/set-state-in-effect` lint rule correctly refuses and which would have painted the wrong tab first.
- **One request per file on upload**, so a refusal names its file and the rest still go, and a person watching a phone upload photos over a poor connection sees steady progress. The page lists a per-file outcome in the catalog's words, including "accepted but held" (7.16.2), which is not an error and is not shown as one.

**Not built here:** the Activity page (5.8b), the needs-review digest email (5.8c), and the team piece (5.8d, still undecided -- today not even the founder can add a second user to a tenant from the Console).

**Related:** Section 3, 7.5, 7.10, 7.11, 7.16.1, 7.16.4, Section 10; D-084, D-090, D-109, D-123, D-126.


## D-129 -- The allowance banner waits until the plan is nearly spent

**Context:** Section 7.16.1 says the tenant surface shows a non-blocking banner at 80% and at 100% of the monthly allowance, and that is what slice 5.7 built (D-126). Walking 5.8a, the founder's judgement was that the people working the queue do not need the month's running total at all: "we don't have to keep reminding them how many arrived or how many is left for the plan ... for the reviewer, we'll just flash a message once they are within 5-10% of their limit."

**Decision (23 Sept 2026):**
- The running numbers live on the **admin's dashboard**, where they already are ("Arrived" and "Counted toward your plan -- 27 / 300"), and are shown there at any usage level. That is the person who is billed and who can act on them.
- The **banner** on the tenant's own screens appears only from `ALLOWANCE_BANNER_THRESHOLD` (0.9) up -- within 5-10% of the limit -- and above that point is still the crossed threshold's wording, so it becomes the 100% entry once the allowance is spent.
- The **80% and 100% thresholds are unchanged**: the one email per threshold per month to the account's admin, and the founder's `allowance_reached` alert at 100%, fire exactly as 7.16.1 requires. `ALLOWANCE_THRESHOLDS` still reads `(0.8, 1.0)`.

**Why this is a change to a constant, not to the rule:** 7.15.4 lists the allowance thresholds among the constants that must never be hardcoded, which is where this lives; the banner threshold is a second constant beside them, and a test asserts it is the later of the two so the two cannot silently collapse back together. Everything 7.16.1 exists to guarantee survives: nothing is blocked, delayed or degraded; the customer is told before they pass the line; the founder still gets the sales signal. What moved is which screen carries the reminder, which 7.16.1 does not specify. The deviation is deliberate and recorded here: **the banner is no longer shown at 80%.**

**Also confirmed by the founder, already built in 5.8a:** a reviewer who reaches `/dashboard` is told they do not have permission, from the catalog (`AUTH-003`, 403) -- not a 404. See D-128 for why this differs from the Console's 404.

**Related:** Section 7.16.1, 7.15.4, 7.16.5; D-126, D-128.


## D-130 -- The activity trail: one query, two screens, and the reviewers can read it (slice 5.8b)

**Context:** Section 6's Phase 5 line asks for an "audit log view" and `docflow-mvp-features.docx` for an "audit trail -- who changed what, when". 5.8a put the last ten events on the admin's dashboard; this is the whole trail, paged and filtered.

**Decisions:**
- **Reviewers see it, not only the admin** (the founder's decision in the 5.8 scoping). The people doing the work are the ones who need to know what a colleague already did, and an audit trail only the boss can read is a weaker check on the work, not a stronger one. It is a read, but not a `member` read: a `viewer`, if one is ever created, does not get it, so the route carries the reviewer role set.
- **One query, two screens.** `tenant_home._EVENTS_CTE` -- the union of `review_actions`, finished `exports` and released documents -- is now written once and read by both `recent_activity` (the dashboard's ten) and `activity_page` (this page, filtered and paged). A test asserts the dashboard's rows are the first rows of the trail, so the two cannot drift apart and then disagree in front of a customer.
- **The filter chips come from the API**, not from a second list in the page, for the same reason. An unknown kind in the query string is dropped rather than refused: a stale bookmark should show the whole list, not an error.
- **`total` counts the whole filtered set**, not the page, so the screen can say "Showing 51-61 of 61" -- what a person needs to know is how much there is, not how much is on screen.
- **Still no extracted value.** An edit says "3 fields", never what they became (Section 7.10); the PO number and filename are carried so the row can link to the order, where the values properly live. A test asserts a planted value never appears in the response.
- **Work the founder did inside the account is labelled "DocFlow support"** and the founder's own address is not shown (7.15.1), exactly as on the dashboard -- it is the same rendering component.
- **Ordering** is `at DESC, sequence DESC NULLS LAST`: `now()` is frozen per transaction, so an edit and the approval written with it share a timestamp and only `review_actions.sequence` says which came first (D-084, and the trap D-123 hit).

**Verified against the running stack**, not only in tests: a real reviewer read the trail for three orders (six events), paged through it, filtered to edits, was shown no extracted value, and the admin's dashboard row matched the trail's first row exactly; a viewer was refused with `AUTH-002`.

**Related:** Section 6 (Phase 5), 7.10, 7.15.1, Section 3; D-084, D-123, D-128.

## D-131 -- The "needs review" digest email (slice 5.8c)

**Context:** Section 6's Phase 5 line asks for "email notifications", and a customer's reviewers need to know that orders are waiting without watching the screen. The 5.8 scoping settled the shape with the founder: a digest, at most one email per tenant every 15 minutes, to owner, admin and reviewers (not viewers), counts only, no re-nagging -- because a 500-document backfill (Section 5.1) must not send 500 emails.

**Decisions:**
- **A scheduled job, not a new mechanism.** The digest is a `review_digest` row in `scheduled_jobs` (0013), run by the existing beat sweep in the tenant's own session, retried and reported to the founder on failure like every other job. Migration `0022_review_digest.sql` only widens the job-type check and adds two indexes.
- **One pending digest per tenant, enforced by the database.** When an order reaches `needs_review`, the worker upserts its id into the tenant's pending digest (a partial unique index on `(tenant_id) WHERE job_type = 'review_digest' AND status = 'pending'`), so two workers finishing orders at the same moment extend one job instead of creating two. Once the sweep claims the job it leaves the index, so an order that arrives while the email is being written starts the *next* digest rather than being lost.
- **The timer starts at the first new order.** The job runs `REVIEW_DIGEST_INTERVAL_MIN` (15, in `constants.py`) after the order that created it, and never sooner than that after the previous digest began. With the sweep every five minutes, an email goes 15-20 minutes after the first order of a batch.
- **No re-nagging.** Only a newly arrived order creates a digest. Orders that simply sit in the queue never produce another email; if every order a digest collected was reviewed before it fired, nothing is sent. What the job decided (sent to how many, or why it skipped) is written onto its own row as counts, so the Console can explain a missing email.
- **Counts only (Section 7.10).** The email says how many new orders are ready, how many wait in all, and roughly how long the oldest has waited ("about 3 hours" -- relative, so no timezone is involved), with a link to the queue. No PO number, buyer, filename or value. The job's payload holds document ids, never content.
- **Who gets it:** active, undeleted users with the owner, admin or reviewer role. A viewer does not. A person whose invite is not yet accepted still gets it -- it is a reason to sign in.
- **Who does not:** test-batch orders never start a digest (the founder reviews them during onboarding), and a tenant that is not live, or is suspended or pending deletion, is not emailed. `cancelling` tenants still are: everything keeps working until the effective date (7.14).
- **Wiring:** the worker calls `review_digest.note_needs_review` last, in its own transaction, after validation -- the same rule as every post-extraction step (D-058): an order ready for review never loses its extraction over a notification.
- **Not built:** a per-person opt-out (needs a settings page; nobody has asked -- open item), and re-sending on a reopened (approved -> needs review) order, which is a human action inside the account, not an arrival.

**Verified against the running stack**, not only in tests: an order sent to a live tenant's intake address was extracted by the real worker, which created the digest due exactly 15 minutes later; the real beat sweep sent it 19 minutes after arrival, to the account's admin, with counts only (held in the outbox, no provider configured).

**Related:** Section 5.1, 6 (Phase 5), 7.9, 7.10, 7.14; D-058, D-103 (outbox), D-113 (scheduled jobs), D-128.
