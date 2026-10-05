-- DocFlow — the reverse of migration 0036 (Stage 3e; founder's Q5 condition 2).
--
-- Puts the database back exactly as 0035 left it: every flag policy open to
-- every role again, dispatcher_raise without worker_restarting, the 13
-- SECURITY DEFINER functions granted to docflow_app (if it exists), and the
-- five 3e roles, worker_starts and 0036's three functions gone.
--
-- What it loses: the rows of worker_starts (the restart record). Nothing
-- else holds data.
--
-- Tested in CI forward -> reverse -> forward (scripts/ci/migration_roundtrip.py):
-- after the reverse, pg_policies and the function grants must equal
-- supabase/reverse/0036_pre_snapshot.json, which is staging's state before
-- 0036 (read 2026-10-02); after forward again, 0036's state.
--
-- Before running it on staging: follow RUNBOOK 10.2 "Going back", in its
-- order (founder, 2026-10-05). The apps are stopped first; docflow_app gets
-- a new password (its old one is gone after the cutover); this script runs;
-- only then are the apps given docflow_app's URL and started, since the four
-- logins stop existing here.
--
-- Run as one transaction, in the Supabase SQL Editor.

-- 0036 section 3: EXECUTE back to docflow_app.
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
        'count_routing_model_failure()'
    ] loop
        foreach r in array array['docflow_api', 'docflow_admin', 'docflow_worker', 'docflow_stripe'] loop
            execute format('revoke all on function %s from %I', f, r);
        end loop;
        if exists (select 1 from pg_roles where rolname = 'docflow_app') then
            execute format('grant execute on function %s to docflow_app', f);
        end if;
    end loop;
end;
$$;

-- 0036 sections 4 and 5.
drop function dispatch_unclaimed_age();
drop function worker_starts_last_hour();
drop function record_worker_start(text, text);
drop table worker_starts;

-- 0036 section 2 (and 6): every flag policy back to every role.
alter policy platform_admin_access on admin_actions to public;
alter policy platform_admin_access on allowance_notices to public;
alter policy platform_admin_access on buyer_merge_candidates to public;
alter policy platform_admin_access on buyer_merges to public;
alter policy platform_admin_access on buyers to public;
alter policy platform_admin_access on catalog_imports to public;
alter policy platform_admin_access on document_headers to public;
alter policy platform_admin_access on document_lines to public;
alter policy platform_admin_access on document_snapshots to public;
alter policy platform_admin_access on document_warnings to public;
alter policy platform_admin_access on documents to public;
alter policy platform_admin_access on email_outbox to public;
alter policy platform_admin_access on exports to public;
alter policy platform_admin_access on extraction_runs to public;
alter policy platform_admin_access on founder_alerts to public;
alter policy platform_admin_access on import_mapping_templates to public;
alter policy platform_admin_access on intake_addresses to public;
alter policy platform_admin_access on intake_rejections to public;
alter policy platform_admin_access on items to public;
alter policy platform_admin_access on learned_rules to public;
alter policy platform_admin_access on onboarding_intake_files to public;
alter policy platform_admin_access on onboarding_intakes to public;
alter policy platform_admin_access on platform_admins to public;
alter policy platform_admin_access on raw_emails to public;
alter policy platform_admin_access on review_actions to public;
alter policy platform_admin_access on rollup_runs to public;
alter policy platform_admin_access on scheduled_jobs to public;
alter policy platform_admin_write on setup_fee_presets to public;
alter policy platform_admin_read on stripe_webhook_events to public;
alter policy platform_admin_access on tenant_daily_metrics to public;
alter policy platform_admin_access on tenant_field_schemas to public;
alter policy platform_admin_access on tenant_lifecycle_events to public;
alter policy platform_admin_access on tenants to public;
alter policy platform_admin_write on tiers to public;
alter policy platform_admin_access on users to public;

alter policy dispatcher_enqueue on email_outbox to public;
alter policy dispatcher_raise on founder_alerts to public
    with check (current_setting('app.dispatcher', true) = 'true'
                and tenant_id is null
                and type in ('model_api_failure', 'model_api_recovered',
                             'dispatcher_stopped', 'routing_model_failure'));
alter policy lifecycle_read on tenants to public;
alter policy pipeline_sweep_read on tenants to public;
alter policy rollup_enqueue on email_outbox to public;
alter policy rollup_raise on founder_alerts to public;
alter policy rollup_read_own on founder_alerts to public;
alter policy rollup_access on rollup_runs to public;
alter policy rollup_read on tenants to public;
alter policy scheduler_access on scheduled_jobs to public;

alter policy self_lookup on platform_admins to public;
alter policy self_lookup on users to public;
alter policy intake_refusal_enqueue on email_outbox to public;
alter policy intake_refusal_raise on founder_alerts to public;
alter policy token_lookup on intake_addresses to public;

alter policy stripe_webhook_lookup on tenants to public;

-- 0036 section 1: the roles' privileges, then the roles.
alter default privileges in schema public revoke all on tables from docflow_tables;
alter default privileges in schema public revoke all on sequences from docflow_tables;
revoke all on all tables in schema public from docflow_tables;
revoke all on all sequences in schema public from docflow_tables;
revoke usage on schema public from docflow_tables;

drop role docflow_api;
drop role docflow_admin;
drop role docflow_worker;
drop role docflow_stripe;
drop role docflow_tables;
