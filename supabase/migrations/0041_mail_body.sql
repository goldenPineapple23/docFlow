-- DocFlow — an e-mail's own text (Phase 5.5, B1's demo half; DECISIONS.md
-- D-230; docs/designs/b1.md section 4).
--
-- What it changes. Additive: no new table, no policy, no grant, and no
-- existing value is changed. The four tables already have row-level security
-- (0002, 0003, 0004), and their policies cover the new columns.
--
--   1. raw_emails: where the e-mail's text is kept and what was decided
--      about it.
--      - body_storage_path, body_kind ('text' | 'html'), body_sha256,
--        body_bytes: the part DocFlow reads, stored as the sender wrote it.
--        All four are set together or not at all.
--      - body_html_path: the HTML part, when the e-mail carried a text part
--        as well, so the raw e-mail is complete (Section 7.2).
--      - body_text_path: the text the model read and the reviewer is shown.
--      - body_state: 'none' (nothing waits for a label; every existing row),
--        'pending', 'labelled', 'held', 'released'.
--      - body_label (six fixed words), body_label_confidence,
--        body_contains_order_lines, body_order_lines_confidence,
--        body_injection_suspected, body_labelled_at: the labelling call's
--        answer.
--      - body_document_id: the document made from the text, if one was.
--      - date_header: the e-mail's Date line as the sender's system wrote
--        it. Untrusted text.
--      - outcome gains 'received': an e-mail with text and no attachment,
--        waiting for its label.
--
--   2. documents: raw_email_id (the e-mail a document came from) with an
--      index, and email_part ('attachment' | 'body'). Both NULL for an
--      upload and for every existing row.
--
--   3. intake_rejections: raw_email_id and mail_label (the same six words).
--
--   4. extraction_runs: a labelling call is recorded here before there is a
--      document, so document_id may be NULL, raw_email_id is added, a check
--      holds that one of the two is set, and run_kind gains 'mail_label'.
--      A run that belongs to an e-mail goes when the e-mail's row goes
--      (on delete cascade), as a run that belongs to a document always has.
--
-- No backfill: the text of e-mails received before this was never kept.
-- The new columns with a default take a constant, which Postgres records
-- without rewriting the table.
--
-- Reverse: supabase/reverse/0041_reverse.sql, tested in CI forward ->
-- reverse -> forward (scripts/ci/migration_0041_roundtrip.py).
--
-- Deploy order: this migration first, then the code. Code from before it
-- reads and writes none of these columns and none of the new values.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor. It runs
-- as one transaction.

alter table public.raw_emails
    add column body_storage_path text,
    add column body_kind text
        constraint raw_emails_body_kind_check check (body_kind in ('text', 'html')),
    add column body_sha256 text,
    add column body_bytes integer
        constraint raw_emails_body_bytes_check check (body_bytes is null or body_bytes >= 0),
    add column body_html_path text,
    add column body_text_path text,
    add column body_state text not null default 'none'
        constraint raw_emails_body_state_check
        check (body_state in ('none', 'pending', 'labelled', 'held', 'released')),
    add column body_label text
        constraint raw_emails_body_label_check
        check (body_label in ('order', 'change', 'cancellation', 'quote_request',
                              'status_question', 'not_an_order')),
    add column body_label_confidence numeric(4,3)
        constraint raw_emails_body_label_confidence_check
        check (body_label_confidence is null or body_label_confidence between 0 and 1),
    add column body_contains_order_lines boolean,
    add column body_order_lines_confidence numeric(4,3)
        constraint raw_emails_body_order_lines_confidence_check
        check (body_order_lines_confidence is null or body_order_lines_confidence between 0 and 1),
    add column body_injection_suspected boolean,
    add column body_labelled_at timestamptz,
    add column body_document_id uuid
        constraint raw_emails_body_document_id_fkey references public.documents(id) on delete set null,
    add column date_header text,
    add constraint raw_emails_body_stored_together check (
        (body_storage_path is null) = (body_kind is null)
        and (body_storage_path is null) = (body_sha256 is null)
        and (body_storage_path is null) = (body_bytes is null)
    );

alter table public.raw_emails drop constraint raw_emails_outcome_check;
alter table public.raw_emails
    add constraint raw_emails_outcome_check
    check (outcome in ('processed', 'quarantined', 'rejected', 'received'));

alter table public.documents
    add column raw_email_id uuid
        constraint documents_raw_email_id_fkey references public.raw_emails(id) on delete set null,
    add column email_part text
        constraint documents_email_part_check check (email_part in ('attachment', 'body'));

create index idx_documents_raw_email on public.documents(raw_email_id) where raw_email_id is not null;

alter table public.intake_rejections
    add column raw_email_id uuid
        constraint intake_rejections_raw_email_id_fkey references public.raw_emails(id) on delete set null,
    add column mail_label text
        constraint intake_rejections_mail_label_check
        check (mail_label in ('order', 'change', 'cancellation', 'quote_request',
                              'status_question', 'not_an_order'));

alter table public.extraction_runs alter column document_id drop not null;
alter table public.extraction_runs
    add column raw_email_id uuid
        constraint extraction_runs_raw_email_id_fkey references public.raw_emails(id) on delete cascade,
    add constraint extraction_runs_document_or_email check (document_id is not null or raw_email_id is not null);

alter table public.extraction_runs drop constraint extraction_runs_run_kind_check;
alter table public.extraction_runs
    add constraint extraction_runs_run_kind_check
    check (run_kind in ('extraction', 'buyer_routing', 'mail_label'));

create index idx_extraction_runs_raw_email
    on public.extraction_runs(raw_email_id) where raw_email_id is not null;
