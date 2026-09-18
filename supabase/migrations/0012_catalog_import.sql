-- DocFlow — Phase 5, slice 5.2 (catalog and customer-list import)
--
-- New tables (both tenant-scoped, RLS from this migration):
--   catalog_imports           one upload's journey: parsed -> mapped -> committed
--   import_mapping_templates  the column mapping remembered per tenant and kind
-- New columns: items.last_import_id, items.retired_by_import_id,
--              buyers.last_import_id.
--
-- Section 7.15.2 Step 4: "upload -> parsed preview (first N rows) -> column
-- mapping (auto-detected, founder-adjustable, saved as a per-tenant mapping
-- template for re-uploads) -> validation report -> commit ... Every commit
-- creates a catalog_imports row (file hash, row counts, mapping used,
-- committed-by) and the items it inserted/updated/retired reference it.
-- Re-uploads are diffs, not replacements." Step 5: the customer list uses
-- "the same component and flow with the buyers schema".
--
-- The file is parsed in the worker, never the API (Section 7.11); the parsed
-- table is stored on the import row so mapping, validation and commit are
-- plain data operations the API can do without touching the file again.
--
-- Recorded as DECISIONS.md D-108.

create table catalog_imports (
    id                   uuid primary key default gen_random_uuid(),
    tenant_id            uuid not null references tenants(id) on delete cascade,
    kind                 text not null check (kind in ('catalog', 'buyers')),
    status               text not null default 'parsing'
                         check (status in ('parsing', 'parsed', 'failed', 'committed', 'discarded')),

    -- Where the file came from: a fresh upload, or one of the tenant's
    -- onboarding files (moved out of staging at tenant creation).
    source               text not null check (source in ('upload', 'intake_file')),
    intake_file_id       uuid references onboarding_intake_files(id),
    original_filename    text not null,       -- metadata only (Section 7.11)
    storage_path         text not null,
    file_sha256          text not null,
    file_type            text not null,

    -- An error-catalog code when status = 'failed'.
    error_code           text,

    -- The parsed table, as text: header cells, then data rows. Strings only,
    -- so a spreadsheet's binary floats never become a SKU or a price
    -- (Section 7.1). Capped in the parser.
    columns              jsonb,
    rows                 jsonb,
    row_count            integer,
    -- The spreadsheet row the header was on, so data row i is spreadsheet
    -- row header_row_number + 1 + i and the report's row numbers match the
    -- file the founder has open.
    header_row_number    integer,

    -- {field: column index | null}, and inline fixes {row_number: {field: value}}.
    mapping              jsonb,
    overrides            jsonb not null default '{}'::jsonb,

    -- What the commit did: counts, and the flags it was committed over.
    summary              jsonb,

    created_by           uuid not null references users(id),
    acting_as_tenant_id  uuid references tenants(id) on delete set null,
    created_at           timestamptz not null default now(),
    parsed_at            timestamptz,
    committed_by         uuid references users(id),
    committed_at         timestamptz,
    deleted_at           timestamptz,

    constraint catalog_imports_failed_has_code check (status <> 'failed' or error_code is not null),
    constraint catalog_imports_committed_is_attributable check (
        status <> 'committed'
        or (committed_by is not null and committed_at is not null and summary is not null)
    )
);
create index idx_catalog_imports_tenant on catalog_imports(tenant_id, kind, created_at desc)
    where deleted_at is null;

alter table catalog_imports enable row level security;
create policy tenant_isolation on catalog_imports
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on catalog_imports
    using (current_setting('app.is_platform_admin', true) = 'true');


create table import_mapping_templates (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references tenants(id) on delete cascade,
    kind        text not null check (kind in ('catalog', 'buyers')),
    -- {field: header text}. Keyed by header TEXT, not position, so a
    -- re-export with its columns reordered still maps.
    mapping     jsonb not null,
    updated_by  uuid not null references users(id),
    updated_at  timestamptz not null default now(),
    unique (tenant_id, kind)
);

alter table import_mapping_templates enable row level security;
create policy tenant_isolation on import_mapping_templates
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on import_mapping_templates
    using (current_setting('app.is_platform_admin', true) = 'true');


-- "The items it inserted/updated/retired reference it."
alter table items
    add column last_import_id       uuid references catalog_imports(id),
    add column retired_by_import_id uuid references catalog_imports(id);

alter table buyers
    add column last_import_id uuid references catalog_imports(id);
