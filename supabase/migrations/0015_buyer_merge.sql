-- DocFlow — Phase 5, slice 5.4 (operator screens: buyer merge, learned rules)
--
-- Recorded as DECISIONS.md D-119.
--
-- New table:    buyer_merges (tenant-scoped, append-only).
-- New columns:  buyers.merged_into_buyer_id, buyers.merged_at.
--
-- Section 7.6: "Merging is a founder action that re-points foreign keys in a
-- transaction and is logged." Section 7.13: "buyer_alias (a founder merge
-- becomes a rule)". Until now near-duplicates were only flagged
-- (buyer_merge_candidates, 0004); this adds the merge itself.


-- ── buyers: where a merged buyer went ───────────────────────────────────────
-- A merged buyer is soft-deleted (deleted_at), never removed, and points at
-- the buyer it was merged into, so an old approved snapshot that names it
-- can always be traced to the live record.
alter table buyers
    add column merged_into_buyer_id uuid references buyers(id),
    add column merged_at            timestamptz,
    add constraint buyers_merged_is_deleted check (
        merged_into_buyer_id is null or deleted_at is not null
    ),
    add constraint buyers_not_merged_into_self check (merged_into_buyer_id <> id);


-- ── buyer_merges (tenant-scoped, append-only) ───────────────────────────────
-- The log of every merge: who, as whom, what moved. Enough to explain -- and
-- by hand, to reverse -- any merge. Append-only like admin_actions and
-- extraction_runs: no updated_at, no deleted_at (D-057).
create table buyer_merges (
    id                   uuid primary key default gen_random_uuid(),
    tenant_id            uuid not null references tenants(id) on delete cascade,
    kept_buyer_id        uuid not null references buyers(id),
    merged_buyer_id      uuid not null references buyers(id),
    candidate_id         uuid references buyer_merge_candidates(id),
    alias_rule_id        uuid references learned_rules(id),
    -- IDs only (Section 7.10): which rows were re-pointed.
    document_ids         jsonb not null default '[]'::jsonb,
    rule_ids             jsonb not null default '[]'::jsonb,
    -- Fields copied onto the kept buyer because it had none, e.g.
    -- ["contact_email"]. Never an overwrite.
    fields_filled        jsonb not null default '[]'::jsonb,
    merged_by            uuid not null references users(id),
    -- Set when the founder merged from the Console (Section 7.15.1).
    acting_as_tenant_id  uuid references tenants(id) on delete set null,
    created_at           timestamptz not null default now(),
    constraint buyer_merges_distinct check (kept_buyer_id <> merged_buyer_id)
);
create index idx_buyer_merges_tenant on buyer_merges(tenant_id, created_at desc);

alter table buyer_merges enable row level security;
create policy tenant_isolation on buyer_merges
    using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
create policy platform_admin_access on buyer_merges
    using (current_setting('app.is_platform_admin', true) = 'true');
