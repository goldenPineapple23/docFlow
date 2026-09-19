-- DocFlow — Phase 5, slice 5.4 part 2 (per-tenant field schema)
--
-- Recorded as DECISIONS.md D-120.
--
-- New table:    tenant_field_schemas (tenant-scoped, versioned).
-- New column:   documents.field_schema_version.
--
-- Section 7.13: "Per-tenant field schema, stored in the database and
-- versioned. The extraction schema in Section 8.1 is the base; a tenant may
-- mark fields required/optional/hidden ... Every extraction logs the schema
-- version it ran against."
--
-- Scope (D-120): this is required / optional / hidden only. Custom fields
-- (resin_grade, job_number) are the other half of 7.13 and wait for a real
-- customer requirement, because they change what the model is asked for and
-- every such change needs a live golden-set run before it ships.


-- ── tenant_field_schemas ────────────────────────────────────────────────────
-- One row per version. `fields` holds only what differs from the built-in
-- default, so a field DocFlow adds later starts at its default everywhere:
--   {"header": {"payment_terms": "hidden", "order_date": "required"},
--    "line":   {"unit": "hidden"}}
-- Values: 'required' | 'optional' | 'hidden'.
create table tenant_field_schemas (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references tenants(id) on delete cascade,
    version             integer not null check (version >= 1),
    fields              jsonb not null default '{}'::jsonb,
    -- Why the founder changed it, for the audit trail. Never customer data.
    note                text,
    created_by          uuid references users(id),
    -- Set when the founder saved it from the Console (Section 7.15.1).
    acting_as_tenant_id uuid references tenants(id) on delete set null,
    is_current          boolean not null default true,
    created_at          timestamptz not null default now(),
    unique (tenant_id, version)
);
-- Exactly one current version per tenant.
create unique index idx_tenant_field_schemas_current
    on tenant_field_schemas(tenant_id) where is_current;

alter table tenant_field_schemas enable row level security;
create policy tenant_isolation on tenant_field_schemas
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on tenant_field_schemas
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── documents: which version read this document ─────────────────────────────
-- `documents.schema_version` (0002) records the extraction schema the model
-- ran against; this records the tenant's field configuration in effect when
-- the document was read, so a later change never makes an old document's
-- checks unexplainable. NULL means "the built-in default" -- every document
-- processed before this migration.
alter table documents add column field_schema_version integer;
