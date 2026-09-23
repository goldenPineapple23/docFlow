-- DocFlow — URGENT fix: platform_admins is unreadable during login (D-124)
--
-- Migration 0018 enabled RLS on platform_admins with only a
-- platform_admin_access policy (requires app.is_platform_admin = 'true').
-- But the ONE place platform_admins is read on every single request --
-- app/deps.py's _resolve_identity, via docflow_core.db.identity_lookup_session
-- -- only ever sets app.auth_user_id, never app.is_platform_admin (that flag
-- doesn't exist yet at the moment we're trying to determine it). Since 0018
-- shipped with no other policy, that SELECT returns zero rows for everyone,
-- including a real platform admin: every /admin/* route 404s unconditionally,
-- because require_platform_admin never sees is_platform_admin=true.
--
-- This was caught by CI/local tests the same day 0018 was applied to
-- docflow-staging, before it reached docflow-prod. Apply this immediately
-- after 0018 wherever 0018 has already run.
--
-- Fix: a narrow self_lookup policy, exactly like the one users already has
-- (0001_foundations.sql) -- visibility into exactly the one row matching the
-- currently-authenticated user, nothing else. It does not reopen the table:
-- a session with no app.auth_user_id set (or one that resolves to no user
-- row) still sees nothing.

create policy self_lookup on platform_admins
    using (
        user_id = (
            select id from users
            where auth_user_id = nullif(current_setting('app.auth_user_id', true), '')::uuid
        )
    );
