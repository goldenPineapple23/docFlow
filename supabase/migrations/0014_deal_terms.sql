-- DocFlow — Phase 5, between slices 5.3 and 5.4: deal terms at tenant creation
--
-- Recorded as DECISIONS.md D-117.
--
-- The price is agreed in the sales conversation, before onboarding, so it is
-- recorded when the tenant is created (Step 2) and never discussed on screen
-- at go-live. Go-live (Step 9) still does the billing; only the choosing
-- moves earlier. Editable until go-live, locked after.
--
-- New table:    setup_fee_presets (GLOBAL -- named here per Section 10).
-- New columns:  tenants.setup_fee_preset_id.
-- Reused:       tenants.setup_fee_amount / setup_fee_billing / setup_fee_note /
--               founding_price (0013) -- now set at creation, not go-live.


-- ── setup_fee_presets (GLOBAL) ──────────────────────────────────────────────
-- docflow-pricing.docx "One-time setup fee": Founding customer $750, Standard
-- $1,500, Complex $2,000-$2,500. Plus two the founder asked for (D-117):
-- Waived ($0) and Custom (any amount) -- both need a written reason, so a
-- later look at the tenant says why it paid what it paid.
--
-- Versioned like tiers (D-102): a tenant points at the exact preset version
-- it was sold on; changing a price is a new version with is_current moved,
-- and existing tenants keep theirs. No price lives in application code.
create table setup_fee_presets (
    id              uuid primary key default gen_random_uuid(),
    code            text not null check (code in ('founding', 'standard', 'complex', 'waived', 'custom')),
    version         integer not null check (version >= 1),
    name            text not null,
    description     text not null,
    -- What the form fills in. NULL for Custom: the founder types it.
    default_amount  numeric(10,2) check (default_amount >= 0),
    -- The range the amount must fall in. A fixed preset has min = max.
    -- max NULL = no upper bound (Custom).
    min_amount      numeric(10,2) not null check (min_amount >= 0),
    max_amount      numeric(10,2) check (max_amount >= min_amount),
    note_required   boolean not null default false,
    sort_order      integer not null,
    is_current      boolean not null default true,
    created_at      timestamptz not null default now(),
    unique (code, version),
    check (default_amount is null or (default_amount >= min_amount
                                      and (max_amount is null or default_amount <= max_amount)))
);
create unique index idx_setup_fee_presets_one_current on setup_fee_presets(code) where is_current;

alter table setup_fee_presets enable row level security;
-- Read in the tenant's own session at go-live, like tiers.
create policy read_all on setup_fee_presets for select using (true);
create policy platform_admin_write on setup_fee_presets
    using (current_setting('app.is_platform_admin', true) = 'true');

-- Version 1, exactly as docflow-pricing.docx (Version 1, September 2026).
-- Real configuration, not test data.
insert into setup_fee_presets
    (code, version, name, description, default_amount, min_amount, max_amount, note_required, sort_order)
values
    ('founding', 1, 'Founding customer', 'Early-adopter discount, first cohort',
        750.00, 750.00, 750.00, false, 1),
    ('standard', 1, 'Standard', 'Typical new customer',
        1500.00, 1500.00, 1500.00, false, 2),
    ('complex',  1, 'Complex',
        'Large or messy catalog, multiple entities/locations, heavy export customization, '
        'significant historical cleanup, or multiple training sessions',
        2000.00, 2000.00, 2500.00, false, 3),
    ('waived',   1, 'Waived', 'No setup fee -- say why in the note',
        0.00, 0.00, 0.00, true, 4),
    ('custom',   1, 'Custom', 'Any other agreed amount -- say why in the note',
        null, 0.00, null, true, 5);


-- ── tenants: which preset the deal was made on ──────────────────────────────
-- NULL for a tenant created before this migration (or before the deal is
-- set); go-live refuses until it is set (ONB-010).
alter table tenants
    add column setup_fee_preset_id uuid references setup_fee_presets(id);
