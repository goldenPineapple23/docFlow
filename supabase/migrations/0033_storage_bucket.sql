-- DocFlow — Phase 5.5 Stage 3b: the one private Storage bucket.
--
-- NO BACKUP: this migration only creates a Storage bucket and touches no
-- existing table, policy or row -- the standing exception in RUNBOOK.md
-- section 1 (founder, 2026-09-30, Q2). Anything else added to this file
-- would end the exception; put it in its own migration instead.
--
-- One private bucket, `docflow-files`. Private means no public URL: every
-- file is served by DocFlow's own API behind its own signed links (D-089).
-- `storage.objects` gets NO policies, so a customer's own sign-in token can
-- reach no file directly; only DocFlow's S3 access key can, and
-- docflow_core.storage checks the tenant prefix before every call (Section
-- 7.5).
--
-- The 25 MB object limit matches MAX_FILE_SIZE_BYTES (file_types.py), which
-- the API already enforces before anything is stored. It is set only when
-- the column exists: on a local stack the Storage service adds
-- `file_size_limit` after migrations run, so there the limit stays with the
-- API (as it always is) rather than failing the migration.
--
-- Safe to run twice. Run once on docflow-staging via the Supabase SQL Editor;
-- CI's local stack applies it from this file.

insert into storage.buckets (id, name, public)
values ('docflow-files', 'docflow-files', false)
on conflict (id) do nothing;

do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = 'storage' and table_name = 'buckets' and column_name = 'file_size_limit'
    ) then
        update storage.buckets set file_size_limit = 26214400 where id = 'docflow-files';
    end if;
end
$$;
