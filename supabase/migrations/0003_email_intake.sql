-- DocFlow — Phase 1 (email intake slice)
--
-- Adds: quarantine columns + email provenance columns on `documents`,
-- email-specific columns on `intake_rejections`, and the new `raw_emails`
-- forensic table (CLAUDE.md Section 7.2: "store the raw email, headers
-- included, for forensics"). Also adds a `token_lookup` RLS policy to the
-- existing `intake_addresses` table (0001) so the email-intake webhook can
-- resolve a tenant from its URL token before any tenant_id is known.
--
-- See supabase/migrations/0001_foundations.sql for the RLS pattern this
-- migration follows verbatim, and DECISIONS.md for the judgment calls made
-- while writing this migration (message_id uniqueness scope, the new
-- token_lookup policy, etc).

-- ── documents: email provenance + quarantine (CLAUDE.md Section 7.16.4) ───
alter table documents
    add column sender_email      text,
    add column message_id        text,
    add column quarantine_reason text
        check (quarantine_reason in
               ('abuse_ceiling','cost_breaker','attachment_cap','auth_fail',
                'unknown_sender_velocity','manual')),
    add column quarantined_at    timestamptz;

-- Needed by the "known sender" / unknown-sender-velocity queries
-- (docflow_core.email_intake), which filter documents by tenant + sender.
create index idx_documents_tenant_sender on documents(tenant_id, sender_email);

-- Plain (non-unique) index: a NULL message_id must remain valid for
-- non-email documents, and more than one document (attachment) legitimately
-- shares one message_id when an email has multiple attachments. Real
-- one-row-per-email dedupe is enforced on raw_emails below, which is the
-- correct place for it (CLAUDE.md Section 7.8) -- see DECISIONS.md.
create index idx_documents_tenant_message on documents(tenant_id, message_id);


-- ── intake_rejections: email-specific columns ──────────────────────────────
alter table intake_rejections
    add column sender_email text,
    add column subject      text,
    add column message_id   text;


-- ── raw_emails (tenant-scoped) ─────────────────────────────────────────────
-- One row per inbound email regardless of outcome -- the forensic record
-- CLAUDE.md Section 7.2 requires. `outcome` records the final overall
-- disposition of the email's attachments (see DECISIONS.md for the
-- tri-state's exact semantics in mixed-outcome cases).
create table raw_emails (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        uuid not null references tenants(id) on delete cascade,
    message_id       text,
    sender_email     text,
    sender_domain    text,
    subject          text,
    spf_result       text,
    dkim_result      text,
    dmarc_result     text,
    raw_headers      jsonb,
    attachment_count integer not null default 0,
    outcome          text not null check (outcome in ('processed','quarantined','rejected')),
    created_at       timestamptz not null default now()
);
create index idx_raw_emails_tenant_created on raw_emails(tenant_id, created_at desc);

-- Message-ID dedupe lookup path (CLAUDE.md Section 7.8): a mail provider's
-- retried webhook delivery for the same (tenant_id, message_id) must be
-- detected cheaply and reliably. Unique (unlike documents.message_id above)
-- because raw_emails really is one row per inbound email by design -- see
-- DECISIONS.md.
create unique index idx_raw_emails_tenant_message_unique
    on raw_emails(tenant_id, message_id) where message_id is not null;

alter table raw_emails enable row level security;

create policy tenant_isolation on raw_emails
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on raw_emails
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── intake_addresses: token-based lookup path (pre-tenant-context) ────────
-- The email-intake webhook (POST /intake/email/{token}) must resolve a
-- tenant from the URL's token before any tenant_id is known, so it can use
-- neither tenant_session() (needs tenant_id already) nor platform_session()
-- (CLAUDE.md Section 7.15.1 restricts that to admin_data_access.py and the
-- rollup job only -- this webhook is neither). This mirrors the existing
-- `self_lookup` policy on `users` (0001), scoped to a session-local token
-- instead of an auth id. See docflow_core.db.token_lookup_session.
create policy token_lookup on intake_addresses
    using (token = nullif(current_setting('app.intake_token', true), ''));
