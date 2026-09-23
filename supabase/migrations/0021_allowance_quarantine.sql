-- DocFlow — Phase 5, slice 5.7: allowances and quarantine (CLAUDE.md 7.16)
--
-- Recorded as DECISIONS.md D-126. Plan: docs/plans/5.7-allowance-quarantine.md.
--
-- Adds, all additive (nothing is dropped, no data is touched):
--   * documents: who released a held document, and when.
--   * documents.quarantine_reason: one new value, 'sender_not_allowed', for the
--     opt-in strict sender-allowlist mode (7.16.3). It quarantines, never rejects.
--   * intake_addresses.grace_ends_at: when a rotated address stops auto-replying.
--   * tenants.strict_sender_mode / sender_allowlist: the opt-in allowlist.
--   * allowance_notices: one row per tenant, month and threshold, so "one email
--     per threshold per month" is enforced by the database, not by hope.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

-- ── documents: release provenance ───────────────────────────────────────────
alter table documents
    add column released_at                  timestamptz,
    add column released_by_user_id          uuid references users(id),
    -- Set when the founder released it from the Console (7.15.1 acting-as).
    add column released_acting_as_tenant_id uuid references tenants(id);

-- The one existing constraint, widened by one value. The six spec reasons are
-- unchanged.
alter table documents drop constraint documents_quarantine_reason_check;
alter table documents add constraint documents_quarantine_reason_check
    check (quarantine_reason in
           ('abuse_ceiling', 'cost_breaker', 'attachment_cap', 'auth_fail',
            'unknown_sender_velocity', 'manual', 'sender_not_allowed'));

-- Held documents are listed per tenant, newest first.
create index idx_documents_tenant_quarantined on documents(tenant_id, created_at)
    where status = 'quarantined' and deleted_at is null;

-- ── intake_addresses: rotation grace period ─────────────────────────────────
alter table intake_addresses add column grace_ends_at timestamptz;

-- ── tenants: strict sender allowlist (opt-in; never the default) ────────────
alter table tenants
    add column strict_sender_mode boolean not null default false,
    add column sender_allowlist   text[]  not null default '{}';

-- ── allowance_notices (tenant-scoped) ───────────────────────────────────────
create table allowance_notices (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null references tenants(id),
    -- Calendar month in the tenant's own timezone, 'YYYY-MM'.
    month         text not null,
    -- 80 or 100 (ALLOWANCE_THRESHOLDS as whole percents).
    threshold_pct integer not null check (threshold_pct in (80, 100)),
    used          integer not null,
    allowance     integer not null,
    created_at    timestamptz not null default now(),
    unique (tenant_id, month, threshold_pct)
);
create index idx_allowance_notices_tenant on allowance_notices(tenant_id, created_at desc);

alter table allowance_notices enable row level security;
create policy tenant_isolation on allowance_notices
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on allowance_notices
    using (current_setting('app.is_platform_admin', true) = 'true');
