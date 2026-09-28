-- DocFlow — Phase 5.5 Stage 2c: Stripe events through one SECURITY DEFINER
-- function (review H11, D-173), and the founder alert for a refused inbound
-- webhook (2a's deferred item, D-171).
--
-- Touches four tables: tenants, stripe_webhook_events, founder_alerts,
-- email_outbox. Deletes nothing.
-- BACKUP FIRST -- see RUNBOOK.md section 1.1
-- (backup_0029: tenants, stripe_webhook_events, founder_alerts, email_outbox).
--
-- 1. tenants.stripe_status_event_at -- Stripe's `created` time of the event
--    whose state is saved. The ordering guard compares the next event against
--    it. NULL on every existing tenant, and NULL means "nothing to compare
--    against": the first event applies.
--
-- 2. stripe_webhook_events is no longer writable by any session. Until now
--    0019's `webhook_access` policy let any transaction carrying
--    app.stripe_webhook insert an event id -- and an id recorded without its
--    update is how H11 loses an update for good (Stripe's retry becomes a
--    "duplicate"). From here the table is written only by the function below,
--    which records the id in the same statement block as the update it
--    stands for. Platform admins may read it (it holds event ids and types,
--    no customer data).
--
-- 3. record_stripe_subscription_event(): records the event, applies the
--    ordering guard and updates the tenant, all in the caller's transaction.
--    Called from inside a tenant_session(): the tenant is read from
--    app.tenant_id, never taken as an argument (Section 7.5), and the Stripe
--    customer id on the event must be that tenant's. Outcomes:
--      applied      -- state saved, event id recorded
--      duplicate    -- event id already recorded; nothing changes
--      stale        -- older than the saved state; recorded, not applied
--      same_second  -- same `created` second as the saved state, so it cannot
--                      be ordered (Stripe's timestamps have one-second
--                      resolution); NOTHING is recorded, and the caller
--                      re-fetches the subscription from Stripe, then calls
--                      again with p_mode = 'fetched'
--      superseded   -- (fetched mode) a newer event was saved while the fetch
--                      was in flight; the fetched state is not written over
--                      it, and the event id is recorded as seen
--    first_past_due_at comes from Stripe's event time (D-170, clock item #3):
--    set by the first past_due, kept (not reset) by unpaid, cleared by any
--    other status.
--
--    Residual risk, named in D-173: while docflow_app holds EXECUTE, any code
--    running as docflow_app can call this with a fabricated event id. Closed
--    by F-1 in Stage 3 (a separate login for the webhook holds EXECUTE; it is
--    then revoked from docflow_app).
--
-- 4. EXECUTE is revoked from PUBLIC, and from Supabase's anon and
--    authenticated roles, which Supabase grants on every new function in
--    `public` by default -- without this the function would be callable from
--    the browser's public key through the REST API. Granted to docflow_app
--    only if that role exists: CI creates it after migrations run, so
--    scripts/ci/create_app_role.py grants the same (a test checks they agree).
--
-- 5. A refused inbound email webhook raises a high-severity
--    `intake_webhook_refused` founder alert (D-171). The request has no tenant
--    (it was refused before the token was read), so it gets the same narrow
--    treatment as 0017's `rollup_raise`: a transaction carrying
--    app.intake_refusal may insert exactly that alert type with no tenant, and
--    queue the founder-alert email that goes with it -- and read nothing.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs as
-- one transaction.

-- ── 1 ────────────────────────────────────────────────────────────────────────
alter table tenants add column stripe_status_event_at timestamptz;

-- ── 2 ────────────────────────────────────────────────────────────────────────
drop policy webhook_access on stripe_webhook_events;
create policy platform_admin_read on stripe_webhook_events for select
    using (current_setting('app.is_platform_admin', true) = 'true');

-- ── 3 ────────────────────────────────────────────────────────────────────────
create function record_stripe_subscription_event(
    p_event_id      text,
    p_event_type    text,
    p_event_created timestamptz,
    p_customer_id   text,
    p_status        text,
    p_period_end    timestamptz,
    p_mode          text
) returns text
language plpgsql
security definer
-- Pinned: an unset search_path is the classic way to hijack a definer
-- function (a caller's own schema shadowing `tenants`).
set search_path = pg_catalog, public
as $$
declare
    v_tenant   uuid := nullif(current_setting('app.tenant_id', true), '')::uuid;
    v_customer text;
    v_saved_at timestamptz;
    v_first    timestamptz;
begin
    if p_mode not in ('event', 'fetched') then
        raise exception 'record_stripe_subscription_event: unknown mode %', p_mode;
    end if;
    if v_tenant is null then
        raise exception 'record_stripe_subscription_event must run inside a tenant session'
            using errcode = 'insufficient_privilege';
    end if;
    if p_event_id is null or p_event_created is null or p_customer_id is null then
        raise exception 'record_stripe_subscription_event: event id, created and customer are required';
    end if;

    -- Lock the tenant row first: concurrent deliveries for one tenant are
    -- serialised here, so the duplicate check and the guard below see a
    -- settled state.
    select stripe_customer_id, stripe_status_event_at, first_past_due_at
      into v_customer, v_saved_at, v_first
      from public.tenants
     where id = v_tenant
       for update;
    if not found then
        raise exception 'record_stripe_subscription_event: tenant not found';
    end if;
    if v_customer is distinct from p_customer_id then
        raise exception 'record_stripe_subscription_event: the event''s customer is not this tenant''s'
            using errcode = 'insufficient_privilege';
    end if;

    if exists (select 1 from public.stripe_webhook_events where id = p_event_id) then
        return 'duplicate';
    end if;

    if v_saved_at is not null then
        if p_event_created < v_saved_at then
            insert into public.stripe_webhook_events (id, type) values (p_event_id, p_event_type);
            return case when p_mode = 'fetched' then 'superseded' else 'stale' end;
        end if;
        if p_event_created = v_saved_at and p_mode = 'event' then
            return 'same_second';  -- records nothing; the caller re-fetches
        end if;
    end if;

    -- unpaid keeps whatever is saved: it must not restart the cure clock just
    -- as Stripe's retries run out, and it does not start one either (a
    -- missing first notice is flagged by the cancel form, 7.15.4).
    if p_status = 'past_due' then
        v_first := coalesce(v_first, p_event_created);
    elsif p_status <> 'unpaid' then
        v_first := null;  -- cured, cancelled, or otherwise resolved
    end if;

    update public.tenants
       set stripe_subscription_status = p_status,
           stripe_current_period_end  = p_period_end,
           first_past_due_at          = v_first,
           stripe_status_event_at     = p_event_created,
           updated_at                 = now()
     where id = v_tenant;
    insert into public.stripe_webhook_events (id, type) values (p_event_id, p_event_type);
    return 'applied';
end;
$$;

-- ── 4 ────────────────────────────────────────────────────────────────────────
revoke all on function record_stripe_subscription_event(text, text, timestamptz, text, text, timestamptz, text)
    from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format(
                'revoke all on function record_stripe_subscription_event'
                '(text, text, timestamptz, text, text, timestamptz, text) from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'docflow_app') then
        execute 'grant execute on function record_stripe_subscription_event'
                '(text, text, timestamptz, text, text, timestamptz, text) to docflow_app';
    end if;
end;
$$;

-- ── 5 ────────────────────────────────────────────────────────────────────────
create policy intake_refusal_raise on founder_alerts for insert
    with check (current_setting('app.intake_refusal', true) = 'true'
                and type = 'intake_webhook_refused' and tenant_id is null);
create policy intake_refusal_enqueue on email_outbox for insert
    with check (current_setting('app.intake_refusal', true) = 'true'
                and tenant_id is null and template = 'founder_alert'
                and related_type = 'founder_alert');
