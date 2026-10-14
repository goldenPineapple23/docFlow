-- DocFlow — the reverse of migration 0037 (Phase 5.5 Stage 4, PR 1a; D-203).
--
-- Puts the database back exactly as 0036 left it: founder_alerts without
-- state_key, documents without fault_keys, and neither index.
--
-- What it loses: the keys themselves, and nothing else. No alert row and no
-- document row is removed. After it,
--   - an alert that was already raised under a state key can be raised
--     again by the next occurrence of the same state, and
--   - the Console's count of affected documents reads nothing, for alerts
--     raised before the reverse as well.
--
-- Run it only with the code from before 0037 deployed: the code that comes
-- with 0037 names state_key in every alert INSERT.
--
-- Tested in CI forward -> reverse -> forward
-- (scripts/ci/migration_0037_roundtrip.py): after the reverse the two
-- columns and the two indexes are gone and every other column of both
-- tables is as it was; after forward again, the state equals the first.

drop index if exists public.idx_documents_fault_keys;
alter table public.documents drop column if exists fault_keys;

drop index if exists public.idx_founder_alerts_state_key;
alter table public.founder_alerts drop column if exists state_key;
