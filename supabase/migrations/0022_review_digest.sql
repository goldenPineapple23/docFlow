-- DocFlow — Phase 5, slice 5.8c: the "needs review" digest email
--
-- Recorded as DECISIONS.md D-131. Plan: docs/plans/5.8-tenant-surface.md.
--
-- The digest is a scheduled job like the first-week check-in (0013): a row in
-- scheduled_jobs, found by the existing beat sweep, run in its own tenant's
-- session. When an order reaches "needs review", the worker adds its id to the
-- tenant's one *pending* digest job, creating that job if there is none. The
-- job fires at least REVIEW_DIGEST_INTERVAL_MIN after the first order that
-- started it (and after the previous digest), and sends one email with counts.
--
-- Additive only: nothing is dropped, no data is touched.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

-- A third job type.
alter table scheduled_jobs drop constraint scheduled_jobs_job_type_check;
alter table scheduled_jobs add constraint scheduled_jobs_job_type_check
    check (job_type in ('first_week_checkin', 'pending_deletion_reminder', 'review_digest'));

-- At most one pending digest per tenant, enforced by the database: two workers
-- finishing orders for the same tenant at the same moment both upsert into the
-- same row (ON CONFLICT on this index) instead of creating two emails. Once
-- the sweep claims the job (status -> 'running') the row leaves the index, so
-- an order arriving while the email is being written starts the next digest.
create unique index uq_scheduled_jobs_pending_review_digest
    on scheduled_jobs(tenant_id)
    where job_type = 'review_digest' and status = 'pending';

-- The throttle looks up the tenant's most recent digest.
create index idx_scheduled_jobs_tenant_type on scheduled_jobs(tenant_id, job_type, started_at);
