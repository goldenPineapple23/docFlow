-- DocFlow — the reverse of migration 0038 (Phase 5.5 Stage 4, Q14; D-206).
--
-- Puts the database back as 0037 left it: documents.wait_cause limited to
-- 'model_provider', 'storage' and 'parse_service' again, and no index on
-- document_lines (matched_item_id).
--
-- Documents already waiting on the database. The narrowed check would
-- refuse them, so first they become ordinary waiting documents: their
-- wait_cause, waiting_since and last_wait_error are cleared. Their status
-- (`pending`) and retry_at are kept, so the dispatcher sends them again as
-- it sends any pending document, and code from before 0038 reads them from
-- the start (or resumes from a saved answer). What is lost: that they were
-- waiting on the database, and when the wait began (the 6-hour maximum
-- counts again from their next wait). No document row is removed.
--
-- Run it only with the code from before 0038 deployed: the code that comes
-- with 0038 writes wait_cause = 'database'.
--
-- Tested in CI (scripts/ci/migration_0038_roundtrip.py): a document waiting
-- on 'database' is cleared as above; after the reverse the check holds the
-- three causes and the index is gone, and nothing else on the two tables
-- differs; after forward again the state is the first.

update public.documents
   set wait_cause = null,
       waiting_since = null,
       last_wait_error = null
 where wait_cause = 'database';

alter table public.documents drop constraint documents_wait_cause_check;
alter table public.documents add constraint documents_wait_cause_check
    check (wait_cause in ('model_provider', 'storage', 'parse_service'));

drop index if exists public.idx_document_lines_matched_item_id;
