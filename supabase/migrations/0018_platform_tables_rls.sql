-- DocFlow — RLS fix: platform_admins and admin_actions
--
-- Recorded as DECISIONS.md D-122.
--
-- Flagged by Supabase's security advisor 2026-09-19/22 on docflow-staging
-- ("rls_disabled_in_public" / "Table publicly accessible") for both tables.
--
-- Migration 0001_foundations.sql deliberately left these two tables without
-- RLS, reasoning "no API ever writes to platform_admins ... admin_actions is
-- written only by docflow_core.admin_data_access" -- i.e. the app's own code
-- never needs a policy to get in. That reasoning is correct for the app's
-- own access path but misses that Supabase's auto-generated PostgREST API
-- exposes every table in the `public` schema to anyone holding the project's
-- anon/service key, independent of whether the app itself goes through that
-- path -- RLS is the only thing that blocks it. The other genuinely-global
-- tables added later (tiers, founder_alerts, onboarding_intakes) all got RLS
-- + a platform_admin_access policy; these two were the oversight.
--
-- platform_session() (packages/core/docflow_core/db.py) already sets
-- `app.is_platform_admin = 'true'` for exactly these two tables, so this is
-- a drop-in fix -- no application code changes.

alter table platform_admins enable row level security;
create policy platform_admin_access on platform_admins
    using (current_setting('app.is_platform_admin', true) = 'true');

alter table admin_actions enable row level security;
create policy platform_admin_access on admin_actions
    using (current_setting('app.is_platform_admin', true) = 'true');
