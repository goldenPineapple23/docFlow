-- DocFlow — Phase 2 (matching foundations slice)
--
-- New tables: buyers, buyer_merge_candidates, items, learned_rules,
-- extraction_runs. New columns: the three duplicate/change-order columns on
-- `documents`, a nullable buyer FK plus per-field provenance on
-- `document_headers`, per-field provenance on `document_lines`, and the
-- `deleted_at` soft-delete column the Section 9 schema requires on every
-- business table (Phase 1's tables predate that instruction -- see
-- DECISIONS.md D-057).
--
-- Naming: the buyer table is `buyers`, not `customers` (DECISIONS.md D-051).
-- Section 9's `learned_rules.customer_id` is therefore `buyer_id` here.
--
-- See supabase/migrations/0001_foundations.sql for the RLS pattern this
-- migration follows verbatim: every tenant-scoped table gets
-- `tenant_isolation` + `platform_admin_access` from the statement that
-- creates it, never added later (CLAUDE.md Section 7.5).
--
-- Scope note (CLAUDE.md Section 0 rule 3 / Section 10): this migration
-- creates the tables the next two slices need, but only `buyers` and
-- `buyer_merge_candidates` are written by code in this slice. `items`,
-- `learned_rules` and `extraction_runs` are created here so the schema is
-- reviewed and applied in one pass; nothing writes to them yet.


-- ── buyers (tenant-scoped) ───────────────────────────────────────────────
-- The tenant's customers: the businesses that send purchase orders to a
-- DocFlow tenant. Created automatically the first time a new buyer name
-- appears on a processed PO (CLAUDE.md Section 3, 7.6); an upfront customer
-- list upload is supported but never required.
--
-- `normalized_name` is the deterministic match key computed by
-- docflow_core.buyers.normalize_buyer_name (lowercase, punctuation to
-- spaces, whitespace collapsed). It deliberately does NOT strip legal
-- suffixes: "Acme Test Inc" and "Acme Test LLC" must stay two rows that get
-- flagged as a merge candidate, never one row silently merged
-- (Section 7.6 / Section 10: "never auto-merge buyers").
create table buyers (
    id                      uuid primary key default gen_random_uuid(),
    tenant_id               uuid not null references tenants(id) on delete cascade,
    -- Exactly as extracted or as the founder imported it -- never rewritten
    -- by normalization (Section 7.1: the stored value is what the document
    -- said; normalization is a derived key, not a correction).
    name                    text not null,
    normalized_name         text not null,
    external_account_number text,
    contact_email           text,
    -- The document whose extraction first created this buyer. NULL for a
    -- buyer imported from a customer-list upload (Phase 5, Step 5).
    created_from_document_id uuid references documents(id) on delete set null,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),
    deleted_at              timestamptz
);
create index idx_buyers_tenant on buyers(tenant_id);
create index idx_buyers_tenant_email on buyers(tenant_id, contact_email) where contact_email is not null;
-- One live buyer row per normalized name per tenant. Also the concurrency
-- guard: two documents from the same new buyer processed at the same moment
-- cannot create two rows (docflow_core.buyers uses ON CONFLICT DO NOTHING
-- against this index and re-reads the winner).
create unique index idx_buyers_tenant_normalized_name
    on buyers(tenant_id, normalized_name) where deleted_at is null;

alter table buyers enable row level security;

create policy tenant_isolation on buyers
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on buyers
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── buyer_merge_candidates (tenant-scoped) ───────────────────────────────
-- CLAUDE.md Section 7.6: "on a new buyer name, create the record and flag
-- any near-duplicate existing names for founder merge. Never auto-merge."
-- This table is that flag, and nothing more: both buyer rows stay fully
-- independent and usable while a candidate is open. The merge action itself
-- (re-pointing foreign keys in a transaction, logged) is Phase 5 Console
-- work and is not built here.
--
-- `buyer_id` is always the newly created buyer, `existing_buyer_id` the
-- older one it resembled, so the pair has a deterministic order and the
-- unique index below actually prevents re-flagging.
create table buyer_merge_candidates (
    id                    uuid primary key default gen_random_uuid(),
    tenant_id             uuid not null references tenants(id) on delete cascade,
    buyer_id              uuid not null references buyers(id) on delete cascade,
    existing_buyer_id     uuid not null references buyers(id) on delete cascade,
    -- 0.0000-1.0000. NUMERIC, not a float: this score is written to the
    -- database and shown to the founder, and CLAUDE.md Section 7.1's "no
    -- float ever touches a stored number" discipline is applied uniformly
    -- rather than case by case.
    similarity_score      numeric(5,4) not null check (similarity_score >= 0 and similarity_score <= 1),
    status                text not null default 'open'
                          check (status in ('open','dismissed','merged')),
    detected_from_document_id uuid references documents(id) on delete set null,
    resolved_by           uuid references users(id),
    resolved_at           timestamptz,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),
    deleted_at            timestamptz,
    constraint buyer_merge_candidates_distinct_pair check (buyer_id <> existing_buyer_id)
);
create index idx_buyer_merge_candidates_tenant_status
    on buyer_merge_candidates(tenant_id, status, created_at desc);
create unique index idx_buyer_merge_candidates_pair
    on buyer_merge_candidates(tenant_id, buyer_id, existing_buyer_id) where deleted_at is null;

alter table buyer_merge_candidates enable row level security;

create policy tenant_isolation on buyer_merge_candidates
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on buyer_merge_candidates
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── items (tenant-scoped) ────────────────────────────────────────────────
-- The tenant's catalog. Required for every tenant (CLAUDE.md Section 3:
-- "there is no mode without a catalog"). Matching runs against it in the
-- next slice; nothing writes here yet.
--
-- `deleted_at` is also the retirement marker the catalog re-upload diff
-- uses: "a SKU present before and absent now is retired (soft-deleted),
-- never hard-deleted" (Section 7.15.2 Step 4). The catalog_imports
-- bookkeeping that references it is Phase 5 and is not created here.
create table items (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    sku             text not null,
    description     text,
    unit_of_measure text default 'EA',
    barcode         text,
    external_id     text,
    -- The source catalog row as uploaded, for columns DocFlow doesn't model.
    raw_data        jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    deleted_at      timestamptz
);
create index idx_items_tenant on items(tenant_id);
create index idx_items_tenant_sku on items(tenant_id, sku);
-- Partial, so a retired SKU that a later catalog upload reinstates doesn't
-- collide with its own soft-deleted predecessor.
create unique index idx_items_tenant_sku_unique on items(tenant_id, sku) where deleted_at is null;

alter table items enable row level security;

create policy tenant_isolation on items
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on items
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── learned_rules (tenant-scoped) ────────────────────────────────────────
-- The generalized learned-rule family from CLAUDE.md Section 7.13, which
-- replaces the single-purpose `customer_item_mappings` concept outright
-- (DECISIONS.md D-006, D-052 -- that table was never created, so there is
-- nothing to migrate or to keep as a view).
--
-- Every rule is tenant-scoped, optionally buyer-scoped (`buyer_id IS NULL`
-- means tenant-wide), created only from a human action (`confirmed_by`),
-- and soft-deletable. Nothing writes here yet; `status = 'proposed'` exists
-- as the Section 7.13 hook for rule proposals, which are explicitly NOT
-- built in the MVP.
create table learned_rules (
    id                 uuid primary key default gen_random_uuid(),
    tenant_id          uuid not null references tenants(id) on delete cascade,
    buyer_id           uuid references buyers(id) on delete cascade,
    rule_type          text not null
                       check (rule_type in ('sku_mapping','buyer_alias','uom_alias','field_hint')),
    match_key          text not null,
    match_value        jsonb not null,
    status             text not null default 'active'
                       check (status in ('proposed','active','disabled')),
    confirmed_by       uuid references users(id),
    source_document_id uuid references documents(id) on delete set null,
    times_applied      integer not null default 0,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    deleted_at         timestamptz
);
create index idx_learned_rules_lookup on learned_rules(tenant_id, rule_type, match_key);
create index idx_learned_rules_buyer on learned_rules(tenant_id, buyer_id);
-- Section 9 specifies UNIQUE (tenant_id, customer_id, rule_type, match_key).
-- Postgres treats NULLs as distinct in a unique index, so a single index
-- would let unlimited duplicate tenant-wide rules exist. Two partial
-- indexes instead -- one for buyer-scoped rules, one for tenant-wide ones --
-- which is the same constraint actually enforced (see DECISIONS.md D-053).
create unique index idx_learned_rules_unique_buyer_scoped
    on learned_rules(tenant_id, buyer_id, rule_type, match_key)
    where buyer_id is not null and deleted_at is null;
create unique index idx_learned_rules_unique_tenant_wide
    on learned_rules(tenant_id, rule_type, match_key)
    where buyer_id is null and deleted_at is null;

alter table learned_rules enable row level security;

create policy tenant_isolation on learned_rules
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on learned_rules
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── extraction_runs (tenant-scoped, append-only) ─────────────────────────
-- One row per model call (Section 9). A document can have several runs --
-- a retry after an API error, or the Section 7.13 second pass with
-- approved-example prompting once the buyer is known -- and
-- `documents.current_extraction_run_id` points at the run the working copy
-- came from.
--
-- `tenant_id` is denormalized from `documents` for the same reason
-- `document_headers` denormalizes it (0002): RLS is enforced on this table
-- directly, not through a join.
--
-- Append-only, so it has no `updated_at` and no `deleted_at` -- the same
-- treatment `admin_actions` gets in 0001 (DECISIONS.md D-057).
create table extraction_runs (
    id                   uuid primary key default gen_random_uuid(),
    tenant_id            uuid not null references tenants(id) on delete cascade,
    document_id          uuid not null references documents(id) on delete cascade,
    model_id             text,
    prompt_hash          text,
    schema_version       text,
    raw_response         jsonb,
    input_tokens         integer,
    output_tokens        integer,
    -- The approved-example prompt overhead, logged separately so the founder
    -- can see exactly what example prompting costs per tenant (Section 7.13).
    example_input_tokens integer,
    est_cost_usd         numeric(10,4),
    latency_ms           integer,
    succeeded            boolean not null,
    error_code           text,
    -- Provenance for Section 7.13: the approved-document IDs used as prompt
    -- examples, and the learned-rule IDs that fired on this run.
    examples_used        jsonb not null default '[]'::jsonb,
    rules_applied        jsonb not null default '[]'::jsonb,
    created_at           timestamptz not null default now()
);
create index idx_extraction_runs_document on extraction_runs(document_id, created_at desc);
create index idx_extraction_runs_tenant on extraction_runs(tenant_id, created_at desc);

alter table extraction_runs enable row level security;

create policy tenant_isolation on extraction_runs
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

create policy platform_admin_access on extraction_runs
    using (current_setting('app.is_platform_admin', true) = 'true');


-- ── documents: duplicate / change-order columns (Section 9) ──────────────
-- Reconstructed here as the schema doc specifies them. Nothing sets them in
-- this slice: duplicate and change-order DETECTION is the slice after next.
-- Section 7.8's rule is already encoded in the column shape -- the earlier
-- document is referenced, never deleted or overwritten.
alter table documents
    add column is_possible_duplicate     boolean not null default false,
    add column is_possible_change_order  boolean not null default false,
    add column duplicate_of_document_id  uuid references documents(id),
    add column current_extraction_run_id uuid references extraction_runs(id),
    add column deleted_at                timestamptz;

create index idx_documents_duplicate_of on documents(duplicate_of_document_id)
    where duplicate_of_document_id is not null;


-- ── document_headers: buyer link + per-field provenance ──────────────────
-- `buyer_id` is nullable and stays NULL when extraction returned a null
-- buyer_name -- that is a legitimate outcome, not an error (every field in
-- the extraction schema is nullable, Section 7.1).
--
-- `field_provenance` is the Section 9 per-field provenance map:
--   {"po_number": "extracted", "sku": "learned_rule:<uuid>",
--    "unit_price": "human_edit:<review_action_id>"}
-- so a reviewer can always see why a value is there.
alter table document_headers
    add column buyer_id         uuid references buyers(id) on delete set null,
    add column field_provenance jsonb not null default '{}'::jsonb,
    add column deleted_at       timestamptz;

create index idx_document_headers_buyer on document_headers(tenant_id, buyer_id)
    where buyer_id is not null;

alter table document_lines
    add column field_provenance jsonb not null default '{}'::jsonb,
    add column deleted_at       timestamptz;


-- ── Section 9's "add deleted_at to every business table" ──────────────────
-- Phase 1's tables were created before that instruction was in scope. The
-- column is added here for completeness; nothing sets it yet, and no query
-- filters on it yet, so behavior is unchanged until a soft-delete path
-- exists to write it (DECISIONS.md D-057).
alter table intake_rejections add column deleted_at timestamptz;
alter table raw_emails        add column deleted_at timestamptz;
alter table intake_addresses  add column deleted_at timestamptz;
