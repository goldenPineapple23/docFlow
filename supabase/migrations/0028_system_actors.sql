-- DocFlow — Phase 5.5 Stage 1: named system actors, and a cap on idle
-- transactions (D-159, D-163, D-165).
--
-- 1. users.system_actor marks a row that stands for a piece of DocFlow rather
--    than a person. Three are seeded, with fixed ids that
--    docflow_core.system_actors mirrors (a test checks they agree):
--    lifecycle-sweep, maintenance-script, deleted-account. Each one has no
--    tenant, no sign-in id, is inactive and has the least role, so it can
--    never sign in or be invited. A check constraint holds that.
--
-- 2. A trigger refuses any insert, edit or delete of a system-actor row, and
--    any attempt to turn a person into one, unless the connected role is the
--    table's owner (postgres, which applies migrations). The app role
--    (docflow_app) can never touch them. This is decided by who is connected,
--    not by a session setting any connection could set (F-1, D-159).
--
-- 3. Every tenant_lifecycle_events row with no actor is given one, by rule:
--      suspended / pending_deletion_entered -> lifecycle-sweep (the only code
--        that ever wrote these without an actor);
--      renamed -> maintenance-script (the 2026-09-26 test-tenant rename);
--      any other row of an already-deleted tenant -> deleted-account (a
--        person's actor blanked by a past purge).
--    The counts are reported as a NOTICE. Then actor_user_id becomes NOT
--    NULL. If any row is still blank, that statement fails and the whole
--    migration changes nothing: an actor is never guessed.
--
-- 4. idle_in_transaction_session_timeout = 5min for docflow_app. A
--    connection that dies mid-transaction released its row locks only when
--    Postgres noticed (D-162's incident), with no upper bound. Five minutes,
--    not one: a Console plan change holds its transaction across two Stripe
--    requests, which can take about 90 s in the worst case, and cutting that
--    off would leave Stripe and DocFlow disagreeing (D-138, D-163). CI
--    creates docflow_app after the migrations run, so
--    scripts/ci/create_app_role.py sets the same value there.
--
-- Deletes nothing. BACKUP FIRST -- see RUNBOOK.md section 1.1
-- (backup_0028: users, tenant_lifecycle_events).
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs
-- as one transaction.

alter table users add column system_actor text unique;

alter table users add constraint users_system_actor_is_inert check (
    system_actor is null
    or (tenant_id is null and auth_user_id is null and is_active = false and role = 'viewer')
);

insert into users (id, tenant_id, auth_user_id, email, full_name, role, is_active, system_actor)
values
    ('00000000-0000-4000-8000-00000000a001', null, null,
     'lifecycle-sweep@system.docflow.invalid', 'DocFlow: lifecycle sweep', 'viewer', false, 'lifecycle-sweep'),
    ('00000000-0000-4000-8000-00000000a002', null, null,
     'maintenance-script@system.docflow.invalid', 'DocFlow: maintenance script', 'viewer', false, 'maintenance-script'),
    ('00000000-0000-4000-8000-00000000a003', null, null,
     'deleted-account@system.docflow.invalid', 'A deleted account', 'viewer', false, 'deleted-account');

create function users_protect_system_actors() returns trigger
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

create trigger users_protect_system_actors
    before insert or update or delete on users
    for each row execute function users_protect_system_actors();

do $$
declare
    n_sweep integer;
    n_script integer;
    n_deleted integer;
    n_left integer;
begin
    update tenant_lifecycle_events
       set actor_user_id = '00000000-0000-4000-8000-00000000a001'
     where actor_user_id is null
       and event_type in ('suspended', 'pending_deletion_entered');
    get diagnostics n_sweep = row_count;

    update tenant_lifecycle_events
       set actor_user_id = '00000000-0000-4000-8000-00000000a002'
     where actor_user_id is null
       and event_type = 'renamed';
    get diagnostics n_script = row_count;

    update tenant_lifecycle_events
       set actor_user_id = '00000000-0000-4000-8000-00000000a003'
     where actor_user_id is null
       and tenant_id in (select id from tenants where status = 'deleted');
    get diagnostics n_deleted = row_count;

    select count(*) into n_left from tenant_lifecycle_events where actor_user_id is null;

    raise notice '0028: lifecycle-sweep % rows, maintenance-script % rows, deleted-account % rows, still blank % rows',
        n_sweep, n_script, n_deleted, n_left;
end;
$$;

alter table tenant_lifecycle_events alter column actor_user_id set not null;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'docflow_app') then
        execute 'alter role docflow_app set idle_in_transaction_session_timeout = ''5min''';
    end if;
end;
$$;
