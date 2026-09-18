-- DocFlow — Phase 5, slice 5.1 (Founder Console foundations)
--
-- New tables:
--   tiers                   GLOBAL (no tenant_id) -- named here per Section 10.
--   onboarding_intakes      GLOBAL -- pre-tenant by definition (Section 7.15.2 Step 1).
--   onboarding_intake_files GLOBAL -- the files of an intake, before any tenant exists.
--   email_outbox            tenant-scoped (tenant_id nullable for founder-only mail).
--   founder_alerts          tenant_id nullable; readable by platform admins only.
-- New columns: tenants.tier_id, tenants.onboarding_intake_id.
--
-- Recorded as DECISIONS.md D-102 .. D-105.


-- ── tiers (GLOBAL) ──────────────────────────────────────────────────────────
-- Section 7.15.2: "Tier names, monthly prices, setup fees, and document
-- allowances come from docflow-pricing.docx and live in a versioned tiers
-- config table (or typed config file -- propose which), referenced by
-- tenants.tier. ... Changing a tier's price creates a new version; existing
-- tenants keep their version until the founder explicitly moves them."
--
-- A table, not a config file (D-102): a tenant must point at the exact
-- version it signed up on, which a foreign key does and a file cannot, and the
-- dashboard's MRR is a SQL sum over this table (7.15.3). Setup fees are NOT
-- here: docflow-pricing.docx makes them a per-customer choice ($750 founding,
-- $1,500 standard, $2,000-2,500 complex), so they are chosen at go-live and
-- recorded on the tenant (slice 5.3).
create table tiers (
    id                   uuid primary key default gen_random_uuid(),
    code                 text not null check (code in ('starter', 'growth', 'scale')),
    version              integer not null check (version >= 1),
    name                 text not null,
    monthly_price        numeric(10,2) not null check (monthly_price >= 0),
    -- The founding-customer promo from docflow-pricing.docx ("first 90 days").
    promo_monthly_price  numeric(10,2) check (promo_monthly_price >= 0),
    promo_days           integer check (promo_days > 0),
    -- Section 7.16.1: soft allowance, never a literal in code.
    document_allowance   integer not null check (document_allowance > 0),
    -- The version offered to NEW tenants. Exactly one per code.
    is_current           boolean not null default true,
    created_at           timestamptz not null default now(),
    unique (code, version)
);
create unique index idx_tiers_one_current on tiers(code) where is_current;

alter table tiers enable row level security;
-- Tier prices are not secret from a tenant (they are on the pricing page),
-- and the allowance banner (7.16.1) must read them in a tenant session.
create policy read_all on tiers for select using (true);
create policy platform_admin_write on tiers
    using (current_setting('app.is_platform_admin', true) = 'true');

-- Version 1, exactly as docflow-pricing.docx (Version 1, September 2026) and
-- Section 7.16.1 state them. Real configuration, not test data.
insert into tiers (code, version, name, monthly_price, promo_monthly_price, promo_days, document_allowance)
values
    ('starter', 1, 'Starter', 299.00, 199.00, 90, 300),
    ('growth',  1, 'Growth',  399.00, 249.00, 90, 1000),
    ('scale',   1, 'Scale',   599.00, 349.00, 90, 3000);

alter table tenants add column tier_id uuid references tiers(id);


-- ── onboarding_intakes / onboarding_intake_files (GLOBAL) ───────────────────
-- Section 7.15.2 Step 1: the prospect's files, uploaded by the founder into
-- staging before any tenant exists. "Nothing in staging is associated with
-- any tenant." Every 7.11 rule applies to them -- a prospect's catalog is an
-- untrusted file -- so each file is validated by the same allowlist as
-- production intake before it is stored.
create table onboarding_intakes (
    id               uuid primary key default gen_random_uuid(),
    prospect_name    text not null,
    contact_email    text,
    received_at      timestamptz not null default now(),
    -- How the files reached the founder. "email" is the MVP's intake form
    -- (the founder's inbox, Section 7.15.2 Step 1).
    source           text not null default 'email' check (source in ('email', 'other')),
    notes            text,
    -- Set when Step 2 creates the tenant and moves the files.
    linked_tenant_id uuid references tenants(id),
    linked_at        timestamptz,
    created_by       uuid not null references users(id),
    created_at       timestamptz not null default now(),
    deleted_at       timestamptz
);
create index idx_onboarding_intakes_open on onboarding_intakes(received_at) where deleted_at is null and linked_tenant_id is null;

create table onboarding_intake_files (
    id                uuid primary key default gen_random_uuid(),
    intake_id         uuid not null references onboarding_intakes(id),
    -- Metadata only; never used in a path (Section 7.11).
    original_filename text not null,
    -- staging/{intake_id}/... until Step 2 moves it to
    -- tenants/{tenant_id}/onboarding/... (then this column is updated).
    storage_path      text not null,
    sha256            text not null,
    byte_size         integer not null,
    detected_type     text not null,
    created_at        timestamptz not null default now(),
    deleted_at        timestamptz
);
create index idx_onboarding_intake_files_intake on onboarding_intake_files(intake_id) where deleted_at is null;

alter table tenants add column onboarding_intake_id uuid references onboarding_intakes(id);

-- Platform admins only. There is no tenant to scope these to, and no tenant
-- session may ever read them.
alter table onboarding_intakes enable row level security;
create policy platform_admin_access on onboarding_intakes
    using (current_setting('app.is_platform_admin', true) = 'true');
alter table onboarding_intake_files enable row level security;
create policy platform_admin_access on onboarding_intake_files
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── email_outbox ────────────────────────────────────────────────────────────
-- Every email DocFlow sends is a row here first; the sender delivers from the
-- row. Until an email provider is configured, rows stay `held` and the
-- founder can read them in the Console -- nothing is silently dropped, and
-- nothing is sent from a code path that leaves no record (D-103).
create table email_outbox (
    id                  uuid primary key default gen_random_uuid(),
    -- NULL for mail that belongs to no tenant (founder alerts, staging).
    tenant_id           uuid references tenants(id) on delete cascade,
    to_address          text not null,
    -- A name from the templates in the repo (Section 7.15.2 Step 9:
    -- "templates in the repo, not hardcoded strings").
    template            text not null,
    subject             text not null,
    body_text           text not null,
    status              text not null default 'queued'
                        check (status in ('queued', 'held', 'sent', 'failed')),
    provider_message_id text,
    -- A short error type, never provider response text (Section 7.16.5).
    error               text,
    -- What the email is about, for the Console ("invite for user X").
    related_type        text,
    related_id          uuid,
    created_at          timestamptz not null default now(),
    sent_at             timestamptz
);
create index idx_email_outbox_pending on email_outbox(created_at) where status in ('queued', 'held');
create index idx_email_outbox_tenant on email_outbox(tenant_id, created_at desc);

alter table email_outbox enable row level security;
-- Platform admins only for now: an invite email carries a sign-in link, and
-- no tenant screen reads the outbox. A tenant-facing "emails we sent you"
-- view would get its own narrow policy.
create policy platform_admin_access on email_outbox
    using (current_setting('app.is_platform_admin', true) = 'true');
-- A tenant session (a notification from the worker, an allowance banner) may
-- queue mail for its own tenant, and nothing else: it cannot read the outbox
-- back, and cannot queue for another tenant.
create policy tenant_enqueue on email_outbox for insert
    with check (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);


-- ── founder_alerts ──────────────────────────────────────────────────────────
-- Section 7.9: "One alert, one row, two channels. Every alert condition
-- writes a founder_alerts row first; the email is sent from that row, and the
-- Console's attention panel reads the same row." Section 7.15.3 names the
-- columns: type, severity, tenant_id nullable, payload, created_at,
-- acknowledged_at, acknowledged_by.
create table founder_alerts (
    id               uuid primary key default gen_random_uuid(),
    type             text not null,
    severity         text not null check (severity in ('info', 'warning', 'high', 'critical')),
    tenant_id        uuid references tenants(id),
    payload          jsonb not null default '{}'::jsonb,
    -- One open alert per condition: a document stuck for three hours is one
    -- alert, not 180. NULL means "never collapse".
    dedupe_key       text,
    email_outbox_id  uuid references email_outbox(id),
    created_at       timestamptz not null default now(),
    acknowledged_at  timestamptz,
    acknowledged_by  uuid references users(id)
);
create unique index idx_founder_alerts_open_dedupe on founder_alerts(dedupe_key)
    where dedupe_key is not null and acknowledged_at is null;
create index idx_founder_alerts_open on founder_alerts(severity, created_at) where acknowledged_at is null;

alter table founder_alerts enable row level security;
create policy platform_admin_access on founder_alerts
    using (current_setting('app.is_platform_admin', true) = 'true');
-- Alert conditions are often detected inside a tenant's own session (an
-- export integrity failure in the worker, an allowance crossing). Such a
-- session may RAISE an alert about its own tenant; only a platform admin can
-- read or acknowledge one.
create policy tenant_raise on founder_alerts for insert
    with check (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
