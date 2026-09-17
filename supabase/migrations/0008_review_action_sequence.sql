-- DocFlow — Phase 3 (a total order for the audit trail)
--
-- New column: review_actions.sequence.
--
-- 0007 ordered the trail by `(created_at, id)`. That is wrong, and it was
-- wrong in a way that passed its own test by luck.
--
-- Postgres `now()` is TRANSACTION start time, not statement time. An edit to
-- an already-approved document writes two rows in one transaction -- the
-- `edited` action and the `reopened` action it causes (Section 7.3's "if
-- someone edits after approval, the document reverts to needs_review") --
-- and both get the identical `created_at`. The tiebreaker was `id`, which is
-- `gen_random_uuid()`, so the trail could render the reopen BEFORE the edit
-- that caused it, at random, on a document-by-document basis.
--
-- The Phase 3 exit criterion is "the audit trail shows exactly what changed".
-- A trail that reorders cause and effect does not meet it, and an audit trail
-- that is only usually in the right order is worse than one that is obviously
-- broken, because nobody goes looking.
--
-- `clock_timestamp()` was the cheaper fix and is rejected deliberately: it
-- would make `created_at` disagree with every other table's transaction
-- timestamp, and it still ties at microsecond resolution. A monotonic
-- sequence is what "these happened in this order" actually means.
-- Recorded as DECISIONS.md D-084.

alter table review_actions
    add column sequence bigserial;

-- Existing rows are backfilled by the bigserial default in id order, which is
-- arbitrary for rows that already tied -- there is no information left to
-- recover their true order from. This only affects rows written before this
-- migration, which at the time of writing are test fixtures on staging and
-- nothing else.

-- The trail read: one document's actions, in the order they happened.
create unique index idx_review_actions_sequence
    on review_actions(document_id, sequence);
