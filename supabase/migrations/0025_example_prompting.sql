-- DocFlow — Phase 5, slice 5.10: approved-example prompting (Section 7.13)
--
-- Recorded as DECISIONS.md D-141 and D-142.
--
-- Two additive columns, nothing else:
--
--   * documents.extracted_text_path -- where the text the parser sent to the
--     model is kept (tenant-prefixed storage, like previews). An example is
--     "the same extracted text the parser produced", and DocFlow never kept it
--     until now. NULL for a document the model read visually (a scan or a
--     photo): such a document can never be an example, because an example is
--     never a file or an image.
--
--   * extraction_runs.run_kind -- 'extraction' (the full read by the pinned
--     extraction model) or 'buyer_routing' (the cheap header-only read that
--     identifies the buyer before extraction so examples can be chosen). Both
--     are model calls, both count towards the daily cost circuit breaker, and
--     the table was already one row per model call (0004). Existing rows are
--     extractions by definition.
--
-- extraction_runs already has RLS (0004) and every column 7.13 needs
-- (examples_used, example_input_tokens); tenants.example_prompting_enabled has
-- existed since 0001, default false.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

alter table documents add column extracted_text_path text;

alter table extraction_runs
    add column run_kind text not null default 'extraction'
        check (run_kind in ('extraction', 'buyer_routing'));

-- No new index: choosing a buyer's recent approved orders uses
-- idx_document_headers_buyer from 0004.
