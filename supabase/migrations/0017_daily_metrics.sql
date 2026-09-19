-- DocFlow — Phase 5, slice 5.5 (founder dashboard: the nightly rollup)
--
-- Recorded as DECISIONS.md D-121.
--
-- New tables: tenant_daily_metrics (tenant-scoped), rollup_runs (GLOBAL --
--             named here per Section 10, it is about the job, not a tenant).
--
-- Section 7.15.3: "computing these on page load means scanning ~50,000
-- documents. Instead, a nightly job (plus an on-demand 'recompute' button)
-- writes `tenant_daily_metrics` -- one row per tenant per day with the counts
-- and sums above -- and the dashboard reads from it. The rollup excludes
-- is_test_batch documents. The job is idempotent (re-running a day overwrites
-- it) and its last-run time is shown on the dashboard; a rollup that hasn't
-- run in 36 hours is itself a founder_alerts row."


-- ── tenant_daily_metrics ────────────────────────────────────────────────────
-- One row per tenant per day, in the TENANT'S OWN timezone -- the same day
-- boundary the monthly allowance uses (Section 7.16.1), so the two can never
-- disagree about which month a document fell in.
--
-- Counts and sums, not documents: nothing here identifies an order, and no
-- customer data is stored (Section 7.10). The two jsonb arrays hold plain
-- numbers (hours, dollars) so the dashboard can compute a median and a p95
-- across any window -- a daily average of averages would be wrong, and the
-- alternative is the page scan this table exists to avoid.
create table tenant_daily_metrics (
    tenant_id                uuid not null references tenants(id) on delete cascade,
    day                      date not null,

    -- Volume (is_test_batch excluded everywhere in this table).
    documents_received       integer not null default 0,
    documents_failed         integer not null default 0,
    documents_approved       integer not null default 0,
    documents_exported       integer not null default 0,

    -- Quality.
    zero_edit_approvals      integer not null default 0,
    edited_actions           integer not null default 0,
    line_items               integer not null default 0,
    matched_lines            integer not null default 0,
    learned_rule_lines       integer not null default 0,
    confidence_sum           numeric(12,4) not null default 0,
    confidence_count         integer not null default 0,

    -- Speed. `review_sessions_excluded` are the ones longer than
    -- ABANDONED_REVIEW_CEILING_MIN, shown next to the KPI rather than hidden.
    review_within_target     integer not null default 0,
    review_sessions          integer not null default 0,
    review_sessions_excluded integer not null default 0,
    -- Hours from receipt to approval, one number per document approved that
    -- day, so the dashboard's median is a real median.
    approval_hours           jsonb not null default '[]'::jsonb,

    -- Cost. One number per document processed that day, for mean and p95.
    document_costs           jsonb not null default '[]'::jsonb,
    est_cost_usd             numeric(12,4) not null default 0,
    input_tokens             bigint not null default 0,
    output_tokens            bigint not null default 0,

    computed_at              timestamptz not null default now(),
    primary key (tenant_id, day)
);
create index idx_tenant_daily_metrics_day on tenant_daily_metrics(day);

alter table tenant_daily_metrics enable row level security;
-- A tenant may read its own numbers (the per-tenant dashboard, slice 5.8).
create policy tenant_isolation on tenant_daily_metrics
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on tenant_daily_metrics
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── rollup_runs (GLOBAL) ────────────────────────────────────────────────────
-- When the rollup last ran, over what, and whether it finished. The dashboard
-- shows the last run, and a gap beyond ROLLUP_STALE_HOURS raises the alert
-- the section asks for. Global because the job spans every tenant; it holds
-- no tenant data beyond counts.
create table rollup_runs (
    id            uuid primary key default gen_random_uuid(),
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    -- 'nightly' | 'manual' (the dashboard's recompute button).
    trigger       text not null default 'nightly' check (trigger in ('nightly', 'manual')),
    day_from      date,
    day_to        date,
    tenants       integer not null default 0,
    rows_written  integer not null default 0,
    ok            boolean,
    -- An error-catalog code or an exception type, never content.
    error         text
);
create index idx_rollup_runs_started on rollup_runs(started_at desc);

alter table rollup_runs enable row level security;
create policy platform_admin_access on rollup_runs
    using (current_setting('app.is_platform_admin', true) = 'true');
-- The rollup's own narrow session writes these rows (docflow_core.db.
-- rollup_session), like the scheduler's access to scheduled_jobs (0013).
create policy rollup_access on rollup_runs
    using (current_setting('app.rollup', true) = 'true');


-- ── tenants: the rollup may list them ───────────────────────────────────────
-- The only cross-tenant thing the rollup needs: which tenants exist, in what
-- timezone. Every number it then computes is read and written inside that
-- tenant's own session, under the ordinary RLS policies. This is not the
-- Section 7.15.1 admin bypass and it cannot read a document: SELECT on one
-- table, for a session nothing but the rollup job opens.
create policy rollup_read on tenants for select
    using (current_setting('app.rollup', true) = 'true');


-- ── founder_alerts / email_outbox: the rollup may raise its own alarm ───────
-- "A rollup that hasn't run in 36 hours is itself a founder_alerts row"
-- (7.15.3) -- and that alert has no tenant, so the tenant_raise policy (0011)
-- cannot carry it. Rather than hand the worker the Section 7.15.1 admin
-- bypass, the rollup session may raise exactly one type of alert, about
-- itself, and queue its email. It still cannot read any other alert.
create policy rollup_raise on founder_alerts for insert
    with check (current_setting('app.rollup', true) = 'true'
                AND type = 'rollup_stale' AND tenant_id IS NULL);
create policy rollup_read_own on founder_alerts for select
    using (current_setting('app.rollup', true) = 'true' AND type = 'rollup_stale');
create policy rollup_enqueue on email_outbox for insert
    with check (current_setting('app.rollup', true) = 'true' AND tenant_id IS NULL);
