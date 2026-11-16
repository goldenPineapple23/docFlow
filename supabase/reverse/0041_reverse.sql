-- DocFlow — the reverse of migration 0041 (Phase 5.5, B1's demo half;
-- D-230; docs/designs/b1.md section 4).
--
-- Puts the database back as 0040 left it: none of 0041's columns on
-- raw_emails, documents, intake_rejections and extraction_runs, neither of
-- its indexes, and the two value lists as they were.
--
-- What is lost: for every e-mail, where its text is stored and how it was
-- labelled; for every document, which e-mail it came from. The stored text
-- itself stays in Storage under the tenant's prefix until the tenant is
-- deleted; nothing here touches Storage.
--
-- It deletes and rewrites no row. So it stops, and changes nothing (one
-- transaction), while any of these exist, and a person decides what happens
-- to them first:
--   - a raw_emails row with outcome 'received';
--   - an extraction_runs row with run_kind 'mail_label';
--   - an extraction_runs row with no document_id.
--
-- Run it only with the code from before 0041 deployed: the code that comes
-- with 0041 writes these columns.
--
-- Tested in CI (scripts/ci/migration_0041_roundtrip.py): after the reverse
-- everything 0041 added is gone and nothing else on the four tables differs;
-- after forward again the state is the first.

drop index public.idx_extraction_runs_raw_email;
alter table public.extraction_runs drop constraint extraction_runs_run_kind_check;
alter table public.extraction_runs
    add constraint extraction_runs_run_kind_check check (run_kind in ('extraction', 'buyer_routing'));
alter table public.extraction_runs drop constraint extraction_runs_document_or_email;
alter table public.extraction_runs drop column raw_email_id;
alter table public.extraction_runs alter column document_id set not null;

alter table public.intake_rejections drop column mail_label;
alter table public.intake_rejections drop column raw_email_id;

drop index public.idx_documents_raw_email;
alter table public.documents drop column email_part;
alter table public.documents drop column raw_email_id;

alter table public.raw_emails drop constraint raw_emails_outcome_check;
alter table public.raw_emails
    add constraint raw_emails_outcome_check check (outcome in ('processed', 'quarantined', 'rejected'));
alter table public.raw_emails drop constraint raw_emails_body_stored_together;
alter table public.raw_emails
    drop column date_header,
    drop column body_document_id,
    drop column body_labelled_at,
    drop column body_injection_suspected,
    drop column body_order_lines_confidence,
    drop column body_contains_order_lines,
    drop column body_label_confidence,
    drop column body_label,
    drop column body_state,
    drop column body_text_path,
    drop column body_html_path,
    drop column body_bytes,
    drop column body_sha256,
    drop column body_kind,
    drop column body_storage_path;
