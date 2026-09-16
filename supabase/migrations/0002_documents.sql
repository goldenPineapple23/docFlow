-- DocFlow — Phase 1: Documents, extraction tables, intake rejections
--
-- Tables: documents, document_headers, document_lines, intake_rejections.
-- Scope matches CLAUDE.md's phase-by-phase rule: this slice only writes
-- documents.status in {pending, processing, needs_review, failed,
-- quarantined}. approved/exported and approved_json are wired by later
-- phases (review UI, export). See supabase/migrations/0001_foundations.sql
-- for the RLS pattern this migration follows verbatim.

-- ── documents (tenant-scoped) ────────────────────────────────────────────
create table documents (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references tenants(id) on delete cascade,
    original_filename   text not null,
    storage_path        text not null,
    source              text not null check (source in ('email','upload','api')),
    status              text not null default 'pending'
                        check (status in
                               ('pending','processing','needs_review','failed',
                                'quarantined','approved','rejected','exported')),
    content_sha256      text not null,
    is_test_batch       boolean not null default false,
    model_id            text,
    prompt_hash         text,
    schema_version      text,
    input_tokens        integer,
    output_tokens       integer,
    est_cost_usd        numeric(10,4),
    injection_suspected boolean,
    overall_confidence  numeric(4,3),
    -- Immutable model response (CLAUDE.md Section 7.1) -- never edited after write.
    raw_json            jsonb,
    -- Frozen at approval time (Section 7.3); untouched by this phase.
    approved_json       jsonb,
    created_at          timestamptz not null default now(),
    processed_at        timestamptz
);
create index idx_documents_tenant_status on documents(tenant_id, status);
create index idx_documents_tenant_created on documents(tenant_id, created_at desc);

alter table documents enable row level security;

create policy tenant_isolation on documents
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on documents
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── document_headers (tenant-scoped, 1:1 with documents) ─────────────────
-- tenant_id is denormalized from documents so RLS can be enforced directly
-- on this table too (CLAUDE.md Section 7.5: "tenant_id on every business
-- table... RLS policies on every one of them"), rather than relying on a
-- join through documents to establish isolation.
create table document_headers (
    document_id                uuid primary key references documents(id) on delete cascade,
    tenant_id                  uuid not null references tenants(id) on delete cascade,
    po_number                  text,
    order_date                 text,
    requested_delivery_date    text,
    buyer_name                 text,
    buyer_contact_email        text,
    -- Single line, per the extraction schema (Section 8.1) -- not split into
    -- address lines (DECISIONS.md: do not re-derive structure the model
    -- doesn't extract).
    ship_to_address            text,
    payment_terms              text,
    order_total                numeric(12,2),
    currency                   text,
    notes                      text,
    -- Per-field 0-1 confidence, keyed by the header field names above.
    header_confidence          jsonb,
    currency_inferred          boolean not null default false,
    created_at                 timestamptz not null default now(),
    updated_at                 timestamptz not null default now()
);
create index idx_document_headers_tenant on document_headers(tenant_id);

alter table document_headers enable row level security;

create policy tenant_isolation on document_headers
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on document_headers
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── document_lines (tenant-scoped) ────────────────────────────────────────
create table document_lines (
    id              uuid primary key default gen_random_uuid(),
    document_id     uuid not null references documents(id) on delete cascade,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    line_number     integer not null,
    sku             text,
    description     text,
    -- Numeric even though the model returns these as strings (Section 7.1) --
    -- cast to Decimal at the extraction boundary, never left as float.
    quantity        numeric(14,4),
    unit            text,
    unit_price      numeric(14,4),
    line_total      numeric(14,2),
    confidence      numeric(4,3),
    created_at      timestamptz not null default now()
);
create index idx_document_lines_document on document_lines(document_id, line_number);
create index idx_document_lines_tenant on document_lines(tenant_id);

alter table document_lines enable row level security;

create policy tenant_isolation on document_lines
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on document_lines
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── intake_rejections (tenant-scoped) ─────────────────────────────────────
-- Files rejected before parsing (Tier 3 / failed 7.11 validation). Minimal
-- for this slice -- email-specific columns (sender, auth results, etc.)
-- land with the email-intake slice, per that slice's own migration.
create table intake_rejections (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references tenants(id) on delete cascade,
    source              text not null check (source in ('email','upload')),
    original_filename   text,
    detected_type       text,
    error_code          text not null,
    created_at          timestamptz not null default now()
);
create index idx_intake_rejections_tenant on intake_rejections(tenant_id, created_at desc);

alter table intake_rejections enable row level security;

create policy tenant_isolation on intake_rejections
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on intake_rejections
    using (current_setting('app.is_platform_admin', true) = 'true');
