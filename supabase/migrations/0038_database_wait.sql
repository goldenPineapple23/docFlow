-- DocFlow — a document waits out a database outage, and the item-delete
-- lookup on document_lines gets its index (Phase 5.5 Stage 4, Q14;
-- DECISIONS.md D-206).
--
-- What it changes. Nothing else: no existing row, no policy, no grant.
--
--   1. documents.wait_cause may also be 'database'. A step of the worker
--      that loses the database (the connection-loss classes D-206 names)
--      puts the document back to `pending` to wait, as it already does for
--      the model provider, Storage and the parse service (0035), instead
--      of failing it. The constraint is 0035's column check, replaced with
--      the same list plus 'database'.
--
--   2. An index on document_lines (matched_item_id) for the rows that have
--      one (D-202 item 12). Deleting an `items` row runs a foreign-key
--      action (ON DELETE SET NULL) that looks up the lines pointing at it
--      by matched_item_id alone; the one index holding the column leads
--      with tenant_id, so each lookup read that whole index or the table.
--      Only the tenant hard delete deletes items today (7.14).
--
-- Reverse: supabase/reverse/0038_reverse.sql, tested in CI forward ->
-- reverse -> forward (scripts/ci/migration_0038_roundtrip.py), including
-- what it does with documents already waiting on 'database'.
--
-- Deploy order: this migration first, then the code. The code that comes
-- with it writes wait_cause = 'database', which the old check refuses. Code
-- from before it never writes that value.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs
-- as one transaction.

alter table public.documents drop constraint documents_wait_cause_check;
alter table public.documents add constraint documents_wait_cause_check
    check (wait_cause in ('model_provider', 'storage', 'parse_service', 'database'));

create index idx_document_lines_matched_item_id on public.document_lines (matched_item_id)
    where matched_item_id is not null;
