-- DocFlow — the reverse of migration 0040 (Phase 5.5 Stage 5, viewer A3;
-- D-232).
--
-- Puts the database back as 0039 left it: no documents.page_count, no
-- documents.pages, and none of 0040's three checks.
--
-- What is lost: every document's page list. The page images themselves stay
-- in Storage under derived/{document}/pages/ until the tenant is deleted;
-- nothing here touches Storage. No document row is removed.
--
-- Run it only with the code from before 0040 deployed: the code that comes
-- with 0040 writes both columns.
--
-- Tested in CI (scripts/ci/migration_0040_roundtrip.py): after the reverse
-- the two columns and three checks are gone and nothing else on documents
-- differs; after forward again the state is the first.

alter table public.documents drop constraint documents_pages_match_count;
alter table public.documents drop constraint documents_pages_both_or_neither;
alter table public.documents drop column pages;
alter table public.documents drop column page_count;
