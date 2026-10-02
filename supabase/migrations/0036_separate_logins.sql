-- DocFlow — Phase 5.5 Stage 3e: separate database logins (finding F-1,
-- D-159), the worker's restart record, and the heartbeat's unclaimed check.
-- Agreed with the founder before building (docs/BUILD-STATUS.md, "3e
-- detailed design" and "3e -- APPROVED WITH CONDITIONS", 2026-10-02).
--
-- Changes no existing row. It changes who may use which policy and
-- function, and adds one genuinely global table (Section 10: named here as
-- global, no tenant_id): worker_starts. So there is no backup schema: the
-- founder's Q5 condition is a snapshot of pg_policies and the function
-- grants before applying, and the reverse script
-- (supabase/reverse/0036_reverse.sql), tested forward -> reverse -> forward
-- in CI against the snapshot committed beside it.
--
-- NOT APPLIED TO STAGING until Claude has reported the backup_0035 check (on
-- or after 2026-10-05 03:18 UTC) and the founder has confirmed (Q5,
-- condition 1). RUNBOOK section 10 has the cutover steps.
--
-- 1. Five roles, all NOLOGIN, with no password in this file (D-013, D-159):
--    - docflow_tables: holds the table and sequence privileges docflow_app
--      has today. Never a login.
--    - docflow_api, docflow_admin, docflow_worker, docflow_stripe: the four
--      logins, each a member of docflow_tables and of nothing else. A policy
--      TO docflow_admin applies to every member of docflow_admin, so no login
--      may ever be a member of another (a test checks). The founder turns
--      each on in the SQL Editor: ALTER ROLE ... WITH LOGIN PASSWORD '...'.
--
-- 2. Every RLS policy keyed on an app.* flag other than app.tenant_id gets a
--    TO naming exactly one login (51 on staging, listed 2026-10-02). The flag
--    condition stays (founder, Q3): the TO is the database's guarantee, the
--    flag keeps each narrow session narrow inside its login. tenant_isolation
--    and the two read_all policies (tiers, setup_fee_presets) stay open to
--    every role, as before.
--
-- 3. EXECUTE on the 13 SECURITY DEFINER functions moves from docflow_app to
--    the logins that call them. The two Stripe event functions go to
--    docflow_stripe alone, which closes D-173's residual risk.
--
-- 4. worker_starts (the restart record, 3e part B): RLS on, no policies;
--    reached only through record_worker_start() (docflow_worker) and
--    worker_starts_last_hour() (docflow_api, docflow_admin). Rows are kept
--    (founder, Q6).
--
-- 5. dispatch_unclaimed_age() (3e part C2): the age of the oldest document
--    dispatched but not yet claimed, for the heartbeat's /fail check.
--
-- 6. The tenant-less alert insert policy dispatcher_raise also allows
--    worker_restarting, raised by the worker's launcher at start-up.
--
-- Every function here has search_path set to '' and schema-qualified names
-- (as 0035; a test checks the setting), and returns counts and times only.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs as
-- one transaction.

-- ── 1 ────────────────────────────────────────────────────────────────────────
-- Roles are cluster-wide, so a database rebuilt from these files on the same
-- server (the local stack) may already have them.
do $$
declare
    r text;
begin
    foreach r in array array['docflow_tables', 'docflow_api', 'docflow_admin',
                             'docflow_worker', 'docflow_stripe'] loop
        if not exists (select 1 from pg_roles where rolname = r) then
            execute format('create role %I nologin nobypassrls', r);
        end if;
    end loop;
end;
$$;

grant usage on schema public to docflow_tables;
grant all on all tables in schema public to docflow_tables;
grant all on all sequences in schema public to docflow_tables;
alter default privileges in schema public grant all on tables to docflow_tables;
alter default privileges in schema public grant all on sequences to docflow_tables;

grant docflow_tables to docflow_api, docflow_admin, docflow_worker, docflow_stripe;

-- Matches 0028's setting for docflow_app (the app's 5-minute idle cap, D-165).
alter role docflow_api set idle_in_transaction_session_timeout = '5min';
alter role docflow_admin set idle_in_transaction_session_timeout = '5min';
alter role docflow_worker set idle_in_transaction_session_timeout = '5min';
alter role docflow_stripe set idle_in_transaction_session_timeout = '5min';

-- ── 2 ────────────────────────────────────────────────────────────────────────
-- app.is_platform_admin -> docflow_admin (35)
alter policy platform_admin_access on admin_actions to docflow_admin;
alter policy platform_admin_access on allowance_notices to docflow_admin;
alter policy platform_admin_access on buyer_merge_candidates to docflow_admin;
alter policy platform_admin_access on buyer_merges to docflow_admin;
alter policy platform_admin_access on buyers to docflow_admin;
alter policy platform_admin_access on catalog_imports to docflow_admin;
alter policy platform_admin_access on document_headers to docflow_admin;
alter policy platform_admin_access on document_lines to docflow_admin;
alter policy platform_admin_access on document_snapshots to docflow_admin;
alter policy platform_admin_access on document_warnings to docflow_admin;
alter policy platform_admin_access on documents to docflow_admin;
alter policy platform_admin_access on email_outbox to docflow_admin;
alter policy platform_admin_access on exports to docflow_admin;
alter policy platform_admin_access on extraction_runs to docflow_admin;
alter policy platform_admin_access on founder_alerts to docflow_admin;
alter policy platform_admin_access on import_mapping_templates to docflow_admin;
alter policy platform_admin_access on intake_addresses to docflow_admin;
alter policy platform_admin_access on intake_rejections to docflow_admin;
alter policy platform_admin_access on items to docflow_admin;
alter policy platform_admin_access on learned_rules to docflow_admin;
alter policy platform_admin_access on onboarding_intake_files to docflow_admin;
alter policy platform_admin_access on onboarding_intakes to docflow_admin;
alter policy platform_admin_access on platform_admins to docflow_admin;
alter policy platform_admin_access on raw_emails to docflow_admin;
alter policy platform_admin_access on review_actions to docflow_admin;
alter policy platform_admin_access on rollup_runs to docflow_admin;
alter policy platform_admin_access on scheduled_jobs to docflow_admin;
alter policy platform_admin_write on setup_fee_presets to docflow_admin;
alter policy platform_admin_read on stripe_webhook_events to docflow_admin;
alter policy platform_admin_access on tenant_daily_metrics to docflow_admin;
alter policy platform_admin_access on tenant_field_schemas to docflow_admin;
alter policy platform_admin_access on tenant_lifecycle_events to docflow_admin;
alter policy platform_admin_access on tenants to docflow_admin;
alter policy platform_admin_write on tiers to docflow_admin;
alter policy platform_admin_access on users to docflow_admin;

-- app.rollup, app.scheduler, app.lifecycle, app.pipeline_sweep, app.dispatcher
-- -> docflow_worker (10). dispatcher_raise also gains worker_restarting (6).
alter policy dispatcher_enqueue on email_outbox to docflow_worker;
alter policy dispatcher_raise on founder_alerts to docflow_worker
    with check (current_setting('app.dispatcher', true) = 'true'
                and tenant_id is null
                and type in ('model_api_failure', 'model_api_recovered',
                             'dispatcher_stopped', 'routing_model_failure',
                             'worker_restarting'));
alter policy lifecycle_read on tenants to docflow_worker;
alter policy pipeline_sweep_read on tenants to docflow_worker;
alter policy rollup_enqueue on email_outbox to docflow_worker;
alter policy rollup_raise on founder_alerts to docflow_worker;
alter policy rollup_read_own on founder_alerts to docflow_worker;
alter policy rollup_access on rollup_runs to docflow_worker;
alter policy rollup_read on tenants to docflow_worker;
alter policy scheduler_access on scheduled_jobs to docflow_worker;

-- app.auth_user_id, app.intake_token, app.intake_refusal -> docflow_api (5)
alter policy self_lookup on platform_admins to docflow_api;
alter policy self_lookup on users to docflow_api;
alter policy intake_refusal_enqueue on email_outbox to docflow_api;
alter policy intake_refusal_raise on founder_alerts to docflow_api;
alter policy token_lookup on intake_addresses to docflow_api;

-- app.stripe_webhook -> docflow_stripe (1)
alter policy stripe_webhook_lookup on tenants to docflow_stripe;

-- ── 4 ────────────────────────────────────────────────────────────────────────
create table worker_starts (
    id          bigint generated always as identity primary key,
    started_at  timestamptz not null default now(),
    -- FLY_MACHINE_ID on Fly, the host name locally. Never anything else.
    machine     text not null,
    -- FLY_IMAGE_REF when set.
    image       text
);
alter table worker_starts enable row level security;
create index worker_starts_started_at on worker_starts (started_at desc);

-- Records one start and returns the starts in the last 60 minutes, this one
-- included. The launcher raises worker_restarting at 3 or more (founder,
-- 2026-10-01).
create function record_worker_start(p_machine text, p_image text)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    n integer;
begin
    insert into public.worker_starts (machine, image)
    values (pg_catalog.left(p_machine, 200), pg_catalog.left(p_image, 300));
    select count(*)::integer into n
      from public.worker_starts
     where started_at > pg_catalog.now() - interval '60 minutes';
    return n;
end;
$$;

create function worker_starts_last_hour()
returns integer
language sql
stable
security definer
set search_path = ''
as $$
    select count(*)::integer
      from public.worker_starts
     where started_at > pg_catalog.now() - interval '60 minutes'
$$;

-- ── 5 ────────────────────────────────────────────────────────────────────────
-- Seconds since the oldest document still dispatched but not claimed was
-- sent; NULL when there is none.
create function dispatch_unclaimed_age()
returns integer
language sql
stable
security definer
set search_path = ''
as $$
    select floor(extract(epoch from (pg_catalog.now() - min(d.dispatched_at))))::integer
      from public.documents d
     where d.status = 'pending' and d.dispatched_at is not null and d.deleted_at is null
$$;

-- ── 3 ────────────────────────────────────────────────────────────────────────
do $$
declare
    f text;
    r text;
begin
    foreach f in array array[
        'record_stripe_subscription_event(text, text, timestamptz, text, text, timestamptz, text)',
        'record_stripe_card_event(text, text, text, text, bigint)',
        'dispatch_candidates(integer)',
        'probe_candidate()',
        'mark_dispatched(uuid)',
        'clear_dispatched(uuid)',
        'dispatcher_heartbeat()',
        'dispatcher_status()',
        'provider_state(text)',
        'provider_record_failure(text, text, text, boolean, integer, integer)',
        'provider_record_success(text)',
        'provider_take_probe(text, integer)',
        'count_routing_model_failure()',
        'record_worker_start(text, text)',
        'worker_starts_last_hour()',
        'dispatch_unclaimed_age()'
    ] loop
        execute format('revoke all on function %s from public', f);
        foreach r in array array['anon', 'authenticated', 'service_role', 'docflow_app'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
    end loop;
end;
$$;

grant execute on function record_stripe_subscription_event(text, text, timestamptz, text, text, timestamptz, text) to docflow_stripe;
grant execute on function record_stripe_card_event(text, text, text, text, bigint) to docflow_stripe;

grant execute on function dispatch_candidates(integer) to docflow_worker;
grant execute on function probe_candidate() to docflow_worker;
grant execute on function mark_dispatched(uuid) to docflow_worker;
grant execute on function clear_dispatched(uuid) to docflow_worker;
grant execute on function dispatcher_heartbeat() to docflow_worker;
grant execute on function provider_record_failure(text, text, text, boolean, integer, integer) to docflow_worker;
grant execute on function provider_record_success(text) to docflow_worker;
grant execute on function provider_take_probe(text, integer) to docflow_worker;
grant execute on function count_routing_model_failure() to docflow_worker;
grant execute on function record_worker_start(text, text) to docflow_worker;
grant execute on function dispatch_unclaimed_age() to docflow_worker;

grant execute on function dispatcher_status() to docflow_api, docflow_worker, docflow_admin;
grant execute on function provider_state(text) to docflow_api, docflow_worker, docflow_admin;
grant execute on function worker_starts_last_hour() to docflow_api, docflow_admin;
