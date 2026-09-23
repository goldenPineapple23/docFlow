# DocFlow

DocFlow extracts structured data from unstructured inbound purchase orders (PDF, Word, Excel, email, faxes, scans, photos), matches every line item against a distributor's own SKU catalog, presents it for fast human review, and exports clean order data as a file the distributor imports into their own system.

**Start here:** `CLAUDE.md` (working rules — read this before touching code), `docs/BUILD-STATUS.md` (every phase and slice: status, decisions, migrations, what's next), `DECISIONS.md` (every judgment call made and why), `CHECKPOINTS.md` (end-of-phase summaries), `SETUP.md` (external account setup), `docs/docflow-claude-code-build-prompt-v2.docx` (the full build spec).

## Status

Phase 0 (Foundations) — in progress.

## Repo layout

```
apps/
  web/      Next.js frontend — tenant surface + Founder Console (/admin/*)
  api/      FastAPI backend — auth, tenant-scoped data access, adminDataAccess
  worker/   Celery workers — extraction, file parsing/conversion, nightly rollup
packages/
  core/     Shared Python: config, data-access layer, file allowlist, error catalog
supabase/
  migrations/   SQL migrations, applied in order, never edited after landing
docs/       Business/spec documents (read-only reference — see docs/ for the full list)
```

## Local development

See `SETUP.md` for creating the required external accounts (Supabase, Anthropic, Stripe) and filling in `.env`. Once that's done:

```
# Frontend
cd apps/web && npm install && npm run dev

# Backend API
cd apps/api && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
uvicorn app.main:app --reload

# Worker (needs Redis running -- Memurai on Windows, see DECISIONS.md D-095)
# --pool=solo is required on Windows: Celery's default prefork pool does not run there.
cd apps/worker && celery -A app.celery_app worker --loglevel=info --pool=solo -Q interactive,bulk

# Scheduler (a second terminal, alongside the worker). Every 5 minutes it asks
# the worker to run due scheduled jobs -- the first-week check-in today, the
# lifecycle jobs later (DECISIONS.md D-113). Jobs live in the database, so
# stopping this only delays them; nothing is lost.
cd apps/worker && celery -A app.celery_app beat --loglevel=info
```

(Exact commands will be filled in as each piece is scaffolded — this section is being written as we go, not after the fact.)

## Architecture

Next.js (TypeScript) frontend + FastAPI (Python) backend/worker, Supabase (Postgres + Auth + Storage), Celery + Redis job queue. See `DECISIONS.md` D-001 through D-003 for why, and `CLAUDE.md` for the full set of guardrails this build follows.
