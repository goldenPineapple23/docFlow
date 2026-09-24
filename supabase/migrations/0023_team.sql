-- DocFlow — Phase 5, slice 5.8d: the Team page
--
-- Recorded as DECISIONS.md D-132. Plan: docs/plans/5.8-tenant-surface.md.
--
-- The account's admin can now invite reviewers, resend an invite, and remove
-- someone. Almost everything that needs already exists: a tenant session may
-- add users to its own tenant (users' tenant_isolation policy), and invites and
-- removals are recorded in tenant_lifecycle_events like the founder's own
-- invite. The one missing fact is whether an invited person has ever signed
-- in, so the Team page can say "Invite sent" or "Signed in", and so a resend
-- is offered only to someone who has not.
--
-- Additive only: one nullable column, nothing dropped, no data touched. Rows
-- that exist today stay NULL until that person's next visit, when the app
-- stamps it (GET /auth/me, which every signed-in screen calls).
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

alter table users add column first_signed_in_at timestamptz;
