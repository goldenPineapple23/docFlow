-- DocFlow — Phase 5, slice 5.3 (test batch, acting-as review, go-live)
--
-- Section 7.15.2 Steps 6-9. Recorded as DECISIONS.md D-111 .. D-114.
--
-- New status:   documents.status 'staged' (a test-batch file stored and
--               waiting for the founder's "Run extraction").
-- New table:    scheduled_jobs (tenant_id nullable; scheduler-readable).
-- New columns:  tenants.setup_fee_*, tenants.founding_price,
--               tenants.test_batch_completed_at, learned_rules.acting_as_tenant_id.


-- ── documents: 'staged' ─────────────────────────────────────────────────────
-- Step 6 uploads the test batch; Step 7 is a separate "Run extraction". In
-- between, the files are stored and validated but never sent to the model.
-- A distinct status rather than 'pending', so nothing that watches pending
-- documents (the stuck-processing alert, D-095) mistakes a file that is
-- deliberately waiting for one that is stuck.
alter table documents drop constraint documents_status_check;
alter table documents add constraint documents_status_check
    check (status in
           ('staged','pending','processing','needs_review','failed',
            'quarantined','approved','rejected','exported'));


-- ── learned_rules: who confirmed it, as whom ────────────────────────────────
-- Section 7.15.1: a mapping the founder confirms while reviewing a tenant's
-- test batch records the founder's own user id (confirmed_by) AND the tenant
-- acted in, so the tenant sees it as DocFlow support.
alter table learned_rules
    add column acting_as_tenant_id uuid references tenants(id) on delete set null;


-- ── tenants: go-live billing choices ────────────────────────────────────────
-- Setup fees are chosen per customer at go-live (D-102), and either added to
-- the first Stripe invoice or invoiced by hand ("docflow-mvp-features.docx
-- allows manual invoicing at first" -- Section 7.15.2). The founding-customer
-- price is the tier's promo, applied by a Stripe coupon for its promo_days.
alter table tenants
    add column setup_fee_amount        numeric(10,2) check (setup_fee_amount >= 0),
    add column setup_fee_billing       text check (setup_fee_billing in ('stripe', 'invoiced_manually')),
    add column setup_fee_note          text,
    add column founding_price          boolean not null default false,
    add column test_batch_completed_at timestamptz,
    add constraint tenants_live_has_billing check (
        onboarding_status <> 'live' or setup_fee_billing is not null
    ) not valid;
-- NOT VALID: checked for every new or changed row from now on, without
-- re-checking rows written before this migration.


-- ── scheduled_jobs ──────────────────────────────────────────────────────────
-- "Schedules the first-week check-in as a job that, seven days later, emails
-- the customer and creates a founder alert" (Step 9). A row, not a queue
-- message with a countdown: it survives a Redis restart, it is visible, and
-- the lifecycle work still to come (effective dates, reminders, the nightly
-- rollup) needs the same thing. A beat task sweeps due rows every few
-- minutes; each job then runs in its own tenant's session.
create table scheduled_jobs (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid references tenants(id) on delete cascade,
    job_type      text not null check (job_type in ('first_week_checkin')),
    run_at        timestamptz not null,
    payload       jsonb not null default '{}'::jsonb,
    status        text not null default 'pending'
                  check (status in ('pending', 'running', 'done', 'failed', 'cancelled')),
    attempts      integer not null default 0,
    last_error    text,          -- an error-catalog code or exception type, never content
    -- One job of a kind per tenant where that matters ('first_week_checkin').
    dedupe_key    text unique,
    created_at    timestamptz not null default now(),
    started_at    timestamptz,
    completed_at  timestamptz
);
create index idx_scheduled_jobs_due on scheduled_jobs(run_at) where status = 'pending';

alter table scheduled_jobs enable row level security;
create policy tenant_isolation on scheduled_jobs
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on scheduled_jobs
    using (current_setting('app.is_platform_admin', true) = 'true');
-- The sweep's own narrow session (docflow_core.db.scheduler_session): it can
-- see and claim scheduled_jobs rows and nothing else, like the token and
-- identity lookups. The job itself then runs in a tenant session.
create policy scheduler_access on scheduled_jobs
    using (current_setting('app.scheduler', true) = 'true');
