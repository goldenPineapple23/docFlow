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
