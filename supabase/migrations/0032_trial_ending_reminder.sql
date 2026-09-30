-- DocFlow — Phase 5.5, card billing follow-up: the trial-ending email and
-- Reply-To (founder, 2026-09-29; D-181 addendum).
--
-- Touches two tables: scheduled_jobs (its job-type check) and email_outbox
-- (one new column). Deletes nothing and changes no existing value.
-- BACKUP FIRST -- see RUNBOOK.md section 1.1 (backup_0032: scheduled_jobs,
-- email_outbox). Neither change alters a row; the backup is the standing
-- precaution.
--
-- 1. scheduled_jobs.job_type gains 'trial_ending_reminder': the card-billed
--    owner's email TRIAL_ENDING_REMINDER_DAYS_BEFORE (2) days before the
--    trial ends, scheduled at go-live.
--
-- 2. email_outbox.reply_to -- the Reply-To address for this email: the
--    support mailbox (SUPPORT_EMAIL) on every email to the customer's own
--    people, NULL on founder alerts and on the intake address's automatic
--    replies to buyers. Filled when the email is queued, so the row records
--    the address the email was written with; the Console Outbox shows it.
--    NULL on every existing row.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs as
-- one transaction. A nullable column with no default does not rewrite the
-- table.

-- ── 1 ────────────────────────────────────────────────────────────────────────
alter table scheduled_jobs drop constraint scheduled_jobs_job_type_check;
alter table scheduled_jobs add constraint scheduled_jobs_job_type_check
    check (job_type in ('first_week_checkin', 'pending_deletion_reminder', 'review_digest',
                        'past_due_reminder', 'trial_ending_reminder'));

-- ── 2 ────────────────────────────────────────────────────────────────────────
alter table email_outbox add column reply_to text;
