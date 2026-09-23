-- DocFlow — Phase 5, slice 5.6: lifecycle actions (cancel/reactivate/delete)
--
-- Recorded as DECISIONS.md D-123.
--
-- New RLS surface: the "cancelling -> suspended" sweep (docflow_core.lifecycle,
-- run by apps/worker/app/tasks/lifecycle_sweep.py) needs to find every tenant
-- whose cancellation_effective_at has passed, across all tenants -- a
-- cross-tenant read. Rather than route it through the Section 7.15.1 admin
-- bypass (which grants far more than "list a few columns of tenants"), it
-- gets its own narrow session, exactly like the nightly rollup (0017's
-- rollup_read) and the scheduled-jobs sweep (0013's scheduler_access): one
-- SELECT policy on one table, for a session nothing else ever opens
-- (docflow_core.db.lifecycle_session). The actual suspend/pending_deletion
-- transition for each tenant found then runs inside that tenant's own
-- tenant_session(), under the ordinary tenant_isolation policy already on
-- `tenants` -- no write policy is added here.

create policy lifecycle_read on tenants for select
    using (current_setting('app.lifecycle', true) = 'true');

-- 0013's scheduled_jobs.job_type check only allowed 'first_week_checkin' --
-- the only job type that existed at the time. The pending-deletion reminder
-- schedule (Section 7.14: "day 1, 15, 25") is a second one.
alter table scheduled_jobs drop constraint scheduled_jobs_job_type_check;
alter table scheduled_jobs add constraint scheduled_jobs_job_type_check
    check (job_type in ('first_week_checkin', 'pending_deletion_reminder'));

-- ── Stripe webhook sync (D-123) ──────────────────────────────────────────────
-- Section 7.15.4's non_payment cancellation rule needs "the timestamp of the
-- first past_due webhook event for the current billing failure". Nothing
-- before this slice kept stripe_subscription_status in sync after go-live --
-- it was set once, at go-live, and never touched again (config.py's
-- STRIPE_WEBHOOK_SECRET has sat unused since Phase 5.3 for exactly this).

alter table tenants add column first_past_due_at timestamptz;

-- Idempotency on Stripe event id (Section 7.12: "Stripe webhooks verify
-- signatures and are idempotent on event ID"). Global: an event isn't a
-- tenant's data, it's the fact that DocFlow already processed it.
create table stripe_webhook_events (
    id          text primary key,  -- Stripe's evt_... id
    type        text not null,
    received_at timestamptz not null default now()
);

alter table stripe_webhook_events enable row level security;
create policy webhook_access on stripe_webhook_events
    using (current_setting('app.stripe_webhook', true) = 'true');

-- The webhook doesn't know a tenant_id until it has looked one up by
-- stripe_customer_id -- a cross-tenant read, so it gets the same narrow-session
-- treatment as the lifecycle sweep. The actual UPDATE to that tenant's row
-- happens in a normal tenant_session(), under tenant_isolation, once the
-- tenant_id is known.
create policy stripe_webhook_lookup on tenants for select
    using (current_setting('app.stripe_webhook', true) = 'true');
