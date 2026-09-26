-- DocFlow — Phase 5.5 Stage 1b: the document status machine, enforced by the
-- database (review findings H1, H3; DECISIONS.md D-158).
--
-- 1. documents.processing_started_at / processing_attempts: the worker claims
--    a document by moving it to `processing` and stamping when; a second
--    delivery of the same job finds it already claimed and does nothing, and a
--    claim older than the stuck timeout can be taken over (a worker that died
--    mid-job). The attempt count lets the stuck-document sweep give up after
--    a fixed number of tries and fail the document with a catalog code
--    instead of retrying forever.
--
-- 2. document_status_transition_allowed(old, new): the one list of legal
--    status changes. docflow_core.document_status.ALLOWED mirrors it and a
--    test asserts the two agree.
--
-- 3. A trigger that refuses any other status change -- whoever sends it: a
--    worker, a retry, a redelivered job, a script, a bug. Most importantly,
--    nothing can move an approved or exported order back to `processing`.
--    It also refuses a document that enters review without the model's
--    answer on it (Section 7.1: raw_json on every extracted document), and a
--    new row inserted straight into a reviewable status (needs_review,
--    approved, exported, rejected) without that answer. Existing rows are not rewritten or
--    re-checked; only changes from now on are.
--
-- BACKUP FIRST -- see RUNBOOK.md section 1.1 (backup_0027: documents).
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

alter table documents
    add column processing_started_at timestamptz,
    add column processing_attempts   integer not null default 0,
    -- Why a document failed after the model had answered (the answer itself
    -- stays in raw_json, unchanged): DOC-020 too long, DOC-021 couldn't be
    -- checked, DOC-022 stuck. A catalog code, never free text.
    add column failure_code          text,
    -- Post-processing steps that didn't finish (buyer identification,
    -- matching, duplicate detection). Kept on the document so the reviewer's
    -- VAL-016 warning survives every later re-validation.
    add column pipeline_issues       jsonb not null default '[]'::jsonb;

-- The stuck-document sweep lists tenants through its own narrow session
-- (docflow_core.db.pipeline_sweep_session), like the rollup and the
-- lifecycle sweep: it can read `tenants` and nothing else; each tenant's
-- documents are then read in that tenant's own tenant_session().
create policy pipeline_sweep_read on tenants for select
    using (current_setting('app.pipeline_sweep', true) = 'true');

-- The stuck-document sweep reads exactly these rows.
create index idx_documents_in_flight
    on documents (processing_started_at)
    where status in ('pending', 'processing') and deleted_at is null;

create function document_status_transition_allowed(old_status text, new_status text)
returns boolean
language sql immutable
as $$
    select old_status = new_status or (old_status, new_status) in (
        ('pending',      'processing'),   -- the worker claims it
        ('pending',      'quarantined'),  -- a hold decided after the row exists
        ('pending',      'failed'),       -- never picked up after every retry
        ('staged',       'pending'),      -- the founder's "Run extraction" (test batch)
        ('quarantined',  'pending'),      -- released
        ('processing',   'needs_review'), -- extracted AND checked
        ('processing',   'failed'),       -- unreadable, model failure, or checks failed
        ('needs_review', 'approved'),
        ('needs_review', 'rejected'),
        ('approved',     'exported'),
        ('approved',     'needs_review'), -- edited or reopened after approval
        ('exported',     'needs_review'), -- reopened
        ('rejected',     'needs_review')  -- reopened
    )
$$;

create function documents_status_guard() returns trigger
language plpgsql
as $$
begin
    if tg_op = 'INSERT' then
        -- The pipeline itself only ever inserts pending / staged / quarantined.
        -- A row inserted straight into a reviewable status (a seed script, a
        -- test fixture) must carry the model's answer like a real one does.
        if new.status in ('needs_review', 'approved', 'exported', 'rejected')
           and not coalesce(new.raw_json ? 'header', false) then
            raise exception 'documents: a new document cannot start as % without the model''s answer', new.status
                using errcode = 'check_violation';
        end if;
        return new;
    end if;

    if new.status is distinct from old.status then
        if not document_status_transition_allowed(old.status, new.status) then
            raise exception 'documents: status % -> % is not allowed', old.status, new.status
                using errcode = 'check_violation';
        end if;
        if old.status = 'processing' and new.status = 'needs_review'
           and not coalesce(new.raw_json ? 'header', false) then
            raise exception 'documents: a document enters review only with the model''s answer (raw_json)'
                using errcode = 'check_violation';
        end if;
    end if;
    return new;
end;
$$;

create trigger documents_status_guard
    before insert or update of status on documents
    for each row execute function documents_status_guard();
