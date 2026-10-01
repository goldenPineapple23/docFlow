-- DocFlow — Phase 5.5 Stage 3c: lost parse tries, and a run row before every
-- paid model call. Decided with the founder before building
-- (docs/BUILD-STATUS.md, "3c detailed design", items 6a and 9; founder's Q5
-- and Q9, 2026-10-01).
--
-- Touches two tables: documents (one column), extraction_runs (three columns,
-- one NOT NULL relaxed, one check constraint, two indexes). Deletes nothing and
-- changes no existing value: every existing extraction_runs row becomes a
-- `finished` row exactly as it is.
-- BACKUP FIRST -- see RUNBOOK.md section 1.1 (backup_0034: documents,
-- extraction_runs).
--
-- 1. documents.parse_lost_attempts -- the processing attempts whose request to
--    the parse service got in and never came out (the connection dropped, the
--    read timed out, the proxy answered 502/504). Like 3a's timeout_attempts,
--    each is a timeout-class try: `retry_rules.decide()` allows at most one try
--    after the first of either, and never more than MAX_PROCESSING_ATTEMPTS in
--    all (Q9). A separate column so the DOC-022 alert can say which it was.
--
-- 2. extraction_runs: a "started" row before each paid model call, then the
--    outcome as a second row (D-163; Q5: append-only kept, the same pattern
--    as admin_actions' intent and outcome rows). Nothing is ever updated.
--    - run_state: 'started' | 'finished'. Existing rows are 'finished'.
--    - started_run_id: the outcome row points at its start row. One outcome
--      per start (unique), so the worker and the stuck sweep can never both
--      write one.
--    - counted_input_tokens: the request's input tokens, counted before the
--      call (the free count endpoint). Kept apart from input_tokens so a
--      reader that sums tokens can never count a call twice.
--    - succeeded may be NULL only on a 'started' row.
--    A started row with no outcome means the worker died during the call; the
--    stuck sweep (or the next claim) writes its outcome: not succeeded,
--    worker_lost_during_call, the counted input's cost as a lower bound.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor; CI's local
-- stack applies it from this file.

alter table documents
    add column parse_lost_attempts integer[] not null default '{}';

alter table extraction_runs
    add column run_state text not null default 'finished'
        check (run_state in ('started', 'finished')),
    add column started_run_id uuid references extraction_runs(id) on delete cascade,
    add column counted_input_tokens integer;

alter table extraction_runs alter column succeeded drop not null;

alter table extraction_runs
    add constraint extraction_runs_succeeded_matches_state check (
        (run_state = 'started' and succeeded is null and started_run_id is null)
        or (run_state = 'finished' and succeeded is not null)
    );

create unique index idx_extraction_runs_one_outcome
    on extraction_runs(started_run_id) where started_run_id is not null;

create index idx_extraction_runs_started
    on extraction_runs(document_id) where run_state = 'started';
