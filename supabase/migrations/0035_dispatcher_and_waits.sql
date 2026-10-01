-- DocFlow — Phase 5.5 Stage 3d: per-tenant fairness through one dispatcher
-- (review H4), and documents that wait instead of failing when the model
-- provider, Storage or the parse service is down. Agreed with the founder
-- before building (docs/BUILD-STATUS.md, "3d detailed design" and "3d --
-- APPROVED WITH CHANGES", 2026-10-01).
--
-- Touches three existing tables: documents (six columns, one index, one
-- transition), founder_alerts (one insert policy), email_outbox (one insert
-- policy). Adds two genuinely global tables (Section 10: named here as global,
-- no tenant_id): model_provider_state and dispatcher_state. Deletes nothing
-- and changes no existing value: every existing document gets
-- dispatch_lane 'interactive' and NULL in the other new columns, so a
-- `pending` one is "waiting" and the dispatcher sends it.
-- BACKUP FIRST -- see RUNBOOK.md section 1.1
-- (backup_0035: documents, founder_alerts, email_outbox).
--
-- 1. documents:
--    - dispatched_at: NULL = waiting (not yet sent to the queue); set =
--      dispatched (sent, not yet claimed). Only the dispatcher sets it.
--    - dispatch_lane: 'interactive' | 'bulk', decided at intake. It orders a
--      tenant's own documents only (Q3).
--    - waiting_since, wait_cause, retry_at, last_wait_error: a document the
--      worker put back to `pending` because the model provider, Storage or
--      the parse service was down. last_wait_error is a status code or a
--      fixed label only, never response text (Section 7.10).
--
-- 2. processing -> pending is allowed: the wait. The trigger's list and
--    docflow_core.document_status.ALLOWED stay equal (a test checks).
--
-- 3. model_provider_state (one row per provider) and dispatcher_state (one
--    row: the heartbeat). RLS on with no policies: no session reads or writes
--    them except through the functions below.
--
-- 4. SECURITY DEFINER functions, each with search_path set to '' and every
--    name schema-qualified (founder's Q8 condition; a test checks the
--    setting). They return ids, counts and times only -- never a document's
--    content. EXECUTE is revoked from PUBLIC, anon, authenticated and
--    service_role, and granted to docflow_app if it exists (CI grants the
--    same in scripts/ci/create_app_role.py; a test checks they agree).
--    3e moves the grants to the worker's own login.
--
-- 5. The tenant-less alerts the worker raises (model_api_failure,
--    model_api_recovered, dispatcher_stopped, routing_model_failure) are
--    inserted under one flag, app.dispatcher -- insert only, those four
--    types, tenant_id NULL -- like 0017's rollup_raise and 0029's
--    intake_refusal_raise. Their email goes to the outbox the same way.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs as
-- one transaction.

-- ── 1 ────────────────────────────────────────────────────────────────────────
alter table documents
    add column dispatched_at   timestamptz,
    add column dispatch_lane   text not null default 'interactive'
        check (dispatch_lane in ('interactive', 'bulk')),
    add column waiting_since   timestamptz,
    add column wait_cause      text
        check (wait_cause in ('model_provider', 'storage', 'parse_service')),
    add column retry_at        timestamptz,
    add column last_wait_error text;

-- The dispatcher's read: every waiting document, oldest first per tenant.
create index idx_documents_waiting
    on documents (tenant_id, created_at)
    where status = 'pending' and dispatched_at is null and deleted_at is null;
-- When each tenant was last given a slot (its latest dispatched_at): the
-- tie-break that makes the turns go round (dispatch_candidates).
create index idx_documents_last_dispatched
    on documents (tenant_id, dispatched_at desc)
    where dispatched_at is not null;

-- ── 2 ────────────────────────────────────────────────────────────────────────
create or replace function document_status_transition_allowed(old_status text, new_status text)
returns boolean
language sql immutable
as $$
    select old_status = new_status or (old_status, new_status) in (
        ('pending',      'processing'),   -- the worker claims it
        ('pending',      'quarantined'),  -- a hold decided after the row exists
        ('pending',      'failed'),       -- never picked up after every retry; or waited past the maximum (DOC-024)
        ('staged',       'pending'),      -- the founder's "Run extraction" (test batch)
        ('quarantined',  'pending'),      -- released
        ('processing',   'pending'),      -- 3d: waits out a provider, Storage or parse-service outage
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

-- ── 3 ────────────────────────────────────────────────────────────────────────
create table model_provider_state (
    provider         text primary key,
    status           text not null default 'up' check (status in ('up', 'down')),
    down_since       timestamptz,
    -- 5xx | overloaded | rate_limited | network | our_configuration
    down_cause       text,
    -- A status code or fixed label only (Section 7.10).
    last_error       text,
    -- Waiting-class failures since the last success, for "3 in 5 minutes".
    recent_failures  timestamptz[] not null default '{}',
    last_success_at  timestamptz,
    last_probe_at    timestamptz,
    updated_at       timestamptz not null default now()
);
alter table model_provider_state enable row level security;
insert into model_provider_state (provider) values ('anthropic');

create table dispatcher_state (
    id            boolean primary key default true check (id),
    last_pass_at  timestamptz
);
alter table dispatcher_state enable row level security;
insert into dispatcher_state (id) values (true);

-- ── 4 ────────────────────────────────────────────────────────────────────────

-- Per tenant: how many documents are in flight (dispatched or processing),
-- when the tenant was last given a slot, and up to p_per_tenant documents
-- that may be sent now (waiting, with no retry_at or one already past). A
-- tenant with documents in flight and none ready comes back as one row with
-- document_id NULL. Ordered within a tenant: interactive before bulk, then
-- oldest first.
create function dispatch_candidates(p_per_tenant integer)
returns table (tenant_id uuid, in_flight integer, last_dispatched_at timestamptz,
               document_id uuid, lane text, arrived_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
    with in_flight as (
        select d.tenant_id, count(*)::integer as n
          from public.documents d
         where d.deleted_at is null
           and (d.status = 'processing' or (d.status = 'pending' and d.dispatched_at is not null))
         group by d.tenant_id
    ),
    ready as (
        select d.tenant_id, d.id, d.dispatch_lane, d.created_at,
               row_number() over (
                   partition by d.tenant_id
                   order by (d.dispatch_lane = 'bulk'), d.created_at, d.id
               ) as rn
          from public.documents d
         where d.deleted_at is null
           and d.status = 'pending'
           and d.dispatched_at is null
           and (d.retry_at is null or d.retry_at <= pg_catalog.now())
    ),
    merged as (
        select coalesce(r.tenant_id, i.tenant_id) as tenant_id, coalesce(i.n, 0) as n,
               r.id, r.dispatch_lane, r.created_at
          from (select * from ready where rn <= p_per_tenant) r
          full join in_flight i on i.tenant_id = r.tenant_id
    )
    select m.tenant_id, m.n,
           (select max(d.dispatched_at) from public.documents d
             where d.tenant_id = m.tenant_id and d.dispatched_at is not null),
           m.id, m.dispatch_lane, m.created_at
      from merged m
$$;

-- While the provider is down, the one document the dispatcher sends as a
-- probe: the oldest one waiting on the provider, else the oldest waiting at
-- all. Its own retry_at is ignored -- the probe is its retry.
create function probe_candidate()
returns table (tenant_id uuid, document_id uuid)
language sql
stable
security definer
set search_path = ''
as $$
    select d.tenant_id, d.id
      from public.documents d
     where d.deleted_at is null and d.status = 'pending' and d.dispatched_at is null
     order by (d.wait_cause is distinct from 'model_provider'), d.created_at, d.id
     limit 1
$$;

-- Compare-and-set: only a document still waiting is marked dispatched. Its
-- retry_at is cleared with it (a probe is sent before its own backoff ends),
-- so the claim, which refuses a document whose retry_at is still ahead, takes
-- every dispatched document.
create function mark_dispatched(p_document_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    update public.documents
       set dispatched_at = pg_catalog.now(), retry_at = null
     where id = p_document_id
       and status = 'pending'
       and dispatched_at is null
       and deleted_at is null;
    return found;
end;
$$;

-- A send that failed after the commit: the document goes back to waiting,
-- so it isn't counted in flight.
create function clear_dispatched(p_document_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    update public.documents
       set dispatched_at = null
     where id = p_document_id
       and status = 'pending'
       and dispatched_at is not null;
    return found;
end;
$$;

create function dispatcher_heartbeat()
returns void
language sql
security definer
set search_path = ''
as $$
    update public.dispatcher_state set last_pass_at = pg_catalog.now() where id
$$;

-- The heartbeat's age and how many documents are waiting (counts only).
-- age_seconds is NULL when the dispatcher has never run.
create function dispatcher_status()
returns table (last_pass_at timestamptz, age_seconds integer, waiting integer)
language sql
stable
security definer
set search_path = ''
as $$
    select s.last_pass_at,
           floor(extract(epoch from (pg_catalog.now() - s.last_pass_at)))::integer,
           (select count(*)::integer
              from public.documents d
             where d.status = 'pending' and d.dispatched_at is null and d.deleted_at is null)
      from public.dispatcher_state s
     where s.id
$$;

create function provider_state(p_provider text)
returns table (status text, down_since timestamptz, down_cause text)
language sql
stable
security definer
set search_path = ''
as $$
    select p.status, p.down_since, p.down_cause
      from public.model_provider_state p
     where p.provider = p_provider
$$;

-- One waiting-class failure. The provider is marked down at the
-- p_threshold-th failure within p_window_min minutes with no success between
-- them -- or at once when p_immediate (our own configuration: a wrong key, a
-- retired model, no credit). newly_down is true only for the call that
-- marked it down, so the outage gets one alert. waiting counts the documents
-- now waiting on the provider.
create function provider_record_failure(
    p_provider    text,
    p_cause       text,
    p_error       text,
    p_immediate   boolean,
    p_threshold   integer,
    p_window_min  integer
) returns table (newly_down boolean, down_since timestamptz, waiting integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_status   text;
    v_recent   timestamptz[];
    v_since    timestamptz;
    v_newly    boolean := false;
begin
    select p.status, p.recent_failures, p.down_since
      into v_status, v_recent, v_since
      from public.model_provider_state p
     where p.provider = p_provider
       for update;
    if not found then
        raise exception 'provider_record_failure: unknown provider %', p_provider;
    end if;

    select coalesce(pg_catalog.array_agg(t order by t), '{}')
      into v_recent
      from pg_catalog.unnest(v_recent || pg_catalog.now()) as t
     where t > pg_catalog.now() - pg_catalog.make_interval(mins => p_window_min);

    if v_status = 'up' and (p_immediate or pg_catalog.cardinality(v_recent) >= p_threshold) then
        v_status := 'down';
        v_since := pg_catalog.now();
        v_newly := true;
        update public.model_provider_state
           set status = 'down', down_since = v_since, down_cause = p_cause,
               last_error = p_error, recent_failures = v_recent, last_probe_at = pg_catalog.now(),
               updated_at = pg_catalog.now()
         where provider = p_provider;
    else
        update public.model_provider_state
           set last_error = p_error, recent_failures = v_recent, updated_at = pg_catalog.now()
         where provider = p_provider;
    end if;

    return query
        select v_newly, v_since,
               (select count(*)::integer
                  from public.documents d
                 where d.status = 'pending' and d.wait_cause = 'model_provider'
                   and d.deleted_at is null);
end;
$$;

-- One success. Clears the failure window. If the provider was down, marks it
-- up, clears retry_at on every document waiting on it (the backlog goes out
-- through the normal turn-taking at once, not each on its own backoff), and
-- returns what the recovery notice reports.
create function provider_record_success(p_provider text)
returns table (recovered boolean, down_since timestamptz, down_cause text, waiting integer, failed_at_limit integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_status text;
    v_since  timestamptz;
    v_cause  text;
    v_waiting integer;
    v_failed  integer;
begin
    select p.status, p.down_since, p.down_cause
      into v_status, v_since, v_cause
      from public.model_provider_state p
     where p.provider = p_provider
       for update;
    if not found then
        raise exception 'provider_record_success: unknown provider %', p_provider;
    end if;

    if v_status = 'up' then
        update public.model_provider_state
           set recent_failures = '{}', last_success_at = pg_catalog.now()
         where provider = p_provider
           and (pg_catalog.cardinality(recent_failures) > 0 or last_success_at is null
                or last_success_at < pg_catalog.now() - interval '1 minute');
        return query select false, null::timestamptz, null::text, 0, 0;
        return;
    end if;

    update public.model_provider_state
       set status = 'up', down_since = null, down_cause = null, recent_failures = '{}',
           last_success_at = pg_catalog.now(), updated_at = pg_catalog.now()
     where provider = p_provider;

    update public.documents
       set retry_at = null
     where status = 'pending' and wait_cause = 'model_provider' and deleted_at is null;
    get diagnostics v_waiting = row_count;

    select count(*)::integer into v_failed
      from public.documents d
     where d.failure_code = 'DOC-024' and d.processed_at >= v_since;

    return query select true, v_since, v_cause, v_waiting, v_failed;
end;
$$;

-- While down: true at most once every p_probe_minutes, for the pass that
-- sends the probe.
create function provider_take_probe(p_provider text, p_probe_minutes integer)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    update public.model_provider_state
       set last_probe_at = pg_catalog.now()
     where provider = p_provider
       and status = 'down'
       and (last_probe_at is null
            or last_probe_at <= pg_catalog.now() - pg_catalog.make_interval(mins => p_probe_minutes));
    return found;
end;
$$;

-- routing_model_failure (founder's condition 2): one more document went
-- without examples today. Adds one to the open alert's count and returns the
-- new count, or 0 when there is no open alert for today (the caller raised
-- it instead).
create function count_routing_model_failure()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_count integer;
begin
    update public.founder_alerts
       set payload = pg_catalog.jsonb_set(
               payload, '{documents_without_examples}',
               pg_catalog.to_jsonb(coalesce((payload ->> 'documents_without_examples')::integer, 0) + 1))
     where type = 'routing_model_failure'
       and acknowledged_at is null
       and dedupe_key = 'routing_model_failure:' || ((pg_catalog.now() at time zone 'UTC')::date)::text
    returning (payload ->> 'documents_without_examples')::integer into v_count;
    return coalesce(v_count, 0);
end;
$$;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array[
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
        'count_routing_model_failure()'
    ] loop
        execute format('revoke all on function %s from public', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'docflow_app') then
        execute 'grant execute on function dispatch_candidates(integer) to docflow_app';
        execute 'grant execute on function probe_candidate() to docflow_app';
        execute 'grant execute on function mark_dispatched(uuid) to docflow_app';
        execute 'grant execute on function clear_dispatched(uuid) to docflow_app';
        execute 'grant execute on function dispatcher_heartbeat() to docflow_app';
        execute 'grant execute on function dispatcher_status() to docflow_app';
        execute 'grant execute on function provider_state(text) to docflow_app';
        execute 'grant execute on function provider_record_failure(text, text, text, boolean, integer, integer) to docflow_app';
        execute 'grant execute on function provider_record_success(text) to docflow_app';
        execute 'grant execute on function provider_take_probe(text, integer) to docflow_app';
        execute 'grant execute on function count_routing_model_failure() to docflow_app';
    end if;
end;
$$;

-- ── 5 ────────────────────────────────────────────────────────────────────────
create policy dispatcher_raise on founder_alerts for insert
    with check (current_setting('app.dispatcher', true) = 'true'
                and tenant_id is null
                and type in ('model_api_failure', 'model_api_recovered',
                             'dispatcher_stopped', 'routing_model_failure'));
create policy dispatcher_enqueue on email_outbox for insert
    with check (current_setting('app.dispatcher', true) = 'true'
                and tenant_id is null and template = 'founder_alert'
                and related_type = 'founder_alert');
