-- DocFlow — Phase 2 (validation warnings + duplicate/change-order slice)
--
-- New table: document_warnings. New column: documents.change_order_of_document_id.
--
-- This migration adds the place CLAUDE.md Section 7.7's warnings live, and
-- the one relationship column Section 7.8 needs that 0004 did not create.
-- It adds NOTHING that can hold a corrected value: there is no "suggested
-- total", no "reconciled amount", no "normalized date" column anywhere
-- below. Section 10 forbids altering a model-extracted value to make
-- validation pass, and the cheapest way to keep that true forever is for the
-- schema to have nowhere to put such a value.
--
-- See supabase/migrations/0001_foundations.sql for the RLS pattern this
-- migration follows verbatim: every tenant-scoped table gets
-- `tenant_isolation` + `platform_admin_access` from the statement that
-- creates it, never added later (CLAUDE.md Section 7.5).


-- ── document_warnings (tenant-scoped) ────────────────────────────────────
-- One row per thing a reviewer needs to look at.
--
-- Rows, not a JSON blob on `documents`, because Section 7.3 requires that
-- "any unresolved warning at approval time must be explicitly acknowledged;
-- the acknowledgement is recorded in `review_actions` with the warning
-- text" — an acknowledgement has to address one warning, so a warning has to
-- be individually addressable.
--
-- `tenant_id` is denormalized from `documents` for the same reason
-- `document_headers` denormalizes it (0002): RLS is enforced on this table
-- directly, not through a join.
create table document_warnings (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         uuid not null references tenants(id) on delete cascade,
    document_id       uuid not null references documents(id) on delete cascade,

    -- NULL for a document-level or header-level warning; set for a warning
    -- about one line, so the review UI can anchor it to the right row.
    document_line_id  uuid references document_lines(id) on delete cascade,

    -- The error-catalog code (docflow_core.errors, VAL-0xx). The catalog
    -- holds the what / why / what-next prose; this column holds only the
    -- stable key, so a wording change is a code change reviewed as a diff
    -- rather than an UPDATE across customer data (Section 7.16.5).
    code              text not null,

    -- The severity of THIS occurrence. Usually the catalog entry's default,
    -- but a money discrepancy is escalated by magnitude — a total that is out
    -- by $4,000 and a missing payment term must not look the same to a
    -- reviewer (docflow_core.validation.money_severity).
    severity          text not null check (severity in ('info','warning','high','critical')),

    -- Which extracted field the warning is about ('order_total', 'quantity',
    -- 'sku_or_description', ...), or NULL for a whole-document warning.
    -- Named `field_name` rather than `field` to match the application
    -- dataclass, which cannot use `field` (dataclasses owns that name).
    field_name        text,
    line_number       integer,

    -- This occurrence's specifics: the two numbers that disagreed, the
    -- tolerance that was applied, the confidence and the threshold. Every
    -- number is a STRING here, never a JSON number — the same discipline
    -- money gets everywhere else (Section 7.1), so a reviewer's screen and an
    -- export can never disagree about a value through float round-tripping.
    detail            jsonb not null default '{}'::jsonb,

    -- The idempotency key: a hash over (code, field, line, detail), computed
    -- in docflow_core.validation.DocumentWarning.fingerprint. Re-validating
    -- an unchanged document produces the same fingerprints, so re-processing
    -- never duplicates a warning and never discards an acknowledgement.
    -- It deliberately covers the compared VALUES, so an acknowledgement of
    -- "out by two cents" cannot silently carry over to "out by $4,000".
    fingerprint       text not null,

    status            text not null default 'open'
                      check (status in ('open','acknowledged','resolved')),

    -- Section 7.3's acknowledgement trail. The FK to `review_actions` is
    -- added by the migration that creates that table (Phase 3's review slice)
    -- rather than pointing at something that does not exist yet; these three
    -- columns are what an acknowledgement needs to be attributable today.
    acknowledged_by   uuid references users(id),
    acknowledged_at   timestamptz,
    acknowledgement_note text,

    -- Set when a re-validation no longer produces this warning (the value was
    -- corrected, or the rule stopped firing). The row survives — including
    -- who acknowledged it and when — it simply stops being live.
    resolved_at       timestamptz,

    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    deleted_at        timestamptz,

    -- An acknowledgement without an acknowledger would be an audit trail that
    -- proves nothing (Section 7.3: approval is never anonymous).
    constraint document_warnings_acknowledged_by_someone
        check ((status <> 'acknowledged') or (acknowledged_by is not null and acknowledged_at is not null))
);

-- The review screen's query: every live warning for one document.
create index idx_document_warnings_document
    on document_warnings(document_id, severity) where deleted_at is null;

-- The Section 7.3 approval check ("are there unresolved warnings?") and the
-- founder's per-tenant view.
create index idx_document_warnings_tenant_status
    on document_warnings(tenant_id, status) where deleted_at is null;

create index idx_document_warnings_line
    on document_warnings(document_line_id) where document_line_id is not null;

-- The idempotency guard, enforced by the database rather than by the
-- application remembering to check: one live warning per (document,
-- fingerprint). Partial on `deleted_at`, so a warning that was resolved and
-- later recurs can be raised again as a new, unacknowledged row.
create unique index idx_document_warnings_fingerprint
    on document_warnings(document_id, fingerprint) where deleted_at is null;

alter table document_warnings enable row level security;

create policy tenant_isolation on document_warnings
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on document_warnings
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── documents: the change-order relationship ─────────────────────────────
-- 0004 added `is_possible_duplicate`, `is_possible_change_order` and
-- `duplicate_of_document_id`. It did not add a pointer for the change-order
-- case, and reusing `duplicate_of_document_id` for both would leave a column
-- whose name states the opposite of what it sometimes holds — a trap for
-- every query written for the rest of the build. Two booleans, two pointers.
--
-- `on delete set null` (not cascade): losing the earlier document must never
-- delete the later one, and Section 7.8's "both exist" means neither row is
-- ever removed to tidy up a relationship.
alter table documents
    add column change_order_of_document_id uuid references documents(id) on delete set null;

-- A document can never be its own duplicate or its own revision. Cheap to
-- enforce, and it makes the "newer points at older, never the reverse"
-- direction in docflow_core.duplicates structurally impossible to break with
-- a self-link.
alter table documents
    add constraint documents_duplicate_of_is_another_document
    check (duplicate_of_document_id is distinct from id);

alter table documents
    add constraint documents_change_order_of_is_another_document
    check (change_order_of_document_id is distinct from id);

create index idx_documents_change_order_of on documents(change_order_of_document_id)
    where change_order_of_document_id is not null;


-- ── document_headers: the PO-number comparison key ───────────────────────
-- The expression below is `docflow_core.duplicates.PO_NUMBER_KEY_SQL`, which
-- is the SQL half of `normalize_po_number` — uppercase, whitespace runs
-- collapsed, trimmed. It must stay character-identical to that constant: if
-- the two drift, the change-order query silently stops using this index and
-- silently changes what "the same PO number" means.
--
-- This is an index on a DERIVED key. `document_headers.po_number` still holds
-- exactly what the document printed (Section 7.1); nothing normalizes it in
-- place.
create index idx_document_headers_po_key
    on document_headers(tenant_id, (btrim(regexp_replace(upper(po_number), '\s+', ' ', 'g'))))
    where po_number is not null and deleted_at is null;
