-- DocFlow — Phase 5.5 Stage 3a: worker time limits (review H5). Two columns,
-- decided with the founder before building (docs/BUILD-STATUS.md, "Stage 3
-- -- agreed with the founder before building", 3a).
--
-- Touches three tables: documents, tenants (a column each), exports (two
-- indexes). Deletes nothing and changes no existing value; an index changes
-- no row.
-- BACKUP FIRST -- see RUNBOOK.md section 1.1 (backup_0030: documents, tenants).
-- exports is not backed up: nothing in it is written or changed here.
--
-- 1. documents.timeout_attempts -- the processing attempts (the value of
--    processing_attempts at the time) that ended because the document task
--    hit its hard time limit. Written by the Celery worker's main process,
--    which is told about every timeout even when the task's own process was
--    killed and could write nothing. The stuck-document sweep reads it: a
--    timeout gets at most one retry (a file that hangs a parser will hang it
--    again), then the document fails with DOC-022 and the alert names the
--    cause. Empty on every existing document, which is exactly right: none
--    of them has timed out, because no time limit existed.
--
-- 2. tenants.stripe_cancel_pending_at -- set in the same transaction that
--    suspends a tenant with a Stripe subscription or customer; cleared once
--    Stripe has cancelled, or when the tenant is reactivated (same
--    transaction as the status change). Until now the suspension committed
--    first and Stripe was called after, so a worker killed between the two
--    left the tenant suspended in DocFlow and still billed by Stripe, with
--    no alert and no retry. Every lifecycle sweep now retries a pending
--    cancel, re-checking under a row lock that the tenant is still in a
--    cancelled state first. NULL on every existing tenant: the column
--    starts recording from this migration on.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. Adding a
-- column with a constant default does not rewrite the table (Postgres 11+).

alter table documents
    add column timeout_attempts integer[] not null default '{}';

alter table tenants
    add column stripe_cancel_pending_at timestamptz;

-- 3. Two small partial indexes on exports (founder, 2026-09-29), for the
--    stuck sweep's EXP-009 step (Stage 3a). Each holds only the rows it
--    names, so both stay tiny however many exports a tenant makes:
--    * pending exports, which the sweep checks for every tenant each tick;
--    * EXP-009 exports, which the health strip counts for today and the
--      sweep counts per tenant for the daily alert.
create index idx_exports_pending on exports(tenant_id, requested_at)
    where status = 'pending' and deleted_at is null;
create index idx_exports_not_finished on exports(generated_at)
    where error_code = 'EXP-009';
