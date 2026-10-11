-- The two trigram indexes Stage 4's matching reads its candidates from
-- (docs/BUILD-STATUS.md, "Stage 4 detailed design", A2 and A7; D-199, D-200).
--
-- This file is the one place the statements are written. The catalog sizing
-- job (scripts/bench/catalog_sizing.py) runs it to measure what the indexes
-- cost, before migration 0037 exists. 0037 copies the statements below word
-- for word, and packages/core/tests/test_bench_catalog.py fails if a
-- migration creates a trigram index on `items` that is not one of them.
--
-- Both are partial on live rows and lead with tenant_id (btree_gist), so
-- `ORDER BY <expression> <-> :q LIMIT K` is an index scan inside one tenant.
-- Nothing here changes a row.

create extension if not exists pg_trgm with schema extensions;
create extension if not exists btree_gist with schema extensions;

create index idx_items_trgm_description on items
    using gist (tenant_id, lower(description) extensions.gist_trgm_ops)
    where deleted_at is null;

create index idx_items_trgm_sku on items
    using gist (tenant_id, lower(regexp_replace(sku, '[^A-Za-z0-9]', '', 'g')) extensions.gist_trgm_ops)
    where deleted_at is null;
