-- DocFlow — a document's page images (Phase 5.5 Stage 5, viewer A3;
-- DECISIONS.md D-228, D-232).
--
-- What it changes. Nothing else: no existing row, no policy, no grant.
--
--   1. documents.page_count: how many pages the parse service drew for the
--      document. NULL: not drawn (never run, or the render failed or was
--      cut off). 0: a format with no pages (shown as text).
--
--   2. documents.pages: the list, one entry per page in order, as a JSON
--      array: {"index", "media_type", "width", "height"} for a drawn page,
--      {"index", "rendered": false, "reason"} for one that wasn't. The
--      images are Storage objects under derived/{document}/pages/.
--
--   The two are written together, once, after every page is stored
--   (apps/worker/app/tasks/render_pages.py), so the checks below hold:
--   both NULL or both set, and when set the list's length is the count.
--   `case` because jsonb_array_length raises on anything but an array, and
--   `and` doesn't promise to test the type first.
--
-- Reverse: supabase/reverse/0040_reverse.sql, tested in CI forward ->
-- reverse -> forward (scripts/ci/migration_0040_roundtrip.py).
--
-- Deploy order: this migration first, then the code. The code that comes
-- with it writes both columns. Code from before it reads neither.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs
-- as one transaction.

alter table public.documents
    add column page_count integer
        constraint documents_page_count_check check (page_count is null or page_count >= 0),
    add column pages jsonb,
    add constraint documents_pages_both_or_neither check ((page_count is null) = (pages is null)),
    add constraint documents_pages_match_count check (
        page_count is null or pages is null
        or case when jsonb_typeof(pages) = 'array'
                then jsonb_array_length(pages) = page_count
                else false end
    );
