# Checkpoints

One entry per phase, written at the end of it, per `CLAUDE.md` Section 0
rule 3: "run the full test suite, write a checkpoint summary (what was built,
what was assumed, what's open), and wait for 'go' before starting the next
phase."

---

## Phase 5.5 Stage 3 — Worker, storage, queue, database logins — DRAFT (2026-10-02), not complete

**DRAFT.** Written ahead of its evidence so the founder can review the form
and the gate split now. Every `[GAP: ...]` is a result that doesn't exist
yet; nothing in a gap is a prediction. The checkpoint is complete when the
gaps are filled, the suites below have passed, and the founder gives "go".

Built as 3a (PR #26), 3b (PR #31), 3c (PR #32), 3d (PR #33) and 3e
(PR #35), in that order, as agreed on 2026-09-29. Between them: the one-run
test lock (PR #27, D-180), card billing with a trial (PRs #28 and #29,
D-181), the PyJWT and Next.js security update (PR #30), the CI runner pin
(PR #36) and the docs records (PRs #25, #34, #37). Migrations `0030` to
`0035` are applied on `docflow-staging`; `0036` is merged and **not
applied** (the logins cutover, RUNBOOK 10.2). Checkpoint run on `main` at
`[GAP: the commit the checkpoint suites run on, after the cutover]`.

### What the checkpoint needs, and what can follow

Stage 3's findings are H5, H6, H4 (fairness) and F-1, plus D-163's run row.
Each verdict needs proof on the real system it concerns (founder's standing
rule: a fake-only proof is not done). Code and CI are done for all of them.
What's left is proof on staging and on Fly. **Proposed (Q1 below):**

| Item | Checkpoint? | Why |
|---|---|---|
| `backup_0034` check (3c ran cleanly for 3 days) | **Needed** | Staging evidence that 3c held. Suites at `f191e27`, as `docflow_app` (RUNBOOK 1.3) |
| `backup_0035` check (3d ran cleanly for 3 days) | **Needed** | The same for 3d, and step 0 of the cutover |
| Logins cutover, RUNBOOK 10.2 steps 0-6, verified | **Needed** | F-1's verdict: until `0036` is on staging and `docflow_app` is NOLOGIN, F-1 holds in CI only |
| Staging suites on `main` as the four logins (10.2 step 5), plus the live golden run | **Needed** | The checkpoint's test runs of record (the two-at-once sweep tests and the restart record run here on staging's database) |
| G (end to end through the queue and the parse service) and A4 against the real API and worker, on Fly | **Needed** | H5's verdict end to end: the parse service is proven alone on Fly (run 2); that the deployed worker reaches it, and the jobs can't reach the API, is proven only here |
| The 500 + 1 staging run, including the slow case and the largest unclaimed age | **Needed** | H4's verdict at the scale the review measured (a 500-document backfill); CI proves the rule with a real Celery worker, not the scale |
| A fresh `PARSE_SERVICE_TOKEN` | Needed **by dependency** | Not evidence, but G and the 500 + 1 run need the worker on Fly, and the founder gated that deploy on it |
| Worker memory measured, and the founder's choice | Needed **by dependency** | The same: it gates the deploy that produces G and the 500 + 1 run |
| Healthchecks.io heartbeat set up and tested to its pass marks (RUNBOOK 9.4) | Needed **by dependency** | The same |
| Two-at-once sweep tests | Done (CI) | Built in 3e, green in CI (worker 180); they run again on staging in the step 5 run |
| Restart record built and tested | Done (CI) | The same |
| `docflow_app` dropped (10.2 step 7, 3 clean days after the cutover) | **Can follow** | Cleanup after F-1 is already enforced; it doesn't change a verdict |
| `backup_0034` and `backup_0035` dropped | **Can follow** | The founder's own actions after each check |
| Moving CI to Ubuntu 26 | **Can follow** | A deliberate CI change before 24.04 is retired, not Stage 3 work |
| The three `live_api` tests (the golden fixture, the golden fixture with examples, the example-contamination check) | **On the list** (founder, 2026-10-09; D-197) | They run only at checkpoints (RUNBOOK 1.4), against the real model |
| A shared tenant helper for the API suite that also removes a test tenant's alerts and outbox rows, with a before-and-after count at the end of the run | **On the list** (founder, 2026-10-08; D-197) | The alert triage of 2026-10-08: a killed run cleans nothing, and only some of the suite's hand-written tenant helpers delete alerts |
| The test leftovers' cleanup on staging, test tenant `db4495fb` ("Acme Test Distributor", `pending_deletion` past its date) included | **On the list** (founder, 2026-10-09; D-197) | The lifecycle sweep visits that tenant every five minutes, and its ready-to-delete alert stays open |

So the checkpoint waits for the first worker deploy (RUNBOOK 9.2) and the
runs made with it. **Decided (founder, 2026-10-02, Q1 below).**

**Order:** the backup checks (2026-10-04 and 2026-10-05), then the cutover
(RUNBOOK 10.2 steps 0-6), then **the first worker deploy straight after the
cutover is verified**. It doesn't wait for `docflow_app`'s drop (about
2026-10-08), so the checkpoint isn't idle. **But only if verification
turned up nothing at all:** anything unexpected, even something that looks
cosmetic, stops the deploy until it's explained (RUNBOOK 10.2, "Stop
condition before the first worker deploy"), so a cutover problem and a
deploy problem never land together. G and A4 run against Stage 3's
parse service, the image on Fly since run 2. **No Stage 4 parse-service
change reaches the Fly parse app before G and A4 have passed**, so each
stage's evidence is about its own code.

### Test runs

`[GAP: staging suites on main after the cutover, one at a time (RUNBOOK
1.4), each as pytest printed it, with the login each ran as]`
- **Core:** `[GAP]`
- **Worker** (as `docflow_worker`): `[GAP]`
- **API** (as `docflow_api`, `docflow_admin`, `docflow_stripe`): `[GAP]`
- **Live** (`pytest -m live_api`: golden, golden with examples,
  contamination): `[GAP]`, with each call's cost, and **the mean, median
  and maximum of the three** (founder, 2026-10-05). Three calls on two short
  text orders: a floor for cost per document, not a sample of what
  customers send ("A representative cost sample", below).

**The two backup checks, run 2026-10-05.** One run serves both: the same
suites, at `f191e27`, in a worktree, connecting as `docflow_app` (RUNBOOK
1.3), one at a time.

- **Why `f191e27` and not `main`.** Staging is at `0035`, read from the
  database on 2026-10-05: `0034`'s and `0035`'s tables and columns are
  there; `0036`'s roles, `worker_starts` and `record_worker_start` are not.
  `main` holds 3e, whose suites connect as `0036`'s four logins, so they
  can't run on staging before the cutover. `f191e27` differs from 3d's
  merge (`4a2b907`) only in `RUNBOOK.md` and `docs/BUILD-STATUS.md`. It is
  the right commit for the 3c window too: 3c's own merge commit expects
  `0034`'s schema, and staging has had `0035` since 2026-10-01.
- **Core:** `746 passed, 1 skipped in 53.58s`. The skip is in
  `test_storage_bucket_live.py`. The same counts as staging's run on
  2026-10-01.
- **Worker:** `161 passed, 6 skipped in 1232.50s (0:20:32)`. The six skips
  need Linux or CI's parse container (pytest's reasons, in the run's
  output); the same counts as 3d's final staging run. Nothing failed.
- **The worker suite's 20:32 is its normal time, not a slow run**
  (founder's question, 2026-10-05). "About 12 minutes" was Claude's
  figure from Stage 3a (2026-09-29), when the suite was 136 tests. It
  was never the time for this code and should not have been quoted.
  The evidence:
  - **The same suite on the same code took 20:43 on staging on
    2026-10-01:** `161 passed, 6 skipped in 1243.29s (0:20:43)`
    (BUILD-STATUS, "3d on staging", worker, final code). Today:
    1232.50 s, 11 s less. Between that run's commit (`2d18b0f`) and
    `f191e27`, the worker, core and parse code differ by one typing
    change in `run_workers.py` (2 lines).
  - **The hand-started parse service isn't the cause.** That earlier
    run let the suite start its own parse service, and took 11 s
    longer. Today's parse service log shows 77 parse jobs taking 39.3
    s in all (3% of the run), the longest 13.66 s; the first came 3
    min 20 s into the run.
  - **How the time grew:** 109 tests in 10:01 at the Stage 2
    checkpoint (below), 167 tests in 20:43 after 3a to 3d. **Which
    tests take the time isn't measured:** neither run printed
    per-test times.
- **API:** `586 passed, 1 skipped, 3 deselected, 657 warnings in 2561.04s
  (0:42:41)`, the count RUNBOOK 1.4 expects. The skip is
  `test_parse_token_boundary.py` (it needs the real parse service with a
  token; it runs in CI). The 3 deselected are the `live_api` tests.
- **After the three suites** the alert query read the same: 15 rows, none
  since either merge. The worktree and its copy of `.env` were then
  deleted, and the parse service stopped.
- **The parse service was started by hand** from the worktree's code, a
  departure from RUNBOOK 1.3 as it was written (the worktree has no
  `apps/parse/.venv`). `apps/parse` is the same at `f191e27` and `main`,
  and `app`, `parse_service` and `docflow_core` were each checked to load
  from the worktree. RUNBOOK 1.3 now gives the steps.
- **Alerts:** none of any type since either merge (3c, 2026-10-01 20:53
  UTC; 3d, 2026-10-02 03:18 UTC). `founder_alerts` holds 15 rows, the
  newest from 2026-09-30. **This carries no weight (founder, 2026-10-05):**
  the worker and API aren't deployed, so no document was processed on
  staging in either window outside the test suites. "Ran cleanly for 3
  days" rests on the suites alone.
- **The backups stay (decided, founder, 2026-10-05):** `backup_0034`,
  `backup_0035` and `backup_3b` are kept until the first worker deploy
  has processed real documents on staging. The first two together are
  0.44 MB. Their size on staging:

  | Backup | Tables | Size |
  |---|---|---|
  | `backup_0034` | documents, extraction_runs | 0.20 MB |
  | `backup_0035` | documents, founder_alerts, email_outbox | 0.24 MB |
  | `backup_3b` (still there) | four tables | 0.16 MB |

  The whole database is 23.4 MB against the free plan's 500 MB limit, so
  keeping them costs nothing and risks nothing. Every backup table has RLS
  on.

**The cutover (RUNBOOK 10.2 on `docflow-staging`, 2026-10-05; all times
UTC). Steps 1 to 6 are done; step 7 is not.** The evidence files are in
`docs/cutover/0036-staging/`.

- **Step 1, 15:52:** staging's snapshot, taken as `docflow_app`, was
  identical to `supabase/reverse/0036_pre_snapshot.json`: 80 policies, 13
  functions, SHA-256 `11f4ebefb1ce0e33c41c723dfe800694ff26657c38d3ae10439528b1242a9b2e`
  (`policy_snapshot_before_0036.json`). Read again at 16:31, the same.
- **Stopped before step 2** on a gap in the RUNBOOK: step 5 had no
  reference state to compare with. Fixed first (D-188, PR #41).
- **Steps 2 to 4, the founder, about 16:50:** `0036` applied, the four
  logins turned on, the five URLs in the root `.env`. Checked without
  showing a value: each URL well formed, four different passwords, each
  connecting as its own login.
- **Step 5, the snapshot, 16:52:31:** SHA-256
  `81bd3359915120f6f948fb42026f7befdcfaf6ccb4acc15d2a044c597e615f9d`, equal
  to `supabase/reverse/0036_post_snapshot.json` and to `main`'s CI; 0
  differences; 80 policies, 16 functions
  (`policy_snapshot_after_0036.json`). Taken again at 21:27:08, after the
  last suite run: the same hash.
- **Step 5, the logins:** `db.verify_logins` passes for all four; none is
  a superuser or bypasses RLS. Read again after every suite run.
- **Step 5, the suites, as the four logins, one at a time:**

  | Suite | Commit | Line as printed | CI's count |
  |---|---|---|---|
  | core | `9fd519a` | `796 passed, 1 skipped in 43.53s` | 797 |
  | worker | `9fd519a` | `2 failed, 172 passed, 6 skipped in 1275.38s (0:21:15)` | 180 |
  | API | `9fd519a` | `1 failed, 624 passed, 1 skipped, 3 deselected, 657 warnings in 2783.15s (0:46:23)` | 626 |
  | API, full re-run | `9fd519a` | `625 passed, 1 skipped, 3 deselected, 657 warnings in 2552.60s (0:42:32)` | 626 |
  | worker | `6cac1bd` | `184 passed, 7 skipped in 1276.50s (0:21:16)` | 191 |
  | core | `6cac1bd` | `805 passed, 1 skipped in 39.59s` | 806 |

  - **The two worker failures were the tests, not `0036`** (D-189, PR
    #42, `main` `6cac1bd`): the launcher tests cleared the other logins
    from the environment but not from the root `.env`. The evidence the
    founder asked for before any fix is in D-189.
  - **The one API failure is recorded as transient** (founder,
    2026-10-05; D-190). `test_11_attachments_quarantines_all_and_none_enqueued`
    got 503 because an upload to Supabase Storage did not answer
    (`ReadTimeoutError`; the client waits 30 s for an answer and tries
    up to three times).
    **Once in 1,252 API tests across the two full runs, and not
    reproduced:** the test passed alone at 18:04, straight after that
    run, and in the full re-run. The evidence:
    1. **Supabase's status history has one incident open across the
       run's window** (17:17 to 18:03 UTC): "Intermittent latency in
       Eastern US", on its API Gateway, opened 2026-09-29 16:26 UTC
       (https://stspg.io/gxmmvm5s9rh0). In its words, "increased latency
       for clients in the eastern US", for "clients connecting to
       Supabase Projects from the eastern United States ... regardless
       of the region in which the Project is hosted". On 2026-10-02 it
       said some users were "still experiencing issues during peak
       Eastern US business hours"; its next update, "A fix for this
       issue has been implemented", came at 18:45 UTC on 2026-10-05,
       about 40 minutes after the run ended. This machine is in that region
       (its clock is UTC-4) and the run was in the early afternoon
       there. Supabase lists no Storage incident that day. **This fits
       the timeout; it does not prove it.**
    2. **The project's own Storage logs for that window** (two exports
       by the founder from the Supabase dashboard, 500 rows each, the
       export's limit: 17:18:46 to 17:37:58 UTC and 17:37:05 to 18:04:33
       UTC; read by Claude on 2026-10-05; D-192). **Storage received the
       upload three times and answered none of them in time.** The rows
       for the failed test's tenant, as logged (UTC):

           17:32:37.367  PUT | ABORTED RES | tenants/2054f64b-.../uploads/c071aedf....txt
           17:33:07.682  PUT | ABORTED RES | (the same key)
           17:33:39.131  PUT | ABORTED RES | (the same key)
           17:34:00.741  [Admin]: ObjectAdminDelete (the same key)
           17:34:09.959  [Lifecycle]: ObjectCreated:Put (the same key)

       The three rows are 30.3 and 31.4 seconds apart, which is the
       client's 30-second wait and its three tries. `ABORTED RES` is read
       here as "the caller hung up before the answer was sent": that is
       Claude's reading of the label, and the timing fits it.
       - **Storage was reporting trouble of its own in the same minutes:**
         eight rows of `[Queue Sender] Error while sending job to queue,
         sending synchronously`, from 17:30:20 to 17:35:41, and none
         before or after in either export.
       - **The next test was hit too, and the retry carried it:** its
         first upload (tenant `00774d47`) is `ABORTED RES` at 17:34:18
         and answered 200 at 17:35:11; that test passed. So "once in
         1,252" is one failed test; two tests met the slow minutes.
       - **Nothing else failed.** The two exports together, each row
         counted once: 931 rows, 524 of them requests, of which 514
         answered 200, one 204, five 404 (tests asking for a file that
         is not there, as they mean to) and these four aborted uploads.
         No 5xx row. Storage was answering in under a second again at
         17:36. The run's first 92 seconds (17:17:14 to 17:18:46) are
         before the exports.
       - **After 17:39 the "object created" rows ran late:** 11 to 91
         seconds behind their uploads until 17:51, under a second again
         at 17:55 and 18:04. Every one of those uploads answered 200.
       - **The exports are not a full record:** many objects have a
         "created" row with no request row, and the reverse. A missing
         row proves nothing; the rows above are there.
       - **The timed-out upload still landed.** Storage finished it at
         17:34:09, half a minute after the client had given up, and the
         object is in the bucket now with no row pointing at it (listed
         read-only on 2026-10-05: one object under `tenants/2054f64b-...`).
         Nobody can reach it: no document row, so no signed URL; and a
         tenant's deletion removes everything under its prefix
         (`delete_tenant_storage`). Left in place with staging's other
         test data. **For a real email this means a 503 can leave one
         unreferenced file behind, and the re-sent email is stored under
         a new key.** Recorded, not changed; whether to do more is the
         founder's to decide (D-192).

       **So the cause is on Supabase's side and lasted about six minutes
       (17:30 to 17:36 UTC).** The logs do not say why; item 1's incident
       is the likely reason and stays unproven.
    3. **What a real email meets when intake answers 503.** The whole
       email is rolled back, so nothing is half received, and Postmark
       sends it again. Its documentation ("Errors and retries",
       https://postmarkapp.com/developer/webhooks/inbound-webhook, read
       2026-10-05): "If Postmark does not receive a 200 response from a
       webhook server, we will retry the POSTing the webhooks. If we
       receive a 403 response, we will stop retries. A total of 10
       retries will be made, with growing intervals." The schedule it
       lists: 1, 5, 10, 10, 10, 15 and 30 minutes, then 1, 2 and 6 hours,
       which is 10 hours 21 minutes in all. Then: "If all of the retries
       have failed, your Inbound page will show the message as Inbound
       Error." Intake answers 503 here and never 403
       (`test_the_inbound_webhook_answers_503_during_an_outage_so_postmark_retries`),
       and a second delivery of the same email is received once
       (`test_duplicate_webhook_delivery_is_idempotent`). So one timeout
       costs that email about a minute. **Two limits:** an outage longer
       than about 10 hours leaves the message at "Inbound Error" until
       someone retries it from Postmark by hand (now in RUNBOOK 7.3); and
       none of this has been seen with a real email, because no Postmark
       account exists yet (D-027).
  - **The API suite was not run again on `6cac1bd`.** PR #42 changed no
    file under `apps/api`, `packages/core/docflow_core`, `apps/worker/app`,
    `apps/parse` or `supabase/migrations`.
  - **Every skip is one this machine always has:** core's
    `test_storage_bucket_live.py` (D-174), the API's
    `test_parse_token_boundary.py`, the worker's six that need Linux or
    CI's parse container, and since D-189 the one that commits a worker
    start, which runs only in CI.
  - **`worker_starts`:** 0 in the last hour before the `6cac1bd` worker
    run (21:04:32) and 0 after it (21:25:56), read as `docflow_admin` and
    as `docflow_api`. The suite's own line: `before 0, after 0; starts
    kept by tests: 0`.
  - **Which worker tests take the time,** measured for the first time
    (`--durations=25`, the founder's request): one test,
    `test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export`,
    takes 255 to 269 s, about a fifth of the run. The next 24 take 16 to
    36 s each: the dispatch, time-limit and pipeline-integrity tests.
- **Step 5, alerts:** `founder_alerts` had 15 rows before `0036`, the
  newest from 2026-09-30, and has the same 15 after every run.
- **`main`'s CI on `6cac1bd`** (run 37373539167), all six jobs green: core
  806, api 626, worker 191, parse 128 and 56, web 73, web-live 3. The
  worker job shows `worker: must run in CI: ...
  test_a_committed_start_appears_in_the_count_healthz_reads: ran and
  passed`. Core and web passed on a re-run: GitHub had cancelled them
  before they started ("The job was not acquired by Runner of type
  hosted"), during its own Actions incident that day.
- **Step 6, every place `docflow_app`'s URL lived:**

  | Place | What became of it |
  |---|---|
  | The root `.env`, live setting | replaced by the four logins at step 4 |
  | The root `.env`, a comment kept from step 4 | removed at 21:35, after the refusal was verified |
  | `.env.example`, any other `.env*` in the repository | never held it |
  | Fly secrets | none on the API and worker apps; only `PARSE_SERVICE_TOKEN` on parse |
  | GitHub secrets and variables | none (the founder's check, both tabs) |
  | A copy of the whole `.env` from 2026-09-28, in a folder beside the repository | **not on the RUNBOOK's list.** A local backup, never uploaded or shared (the founder); deleted by the founder and removed from the Recycle Bin, verified 18:37 |
  | Claude Code's own files on this machine: two edit backups and two session transcripts | left in place; the URL in them stopped working at `NOLOGIN` |

  The founder asked for a search of the whole user profile for other
  copies. As run: files matched by name (`.env*`, `*env*.txt`, `*.env`)
  and by content (the old URL, the service role key, the Anthropic key,
  the four new passwords), 57,911 files seen and 42,233 read, plus the
  twenty session transcripts over 5 MB read separately. Not covered:
  browser profiles and app caches, game folders, zip files other than
  those in Downloads, files over 5 MB, and 40 files that were locked.
  The four new passwords are in the root `.env` and nowhere else.
- **Step 6, `NOLOGIN`:** the founder ran `ALTER ROLE docflow_app NOLOGIN`
  at about 21:32 (`rolcanlogin` false at 21:33:13). **The old URL still
  connected.** The pooler held one open session for `docflow_app`, opened
  at 21:31:22 by Claude's own first check, made before `NOLOGIN` had run;
  `NOLOGIN` stops new logins, not open sessions, and the pooler handed
  that session to each new attempt. The founder ended it
  (`pg_terminate_backend`). **Refused at 21:35:02, three attempts of
  three:** `FATAL:  (EAUTHQUERY) user not found in the database`. Only
  then was the old URL removed from `.env`; the five settings were
  checked again at 21:35:36. RUNBOOK 10.2 step 6 now has the missing
  step.
- **Step 7 (dropping `docflow_app`) is not done.** Decided (founder,
  2026-10-05; D-190): only after the first worker deploy has processed
  real documents on staging, the same trigger as the backups (D-186).
  The RUNBOOK said "after 3e has run cleanly on staging for 3 days", and
  nothing of 3e runs on staging until the worker is deployed.
- **What was not expected, in one list** (10.2's stop rule: each stops
  the first worker deploy until explained and the founder has said go):
  the missing reference state at step 5 (D-188); the two worker test
  failures and the six rows the suite left in `worker_starts` (D-189);
  one Storage timeout in the API suite; the copy of `.env` outside the
  repository; jobs cancelled by GitHub before they started, in the CI
  runs on `69a2970` and on `6cac1bd`; the pooled session after
  `NOLOGIN`. Each is explained above; the Storage timeout is
  recorded as transient, with its evidence, the project's own Storage
  logs included (D-192). **The first worker deploy
  has not been started and waits for the founder's go.**

**The first worker deploy** (RUNBOOK 9.7; steps 2 to 8 on 2026-10-06,
D-194; steps 9 to 15 on 2026-10-07 and 2026-10-08, D-196; the files are in
`docs/spikes/first-worker-deploy-staging/`). **Every gate passed, and the
founder accepted the 500 + 1 run on 2026-10-08.** Times are UTC.

- **G** (2026-10-07). G1: the golden text order, a legacy `.doc` and a
  scanned image, each uploaded through the deployed API and read by the
  deployed worker and parse service to `needs_review` (24.4 s, 25.3 s,
  14.1 s; $0.0466); the golden document's stored values match Section 8.3
  in 35 of 35 fields; the stopped parse machine woke on the first upload
  in 3.55 s with its canary passing. G2: a catalog import by the same
  path, committed, 4 rows. G3: 13,600 to 13,700 Redis commands in the
  idle hour, and a busy hour (19 short orders) within noise of that.
- **A4 against the real API and worker** (2026-10-07): from inside a parse
  job the API's private address (port 8000) and the worker's (port 22,
  Fly's SSH) are both blocked, while the same addresses answer from
  outside the job; all 22 group-A self-tests passed.
- **The 500 + 1 run** (2026-10-08, the real model, a 2 GB worker with one
  document slot). **501 of 501 `needs_review`, each on its first attempt;
  no alert; no stop.** Cost $6.2675: per document a median of $0.0125 and
  at most $0.0179. Time: 500 documents in about 70.5 minutes, about 8.5 s
  each. **The single order** was saved while 332 of the backfill waited,
  was dispatched when the document being read finished, 6.2 s later, and
  was processed 14.0 s after its save; **no backfill document was claimed
  between its save and its claim.** **The slow case:** the founder pressed
  the Console's "recompute" twice; the first press fell with two
  five-minute sweeps and one document waited **4.34 s** to be claimed,
  **the largest unclaimed age of the run**; the second, 3.15 s; the median
  was 0.17 s. Far under 20 minutes, so `DISPATCH_UNCLAIMED_ALERT_MIN` stays
  20 and nothing went back to the founder under Q9.
- **Memory, and the founder's choice.** On the 1 GB worker (962 MiB
  usable) a 22 MB scanned PDF took the documents process to 328 MiB and
  the machine to 699 MiB; a 24.4 MB one took them to 466 and 831 MiB
  before failing for another reason (below). **The founder chose a 2 GB
  worker for production,** and a lower recycle threshold (about 500 MiB),
  to be proposed by PR. On a 2 GB worker, two 22 MB scans back to back
  peaked at 322 and 333 MiB (the process's own high-water mark: 359 MiB)
  and the process held 330 MiB afterwards: the second did not add to the
  first.
- **The heartbeat drills against their pass marks** (2026-10-07). Worker
  stopped: the "down" e-mail 10 minutes after the last ping (mark: 12).
  Started again: the "up" e-mail at the first ping. Three starts in an
  hour: `worker_restarting`, high, raised at the third, its e-mail held;
  recorded as "raised and held, not delivered". Documents worker paused
  with the dispatch process running: `/fail` sent at 20 min 24 s unclaimed
  (mark: 20 minutes plus one pass), then "up" when it was resumed.
- **The fresh token's set time on both apps:** not obtainable; this `fly`
  version prints no time for a secret (D-194). What was shown instead: the
  token's digest was new and the same on the parse app and the worker at
  15:27:10 on 2026-10-06, and every document since has been parsed through
  it.
- **Found on the way, each recorded in D-196 for Phase 6 and none fixed
  here:** a scanned PDF between about 23.9 MB and the 25 MB intake cap is
  accepted and then fails at the model's request limit (DOC-008, whose
  advice to upload it again cannot work for this cause); alert e-mails
  from the worker link to `localhost`; a tenant with no tier has no abuse
  ceiling; the rollup and the sweeps share the documents process (the
  4.34 s above); the documents process grew from 104 to 168 MiB over 500
  text orders; about half of the idle Redis commands are the workers'
  once-a-second poll.
- **Not obtained:** a `/healthz` reading before the first deploy of
  2026-10-07; two login lines that day; the time of one Redis reading; a
  Redis reading before the 500 + 1 run; the parse machine's memory during
  the two scans of 2026-10-08. Each is in D-196.
- **Now due, the founder's actions, not done here:** dropping
  `backup_0034`, `backup_0035`, `backup_3b` and the role `docflow_app`
  (D-186, D-190; RUNBOOK 1.3 and 10.2 step 7).

**After the first worker deploy: the stop drills and the second idle hour**
(Fly staging, 2026-10-08 and 2026-10-09; D-197 has each run's times):

- **An idle worker scaled to 0** exits by itself in 10 s with code 0.
  `fly scale count` sends `SIGTERM` 5 s after the `SIGINT`, which the
  worker survives; a deploy sent none. A scale-down is not a stand-in for
  a deploy.
- **One order at the 10 s queue read:** the worker received the task 4 ms
  after the dispatch pass sent it.
- **A scale-down during a read:** the read finished, about 5 s after the
  signal, and the worker then exited with code 0.
- **The documents worker killed during a read:** the stuck sweep returned
  the document and it was read on its second try. Another tenant's order,
  already saved, waited **31 minutes 16 seconds from the cut-off claim to
  its dispatch**, with no alert: the Phase 6 launch blocker, measured.
- **A deploy during a read** (drill D, passed, founder): the read finished
  8 s after the signal, the worker exited by itself 12 s after it, no
  `SIGTERM`. A read longer than `kill_timeout` (30 s) is still untested on
  a deploy.
- **The idle hour with the 10 s read and no stored results: 6,205
  commands** (the first deploy's was about 13,650). Production's Redis
  stays on Pay as You Go (founder).
- Spend for all of it: $0.0630, to $6.9740 over 1,076 runs. The deployed
  worker has now read 537 documents.

**CI**, latest on `main` (`d004186`, run 37043224237, after 3e and the
runner pin): core 745 tests, api 626, worker 180, parse unit 128 and HTTP
56, each `0 failed, 0 skipped, 0 unapproved`; web 73 passed, web-live 3
passed. The only warning is the known IPv6 one (the runner has no IPv6;
IPv6 isolation is proven on Fly). `[GAP: CI on the checkpoint commit]`

### Verdicts (draft; each one final only when its gap is filled)

| Finding | Draft verdict | Evidence so far | Still owed |
|---|---|---|---|
| **H5** (quick part, 3a) No time limits or memory cap on any task | **Closed** | A hard limit per task, one retry after a timeout, then DOC-022 (D-179). Worker `test_time_limits_prefork.py`, a real prefork worker in CI: `test_a_hung_task_is_killed_the_worker_carries_on_and_a_timeout_gets_one_retry`, `test_a_worker_killed_outright_records_no_timeout_and_takes_the_worker_stopped_path` | Nothing |
| **H5** (3c) Parsers ran inside the worker, which held every key | **Closed** | `apps/parse`: one sandboxed job per file, isolation confirmed by the job itself, no keys in the service (D-183). CI: canary, A, S and B self-tests in the real sandbox, D1 parity on every fixture through the real service. **Fly staging run 2 (2026-10-01): every item PASS**, including A-net IPv6, cgroup v1, A3 against real Upstash and A4 against a stand-in (`docs/spikes/3c-fly-staging/2026-10-01-sha256-acc99434/`). API `test_parse_token_boundary.py`: the API never holds the parse token. **End to end on Fly staging (2026-10-07, D-196):** G1, G2 and G3 through the deployed API, worker and parse service; A4 against the real API and worker PASS | Nothing |
| **H6** Files on one machine's disk | **Closed** | Supabase Storage, a private bucket, the prefix check on read, write and delete (D-182). Core `test_storage.py` (25), including `test_tenant_a_cannot_read_a_tenant_b_path_and_storage_is_never_contacted`; `test_storage_bucket_live.py` against the real bucket: `test_the_bucket_has_no_public_url`, `test_the_anon_key_reaches_no_file`, `test_a_customers_own_signed_in_token_reaches_no_file`, `test_a_hard_delete_empties_the_tenants_folder_in_the_real_bucket`. API `test_storage_outage_api.py`. Staging rollout 2026-09-30: 89 files copied and verified by SHA-256; 14 rows flagged and left out by the founder's choice (seed and test data) | Nothing |
| **H4** (fairness, 3d) One tenant's backfill starved every other tenant | **Closed** | The dispatcher takes turns between tenants with a per-tenant cap; providers' outages are waits, not failures (D-184). Worker `test_dispatch_real_worker.py`, a real Celery worker and queue: `test_a_newcomer_gets_the_next_slot_and_a_tenants_single_order_beats_its_own_backfill`, `test_the_dispatch_process_reads_only_its_queue_and_never_claims_a_document`; `test_dispatch_db.py` (22) on the real database. Staging suites 2026-10-01 green on 3d's final code. **The 500 + 1 run on Fly staging (2026-10-08, D-196):** a second tenant's single order, saved while 332 of a 500-document backfill waited, was claimed before any further backfill document and processed 14.0 s after its save; 501 of 501 `needs_review`; largest unclaimed age 4.34 s | Nothing (the Phase 6 load test is its own item) |
| **H4** (matching speed) | **Not Stage 3** | Stage 4 (design proposed alongside this draft) | -- |
| **F-1** Flag policies keyed on settings any connection could set | **Closed; the old login's drop owed** | Four logins, each flag policy `TO` one login, functions granted login by login (D-185). API `test_logins_db.py` (13 tests, some per login or per function), including `test_every_flag_policy_applies_to_exactly_its_login`, `test_a_flag_that_opens_tenants_opens_it_only_on_its_own_login`, `test_each_security_definer_function_is_callable_by_exactly_its_logins`. `0036` round-tripped in CI (forward, reverse, forward) against staging's pre-0036 snapshot. **On staging since 2026-10-05** ("The cutover", above): the snapshot equals the committed post-0036 state, the API suite with these tests passed as the four logins (`625 passed, 1 skipped, 3 deselected`), and `docflow_app` is `NOLOGIN` and refused. **Deployed (2026-10-06 to 2026-10-08, D-194, D-196):** the worker prints `login check passed as docflow_worker` at each start and has processed 532 documents on its own login (531 to `needs_review`, one failed at the model's request limit), each uploaded through the API on its own | Step 7, dropping `docflow_app` (now due; the founder's action) |
| **D-173 residual risk** Any app code could call the Stripe event function | **Closed in CI and on staging** | Only `docflow_stripe` may execute it: `test_only_the_stripe_login_may_execute_the_function`, `test_the_api_login_is_refused_the_stripe_event_function`. Both passed on staging as the four logins on 2026-10-05, and the function's grant there is `docflow_stripe` alone (the post-0036 snapshot) | -- |
| **D-163** A worker killed mid-call left the call uncosted | **Closed** | A `started` run row before every paid call (0034). Worker `test_stage3c_db.py`: `test_F3_a_worker_killed_during_the_model_call_leaves_the_call_on_the_cost_record`, `test_F3_a_finished_call_has_its_started_row_and_one_outcome`, `test_F3_the_database_refuses_a_started_row_that_claims_an_outcome`; core `test_D163_the_routing_call_has_a_started_row_before_it_and_its_outcome_points_at_it`. **The document's own cost figure was found short on Fly staging on 2026-10-08 and fixed by PR #54** (D-163's addendum): it is set from the sum of the document's finished runs; `test_F3_the_reread_after_a_lost_call_keeps_the_lost_call_on_the_documents_cost`, `test_F3_the_documents_cost_counts_the_routing_read_with_the_extraction`, `test_a_paid_call_that_ended_in_a_provider_wait_stays_on_the_documents_cost` | Nothing |
| **M6** `rollup_stale` raised but never registered | **Closed** (fixed in 3d) | Registered; one test raises every registered type end to end | Nothing |
| **Spike carry-ins** (D-150): hide `/.fly` and `/sys`; re-probe against the real Upstash and API | **Closed** | Hidden in the job's mount namespace and checked on Fly in run 2; A3 against the real Upstash passed there. **A4 against the real API and worker passed on Fly staging on 2026-10-07** (D-196; `docs/spikes/first-worker-deploy-staging/2026-10-07/a4_real_api_worker_2026-10-07.txt`) | Nothing |

### What was built

- **3a (D-179, `0030`):** hard time limits per task; a timeout written by
  the worker's main process; one retry, then DOC-022; the owed Stripe cancel
  retried by the lifecycle sweep; exports and imports that never finish
  (EXP-009, IMP-009).
- **3b (D-182, `0033`):** files in Supabase Storage through a
  Storage-only key; a Storage outage waits, never fails; a refused stored
  path is told apart from a missing one, and a cross-tenant path raises a
  critical alert; DOC-025 to DOC-028.
- **3c (D-183, `0034`):** the parse service on Fly, one sandboxed job per
  file; the worker and API hold no parsing library; the run row before
  every paid call; Debian from a dated snapshot, moved weekly by CI.
- **3d (D-184, `0035`):** the dispatcher, per-tenant turns and caps;
  provider, Storage and parse-service waits; `/healthz` with the
  dispatcher's heartbeat; beat as its own process.
- **3e (D-185, `0036`):** four database logins; the restart record and
  `worker_restarting`; the Healthchecks.io heartbeat with `/fail` for a
  document dispatched and unclaimed for 20 minutes; the two two-at-once
  sweep tests; the tested reverse.
- **Between slices:** the one-run test lock (D-180), card billing with a
  7-day trial (D-181), the security update (#30), the CI runner pin (#36).

### What was assumed

- **Fly's machine restart policy** (`on-failure`, 10 retries counted within
  5 minutes) is as Fly documents it; the restart record covers what it
  doesn't (a slow crash loop).
- **Healthchecks.io's free plan** is as its pricing page read on 2026-10-02
  and Terms read on 2026-10-02 (20 checks; no commercial-use restriction
  stated; at most 5 pings a minute per check).
- **The pooler's headroom:** four logins at pool size 5 is 25 connections
  against about 44 usable on Nano; production's pool size is a Phase 6
  decision.
- **A duplicate retry job can exist, by design** (3e, founder): the
  document task's claim runs it once. Nobody should "fix" it into counting
  jobs.

### Found during Stage 3, not by the tests

- **3b:** Supabase's bulk delete never worked (`DeleteObjects` needs its
  body labelled `application/xml`); found on the first staging worker run.
- **3c:** on Fly's cgroup v1 a dead process shows the root cgroup, so the
  first orphan reaper reaped nothing there; found on Fly run 1 and fixed
  before the merge.
- **3d:** the agreed tie-break starved a newcomer at one slot (now "served
  least recently"); the monitor keyword `"stale": false` could never match
  the compact JSON the API sends.
- **3e:** Better Stack's free plan is "for personal projects"; dispatched
  documents could cycle unclaimed with no alert; the documents worker's
  slots are shared with every other task, which set the unclaimed limit at
  20 minutes; `create_logins.py` called a method psycopg's cursor doesn't
  have, and every CI job stopped there until it was fixed.
- **At the 3e merge:** the backup checks' rule "suites green on `main`"
  became impossible once `main` needed `0036`; decided: suites at
  `f191e27` (RUNBOOK 1.3).
- **At the drills after the first worker deploy (2026-10-08 and
  2026-10-09; D-197):**
  - a document re-read after a lost call showed only the re-read's cost
    ($0.0120 against runs summing to $0.0142). Fixed by PR #54: the
    document's cost is now the sum of its finished runs (D-163's
    addendum); no backfill;
  - for about 10 s after every worker start each Celery worker receives
    nothing; the fix to evaluate is its own PR, not built;
  - the 18 unacknowledged staging alerts were triaged: test leftovers,
    alerts raised by design, two tied to the 5.7 walkthrough, the drills'
    own, and one (`intake_webhook_refused`, 2026-09-27) closed as most
    likely a draft run before `88e8cd8`;
  - by the code, the `tenant_ready_to_delete` alert returns within five
    minutes of each acknowledgement; not yet seen.
- **While drafting this:** the Phase 6 item on plan changes still said to
  lower the idle-transaction cap "for `docflow_app` ... plus the CI role
  script and its agreement test". After 3e the cap is set on the four logins
  in `0036`, and the CI role script no longer exists. Corrected in
  BUILD-STATUS in the same commit as this draft.

### Open, carried forward

- **Stage 4:** matching speed (`pg_trgm`, measured at 50k items) and the
  broad-`except` audit. Design approved with changes (founder,
  2026-10-02; Q1-Q15 decided; two PRs, B first; the new catalog entries
  final);
  building waits for this checkpoint's "go".
- **Stage 5:** as listed in BUILD-STATUS (test hygiene, the D-178
  audit-trail findings, the stranded test data, M7, M10, M11).
- **Phase 6:** narrow `docflow_stripe`'s grants; production's polled
  `/healthz` check; plan changes outside the transaction; the Sentry
  scrubber; production's pool size.
- **A representative cost sample** (scans, images, spreadsheets, long
  orders): still not measured. The 500 + 1 run's documents will be the
  first such sample, if its mix includes them.

### For Stage 4: AI cost per document, and whether the database holds the catalog (founder, 2026-10-05)

**AI cost per document.** `[GAP: mean, median and maximum est_cost_usd of
the three live calls in this checkpoint's golden run, on the checkpoint
commit]`. Nothing is measured yet: the live run is part of the step 5 runs,
after the cutover. The last measurement is Stage 2's (2026-09-29, below):
$0.0141, $0.0177 and $0.0191, so mean $0.0170, median $0.0177, maximum
$0.0191 on `claude-sonnet-5`. Those are short text orders. Scans, images,
spreadsheets and long orders are still unmeasured (above).

**Does the database hold about 2.5 million catalog rows plus the trigram
indexes?** Nothing here is measured. Staging's `items` table holds 70 rows,
`pg_trgm` and `btree_gist` aren't installed (both are available), and the
design allows nothing of Stage 4 on staging before `docflow_app` is
dropped. What follows is an estimate from the table's columns and the two
indexes the design names (description, and the stripped SKU), with the
plan figures read from Supabase's pricing and compute pages on 2026-10-05.

| | Estimate at 2.5 million rows |
|---|---|
| Table rows | 0.4 to 1.1 GB. The spread is `raw_data`, the uploaded row kept as JSON, whose size depends on the customer's file |
| The four existing indexes | 0.3 to 0.5 GB |
| The two trigram indexes | 0.5 to 1.5 GB. This is the least certain line |
| **Total** | **about 1.5 to 3 GB** |

| Compute | Plan | Price | Memory | Database size | Holds 2.5 million rows? |
|---|---|---|---|---|---|
| **Nano (staging today)** | Free only | $0 | up to 0.5 GB | 500 MB, then the project goes read-only | **No.** The lowest estimate is three times the limit |
| Micro | Paid plans | about $10/month, covered by the Pro plan's $10 compute credit | 1 GB | 8 GB disk included on Pro, then $0.125 per GB | **Fits on disk, by estimate. Speed unknown:** the indexes are about as large as the memory or larger |
| Small | Paid plans | about $15/month ($5 after the credit) | 2 GB | the same disk | Fits on disk; more of the indexes in memory. Speed unmeasured |

- **The upgrade price:** the Pro plan is $25 a month for the organisation.
  It includes $10 of compute credit, which covers one Micro project. A
  second project on Micro (production, in Phase 6) adds about $10 a month.
- **Two facts in the records that the question doesn't match:**
  1. **Staging is Nano on the free plan, not Micro** (founder, from the
     dashboard, 2026-09-29; D-155: "Staging stays free tier"). The
     database is 23.4 MB today.
  2. **No 2.5 million row run is in the approved Stage 4 design.** D-155
     (2026-09-25) says the 2.5 million row catalog is seeded in the CI
     database. The design approved on 2026-10-02 times 250,000 rows on
     staging and checks parity in CI at 50,000. It doesn't mention D-155 or
     2.5 million rows.
- **A risk to the approved staging run.** By the same estimate, 250,000
  rows take 150 to 300 MB on a database with a 500 MB limit that turns
  read-only when passed.
- **Decided (founder, 2026-10-05; D-186):**
  1. **Staging stays on Nano for Stage 4.** Production's compute is a
     Phase 6 decision from measured numbers.
  2. **D-155's CI seed is replaced by one manually triggered CI job**
     that seeds 2.5 million rows with the trigram indexes and prints
     table size, index sizes and query timings. It runs before the
     staging run, and replaces the estimate above with a measurement.
  3. **The staging benchmark guards the database's size:** it refuses
     to start if the current size plus the projected 250,000-row size
     is over 350 MB, reports the size afterwards, and removes its rows
     at the end of the run on the founder's typed OK, with a second
     size read.

  The detail, and what was found while recording it, is in
  BUILD-STATUS (Stage 4 design, "Decided (founder, 2026-10-05)").
- **A limit the founder named:** Stage 2's cost figures, and the golden
  set, are short text orders. With no long or scanned order in the
  sample, the margin will look better than it is.

### Decided (founder, 2026-10-02, on this draft)

1. **The gate split: the checkpoint waits for the first worker deploy**, as
   proposed. Closing H4 and H5 on CI evidence alone breaks the
   real-system rule. And Stage 4's part B edits the parse service
   (`conversion.py`, `documents.py`, `tables.py`): if it landed before G
   and A4, Stage 3's real-system proof would run against Stage 4's parse
   code, and neither stage's evidence would be clean. **The worker deploy
   is scheduled straight after the cutover is verified**, not after the
   `docflow_app` drop, **and only if verification turned up nothing at
   all** (founder, 2026-10-02; RUNBOOK 10.2's stop condition).
2. **The live golden run stays** in the checkpoint's runs: about $0.05 for
   the only end-to-end run against the real model.

---

## Phase 5.5 Stage 2 — Security and lifecycle — COMPLETE (2026-09-29), go given

Built as 2a (PR #14), 2b (PR #15), 2c (PR #18, migration `0029` applied and
verified on `docflow-staging` 2026-09-28) and 2d (PR #20), with the MFA
follow-ups #21 and #22, the audit-findings design (#23) and the D-170 clock
PR (#24). The founder's go for Stage 3 came on 2026-09-29, before this entry
was written. The suite run and this record were missing and are made up here.
Checkpoint run on `main` at `5db7762`.

### Test runs

On staging, one at a time (RUNBOOK 1.4), as pytest printed them:

- **API:** `517 passed, 3 deselected, 579 warnings in 2029.53s (0:33:49)`.
  The 3 deselected are the `live_api` tests below.
- **Worker:** `109 passed, 4 warnings in 601.49s (0:10:01)`.
- **Live** (`pytest -m live_api`, real calls to `claude-sonnet-5`):
  - **First run, 10:18: `3 failed, 517 deselected, 1 warning in 18.41s`**,
    and a re-run straight after failed the same way (`3 failed ... in
    17.52s`). Every call got `InternalServerError` (HTTP 500) from
    Anthropic's API within 3-4 s, after the SDK's retries. No assertion on
    extracted values ran.
  - At 11:41 a diagnostic showed the API healthy: the golden call succeeded,
    and so did a one-word request to each of the two models.
  - **Run of record, 11:41: `3 passed, 517 deselected, 1 warning in 27.08s`:**
    - `test_live_extraction_matches_section_8_3` (golden)
    - `test_live_golden_fixture_still_extracts_exactly_with_examples`
    - `test_live_contamination_no_example_value_appears`

  The failure is recorded, not treated as flaky: a provider outage fails the
  golden run, and the checkpoint waits for a pass.
  - The failures match Anthropic's incident "Elevated errors on claude.ai,
    Claude Code, Claude Cowork and the Claude API" (status.claude.com: first
    posted 14:21 UTC, services normal from 14:59 UTC, **resolved 16:27
    UTC**). The founder asked for a re-run once it was resolved, before the
    checkpoint was pushed.
  - **Re-run after resolution, 16:28 UTC: `3 passed, 517 deselected, 1
    warning in 26.36s`**, with the live tests now printing each call's cost:

    | Test | Input tokens | Output tokens | Est. cost |
    |---|---|---|---|
    | golden | 2,621 | 888 | $0.0141 |
    | golden with examples | 5,218 (2,604 of them examples) | 731 | $0.0177 |
    | contamination | 5,156 (2,604 of them examples) | 883 | $0.0191 |

    All three on `claude-sonnet-5`; $0.051 for the run.

Also:

- **Core** (no database): `552 passed in 22.70s`.
- **CI on `5db7762`:** core, api, web, web-live and worker all green.
- **Warnings:** the API suite's count rose from 443 (Stage 1) to 579 as tests
  were added. D-163's triage found them all test-only (PyJWT's short-key
  warning). Clearing them is the Stage 5 item "use a test JWT secret of at
  least 32 bytes".

No failures and no skips in the runs of record.

### Verdicts

| Finding | Verdict | Evidence |
|---|---|---|
| **H8** Inbound mail was admitted on the intake address alone | **Closed** | Postmark's HTTP Basic credentials checked before the payload is parsed or the token resolved; a blank credential refuses everything (D-171). API `test_email_intake_auth.py` (12 tests): `test_the_intake_address_alone_no_longer_admits_anything`, `test_a_forged_authentication_results_header_never_gets_that_far`, `test_a_wrong_password_is_refused`, `test_the_right_credentials_are_processed_as_before`. A refusal raises a high-severity `intake_webhook_refused` alert: `test_intake_refusal_alert.py` (7 tests), `test_a_refused_request_raises_one_high_severity_alert_with_no_tenant`, `test_the_refusal_session_reads_nothing`. |
| **H10** A suspended tenant could still upload | **Closed** | One predicate, `intake_gate.blocks_new_intake`, for both intake channels; INT-010 before the file is validated or stored (D-172). API `test_lifecycle_intake_gate.py` (8 tests): `test_a_blocked_tenant_cannot_upload`, `test_the_refusal_is_not_a_404`, `test_an_active_or_cancelling_tenant_uploads_normally`, `test_a_blocked_tenant_can_still_read_and_export`. |
| **H11** A Stripe event could be lost, reordered or suppressed | **Closed**, one named residual risk | `record_stripe_subscription_event()` records the event id in the same transaction as the status write, applies the ordering guard and cross-checks the customer (D-173, D-175, D-176). API `test_stripe_webhook_api.py` (22 tests): `test_the_event_id_is_recorded_in_the_same_transaction_as_the_status_write`, `test_an_event_older_than_the_saved_state_is_recorded_and_not_applied`, `test_a_same_second_event_fetches_stripes_state_with_no_transaction_holding_the_row`, `test_a_newer_event_saved_during_the_fetch_is_not_overwritten`, `test_a_crash_between_the_fetch_and_the_save_records_nothing_and_a_replay_applies`, `test_no_session_can_insert_into_stripe_webhook_events_directly`, `test_the_function_refuses_a_customer_that_is_not_the_session_tenants`. **Residual risk (D-173):** code running as `docflow_app` can call the function with a made-up event id. Closed by F-1 in Stage 3e. |
| **H9** The Console had no MFA or step-up | **Closed**; enforcement on | aal2 for the whole Console (AUTH-006); a TOTP challenge under 5 minutes old for the seven destructive actions (AUTH-007); two lost-device drills passed on staging (D-177, D-178). API `test_console_mfa.py` (14 tests): `test_exactly_the_seven_destructive_routes_require_a_recent_code`, `test_a_platform_admin_without_an_authenticator_code_gets_auth_006`, `test_anyone_else_still_gets_a_404_not_an_mfa_answer`. `CONSOLE_MFA_ENFORCED` is on for the local API. |
| **D-170 clock items** (#3 and #6 in 2c; #1, #2, #4, #5 and #7 in PR #24) | **Closed** | Stripe's clock has one named tolerance of 300 s, and the database writes what it later compares. API `test_a_signature_stamped_more_than_300_seconds_ago_is_refused`, `..._in_the_future_is_refused`, `test_the_cure_clock_starts_at_stripes_event_time_and_unpaid_does_not_reset_it`, `test_d170_clock.py` (3 tests); core `test_one_clock.py`, the CI check that a database-access module may not pass the app's clock into SQL. |

### Measured AI cost per document (founder's question, 2026-09-29)

From staging's cost record: `documents.est_cost_usd`, plus one
`extraction_runs` row per paid call since D-142. These are estimates (token
counts x the price table in `extraction.py`), not invoiced amounts. The price
table was checked on 2026-09-29 against the claude-api skill's model table:
`claude-sonnet-5` $2 / $10, `claude-haiku-4-5` $1 / $5 per million tokens.

| Set | Documents | Mean | Median | Max |
|---|---|---|---|---|
| All | 18 | $0.0138 | $0.0135 | $0.0189 |
| `.txt` | 16 | $0.0138 | $0.0137 | $0.0189 |
| `.docx` | 2 | $0.0131 | $0.0131 | $0.0132 |
| With examples (routing + 3 examples) | 2 | $0.0187 | $0.0187 | $0.0189 |
| Without examples | 16 | $0.0131 | $0.0131 | $0.0144 |

- **Models:** every extraction was `claude-sonnet-5`. The two routing calls
  were `claude-haiku-4-5` at $0.0009 each; each is included in its
  document's total.
- **Retries and verification passes:** no document had more than one
  extraction call on record. Secondary-model verification is deferred
  (Section 3), and the second pass with examples was left out by founder
  decision (D-141). Retries inside the SDK after a 500 or 429 are not billed
  and not visible to us. Before D-163 (2026-09-26), a failed call's cost
  could go unrecorded.
- **Limits of this sample:** all 18 are short text orders, one page with 2-4
  lines. **Staging holds no costed scanned PDF, image, spreadsheet or
  multi-page TIFF**, so there is no split by page count and nothing on
  visual reads. Long orders were measured in D-161: 300 lines $0.41, 600
  lines $0.81.
- The post-resolution golden run measured the same range: $0.0141 without
  examples, $0.0177 and $0.0191 with them (the table under Test runs). The
  live tests print each call's cost from now on.

### Open, carried forward

- **Stage 3** (design agreed 2026-09-29, `docs/BUILD-STATUS.md`): H5, H6, H4
  fairness, F-1. D-163's run row before the model call moved into 3c.
- **Stage 4:** matching speed, and the audit of the ~23 broad `except`
  blocks on the document path (founder, 2026-09-29).
- **Stage 5:** the test-hygiene items, the audit-trail findings (D-178), the
  stranded test data, and the typed note on high-severity acknowledgements
  (agreed 2026-09-27, not yet given a stage).
- **A representative cost sample** (scans, images, spreadsheets, long
  orders): not measured; it needs paid runs.

---

## Phase 5.5 Stage 1 — Data integrity — COMPLETE (2026-09-26), go given

Built as 1a (PR #4), 1b (PR #6), 1c (PR #7) and the named system actors
(PR #8). Migrations `0026`, `0027` and `0028` applied and verified on
`docflow-staging`. Checkpoint run on `main` at `b04f16d`.

### Test runs

On staging, one at a time (D-160), as pytest printed them:

- **API:** `413 passed, 3 deselected, 443 warnings in 1690.85s (0:28:10)`. The 3 deselected are the `live_api` tests below.
- **Worker:** `107 passed, 4 warnings in 542.62s (0:09:02)`.
- **Live** (`pytest -m live_api`, real calls to `claude-sonnet-5`): `3 passed, 8 deselected, 1 warning in 21.21s`.
  - `test_live_extraction_matches_section_8_3` (golden)
  - `test_live_golden_fixture_still_extracts_exactly_with_examples`
  - `test_live_contamination_no_example_value_appears`

Also:

- **Core** (no database): `542 passed in 5.51s`.
- **CI on `b04f16d`:** web, core, worker and api all green.

No failures and no skips in any run.

### Verdicts

| Finding | Verdict | Evidence |
|---|---|---|
| **C1** The database rounded money and quantities | **Closed** | Migration 0026 makes the four columns plain `numeric` (D-154). Worker `test_numeric_fidelity_db.py`: `test_C1_worker_stores_extracted_numbers_exactly`, `test_C1_human_edit_is_stored_exactly`, `test_C1_every_export_matches_raw_json_for_unedited_fields`, `test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` (random decimals from model to every export, compared with the original strings), `test_C1_numeric_columns_are_unconstrained_so_postgres_cannot_round`. Core `test_numeric_fidelity.py` (no exponents, exact checks, VAL-015 warns and never rounds). |
| **H1** "Needs review" before the checks ran; failures silent | **Closed** | Validation and the move to review are one transaction; a failed step is VAL-016 plus an alert (D-158). Worker `test_pipeline_integrity_db.py`: `test_H1_an_order_is_still_processing_while_its_checks_run`, `test_H1_a_validation_failure_fails_the_order_with_a_code_and_alerts`, `test_H1_a_step_that_did_not_finish_is_an_explicit_warning_and_an_alert`. |
| **H3** Redelivery could knock an approved order back | **Closed** | State machine enforced by a database trigger (0027); claim-first, resumable worker (D-158). Worker: `test_H3_a_redelivered_job_for_an_approved_order_changes_nothing`, `test_H3_a_duplicate_delivery_while_another_worker_is_on_it_is_a_no_op`, `test_H3_a_worker_killed_mid_job_is_resumed_without_a_second_model_call` (SIGKILLs a real process), `test_H3_the_database_refuses_to_move_an_approved_order_back_to_processing`, `test_H3_the_state_machine_in_code_is_the_one_in_the_database`, plus the stuck-document tests. Core `test_document_status.py` guards status writes outside `document_status`. The whole read (retries included) ends inside the 30-minute stuck timeout: `test_M1_the_whole_read_ends_well_inside_the_stuck_timeout`. |
| **M1** Orders over ~50–60 lines always failed | **Closed** up to about 1,000 lines | Streamed call, `max_tokens` 128,000, measured 80 / 300 / 600 lines read exactly (D-161). Core `test_extraction_streaming.py` (10 `test_M1_*`); worker `test_M1_a_100_line_order_is_stored_whole`, `test_M1_a_truncated_answer_fails_with_DOC_020_and_keeps_nothing`, `test_M1_the_read_deadline_is_set_at_the_claim`. **Accepted limit (founder, D-163):** past about 1,000 lines the order ends as DOC-020 after up to the 20-minute read, not immediately; the RUNBOOK onboarding checklist checks the largest sample order. Chunking stays deferred. |
| **M3** A bad confidence or a failed save stranded the order | **Closed** | Out-of-range confidence becomes 0 (D-154); a failed save is DOC-021 with the answer kept (D-158). Core `test_M3_an_out_of_range_confidence_is_treated_as_no_confidence`, `test_M3_an_in_range_confidence_is_untouched`; worker `test_M3_a_failure_saving_the_answer_ends_in_failed_not_stranded`. |
| **H2** A human edit was never re-validated | **Closed** | `apply_edits` re-validates in the same transaction; approval re-checks first (D-162). API `test_review_integrity.py`: `test_H2_a_mistyped_price_raises_a_warning_and_blocks_approval`, `test_H2_fixing_the_value_resolves_its_warning`, `test_H2_approval_rechecks_and_saves_a_warning_it_finds_even_though_it_refuses`, `test_H2_an_edit_through_the_core_function_rechecks_too`. |
| **M4** Acknowledgement text came from the browser | **Closed** | The server writes the catalog wording plus the stored values; the body carries only a warning id and a note (D-162). API `test_M4_the_acknowledgement_text_is_the_catalog_wording_not_what_the_client_sent`, `test_M4_an_acknowledgement_of_a_warning_not_on_this_order_is_refused`, `test_M5_M4_the_core_acknowledgement_carries_only_an_id_and_a_note`. |
| **M5** Approval not tied to what the reviewer saw | **Closed** | `expected_version` required on approve, row lock in the transaction (D-162); a dead connection's lock is now released within 5 minutes (0028, D-165). API `test_M5_approving_values_someone_changed_since_you_loaded_them_is_refused`, `test_M5_the_approve_route_requires_the_version_token`, `test_M5_a_stale_version_is_refused_by_the_core_function`, `test_M5_an_approval_in_progress_holds_the_row_so_an_edit_waits`; `test_system_actors.py::test_the_app_roles_idle_transactions_are_capped_at_five_minutes`. Browser `e2e/review.spec.ts` refuses an approval without the screen's version. |

### Also done in Stage 1

- **Golden fixture renamed** to fake names (D-159).
- **Every paid call has a cost record** (D-163).
- **The Audit tab runs on one clock** (D-164).
- **Named system actors:** no blank actor in the lifecycle log; the app role can't touch the actors (D-165).

### Open, carried forward (not part of these findings)

- **Stage 3:**
  - A run row is written only after the model answers, so a worker killed mid-call leaves that call's cost unrecorded (D-163).
  - F-1, separate database logins.
- **Phase 6, early:** plan changes must stop holding a transaction across Stripe calls; then revisit the 5-minute cap.
- **Stage 5:** the test-hygiene items and the stranded test tenants in `docs/BUILD-STATUS.md`.
- **IIF's QuickBooks limits** stay a warning (EXP-008) until a real import decides (D-157, UAT TC-26).

---

## Phase 5 — Founder Console, tenant surface, operations — COMPLETE (2026-09-25), go given

Built as ten slices from 18 to 25 Sept 2026, each walked by the founder
before the next began. Slice-by-slice detail is in `docs/BUILD-STATUS.md`;
the reasoning is in `DECISIONS.md` D-102 – D-142.

### Exit criteria

| Criterion (Section 6) | Result |
|---|---|
| The founder runs all nine steps of the onboarding process against a fake prospect from the Console, ending with a live tenant whose owner can sign in by invite and see their reviewed test batch | **Met in 5.3** (18 Sept): Acme Test Prospect went from intake to live entirely from the Console. Stripe test-mode subscription, setup fee on the first invoice, founding price, first-week check-in scheduled. |
| The Console never calls a parsing, extraction or review function the tenant surface doesn't, **verified by a module-dependency check** | **Met, with a check added at this checkpoint** (`apps/api/tests/test_console_shared_paths.py`). Until today this rested on the design (D-111, D-112), not a check. The test confirms each of these from the code: <br>• the admin router imports no extraction, review, matching or validation module and no worker code <br>• it sends documents to extraction only through the tenant surface's own task <br>• its test-batch upload is the tenant's `ingest_upload` <br>• every acting-as route is served by the very same endpoint function as the tenant's route <br>• there is one catalog parser, used by the one catalog import |
| A full cancel → suspend → export window → reactivate cycle, and a full cancel → delete cycle, work end to end in staging for each of the three cancellation reasons, with the correct effective date | **Met by the lifecycle tests against the staging database** (effective date for each reason, including the non-payment fallback; suspend sweep; reactivation with no data loss; typed-name delete, purge order across every foreign key). **Walked in the browser:** cancel → suspend → reactivate (23 Sept), and a real typed-name delete (Acme Test Lifecycle B, 24 Sept, which found D-133). The browser walk used one reason, not all three; the other two rest on the tests. |
| The dashboard's KPI cards match a hand-computed query on the same staging data | **Met in 5.5**: every card is checked against independent hand-written SQL (D-121). |
| With example prompting on for a test buyer with 10+ approved documents, the golden fixture still extracts exactly, and the contamination test passes live | **Met today** (details below). |

**Example prompting, the last criterion:**
- **Live model tests:** golden fixture without examples, golden fixture with examples, contamination test. All three passed.
- **Real-stack drive** on Acme Test Prospect:
  - Ten fictional Bella's Coffee House orders went through the real worker task and were approved by a real tenant user with `approve_document` (`scripts/seed_example_history.py`).
  - The feature was turned on, then `docs/sample_po.txt` was put through the real worker as an upload with no sender.
  - The Haiku routing read found the buyer from the header, and Sonnet read the order with 3 examples (2,704 example tokens).
  - Every value came back exactly the golden fixture's.
  - Two runs were recorded. The order's cost ($0.0189) is the extraction ($0.0180) plus the look-up ($0.0009).

### Verification at the checkpoint

| Suite | Result |
|---|---|
| `packages/core` | 443 passed |
| `apps/api` | **385 passing, 0 skipped.** The full run (23 min against staging): 366 passed, 10 skipped because it began before migration 0025 was applied. Those 10, plus the 9 tests added afterwards (dependency check, Audit tab), then passed on their own, along with every file changed during the run. |
| `apps/worker` | 80 passed, 0 skipped |
| `apps/web` | 42 Vitest + 60 Playwright |
| Lint / typecheck | ruff clean (repo root and api); mypy clean (api, worker); ESLint and tsc clean |
| Live model tests | golden fixture, golden with examples, contamination: all passed against the real API |

Migrations `0011` – `0025` are applied to `docflow-staging`.

### What was built

- **Console foundations (5.1):** tiers table (versioned prices, never in code), email outbox, founder alerts, invites, intake staging.
- **Catalog and customer import (5.2):** one import path with preview, column mapping, a validation report with blockers, and diff commit (retire, never delete).
- **Test batch, acting-as review, go-live (5.3):**
  - Steps 6–9 as screens.
  - The tenant's review and export routes are mounted a second time behind an audited admin gate.
  - Stripe subscription invoiced by email; scheduled jobs are table rows.
  - Deal terms and setup-fee presets.
- **Operator screens (5.4):** buyer merge becomes a `buyer_alias` rule; learned-rule management; per-tenant field settings (required, optional or hidden, versioned).
- **Founder dashboard (5.5):** attention panel, health strip, tenant list and KPI cards, all from a nightly rollup.
- **Lifecycle (5.6):** cancel with a reason-based effective date, suspend sweep, reactivate, wind-down and ready-to-delete queues, typed-name hard delete, Stripe webhook sync, 7-day billing trial.
- **Allowances and quarantine (5.7):** metering, banners and emails, abuse ceilings, the daily AI-cost breaker, intake abuse layers, quarantine screens.
- **Tenant surface (5.8):** upload page, dashboard, activity page, needs-review digest email, team page, role audit.
- **Billing (5.9):** plan change from the Console, Billing card, MRR counted as what customers actually pay.
- **Approved-example prompting (5.10, D-141):**
  - A per-tenant switch that needs a golden-run confirmation.
  - Buyer pre-identification from the sender's email or domain, else a Haiku header read.
  - Up to 3 of the buyer's newest approved orders as text plus values. The parser's text is now stored per order.
  - The contamination test, recorded and live.
  - A "read with N earlier orders" note for reviewers.
  - Cost tracked separately in the Console.
- **The tenant page's Audit tab (D-143),** found missing at this checkpoint and built into 5.10 at the founder's request: lifecycle events and Console actions, newest first, with page views on request.

### What was assumed

- **Only two roles are handed out** (founder decision, 5.8a): owner (shown as Admin) and reviewer. Viewer stays in the schema.
- **The allowance banner waits until 90%** (D-129), a deliberate deviation from 7.16.1's 80%; the 80% and 100% emails are unchanged.
- **`CURE_PERIOD_DAYS` = 0** (D-125): Net-15 invoice terms are themselves the grace period; suspension is always the founder's decision.
- **Test-batch orders count towards a buyer's 10 approved orders** for example prompting; the founder approved them in the normal review screen (D-141).
- **No second extraction pass with examples** (founder, D-141).
- **Example prompting is off for every tenant.** It was switched on for Acme Test Prospect for the drive and left on there; it is test data.

### What is open

- **Before the first real customer:**
  - an email provider (until then every invite, notice and digest waits in the Console outbox)
  - the Stripe setting that auto-cancels a subscription after 90 days unpaid (D-125)
  - `RUNBOOK.md` (Phase 6)
- **Stranded test data:**
  - Interrupted test runs (19–24 Sept) left five `console-…@example.com` platform admins. With the founder's OK they were **revoked and deactivated on 2026-09-25**, not deleted: two of them are named as the actor on the `created` events of leftover test tenants, and deleting them would rewrite that history. The founder is now the only active platform admin.
  - The same runs left three "Acme Test Distributor" tenants (two active, one in wind-down). All staging data is test data; the founder will clean up in a QA pass after all phases are built.
  - The test helper that strands them (`_Console` cleanup when a lifecycle event names its user) should be fixed in Phase 6.
- **Carried from before:** IIF against real QuickBooks Desktop; the stuck-in-processing alert (7.9, Phase 6); custom per-tenant fields (D-120); per-person digest opt-out; " -- " versus real dashes in catalog text; pending document updates listed in BUILD-STATUS.
- **Orders approved before 2026-09-25 have no stored text,** so they count towards a buyer's 10 but can't be shown as examples.

### Found at this checkpoint, not by the tests

- **The Audit tab 7.15.3 lists had never been built**; found while writing the walkthrough, built now (D-143).

- **The daily AI-cost breaker could never trip** (D-142). It and the health strip's model counts read `extraction_runs`, which nothing wrote to. Its test inserted rows by hand. Every model call is recorded now.
- **The Phase 5 dependency criterion had no check.** It held by design but nothing verified it; `test_console_shared_paths.py` does now.
- **The two runs of one order sorted in the wrong order** on the real drive: same-transaction `now()`, the D-123 trap again. Fixed with `clock_timestamp()` and a regression test.

---

## Phase 4 — Export — COMPLETE (2026-09-18), go given

### Exit criteria, both met

| Criterion (Section 6) | Result |
|---|---|
| Round-trip: the exported file, parsed back, equals the approved data exactly | **Passed, all four formats** — in core against hostile values (formula text, quotes, commas, line breaks, non-English text), and end to end through the API against the real database, comparing the downloaded bytes with the stored snapshot. IIF compares every field it carries and asserts its omissions are exactly the documented set (D-099). |
| Exporting twice produces byte-identical files | **Passed** — rendered twice in-process, pinned SHA-256 digests per format (stable across processes, Python environments and time), and two real exports of one snapshot record the same checksum. |

Both checks also run **at runtime** on every export: a file that is not
byte-identical on a second rendering, or does not parse back to the snapshot,
is never stored or offered (`EXP-004`).

### Verification at the checkpoint

| Suite | Result |
|---|---|
| `packages/core` | 282 passed |
| `apps/api` | 159 passed, 0 skipped (17 new export tests against the real database and RLS) |
| `apps/worker` | 72 passed, 0 skipped |
| `apps/web` | 23 Vitest + 14 Playwright |
| Lint / typecheck | clean in all four projects |
| Live golden fixture | passed against the real Anthropic API |
| Real browser, real stack | signed in, approved an order, downloaded CSV, Excel, JSON and IIF through API → Redis → worker → storage → signed link; status became "Exported to file" |
| Excel file in real office software | opened in LibreOffice: formula text stays text, amounts keep their exact decimals |

Migration `0010` is applied to `docflow-staging`.

### What was built

- **`exports` table** (0010): one row per export from the click, pinned to
  the exact approved snapshot, `pending → ready | failed` with a catalog
  code; finished rows made immutable by a trigger. RLS from creation.
- **`docflow_core/exports.py`** — pure: approved snapshot in, verified bytes
  out, for CSV, Excel, JSON and QuickBooks IIF (Estimate). No database access.
- **`docflow_core/export_jobs.py`** — records requests (API) and produces
  files (worker). The web process never imports the file-building module
  (enforced by the parsing-boundary test).
- **Worker task** `docflow.generate_export`; **API** routes to request, list,
  poll and download; downloads via purpose-bound short-lived signed links.
- **Export panel** on the review screen: four buttons, automatic download,
  per-order history with "Earlier approval" marking, catalog-coded errors.
- **Catalog SKU frozen into the approval snapshot** (D-099), so an export
  carries the tenant's own SKU as it was when the order was approved.
- Seven `EXP-0xx` catalog entries.

### What was assumed

- **D-099 IIF details** — account names `Estimates` / `Sales`, that an unknown
  customer `NAME` is created rather than refused, and that catalog SKUs match
  QuickBooks item names. Constants in one place; unverifiable without
  QuickBooks Desktop.
- **D-100** — viewers may export (the catalog already promised it).
- **One PO per file** — the founder's choice; batch export deferred.

### What is open

- **IIF against real QuickBooks Desktop (UAT TC-26).** The founder is finding
  someone with QuickBooks Desktop to try an import.
- **EXP-004 founder alert** — logged at error level until `founder_alerts`
  exists (Phase 5, 7.15.3), then wired to it (D-098).
- **Orders approved before today** export with an empty catalog SKU until
  re-approved (D-099).
- ~~"One-click Approve & Export"~~ **Done** after the checkpoint (D-101): a
  button beside Approve with a remembered format; verified on the real stack.
- **An IIF file made before the order-date rule** (the test order
  `e2e-po.docx`, BCH-2291) remains in that order's history; finished exports
  are permanent records by design.

### Found by driving the real app, not by the tests

An order with **no order date** produced an IIF file with a blank DATE, which
QuickBooks would reject. Every test passed, because no test fixture lacked a
date. IIF now refuses such an order with `EXP-006` and says to add the date
or export CSV/Excel. The Phase 3 lesson held again: drive the real thing.

### Found by CI after the checkpoint commit

The pinned Excel digest failed on CI's Linux runner: Python's zipfile
stamps each entry with the OS that wrote it, so the same approved order gave
different .xlsx bytes on Windows and Linux -- a real break of "same snapshot,
byte-identical file" once the worker runs on a Linux server. Fixed in
`3d413b7` (the field is pinned; a test builds every format as Windows, Linux
and macOS against one set of digests). CI green; core 282 passed.

---

## Phase 3 — Human review UI — COMPLETE (2026-09-17)

### Exit criteria, both met

| Criterion (Section 6) | Result |
|---|---|
| A non-technical person corrects and approves the golden fixture in under 2 minutes | **Passed.** Run by a tester who had not seen the product before. |
| The audit trail shows exactly what changed | **Passed.** The trail names each field with its before and after value. |

### Verification at the checkpoint

| Suite | Result |
|---|---|
| `packages/core` | 228 passed |
| `apps/api` | 141 passed, 0 skipped |
| `apps/worker` | 56 passed, 1 skipped (LibreOffice absent) |
| `apps/web` | 23 Vitest + 8 Playwright |
| Lint / typecheck | clean in all four projects |
| Live golden fixture | passed against the real Anthropic API |
| CI | green (first time in the project's history — see D-087) |

Migrations `0007`, `0008` and `0009` are applied to `docflow-staging`.

### What was built

**Slice 1 — review core** (`6705d6f`). `review_actions` and
`document_snapshots` tables; approval columns on `documents`; the
`documents_approved_is_attributable` CHECK; `docflow_core/review.py` with
edit / approve / reject / reopen, the editable-field allowlist, and snapshot
freezing. Five `REV-0xx` catalog entries.

**Slice 2 — review API** (`fcba9c0`). Nine endpoints over
`docflow_core.review`; first role enforcement in the codebase (`viewer`
reads, everyone else writes); `app/errors.py` as the single catalog-to-HTTP
renderer; signed short-lived URLs for the document viewer.

**Slice 3 — review UI** (`707f183`). Queue and review screens, sandboxed
document viewer, per-field confidence, editable header and lines, SKU search
with create-mapping, approve / reject, audit trail, keyboard shortcuts. First
tests `apps/web` has ever had, wired into CI.

**After the walkthrough** (`4732b44`, `8d3d5fb`, `a029cfe` and others):
everything the tester stumbled on, the document previews for formats no
browser renders, and the visual pass.

### What was assumed

- **Tolerances (D-073)** are judgement calls, not spec numbers. Tuned so a
  correct 40-line order produces zero warnings, accepting that a sub-cent
  per-line error goes unreported.
- **D-078** errs toward surfacing: an unknown buyer on either side still
  flags a change order. Nothing here auto-applies.
- **D-080** rounds *derived* money to two places for display. Extracted
  values are never re-scaled.
- **D-086** — the Playwright suite stubs the API at the network boundary.
  The API's own behaviour is proven against real Postgres separately.
- **D-092** — a preview is a convenience: a parser failure means no preview,
  never a failed document.

### What is open

- ~~Redis / Celery has never run end to end.~~ **Done** 2026-09-18
  (D-095): Memurai installed; a Word PO uploaded through the API went
  upload → Redis → worker → extraction → matching → validation →
  `needs_review` in 16 s, with its preview stored. The first real run found
  that the worker registered **no tasks** when started as documented, so
  every document would have sat in `pending` forever. Fixed and tested.
- ~~LibreOffice is not installed.~~ **Done** 2026-09-18 (D-094):
  installed, the gated `.doc` test now runs (worker: 0 skips), and it found
  that a corrupt `.doc` was "converted" into a document of garbage instead
  of failing. Fixed by pinning LibreOffice's Word import filter.
  **Still open from this item:** full-fidelity previews for Word/Excel
  (convert to PDF in the worker) -- now possible, not yet built:
  Section 7.11 already runs LibreOffice headless in the isolated
  worker for Tier 2 formats, and the same call converts `.docx`
  and `.xlsx` to PDF, which a browser renders with the layout intact. That
  replaces today's extracted-text preview for those formats with the real
  page. `.eml` / `.msg` stay as text -- an email body has little layout, and
  when a PO arrives by email the order is usually an attachment, which 7.11
  already unwraps and validates on its own. Ruled out permanently: a hosted
  conversion service (Section 7.10 and Section 10 forbid sending customer
  documents to third parties) and browser-side renderers such as SheetJS or
  docx-preview (that moves parsing of hostile files into the customer's
  browser, and 7.12 forbids rendering document-derived markup).
- ~~The line-items section is taller than it needs to be.~~ **Done** after
  the checkpoint (commit `1823147`): line items are now a table, one row per
  line, with matching state in a row beneath that opens whenever there is
  something to see.
- ~~The worker does not generate document previews.~~ **Done** after the
  checkpoint (D-093): the worker now writes a preview for every upload a
  browser cannot show, and in doing so closes two gaps the module had --
  `.msg`, `.doc`, `.xls`, `.odt` and `.ods` got no preview at all, and Word
  previews omitted tables, i.e. the line items.
- **Aesthetics** — the founder wants a further pass. The document viewer is
  explicitly liked and should be left alone.
- **Phase 5 asks already raised by the founder**, correctly scheduled and not
  built: a per-tenant dashboard (documents by status, recent activity) and
  the allowance banner (7.16.1). Phase 4 is export to downloadable files
  (CSV, Excel, JSON, IIF) -- not ERP writeback, which Section 3 rules out.

### The lesson of this phase, recorded because it cost the most

Three defects reached the end of Phase 3 having passed every suite: CI had
**never** been green (D-087), login had **never** worked on a real Supabase
project (D-088), and four more failures sat between the layers (D-089) —
missing browser configuration, no CORS, a viewer that could not
authenticate, and two headers that each independently stopped the document
rendering.

Every layer was well tested. The seams between them were tested nowhere, and
no test signs in and then looks around. The walkthrough found a dead-end
landing page in under a minute (D-090) that no amount of unit testing would
have surfaced.

**A green suite is evidence about the layer it covers, never about the
product.** Drive the real thing — real browser, real API, real database —
before calling a phase done.

---
