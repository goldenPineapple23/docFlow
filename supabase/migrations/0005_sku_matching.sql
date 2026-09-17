-- DocFlow — Phase 2 (SKU matching slice)
--
-- No new tables: this slice reads `items` and `learned_rules` (both created
-- empty by 0004) and needs somewhere on `document_lines` to record what the
-- deterministic matching step concluded. `document_lines` already carries
-- `tenant_id` and both RLS policies from 0002, so adding columns to it adds
-- no new isolation surface (CLAUDE.md Section 7.5).
--
-- The shape of these columns is the Section 7.6 rule written down:
--
--   "Fuzzy matches always surface candidate + score; below threshold they
--    are suggestions, never auto-applied."
--
-- so `match_candidates` exists independently of `matched_item_id` — a line
-- can (and routinely will) have scored candidates and no match at all. That
-- is a correct, expected outcome a reviewer resolves, not a failure state.
--
-- Nothing here rewrites an extracted value. `sku`, `description` and `unit`
-- keep exactly what the model read off the document (Section 7.1); every
-- column below is a *derived conclusion about* those values, stored beside
-- them.


-- ── document_lines: the matching result ──────────────────────────────────
alter table document_lines
    -- The catalog item this line resolved to, or NULL. `on delete set null`
    -- rather than cascade: losing the catalog row must never delete a
    -- customer's order line.
    add column matched_item_id  uuid references items(id) on delete set null,

    -- How it resolved, in the Section 7.6 precedence order:
    --   learned_rule   — an active sku_mapping rule a human confirmed
    --   exact_sku      — the printed SKU equals a live catalog SKU
    --   fuzzy          — similarity at or above threshold, with the
    --                    measure guard satisfied (docflow_core.matching)
    --   human_confirmed— a reviewer picked the item themselves
    -- There is deliberately no value meaning "auto-applied below threshold",
    -- because no such path exists (Section 10).
    add column match_method     text
                                check (match_method in
                                       ('learned_rule','exact_sku','fuzzy','human_confirmed')),

    -- 0.0000-1.0000. NUMERIC, bound as a string, never a float — the same
    -- discipline money gets (Section 7.1), applied uniformly to every stored
    -- number rather than case by case.
    add column match_score      numeric(5,4)
                                check (match_score >= 0 and match_score <= 1),

    -- Section 7.6: "Never silently normalize units of measure or quantities.
    -- Suggest, flag, let the human decide." `unit` above keeps what the
    -- document printed, forever. `matched_uom` is the *suggestion* a
    -- tenant-wide `uom_alias` learned rule produced ("CS" -> "CASE"), stored
    -- in its own column so applying a rule can never be mistaken for
    -- correcting the document.
    add column matched_uom      text,

    -- True when the line's unit and the matched item's `unit_of_measure`
    -- disagree. A flag, not a correction: nothing is changed to make them
    -- agree. Warnings as first-class rows are the next slice; this is the
    -- fact that slice will read (see DECISIONS.md D-070).
    add column uom_mismatch     boolean not null default false,

    -- The top-N scored alternatives, as
    --   [{"item_id": "...", "sku": "...", "description": "...",
    --     "score": "0.9200", "matched_on": "description",
    --     "eligible": true, "blocked_reason": null}, ...]
    -- Scores are strings in JSON transport (Section 7.1). Populated whenever
    -- the fuzzy step ran, whether or not anything cleared the threshold, so
    -- the reviewer always sees what was considered and why it was not taken.
    add column match_candidates jsonb not null default '[]'::jsonb,

    add column matched_at       timestamptz;

-- A match without a method (or a method without a match) would be a row
-- nobody could interpret. Both or neither. Kept as its own statement rather
-- than another subcommand above, so it unambiguously runs after every column
-- it names exists.
alter table document_lines
    add constraint document_lines_match_method_requires_item
    check ((matched_item_id is null) = (match_method is null));

-- Supports the Section 7.15.3 "mapping reuse rate" rollup and the
-- Section 10 check that a retired SKU is not silently orphaned.
create index idx_document_lines_matched_item
    on document_lines(tenant_id, matched_item_id)
    where matched_item_id is not null;
