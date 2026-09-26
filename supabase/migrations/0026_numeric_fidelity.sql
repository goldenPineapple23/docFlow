-- DocFlow — Phase 5.5 Stage 1: no value is ever rounded between the document
-- and the export (review finding C1; DECISIONS.md D-149, D-154).
--
-- 1. The four document-derived number columns lose their fixed scale.
--    `numeric(14,4)` and friends made Postgres round on insert, silently:
--    '0.00345'::numeric(14,4) is 0.0035. An unconstrained `numeric` keeps
--    every digit, and keeps the scale it was given ('47.50' stays 47.50, '2'
--    stays 2). Existing values are unchanged by this: they are already
--    within the new type. Changing only the typmod rewrites nothing.
--
-- 2. exports.warnings: the catalog-coded warnings raised when a file was made
--    (e.g. EXP-008, a number with more decimal places than QuickBooks Desktop
--    is known to accept). A list of {"code", "field", "line_number"}; never a
--    value. Frozen with the rest of a finished export row.
--
-- BACKUP FIRST (Section 5: every migration touching existing data is
-- preceded by a stated backup step). In the Supabase SQL Editor, as postgres:
--
--   create schema if not exists backup_0026;
--   create table backup_0026.document_headers as table public.document_headers;
--   create table backup_0026.document_lines   as table public.document_lines;
--
-- then check the row counts match, then run this file. The backup schema is
-- not exposed through the API and is dropped only on the founder's say-so.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

alter table document_headers alter column order_total type numeric;

alter table document_lines
    alter column quantity   type numeric,
    alter column unit_price type numeric,
    alter column line_total type numeric;

alter table exports add column warnings jsonb not null default '[]'::jsonb;

create or replace function exports_finished_rows_are_immutable() returns trigger
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
        or new.warnings         is distinct from old.warnings
    ) then
        raise exception 'exports row % is finished and cannot be changed', old.id;
    end if;
    return new;
end;
$$;
