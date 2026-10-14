-- DocFlow — an alert raised once per state, and the documents a state has hit
-- (Phase 5.5 Stage 4, PR 1a; DECISIONS.md D-203).
--
-- What it adds. Nothing else changes: no existing row, no policy, no grant.
--
--   1. founder_alerts.state_key, with a unique index over EVERY row,
--      acknowledged or not. 0011's dedupe index covers only unacknowledged
--      rows, so acknowledging an alert let the next occurrence raise it
--      again (the re-raised-alert defect, D-198). An alert raised under a
--      state key is raised once for that state: a repeat fails this index
--      and nothing is written, whether the first one is open or
--      acknowledged. The database has to be the one to refuse it, because a
--      tenant session may insert an alert and never read one (0011's
--      tenant_raise). NULLs are distinct, so an alert without a state key
--      (every alert type that keeps its own rule) is never refused here.
--
--   2. documents.fault_keys: the state keys of the faults that hit this
--      document, written by the worker in the transaction that records the
--      document's outcome. The Console counts documents by key, so an
--      acknowledged alert whose state is still hitting documents can be
--      seen without any session but the platform admin's reading an alert.
--      The partial index holds only documents that carry a key.
--
-- Reverse: supabase/reverse/0037_reverse.sql, tested in CI forward ->
-- reverse -> forward (scripts/ci/migration_0037_roundtrip.py).
--
-- Deploy order: this migration first, then the code. The code's alert INSERT
-- names state_key, so it fails on a database without the column. Code from
-- before this migration is unaffected by it (two nullable columns).

alter table public.founder_alerts add column state_key text;
create unique index idx_founder_alerts_state_key on public.founder_alerts (state_key);

alter table public.documents add column fault_keys text[];
create index idx_documents_fault_keys on public.documents using gin (fault_keys)
    where fault_keys is not null;
