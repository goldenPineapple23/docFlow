-- DocFlow — M7: the staging-TTL alert's one read, the rollup's permission to
-- raise it, and Supabase's Data API roles shut out of `public` (Phase 5.5
-- Stage 5, M7; DECISIONS.md D-214).
--
-- What it changes. No row, no table, no column.
--
--   1. A SECURITY DEFINER function, staging_intakes_past_ttl(ttl_days), for
--      the nightly rollup's staging-TTL check (Section 7.15.2 Step 1:
--      staging files not linked to a tenant within STAGING_TTL_DAYS appear in
--      the attention panel). onboarding_intakes is readable only by
--      docflow_admin under the platform flag (0011, 0036), and the worker
--      holds no admin login. The function returns ids, times and a file
--      count: never the prospect's name or address. EXECUTE: docflow_worker
--      only.
--
--   2. 0017's rollup_raise policy may also insert `staging_ttl_elapsed`. It
--      stays TO docflow_worker, tenant-less only. rollup_read_own and
--      rollup_enqueue are unchanged (founder, 2026-10-21).
--
--   3. The read_all policies on tiers and setup_fee_presets (0011, 0014)
--      apply TO docflow_tables instead of every role: all four DocFlow
--      logins are members of it (0036), so each reads both tables, and anon
--      and authenticated, who are not, stay outside the policy. With section
--      5 they also hold no privilege on either table; the policy is the
--      second layer. Not a list of the logins that read the tables today: a
--      login left out of such a list reads no row, silently, and a LEFT JOIN
--      turns that into a missing tier (founder, 2026-10-21; D-214).
--
--   4. The four functions Supabase's Security Advisor flags get an empty
--      search_path, with every function and table they name written with
--      its schema (operators and built-ins come from pg_catalog, which
--      Postgres always searches first). Their behaviour is unchanged.
--      EXECUTE is revoked from PUBLIC, anon and authenticated, and
--      document_status_transition_allowed is granted back to the three
--      logins that change a document's status (docflow_api, docflow_worker,
--      docflow_admin): documents_status_guard calls it as the login that
--      updates the row. The three trigger functions need no grant: Postgres
--      checks EXECUTE on a trigger function when the trigger is created, not
--      when it fires. service_role keeps its grant (not named, not used).
--
--   5. anon and authenticated lose every privilege on every table and
--      sequence in public, and postgres's default privileges in public stop
--      giving them tables, sequences and functions created later. DocFlow
--      never calls the Data API; its logins hold their privileges through
--      docflow_tables (0036), which this does not touch.
--      supabase_admin's own default privileges in public still give anon and
--      authenticated everything it creates there. postgres is not a member
--      of supabase_admin, so they can't be altered from the SQL Editor
--      (read on staging 2026-10-21). DocFlow creates nothing as
--      supabase_admin; Phase 6 turns the Data API off in production.
--
-- Reverse: supabase/reverse/0039_reverse.sql, tested in CI forward ->
-- reverse -> forward (scripts/ci/migration_0039_roundtrip.py), against
-- supabase/reverse/0039_pre_snapshot.json (staging before 0039, read-only,
-- 2026-10-21) and 0039_post_snapshot.json (scripts/ci/grants_snapshot.py).
--
-- Deploy order: this migration first, then the code. The code that comes
-- with it calls staging_intakes_past_ttl and raises staging_ttl_elapsed in
-- the rollup's session; on a database without 0039 that one check fails,
-- is logged, and the rollup carries on (D-214). Code from before it is
-- unaffected.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor, as
-- postgres. It runs as one transaction.

-- ── 1 ────────────────────────────────────────────────────────────────────────
create function public.staging_intakes_past_ttl(ttl_days integer)
returns table (intake_id uuid, created_at timestamptz, files integer)
language sql stable security definer
set search_path = ''
as $$
    select i.id,
           i.created_at,
           (select count(*) from public.onboarding_intake_files f
             where f.intake_id = i.id and f.deleted_at is null)::integer
      from public.onboarding_intakes i
     where i.linked_tenant_id is null
       and i.deleted_at is null
       and i.created_at < now() - make_interval(days => ttl_days)
     order by i.created_at, i.id
$$;
revoke all on function public.staging_intakes_past_ttl(integer) from public, anon, authenticated, service_role;
grant execute on function public.staging_intakes_past_ttl(integer) to docflow_worker;

-- ── 2 ────────────────────────────────────────────────────────────────────────
alter policy rollup_raise on public.founder_alerts
    with check (current_setting('app.rollup', true) = 'true'
                and type in ('rollup_stale', 'staging_ttl_elapsed') and tenant_id is null);

-- ── 3 ────────────────────────────────────────────────────────────────────────
alter policy read_all on public.tiers to docflow_tables;
alter policy read_all on public.setup_fee_presets to docflow_tables;

-- ── 4 ────────────────────────────────────────────────────────────────────────
-- Bodies that name nothing outside pg_catalog: the setting alone.
alter function public.document_status_transition_allowed(text, text) set search_path = '';
alter function public.exports_finished_rows_are_immutable() set search_path = '';

create or replace function public.documents_status_guard() returns trigger
language plpgsql
set search_path = ''
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
        if not public.document_status_transition_allowed(old.status, new.status) then
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

create or replace function public.users_protect_system_actors() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    -- The table's owner (migrations) may manage system actors; nobody else.
    if pg_catalog.pg_has_role(current_user, (select relowner from pg_catalog.pg_class where oid = tg_relid), 'MEMBER') then
        return coalesce(new, old);
    end if;
    if tg_op in ('UPDATE', 'DELETE') and old.system_actor is not null then
        raise exception 'users: system actor % cannot be changed or deleted', old.system_actor
            using errcode = 'insufficient_privilege';
    end if;
    if tg_op in ('INSERT', 'UPDATE') and new.system_actor is not null then
        raise exception 'users: only a migration can create a system actor'
            using errcode = 'insufficient_privilege';
    end if;
    return coalesce(new, old);
end;
$$;

revoke execute on function public.document_status_transition_allowed(text, text) from public, anon, authenticated;
revoke execute on function public.documents_status_guard() from public, anon, authenticated;
revoke execute on function public.exports_finished_rows_are_immutable() from public, anon, authenticated;
revoke execute on function public.users_protect_system_actors() from public, anon, authenticated;
grant execute on function public.document_status_transition_allowed(text, text) to docflow_api, docflow_worker, docflow_admin;

-- ── 5 ────────────────────────────────────────────────────────────────────────
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke execute on functions from anon, authenticated;
