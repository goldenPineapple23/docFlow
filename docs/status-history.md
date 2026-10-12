# DocFlow build status: earlier status paragraphs

Each entry is the status paragraph `docs/BUILD-STATUS.md` carried until the date shown, word
for word, newest first (founder, 2026-10-05; D-191). The current status is in BUILD-STATUS
itself. This file is not read at the start of a session; it is here so nothing is lost.

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
