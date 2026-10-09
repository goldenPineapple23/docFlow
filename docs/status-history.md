# DocFlow build status: earlier status paragraphs

Each entry is the status paragraph `docs/BUILD-STATUS.md` carried until the date shown, word
for word, newest first (founder, 2026-10-05; D-191). The current status is in BUILD-STATUS
itself. This file is not read at the start of a session; it is here so nothing is lost.

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
