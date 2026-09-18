-- DocFlow — Phase 4 (export)
--
-- New table: exports (tenant-scoped, RLS from this migration).
--
-- CLAUDE.md Section 7.4: "Every export creates an `exports` row (Section 9):
-- format, storage path, SHA-256, generated-by, generated-at, and the snapshot
-- hash it was built from." Section 9 names the columns: id, tenant_id,
-- document_id, format (csv | xlsx | json | iif), storage_path, sha256,
-- snapshot_hash, generated_by, generated_at. The additions below, and why:
--
--   * snapshot_id -- the exact `document_snapshots` row the file is built
--     from. The hash says WHICH snapshot; the id lets the worker load it
--     without a lookup that could race a re-approval. An order re-approved
--     between the click and the file being written still gets a file of what
--     was approved at the moment of the click (Section 7.3: "exports are
--     generated from the snapshot, never from the live tables").
--   * status / error_code / requested_at -- files are generated in the
--     worker (DECISIONS.md D-098), so a row exists from the click and says
--     whether the file is ready, or which catalog code explains why not.
--   * byte_size -- shown beside the download; costs nothing.
--   * acting_as_tenant_id -- Section 7.15.1: a founder exporting inside a
--     tenant is recorded as themselves, acting as that tenant.
--   * deleted_at -- soft delete, per Section 7.10.
--
-- Recorded as DECISIONS.md D-098.

create table exports (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           uuid not null references tenants(id) on delete cascade,
    document_id         uuid not null references documents(id) on delete cascade,
    snapshot_id         uuid not null references document_snapshots(id),

    format              text not null check (format in ('csv', 'xlsx', 'json', 'iif')),
    status              text not null default 'pending'
                        check (status in ('pending', 'ready', 'failed')),

    -- Server-generated, under tenants/{tenant_id}/exports/ (Section 7.5).
    -- Never returned to a client (Section 7.4: "storage paths are never
    -- user-controlled or user-visible").
    storage_path        text,
    sha256              text,
    byte_size           integer,

    -- The `document_snapshots.snapshot_sha256` of `snapshot_id`, copied here
    -- because Section 7.4 names it as a column of this table.
    snapshot_hash       text not null,

    -- An error-catalog code (EXP-0xx) when status = 'failed'. Never free text.
    error_code          text,

    generated_by        uuid not null references users(id),
    acting_as_tenant_id uuid references tenants(id) on delete set null,

    requested_at        timestamptz not null default now(),
    generated_at        timestamptz,
    deleted_at          timestamptz,

    -- A ready export names its file, its checksum and when it was made; a
    -- failed one names why. Enforced here, not remembered by the application.
    constraint exports_ready_is_complete check (
        status <> 'ready'
        or (storage_path is not null and sha256 is not null and generated_at is not null)
    ),
    constraint exports_failed_has_code check (status <> 'failed' or error_code is not null)
);

-- The document's export history, newest first.
create index idx_exports_document on exports(document_id, requested_at desc) where deleted_at is null;
-- Per-tenant reporting (Phase 5).
create index idx_exports_tenant on exports(tenant_id, requested_at) where deleted_at is null;

alter table exports enable row level security;

create policy tenant_isolation on exports
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on exports
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── A finished export is a record, not a draft ──────────────────────────────
-- Once a row is `ready` or `failed`, what it says about the file can never
-- change: an audit record that can be edited afterwards proves nothing. Only
-- soft deletion is allowed from then on.
create function exports_finished_rows_are_immutable() returns trigger
language plpgsql as $$
begin
    if old.status in ('ready', 'failed') and (
        new.status              is distinct from old.status
        or new.storage_path     is distinct from old.storage_path
        or new.sha256           is distinct from old.sha256
        or new.byte_size        is distinct from old.byte_size
        or new.snapshot_id      is distinct from old.snapshot_id
        or new.snapshot_hash    is distinct from old.snapshot_hash
        or new.format           is distinct from old.format
        or new.error_code       is distinct from old.error_code
        or new.generated_by     is distinct from old.generated_by
        or new.generated_at     is distinct from old.generated_at
        or new.document_id      is distinct from old.document_id
        or new.tenant_id        is distinct from old.tenant_id
    ) then
        raise exception 'exports row % is finished and cannot be changed', old.id;
    end if;
    return new;
end;
$$;

create trigger exports_finished_rows_are_immutable
    before update on exports
    for each row execute function exports_finished_rows_are_immutable();
