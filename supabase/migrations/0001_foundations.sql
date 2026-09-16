-- DocFlow — Phase 0: Foundations
-- Tables: tenants, users, platform_admins, admin_actions, intake_addresses,
-- tenant_lifecycle_events. Everything else (customers, items, documents,
-- extraction tables, learned_rules, etc.) lands in its own phase's migration
-- per CLAUDE.md's "work phase by phase" rule.
--
-- Prerequisite (SETUP.md Step 1.6): a "docflow_app" Postgres role must
-- already exist with NOBYPASSRLS, and DATABASE_URL must connect as that
-- role. If this migration is applied via the default "postgres" role
-- (which has BYPASSRLS), the RLS policies below will still be created
-- correctly, but only take effect for connections that use docflow_app.
--
-- RLS model (see packages/core/docflow_core/db.py for the full explanation):
--   - tenant_isolation: tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
--   - platform_admin_access: current_setting('app.is_platform_admin', true) = 'true'
--   - users also gets self_lookup: auth_user_id = nullif(current_setting('app.auth_user_id', true), '')::uuid
-- Postgres RLS policies are permissive and OR'd together, so a row is
-- visible if ANY policy matches.
--
-- The nullif(..., '')::uuid wrapper (rather than a bare ::uuid cast) matters
-- because DATABASE_URL is a transaction-mode pooler connection: RESET on a
-- custom GUC restores it to an empty string, not SQL NULL, and a bare
-- ''::uuid cast raises an error rather than evaluating to false. See
-- docflow_core.db._reset_rls_settings for the corresponding app-side defense.

create extension if not exists pgcrypto;

-- ── tenants ──────────────────────────────────────────────────────────────
-- Note: tenants is keyed by its own id, not a separate tenant_id column, so
-- its RLS policies compare `id` instead of `tenant_id`.
create table tenants (
    id                 uuid primary key default gen_random_uuid(),
    name               text not null,
    slug               text unique,
    primary_currency   char(3) not null default 'USD',
    timezone           text not null default 'UTC',
    status             text not null default 'active'
                       check (status in ('active','cancelling','suspended','pending_deletion','deleted')),
    status_changed_at  timestamptz not null default now(),
    cancellation_effective_at timestamptz,
    cancellation_reason text check (cancellation_reason in ('customer_requested','non_payment','for_cause')),
    deletion_scheduled_at timestamptz,
    deleted_at         timestamptz,
    deleted_by         uuid,
    deletion_reason    text,
    onboarding_status  text not null default 'tenant_created'
                       check (onboarding_status in
                              ('tenant_created','catalog_loaded','test_batch_uploaded',
                               'test_batch_running','test_batch_complete','live')),
    went_live_at       timestamptz,
    invite_sent_at     timestamptz,
    intake_address_active boolean not null default false,
    stripe_customer_id text,
    stripe_subscription_id text,
    stripe_subscription_status text,
    stripe_current_period_end timestamptz,
    example_prompting_enabled boolean not null default false,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);
-- tier_id and intake_id foreign keys are added once the tiers and
-- onboarding_intakes tables exist (Phase 5).

alter table tenants enable row level security;

create policy tenant_isolation on tenants
    using (id = nullif(current_setting('app.tenant_id', true), '')::uuid);

-- A bare USING clause (no FOR clause) governs SELECT/UPDATE/DELETE directly
-- and, per Postgres's default, doubles as WITH CHECK for INSERT/UPDATE when
-- no separate WITH CHECK is given -- so this one policy is enough to let
-- the Phase 0 tenant-creation transaction (admin_data_access.create_tenant)
-- both write and later read the row it created.
create policy platform_admin_access on tenants
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── users ────────────────────────────────────────────────────────────────
-- tenant_id is nullable to support platform-admin-only accounts that belong
-- to no tenant at all (DECISIONS.md D-004) -- the founder is not a tenant
-- role (CLAUDE.md Section 3).
create table users (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid references tenants(id) on delete cascade,
    auth_user_id  uuid unique,  -- Supabase Auth user id, set once the invite is accepted (D-012)
    email         text not null,
    full_name     text,
    role          text not null default 'reviewer'
                  check (role in ('owner','admin','reviewer','viewer')),
    is_active     boolean not null default true,
    invite_sent_at timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    deleted_at    timestamptz
);
create index idx_users_tenant on users(tenant_id);
-- A platform-admin-only user (tenant_id IS NULL) still needs a globally
-- unique email; the base schema's UNIQUE(tenant_id, email) allows duplicate
-- emails across NULL-tenant rows since Postgres treats NULLs as distinct.
create unique index idx_users_tenant_email on users(tenant_id, email) where tenant_id is not null;
create unique index idx_users_email_global on users(email) where tenant_id is null;

alter table users enable row level security;

create policy tenant_isolation on users
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on users
    using (current_setting('app.is_platform_admin', true) = 'true');

-- Narrow self-lookup so app/deps.py can resolve "which user is this auth
-- token for" without needing the platform-admin bypass for an ordinary
-- tenant user's own identity check (see packages/core/docflow_core/db.py).
create policy self_lookup on users
    using (auth_user_id = nullif(current_setting('app.auth_user_id', true), '')::uuid);


-- ══════════════════ GLOBAL TABLES — no tenant_id, no RLS ══════════════════
-- platform_admins and admin_actions are the only two Phase 0 tables that are
-- genuinely global. No API ever writes to platform_admins (seeded by
-- migration or RUNBOOK.md CLI only); admin_actions is written only by
-- docflow_core.admin_data_access.

create table platform_admins (
    user_id     uuid primary key references users(id),
    granted_at  timestamptz not null default now(),
    granted_by  uuid references users(id),
    revoked_at  timestamptz
);

create table admin_actions (
    id                     uuid primary key default gen_random_uuid(),
    platform_admin_user_id uuid not null references users(id),
    action                 text not null,
    target_tenant_id       uuid references tenants(id),
    target_type            text,
    target_id              uuid,
    payload                jsonb,
    created_at             timestamptz not null default now()
);
create index idx_admin_actions_tenant on admin_actions(target_tenant_id, created_at desc);
create index idx_admin_actions_admin on admin_actions(platform_admin_user_id, created_at desc);


-- ── intake_addresses (tenant-scoped) ────────────────────────────────────
create table intake_addresses (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references tenants(id) on delete cascade,
    token       text not null unique,
    address     text not null unique,
    status      text not null default 'active' check (status in ('active','grace','retired')),
    created_at  timestamptz not null default now(),
    retired_at  timestamptz
);
create unique index idx_intake_addresses_one_active on intake_addresses(tenant_id) where status = 'active';

alter table intake_addresses enable row level security;

create policy tenant_isolation on intake_addresses
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on intake_addresses
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── tenant_lifecycle_events (tenant-scoped, but outlives "deletion") ─────
-- No ON DELETE CASCADE on tenant_id: a tenant's lifecycle log must survive
-- even after its business data is purged (CLAUDE.md Section 7.14) --
-- deletion soft-deletes the tenants row (sets deleted_at), it never drops it.
create table tenant_lifecycle_events (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references tenants(id),
    event_type          text not null,
    actor_user_id       uuid references users(id),
    reason              text,
    constants_in_effect jsonb not null default '{}'::jsonb,
    payload             jsonb,
    created_at          timestamptz not null default now()
);
create index idx_tenant_lifecycle_events_tenant on tenant_lifecycle_events(tenant_id, created_at desc);

alter table tenant_lifecycle_events enable row level security;

create policy tenant_isolation on tenant_lifecycle_events
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on tenant_lifecycle_events
    using (current_setting('app.is_platform_admin', true) = 'true');
