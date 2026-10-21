-- DocFlow — the reverse of migration 0039 (Phase 5.5 Stage 5, M7; D-214).
--
-- Puts every grant, policy and function back as 0038 left them: the state in
-- supabase/reverse/0039_pre_snapshot.json (staging before 0039, read-only,
-- 2026-10-21), which CI's round trip compares against
-- (scripts/ci/migration_0039_roundtrip.py, scripts/ci/grants_snapshot.py).
--
--   5. anon and authenticated get every privilege on every table and
--      sequence in public back, and postgres's default privileges in public
--      give them new tables, sequences and functions again.
--   4. The four Advisor functions: PUBLIC, anon and authenticated may
--      EXECUTE them again, the three DocFlow logins' grant on
--      document_status_transition_allowed goes (they hold it through PUBLIC
--      again), and the bodies and settings are 0026's, 0027's, 0028's and
--      0035's, word for word.
--   3. read_all on tiers and setup_fee_presets applies to every role again.
--   2. rollup_raise allows `rollup_stale` only.
--   1. staging_intakes_past_ttl is dropped.
--
-- No row is changed or removed. Alerts of type staging_ttl_elapsed already
-- raised stay; the rollup can't raise another, and the code that comes with
-- 0039 logs that one check as failed and carries on. So run it with the code
-- from before 0039 deployed.

-- ── 5 ────────────────────────────────────────────────────────────────────────
alter default privileges for role postgres in schema public grant execute on functions to anon, authenticated;
alter default privileges for role postgres in schema public grant all on sequences to anon, authenticated;
alter default privileges for role postgres in schema public grant all on tables to anon, authenticated;
grant all on all sequences in schema public to anon, authenticated;
grant all on all tables in schema public to anon, authenticated;

-- ── 4 ────────────────────────────────────────────────────────────────────────
revoke execute on function public.document_status_transition_allowed(text, text) from docflow_api, docflow_worker, docflow_admin;
grant execute on function public.document_status_transition_allowed(text, text) to public, anon, authenticated;
grant execute on function public.documents_status_guard() to public, anon, authenticated;
grant execute on function public.exports_finished_rows_are_immutable() to public, anon, authenticated;
grant execute on function public.users_protect_system_actors() to public, anon, authenticated;

alter function public.document_status_transition_allowed(text, text) reset search_path;
alter function public.exports_finished_rows_are_immutable() reset search_path;

-- 0027's body. CREATE OR REPLACE with no SET clause also clears the setting.
create or replace function public.documents_status_guard() returns trigger
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

-- 0028's body.
create or replace function public.users_protect_system_actors() returns trigger
language plpgsql
as $$
begin
    -- The table's owner (migrations) may manage system actors; nobody else.
    if pg_has_role(current_user, (select relowner from pg_class where oid = tg_relid), 'MEMBER') then
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

-- ── 3 ────────────────────────────────────────────────────────────────────────
alter policy read_all on public.tiers to public;
alter policy read_all on public.setup_fee_presets to public;

-- ── 2 ────────────────────────────────────────────────────────────────────────
alter policy rollup_raise on public.founder_alerts
    with check (current_setting('app.rollup', true) = 'true'
                and type = 'rollup_stale' and tenant_id is null);

-- ── 1 ────────────────────────────────────────────────────────────────────────
drop function public.staging_intakes_past_ttl(integer);
