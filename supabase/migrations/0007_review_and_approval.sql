-- DocFlow — Phase 3 (human review, approval, and the immutable snapshot)
--
-- New tables: review_actions, document_snapshots.
-- New columns: documents.approved_at / approved_by / approved_snapshot_hash /
-- review_started_at, and document_warnings.acknowledged_review_action_id.
--
-- This migration builds the audit trail CLAUDE.md Section 7.3 requires:
-- "Every human edit writes a `review_actions` row with before and after
-- values", "there is no code path that sets `approved` without a user ID and
-- timestamp", and "on approval, freeze a complete snapshot ... this snapshot
-- is immutable."
--
-- Like 0006, it adds nothing that can quietly rewrite an extracted value. The
-- review path writes to document_headers / document_lines only through an
-- explicit human edit that records its own before-and-after here first; there
-- is no column below that a machine can use to "correct" anything.
--
-- See supabase/migrations/0001_foundations.sql for the RLS pattern this
-- migration follows verbatim: every tenant-scoped table gets
-- `tenant_isolation` + `platform_admin_access` from the statement that
-- creates it, never added later (CLAUDE.md Section 7.5).


-- ── review_actions (tenant-scoped) ───────────────────────────────────────
-- One row per thing a human did to a document.
--
-- docflow-database-schema.docx defines this table without a `tenant_id`.
-- That is not buildable here: Section 10 forbids adding a table without
-- `tenant_id` and RLS, and an audit trail reachable across a tenant boundary
-- would be the worst possible table to get wrong. `tenant_id` is denormalized
-- from `documents` for the same reason `document_warnings` denormalizes it
-- (0006): RLS is enforced on this table directly, not through a join.
-- Recorded as DECISIONS.md D-081.
create table review_actions (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         uuid not null references tenants(id) on delete cascade,
    document_id       uuid not null references documents(id) on delete cascade,

    -- Who did it. NOT NULL and no default: Section 7.3 says approval is never
    -- anonymous, and the cheapest way to keep that true is for the column to
    -- refuse a row that cannot name a person.
    user_id           uuid not null references users(id),

    -- Section 7.15.1: when the founder acts inside a tenant, the action runs
    -- through the normal tenant-scoped path with an explicit
    -- `acting_as_tenant_id`, and the row records BOTH the founder's user_id
    -- and this. NULL for ordinary tenant traffic, which is the common case.
    -- The founder never "becomes" a tenant user and never appears in a
    -- tenant's audit log as anyone other than themselves.
    acting_as_tenant_id uuid references tenants(id) on delete set null,

    -- `edited` is the one the KPI in 7.15.3 counts ("share of approved
    -- documents with no review_actions of type edited"), so its meaning must
    -- stay exactly "a human changed an extracted value".
    --
    -- `reopened` is the Section 7.3 revert: "if someone edits after approval,
    -- the document reverts to needs_review and must be re-approved". That
    -- transition is a thing that happened to the document and a reviewer
    -- needs to see it in the trail, so it gets its own row rather than being
    -- inferred from an `edited` row that happens to follow an `approved` one.
    action            text not null
                      check (action in ('edited','approved','rejected','reopened')),

    -- Section 9: "review_actions.changes must be structured as
    -- {field, before, after} entries". An ARRAY of them, because one save can
    -- change several fields and they belong to one human action.
    --
    -- Every value inside is a STRING, never a JSON number -- the same
    -- discipline money gets everywhere else (Section 7.1). A `before` that
    -- round-tripped through a float would make the audit trail disagree with
    -- the document it is supposed to prove something about.
    changes           jsonb not null default '[]'::jsonb,

    -- Section 9: "plus a warning_acknowledgements array on approval", and
    -- Section 7.3: "any unresolved warning at approval time must be
    -- explicitly acknowledged; the acknowledgement is recorded in
    -- review_actions WITH THE WARNING TEXT". The text is copied in, not
    -- referenced -- if the catalog wording changes next year, this row must
    -- still say what the human actually agreed to at the time.
    warning_acknowledgements jsonb not null default '[]'::jsonb,

    -- A rejection reason, or a note the reviewer left. Free text from a
    -- trusted tenant user, never from a document.
    note              text,

    created_at        timestamptz not null default now(),
    deleted_at        timestamptz,

    -- An edit that records no changes proves nothing, and an approval that
    -- carries a changes array is two different actions wearing one row.
    constraint review_actions_edited_has_changes
        check (action <> 'edited' or jsonb_array_length(changes) > 0)
);

-- The review screen's own query: this document's trail, newest last.
create index idx_review_actions_document
    on review_actions(document_id, created_at) where deleted_at is null;

-- 7.15.3's zero-edit-approvals and human-correction-rate KPIs, per tenant
-- over a date range.
create index idx_review_actions_tenant_action
    on review_actions(tenant_id, action, created_at) where deleted_at is null;

-- 7.15.1: "a Console review action on a Tenant B document appears in Tenant
-- B's audit log attributed to the founder with acting_as_tenant_id set." The
-- tenant surface labels these rows "DocFlow support", so it filters on this.
create index idx_review_actions_acting_as
    on review_actions(acting_as_tenant_id) where acting_as_tenant_id is not null;

alter table review_actions enable row level security;

create policy tenant_isolation on review_actions
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on review_actions
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── document_snapshots (tenant-scoped, append-only) ──────────────────────
-- Section 7.3 asks for two things that sound contradictory until you give
-- snapshots their own table:
--
--   "On approval, freeze a complete snapshot ... This snapshot is immutable."
--   "If someone edits after approval, the document reverts to needs_review
--    and must be re-approved -- THE OLD SNAPSHOT IS RETAINED."
--
-- A single `documents.approved_json` column cannot do both: re-approval would
-- have to overwrite it, and the old snapshot would be gone. So every approval
-- appends a row here, forever, and `documents.approved_json` holds a copy of
-- the current one for the readers that only ever want the latest (Section
-- 7.13's example prompting reads exactly that).
--
-- Nothing in the application updates a row in this table. Recorded as D-082.
create table document_snapshots (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         uuid not null references tenants(id) on delete cascade,
    document_id       uuid not null references documents(id) on delete cascade,

    -- The approval that produced it. Gives the snapshot its actor and its
    -- timestamp without duplicating them.
    review_action_id  uuid not null references review_actions(id),

    -- The complete approved header and lines, exactly as the reviewer left
    -- them. Every number a string (Section 7.1).
    snapshot          jsonb not null,

    -- SHA-256 over a canonical rendering of `snapshot`. Section 7.4 requires
    -- every `exports` row to record "the snapshot hash it was built from",
    -- and the mandatory round-trip test compares a parsed export against the
    -- snapshot. This is the value both sides name.
    snapshot_sha256   text not null,

    -- Set when a LATER approval supersedes this one. The row itself is never
    -- touched otherwise and never deleted -- an export generated last March
    -- must still be explicable in terms of the snapshot it came from.
    superseded_at     timestamptz,

    created_at        timestamptz not null default now(),
    deleted_at        timestamptz
);

-- "The current snapshot for this document" and "every snapshot, in order".
create index idx_document_snapshots_document
    on document_snapshots(document_id, created_at desc) where deleted_at is null;

-- At most one live snapshot per document. A second approval must supersede
-- the first in the same transaction, enforced here rather than by the
-- application remembering to.
create unique index idx_document_snapshots_current
    on document_snapshots(document_id)
    where superseded_at is null and deleted_at is null;

-- Exports look a snapshot up by hash (7.4).
create index idx_document_snapshots_hash on document_snapshots(tenant_id, snapshot_sha256);

alter table document_snapshots enable row level security;

create policy tenant_isolation on document_snapshots
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on document_snapshots
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── documents: the approval columns ──────────────────────────────────────
-- 0002 created `approved_json` and nothing to say who approved it or when.
-- Section 7.3 requires both, and 7.15.3 computes two of its KPIs from them
-- ("review time <= 2 min" and "receipt -> export-ready").
alter table documents
    add column approved_at             timestamptz,
    add column approved_by             uuid references users(id),
    add column approved_snapshot_hash  text,

    -- Set when a reviewer FIRST opens the document, never updated afterwards.
    -- 7.15.3 measures review time as `approved_at - review_started_at`, so a
    -- second opening must not reset the clock and make a slow review look
    -- fast. Nothing reads this until Phase 5; it has to be correct from the
    -- first review or the KPI is unrecoverable for every document reviewed
    -- before someone notices.
    add column review_started_at       timestamptz;

-- Section 7.3: "there is no code path that sets `approved` without a user ID
-- and timestamp." The application enforces it, and so does this -- a status
-- of `approved` or `exported` without an actor and a time is rejected by the
-- database no matter which code path tried.
alter table documents
    add constraint documents_approved_is_attributable
    check (
        status not in ('approved','exported')
        or (approved_by is not null and approved_at is not null
            and approved_json is not null and approved_snapshot_hash is not null)
    );

-- The 7.15.3 KPI queries: approvals per tenant over a window.
create index idx_documents_approved on documents(tenant_id, approved_at)
    where approved_at is not null and deleted_at is null;


-- ── document_warnings: the acknowledgement link 0006 deferred ────────────
-- 0006 said: "The FK to `review_actions` is added by the migration that
-- creates that table (Phase 3's review slice) rather than pointing at
-- something that does not exist yet." This is that migration.
--
-- Which approval acknowledged this warning. The warning text itself lives in
-- that row's `warning_acknowledgements` array (see above); this is the join
-- back, so the review screen can show "acknowledged during the approval on
-- the 4th" rather than just "acknowledged".
alter table document_warnings
    add column acknowledged_review_action_id uuid references review_actions(id);
