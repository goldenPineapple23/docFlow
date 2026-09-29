-- DocFlow — Phase 5.5, before Stage 3b: card billing with a 7-day trial
-- (founder, 2026-09-29; decisions D1-D6 in docs/BUILD-STATUS.md, "Card
-- billing with a 7-day trial"; D-181).
--
-- Touches two tables: tenants (three columns) and scheduled_jobs (its
-- job-type check). Adds one function, which writes stripe_webhook_events.
-- Deletes nothing and changes no existing value except `billing_method`,
-- which every existing tenant gets as 'invoice' -- how each of them is
-- billed today.
-- BACKUP FIRST -- see RUNBOOK.md section 1.1 (backup_0031: tenants).
-- scheduled_jobs is not backed up: a check constraint changes no row.
--
-- 1. tenants.billing_method -- how the subscription collects: 'card' (Stripe
--    charges the saved card, charge_automatically) or 'invoice' (Stripe
--    emails an invoice, send_invoice; D-113). Chosen at go-live (D6).
--    'invoice' on every existing tenant and as the default, so nothing that
--    exists today changes.
--
-- 2. tenants.card_on_file_at -- when a card was first saved through
--    DocFlow's card page. Go-live's card gate (D1) reads it. NULL until then.
--
-- 3. tenants.setup_fee_paid_at -- when the setup fee was collected at
--    signing (D2, standard customers). Once set, the fee's preset, amount and
--    billing are locked (founder, 2026-09-29), and go-live adds no setup-fee
--    item to the first invoice. NULL for founding customers (their fee comes
--    with month one), a waived fee, and a fee invoiced by hand.
--
-- 4. record_stripe_card_event(): the only way a card-saved or setup-fee-paid
--    event reaches the database, in the same pattern as 0029's
--    record_stripe_subscription_event(). stripe_webhook_events is writable by
--    no session (0029), so an event id can only be recorded by a function,
--    in the same transaction as the effect it stands for (review H11). Called
--    from inside a tenant_session(): the tenant is read from app.tenant_id,
--    never taken as an argument (Section 7.5), and the event's Stripe
--    customer must be that tenant's. The timestamps are the database's
--    (D-170). Outcomes:
--      applied        -- recorded, and card_on_file_at / setup_fee_paid_at set
--      duplicate      -- event id already recorded; nothing changes
--      already_paid   -- a setup fee arrived for a tenant whose fee was already
--                        paid: recorded, nothing else changes; the caller
--                        alerts the founder (a second charge to refund or keep)
--      amount_mismatch -- the amount collected isn't the tenant's setup fee, or
--                        the fee isn't billed through Stripe: the card is
--                        still recorded as saved, the fee is NOT marked paid,
--                        and the caller alerts the founder
--    Same residual risk as 0029's function (D-173): while docflow_app holds
--    EXECUTE, code running as docflow_app could call it with a made-up event
--    id. Closed in Stage 3e (F-1): the Stripe login holds EXECUTE and it is
--    revoked from docflow_app.
--
-- 5. EXECUTE revoked from PUBLIC and from Supabase's anon, authenticated and
--    service_role; granted to docflow_app if it exists (CI creates it after
--    migrations run, so scripts/ci/create_app_role.py grants the same, and a
--    test checks they agree) -- exactly as 0029.
--
-- 6. scheduled_jobs.job_type gains 'past_due_reminder': the owner's reminder
--    3 days (PAST_DUE_REMINDER_DAYS_BEFORE) before the date from which a
--    past-due tenant may be suspended.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs as
-- one transaction. Adding a column with a constant default does not rewrite
-- the table (Postgres 11+).

-- ── 1-3 ──────────────────────────────────────────────────────────────────────
alter table tenants
    add column billing_method text not null default 'invoice'
        check (billing_method in ('card', 'invoice')),
    add column card_on_file_at timestamptz,
    add column setup_fee_paid_at timestamptz;

-- ── 4 ────────────────────────────────────────────────────────────────────────
create function record_stripe_card_event(
    p_event_id     text,
    p_event_type   text,
    p_customer_id  text,
    p_kind         text,
    p_amount_cents bigint
) returns text
language plpgsql
security definer
-- Pinned, as in 0029: an unset search_path lets a caller's own schema shadow
-- `tenants`.
set search_path = pg_catalog, public
as $$
declare
    v_tenant   uuid := nullif(current_setting('app.tenant_id', true), '')::uuid;
    v_customer text;
    v_fee      numeric(10,2);
    v_billing  text;
    v_paid_at  timestamptz;
begin
    if p_kind not in ('card', 'setup_fee') then
        raise exception 'record_stripe_card_event: unknown kind %', p_kind;
    end if;
    if v_tenant is null then
        raise exception 'record_stripe_card_event must run inside a tenant session'
            using errcode = 'insufficient_privilege';
    end if;
    if p_event_id is null or p_customer_id is null then
        raise exception 'record_stripe_card_event: event id and customer are required';
    end if;
    if p_kind = 'setup_fee' and p_amount_cents is null then
        raise exception 'record_stripe_card_event: a setup fee needs the amount collected';
    end if;

    -- Lock the tenant row first, so concurrent deliveries for one tenant are
    -- serialised and the duplicate check below sees a settled state.
    select stripe_customer_id, setup_fee_amount, setup_fee_billing, setup_fee_paid_at
      into v_customer, v_fee, v_billing, v_paid_at
      from public.tenants
     where id = v_tenant
       for update;
    if not found then
        raise exception 'record_stripe_card_event: tenant not found';
    end if;
    if v_customer is distinct from p_customer_id then
        raise exception 'record_stripe_card_event: the event''s customer is not this tenant''s'
            using errcode = 'insufficient_privilege';
    end if;

    if exists (select 1 from public.stripe_webhook_events where id = p_event_id) then
        return 'duplicate';
    end if;
    insert into public.stripe_webhook_events (id, type) values (p_event_id, p_event_type);

    if p_kind = 'setup_fee' and v_paid_at is not null then
        return 'already_paid';
    end if;

    update public.tenants
       set card_on_file_at = coalesce(card_on_file_at, now()),
           updated_at      = now()
     where id = v_tenant;

    if p_kind = 'card' then
        return 'applied';
    end if;

    -- A setup fee: marked paid only if it is exactly this tenant's fee, billed
    -- through Stripe. Money is compared in whole cents, never as a float.
    if v_billing is distinct from 'stripe' or v_fee is null
       or (v_fee * 100)::bigint <> p_amount_cents then
        return 'amount_mismatch';
    end if;
    update public.tenants set setup_fee_paid_at = now() where id = v_tenant;
    return 'applied';
end;
$$;

-- ── 5 ────────────────────────────────────────────────────────────────────────
revoke all on function record_stripe_card_event(text, text, text, text, bigint) from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format(
                'revoke all on function record_stripe_card_event(text, text, text, text, bigint) from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'docflow_app') then
        execute 'grant execute on function record_stripe_card_event(text, text, text, text, bigint) to docflow_app';
    end if;
end;
$$;

-- ── 6 ────────────────────────────────────────────────────────────────────────
alter table scheduled_jobs drop constraint scheduled_jobs_job_type_check;
alter table scheduled_jobs add constraint scheduled_jobs_job_type_check
    check (job_type in ('first_week_checkin', 'pending_deletion_reminder', 'review_digest',
                        'past_due_reminder'));
