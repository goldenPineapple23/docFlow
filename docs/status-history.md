# DocFlow build status: earlier status paragraphs

Each entry is the status paragraph `docs/BUILD-STATUS.md` carried until the date shown, word
for word, newest first (founder, 2026-10-05; D-191). The current status is in BUILD-STATUS
itself. This file is not read at the start of a session; it is here so nothing is lost.

## Replaced on 2026-10-18 (Q14 merged as PR #67; PR 1b-ii built; D-206 item 11, D-207)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-16, with the Stripe test's fix merged and Q14 built:** Phase 5.5, **Stage 4**
(the audit of the broad `except` blocks). **PR 1a, PR 1b-i and the Stripe test's fix are merged** (PRs
#64, #65 and #66; `main` is `37c914c`; D-203 item 14, D-204 item 10, D-205 item 3). **Q14, the database
retry, is built and not merged** (branch `phase55/stage4-q14-database-retry`; D-206): a step of the
worker that loses the database (the exact classes D-206 names) waits it out as `wait_cause = 'database'`
instead of failing, retried on the model provider's steps for up to 6 hours, then DOC-021 and its
alert; a lost save re-runs the extraction at most once, counted from the cost record so the limit holds
through the stuck sweep; other database errors fail as before, keyed by their SQLSTATE, and the UTC
date is gone from connection errors' keys. **Migration `0038`** (the fourth wait cause and the
item-delete index on `document_lines`) is **not applied on staging**: backup waived on staging
(founder); it waits for the founder's word after the push run is green, and then the staging suites
run before the PR merges (RUNBOOK 1, 1.10). **Staging:** `docflow-parse-staging` runs 1b-i;
`docflow-worker-staging` has no machines and is still v10 with the old `parse_client`, which must not be
started against it. The next worker start: deploy `main`'s worker first, read the machine's memory
(raised to 2 GB; `fly.toml` says 1 GB), then start it. Deploy order for Q14: `0038`, then the code.
Next: **PR 1b-ii** (VAL-017 and VAL-018, IMP-011 and the import's own code; until then an import that
hits our bug shows IMP-004), then the Stage 4 checkpoint. Not fixed, each its own PR, timed at the
Stage 4 checkpoint: IMP-003's wording, the 50,000-row cap, the long catalog commit, and a PO that fails
on our side at intake raising no alert. If Stage 4 is not merged by 2026-11-02, D-195's two changes go
next. The API's and the parse machine's auto-stop are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-16 (the Stripe test's fix merged as PR #66; Q14 built; D-205 item 3, D-206)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-16, with PR 1b-i merged and the Stripe test's fix built:** Phase 5.5,
**Stage 4** (the audit of the broad `except` blocks). **PR 1a is merged** (PR #64, D-203 item 14) and
**PR 1b-i is merged** (PR #65; `main` is `c1589a3`; D-204 item 10): a parsing block catches only what
its library raises on a malformed file; anything else is ours, the job answers `error`, and the
document fails with DOC-030 and the keyed alert; the mutation corpus runs in CI through the real
image; a library's stdout no longer corrupts the job's answer (a bug since 3c), and a job's stderr is
logged only as length, hash and category. The Fly gate passed on its head (every self-test PASS,
A-net IPv6 PASS; evidence `docs/spikes/3c-fly-staging/2026-10-16-sha256-2e5d20af/`). **The Stripe
test's fix is built and not merged** (branch `phase55/stage4-stripe-test-race`; D-205): the exact
300-second edge pinned in core on a fixed clock, the API tests given a 30-second margin; no product
code. **Staging:** `docflow-parse-staging` runs 1b-i; `docflow-worker-staging` has no machines and is
still v10 with the old `parse_client`, which must not be started against it. The next worker start:
deploy `main`'s worker first, read the machine's memory (raised to 2 GB; `fly.toml` says 1 GB), then
start it. Deploy order for 1b-i: the worker, then the parse app (the parse app is already on it).
Next: the database retry with migration `0038` (Q14), then **PR 1b-ii** (VAL-017 and VAL-018, IMP-011
and the import's own code; until then an import that hits our bug shows IMP-004), then the Stage 4
checkpoint. Not fixed, each its own PR, timed at the Stage 4 checkpoint: IMP-003's wording, the
50,000-row cap, the long catalog commit, and a PO that fails on our side at intake raising no alert.
If Stage 4 is not merged by 2026-11-02, D-195's two changes go next. The API's and the parse
machine's auto-stop are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-16 (PR 1b-i merged as PR #65; the Stripe test's fix built; D-204 item 10, D-205)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-16, with PR 1b-i built:** Phase 5.5, **Stage 4** (the audit of the broad
`except` blocks). **PR 1a is merged** (PR #64; `main` is `5df2ddb`; D-203 item 14): the alert raised
once per state, the guard over every broad `except`, migration `0037` (on staging, not deployed in
code). **PR 1b is split (founder; D-204). PR 1b-i is built and not merged** (branch
`phase55/stage4-pr1b-i-parse-error`, uncommitted): every parsing block that blamed the file for any
exception now catches only what its library raises on a malformed file, a generic type only from the
library's own frame; anything else is ours, and the parse job answers a new outcome, `error`. A
document whose parse fails on our side, or whose job crashed, fails with **DOC-030** ("a problem on
our side, not with your file") and the keyed alert, keyed by the parse app's release, never DOC-005.
The zip scan and the unguarded ODF `mimetype` read catch exactly what a broken zip raises. Email
intake refuses only the attachment whose check failed on our side, as DOC-030, and reads the rest,
with the keyed alert naming the intake rejection ("Email intake, no document" in the Console). **The mutation corpus:** 369
broken copies of every fixture, each held to the answer it gave before this PR and never `error`, in
CI through the real image too; it found three crashes from the unguarded read (now DOC-005), one
bug of this PR's own (fixed), and **a bug present since 3c**: a library printing to stdout (xlrd, on
an `.xls` whose size is not a whole number of 512-byte sectors, valid ones included) corrupted the
job's answer, so the file failed as DOC-005 or IMP-004; the job now keeps its real stdout for the
answer alone, and the service logs a job's stderr only as length, hash and category, never as text
(7.10; D-204 item 4a). RUNBOOK 8.6 says what to do with a DOC-030. No migration. Local runs
without staging green (parse 534, core 162 of its non-database files, worker 8, web); **the database
tests run in CI only (founder), and the Fly gate (RUNBOOK 8.1) waits for a green pull-request run and
the founder's word.** Deploy order:
the worker, then the parse app. **PR 1b-ii follows Q14:** VAL-017 and VAL-018, IMP-011 and the
import's own code (until then an import that hits our bug shows IMP-004). After 1b-i: the Stripe
test's fix, then the database retry with migration `0038` (Q14), then 1b-ii and the Stage 4
checkpoint. Not fixed, each its own PR, timed at the Stage 4 checkpoint: IMP-003's wording, the
50,000-row cap, the long catalog commit, and a PO that fails on our side at intake raising no alert.
If Stage 4 is not merged by 2026-11-02, D-195's two changes go next. The Fly worker and beat are at
0 with PR A's code (v10), the API's and the parse machine's auto-stop are on, and the Healthchecks.io
check is paused. On the next worker start the machine's memory is read before anything else.

## Replaced on 2026-10-16 (PR 1a merged as PR #64; PR 1b split; PR 1b-i built, not merged; D-204)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-14, with PR 1a built and its staging run done:** Phase 5.5, **Stage 4** (matching performance, then
the audit of the broad `except` blocks). PR A is on `main` (PR #62) and the bench tenant is removed
(PR #63; `main` is `d9b7e8f`). **PR 1, the audit, is split in two (founder; D-203), and PR 1a is
built and not merged** (branch `phase55/stage4-pr1a-except-audit`). It holds: the alert for a fault
in one of our own steps, with the exception's type and our code location and never its message;
**that alert raised once per state**, so an acknowledged one is not raised again until the
exception, the place, the release or the tenant changes (the re-raised-alert defect of D-198 does
not apply to it); the documents a state has hit, counted on the Console, with a list of acknowledged
alerts that are still happening; an alert for a model call's cost record that could not be written;
the guard test over every broad `except` in `apps/` and `packages/` (84 blocks: 16 exempt by rule,
68 listed with a kind); two request blocks narrowed; sixteen log calls that printed an
exception's message changed to its type, our location and the frames; and the five kind 3 log
lines given the frames as well (founder, 2026-10-14). **It carries migration
`0037`** (two nullable columns and their indexes, a reverse script, the CI round trip), **applied on
staging by the founder on 2026-10-14 and verified.** The staging suites from the branch: core and
worker green; the API's one failure was a fault in a test of the PR's own, fixed in the test and its
file green alone (D-203 item 13). The migration goes before the code. No Fly gate applies to 1a. **PR 1b follows:** the parse service's
blocks, DOC-030, VAL-017 and VAL-018, a new job outcome, the mutation corpus and the Fly gate
(RUNBOOK 8.1). After it, inside Stage 4: the Stripe test's fix, and the database retry with
migration `0038`. **The product cannot import more than 50,000 items** (the parse service's row cap,
D-108); the 250,000 measurement on the Fly worker moves to the cap's own PR. **Not fixed, each for
its own PR, with its timing decided at the Stage 4 checkpoint and not inside Stage 4 (founder):**
IMP-003's wording, which would retire items (a pilot blocker); the 50,000-row cap (a candidate); the
catalog commit, which held one request for 4 min 14 s (a pilot blocker, pending a test through a
public address); and, new, **a PO that fails on our side at intake raises no alert** (a pilot
blocker). A known limit stands: a 40-line order on a 250,000-item catalog took 4.86 s on a CI
runner, against the 4 s mark, until the catalog is kept between documents (PR B, not now). After
Stage 4: Stage 5, demo readiness with D-195's model work, the Phase 6 pilot-blocker subset, then
pilots with two or three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not
merged by 2026-11-02, D-195's two changes go next, before Stage 5; the estimate today ends Stage 4
around 2026-10-29 to 2026-10-30. The Fly worker and beat are at 0 with PR A's merged code as the
release (v10), the API's and the parse machine's auto-stop are on, and the Healthchecks.io check is
paused. On the next worker start the machine's memory is read before anything else.

## Replaced on 2026-10-13 (PR 1 split in two; PR 1a built, not merged, with migration `0037`; D-203)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-13, after PR A merged:** Phase 5.5, **Stage 4** (matching performance). **PR
A is merged** (PR #62; `main` is `2988661`; D-202): the fuzzy step in `matching.py` scores the whole
catalog in one call a field and runs the per-item code on the best 100 by each; the loop as it was
is kept as the definition, and 21 parity tests hold the two to the same result. Its merge gate
(founder, 2026-10-13) was a 50,000-item tenant measured on staging through the product, plus the
green 250,000-item CI bench run, and both were met: no difference from the full scan on 100 lines at
250,000 items (run 38424636633); on the Fly worker at 50,000 items, 12.3 ms a line at p50 and 14.8
ms at p95 (the mark is 100 ms), a 40-line order in 1.7 s (the mark is 4 s) and no difference on 30
lines; and one real 40-line PO to `needs_review` with its lines matched in 3.4 s, for $0.0694. CI
was green on the merged head (13 checks) and on `main` (six jobs), each on its first attempt. **The
bench tenant is removed from staging** (D-202 item 18): cancelled by the founder, suspended by one
sweep run locally, made due by one statement the founder ran, and deleted by the founder in the
Console, its 50,000 items in about 4.1 s through a local API. Its Stripe test customer is deleted,
and nothing of any other tenant changed. The database is 43 MB by SQL (58 of 500 MB on the
dashboard). **The next step is PR 1, the broad-`except` audit, on top of `main`; nothing of it is
built.** After it, inside Stage 4: the Stripe test's fix and the database retry, as their own small
PRs. **The product cannot import more than 50,000 items** (the parse service's row cap, D-108),
which the measurement found; the 250,000 measurement on the Fly worker moves to the cap's own PR.
**Three findings are not fixed, each for its own PR, timed at the Stage 4 checkpoint:** IMP-003's
wording, which would retire items (a pilot blocker); the 50,000-row cap (a candidate); and the
catalog commit, which held one request for 4 min 14 s (a pilot blocker, pending a test through a
public address). A known limit stands: a 40-line order on a 250,000-item catalog took 4.86 s on a CI
runner, against the 4 s mark, until the catalog is kept between documents (PR B, not now). After
Stage 4: Stage 5, demo readiness with D-195's model work, the Phase 6 pilot-blocker subset, then
pilots with two or three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not
merged by 2026-11-02, D-195's two changes go next, before Stage 5. The Fly worker and beat are at 0
with PR A's merged code as the release (v10; no redeploy is owed), the API's and the parse machine's
auto-stop are on, and the Healthchecks.io check is paused. On the next worker start the machine's
memory is read before anything else.

## Replaced on 2026-10-13 (PR A merged as PR #62; the bench tenant removed from staging; D-202 items 16 to 18)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-13, after the staging measurement:** Phase 5.5, **Stage 4** (matching
performance). **PR A is built, measured and not merged** (PR #62, branch `phase55/stage4-fast-scan`;
D-202): the fuzzy step in `matching.py` scores the whole catalog in one call a field and runs the
per-item code on the best 100 by each; the loop as it was is kept as the definition, and 21 parity
tests hold the two to the same result. **Its merge gate (founder, 2026-10-13): a 50,000-item tenant
measured on staging through the product, plus the green 250,000-item CI bench run.** Both are done.
The CI bench run (38424636633): no difference from the full scan on 100 lines, 36.5 ms a line at
p50. On staging: 50,000 items imported through the Console, then on the Fly worker 12.3 ms a line at
p50 and 14.8 ms at p95 (the mark is 100 ms), a 40-line order in 1.7 s (the mark is 4 s), no
difference on 30 lines; then one real 40-line PO reached `needs_review` with its lines matched in
3.4 s, for $0.0694. **Merging is the founder's.** The gate had been a 250,000-item tenant on
staging: **the product cannot import more than 50,000 items** (the parse service's row cap, D-108),
which the measurement found; a direct load was declined, and the 250,000 measurement on the Fly
worker moves to the cap's own PR. **Three findings are not fixed, each for its own PR, timed at the
Stage 4 checkpoint:** IMP-003's wording, which would retire items (a pilot blocker); the 50,000-row
cap (a candidate); and the catalog commit, which held one request for 4 min 14 s (a pilot blocker,
pending a test through a public address). A known limit stands: a 40-line order on a 250,000-item
catalog took 4.86 s on a CI runner, against the 4 s mark, until the catalog is kept between
documents (PR B, not now). **Staging holds the bench tenant** `Acme Test Bench 01 (50000 items)`
(about 30 MB; the dashboard read 78 of 500 MB) until PR A merges; the founder then removes it with
the Console's delete. **Nothing of PR 1 is built;** PR 1 (the broad-`except` audit) follows on top
of PR A, then the Stripe test's fix and the database retry as their own small PRs. After Stage 4:
Stage 5, demo readiness with D-195's model work, the Phase 6 pilot-blocker subset, then pilots with
two or three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not merged by
2026-11-02, D-195's two changes go next, before Stage 5. The Fly worker and beat are at 0 with PR
A's image as the release (v10; if PR A does not merge, `main`'s image is deployed again before
staging is used for anything else), and the Healthchecks.io check is paused.

## Replaced on 2026-10-13 (after the staging measurement at 50,000 items; PR A's merge gate changed; D-202 items 13 to 15)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-13:** Phase 5.5, **Stage 4** (matching performance, measured on a
250,000-item tenant). The measurements and what they retire are on `main` (PR #61, `2b9c163`;
D-201): the approved design's two trigram indexes, migration `0037`, the cut-off guard and the
fallback count are not built, and matching keeps scoring every item. **PR A is built and not
merged** (branch `phase55/stage4-fast-scan`; D-202): the fuzzy step in `matching.py` scores the
whole catalog in one call a field and runs the per-item code on the best 100 by each; the loop as
it was is kept as the definition, and 21 parity tests hold the two to the same result. On the
development machine at 250,000 items: 55 ms a line against 10.1 s, no difference on 12 lines.
**Before PR A merges:** the CI bench run on `250000` from its branch (zero differences on 100
lines), and the measurement on the Fly worker against the 250,000 tenant loaded on staging, which
stays on Nano (about 125 MB, under the 350 MB guard), then one real 40-line PO end to end. The
founder is asked before each staging step. **Nothing is loaded on staging, and nothing of PR 1 is
built;** PR 1 (the broad-`except` audit) follows on top of PR A. A known limit is recorded: a
40-line order on a 250,000-item catalog is expected to take about 6 s, against the 4 s mark,
until the catalog is kept between documents (PR B, not now). After Stage 4: Stage 5, demo
readiness with D-195's model work, the Phase 6 pilot-blocker subset, then pilots with two or
three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not merged by
2026-11-02, D-195's two changes go next, before Stage 5. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused.

## Replaced on 2026-10-13 (the records merged as PR #61; PR A built, not merged; D-202)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-12:** Phase 5.5, **Stage 4** (matching performance, measured on a
250,000-item tenant). The sizing job is on `main` (PR #60, `c30926b`) and has run for the 250,000
tenant and the 800,000 mix; the 2.9 million mix has not been run. **Its first run showed that the
approved design's candidate query reads its whole trigram index on a fastener catalog, and no index
variant measured after it keeps parity with today's full scan** (D-201). **Decided (founder):
matching keeps scoring every item, and is made fast instead.** Measured in the bench on a CI
runner at 250,000 items: 57 ms a line against 13.9 s today, with no difference on 100 lines.
**Retired from the approved design:** migration `0037`, both trigram indexes, the cut-off guard
and the fallback count. **The order now:** this records PR (the bench scripts, D-201 and the
measurements; no product code); then **PR A**, the exact fast scan in `matching.py` with no state
kept between documents; then PR 1 (the broad-`except` audit) on top of it. Before PR A merges it
is measured on the Fly worker against the 250,000 tenant loaded on staging, which stays on Nano
(about 125 MB without the indexes, under the 350 MB guard). **Nothing is loaded on staging, and
nothing of PR A or PR 1 is built.** A known limit is recorded: a 40-line order on a 250,000-item
catalog is expected to take about 6 s, against the 4 s mark, until the catalog is kept between
documents (PR B, not now). After Stage 4: Stage 5, demo readiness with D-195's model work, the
Phase 6 pilot-blocker subset, then pilots with two or three customers ("The plan after the Stage
3 "go"", below). If Stage 4 is not merged by 2026-11-02, D-195's two changes go next, before
Stage 5. The Fly worker and beat are at 0 and the Healthchecks.io check is paused.

## Replaced on 2026-10-12 (the candidate indexes measured and retired; an exact, faster full scan instead; D-201)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-11 (evening):** Phase 5.5, **Stage 4 has started** (matching performance,
at 800,000 catalog items). Stage 3 is complete, "go" given 2026-10-11 (D-199), and the leftover
count is a failure since PR #59 (`main` `c462565`). **Stage 4's order (founder, 2026-10-11;
D-200): the catalog sizing job as its own PR, then PR 1 (the broad-`except` audit), then PR 2
(matching).** **Built: the sizing job** (`scripts/bench/`, `.github/workflows/catalog-sizing.yml`;
RUNBOOK 1.8): fake catalogs with fastener families from a fixed seed, loaded into a CI runner's own
database with the two trigram indexes, and the sizes printed. Once it is on `main` the founder runs
it for three mixes, smallest first (the 250,000 tenant alone, the 800,000 mix, then the 2.9 million
mix after a prediction of its run time from the first two), and chooses, from the measured sizes,
between Supabase Pro with the 800,000 mix and Nano with the 250,000 tenant alone. **Nothing is loaded on staging** (Nano on Supabase's Free plan, 500 MB) and nothing of PR 1
or PR 2 is built. After Stage 4: Stage 5, demo readiness with D-195's model work, the Phase 6
pilot-blocker subset, then pilots with two or three customers ("The plan after the Stage 3 "go"",
below). If Stage 4 is not merged by 2026-11-02, D-195's two changes go next, before Stage 5. The
Fly worker and beat are at 0 and the Healthchecks.io check is paused.

## Replaced on 2026-10-11, in the evening (Stage 4 started with the catalog sizing job; D-200)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-11:** Phase 5.5. **Stage 3 is complete: the founder gave the "go" on
2026-10-11**, after merging the checkpoint's record (PR #58, `main` `d674d4a`; D-199). **First
after it:** an API run that does not leave the database as it found it now fails (D-199; RUNBOOK
1.4); on staging at `b278490` the suite read `641 passed, 1 skipped, 3 deselected` with `+0` on all
six counts. **The order from here (founder, 2026-10-11; "The plan after the Stage 3 "go"",
below):** Stage 4 (matching performance, now at 800,000 catalog items; the design is being revised
to that load, and nothing is built before the founder approves the revision); Stage 5 (cleanup,
with the fix for the 10 seconds after a worker start as its own PR there, not before Stage 4); demo
readiness, with D-195's model work; the Phase 6 pilot-blocker subset; then pilots with two or three
customers. If Stage 4 is not merged by 2026-11-02, D-195's two changes go next, before Stage 5.
Staging's database is Nano on Supabase's Free plan (500 MB): nothing is loaded for Stage 4 until a
CI job has measured the sizes and the founder has chosen. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused.

## Replaced on 2026-10-11 (the Stage 3 "go"; D-199)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-09:** Phase 5.5, Stage 3, **checkpoint written and waiting for the founder's
"go"** (CHECKPOINTS.md; D-198). Slices 3a to 3e are merged, the logins cutover (RUNBOOK 10.2) is
done with step 7, and the first worker deploy (RUNBOOK 9.7) is done through step 15. **Done on
2026-10-09:** the three backups and the old role `docflow_app` dropped by the founder; one purge for
every throwaway tenant the API suite makes, with a printed count of what a run leaves (PR #57); the
test leftovers on staging cleaned up (tenant `db4495fb` deleted in the Console; test tenants'
pending jobs cancelled, their alerts and held mail removed; four leftover test admins revoked and
six user rows made inactive). Staging holds 0 pending jobs, 0 held mail rows and 0 alerts belonging
to a test tenant; 30 held rows and 4 alerts with no tenant are kept on purpose. **The checkpoint's
runs on `main` `5a09632`:** core `806 passed, 1 skipped`, worker `191 passed, 7 skipped`, API `630
passed, 1 skipped, 3 deselected` with nothing left behind, and the three live tests passed for
$0.0496 on `claude-sonnet-5`; CI green on the same commit. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused. **Next, after the "go":** the leftover count becomes a failure, the
fix for the 10 seconds after a worker start (its own PR), the model policy (D-195), then Stage 4.
Nothing of Stage 4 is built before the "go".

## Replaced on 2026-10-09 (the Stage 3 checkpoint's runs done; D-198)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-09:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done, step 7 included. The first worker deploy (RUNBOOK 9.7) is done
through step 15 and the founder accepted the 500 + 1 run (D-194, D-196). **Merged since:** the
recycle threshold at 500 MiB (PR #52), the Redis idle change (PR #53) and the document-cost fix
(PR #54; D-163's addendum). **The stop drills and the second idle hour are done (D-197):** an idle
hour is 6,205 Redis commands, so production stays on Pay as You Go; a deploy during a short read
lets the read finish; a worker killed during a read held another tenant's order for 31 minutes 16
seconds, which is the Phase 6 launch blocker, measured. The deployed worker has read 537 documents
on Fly staging; recorded model spend is $6.97. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused. **Dropped by the founder on 2026-10-09,** each checked read-only
by Claude before and afterwards: `backup_0034`, `backup_0035` and `backup_3b` (RUNBOOK 1.3; no
backup schema is left on staging), and the old role `docflow_app` (RUNBOOK 10.2 step 7, with the
SQL as run). Going back past `0036` now means recreating that role first (RUNBOOK 10.2, "Going
back", step 2). **Built for the checkpoint's list:** one purge for every throwaway tenant the API
suite makes (files, rows, the tenant; it refuses a tenant the run did not create) and a printed
count of what a run left behind (RUNBOOK 1.4; four tests added, API 631 in CI). **Next:** the
Stage 3 checkpoint, whose list also holds the three `live_api` tests and the test leftovers'
cleanup (CHECKPOINTS.md). Nothing of Stage 4 is built before its "go", and the model policy (D-195) waits
for the same "go".

## Replaced on 2026-10-09 (the stop drills and the second idle hour done, three PRs merged; D-197)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-08:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done through step 6. **The first worker deploy (RUNBOOK 9.7) is done
through step 15: every gate passed, and the founder accepted the 500 + 1 run on 2026-10-08**
(D-194, D-196; CHECKPOINTS.md, Stage 3, "The first worker deploy"). The deployed worker has read
532 documents on Fly staging; recorded model spend is $6.91. The Fly worker and beat are at 0 and
the Healthchecks.io check is paused. **Next:** two PRs proposed for the founder's review, neither
merged without the founder: the recycle threshold at about 500 MiB, and a longer queue-poll
interval with task-result storage off, followed by one idle hour measured again (D-196). Now due,
the founder's actions: dropping `backup_0034`, `backup_0035`, `backup_3b` and the role
`docflow_app` (RUNBOOK 1.3; 10.2 step 7). Then the Stage 3 checkpoint. Nothing of Stage 4 is
built before its "go", and the model policy (D-195) waits for the same "go".

## Replaced on 2026-10-08 (the first worker deploy's steps 9 to 15 done, the 500 + 1 run accepted; D-196)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-06:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done through step 6. **The first worker deploy (RUNBOOK 9.7) is under
way: steps 2 to 8 are done and passed.** The parse service, the worker and the API are deployed on
Fly staging, one machine each, nothing public (D-194). No document has been processed and nothing
is spent on the model. Step 7's log showed the worker writing its heartbeat URL on every ping:
fixed here, with a line the launcher now prints when its login check passes (D-194). **Next:** the
founder replaces the Healthchecks.io check (RUNBOOK 9.4), Claude deploys the fixed worker and
repeats step 7's checks, then step 9 (G) on the founder's word. The Fly worker and beat are at 0
whenever they are not being tested. Then the Stage 3 checkpoint; nothing of Stage 4 is built
before its "go". `backup_0034`, `backup_0035`, `backup_3b` and the role `docflow_app` are dropped
only after that deploy has processed real documents on staging (RUNBOOK 1.3; 10.2 step 7).

## Replaced on 2026-10-06 (the first worker deploy's steps 2 to 8 done; D-194)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-05:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged. The logins
cutover (RUNBOOK 10.2) is done through step 6 on staging: `0036` applied, the four logins in use,
`docflow_app` set `NOLOGIN` and refused, its URL gone from the root `.env` (CHECKPOINTS.md, Stage
3, "The cutover"; D-188, D-189, D-190). **Next: the first worker deploy. The founder approved its
plan and budget on 2026-10-05** (D-193; the procedure is RUNBOOK 9.7; "Gates for the first worker
deploy" below). Nothing is set on Fly, deployed or spent yet. It starts with the founder's steps 1
and 3, and no deploy command runs before the founder confirms the secret names at STOP 1. Then the
Stage 3 checkpoint. Nothing of Stage 4 is built before that checkpoint's "go". `backup_0034`,
`backup_0035`, `backup_3b` and the role `docflow_app` are dropped only after that deploy has
processed real documents on staging (RUNBOOK 1.3; 10.2 step 7).

## Replaced on 2026-10-05 (later the same day, when the first worker deploy's plan was approved)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-05:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged. The logins
cutover (RUNBOOK 10.2) is done through step 6 on staging: `0036` applied, the four logins in use,
`docflow_app` set `NOLOGIN` and refused, its URL gone from the root `.env` (CHECKPOINTS.md, Stage
3, "The cutover"; D-188, D-189, D-190). **Next: the first worker deploy, which waits for the
founder's go** ("Gates for the first worker deploy" below; RUNBOOK 9.2), then the Stage 3
checkpoint. Nothing of Stage 4 is built before that checkpoint's "go". `backup_0034`,
`backup_0035`, `backup_3b` and the role `docflow_app` are dropped only after that deploy has
processed real documents on staging (RUNBOOK 1.3; 10.2 step 7).

## Replaced on 2026-10-05

<!-- the paragraph as it stood, starting on the next line -->
**Keeping this file current:** update it at the end of every slice, in the same
commit as the slice. Statuses below are as of **2026-10-05** (latest: **the logins cutover (RUNBOOK 10.2) is done through step 6 on staging: `0036` applied, the four logins in use, every step 5 check passing on `main` `6cac1bd`, `docflow_app` set `NOLOGIN` and refused (21:35 UTC), its URL gone from the root `.env`; step 7 not done; the first worker deploy waits for the founder's go**. Earlier the same day: **step 5 found two worker test defects on the founder's machine, not in `0036`, fixed in PR #42 (D-189)**. Earlier the same day: **the logins cutover (RUNBOOK 10.2) started: step 1 passed, then stopped before `0036` on a gap in step 5 (CI's forward state was kept nowhere); the forward state is now a committed file, `0036_post_snapshot.json`, asserted in CI (D-188); staging still at `0035`**. Earlier the same day: **the two backup checks run at `f191e27` as `docflow_app`: core 746 passed and 1 skipped, worker 161 passed and 6 skipped, API 586 passed, 1 skipped and 3 deselected, nothing failed; alerts empty, which carries no weight; `backup_0034`, `backup_0035` and `backup_3b` kept until the first worker deploy has processed real documents on staging (founder); staging confirmed at `0035`**. Earlier, 2026-10-02: **Stage 3 checkpoint DRAFTED (CHECKPOINTS.md, gaps for the cutover, the backup checks and the first worker deploy) and the Stage 4 design APPROVED WITH CHANGES (founder, 2026-10-02: Q1-Q15 decided; DOC-030, VAL-017 and VAL-018 final; PR 1's content final at `444dc78`, blocked only on the Stage 3 checkpoint's "go"), nothing built. The checkpoint waits for the first worker deploy, scheduled straight after the cutover is verified. Before that: 3e MERGED 2026-10-02 17:43 UTC (PR #35, `e99fbb9`, D-185) and the CI runner pin merged 17:48 UTC (PR #36, main `d004186`); the backup checks run the staging suites at `f191e27`, in a separate worktree as `docflow_app` (founder, PR #37; RUNBOOK 1.3); `0036` not applied to staging before the `backup_0035` check, on or after 2026-10-05 03:18 UTC**. Earlier the same day: 3d merged, PR #33, main `4a2b907`, 2026-10-02 03:18 UTC (D-184), recorded in PR #34; the two two-at-once sweep tests go to 3e). Earlier, 2026-10-01: 3d built, `0035` on staging, staging suites green. Earlier the same day: 3c merged, PR #32, main `085a2a5`. Earlier, as of 2026-09-30: security PR #30 and 3b (#31) merged; the 3c design proposed. Earlier summary, as of 2026-09-29 (Phase 5.5: Stages 0, 1 and 2 done -- 2a-2d merged (PRs #14, #15, #18, #20, plus #21 and #22), the audit-findings design (#23) and the D-170 clock PR (#24) merged, Stage 2 checkpoint written; **Stage 3 design agreed 2026-09-29; 3a merged (PR #26, D-179; `0030` on staging); the test-run lock merged (PR #27, D-180); card billing built (D-181), migration `0031` awaiting staging; 3b next**; `0029` row counts confirmed by the founder (the only difference: 52 `stripe_webhook_events` test ids from post-migration runs); D-150 settled -- Fly.io, proof spike PASSED 2026-09-28).
