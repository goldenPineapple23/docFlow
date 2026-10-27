# DocFlow — Runbook

Step-by-step procedures for running DocFlow. Written for the founder: every
step says exactly what to click or paste, and what you should see. Phase 6
completes this file (restore drill, parser upgrades, constants); Phase 5.5
starts it with the procedures that were needed first.

---

## 1. Applying a database migration

Migrations are SQL files in `supabase/migrations/`, applied in number order,
never edited after they have been applied (build prompt Section 5). They are
applied by hand in the Supabase SQL Editor, because the app's own database
role deliberately cannot change the schema (DECISIONS.md D-013, D-017).

**Order of events for any pull request that needs a migration:**

1. Take the backup below, **before** anything else.
2. Check the row counts.
3. Apply the migration file to `docflow-staging`.
4. Tell Claude it is applied. **Do not merge the pull request until Claude has
   run the tests against staging and confirmed** (the PR message says so at the
   top).
5. Merge. Keep the backup until you decide to drop it.
6. From Phase 6 on: only after all of this on staging, apply the same file to
   `docflow-prod`, again with its own backup first.

**The one exception to backup-first (decided 2026-09-30, Stage 3b):** a
migration that **only creates Storage buckets** (inserts into
`storage.buckets`) and touches no existing table, policy or row skips steps
1-2. There is nothing in it to restore. A migration that does anything else
as well, even adding one policy, takes the backup as normal.

**A migration that changes policies or grants** (the pattern from `0036`,
founder, 2026-10-05). Rows have a backup; who-may-use-what has a snapshot
(`scripts/ci/policy_snapshot.py`: every RLS policy on `public` and the
EXECUTE grantees of every SECURITY DEFINER function). Such a migration
ships with three files beside it in `supabase/reverse/`:
- `NNNN_pre_snapshot.json`: staging before the migration, taken read-only;
- `NNNN_post_snapshot.json`: the state after it, as CI's database shows it
  through the same snapshot code;
- `NNNN_reverse.sql`, if the founder asked for a tested way back.

CI asserts both states on every push and prints their SHA-256s
(`scripts/ci/migration_roundtrip.py` for `0036`). **The post file comes
from CI, so CI agreeing with it shows only that it is pinned, not that it
is right:** a test holds it to the migration's own statements and to a list
a person wrote (for `0036`, `packages/core/tests/test_policy_snapshots.py`:
only the named policies changed, and the function grants equal
`FUNCTION_GRANTS`). At the cutover, the database's snapshot is compared
with the pre file before applying and with the post file after, by
SHA-256 (10.2 steps 1 and 5). Production compares with the same post file.

### 1.1 The standard backup (run before the migration)

Replace `NNNN` with the migration's number and list every table the migration
touches (the PR message names them). In the Supabase SQL Editor:

```sql
create schema if not exists backup_NNNN;

create table backup_NNNN.<table_one> as table public.<table_one>;
alter table backup_NNNN.<table_one> enable row level security;

create table backup_NNNN.<table_two> as table public.<table_two>;
alter table backup_NNNN.<table_two> enable row level security;
```

**Why RLS on a backup table:** Supabase's security advisor flags any table
without row-level security. Turning it on with no policies means only the
project owner (you, in the SQL Editor) can read it; the app and the public API
cannot. A backup holds customer data and gets exactly the same protection as
the table it copies.

### 1.2 The row-count check

```sql
select 'table_one' as t, (select count(*) from public.table_one) as live,
       (select count(*) from backup_NNNN.table_one) as backup
union all
select 'table_two', (select count(*) from public.table_two),
       (select count(*) from backup_NNNN.table_two);
```

`live` and `backup` must match on every row. If they don't, stop and say so;
don't run the migration.

### 1.3 Dropping a backup

Only when you decide to, never automatically, and never while the change it
protects is still being checked. When (founder, 2026-09-29):

- **Staging:** once the migration's PR has merged (the suites have passed on
  staging with the migration applied by then).
- **Production:** 14 days after the migration was applied there, if nothing
  about it has needed the backup.

Each drop is its own decision: look at what the schema holds first, then drop
that one schema. A backup holds customer data (in production), so it should
not outlive its purpose.

```sql
-- what it holds
select table_name from information_schema.tables where table_schema = 'backup_NNNN';

drop schema backup_NNNN cascade;
```

To list every backup still there:

```sql
select n.nspname as backup, string_agg(c.relname, ', ' order by c.relname) as tables
  from pg_namespace n left join pg_class c on c.relnamespace = n.oid and c.relkind = 'r'
 where n.nspname like 'backup%' group by n.nspname order by n.nspname;
```

**On `docflow-staging`:** the founder dropped `backup_0026` to
`backup_0032` on 2026-09-30, after the PR for `0032` (the card billing
follow-up, PR #29) merged. Since then:
- `backup_3b` (the 3b cutover's restore point: catalog_imports 22,
  documents 54, exports 11, onboarding_intake_files 2). **Dropped
  2026-10-09** by the founder, between 18:23:33 and 18:26:07 UTC, one
  `drop table backup_3b.<table>;` for each of the four tables and then
  `drop schema backup_3b;` (no `cascade`, as for the two below). Claude's
  read-only comparison of the two reads: the schema and its four tables
  gone, nothing else changed, and **no `backup_*` schema is left on
  `docflow-staging`**;
- **`backup_0034`** (documents 105, extraction_runs 17; live = backup),
  taken 2026-10-01 before `0034`. **Founder, 2026-10-01: dropped after 3c
  has run cleanly on staging for 3 days** -- 3c merged 2026-10-01 20:53 UTC,
  so not before 2026-10-04 20:53 UTC, and only after Claude reports the
  check (staging suites green at `f191e27`, see "Which code the checks
  run" below; no `document_failed`,
  `document_stuck`, `parse_service_unavailable` or `parse_seccomp_kill`
  alert on staging since the merge). The founder runs, in the SQL Editor on
  `docflow-staging` (no `cascade`, so nothing else can go with it):

  ```sql
  drop table backup_0034.documents;
  drop table backup_0034.extraction_runs;
  drop schema backup_0034;
  ```

  and the date is recorded here and in BUILD-STATUS.
  **Dropped 2026-10-09** by the founder with the commands above, between
  18:15:34 and 18:20:11 UTC. Claude's read-only comparison of the two reads
  (as `docflow_admin`): the schema and its two tables gone, and nothing
  else changed -- the other backups, every other schema's object counts,
  `public`'s columns, policies and grants, and the six `docflow*` roles.
- **`backup_0035`** (documents 105, founder_alerts 15, email_outbox 73;
  live = backup), taken 2026-10-01 before `0035`. **Founder, 2026-10-01:
  the same rule as `backup_0034`.** It is dropped after 3d has run cleanly
  on staging for 3 days, counted from the 3d merge. 3d merged 2026-10-02
  03:18 UTC (PR #33, `4a2b907`), so not before **2026-10-05 03:18 UTC**,
  and only after Claude reports the check:
  - the staging suites are green at `f191e27` (below);
  - no `document_failed`, `document_stuck`, `dispatcher_stopped` or
    `model_api_failure` alert on staging since the merge (DOC-024 arrives as
    `document_failed`).

  The founder runs, in the SQL Editor on `docflow-staging`:

  ```sql
  drop table backup_0035.documents;
  drop table backup_0035.founder_alerts;
  drop table backup_0035.email_outbox;
  drop schema backup_0035;
  ```

  The date is recorded here and in BUILD-STATUS.
  **Dropped 2026-10-09** by the founder with the commands above, between
  18:20:11 and 18:23:33 UTC. The same read-only comparison: the schema and
  its three tables gone, and nothing else changed (`backup_3b`, `public`
  and the six `docflow*` roles included).
- **Decided (founder, 2026-10-05): `backup_0034`, `backup_0035` and
  `backup_3b` are kept until the first worker deploy has processed real
  documents on staging.** This replaces "3 days after the merge" for
  the first two. The alert half of both checks came back empty on
  2026-10-05, and it proves nothing: the worker and API aren't
  deployed, so no document was processed on staging in either window.
  `backup_0034` and `backup_0035` together hold 0.44 MB, `backup_3b`
  0.16 MB (CHECKPOINTS.md, Stage 3). The drop commands above stay as
  written, for when the founder runs them.
- **Which code the checks run (founder, 2026-10-02, at the 3e merge):** both
  checks run the staging suites at **`f191e27`**, not at `main`. `main`
  holds 3e (merged 2026-10-02, PR #35), whose suites connect as the four
  logins and test `0036`, and `0036` isn't on staging until the cutover,
  which itself waits for the `backup_0035` check -- so `main`'s suites
  can't pass on staging before then. `f191e27` is the last `main` before
  3e; it differs from 3d's merge (`4a2b907`) only in `RUNBOOK.md` and
  `docs/BUILD-STATUS.md`, so it is exactly the code staging's `0035` schema
  expects. `main`'s suites run on staging for the first time at RUNBOOK
  10.2 step 5, after `0036` is applied. How Claude runs them:
  - in a **separate worktree** checked out at `f191e27`
    (`git worktree add <scratch dir> f191e27`), never by moving the main
    checkout back;
  - **connecting as `docflow_app`**, the login `f191e27` uses (the
    worktree's `.env` is a copy of the root `.env`, whose `DATABASE_URL` is
    `docflow_app`; the copy is deleted with the worktree);
  - with the existing venvs (no Python dependency changed after
    `f191e27`) and the worktree's `packages/core` first on `PYTHONPATH`,
    checked before each suite: `docflow_core.__file__` must be inside the
    worktree, or the run stops (the venvs' editable install points at the
    main checkout, which is 3e's code). **For the API and worker suites,
    `app.__file__` too** (founder, 2026-10-02): `app` isn't an installed
    package, so pytest finds it from the suite's own folder, and the check
    runs the same way, from the worktree's `apps/api` or `apps/worker`:
    `python -c "import app, docflow_core; print(app.__file__); print(docflow_core.__file__)"`.
    Both paths must be inside the worktree;
  - **the parse service is started by hand, from the worktree's code**
    (found 2026-10-05, at the first run). The worker suite starts its own
    dev parse service from `apps/parse/.venv` beside it, and a worktree has
    no `.venv` (it isn't in git), so without this the suite stops with "No
    parse service to test against". The worktree doesn't get a venv of its
    own: a new one under the scratch path runs into Windows' 260-character
    limit, and a link to the main checkout's venv could take that venv with
    it when the worktree is deleted. Instead, from the worktree's
    `apps/parse`, with the main checkout's `apps/parse/.venv` python and the
    same `PYTHONPATH`:
    - check first, the same way as above:
      `python -c "import parse_service, docflow_core; print(parse_service.__file__); print(docflow_core.__file__)"`.
      Both paths must be inside the worktree, or the run stops (the parse
      venv's editable installs point at the main checkout too);
    - **for the worker suite:** start it with `PARSE_ISOLATION=off`,
      `DOCFLOW_ENV=development`, a free `PORT`, and `PARSE_SERVICE_TOKEN`
      set to the test token in `apps/worker/tests/conftest.py`
      (`DEV_PARSE_TOKEN`); run the suite with `PARSE_SERVICE_URL` pointing
      at it and the same `PARSE_SERVICE_TOKEN`. That is the path CI's
      worker job takes (the suite uses the service it is given and starts
      none);
    - **for the API suite:** start it as SETUP.md step 7a does
      (`PARSE_ISOLATION=off`, no token, the default port 8100), as every
      earlier staging run of the API suite did;
    - wait for `/health` to answer before starting the suite, and say in
      the report that the service was started by hand;
    - stop it afterwards by its process, not from the shell that started
      it: Git Bash's `kill` leaves the two `python.exe -m
      parse_service.server` processes running and the port taken. In
      PowerShell, list `python.exe` processes with their command lines,
      stop those two, and check the port is free before the next suite;
  - core, worker, API, one at a time (1.4).

  The report names the commit (`f191e27`) and the login (`docflow_app`)
  beside each suite's own summary line.

`docflow-prod` doesn't exist yet (Phase 6), so it has no backups.

### 1.4 Running the test suites against staging

**Standing rule: one suite at a time, or core, worker and API together
through the runner.** Run core, worker, API and web against
`docflow-staging` one after another, or start core, worker and API together
with `scripts/run_suites_parallel.py` (below); never any other overlap. The
suites share one database. Several tests create and delete their own
tenants, and some check a count across the whole table, so one suite's test
data can fail another suite's test. This happened on 2026-09-26: the API
suite's `deal7` case failed while the worker suite was running (DECISIONS.md
D-160).

**Core, worker and API together, through the runner (D-211; the proof run
passed on 2026-10-19, D-212 item 6).** Serial runs
stay allowed and unchanged. From the repository root, in the same
environment a single staging suite's run would have (only the database and
Storage reachable, `DOCFLOW_STAGING_RUN=1`, the dev parse service up and
Memurai answering, as below):

```
apps/api/.venv/Scripts/python scripts/run_suites_parallel.py <a folder outside the repository>
```

It starts the three suites at once (`pytest -q -rs --durations=25`, one log
each in that folder) with `DOCFLOW_TEST_PARALLEL_RUN=1`, so their run locks
take the shared key in shared mode and nothing else can start meanwhile. It
reads the six leftover counts before it starts them and after the last one
ends, and fails on anything but `+0`; each suite's own leftover line then
reads `counted by the parallel runner`. It also prints, read-only, the
database's size before and after and the peak of its connections during the
run. **A failed parallel run is read before anything is re-run:** quote the
failed test and its cause from its log (1.6), and only then decide whether
to run again, in parallel or one at a time.

**No worker suite starts if it would run past 23:20Z (founder, 2026-10-19;
D-212 item 7),** alone or through the runner. The worker's
`test_time_limits.py::test_more_than_the_daily_threshold_of_unfinished_exports_raises_one_alert_that_day`
counts exports from UTC midnight and its alert is deduped per UTC day, so a
run that crosses 00:00Z (20:00 in New York in summer time, 19:00 in winter)
can fail at any speed. The worker suite takes about 40 minutes alone and 38
to 45 through the runner, so in practice none starts after about 22:35Z.
Read the UTC clock before starting one.

**Every staging run carries `DOCFLOW_STAGING_RUN=1` (D-211).** Each suite's
conftest refuses to start, before it opens any connection, if a database URL
points anywhere but `localhost`, `127.0.0.1` or `db.invalid`, unless the
process's own environment carries `DOCFLOW_STAGING_RUN=1` (never read from
`.env`). Set it only for a staging run the founder has granted. It replaces
the shell's block as the protection for the database: on 2026-10-19,
PowerShell's `$env:DATABASE_URL = ""` deleted the variable instead of
blanking it, the root `.env`'s staging URL was read, and two "blocked" local
runs took the run lock on staging. To run with no database, set each URL to
`postgresql://nobody:nothing@db.invalid:5432/none`, or start the run from Git
Bash, where `DATABASE_URL=` keeps an empty value.

**The API and worker suites enforce it (D-180; D-211).** Each run takes two
Postgres advisory locks when it starts and holds them until it ends: its
suite's own, and one every suite shares, which a normal run takes alone and
a run the parallel runner started takes in shared mode. A second run of a
suite, or any run that would overlap a normal one, stops at once, with exit
code 3 and a message naming the run that holds the lock:

```
Exit: Another test run is using this database: docflow-test-run api pid=23972 host=NKPC started=18:53:17Z.
```

Wait for that run to finish, or stop it, then start again. The lock goes with
its run: it is released at the end, and also when the run is killed (tested
three times against staging), so there is nothing to clear by hand. Collect-only
runs (`--co`) don't take it. It doesn't cover the web suites or scripts such as
`seed_live_e2e.py`: those still follow the rule by hand. This came from
2026-09-29, when two sessions ran the full API suite 76 seconds apart and each
deleted a tenant the other was counting.

The audit of the "count everything" tests was Stage 5's test-hygiene PR
(D-211): the counts that could move under another suite were replaced or
scoped, and the tests that touch the database's global state (the model
provider's row, the tenant-less provider and dispatcher alerts, the open
dispatch passes) hold a third lock, `shared_state`, so they never overlap.

**Before the API suite: start the dev parse service** (SETUP.md step 7a:
from `apps/parse`, `PARSE_ISOLATION=off`, no token, the default port 8100)
and wait for `http://127.0.0.1:8100/health` to answer. This holds in any
checkout, not only a worktree (1.3). The API suite's catalog imports call
it and the suite starts none; the worker suite starts its own, and core
needs none. Without it the suite still runs to the end, about 20 minutes
slower, and the import tests fail with
`parse_service_unavailable ... reason=no_connection` in their logs: on
2026-10-06 at `cfbc18c` that read `33 failed, 592 passed` in 1:04:31, and
`625 passed` in 45:32 once the service was up. Stop it afterwards by its
two processes and check the port is free (1.3). Say in the report that
the service was started by hand.

How a run is reported:

- Save each suite's full output to a file. Never cut it with `tail` or
  `head`: the exit code of the pipe replaces pytest's, so a failed run looks
  like it passed, and the failure details are lost.
- **A commit or a push never shares a command with the checks that gate
  it.** Run lint, the type check and the tests, read each one's own exit
  code, and only then commit and push, as a separate command. On
  2026-10-05 Claude pushed a script that didn't parse (`a810f92`): the
  checks had failed, but they were piped and chained in front of the push,
  so the push ran anyway. Fixed in the next commit; CI would have caught
  it, but only after it was on GitHub.
- Quote pytest's last line as printed. **The worker suite** should read
  `184 passed, 7 skipped` on this Windows machine since D-189
  (2026-10-05, 20:46 on staging): six skips need Linux or CI's
  parse container, and the seventh commits a worker start, so it runs only
  on CI's own database (`.github/approved-skips.txt`, `worker-staging`).
  **Its last lines also include the suite's own check**, which should read
  `worker_starts in the last 60 minutes: before N, after N; starts kept by
  tests: 0; committed on CI's own database: 0`, with the second number no
  higher than the first. Before D-189 it read
  `161 passed, 6 skipped` on this Windows machine (the six need Linux or
  CI's parse container) and took about 20 and a half minutes on staging
  at `f191e27`: 20:43 on 2026-10-01, 20:32 on 2026-10-05. (An older
  figure of about 12 minutes, from Stage 3a's 136 tests, was quoted on
  2026-10-05 as if it were current. Compare a run with the last
  recorded run of the same code, not with memory.)
  **The API suite** should read `625 passed, 1 skipped, 3 deselected` with
  nothing failed on `main` since 3e (2026-10-05 at `9fd519a`: 42:32 on
  staging). Before 3e, at `f191e27`, it read `586
  passed, 1 skipped, 3 deselected` with nothing failed. Staging on
  2026-10-01 (Stage 3d) read 585, plus the keyword test added during that
  run and passed on its own. The one skip, `test_parse_token_boundary.py`, needs the real
  parse service with a token and runs in CI; it took 42:52, against about 34
  minutes before). Earlier: 571 at 3b (`aefd428`), 424 at Stage 1, 562 after
  card billing, 568 on `e3e8641`.
  **From the Redis idle change (2026-10-08, RUNBOOK 9.8):** the worker
  suite reads `188 passed, 7 skipped` (three tests added to
  `test_celery_app.py`, one to `test_dispatch_db.py`) and the API suite
  `626 passed, 1 skipped, 3 deselected` (one added,
  `test_celery_client.py`). The core suite is unchanged: `806 passed, 1
  skipped`.
  **From the document-cost fix (2026-10-09, D-163's addendum):** the
  worker suite reads `191 passed, 7 skipped` (two tests added to
  `test_stage3c_db.py`, one to `test_dispatch_db.py`). Core and API are
  unchanged.
  **A change to `packages/core` needs all three suites** (2026-10-09,
  D-197): the API and the worker both install it. On that day a fix in
  `docflow_core/model_runs.py` was first taken to be worker-only and the
  API suite was left out; it was run once that was noticed.
  **From the test-tenant purge (2026-10-09; the Stage 3 checkpoint's
  list, D-197):** the API suite gains four tests, all in
  `test_tenant_cleanup.py`, and loses none: CI reads `631 tests`. On this
  machine against staging it reads `630 passed, 1 skipped, 3 deselected`
  (2026-10-09 at `5a09632`, 38:26: the Stage 3 checkpoint's run of
  record), and the leftover line read `+0` on all six counts. Core and worker are
  unchanged.
  - **Every throwaway tenant the API suite makes now ends in one purge**
    (`apps/api/tests/tenant_cleanup.py`): the tenant's files in the bucket,
    every row that points at it, and the tenant row. Before, nine helpers
    and four cleanup functions each had a delete list, and only some
    removed a tenant's founder alerts, e-mails and scheduled jobs.
  - **The purge refuses any tenant the run did not create.** A helper
    registers the id when it makes the tenant; an unregistered id raises
    `NotThisRunsTenant`, whatever the tenant is called. Nothing in the
    product imports the purge (a test checks).
  - **The run prints what it left behind**, just above pytest's last
    line: `left behind by this run (before -> after): tenants N -> N (+0),
    founder alerts ..., e-mails ..., scheduled jobs ..., platform-admin
    rows ..., users with no tenant ...`. In CI the same line is a notice
    annotation, `api leftover count`. **A failure since 2026-10-11**
    (founder; D-199): it was printed only until the staging run of record
    at `5a09632` read `+0` on every count. A run that does not read `+0`
    on all six exits 1, pytest's last line counts `1 error`, and a line
    under the counts names the ones that moved; in CI the annotation is
    an error. A count that goes down fails too, and so does a run whose
    counts can't be read again at the end. A killed run prints nothing
    and cleans nothing.
  **From the leftover count as a failure (2026-10-11; D-199):** the API
  suite gains eleven tests, all in `test_leftover_count.py`, and loses
  none: CI reads `642 tests`. On this machine against staging it reads
  `641 passed, 1 skipped, 3 deselected` (2026-10-11 at `b278490`, 39:18),
  and the leftover line read `+0` on all six counts. Core and worker are
  unchanged.
  **From the catalog sizing job (2026-10-11; D-200; section 1.8):** the
  core suite gains 43 tests, all in `test_bench_catalog.py`, none needing
  a database, and loses none: CI reads `850 tests`. On this machine against
  staging it reads `849 passed, 1 skipped` (2026-10-11 at `1826c70`, 45 s;
  it read `838 passed, 1 skipped` at `e7b60f7`, before the eleven tests of
  the load's progress lines). API and worker are unchanged.
  **From the index variants and the faster full scan's bench (2026-10-12;
  D-201; section 1.8):** the core suite gains 19 more tests in
  `test_bench_catalog.py` (62 in the file), none needing a database, and
  loses none: CI reads `869 tests`. On this machine against staging it reads
  `868 passed, 1 skipped` (2026-10-12 at `36412e4`, 52 s). API and worker
  are unchanged.
  **From PR A, the fuzzy step's fast scan (2026-10-13; D-202):** the core
  suite gains 28 tests, none needing a database, and loses none: 21 in the
  new `test_matching_fast_scan.py` and 7 more in `test_bench_catalog.py`
  (69 in the file). CI should read `897 tests`. On this machine against
  staging it reads `896 passed, 1 skipped` (2026-10-13 at `9426bd0`, 95 s).
  The worker and API counts are unchanged: `191 passed, 7 skipped`
  (2026-10-12 at `075ffdd`, 24:47) and `641 passed, 1 skipped, 3 deselected`
  (2026-10-13 at `9426bd0`, 39:01, the leftover line `+0` on all six).
  **A known cause of one API failure, not yet fixed (D-202 item 8):**
  `test_a_signature_stamped_more_than_300_seconds_in_the_future_is_refused`
  reads 200 when the test reads the clock late in a second. It is a race in
  the test, and the product's check is correct. If it is the run's only
  failure, say so with this cause, and run the suite again for the record.
  **A machine that sleeps during a run** drops the connection that holds the
  suite lock: `test_this_run_holds_the_lock_and_a_second_run_is_refused_by_name`
  then fails, and the run is not a run of record (2026-10-12; D-202 item 8).
  Let it finish, so its cleanup runs, and run again.
  **What CI reports for the same code, side by side** (founder,
  2026-10-08). CI runs every test, so each line must also read `0 failed,
  0 skipped, 0 unapproved`:

  | Suite | This machine, against staging | CI |
  |---|---|---|
  | core | 896 passed, 1 skipped | 897 tests |
  | worker | 191 passed, 7 skipped | 198 tests |
  | API | 641 passed, 1 skipped, 3 deselected | 642 tests |

  The difference in each row is exactly the skips named here, which need
  Linux, CI's containers or a secret this machine does not hold. A CI
  count that differs from this table, or a skip in CI, is to be explained
  before a merge.
  **The API suite needs the local Redis running** (Memurai, port 6379):
  three tests in `test_email_intake_auth.py` make a real send. Memurai's
  developer licence shuts it down 10 days after each start (it did at
  2026-10-08 15:50:51Z), and with it down the suite reads `3 failed`.
  Check the service before the run and after it; starting it needs
  administrator rights (`Start-Service Memurai`).
  If the count is *lower* than the number written here, find out what
  stopped running before calling the run green.
  The 3 deselected are the `live_api` tests, which only run at checkpoints
  (`apps/api/pyproject.toml`): `pytest -m live_api` — the golden fixture, the
  golden fixture with examples, and the example-contamination check.

### 1.5 The live end-to-end suite (`apps/web/e2e-live`)

The one browser suite with nothing stubbed: a real sign-in, the real API over
HTTP, real Postgres, real RLS (DECISIONS.md D-166). It needs three things
running before it will do anything useful.

1. **The API**, on port 8000:

   ```
   cd apps/api && .venv/Scripts/python -m uvicorn app.main:app --port 8000
   ```

2. **A current production build of the web app.** The suite serves it on port
   3101, and Next compiles `NEXT_PUBLIC_*` into the bundle — so a build made
   against different settings will point the browser at the wrong stack:

   ```
   cd apps/web && npm run build
   ```

3. **Then the suite itself**, which seeds its own throwaway tenant, reviewer
   and orders before the run and deletes them after it:

   ```
   cd apps/web && npm run test:e2e:live
   ```

It counts as one of the suites under the one-at-a-time rule above. The tenant
it creates is named `Acme Test Live E2E <id>`; if a run is interrupted before
its teardown, remove the leftovers with:

```
python scripts/seed_live_e2e.py teardown --out apps/web/e2e-live/.seed.json
```

CI runs the same suite in the `web-live` job against the local Supabase stack,
so it needs no staging credentials there.

### 1.6 Standing rule: the suite reports its own evidence

**A suite says why it failed. Nobody diagnoses a failure from a symptom.**
When a run fails, the first fix is not to the product — it is to make the
failure name its own cause. Only then fix what it names.

What this means in practice:

- **A timeout is not a diagnosis.** `waitForURL` timing out says the page
  never arrived; it says nothing about why. A suite that can time out on a
  dependency (a sign-in, a service, a build) checks that dependency first,
  outside the browser, and fails there with what it found.
- **Print what the other side actually said.** Not "sign-in failed" but what
  `/auth/v1/settings` reports the server offers. Not "401" but the error type
  the verifier raised. A failure that carries the other side's own answer ends
  in one run.
- **Two plausible fixes in a row means the evidence is missing, not that the
  third guess will land.** Stop changing the product and make the suite talk.
- **CI logs are unreadable here** (`project-github-workflow`: job logs return
  403). The message thrown by the test is the whole of what we get, so it has
  to be enough on its own. Playwright's `github` reporter puts it in the
  annotations; make it worth reading.
- **This applies to the product too.** A path that fails closed in silence —
  a rejected token, a refused write — logs the error type and message, never
  the token, the claim or the data (Section 7.10).

**Why this is a standing rule and not one decision's footnote.** Twice in
Phase 5.5 a diagnosis lived somewhere unreadable and cost multiple round
trips: the `web-live` sign-in failure, where two convincing fixes were both
wrong and what ended it was a seeding-time check that printed the server's own
settings (D-166); and the session-token skew behind it, invisible until the
JWKS path logged why it had rejected a token (D-167). Both were found by
instrumentation, not by reasoning. A test that fails without saying why costs
more than the bug it is hiding.

**Related:** D-166, D-167; §1.4 on how a run is reported.

### 1.7 Standing rule: an isolation failure means stop and report

**When a check of an isolation boundary fails -- a sandbox, a tenant boundary,
a network or permission control -- stop and report it. Do not fix it and
re-run first.** (Founder, 2026-09-28, after the D-150 spike.)

The one exception is a bug in the *measurement*, not the boundary. A
measurement may be fixed and the check re-run **only if**:

1. the fixed check is shown to **fail against the positive control** -- run it
   where the boundary is absent (outside the sandbox, as the privileged user,
   in the other tenant) and quote it failing there. A check that cannot fail
   proves nothing when it passes; and
2. **both runs' output is kept** -- the failing run and the re-run -- next to
   the result, so a reader can judge the call.

If either cannot be done, the failure stands and is reported as a failure.

**Why:** in the D-150 spike, run 1's interfaces check failed because it read
`/sys/class/net`, which shows the machine's interfaces, not the sandbox's. The
fix was right, but it was re-run before the corrected check had been shown to
fail anywhere -- so for a while "PASS" rested on a check nobody had seen
fail. The positive-control run was done afterwards
(`docs/spikes/d150-fly-netns/evidence-run3-interfaces-control.txt`: FAIL on
the machine, PASS in the sandbox). The order is the rule: show it can fail,
then trust it passing.

**Related:** D-150; §1.6.

### 1.8 The catalog sizing job (Stage 4; D-199, D-200, D-201)

**What it is for.** `docflow-staging` is Supabase's Free plan: its database
turns read-only past 500 MB, and that would stop all of staging. Before any
Stage 4 catalog is loaded there, this job measures what the catalogs and
their two trigram indexes cost on disk, on a throwaway database.

**The two trigram indexes are not built in the product (D-201).** The job
still loads and sizes them, as the baseline its measurements are read
against. Read "the table and every index" less the two `idx_items_trgm_*`
lines for what a catalog costs on staging: about 125 MB for the 250,000
tenant.

**Where it runs.** On a GitHub runner, against the runner's own Supabase
stack, like the CI jobs. The script (`scripts/bench/catalog_sizing.py`)
refuses any database that is not on the machine it runs on. **It never runs
against `docflow-staging` or production, and nothing here loads a row
there.**

**Starting it** (the founder; it is started by hand and Claude has no
login to start it): GitHub -> Actions -> "Catalog sizing (manual)" -> Run
workflow, branch `main`. Leave the seed as it is. One run per mix,
**smallest first** (founder, 2026-10-11): the 250,000 tenant, then the Stage
4 mix. From those two runs' measured rates Claude gives a prediction for the
scale gate's mix, and the founder starts that one only after reading it:

| Run | Enter as `mix` | Tenants | Items |
|---|---|---|---|
| The 250,000 tenant alone | `250000` | 1 | 250,000 |
| The Stage 4 mix | `250000,100000x2,50000x7` | 10 | 800,000 |
| The scale gate's mix | `250000,100000x4,50000x45` | 50 | 2,900,000 |

**Run so far (D-201 has the figures):** the 250,000 tenant (run
38178174400, 2m 20s) and the Stage 4 mix (run 38179551199, 3m 07s), both on
`main` `c30926b` on 2026-10-11. **The scale gate's mix has not been run.**
Predicted from those two: about 10 minutes and 2.5 to 2.8 GB.

**What it prints**, as annotations on the run's page (Claude reads them
from there; the job's log can't be read from the development machine, 1.6):
- `catalog sizing: inputs`: the mix, the seed, the Postgres version, the
  commit. Printed before the load;
- `catalog sizing: loaded N of M rows`: one line per tenant (its rows, its
  seconds, rows a second, and how far the load is). In the job's log and the
  run's summary each line appears as its tenant finishes. GitHub keeps ten
  notices from one step, so as annotations the lines travel together: after
  the first tenant, and each time the load passes a quarter of its rows
  (five at most);
- `catalog sizing: sizes as loaded`: the table, each index (the two trigram
  indexes by name), the table with every index, and the database, each in
  MB, in bytes and in bytes a row; and how long the load took with both
  indexes in place;
- `catalog sizing: candidate query on the largest tenant`: p50, p95 and max
  for the 50 nearest items by description and by SKU, and each query's plan;
- `catalog sizing: each trigram index rebuilt at this many rows`: the build
  time and the size of a freshly built index.

**How to read it.**
- **The sizes are the measurement.** The staging benchmark's guard is set
  from them: it refuses to start if staging's current size plus the measured
  size of the load is over 350 MB. Until it is known which reading Supabase
  enforces the limit on, the current size is the larger one, the
  dashboard's (D-199).
- **The timings are GitHub's runner, not staging's,** and they time the
  index query alone. The match timings of record come from the matching
  benchmark, on the database of record.
- The catalogs are fake and come from a fixed seed
  (`scripts/bench/catalog_gen.py`): the same mix and seed give the same
  rows, so a run can be repeated.
- **A run that is cut short** still shows its inputs and the tenants loaded
  so far, with their rates. The job stops itself at 350 minutes
  (`timeout-minutes`); GitHub stops any job on its own runners at 360. The
  scale gate's mix had not been timed when this was written.

**The index statements have one home:** `scripts/bench/trigram_indexes.sql`.
No migration carries them: `0037`, which was to copy them, is retired
(D-201). A test still fails if a migration creates a trigram index that is
not one of them.

The job also runs by itself, on a small mix, on a pull request that changes
its own files: the sizing step, then every index variant. It is not one of
the required checks on `main`.

**The `variants` input: the matching measurements (D-201).** Left empty,
the job is the sizing job above. With a value it measures matching on the
largest tenant of the mix instead, and prints annotations titled
`catalog sizing: variants: ...` or `catalog sizing: scan: ...`:

| Enter as `variants` | What is measured | Script | The 250,000 tenant took |
|---|---|---|---|
| `scan` | **The product's own fuzzy step** (`matching.resolve_line`, from D-202) against the full scan: each line's time for fastener and general lines, parity on 100 lines, a 40-line order, memory, several cores. **The run fails if any line differs.** It is the proof a change to the fuzzy step needs before it merges | `scripts/bench/full_scan.py` | 27 minutes |
| `all` | Every index variant for the candidate query (GiST at four signature lengths, GIN at three thresholds, an index without `tenant_id`), each at 50, 100 and 200 candidates, with today's full scan | `scripts/bench/index_variants.py` | 51 minutes |
| names with commas, e.g. `baseline,gin` | Those variants only | the same | |

- **It can be started from a branch** before that branch is merged: Run
  workflow, "Use workflow from" = the branch. That works because the
  workflow file is on `main`.
- **Today's full scan is most of the run time:** 100 lines at about 14 s
  each on the 250,000 tenant.
- **Two limits of GitHub's annotations, found by running.** Ten notices are
  kept from one step, and one annotation is cut at 4,096 characters without
  a word. The scripts keep under both: a report over the limit is an error
  on the run, and a test holds the notices at ten or under. A comma in an
  annotation's title ends the title.
- `scan` installs numpy for its several-cores measurement only. numpy is
  not a product dependency and is in no lockfile.
- These refuse any database that is not on the machine they run on, like
  the sizing job.
- **The lines are the same on every run** of the same mix and seed (from
  D-202): they are drawn in the order of `md5(sku)`. The runs before it
  drew by row id, so each of those tested different lines.

**One bench script is not for the runner: `scripts/bench/worker_matching.py`
(D-202 item 5).** It times matching inside the worker's machine, as
`docflow_worker`, against the database that machine is configured for, for
one bench tenant: `python worker_matching.py --tenant-id <id>`. It sets
every transaction read-only, refuses any tenant whose name does not start
"Acme Test Bench", and prints counts, timings and sizes only. It is not in
the worker's image and nothing runs it by itself. **Running it is a step on
staging: the founder is asked first.**

### 1.9 Migration 0037: the alert's state key (Stage 4, PR 1a; D-203)

`supabase/migrations/0037_alert_state_key.sql` adds two nullable columns
and their indexes: `founder_alerts.state_key` (unique over every row,
acknowledged or not) and `documents.fault_keys`. It changes no existing
row, policy or grant.

- **The order is the migration first, then the code.** The code that comes
  with it names `state_key` in every alert it writes, so on a database
  without the column no alert can be raised. Code from before `0037` is
  unaffected by it. So: apply `0037`, then deploy.
- **Staging: no backup (founder, 2026-10-13).** Additive columns, no row
  changes and a reverse that CI tests. Run the file in the SQL Editor, then
  check that both columns and both indexes are there.
- **Production follows the standard backup rule regardless (founder,
  2026-10-13):** 1.1 for `founder_alerts` and `documents`, the row counts
  of 1.2, then the migration.
- **Going back** is `supabase/reverse/0037_reverse.sql`, run only with the
  code from before `0037` deployed. It removes the two columns and the two
  indexes and no row. What is lost is the keys: an alert already raised can
  then be raised again by the same state, and the Console's count of
  affected documents reads nothing. CI runs forward, reverse, forward on
  every push (`scripts/ci/migration_0037_roundtrip.py`).

### 1.10 Migration 0038: the database wait and the item-delete index (Stage 4, Q14; D-206)

`supabase/migrations/0038_database_wait.sql` lets `documents.wait_cause` be
`database` (0035's check, replaced with the same list plus that value) and
adds `idx_document_lines_matched_item_id`. It changes no existing row,
policy or grant.

- **The order is the migration first, then the code.** The code that comes
  with it writes `wait_cause = 'database'`, which the old check refuses.
  Code from before `0038` never writes it. So: apply `0038`, then deploy.
- **Staging: no backup (founder, 2026-10-16),** as for `0037`: a check
  widened, an index added, no row changed, a reverse that CI tests. Run the
  file in the SQL Editor, then check that the check names four causes and
  the index is there. Then Claude runs the staging suites (step 4 of this
  section) before the PR merges.
- **Production follows the standard backup rule regardless:** 1.1 for
  `documents` and `document_lines`, the row counts of 1.2, then the
  migration.
- **Going back** is `supabase/reverse/0038_reverse.sql`, run only with the
  code from before `0038` deployed. Documents already waiting on the
  database become ordinary waiting documents first (their wait cause, its
  start and its last error cleared; still `pending`, so the dispatcher sends
  them again); then the three-cause check is put back and the index dropped.
  No row is removed. CI runs it, and forward, reverse, forward, on every push
  (`scripts/ci/migration_0038_roundtrip.py`).

### 1.11 Migration 0039: the rollup's alerts, and the Data API roles shut out (Stage 5, M7; D-214)

`supabase/migrations/0039_m7_alerts_and_data_api_roles.sql` changes no row,
table or column. It changes who may use what:

- adds `staging_intakes_past_ttl(ttl_days)`, a SECURITY DEFINER function only
  `docflow_worker` may run, for the nightly staging-TTL alert;
- lets 0017's `rollup_raise` policy also insert `staging_ttl_elapsed` (still
  `TO docflow_worker`, still tenant-less only);
- gives the `read_all` policies on `tiers` and `setup_fee_presets` a `TO
  docflow_tables`, so every DocFlow login reads them and `anon` and
  `authenticated` stay outside the policy;
- gives the four functions Supabase's Security Advisor flags an empty
  `search_path`, schema-qualifies what they name, revokes EXECUTE from
  `PUBLIC`, `anon` and `authenticated`, and grants
  `document_status_transition_allowed` back to `docflow_api`,
  `docflow_worker` and `docflow_admin`;
- revokes every privilege `anon` and `authenticated` hold on the tables and
  sequences in `public`, and the defaults that would give them new ones.

**Not changed, and why:** `supabase_admin`'s own default privileges in
`public` still give `anon` and `authenticated` everything that role creates
there. `postgres` is not a member of `supabase_admin`, so they can't be
altered from the SQL Editor (read on staging, 2026-10-21). DocFlow creates
nothing as `supabase_admin`, and Phase 6 turns the Data API off in
production. `service_role` keeps its grants.

- **The order is the migration first, then the code.** The code that comes
  with it calls the new function and raises `staging_ttl_elapsed` from the
  rollup's session. Without 0039 that one check fails, is logged
  (`rollup_alert_check_failed check=staging_ttl_elapsed`) and the rollup
  carries on. Code from before 0039 is unaffected by it.
- **Staging: the steps are at the top of the PR message.** The standard
  backup (1.1, 1.2) of `tiers`, `setup_fee_presets` and `founder_alerts` (the
  three tables whose policies change), and a snapshot of every grant, policy,
  default and function setting in `public` into `backup_0039.grants_before`.
  Then Claude reads staging read-only with `scripts/ci/grants_snapshot.py`:
  its SHA-256 must equal `supabase/reverse/0039_pre_snapshot.json`'s
  (`b065a7c3...`). Then the migration; then the same read must equal
  `0039_post_snapshot.json` (`bb67bc5e...`). Then the staging suites, each on
  the founder's grant, before the PR merges.
- **Production follows the same steps** (the standard backup and the
  snapshot, then the migration, then the two SHA-256 checks).
- **Going back** is `supabase/reverse/0039_reverse.sql`, run only with the
  code from before 0039 deployed. It puts every grant, policy, default and
  function body back as 0038 left them (the pre file's state) and drops the
  function. No row is changed: `staging_ttl_elapsed` alerts already raised
  stay. CI runs reverse and forward on every push
  (`scripts/ci/migration_0039_roundtrip.py`), comparing with both files.

---

## 2. Inbound email (Postmark)

The inbound webhook answers two separate questions, and it is worth keeping them
separate in your head because only one of them is a secret:

- **Which tenant is this for?** The per-tenant token in the URL path. Public by
  design — it is the local part of the address the customer gives to its buyers.
- **Is this actually Postmark?** HTTP Basic credentials on the webhook URL. This
  is the secret, and it is the only thing standing between the intake endpoint
  and anyone who has ever received a PO from one of your customers (review
  finding H8).

### 2.1 Cutover order — credentials before enforcement

> **Do not run this on an address real customers send to until Stage 2c has
> landed** (founder, 2026-09-27; D-171). A refused request looks identical from
> outside whether it is a bad cutover or an attacker, and a bad cutover means no
> mail arrives at all. Until 2c raises a founder alert on refusal, the only
> signal is step 4's log line — which nobody is watching in real time. Staging
> and a test address are fine now; a production intake address waits.
>
> **2c builds that alert** (D-175): every refusal raises a high-severity
> `intake_webhook_refused` founder alert naming the reason, one open alert per
> reason. The condition is met for an address once migration `0029` is applied
> to the database behind it and the 2c code is deployed there. After a
> cutover, a refusal you did not expect shows up in the Console's attention
> panel -- `not_configured` or `no_credentials` means one of steps 1–3 went wrong;
> `mismatch` means the credential in Postmark's URL and the one deployed differ.


**Do these in order.** Step 3 is what makes mail flow; steps 1–2 are what makes
it flow *authenticated*. Doing 3 first means a window where the old hole is open;
doing 3 last means no window at all, at the cost of inbound mail being refused
until it is done — which is the right way round, and it is why a blank
credential refuses everything rather than falling back to token-only.

1. Generate the credential pair:

   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Use a distinct value for the username too — it is compared in constant time
   like the password, so it is a second 32 bytes of secret, not a label.

2. Set `POSTMARK_WEBHOOK_USERNAME` and `POSTMARK_WEBHOOK_PASSWORD` in the API's
   environment and **deploy**. Inbound mail is now refused for everyone,
   including Postmark, which is expected: nothing is pointed here yet.

3. In Postmark, set the inbound webhook URL to carry them:

   ```
   https://USER:PASS@<api-host>/intake/email/{token}
   ```

   Postmark sends them as an `Authorization: Basic` header. Send one test mail
   and confirm a 200 in Postmark's own delivery log.

4. Confirm nothing is being refused. **From Stage 2c onward** this is the
   Console's attention panel: a refusal raises an `intake_webhook_refused` alert
   at severity **high**, once per day per reason, so a mistake in step 3 shows up
   in minutes rather than when a customer asks where their orders went.
   **Until 2c lands**, read the API log instead — every refusal logs
   `intake_webhook_refused reason=<word>`, and the word tells you which mistake
   it was:

   | reason | what it means |
   |---|---|
   | `not_configured` | step 2 didn't take effect — the environment has no credentials |
   | `no_credentials` | step 3 didn't take effect — Postmark is posting without them |
   | `mismatch` | step 2 and step 3 disagree — compare them, do not guess |
   | `not_basic` / `undecodable` | something other than Postmark is posting here |

**Never** put the credentials in the tenant's intake *address*, a Console
screen, a log line or a support thread. They are not in any of those today and a
test asserts it (`apps/api/tests/test_email_intake_auth.py`).

### 2.2 Rotating the credentials

Rotation has a gap by nature: Postmark's webhook URL holds one credential pair
at a time, so between changing the environment and changing Postmark, mail is
refused. Postmark retries a non-200 for a while, so a short gap loses nothing —
but keep it short and do it deliberately rather than discovering it.

1. Generate a new pair (2.1 step 1).
2. Update Postmark's webhook URL to the new pair **first**. Inbound mail is now
   refused; Postmark begins retrying.
3. Set the new values in the API's environment and deploy.
4. Send a test mail and confirm a 200 in Postmark's delivery log. Postmark's
   retries of anything refused in the gap now succeed.
5. Confirm no new `intake_webhook_refused` alert since the deploy.

**Rotate when:** the credential has been in a shell history, a screenshot, a
ticket or a third party's hands; someone with access to it leaves; or annually,
whichever comes first. Rotating is cheap and the gap is minutes.

**What rotation does not do:** it does not change any tenant's intake address.
Rotating a *tenant's* address is a separate, per-tenant action on the tenant page
(7.16.3) with its own grace period.

### 2.3 Confirming the source addresses, then enforcing the allowlist

`POSTMARK_INBOUND_IP_ALLOWLIST` is **log-only until confirmed** (D-155). An
allowlist enforced on a guess silently drops customers' purchase orders, which
is the exact failure the product exists to prevent — so it corroborates the
credentials, it does not replace them.

1. Leave it blank until real inbound mail is arriving.
2. After the first genuine inbound mail, collect the source addresses the API
   saw. With the allowlist blank nothing is logged about the source, so set it to
   a deliberately wrong value (e.g. `203.0.113.1`) for the collection window:
   every request then logs `source_not_on_allowlist`, and the request's source
   address is in the web server's own access log.
3. Compare what you collected against Postmark's published inbound IP list
   (Postmark documents these; take them from Postmark's own page, not from a
   search result).
4. If they agree, set the confirmed addresses. **They are still log-only** — the
   code has no enforcing branch, deliberately. Turning enforcement on is a code
   change with its own test and its own `DECISIONS.md` entry, made once the list
   has been stable across a few weeks of real mail.
5. If they disagree, stop and find out why before changing anything. A source
   address that is not Postmark's and still passed the credential check is worth
   understanding, not filtering.

### 2.4 Outbound: the sending domain and the support mailbox (founder, 2026-09-30)

Needed before automatic sending (its own stage after 3e, D-103). Verification
can take up to 48 hours, so it is done ahead of the stage. `yourdomain.com`
below is the company's main domain (the one on the website and in the
customer agreement).

**Addresses on the domain:**
- `notifications@yourdomain.com`: the From address of every DocFlow email
  (`EMAIL_FROM_ADDRESS`). Needs no mailbox; customer emails carry Reply-To.
- `support@yourdomain.com`: the support mailbox (`SUPPORT_EMAIL`), routed to
  the founder's inbox.
- The intake addresses stay on their own subdomain (`INTAKE_EMAIL_DOMAIN`,
  e.g. `mail.yourdomain.com`, section 2), so their MX record never conflicts
  with the root domain's.

**1. Find the DNS host.** The records go wherever the domain's nameservers
point, which may not be where it was bought:
`nslookup -type=ns yourdomain.com` (Cloudflare shows `*.ns.cloudflare.com`;
GoDaddy `*.domaincontrol.com`; Namecheap `*.registrar-servers.com`; Google
`ns-cloud-*.googledomains.com`).

**2. Postmark: add the domain.** account.postmarkapp.com → **Sender
Signatures** → add a **domain** (not a single-address signature) → `yourdomain.com`.
Postmark then shows two records under **DNS Settings**.

**3. Add the records at the DNS host** (type, name, value exactly as Postmark
shows them; most hosts add `.yourdomain.com` to the name themselves, so type
only the part before it):

| Type | Name | Value |
|---|---|---|
| TXT | the DKIM name Postmark shows, e.g. `20260930123456pm._domainkey` | the `k=rsa; p=...` value Postmark shows (copy it whole) |
| CNAME | `pm-bounces` | `pm.mtasv.net` |
| TXT | `_dmarc` | see step 4 |

No SPF change is needed for Postmark: with the Return-Path CNAME, Postmark's
mail passes SPF on `pm-bounces.yourdomain.com` (Postmark: "Why we no longer
ask for SPF records"). On Cloudflare, set the CNAME to **DNS only** (grey
cloud), not proxied.

**4. DMARC.** One `_dmarc` TXT record per domain: if one exists, edit it, don't
add a second. Start with monitoring only:

```
v=DMARC1; p=none; rua=mailto:<the address Postmark's DMARC tool gives>
```

Get the `rua` address from dmarc.postmarkapp.com (free weekly digests: enter
the domain and the founder's email). After a few weeks, once the digests show
every legitimate sender passing (Postmark, the support mailbox's provider),
move to `p=quarantine`, later `p=reject`.

**5. Check.** In Postmark, the domain's **DNS Settings** → **Verify** next to
each record, until both show verified (up to 48 hours). From Windows:

```
nslookup -type=cname pm-bounces.yourdomain.com
nslookup -type=txt <dkim name>._domainkey.yourdomain.com
nslookup -type=txt _dmarc.yourdomain.com
```

**6. Account approval.** A new Postmark account can only send to addresses on
its own verified domains until Postmark approves it for sending to anyone:
request approval in the account, describing the mail as transactional
(invites, billing notices, reminders to business customers).

**7. The support mailbox.** Route `support@yourdomain.com` to the founder's
inbox:
- If the domain already has email (Google Workspace, Microsoft 365): add
  `support@` as an alias or group there. Nothing else to change.
- If it has none and DNS is on Cloudflare: **Email** → **Email Routing** →
  enable, then **Routing rules** → **Create address**: `support` → **Send to
  an email** → the founder's inbox (confirm the verification email).
  Cloudflare adds its MX and SPF records to the root domain itself. Replies
  then go out from the founder's own address, not `support@`.
- To reply *as* `support@` (recommended once customers write in), the address
  needs a real mailbox: Google Workspace (or similar) on the domain. Don't use
  Postmark for personal replies: it is for application email.

Then set `SUPPORT_EMAIL=support@yourdomain.com` and
`EMAIL_FROM_ADDRESS=notifications@yourdomain.com` in `.env` and restart the
API. The Postmark server API token (`EMAIL_PROVIDER_API_KEY`) waits for the
automatic-sending stage: while it is blank, every email stays held in the
Outbox.

### 2.5 When the founder gets an `intake_failed` alert (INT-011): retrying the e-mail (Stage 5, M7; D-210, D-214)

The webhook failed on our side for a known tenant and answered 500, so
Postmark keeps resending the e-mail for about 10 hours 20 minutes. Nothing
from it was saved.

1. **Fix the fault the alert names, and deploy the fix.** While the resends
   last, the next one is taken in normally and nothing else is needed.
2. **If the resends ran out first,** the e-mail shows as **Inbound Error** on
   Postmark's **Inbound** page for the server. Open it and note its
   **MessageID**.
3. **Open a new PowerShell window with history off** (8.1): run
   `Set-PSReadLineOption -HistorySaveStyle SaveNothing` before anything else.
   Every call below needs the **server** token (`EMAIL_PROVIDER_API_KEY`, the
   inbound server's API token; Postmark: "This request requires server level
   privileges"), typed into this window only, never pasted into chat:

   ```powershell
   $h = @{ "Accept" = "application/json"; "X-Postmark-Server-Token" = "<the server token>" }
   ```

   - **To find the MessageID through the API instead of the page:**

     ```powershell
     (Invoke-WebRequest -UseBasicParsing -Headers $h -Uri "https://api.postmarkapp.com/messages/inbound?count=50&offset=0&status=failed").Content
     ```

     Postmark's page names the list in its answer both `Messages` (its
     table) and `InboundMessages` (its example), so **read `MessageID` from
     the raw JSON** this prints, rather than relying on either key.
4. **Retry it:**

   ```powershell
   Invoke-RestMethod -Method Put -Headers $h -Uri "https://api.postmarkapp.com/messages/inbound/<MessageID>/retry"
   ```

   This is Postmark's `PUT /messages/inbound/{messageid}/retry`, sent with an
   empty body as its own example does (`curl -d ""`; Windows PowerShell 5.1
   drops an empty argument to `curl.exe`, so the PowerShell call is used).
   Close the window afterwards.
5. **Read the answer:**
   - `ErrorCode 0`, "Successfully rescheduled failed message: <id>": done.
     Postmark sends it to the webhook again.
   - `701` (PowerShell shows it in the error text): the message was not
     found, or can't be retried. Check the MessageID, and that it is an
     Inbound Error.
   - `703`: Postmark could not retry it. Try again later; if it persists,
     ask the sender to send the order again.
   - `10`: the token is wrong or missing (step 3).
6. **Confirm it arrived:** the order is in the tenant's documents in the
   Console, or a new INT-011 alert says why it wasn't.

---

## 3. Onboarding a new tenant — checklist

The Console walks you through the nine setup steps (Intake → Go live). This
list is the checks that sit around them. Phase 6 completes it.

**Once, before the first real customer (account-wide):**

- [ ] **Stripe must never cancel a subscription by itself.** Suspension is
  your decision (D-125), taken through DocFlow's lifecycle; a cancel on
  Stripe's side skips it and leaves an active tenant with no billing. Two
  settings, on two different pages, both set to **leave the subscription past
  due**:
  - **Failed card payments, after the last retry** (card billing): **Billing**
    → **Revenue recovery** → **Retries**
    (`https://dashboard.stripe.com/revenue_recovery/retries`), the outcome
    "if all retries for a payment fail". This is the one found still on
    "cancel" in the sandbox on 2026-09-29, after it had been changed on the
    other page: a test subscription was cancelled after its last retry.
  - **Invoices sent to customers** (invoice billing): **Settings** (gear
    icon) → **Billing** → **Subscriptions and emails**
    (`https://dashboard.stripe.com/settings/billing/automatic`). Today it
    cancels 90 days after the due date.
- [ ] **Retry schedule: Smart Retries, 8 tries within 1 week** (founder,
  2026-09-29), on the same Retries page. The last retry then lands by day 7,
  well before day 14, the earliest date a past-due tenant may be suspended
  (`CURE_PERIOD_DAYS`); from then until the owner updates the card, DocFlow
  charges the new card itself when it is added (D-181).
- [ ] **Check the account before saving.** A sandbox is an account of its
  own: the account switcher (top left) must show the account DocFlow's key
  belongs to, and a link scoped to it carries its id
  (`https://dashboard.stripe.com/<account id>/test/...`). Each setting exists
  separately in test mode, in each sandbox, and in live mode; do live mode
  before the first real customer. To confirm in test mode, ask for the
  "retries exhausted" check (a Stripe test clock): the subscription must end
  `past_due`, never `canceled`.
- [ ] **The webhook endpoint sends card billing's events** (D-181), besides
  the `customer.subscription.*` events it already sends:
  `checkout.session.completed` (a card saved, a setup fee paid at signing)
  and `customer.updated` (a card changed; DocFlow charges a past-due
  account's open invoice to it). In Stripe: **Developers** → **Webhooks** →
  the DocFlow endpoint → **Select events**.
- [ ] **A support mailbox that someone reads**, and its address in
  `SUPPORT_EMAIL` (founder, 2026-09-29). The card-billing emails tell
  customers to email it to cancel, and "Ask for a card" refuses while it is
  blank (ONB-018). Cancel requests that arrive there follow section 6.
- [ ] **The customer portal is on**, for the owner's "Update card" (D-181):
  **Settings** → **Billing** → **Customer portal**. DocFlow uses its own
  portal configuration (tagged `card-update-v1`: update the card and see
  invoices, no cancelling), created automatically the first time an owner
  opens "Update card", and put back if it is changed in the dashboard. Don't
  delete it; the account's default configuration isn't used.
- [ ] **Stripe's own trial-ending email is off** (founder, 2026-09-29; done
  in the sandbox that day): **Settings** → **Billing** → **Subscriptions and
  emails**, the reminder Stripe sends before a free trial ends. DocFlow
  sends its own trial-ending email to card-billed customers, 2 days before
  the trial ends (D-181 addendum 2), so with Stripe's on, customers would get
  two.

**For each new tenant:**

- [ ] **Check the line count of the prospect's largest sample order.** Open
  the biggest purchase order in their intake files and count its line items
  (or look at the line number of the last item).
  - Up to about 1,000 lines: DocFlow reads it in one pass. Measured on
    2026-09-26 (D-161): 600 lines read exactly in 8 minutes, about $0.81.
  - Over about 1,000 lines: DocFlow can't read it yet. It fails as DOC-020
    ("enter this order by hand for now") after a paid read of up to about
    15 minutes and about $1.40. Tell the prospect before go-live that orders
    this long must be keyed by hand for now, and record it in your onboarding
    notes. Splitting long orders across several reads is deferred (D-158).

---

## 4. Console MFA (D-151, D-177)

The Console needs a code from an authenticator app as well as a password, once
`CONSOLE_MFA_ENFORCED` is on. With it on:

- every `/admin` page needs a sign-in confirmed with a code (an `aal2` session);
  until then the Console shows only the authenticator screen;
- **hard delete, clear quarantine, cancel, intake-address rotation, buyer merge,
  go live and tier change** also need a code entered in the last five minutes
  (`MFA_STEP_UP_MAX_AGE_SECONDS`, 300 s, plus `MFA_CLOCK_TOLERANCE_SECONDS`,
  30 s, for Supabase's clock). The Console asks for one and then carries on.

It ships **off**, so you can enrol before it can lock anyone out. While it is
off, three things say so: a warning in the API's startup log, an amber banner
on every Console page, and a high-severity founder alert ("Console MFA
enforcement is off") raised on the first Console request after each API start.

### 4.1 Switching it on, the first time

Do these in order. Don't turn it on before step 3 is confirmed.

1. **Enrol.** Sign in to the Console, open **/admin/security** (the banner
   links to it), choose **Set up an authenticator**, scan the QR code with an
   authenticator app, and enter the code it shows. The page then says "This
   sign-in is confirmed with your authenticator."
2. **Add a backup.** On the same page choose **Add a second authenticator
   (backup)** and enrol a second one -- in a password manager, or on another
   device. Supabase gives no backup codes (recovery codes were considered and
   left out, founder 2026-09-28), so a second authenticator is what gets you in
   if the phone is lost without needing 4.3.
3. **Confirm.** Tell Claude (or check in the Supabase dashboard under
   Authentication → Users → your user) that your account shows at least one
   **verified** TOTP factor.
4. **Turn it on.** Set `CONSOLE_MFA_ENFORCED=true` in the environment the API
   runs with (the root `.env` locally; the host's environment settings when
   deployed) and restart the API. The startup warning and the banner stop.
5. **Check it.** Sign out and in again: the Console should ask for a code
   before showing anything. Enter it. Then, more than five minutes later, try
   a destructive action (e.g. rotate a test tenant's intake address): it
   should ask for a fresh code first.

**Emergency lever.** If something is wrong and you are locked out of the
Console, set `CONSOLE_MFA_ENFORCED=false` and restart the API. The banner,
the startup warning and the founder alert come back while it is off -- that
is intended; turn it back on as soon as the cause is fixed.

### 4.2 docflow-prod: never deployed with it off after enrolment

**Once you have enrolled, `docflow-prod` is never deployed with
`CONSOLE_MFA_ENFORCED` off** (founder, 2026-09-28). The only exception is the
emergency lever above, used deliberately, for as short a time as it takes.
Check the deploy's environment settings for it as part of every production
deploy. In `docflow-prod`, also remember to turn public signup off (SETUP.md
Step 1.7) -- the two together are what keep the Console closed.

### 4.3 A lost or broken authenticator

If you still have your backup authenticator (4.1 step 2), sign in with it and
use /admin/security to add a replacement. Otherwise:

1. **Supabase dashboard** → the project → **Authentication** → **Users** →
   click your user → scroll to **Danger zone** → **Remove MFA factors** →
   confirm. **Not** "Ban user" and **not** "Delete user", which sit just below
   it. (Anyone who can reach this page can remove MFA from any account -- so
   your Supabase account itself must have MFA on, and so must the GitHub
   account that can deploy. They are the root of trust for this procedure.)

   **This removes every authenticator on the account at once** -- the
   dashboard has no per-factor delete -- so your backup goes too.
2. **Sign in to the Console again.** With enforcement on, it shows the
   authenticator screen with **Set up an authenticator**. Enrol a new one,
   **and a new backup**, as in 4.1 steps 1-2.
3. The lost device's codes stopped working at step 1; nothing else needs
   revoking. The founder alert log and `admin_actions` show nothing for this,
   because it happens in Supabase, not in DocFlow -- note it in your own
   records.

**Tested on staging, 2026-09-28** (D-177): a throwaway platform admin with a
verified authenticator; the founder used **Remove MFA factors** in the
dashboard exactly as written above; the account then had no authenticator,
signed in with its password alone, enrolled a replacement and passed a real
challenge with it. The account was then revoked and deleted.

### 4.4 Refused Console requests, and the alert (D-178, D-220)

With enforcement on, a Console request refused with **AUTH-006** (no
authenticator code on this sign-in) or **AUTH-007** (the code is more than
5 min 30 s old, for one of the seven destructive actions) is recorded in
`admin_actions` as `console_refused`, with the code, the route, and the
code's age. Only an active platform admin's sign-in can be refused this way;
anyone else gets the 404 first. So a refusal is you, or someone with your
password.

- **The alert:** the `CONSOLE_REFUSAL_ALERT_COUNT`th (5th) refusal for one
  account in `CONSOLE_REFUSAL_WINDOW_MINUTES` (15) raises
  `console_refusals_repeated`, high severity. One stays open per account
  until you acknowledge it.
- **Past the cap** nothing more is written for that account until the window
  rolls; each refusal is a log line, `console_refused_not_recorded`, with the
  code, the account id and the route.
- **When it fires:** if it was you (a stale tab, a code typed late), nothing
  to do. If it wasn't, change your password at once and look at the account's
  sessions in Supabase (**Authentication** → **Users** → your user).
- **What it can't see:** a wrong authenticator code (AUTH-008) never reaches
  DocFlow. It goes from the browser to Supabase, which limits code checks per
  IP address only (15 a minute plus a burst of 30, by default) and writes no
  audit entry for a wrong code. Per-account protection against guessing is a
  gate before the first pilot (D-220 item 6).

### 4.5 "Outcome unknown" on the Audit tab (D-178, D-220)

Every Console change records its outcome next to the request: **Done**, or
**Failed** with its catalog code. **"Outcome unknown: no outcome was recorded.
Check the tenant's current state before retrying."** means the request was
recorded and its result was not. What that covers depends on the kind of
change:

- **Console routes** (catalog and customer-list imports, test batch, go
  live, cancel, reactivate, buyer merges, learned rules, field schema,
  example prompting, quarantine, intake address, sender settings, card
  request): **Failed** when the route ends with a catalog code or a bare HTTP
  error (shown as its status). **Outcome unknown** when the request stopped
  midway: an unexpected error in the API (logged as `console_write_crashed`
  with the request's id), the machine stopped, or the outcome itself could
  not be written (logged as `console_outcome_not_recorded`). The change may or
  may not have happened. A request refused for bad input (422) leaves no line
  at all: it is checked before it is recorded.
- **Acting-as changes** (review and export inside a tenant): the same, with
  one more case. A request refused for bad input (422) also reads **Outcome
  unknown**, because acting-as records the request before its input is
  checked. Nothing ran.
- **The seven same-transaction actions** (create intake, add an intake file,
  create tenant, deal terms, tier change, invite, hard delete): the request
  is recorded in a transaction of its own before the work starts, and the
  work runs in one transaction. **Done** commits with the work. Any error,
  an unexpected one included, rolls the work back and reads **Failed** (with
  no code for an unexpected error; its type is in the outcome's detail).
  **Outcome unknown** when the machine stopped after the request was
  recorded, or the failure itself could not be written (logged as
  `console_failure_not_recorded`). The database work never half-commits,
  but a step outside the database (a Stripe call, a file removal) may
  already have happened. If the request itself cannot be recorded, the
  work never starts.
  - A creation's request names the new tenant only by its id, because the
    tenant doesn't exist yet when the request is recorded. The tenant's
    Audit tab shows the line all the same. A creation that never finished
    has no tenant, so its line is on no Audit tab. If it stopped after
    Stripe answered, the Stripe customer exists with no tenant: a failure
    deletes it, a stop cannot.
  - Intake changes belong to no tenant and appear on no Audit tab.

1. Look at the tenant's current state on the page the action belongs to
   (Overview, Lifecycle, Catalog...) before doing anything again.
2. For a tier change, check the subscription in Stripe too: Stripe is called
   inside the database transaction, so a failed commit after Stripe accepted
   leaves the two out of step (the failure record says `stripe: accepted`).
3. For a hard delete, a failure says how far it got: `check` (nothing
   changed), `storage` (some files may be gone) or `database` (the files are
   gone, the rows remain). Running the delete again finishes it.

Lines from before D-178 say **"Outcome not recorded (before D-178)"**: those
actions were recorded before outcomes were kept.

---

## 5. The worker: time limits, the prefork pool, and jobs that never finish (Stage 3a, D-179)

### 5.1 Production runs Celery's prefork pool

Every task has a **hard** time limit, and nothing else stops a parser hung
inside C code. Those limits only work under Celery's **prefork** pool on
Linux. The `solo` pool used on Windows for development **ignores them**.
Start production and staging workers with `--pool=prefork`. Never deploy one
with `--pool=solo` or `--pool=threads`.

The evidence that the limits work is `apps/worker/tests/test_time_limits_prefork.py`,
which starts a real prefork worker in CI (Linux). On Windows it skips.

### 5.2 The limits and related constants (`packages/core/docflow_core/constants.py`)

| Constant | Value | What it does |
|---|---|---|
| `DOCUMENT_TASK_TIME_LIMIT_SECONDS` | 27 min | Hard limit on reading one document. Under `STUCK_PROCESSING_TIMEOUT_MIN`, so a killed attempt is retried by the sweep, never raced by it. |
| `EXPORT_TASK_TIME_LIMIT_SECONDS` / `IMPORT_TASK_TIME_LIMIT_SECONDS` | 5 min each | Hard limits on building an export / reading a catalog or customer list. |
| `ROLLUP_TASK_TIME_LIMIT_SECONDS` | 15 min | Nightly KPI rollup. |
| `SCHEDULED_JOBS_TASK_TIME_LIMIT_SECONDS` | 10 min | Check-ins, reminders, digests. |
| `LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS` / `LIFECYCLE_SWEEP_TIME_BOX_SECONDS` | 10 min / 4 min | Past the time box the sweep takes no new tenant; the next tick takes the rest. |
| `STUCK_SWEEP_TASK_TIME_LIMIT_SECONDS` | 4 min | The stuck sweep. |
| `WORKER_MAX_MEMORY_PER_CHILD_KIB` | 500 MiB | A worker process is replaced after a task that took it past this. It isn't a cap during a task (the parse service's limits are, from 3c). 700 MiB until 2026-10-08; lowered by the founder after the memory measurement at the first worker deploy (D-196). |
| `STUCK_PROCESSING_TIMEOUT_MIN` | 30 | A document in `processing` this long is retried or failed (DOC-022). An export still `pending`, or an import still `parsing`, this long after it was created is failed (EXP-009 / IMP-009). |
| `MAX_PROCESSING_ATTEMPTS` | 3 | Tries before a document whose worker stopped is failed (DOC-022, cause `worker_stopped`). A timeout gets one retry only (cause `timeout`). |
| `EXPORTS_NOT_FINISHED_ALERT_PER_DAY` | 3 | A tenant with more EXP-009s than this in one UTC day raises one `exports_not_finishing` alert. |

A test reads the finalized Celery app and fails if any task gains a soft
limit, or if the document task's hard limit is no longer 27 minutes. So a
global `task_soft_time_limit` added later is caught (Celery would apply it to
every task that doesn't set its own, which is all of them).

### 5.3 When the founder gets one of these alerts

- **`document_stuck`, cause `timeout`:** the document hit its hard limit
  twice. It is the file, not the worker. Look for `document_timeout
  document_id=...` in the worker log. The customer sees DOC-022.
- **`document_stuck`, cause `worker_stopped`:** the worker died or was
  restarted under the document three times. Check the worker's restarts and
  memory.
- **`exports_not_finishing`:** a tenant's exports keep being lost or killed.
  Check the worker is running and consuming the `interactive` queue, and look
  for `export_` lines in the log. The tenant was told to start the export
  again (EXP-009); the health strip shows the day's total.
- **IMP-009 on an import in the Console:** start the import again from the
  same file. If it stops again, find the import's ID in the worker log.
- **`reactivation_invoices_to_review`:** a reactivation kept the tenant's old
  subscription, because Stripe still had it live after the suspension.
  DocFlow changed nothing at Stripe. In the Stripe dashboard, for that
  subscription: **collect** the invoices under "Before suspension" (service
  was delivered); **void** the drafts and open invoices under "During
  suspension", and **refund** the paid ones. If the alert says the invoice
  list couldn't be read, look at the subscription's invoices in Stripe
  directly.

### 5.4 A Stripe cancel still owed after a suspension

`tenants.stripe_cancel_pending_at` is set when a suspension claims a tenant
with Stripe billing, and cleared when the cancel goes through. Every
lifecycle sweep retries owed cancels first; a failure raises
`stripe_cancel_failed`. To see what is owed:

```sql
select id, name, status, stripe_subscription_id, stripe_cancel_pending_at
from tenants where stripe_cancel_pending_at is not null;
```

Reactivating the tenant clears the mark. Don't clear it by hand while the
tenant is still suspended: the subscription would keep billing.

## 6. Card billing: cancelling during the trial, and refunds (D-181)

### 6.1 A cancel request during the trial

Customers cancel by emailing `SUPPORT_EMAIL` (the card-billing emails say so;
they can't cancel in Stripe, because DocFlow's portal configuration has
cancelling turned off). The go-live and trial-ending emails ask them to
email **by the day before the trial ends** (the cancel-by date), which leaves
you a day to enter the cancel before Stripe charges. A request that arrives
on the trial's last day is still before the trial ends, so 6.2 applies to it.
When a request arrives:

1. **Note the time the request arrived** (the email's timestamp). That time
   decides the refund rule below, not the time you act on it.
2. Console → the tenant → Lifecycle → **Cancel**, reason **Customer
   requested**. For a tenant still in its trial, confirming it also tells
   Stripe to end the subscription at the trial's end and removes a pending
   setup fee, so nothing more can be charged. If Stripe doesn't answer, you
   see CON-006 and nothing has changed: try again.
3. Reply to the customer confirming the cancel and the date service ends
   (the trial's last day, unless you set a later date).

### 6.2 The refund rule (founder, 2026-09-29)

**A cancel request received before the trial ends gets a full refund of
anything charged after that request.** Examples: the request came in on day
6, but the cancel was entered after Stripe had already charged the first
month on day 7 -- refund that charge in full. The standard customer's setup
fee, paid when they added their card (before any request), is
non-refundable and is not part of this rule.

Refund steps, in the Stripe dashboard (check the account switcher first,
section 3):

1. **Customers** → search for the tenant (the Console's billing card has
   **Open in Stripe ↗**, which goes straight there).
2. Under **Payments**, find each payment dated **after** the request's
   arrival time.
3. Open the payment → **Refund** → **Full refund** → reason **Requested by
   customer** → **Refund**.
4. If the subscription is still running (the cancel was entered after the
   trial ended), check that the Console shows the tenant as cancelling, so
   no further month is charged.
5. Add a note in the Console's cancel (or the tenant's lifecycle log) with
   the request time and the refunded amount, so the record says why money
   went back.

Stripe returns the money to the card; it can take 5-10 business days to
reach the customer. DocFlow does not refund anything automatically.

## 7. File storage: Supabase Storage (Stage 3b, D-182)

DocFlow's files live in one private bucket, `docflow-files`, reached through
Storage's S3-compatible endpoint with a Storage-only access key. Nothing
reads the old local `storage/` folder any more except the copy script below.

### 7.1 Creating the key (once per project)

1. Supabase dashboard -> the project -> Storage -> **S3 Configuration**.
2. Copy the **Endpoint** into `STORAGE_S3_ENDPOINT` and the **Region** into
   `STORAGE_S3_REGION` in the root `.env`.
3. **New access key**, described "DocFlow API and worker". Copy the key id
   into `STORAGE_S3_ACCESS_KEY_ID` and the secret into
   `STORAGE_S3_SECRET_ACCESS_KEY`. The secret is shown once.
4. The same four values go into the API's and the worker's secrets on Fly.

The key reaches every file of every tenant. Treat it like the database
password: never in the frontend, never in a log, never in a ticket. If it
leaks, revoke it on the same page and create a new one.

### 7.2 Copying staging's files into the bucket (the 3b rollout)

Run from the repo root with `apps/api/.venv`'s Python, on the machine that
holds the `storage/` folder:

    python scripts/copy_storage_to_bucket.py            # dry run first
    python scripts/copy_storage_to_bucket.py --apply    # then the copy

It copies every file a row still references under the same key, reads each
one back and checks its SHA-256, never overwrites something different that
is already in the bucket, and leaves the local folder untouched. A clean run
exits 0 and lists nothing. **Run it again with `--apply` straight after the
switch** (the delta copy): it picks up anything the old code wrote in the
meantime; "referenced but missing locally" must then be 0.

The orphan count is files no row references (from failed uploads, and old
test runs). They are left where they are.

### 7.3 When the founder gets a `storage_unavailable` alert

At most one an hour for the whole platform; it names the first tenant that
hit it. While Storage is down: uploads answer DOC-025 and nothing is
received; Postmark gets a 503 and sends the mail again later (**up to 10
more times over about 10 hours 20 minutes:** after 1, 5, 10, 10, 10, 15
and 30 minutes, then 1, 2 and 6 hours; it stops at once on a 403, which
intake never sends for this. Postmark's inbound webhook documentation,
"Errors and retries", read 2026-10-05. **After the last try Postmark
shows the message as "Inbound Error" and nothing sends it again by
itself:** after an outage longer than that, retry those messages from
Postmark by hand); documents
already received go back to waiting (Stage 3d: `pending`, `wait_cause =
storage`) without using up their tries. Check the Supabase status page and
the project's Storage logs. Nothing needs doing once Storage is back: the
dispatcher sends each waiting document again every
`STORAGE_WAIT_RETRY_MINUTES` (5), with no maximum.

**Reading the Storage logs** (Supabase dashboard, Logs, Storage; learned
on 2026-10-05, D-192). The dashboard shows local time. An export stops at
500 rows, newest first (both exports that day did), so choose a range of
20 minutes or less around the failure, and export a second range if the
file has exactly 500 rows. Look for `ABORTED RES` on the upload's path:
Storage received the request and the caller hung up before the answer (our
client waits 30 seconds, up to three times). Look also for `[Queue Sender]
Error` rows near it, which are Storage's own. **A timed-out upload can
still be finished by Storage afterwards.** That file has no row pointing
at it, so nothing can reach it, and it goes when the tenant is deleted.

A `document_failed` alert with **DOC-026** is different: the stored original
is missing or isn't the file that was received. That is never an outage. Ask
the customer to upload the file again, and look for how the object went
missing.

## 8. The parse service (Stage 3c)

The parse service opens every file a stranger sends, inside a per-file
sandbox (BUILD-STATUS "3c detailed design"). More of this section is
written as 3c is built: running it, deploying it, reading the canary's
startup log, the parser-upgrade process, and the
`parse_service_unavailable` alert.

### 8.1 Gate: the Fly self-tests pass before a merge or a production deploy that touches it (founder, 2026-10-01)

CI runs on cgroup v2. Fly machines run cgroup v1, the path production
uses, and **only a Fly run proves that path**. So the self-tests must pass
**on Fly staging** before a change that touches the parse service merges to
`main`, and again before any production deploy of it, not only at the 3c
checkpoint (founder, 2026-10-01: `main` is what every later stage builds on,
so it must not carry a sandbox whose production path is unproven): all of them, `python -m
parse_service.selftest all` (the canary, A, S and B; BUILD-STATUS "3c test
table", B1-B16 and A15 included). **IPv6 isolation is proven only there:**
CI's runner has no IPv6, so A-net IPv6 is NOT-RUN in CI (D-183).

"Touches the parse service" means any change to:
- its code (`apps/parse/`), its Dockerfile, base image or packages
  (LibreOffice and `python3-seccomp` included), or its lock file;
- its `fly.toml`;
- the worker's `parse_client`.

**Setting secrets on Fly (founder, 2026-10-01):** start a new PowerShell
window and run `Set-PSReadLineOption -HistorySaveStyle SaveNothing` before
typing anything else. Values typed literally (a database URL, S3 keys, an
API key) are otherwise saved in plain text in PowerShell's history file. It
applies to that window only; close it when done. Never paste a value into
chat. Each app gets only the secrets its process reads, and its own
database login (F-1, section 10): never `docflow_app` shared between apps,
never `postgres` or the service role.

The procedure:
1. Deploy the change to Fly staging first, after section 11's dependency
   audit passes.
2. Check the canary's startup log: every line PASS.
3. Run the self-tests on Fly staging (every line PASS; A-net IPv6 must be
   PASS there, not NOT-RUN), and keep the output in
   `docs/spikes/3c-fly-staging/` with the date and the image digest.
4. Only then merge, and later deploy to production (after its own Fly
   staging run of the merged code).

If any self-test fails on Fly, nothing merges and nothing goes to
production: stop and report (1.7).

**What gates the 3c merge, and what waits (founder, 2026-10-01).** The
worker and the API are not on Fly until F-1's own logins exist (Stage 3e),
so the 3c merge gate is the parse app alone, every item on the Fly machine:
- the canary (8.3), every line PASS;
- every A, S and B self-test PASS, A-net IPv6 PASS (not NOT-RUN);
- **A4 against a stand-in:** a TCP listener on the private network, in a
  throwaway app, passed with `--targets`. The control (reached from outside
  the sandbox on the parse machine) runs first and must succeed; inside a
  job the same address must be blocked. NOT-RUN fails the gate;
- **N1:** `fly ips list` shows one private IPv6 and nothing else;
- **N2:** from outside Fly, the app's public name resolves to no public
  address and nothing answers;
- **N3:** with the parse machine stopped, a client machine in a throwaway
  app calls `http://docflow-parse-staging.flycast/...` with no token. A
  401 proves the request started the machine and the token is enforced.

The throwaway app is destroyed afterwards; its name, machine IDs and times
go in the evidence.

**These gate the first worker/API deploy, not the 3c merge:** G (end to end
through the queue and the parse service), and A4 again against the real
staging API and worker. At that deploy, generate a fresh
`PARSE_SERVICE_TOKEN` and set it on the parse app and the worker together;
the 3c token is not carried over.

### 8.2 Running it

- **Dev (this Windows machine):** SETUP.md Step 7a. `PARSE_ISOLATION=off`,
  one process per file and the same time limit, no sandbox. Refused in
  production mode.
- **CI:** the `parse` job builds `apps/parse/Dockerfile` and runs it with
  `--privileged` (the closest a GitHub runner gets to root in a Fly VM).
  Its self-test, E1, E4 and canary results are annotations on the run's
  summary page.
- **Fly:** the same image. Production mode is on (Fly sets `FLY_APP_NAME`):
  isolation must be on, `PARSE_SERVICE_TOKEN` must be set, and any
  `PARSE_TEST_ONLY_*` setting makes it refuse to start. Private only, over
  Flycast (`fly ips list` must show one private IPv6 address and nothing
  else, after every deploy). Staging deploys with `--ha=false` (one
  machine). **That is staging only:** production's machine count is a
  Phase 6 decision and does not carry over by default (founder,
  2026-10-01).
- **Secrets:** `PARSE_SERVICE_TOKEN` goes on the worker's app and the parse
  service's app, never the API's. If it is set on the API, the API refuses
  to start ("DocFlow API refused to start: PARSE_SERVICE_TOKEN is set ...")
  and Fly keeps restarting it: `fly secrets unset PARSE_SERVICE_TOKEN -a
  <api app>`.

### 8.3 Reading the startup log (the canary)

Before it opens its port the service runs its canary through the real
sandbox and prints one line per check:

```
canary: cgroup v1
RESULT canary:network PASS -- {...}
RESULT canary:view PASS -- {...}
RESULT canary:seccomp PASS -- {...}
RESULT canary:memory-cap-kills PASS -- {...}
RESULT canary:cpu-quota-set-and-budget-kills PASS -- {...}
canary: PASS
parse service listening on port 8100
```

Any FAIL, or "refused to start", means the machine cannot isolate a file:
the service exits, Fly restarts it, it fails again, and the worker gets
"unavailable" -- documents wait, nothing is parsed unsandboxed, and the
`parse_service_unavailable` alert fires. **Stop and report (1.7); never
switch isolation off to get it running.**

### 8.4 When the founder gets a `parse_service_unavailable` alert

At most once an hour for the whole platform. The payload's `reason`:
- `no_connection` / `http_503`: the service is down,
  overloaded, or its canary is failing. Check the app's machines and its
  startup log (8.3). Documents wait (`wait_cause = parse_service`) and the
  dispatcher sends them again every `PARSE_SERVICE_WAIT_RETRY_MINUTES` (2);
  nothing is failed for waiting (Stage 3d).
- `unauthorized`: the worker's and the service's `PARSE_SERVICE_TOKEN`
  differ. Set the same value on both apps.
- `http_4xx`: a request the service refused (a DocFlow bug, never the
  file's fault). Report it.

A **DOC-022** alert whose detail has `last_lost_reason` (`http_502`,
`http_504`, or a read error such as `ReadTimeout`) means the parse requests
got in and never came out: a 502/504 is Fly's proxy (the machine died or
didn't start), not a parser crash, which arrives as a `crashed` answer and
DOC-030 (8.6).

**A `parse_seccomp_kill` alert** (founder, 2026-10-01: every one, never
rate-limited) means a parse job was killed by the seccomp filter (SIGSYS).
The filter kills only a system call from a foreign architecture (x32 or
32-bit), which no parser makes by accident: treat the file as an attack.
The alert has no syscall number (the kill reports none; only the kernel's
audit log on the parse machine has it, in `fly logs` if the kernel printed
it). The document failed with DOC-030 and its keyed alert (8.6) as well
(since PR 1b-i; an import with IMP-004); nothing else needs doing in
DocFlow. Keep the file's hash from the alert's
document for any follow-up.

### 8.5 Upgrading a parser (an ongoing duty, CLAUDE.md 7.11)

Every library in the image reads files strangers send. When a CVE lands in
one (pdfplumber, python-docx, openpyxl, Pillow, pillow-heif, olefile, xlrd,
defusedxml, LibreOffice, libseccomp, or the base image):
1. Move the pin: `apps/parse/requirements.lock.txt` for a Python package;
   `DEBIAN_SNAPSHOT` in `apps/parse/Dockerfile` for LibreOffice, libseccomp
   or any other Debian package (a snapshot.debian.org date, e.g.
   `20261008T000000Z`: every Debian package in the image comes from the
   archive as it was then); the digest for the base image.
2. CI must pass in full: the unit tests, D2 parity (every fixture's text
   must stay the same), and the self-tests in the real sandbox.
3. Then 8.1: deploy to Fly staging, check the canary log, run all the
   self-tests there.
4. Only then merge (the weekly job's PR included), and deploy to
   production.

**The weekly Debian move (D-183).** Every Monday at 06:00 UTC
`.github/workflows/debian-snapshot.yml` runs the whole CI workflow with the
image built from that day's snapshot. Green: it pushes
`deps/debian-snapshot-<date>` with the new date, and the run's summary page
has the PR link. Open the PR, let its own CI run pass, merge, then 8.1.
Red: nothing is pushed and GitHub emails the failure; read the run's
annotations (D2 parity and the self-tests name what changed) and treat it
as step 2 failing. Run it by hand any time from the Actions tab ("Run
workflow"), for example the day a LibreOffice CVE is announced, rather than
waiting for Monday.

A build never changes by itself: until the date moves, a rebuild installs
exactly what the last one did. If snapshot.debian.org is down, the build
fails; it never falls back to the live archive.

**The daily dependency audit (founder, 2026-10-23; D-219).**
`.github/workflows/dependency-audit.yml` audits what every app ships, every
day at 06:30 UTC (section 11 says what blocks). **A production advisory it
finds gets a fix PR the next working day:** the pin moves in that app's
lock file (for parse, steps 1 to 4 above), and the PR's own CI run blocks
on the advisory until it is fixed. A failed run is GitHub's email to
whoever last changed the workflow's schedule line; failed-workflow emails
must be on in that account's notification settings. GitHub disables a
scheduled workflow in a public repository after 60 days without activity,
and emails a warning first: re-enable it from the Actions tab.

### 8.6 When the founder gets a DOC-030 alert (D-203, PR 1b-i)

A `document_failed` alert with `error_code` DOC-030 means a parse job
failed **on our side, not the file's**. The customer was told "We're
already on it" and asked to hold off resending until DocFlow says it's
fixed. Keeping that promise is this step.

**What the alert says.** It is raised once per state: the tenant, the step
(`parsing`), and what failed, in the **parse app's** release. An
acknowledged alert stays quiet; the Console's "Affected documents: N" and
"Acknowledged, still happening" count the documents that carry its key
(`documents.fault_keys`). The payload's `exception_type` and `location`:
- **a Python type and `module:function:line`** (an `error` answer): an
  exception no parser raises on a malformed file reached our code there.
  The parse app's log has `job_error kind=... error_type=... location=...`
  and the worker's `parse_error document_id=... error_type=... location=...`.
  Never a message: neither log carries one.
- **`crashed` and a cause** (`signal_11`, `exit_1`,
  `self_reported_memory_error`, `invalid_output`): the job died without an
  answer. A `signal_31` is a seccomp kill and has its own alert too (8.4).
- **`invalid_answer` and a reason**: the worker refused the service's
  answer; the release is `unknown`.

**1. List the affected documents, read-only, from the record** (the alert
is only the trigger; founder, 2026-10-02). As `docflow_admin`, in the SQL
Editor, with the alert's `state_key` from the Console:

    SELECT tenant_id, id, created_at, processed_at
    FROM documents
    WHERE fault_keys @> ARRAY['<the alert's state_key>']::text[]
    ORDER BY tenant_id, processed_at;

Every DOC-030 document since a date, whatever its state:
`WHERE failure_code = 'DOC-030' AND processed_at >= '<date>'`.

**2. Fix it**, with a test that reproduces the fault. If a mutation-corpus
case or a customer file shows a parser raising a type we didn't list for
its block, add the type to that block (`conversion.py`, `documents.py`,
`tables.py`) and the case to the corpus; never widen a block back to
`Exception`.

**3. Deploy: the worker first, then the parse app** (an older worker reads
an `error` answer as unreadable). The parse app's deploy follows 8.1.

**4. E-mail each affected tenant once the fix is deployed**: which
documents (by received date and the customer's own reference, never the
contents), that the problem was DocFlow's, and that they can now send them
again. Nothing re-reads a failed document by itself (`failed` is final;
the founder re-run is a Phase 6 item). **A re-upload works; an e-mail must
be a new forward**, since a redelivery of the same message (its
Message-ID) is dropped as a duplicate.

**Also DOC-030, without a document:** an e-mail attachment whose check
failed on our side is refused as DOC-030 at intake and the rest of the
e-mail is read (`email_attachment_validation_fault` in the API log). It is
in `intake_rejections` with `error_code = 'DOC-030'`. Its alert has step
`email_intake` and names the rejection (`intake_rejection_id`), never the
e-mail; the Console shows it as "Email intake, no document". List them:
`SELECT id, created_at FROM intake_rejections WHERE error_code = 'DOC-030'
AND created_at >= '<date>'`. The sender is in that row, for the step 4
e-mail. **An import** that hits our bug shows IMP-004 until PR 1b-ii
(`import_parse_error` in the log).

## 9. The dispatcher and waiting documents (Stage 3d)

Since 3d nothing sends a document straight to the queue. A new, released or
test-batch document waits as `pending`, and the dispatcher
(`docflow_core/dispatch.py`, in the worker) sends the next ones, taking turns
between tenants: fewest in flight first, then the tenant served least
recently. At most `TENANT_IN_FLIGHT_CAP` (2) per tenant while another tenant
has something ready, and never more than `DISPATCH_IN_FLIGHT_TARGET` in
flight across all tenants. A dispatch pass runs after every intake, at the
end of every document task, and from beat every 30 seconds.

### 9.1 Cutover order (the 3d rollout, and any fresh environment)

1. Back up and apply `0035` (section 1; `backup_0035`: documents,
   founder_alerts, email_outbox).
2. Deploy the worker with its `beat` process (9.3).
3. Then deploy the API, which stops sending the document task.

On staging the worker and API are not on Fly until 3e (their own database
logins); until then step 2 is the local worker and beat (README), and only
one stack runs against staging at a time (9.3).

An API running without a dispatcher behind it leaves every new document
waiting. `dispatcher_stopped` says so within `DISPATCHER_STALE_MIN` (10
minutes), and `/healthz` shows `"stale": true`.

### 9.2 Adding worker capacity

`DISPATCH_IN_FLIGHT_TARGET` (a setting, staging 1) is the worker's document
slots. The worker machine runs one command, `python -m app.run_workers`,
which starts two Celery workers and keeps them together (if one exits, it
stops the other and exits non-zero, and Fly restarts the machine):
- `documents`: `DISPATCH_IN_FLIGHT_TARGET` processes, reading `interactive`
  and `bulk` (documents, exports, imports, sweeps);
- `dispatch`: one process, reading only the `dispatch` queue. It never
  claims a document, so a long order can't hold up the heartbeat.

With one worker machine:
1. Set `DISPATCH_IN_FLIGHT_TARGET` to the new number of slots, on the worker
   app AND the API app (the API doesn't use it today, but keep them equal).
2. Size the machine for two Celery main processes, the dispatch process and
   one process per slot (each document process may reach
   `WORKER_MAX_MEMORY_PER_CHILD_KIB` before it is replaced). Measure it on
   the machine (below) before relying on it.
3. Deploy the worker.

**The first worker deploy is gated on all of these** (founder, 2026-10-01;
the same list as BUILD-STATUS "3d on staging"):
- G, and A4 against the real API and worker (section 8, from 3c);
- **a fresh `PARSE_SERVICE_TOKEN`**, generated by the founder and set on the
  parse app and the worker together (agreed in 3c);
- the 500 + 1 staging run, its budget to the founder first;
- the memory measurement below, and the founder's choice;
- 3e's restart record: starts recorded, starts in the last hour on
  `/healthz`, and `worker_restarting` (high) at 3 or more starts in 60
  minutes, raised by the launcher;
- **the external heartbeat, set up and tested** (9.4: Healthchecks.io,
  decided in 3e), with its pass marks met;
- **the two two-at-once sweep tests** (founder, 2026-10-01): the
  scheduled-jobs sweep and the stuck sweep, each run twice at once against
  the real database, built in 3e and passing (9.3);
- **the logins cutover done and verified** (section 10), the worker on
  `docflow_worker` and the API on its three.

The order these are met in, who does each step and where the founder's
word is needed: 9.7.

**Straight after the worker starts, at the first deploy** (founder,
2026-10-05; D-189): confirm that the start was recorded and that `/healthz`
reflects it. No test commits a start on staging (the one that does runs
only on CI's own database), so this is where it is first seen for real.
1. Before the deploy, read `worker_starts_last_hour` on the API's `/healthz`
   (the deployed API, or the API run on this machine against staging). The
   suites leave no row, so with no worker start in the last hour it is 0.
2. Deploy the worker. When the machine is up, the founder runs, in the SQL
   Editor on `docflow-staging` (no login the apps use can read this table):
   ```sql
   select id, started_at, machine, image from worker_starts order by started_at desc limit 5;
   ```
   **One new row:** `started_at` at the deploy, `machine` the Fly machine's
   id, `image` the deployed image.
3. `/healthz` again: `worker_starts_last_hour` is step 1's number plus one.
4. If there is no row, more than one, or `/healthz` did not move by one,
   stop and report before the deploy's other checks: a second row is a
   second start, which is what `worker_restarting` counts.

**Measuring the worker machine's memory** (founder, 2026-10-01). The worker
and the API stay off Fly until 3e gives them their own database logins, so
this happens at **the first worker deploy after 3e, with G and the 500 + 1
staging run**, never before. It does not gate the 3d merge. At that deploy,
with `fly ssh console -a docflow-worker-staging --process-group worker`
(Fly is cgroup v1):

```
cat /sys/fs/cgroup/memory/memory.usage_in_bytes      # the machine, now
cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes  # the machine, peak since start
ps -o pid,ppid,rss,args -u docflow                   # each process, KiB, now
grep VmHWM /proc/<pid>/status                        # each process, its own peak
```

1. Idle: both Celery main processes, the dispatch process, the document
   process.
2. Under real documents, **including `.doc` and scanned PDFs** (the
   heaviest): the same four, the document process at its peak.
3. Expect 700 MiB per process (`WORKER_MAX_MEMORY_PER_CHILD_KIB`) not to fit
   with headroom on 1 GB: a document process at the limit plus three others
   at roughly 100 MiB each is about the whole machine. Bring the founder the
   numbers and two options: a lower per-process limit (around 550 MiB, if
   real documents stay well under it), or a 2 GB worker machine and its
   monthly cost. **Change neither until the founder chooses.** Record the
   numbers and the choice in BUILD-STATUS.
   *(Measured 2026-10-07 and 2026-10-08, D-196. The founder chose both: a
   2 GB worker for production, and the per-process threshold lowered from
   700 to 500 MiB. The largest file a tenant can upload took a document
   process to 466 MiB; a 22 MB scan to about 330 to 360 MiB. With the
   threshold at 500 MiB, the four other processes (about 420 MiB together
   at idle) and one document slot, a 1 GB machine is still tight and a
   2 GB one has room.)*

**When the worker keeps restarting.** `fly.toml` sets the `worker` group's
restart policy to `on-failure`, 10 retries. Fly counts those within a
5-minute window (its restart-policy docs, read 2026-10-01); after the 10th
it leaves the machine stopped. Check it on the deploy with
`fly machine status <id>` (the restart policy is listed).
- **Down and staying down** (it can't start, or ran out of retries): no
  dispatch passes, so the heartbeat ages and `/healthz` reads
  `"stale":true` after `DISPATCHER_STALE_MIN` (10 minutes). Only `/healthz`
  can see this: `dispatcher_stopped` is raised by the stuck sweep, which runs
  on the same machine. The external monitor (9.4) is what tells the founder.
- **Restarting slowly** (up for a few minutes, then a worker exits, again
  and again): each start's dispatch process runs passes before the next
  exit, so the heartbeat can stay fresh and `/healthz` won't show it. Today
  this is visible only in `fly logs` (`run_workers: ... exited; stopping the
  other`) and in Fly's machine events, and indirectly as `document_stuck`
  alerts for documents caught mid-read. **3e closes this before the first
  worker deploy** (founder, 2026-10-01): the launcher records each start,
  `/healthz` shows the starts in the last hour, and an alert fires past a
  threshold (BUILD-STATUS "3e -- F-1").

More than one worker machine is a Phase 6 decision. Until then,
`DISPATCH_IN_FLIGHT_TARGET` must equal one machine's slots. The dispatcher
would otherwise keep more documents in flight than any worker can take.

### 9.3 Beat: exactly one, never two at once

Beat is its own process, never `celery worker -B`. Locally it is the
README's second terminal. On Fly it is the worker app's `beat` process group
(`apps/worker/fly.toml`):
- **Never scale `beat` above 1:** `fly scale count worker=1 beat=1`.
- **Never deploy the worker app with `--strategy canary` or `bluegreen`.**
  Both start a new machine beside the old one, which means two beats for a
  while. `fly.toml` pins `strategy = "rolling"`, which updates the machine in
  place.
- **Run only one stack (local or Fly) against staging at a time.** Two
  dispatchers on one database would share the in-flight count but send to
  two different queues.

If two beats ever do run, nothing breaks: every beat task is safe to run
twice. The beat schedule is in `apps/worker/app/celery_app.py`
(`beat_schedule`, five entries), and its design record is BUILD-STATUS "3d
-- APPROVED WITH CHANGES", change C.

| Beat task (interval) | The guard (code) | The test |
|---|---|---|
| `run_scheduled_jobs` (5 min) | Jobs claimed `FOR UPDATE SKIP LOCKED` (`scheduled_jobs.py` line 70) | `test_sweeps_twice_at_once_db.py::test_two_scheduled_job_sweeps_at_once_run_each_due_job_exactly_once` (3e) |
| `run_daily_rollup` (03:15 UTC) | Upsert `ON CONFLICT (tenant_id, day) DO UPDATE` (`metrics.py` line 155); `rollup_stale` deduped | `test_dashboard_api.py::test_running_a_day_twice_leaves_the_same_row` (one after the other; two at once rely on Postgres's `ON CONFLICT`) |
| `run_lifecycle_sweep` (5 min) | Compare-and-set claim with the tenant row locked `FOR UPDATE` across the Stripe calls (`lifecycle.claim_for_suspend`) | `test_lifecycle_api.py::test_the_sweep_never_claims_a_tenant_twice` |
| `sweep_stuck_documents` (5 min) | Every status change compare-and-set (`document_status.transition`); lost-call rows `ON CONFLICT DO NOTHING`; alerts deduped | `test_sweeps_twice_at_once_db.py::test_two_stuck_sweeps_at_once_change_and_alert_each_document_once` (3e). A retry may be enqueued by both sweeps (it changes no status); the task's claim runs it once, tested in the same test |
| `dispatch` (30 s) | Transaction advisory lock `pg_try_advisory_xact_lock(3352026100)` (`dispatch.py`): a second pass returns at once | `test_dispatch_db.py::test_twenty_concurrent_passes_dispatch_every_document_exactly_once` |

Two rows rest on the guard alone, with no test that fires the task twice:
the scheduled-jobs sweep and the stuck sweep. **Founder, 2026-10-01 (at
the 3d merge): both tests are built in 3e**, not 3d and not Stage 5. **Built
in 3e** (the table above): each runs its sweep twice at once, in two
threads released together, against the real database. They gate the first
worker deploy (9.2): passing in CI and on staging.

### 9.4 The external uptime monitor (from the first worker deploy)

**Decided in 3e (founder, 2026-10-02): a push heartbeat for the worker, on
Healthchecks.io's free plan** (Q1; BUILD-STATUS "3e detailed design", part
C). A polled check of `/healthz` is for production's public API only, chosen
in Phase 6 (the notes further down). Staging's API stays private.

**What it covers.** After a dispatch pass that succeeded, the dispatch
process pings `HEARTBEAT_URL` (`docflow_core.heartbeat`). The pings stop when
the worker is down, crash-looping because it can't reach the database, or
its documents worker has exited (the launcher stops both). And it sends
`/fail`, which alerts at once, when a document has been dispatched and not
claimed for `DISPATCH_UNCLAIMED_ALERT_MIN`: a documents worker that is up
but not taking work. **Not covered here**, each with its own alert: a
document hanging inside a task (`document_stuck`), the parse service
(`parse_service_unavailable`), the model provider (`model_api_failure`).

**Setting it up** (once per environment; the founder):
1. healthchecks.io -> sign up (one account; using several to get round the
   limits is against its rules) -> **Add Check**: name
   `docflow-worker-staging`, schedule **Simple**, **Period 5 minutes**,
   **Grace 5 minutes** (Q7). Integrations: e-mail to the founder address.
2. Copy the check's ping URL (`https://hc-ping.com/<uuid>`). It is a
   secret: whoever has it can mark the worker healthy.
3. `fly secrets set HEARTBEAT_URL=... --app docflow-worker-staging --stage`
   (typed, history off, as for every secret; RUNBOOK 8). On the worker
   only, never the API.
4. Production in Phase 6: its own check (`docflow-worker-prod`) and URL.

**If the ping URL has been seen where it should not be** (it was on
2026-10-06: the worker's own log, D-194): add a new check with the same
settings, set its URL as in step 3, deploy or restart the worker, see the
first ping arrive on the new check, then delete the old check. The check's
page shows the URL in full, so crop it out of any screenshot.

**Throttle.** At most one ping per `HEARTBEAT_PING_MIN` (5), so 288 a day;
Healthchecks.io records at most 5 a minute per check. A change between
success and `/fail` goes at once. Passes run every 30 s, so pings land 5:00
to 5:30 apart, inside the grace.

**Pass marks, tested at the first worker deploy (gate list, 9.2):**
- stop the worker (`fly scale count worker=0 --app docflow-worker-staging --yes`):
  the Healthchecks.io e-mail within **12 minutes** of the last ping (5 to
  the expected ping, 5 grace, about 1 for e-mail, 1 of slack, since its docs
  don't say how often lateness is evaluated). Record the measured time.
- start it again: the recovery e-mail after the first ping.
- stop the documents worker taking work with the dispatch process running:
  the `/fail` e-mail within `DISPATCH_UNCLAIMED_ALERT_MIN` plus one pass.
- record the largest unclaimed age seen during the 500 + 1 run (founder,
  Q2). **It must include the slow case** (founder, Q9): partway through,
  trigger the rollup by hand (the Console's "recompute") and a sweep, so a
  document waits behind them. Well under 20 minutes: keep 20. Near 20:
  back to the founder before changing anything. Never raise it to 30 or
  more: the stuck sweep returns an unclaimed document to waiting at 30 and
  its next dispatch restarts the age, so the `/fail` could never fire.

The rest of this section is about **the polled check for production's API
(Phase 6)**, written on 2026-10-01 before 3e decided. The keyword itself is
settled and pinned by a test.

**Needed from the first worker deploy, not Phase 6** (founder, 2026-10-01).
Once the worker is on Fly, nothing inside DocFlow can report these:
- a worker machine that is down;
- a worker crash-looping because it can't reach the database.

`dispatcher_stopped` (the stuck sweep) and `worker_restarting` (the
launcher, 3e) both need a running worker that can reach the database. The
heartbeat going stale is the one signal left, and only `/healthz` shows it.

A polled monitor would check the API's `GET /healthz` (no sign-in) and
alert when **either**:
- the endpoint doesn't answer (the API itself is down), or
- the body does **not** contain `"stale":false`.

That keyword is exact: compact JSON, **no space after the colon**.
`test_healthz_dispatcher.py` pins the bytes and checks this section gives
them. The response is always HTTP 200, so the monitor must match the body,
not the status. A database the API can't reach also reads `"stale":true`.

#### Notes for the 3e design (not a setup)

What was found on 2026-10-01, in short:
- a 120 s confirmation period is possible (Better Stack's API takes it in
  seconds);
- whether Better Stack re-checks inside the confirmation period is
  undocumented, so assume the confirming check is the next 3-minute one;
- Fly's auto-stop loop runs "every few minutes", so with 3-minute checks the
  API may stay awake or be cold-started;
- the staging API is private-only, so Better Stack can't poll it.

The detail follows. No more research on it until the 3e design (founder).

**Better Stack Uptime, free plan** (approved by the founder, 2026-10-01).
Read 2026-10-01: 10 monitors, HTTP(s) keyword checks, e-mail and Slack
alerts, no restriction on commercial use stated. **Correction (2026-10-02,
3e design):** the pricing page's free-plan heading reads "Free for personal
projects"; the Terms of Use say nothing about plan type. The tool is
re-decided in 3e (BUILD-STATUS "3e detailed design", C1, Q1). Its check-frequency page
says the interval runs "from 3 minutes for free plans to 30 seconds for paid
plans", so **3 minutes is the free plan's fastest**. UptimeRobot's free plan
is out: its page limits it to "hobby and non-profit projects". Re-read the
terms on the day it is set up.

A polled check, if 3e chooses one, would be set up like this (Monitors ->
the monitor -> Configure -> Advanced settings):
- an HTTP keyword monitor on `https://<api>/healthz`, alerting when
  `"stale":false` is absent;
- check frequency **3 minutes**;
- **confirmation period 120 seconds** (founder, 2026-10-01). Better
  Stack's API takes it in seconds, so 120 can be set exactly; if the
  dashboard's dropdown has no 2 minutes, take the nearest and write it
  down. Why not "immediate": the 10-minute stale wait protects only the
  stale path. The "no answer" path would page on a single dropped request
  (a network blip, a slow Fly proxy, a hiccup at Better Stack's probe),
  and a monitor that cries wolf gets ignored.
- **request timeout 30 seconds** (the options are 2, 3, 5, 10, 15, 30, 45,
  60): enough for an API machine that Fly has auto-stopped to start and
  answer (below);
- recovery period: write down the value (the API's default is 0);
- e-mail to the founder's alert address.

**How long the alert can take, worked out from the settings.** Measured
from the moment the worker is stopped (S) to the founder's e-mail:

| Step | Worst case | Why |
|---|---|---|
| Last heartbeat | at S, or up to 30 s before | Beat sends a pass every `DISPATCH_INTERVAL_SECONDS` (30), and the dispatch process has nothing else to do |
| `/healthz` reads stale | **up to 10:00** after S | stale once the heartbeat is more than `DISPATCHER_STALE_MIN` (10) minutes old: between 9:30 and 10:00 after S |
| The monitor's first failing check | **up to 3:00** more | the 3-minute check frequency |
| Confirmation | **up to 3:00** more | the period is 2:00, but Better Stack's docs don't say whether it re-checks inside it; at worst the confirming check is the next one, 3:00 later |
| The e-mail | about 1:00 | delivery; not ours to set, an allowance |

**Worst case = 17 minutes** (16 to the incident, plus a minute for the
e-mail). If Better Stack does re-check inside the confirmation period, the
alert comes at about 16; 17 is the pass mark either way.

**Test it on the day:**
1. Note the time, then stop the worker.
2. **Pass:** the alert e-mail arrives no later than **17 minutes** after
   the stop.
3. **Investigate if it arrives earlier than 9:30.** That alert didn't come
   from the stale heartbeat, so something else failed (the endpoint
   itself?).
4. Start the worker again. The next pass writes the heartbeat within 30 s,
   and the monitor sees it at its next check. **Pass:** the incident
   resolves within **3:30 + the recovery period**.
5. Record the times, the confirmation and recovery periods as set, and the
   request timeout in BUILD-STATUS.

**The API's auto-stop and the monitor.** On staging, `apps/api/fly.toml`
has `auto_stop_machines = "stop"`, `auto_start_machines = true` and
`min_machines_running = 0`. Fly's stop loop "runs every few minutes and
stops at most one Machine per region per pass" (Fly's autostop docs, read
2026-10-01). The docs don't say exactly when an idle machine counts as
spare, so with a request only every 3 minutes, assume both can happen:
- **It stays awake.** The checks keep it running, so staging pays for the
  API all month: shared-cpu-1x 512 MB, about $3.19 a month at the rates
  read 2026-09-30. That's small, and accepted rather than fought.
- **It is stopped between checks.** The next check starts it (cold start).
  Our own measurement of a Fly cold start is 3c's N3: a request to the
  stopped parse machine was answered in 5.5 s, the machine started by that
  request. The API's start time is measured on the day (a check against a
  stopped machine). The 30-second request timeout covers both with margin,
  and the 2-minute confirmation absorbs a one-off slow start.

**Production** has no `fly.toml` yet (Phase 6). Its API should run with
`min_machines_running = 1` regardless of the monitor, so that a customer is
never the one who waits for a cold start.

**The staging API is private.**
`apps/api/fly.toml` gives it no public IP. It is reached over `fly proxy`
or Flycast from other Fly apps (3c, N1/N2). Better Stack's probes are on the
public internet, so as configured they can't reach `/healthz` at all.
**3e chose the push heartbeat above** for the worker. Production's API will
be public (browsers call it, and Postmark's and Stripe's webhooks must reach
it), so its own polled check is chosen in Phase 6 with these notes. Better
Stack's free plan is labelled "Free for personal projects" on its pricing
page (read 2026-10-02), so the tool is re-decided then.

**Not built: a status-code endpoint** (a 503 when stale), suggested by the
founder as optional. It is less fragile than matching text, but it must
never be the path Fly's own health check uses, or Fly would restart a
healthy API whenever the worker is down. With the keyword pinned by a test
it isn't needed now.

The Console's health strip shows the same heartbeat, red past
`DISPATCHER_STALE_MIN`, but only while someone is looking at it.

### 9.5 When the founder gets one of these alerts

- **`model_api_failure`** (high, no tenant): the model provider is marked
  down. Nothing is failed and nothing is sent except one probe every
  `PROVIDER_PROBE_MINUTES` (2). Customers see **Delayed** (DOC-023). Its
  `cause`:
  - `our_configuration`: the API key, the account, the credit or the model.
    Its `last_error` names it: `http_401` key, `http_402` billing or credit,
    `http_403` access, `http_404` the model retired or renamed,
    `http_400_own_spend_limit` our own spend limit in the Console,
    `http_429_spend_cap` the tier's monthly cap. Fix it in the Anthropic
    Console. A retired model needs a code change (the model IDs are still
    constants, review M10). The next probe after the fix recovers.
  - `5xx`, `overloaded`, `rate_limited`, `network`: the provider's side.
    Check status.anthropic.com. Nothing to do: it recovers on the first
    probe that succeeds.
  Documents still waiting after `PROVIDER_MAX_WAIT_HOURS` (6) fail with
  **DOC-024** (a `document_failed` alert per tenant per day). The customer is
  told to upload again or enter the order by hand.
- **`model_api_recovered`** (info): the first success after an outage. It
  shows how long it lasted, how many documents waited and how many reached
  the 6-hour limit. The backlog goes out at once, in turns. Acknowledge
  the `model_api_failure` alert yourself; recovery doesn't.
- **`dispatcher_stopped`** (high, at most one an hour): documents are
  waiting and no dispatch pass has run for `DISPATCHER_STALE_MIN`. Check
  that the worker and the `beat` process are running (`fly status --app
  docflow-worker-staging`), and the Upstash queue. Documents resume by
  themselves once both run.
- **`routing_model_failure`** (warning, once a UTC day): the cheaper routing
  model refused DocFlow (same causes as `our_configuration` above).
  Documents are still read, but without approved examples.
  `documents_without_examples` on the Console's alert counts them since the
  day's first failure (the email has the count when it was raised).
- **`document_stuck` with `lost_jobs_returned`**: documents were sent to the
  queue and never claimed (a queue or broker problem). They are back to
  waiting and go out again by themselves.
- **`worker_restarting`** (high, at most one an hour; Stage 3e): the worker
  started 3 or more times in 60 minutes. Its payload names the machine and
  the count; `/healthz` shows `worker_starts_last_hour`. Look at `fly logs
  --app docflow-worker-staging` for `run_workers: ... exited; stopping the
  other` and the line before it (a memory kill, a database or broker
  error). A deploy is one start; a deploy and one crash is two.
- **An alert with `exception_type` and `location` in its payload** (Stage
  4, PR 1a; D-203): a step of ours failed. It is one of
  `pipeline_step_failed` (buyer identification, matching or duplicate
  detection: the document is in review with VAL-016), `document_failed`
  with DOC-021 (saving or validation: the document failed), or
  `cost_record_lost` (a model call's run row could not be written; `step`
  says `started`, the row before the call, or `outcome`).
  - **`location` is `module:function:line` in our own code, and `release`
    the image it ran in.** The exception's message is never in the alert or
    the log. The log line of the same moment carries the same type and
    location with the document's id: search `fly logs` for the id in
    `first_document_id`.
  - **It is raised once for that state**, for that tenant: the same
    exception type at the same place in the same release raises nothing
    again, **acknowledged or not**. A new release, another tenant, another
    step or another exception type is a new alert.
  - **A dropped database connection** (`OperationalError`,
    `InterfaceError`) is raised once a UTC day instead, until the database
    retry exists.
  - **"Affected documents: N"** on the alert counts the documents that
    state has hit, read from the documents themselves.
  - **After you acknowledge it, look at "Acknowledged, still happening"**
    on the Console's home page. An alert is listed there while its state
    keeps hitting documents, with the count since you acknowledged it, and
    leaves the list after `ALERT_STILL_HAPPENING_DAYS` (7) with no new one.
    Nothing is e-mailed for a repeat.
- **Healthchecks.io: "docflow-worker-... is DOWN"** (e-mail, not a DocFlow
  alert; 9.4): no ping for period + grace. The worker is down, crash-looping,
  or can't reach the database. `fly status --app docflow-worker-staging`,
  then `fly logs`. It says UP again after the first good pass.
- **Healthchecks.io: the check failed** (`/fail`): a document has been
  dispatched and not claimed for `DISPATCH_UNCLAIMED_ALERT_MIN`. The
  dispatch process is fine; the documents worker isn't taking work (hung, or
  lost its broker connection). Restart the worker machine; the stuck sweep
  will have returned the document to waiting.

### 9.6 The constants (`packages/core/docflow_core/constants.py`)

| Constant | Value | What it does |
|---|---|---|
| `TENANT_IN_FLIGHT_CAP` | 2 | Most in flight for one tenant while another has something ready |
| `INTERACTIVE_BATCH_MAX` | 10 | An upload or release of up to this many goes in the interactive lane |
| `DISPATCH_INTERVAL_SECONDS` | 30 | Beat's backstop pass |
| `DISPATCHER_STALE_MIN` | 10 | Heartbeat age that means stopped |
| `PROVIDER_RETRY_MINUTES` | 1, 2, 4, 8, 15 | Provider wait backoff (then every 15) |
| `PROVIDER_MAX_WAIT_HOURS` | 6 | Then DOC-024 |
| `PROVIDER_DOWN_FAILURES` / `PROVIDER_DOWN_WINDOW_MIN` | 3 / 5 | Marked down (our own configuration: at the first) |
| `PROVIDER_PROBE_MINUTES` | 2 | One probe while down |
| `STORAGE_WAIT_RETRY_MINUTES` | 5 | Storage wait, no maximum |
| `PARSE_SERVICE_WAIT_RETRY_MINUTES` | 2 | Parse-service wait, no maximum |
| `WORKER_RESTART_ALERT_STARTS` / `WORKER_RESTART_WINDOW_MIN` | 3 / 60 | `worker_restarting` (3e); the window is also in 0036's functions, a test keeps them equal |
| `HEARTBEAT_PING_MIN` | 5 | At most one heartbeat ping this often (3e) |
| `HEARTBEAT_PING_TIMEOUT_SECONDS` | 5 | A ping's own time limit; a failed ping never fails a pass |
| `DISPATCH_UNCLAIMED_ALERT_MIN` | 20 | Dispatched and unclaimed this long sends `/fail` (Q2; founder Q9: 20, above every non-document task's hard limit and below `STUCK_PROCESSING_TIMEOUT_MIN` (30), both pinned by a test) |
| `CONSOLE_REFUSAL_ALERT_COUNT` / `CONSOLE_REFUSAL_WINDOW_MINUTES` | 5 / 15 | Refused Console requests (AUTH-006, AUTH-007) recorded per account per rolling window; the row that reaches the count raises `console_refusals_repeated` (high), and refusals past it are logged only (D-220) |
| `ALERT_STILL_HAPPENING_DAYS` | 7 | An acknowledged alert stays under "Acknowledged, still happening" until this many days pass with no new document hit by its state (D-203) |

### 9.7 The first worker deploy on staging: the procedure (founder, 2026-10-05; D-193)

9.2 lists the gates. This is the order they are met in, who does each step,
and where the founder's word is needed. It adds no gate and removes none.

**Who does what.**
- **The founder:** every secret (the parse token, the heartbeat URL, every
  `fly secrets set`), the Healthchecks.io check, the SQL Editor queries, and
  each **STOP**.
- **Claude:** the deploy commands, the test runs, the measurements and the
  record. Never sees or types a secret's value.

**What each app is given** (names only): 3c's approved list, the logins as
in 10.1, and what 3d and 3e added. A setting not in this table comes back
to the founder before it is set.

| App | Settings |
|---|---|
| parse | `PARSE_SERVICE_TOKEN`, nothing else |
| worker | `PARSE_SERVICE_TOKEN` (the same value), `DATABASE_URL` (`docflow_worker`'s: the `.env` line `WORKER_DATABASE_URL`), `REDIS_URL`, `ANTHROPIC_API_KEY`, the four `STORAGE_S3_*`, `HEARTBEAT_URL`, `FOUNDER_ALERT_EMAIL` |
| API | `DATABASE_URL` (`docflow_api`'s: the `.env` line `API_DATABASE_URL`), `ADMIN_DATABASE_URL`, `STRIPE_DATABASE_URL`, `REDIS_URL`, `SUPABASE_URL`, `CONSOLE_MFA_ENFORCED`, the four `STORAGE_S3_*` |

Left off, and why:
- **`SUPABASE_JWT_SECRET`** (founder: off unless the API fails without it).
  The API checks this project's ES256 session tokens against the project's
  published keys, using `SUPABASE_URL` alone (`apps/api/app/deps.py`;
  D-088, D-174). It is blank on the founder's machine too.
- **`EMAIL_PROVIDER_API_KEY`**, on both apps. No e-mail sender is built yet
  (D-103: its own stage, before the first pilot). Unset, every e-mail is
  written `held` and read in the Console's Outbox. Set, the rows would be
  written `queued` and nothing would send them.
- **The Stripe keys, the Postmark webhook pair, `SUPABASE_SERVICE_ROLE_KEY`,
  `DOCUMENT_URL_SIGNING_SECRET`.** So Fly staging has no inbound e-mail, no
  Stripe, no invites and no document viewer. No gate needs them.

**Rules for the whole deploy** (founder, 2026-10-05).
- **The real model reads every document**, the 500 + 1 included. A deployed
  worker has no stand-in for the model.
- **The Fly worker and beat are stopped at the end of every session**
  (`fly scale count worker=0 beat=0 --app docflow-worker-staging --yes`), and the
  founder pauses the Healthchecks.io check while they are. Two reasons: one
  stack against staging at a time (9.3), and a running dispatcher would
  send the documents the staging test suites create to the real model.
  The same command is the emergency stop. **`--yes` is required** (founder,
  2026-10-21; D-213 item 5): without it the command waits for a
  confirmation, and in a non-interactive session it hangs.
- **Spend.** The hard limit on model spend is the Anthropic account's
  prepaid balance with auto-reload off (set by the founder; $40.97 on
  2026-10-05). Claude stops the worker and reports when the deploy's total
  spend (model, Fly, Redis) reaches **$40**. The per-tenant breaker ($50 a
  day) is above both, so it is not the protection here. Fly and Upstash
  bill by use with no cap that was found (3c, Q10).

**The steps.**

1. **Founder:** create the Healthchecks.io check (9.4, "Setting it up",
   items 1 and 2). Keep the ping URL for step 3.
2. **Claude:** confirm nothing else runs against staging: no local worker,
   beat, API or test suite.
3. **Founder:** set the secrets, in a new PowerShell window with history
   off (8.1). `--stage` stores a value without starting anything.
   ```powershell
   Set-PSReadLineOption -HistorySaveStyle SaveNothing
   $fly = "$env:USERPROFILE\.fly\bin\fly.exe"
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # the new parse token
   & $fly redis status docflow-staging-redis                       # the private redis:// URL

   & $fly secrets set --app docflow-parse-staging --stage `
     PARSE_SERVICE_TOKEN=<the new token>

   & $fly secrets set --app docflow-worker-staging --stage `
     PARSE_SERVICE_TOKEN=<the same new token> `
     DATABASE_URL=<the value of WORKER_DATABASE_URL in .env> `
     REDIS_URL=<the private redis:// URL> `
     ANTHROPIC_API_KEY=<as in .env> `
     STORAGE_S3_ENDPOINT=<as in .env> `
     STORAGE_S3_REGION=<as in .env> `
     STORAGE_S3_ACCESS_KEY_ID=<as in .env> `
     STORAGE_S3_SECRET_ACCESS_KEY=<as in .env> `
     HEARTBEAT_URL=<the ping URL from step 1> `
     FOUNDER_ALERT_EMAIL=<as in .env>

   & $fly secrets set --app docflow-api-staging --stage `
     DATABASE_URL=<the value of API_DATABASE_URL in .env> `
     ADMIN_DATABASE_URL=<as in .env> `
     STRIPE_DATABASE_URL=<as in .env> `
     REDIS_URL=<the same Redis URL> `
     SUPABASE_URL=<as in .env> `
     CONSOLE_MFA_ENFORCED=<as in .env> `
     STORAGE_S3_ENDPOINT=<as in .env> `
     STORAGE_S3_REGION=<as in .env> `
     STORAGE_S3_ACCESS_KEY_ID=<as in .env> `
     STORAGE_S3_SECRET_ACCESS_KEY=<as in .env>
   ```
   **Three things must not happen:** the worker given `ADMIN_DATABASE_URL`,
   `STRIPE_DATABASE_URL` or `API_DATABASE_URL` (it refuses to start); the
   API given `PARSE_SERVICE_TOKEN` (it refuses to start); any app given a
   `postgres` or service-role credential.
4. **Claude:** `fly secrets list` on each app: names, never values. Report
   the three lists and the time the token was set on each app.

   **STOP 1.** The founder confirms the lists. No deploy command runs
   before this.
5. **Claude:** restart the parse service on its new token (the same image;
   no parse code is deployed). Every line of the canary is PASS (8.3) and
   `fly ips list` shows one private IPv6 address and nothing else.
6. **Claude:** read `worker_starts_last_hour` (9.2, "Straight after the
   worker starts", item 1).
7. **Claude:** after section 11's dependency audit passes, deploy the
   worker (`fly deploy`, rolling, one `worker`
   machine and one `beat` machine; 9.3). **Founder:** the `worker_starts`
   query in 9.2. **Claude:** `/healthz` moved by one; the launcher's log
   shows both Celery workers up and the login check passed as
   `docflow_worker`. **Founder:** the first ping shows in Healthchecks.io
   within about 5 minutes. No row, two rows, or `/healthz` not moving by
   one: stop and report (9.2).

   The deploy command carries `--ha=false` (the command in
   `apps/worker/fly.toml`): without it Fly adds a stopped spare beside
   each machine (founder, 2026-10-06; D-194). The login line reads
   `run_workers: login check passed as docflow_worker`. The launcher
   prints it from D-194 on; on 2026-10-06 the first deploy ran the code
   before it, where a pass printed nothing.
8. **Claude:** deploy the API (9.1: after the worker), also with
   `--ha=false`. `fly ips list` shows no public address; `/healthz` over
   `fly proxy` reads `"stale":false`. The API gets no Flycast address
   either (founder, 2026-10-06; D-194), so `fly ips list` is empty, and
   the machine, which stops itself when idle, is started by hand before
   `fly proxy` is used.

   **STOP 2.** Claude reports steps 5 to 8. The founder says whether to go
   on to the gates.
9. **G** (3c's test table). G1: three uploads through the Fly API, the
   queue, the Fly worker and the parse service to `needs_review`: the
   golden fixture, a `.doc` and a scanned image. Record each document's
   time and cost. G2: a catalog import by the same path. G3: Redis commands
   in a timed idle hour and a timed busy one (3c, Q10: more than about
   6,850 in the idle hour means the fixed $10 plan. *Corrected 2026-10-08,
   D-196: this said "about 69,400", an arithmetic error ten times too
   high; $10 a month at $0.20 per 100,000 commands over 730 hours is about
   6,850 an hour.*).
10. **A4 against the real API and worker.** The parse self-test with a
    targets file: the API's private address on port 8000, with the API
    machine started first (a connection to its private address does not
    wake it), and the worker's private address on **port 22**. The worker
    listens on nothing of its own; port 22 is Fly's SSH service, which
    every Fly machine runs (3c's evidence shows it on the parse machine).
    Each control must reach its target from outside the sandbox, and the
    same connection from inside a job must be blocked. NOT-RUN fails the
    gate. **If the control cannot reach the worker's port 22** (founder):
    the worker is recorded as "not tested: it accepts no connections",
    never as passed, and the API half must pass in full. A pass on port 22
    is recorded as "Fly's SSH port, not a DocFlow port".
11. **Memory** (9.2, "Measuring the worker machine's memory").

    **STOP 3.** The founder gets the numbers and chooses: a lower
    per-process limit, or a 2 GB worker machine. Neither changes before.
12. **The heartbeat drills** (9.4's pass marks), and the restart alert:
    1. stop the worker: the Healthchecks.io e-mail within 12 minutes;
    2. start it: the recovery e-mail after the first ping;
    3. kill the documents worker three times within the hour:
       `worker_restarting` is raised. **Pass mark (founder, 2026-10-05):**
       the alert row and its held e-mail, read by the founder with the
       query below, and recorded as "raised and held, not delivered". The
       3e design said "the `worker_restarting` email"; DocFlow delivers no
       e-mail until the sending stage is built, and delivery is proven
       then.
       ```sql
       select a.type, a.severity, a.created_at, o.status as email_status
       from founder_alerts a
       left join email_outbox o on o.id = a.email_outbox_id
       where a.type = 'worker_restarting'
       order by a.created_at desc limit 3;
       ```
    4. stop the documents worker taking work with the dispatch process
       running: the `/fail` e-mail after `DISPATCH_UNCLAIMED_ALERT_MIN`
       plus one pass.
13. **Claude:** the 500 + 1 budget again, with G1's measured cost and time
    per document and G3's Redis count.

    **STOP 4.** The founder signs off the number. The run does not start
    before this.
14. **The 500 + 1 run.** One Scale-tier test tenant sends 500 distinct
    short text orders (so no allowance alert is raised); a second tenant
    then sends 1. The script that makes and uploads them is shown to the
    founder before it runs. To be seen: the single order is read ahead of
    the backfill's queue. The slow case and the largest unclaimed age: 9.4.
    **Stop the worker and report** on any `document_failed` or
    `document_stuck` alert not explained at once, the tenant's cost breaker
    tripping, or spend reaching $40.
15. **Claude:** stop the machines; the founder pauses the check. Report the
    Redis command count and what the $10 rule says.
16. **Claude:** the record, by PR: CHECKPOINTS.md (the "first worker
    deploy" gap), BUILD-STATUS, and this section if anything differed.

**As run, 2026-10-07 and 2026-10-08 (D-196). What differed from the steps
above, and what to do the same way next time:**
- **Before any session's first upload, in this order:** nothing local
  against staging; start the API machine (`fly machine start`), then open
  `fly proxy` (it refuses while the machine is stopped); read `/healthz`
  *before* the deploy; deploy the worker with a live `fly logs` capture
  running, so the `login check passed as docflow_worker` line is kept (Fly
  returns only its last 100 lines afterwards); read `/healthz` after; then
  the founder un-pauses the check and runs the `worker_starts` query.
- **The staging API stops itself about 6 minutes after a start,** since
  `fly proxy` traffic does not count as activity. For a long run:
  `fly machine update <id> --autostop=off --yes`, and
  `--autostop=stop --yes` straight after, each read back from
  `fly machine list --json`.
- **Step 9, G3, and step 15: the rule, restated by the founder.** For an
  always-on worker, switch to a fixed plan when idle commands an hour x 730
  x $0.000002 exceeds the cheapest fixed plan that fits the data (Fly's
  Upstash: 250 MB at $10 a month). The founder reads Upstash's count
  **before and after** every timed period, with the time of each reading;
  on 2026-10-08 no reading was taken before the run, and its share could
  only be estimated.
- **Step 14.** The stop was $15 of total recorded spend, not $40 (STOP 4).
  The worker was resized for the run (`fly scale memory 2048
  --process-group worker`, a second recorded start) and the API's
  auto-stop was off for the session. The backfill's tenant got the fixture
  catalog first, imported by the founder in the Console: catalog imports
  exist only on the Console's routes, so a script cannot do it. The slow
  case was the founder pressing "recompute" at about a quarter and a half
  of the run; the sweeps come by themselves every five minutes.
- **A long run is started as a detached process** (`Start-Process` with
  its output to files), never as a terminal call with a time limit. The
  script kept in `docs/spikes/first-worker-deploy-staging/2026-10-08/` has
  a `--resume` mode for a watching process that ends early.
- **`fly redis status` is never run:** it prints the URL with its
  password. The founder reads Upstash's pages instead.

**Rules and notes added after the drills of 2026-10-08 and 2026-10-09
(D-197):**
- **A deploy that has started is never stopped** (founder, 2026-10-08). If
  the script around it fails, leave the deploy to finish and report the
  result. A release that looks stuck or half-applied is reported, not
  repaired. On 2026-10-08 a deploy stopped 21 s in had already updated
  both machines and never marked its release complete: that release (v7 on
  the staging worker) still reads `running`, which is cosmetic, and the
  next deploy went past it without a warning.
- **A paused Healthchecks.io check un-pauses at the worker's next ping,**
  so a pause only holds while the worker is down. Pause it after the
  worker has stopped, not before, and resume it when the worker is
  deployed again.
- **`fly` or `flyctl`:** on 2026-10-09 flyctl updated itself (to v0.4.115)
  and the `fly` command name disappeared from `C:\Users\NK\.fly\bin` on
  the founder's machine. `flyctl` is the same program and takes the same
  commands.

**Notes added after the staging measurement of 2026-10-13 (D-202 items 13
and 14):**
- **An `fly ssh` session on the worker's machine carries the worker's
  database setting.** Checked by printing yes or no, never the value. The
  session is root; a read-only script put in `/tmp` was run as the
  worker's own user (`setpriv --reuid=10100 --regid=10100 --clear-groups`)
  with the worker's Python (`/opt/worker/venv/bin/python`), and removed
  afterwards. The image is not changed and no worker start is recorded.
- **The Fly API cannot create a tenant.** The Console's create-tenant
  route always creates the Stripe customer, and `docflow-api-staging` has
  no Stripe key (above), so the screen would show CON-006. A tenant is
  created through a local API against staging, by the founder, after
  these read-only checks, each reported first:
  - the local `.env`'s Stripe key starts with `sk_test_` (the prefix only
    is read);
  - the local API's queue is the local Redis, so anything it enqueues
    reaches no worker: the tenant is created and nothing else is done;
  - starting the API runs no migration, sweep or scheduled work;
  - the commit the local API runs from is recorded as the code that
    created the tenant.

  Claude's `fly proxy` holds port 8000, so it is stopped first and started
  again when the founder has stopped the local API; `/healthz` is read
  before anything is uploaded.
- **The parse machine stops itself about six minutes after a start,** as
  the API does, unless its auto-stop is turned off for the session
  (`fly machine update <id> --autostop=off --yes --app
  docflow-parse-staging`, and `--autostop=stop --yes` at the wind-down,
  each read back from `fly machine list --json`). The update leaves a
  stopped machine stopped: start it afterwards. A parse machine start is
  not a worker start and writes no `worker_starts` row. Its start-up
  check counts one out-of-memory kill (`canary:memory-cap-kills PASS`), so
  the machine's count reads 1 when nothing has gone wrong.
- **`fly proxy` bypasses Fly's public edge.** It is a tunnel to the
  machine. A request timed through it says nothing about the edge's
  timeouts: the catalog commit of 2026-10-13 ran 4 min 14 s through it,
  and whether the edge would have cut it is not established (D-202 item
  14 has the sources).
- **A Stripe test customer created through a local API lives in the
  sandbox of the local `.env`'s `sk_test_` key.** A sandbox is an account
  of its own, and the Stripe dashboard opens whichever one the browser
  last used, so the customer may not be in the default view (the bench
  tenant's customer, 2026-10-13; D-202 item 18). To find one, don't search
  the sandboxes by hand: read the key's account id first, read-only
  (`GET /v1/account`, its `id`, `acct_...`), and open that sandbox.
  `GET /v1/customers/<id>` with the same key then shows the customer, or
  `deleted: true` once it is deleted. The key's prefix is checked first
  (`sk_test_`), and the key is never printed.

**If something goes wrong.** Any step fails: stop the worker and report.
Documents already received stay as they are; nothing is deleted. Going back
entirely is stopping the three apps: staging is then as before, the local
stack against the same database, and no migration is part of this deploy.
If the parse service and the worker disagree on the token, documents wait
and `parse_service_unavailable` is raised (8.4); set the same value on both
again.

### 9.8 What an idle worker costs on Redis (founder, 2026-10-08; D-196)

Upstash bills by the command, and an idle worker sends commands all day.
Measured at the first worker deploy (2026-10-07, staging, one worker
machine and beat, nothing to read): **13,600 to 13,700 commands an hour**.
A busy hour was within noise of that, and the 500 + 1 run added roughly 16
commands a document. So the idle rate is the bill.

Three settings follow from that measurement:

| Setting | Where | Value | Why |
|---|---|---|---|
| `BROKER_POLL_SECONDS` | `apps/worker/app/celery_app.py` (kombu's `polling_interval`, which sets its BRPOP timeout) | 10 (kombu's default is 1) | Each of the two worker processes keeps one blocking read open on its queues and repeats it when it times out: at 1 s, about 7,200 commands an hour between them (Upstash showed about 6,700), half of idle. Once a worker is running, the read returns the moment a job arrives, so pickup is no slower (measured: 4 ms from the send to the worker). **Not so for about 10 s after a worker starts** (below; corrected 2026-10-09, D-197: this row said "pickup is no slower" without the exception). |
| No result backend | the three `Celery(...)` objects: the worker, `apps/api/app/celery_client.py`, `docflow_core/email_intake.py`; and `task_ignore_result` on the worker | off | Every task's result was stored in Redis for a day and nothing ever read one. About 156 scheduled tasks an hour: the roughly 150 new keys an hour seen on Upstash. |
| `kill_timeout` | `apps/worker/fly.toml` | 30 s (Fly's default is 5) | A stopping worker waits for the read it has open (kombu's `Channel.close`), so shutdown takes up to `BROKER_POLL_SECONDS` longer. An idle worker's shutdown took about 4 s at the 1 s read (2026-10-08, Fly's log). `tests/test_celery_app.py` fails if `kill_timeout` drops below the poll plus 15 s. |

**Measured on Fly staging, 2026-10-08 and 2026-10-09 (D-197):**
- **The idle hour: 6,205 commands** (2026-10-09, 14:14 to 15:14; Upstash
  read by the founder before and after: 221,696 and 227,901). That is
  about $9.06 a month for an always-on worker, under the $10 line.
  **Production stays on Pay as You Go** (founder). Re-check against the
  first production bill and switch to the Fixed 250 MB plan if it goes
  over $10. Staging stays on Pay as You Go.
- **Before an idle-hour reading, read the API and parse machine states
  first** (founder, 2026-10-09): `flyctl machine list` for
  `docflow-api-staging` and `docflow-parse-staging` must both read
  `stopped`, at the first Upstash reading and again at the second. A
  Redis reading taken while the API is still up is not an idle reading.
  For the hour measured on 2026-10-09 both were stopped (D-197, item 6).
- **Before production's Redis is used** (founder): confirm its endpoint is
  reachable only over Fly's private network. TLS is disabled on the
  Upstash endpoint.
- **An idle worker's shutdown at the 10 s read: 10 s, by itself, exit 0**
  (scaled to 0 on 2026-10-08). Fly sent `SIGTERM` 5 s after the `SIGINT`
  and the worker survived it. That `SIGTERM` belongs to `fly scale count`,
  which destroys the machine with `Kill: true` (Claude's reading of
  flyctl's source, not proven); a deploy sent none (below). **A scale-down
  is not a stand-in for a deploy.**
- **A deploy during a read** (2026-10-09): `SIGINT`, the read finished 8 s
  later, the worker exited by itself 12 s after the signal, no `SIGTERM`.
  **Not tested on a deploy: a read longer than `kill_timeout`.** To land
  the signal inside a read, build and push the image first
  (`fly deploy --build-only --push`), upload, and start
  `fly deploy --image <that image>` once the document is `processing`: a
  plain `fly deploy` spends about 20 s building before it signals, longer
  than a short read.
- **About 10 s after every worker start, each Celery worker receives
  nothing** (four starts of four at the 10 s read; about 1 s at the old
  1 s read): a job already in the queue was received 8.9 s after `ready`.
  Read, not proven, as Celery's start-up neighbour search (mingle) leaving
  a blocking read open for the poll time. **Since D-212 both workers start
  without mingle and without gossip** (`app.run_workers`; heartbeat and
  remote control stay on). The Fly proof, a waiting message received within
  2 s of `ready` at a start, gates that PR's merge; its result is in the
  next PR's records.

**What was owed, as written before those measurements:**
1. **One idle hour on staging with these settings.** The founder reads
   Upstash's command count before and after, with the time of each
   reading, and the "Top commands" view. Expected, as an estimate only:
   about 7,000 an hour (the measured rate less about 6,500 BRPOP and a few
   hundred result commands). Against the rule in 9.7: the $10 fixed plan
   breaks even at about 6,850 an hour.
2. **The worker's shutdown time at the 10 s read,** at the same deploy:
   scale the worker to 0 and read, in `fly logs`, the time from `Sending
   signal SIGINT to main child process` to `Main child exited normally with
   code: 0`. It passes if the worker exits by itself, with code 0, inside
   `kill_timeout`. If it is cut off instead, that goes to the founder
   before anything is changed.

**On this Windows machine** the local worker runs without Celery's event
loop (the `solo` pool), and there kombu also sleeps after an empty read:
up to about 2 s before the next look at the queue. Local runs can feel
that; the deployed prefork worker is not affected.

**Not changed, and a question for the founder if the idle hour is still
above the break-even:** by default Celery's workers also exchange
heartbeat and gossip messages through the broker, and can run without
them (`--without-gossip --without-mingle --without-heartbeat`); nothing in
DocFlow reads them. How much of the other half of the idle commands they
are is not known: Upstash's "Top commands" view after the idle hour will
show. Not part of this change.

## 10. Database logins (Stage 3e, F-1)

Since migration `0036` DocFlow connects as four logins, never `postgres` and
no longer `docflow_app` (D-159, D-185). Each is NOBYPASSRLS, has no CREATE,
and is a member of `docflow_tables` (the table grants) and nothing else.
Every policy that opens rows on an `app.*` flag applies TO exactly one of
them, so a flag set on any other login opens nothing.

### 10.1 Which app holds which

| Login | Used for | Held by (setting) |
|---|---|---|
| `docflow_api` | tenant requests, the sign-in and intake-token lookups, the refusal alert, `/healthz` | the API (`DATABASE_URL`) |
| `docflow_admin` | the Console (`admin_data_access`) and every script | the API (`ADMIN_DATABASE_URL`); the founder's machine for scripts |
| `docflow_stripe` | the Stripe webhook only | the API (`STRIPE_DATABASE_URL`) |
| `docflow_worker` | every job and sweep, the dispatcher, the restart record | the worker (`DATABASE_URL`) and nothing else |

- **The worker refuses to start** holding `ADMIN_DATABASE_URL`,
  `STRIPE_DATABASE_URL` or `API_DATABASE_URL` (`app.run_workers`).
- **Each process refuses to start** if a URL it holds connects as another
  login (the API checks its three; the worker its own). The message names
  the setting and the role found, never the URL. An unreachable database
  is logged, not fatal.
- **The founder's `.env`** holds all of them (it runs the API, the worker
  and the scripts): `DATABASE_URL` and `API_DATABASE_URL` = `docflow_api`,
  `WORKER_DATABASE_URL`, `ADMIN_DATABASE_URL`, `STRIPE_DATABASE_URL`
  (`.env.example`). Scripts run as `docflow_admin` (founder, Q8).
- **The pooler:** each login is its own pool. Four logins plus Storage at
  pool size 5 is at most 25 connections, against about 44 usable on Nano
  (BUILD-STATUS "3e -- F-1"). Production's pool size is decided in Phase 6.

### 10.2 The cutover on docflow-staging

The founder's conditions (2026-10-02, Q5) are steps 0, 1 and 6.

0. **Not before Claude has reported the `backup_0035` check** (on or after
   2026-10-05 03:18 UTC; section 1.3) and the founder has confirmed, so 3e
   can't land inside 3d's 3-day clean-run window.
1. **The snapshot (the backup).** Claude runs, read-only:
   `python scripts/ci/policy_snapshot.py "<staging DATABASE_URL>" > policy_snapshot_before_0036.json`
   and compares it with `supabase/reverse/0036_pre_snapshot.json` (taken
   2026-10-02). They must be identical: a difference means staging changed
   since, and the cutover stops until it is explained. The file is kept
   with the evidence.
2. **The founder applies `0036`** in the SQL Editor. It changes no rows.
3. **The founder turns on the four logins**, each with its own generated
   password (`python -c "import secrets; print(secrets.token_urlsafe(32))"`,
   typed, never pasted into a chat), in the SQL Editor:
   ```sql
   ALTER ROLE docflow_api    WITH LOGIN PASSWORD '...';
   ALTER ROLE docflow_worker WITH LOGIN PASSWORD '...';
   ALTER ROLE docflow_admin  WITH LOGIN PASSWORD '...';
   ALTER ROLE docflow_stripe WITH LOGIN PASSWORD '...';
   ```
4. **The founder builds each URL**: the pooled ("transaction mode") string
   from Project Settings -> Database, with the user `docflow_api.<project
   ref>` (and so on) and its password, and puts them in `.env` (10.1).
5. **Claude verifies:** a second snapshot equals CI's forward state; each
   login connects as itself (`db.verify_logins`); then the staging suites
   (core, worker, API; RUNBOOK 1.4), reported with their own summary lines.
   **The forward state is a committed file,
   `supabase/reverse/0036_post_snapshot.json`** (founder, 2026-10-05; found
   at this step on staging: CI used to hold that state in memory and throw
   it away, so there was nothing to compare with). CI's round trip fails
   unless its own forward state equals that file, and prints both states'
   SHA-256s on every run. The comparison is by SHA-256, with the same code
   CI uses:
   ```
   python scripts/ci/policy_snapshot.py "<URL>" > policy_snapshot_after_0036.json
   python scripts/ci/policy_snapshot.py --sha256 policy_snapshot_after_0036.json supabase/reverse/0036_post_snapshot.json
   ```
   The two hashes must be equal, and equal to the "post-0036 state" hash on
   `main`'s latest CI run. If they differ, `--diff <taken> <committed>`
   names each policy or function that differs, and the cutover stops. The
   file taken is kept with the evidence.
   This is the first time `main`'s suites run on staging since 3e merged:
   until `0036` is there, the backup checks run them at `f191e27` (1.3).
6. **Before `docflow_app` is switched off, every place its URL lives is
   listed** and switched or removed: the local `.env` (and any other `.env*`
   in the repository, searched by name only), Fly secrets on every app
   (`fly secrets list --app <app>`: names and digests only), GitHub
   repository secrets (Settings -> Secrets and variables -> Actions; CI uses
   none today), and anything else the search finds. Then the founder:
   `ALTER ROLE docflow_app NOLOGIN;`
   **Then it is verified, and the old URL goes** (founder, 2026-10-05):
   Claude connects with the old `docflow_app` URL and the database must
   refuse it. Only after that refusal is the old URL removed from the root
   `.env` (on staging it was kept there as a comment from step 4 until this
   point). From then on the old password is recorded nowhere.
   **`NOLOGIN` does not close sessions that are already open, and the
   pooler reuses them** (found on staging, 2026-10-05: the old URL kept
   connecting through one session the pooler still held, opened by a
   check made a minute before `NOLOGIN` ran). So:
   - never try the old URL between step 5 and `NOLOGIN`;
   - straight after `NOLOGIN`, the founder ends the login's open sessions,
     in the SQL Editor:
     ```sql
     select pg_terminate_backend(pid) from pg_stat_activity where usename = 'docflow_app';
     ```
   - then the check: three attempts, each refused. Through the pooler the
     refusal reads `FATAL:  (EAUTHQUERY) user not found in the database`.
7. **`docflow_app` is dropped only after the first worker deploy has
   processed real documents on staging** (founder, 2026-10-05; D-190: the
   same trigger as the backups, 1.3 and D-186. It replaces "after 3e has
   run cleanly on staging for 3 days", which proves nothing while no
   worker is deployed), and after Claude reports the check:
   ```sql
   begin;

   -- table and sequence grants (38 tables, 2 sequences in public)
   revoke all on all tables in schema public from docflow_app;
   revoke all on all sequences in schema public from docflow_app;

   -- function grants (none exist today; 0036 removed them)
   revoke all on all functions in schema public from docflow_app;

   -- default privileges (both entries were set by postgres)
   alter default privileges for role postgres in schema public revoke all on tables from docflow_app;
   alter default privileges for role postgres in schema public revoke all on sequences from docflow_app;

   -- schema usage
   revoke usage on schema public from docflow_app;

   drop role docflow_app;

   commit;
   ```
   The date is recorded here and in BUILD-STATUS.

   **Done on `docflow-staging`, 2026-10-09**, by the founder in the SQL
   Editor, between 18:32:25 and 18:36:42 UTC, with the SQL above, which is
   what ran. It differs from what this step held until then (five
   statements and the `DROP ROLE`) in three ways the founder approved
   beforehand: the function revoke (it changed nothing: the role held no
   function grant); `for role postgres` on the two default-privilege lines
   (without it the statement acts only on entries set by whoever runs it);
   and `begin` / `commit`, so a refused `drop role` would have undone the
   revokes. Before it, Claude read (as `docflow_admin`, read-only) that
   nothing else named the role anywhere on the server: the dependency
   record `drop role` itself consults, and every access list, owner and
   role reference in every schema, database privileges included. It was
   named by 38 tables and 2 sequences in `public`, by `public` itself and
   by `postgres`'s two default-privilege entries, and by nothing else.
   After it: the role is gone and nothing names it; `public`'s columns,
   policies, function grants and the other grantees' table, schema and
   default privileges are unchanged; the four logins and `docflow_tables`
   are unchanged, and each login connects as itself and reads a table; the
   Fly worker and beat kept running. `postgres`'s membership of the role and
   the role's 5-minute idle setting went with it.

**Done on `docflow-staging`, 2026-10-05 (UTC):** step 1 at 15:52; steps 2
to 4 by the founder at about 16:50; step 5's snapshot at 16:52 (SHA-256
`81bd33599151...`) and again at 21:27 after the last suite run; step 6's
`NOLOGIN` at about 21:32, the refusal verified at 21:35, the old URL
removed from the root `.env` then. Step 7 was done on 2026-10-09 (above);
until then it waited for the first worker deploy to have processed real
documents. The record of steps 1 to 6 is in CHECKPOINTS.md (Stage 3, "The
cutover").

**Stop condition before the first worker deploy** (founder, 2026-10-02).
The first worker deploy (9.2) is scheduled straight after steps 1-6 are
verified, without waiting for step 7. **It goes ahead only if verification
turned up nothing at all.** Anything unexpected in steps 1-6, even if it
looks cosmetic, stops it until the cause is explained and the founder has
said go. **The rule is "anything unexpected", and the list below is
examples, not the full set:** it includes, but is not limited to, the
following. Something not on it is never fine for that reason alone.
- a snapshot difference;
- a login that doesn't connect as itself;
- any failed, skipped or errored test in the step 5 suites, or a count
  that differs from CI's;
- a warning that wasn't in CI's run;
- a `docflow_app` URL found in step 6 that wasn't on the list;
- a new founder alert on staging since `0036`;
- **anything else that wasn't expected**: a step slower than usual, a log
  line not seen before, a number that differs from what this RUNBOOK or
  CI led us to expect.

Otherwise a cutover problem and a deploy problem land together, and nobody
can tell which caused what. This is decided here, not left to judgment on
the day.

**Going back.** `supabase/reverse/0036_reverse.sql` restores the policies
and grants exactly as before (CI proves it on every push, against the
snapshot). It loses only the rows of `worker_starts`.

**The order matters, and the apps are down from step 1 to step 5**
(founder, 2026-10-05). After `0036`, `docflow_app` holds no policy and no
function grant, so an app switched to it before the reverse fails every
query; and the reverse drops the four logins, so run first it pulls them
from under running apps. **The old `docflow_app` password is gone after
the cutover** (step 6 removes its URL once the refusal is verified), so
going back also needs a new one.

1. **Stop the API and the worker.** On Fly, scale each app to zero
   (`fly scale count 0 --app <app> --yes`); on this machine, stop them.
2. **The founder recreates `docflow_app`, then gives it a new password.**
   The role was dropped on staging on 2026-10-09 (step 7), so it is made
   again first, with its table grants as SETUP.md Step 1.6 made them and
   the idle cap `0028` set on it, in the SQL Editor:
   ```sql
   CREATE ROLE docflow_app NOLOGIN NOBYPASSRLS;
   GRANT USAGE ON SCHEMA public TO docflow_app;
   GRANT ALL ON ALL TABLES IN SCHEMA public TO docflow_app;
   GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO docflow_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO docflow_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO docflow_app;
   ALTER ROLE docflow_app SET idle_in_transaction_session_timeout = '5min';
   ```
   Without the role, step 3's script still runs, but it grants the 13
   functions to nobody and drops the four logins, so no login is left for
   the apps and the snapshot can't match (the script's header says the
   same). This recreate has not been run on staging: CI's round trip makes
   the role without the table grants, since its snapshots hold policies and
   function grants only. Then the password, generated as in step 3 (typed,
   never pasted into a chat):
   ```sql
   ALTER ROLE docflow_app WITH LOGIN PASSWORD '...';
   ```
3. **The founder runs `supabase/reverse/0036_reverse.sql`** in the SQL
   Editor, whole, once. Then the snapshot must equal
   `supabase/reverse/0036_pre_snapshot.json`, by SHA-256, as in step 5:
   ```
   python scripts/ci/policy_snapshot.py "<the new docflow_app URL>" > policy_snapshot_after_reverse.json
   python scripts/ci/policy_snapshot.py --sha256 policy_snapshot_after_reverse.json supabase/reverse/0036_pre_snapshot.json
   ```
   The four logins no longer exist, so the snapshot is taken as
   `docflow_app`: its new URL (the pooled string, as in step 4) goes into
   the root `.env` first, on a line of its own. Nothing uses it yet; the
   apps are stopped. If the hashes differ, stop: nothing is started.
4. **The new `docflow_app` URL goes everywhere the four logins' URLs
   were:** `DATABASE_URL` in the root `.env`, with the four logins' lines
   removed, and the Fly secrets on each app (`fly secrets set`, and
   `fly secrets unset` for the others).
5. **Start the apps and confirm each connects as `docflow_app`**
   (`select current_user`, as `db.connected_login` reads it). They must run
   the code from before 3e: `main`'s API and worker refuse to start on any
   login but their own (`db.verify_logins`), and call functions the reverse
   has dropped. The last `main` before 3e is `f191e27` (1.3).

**Production (Phase 6)** is created straight into this state: `0036` with
the rest, then 10.2 steps 3-5. It never has `docflow_app`. Step 5's
snapshot is compared with the same `0036_post_snapshot.json`, or with the
post-state file of whichever later migration last changed a policy or a
grant (section 1, "A migration that changes policies or grants").

### 10.3 Rotating one login's password

1. Generate a new password (as in 10.2 step 3).
2. `ALTER ROLE docflow_<login> WITH PASSWORD '...';` in the SQL Editor.
   Existing connections keep working; new ones need the new password.
3. At once, set the new URL where that login lives (10.1): `fly secrets set
   ... --app <app>` (this restarts the app) and the founder's `.env`.
4. Check: `/healthz` answers, and the app's log has no
   `login_startup_check` or `refused to start` line.

Rotate one at a time. A leaked URL is rotated the same day; the login's
policies limit what it could reach, but a worker or Stripe URL in the wrong
hands still writes as that service.

---

## 11. Before any deploy: the dependency audit (founder, 2026-10-23; D-219)

Every deploy of an app, to staging or production, checks three things
first. A failed check means no deploy, until it is fixed or the founder
decides otherwise.

1. **The deploy commit's CI run is green.** Its dependency audit blocks
   only when that app's dependency files changed in the commit's own
   changes, so a green run doesn't prove the dependencies are clean today.
2. **The latest daily audit on `main` is green, or its failure is
   explained.** It is "Dependency audit (daily)" in the Actions tab
   (`.github/workflows/dependency-audit.yml`, every day at 06:30 UTC).
3. **The app's dependencies are clean today.** From the repository root,
   at the commit being deployed:
   ```
   bash scripts/ci/predeploy_audit.sh <api|worker|parse|web>
   ```
   It audits the app's lock file as it is now, always blocking, and exits
   0 only when nothing known is found. A Python app needs pip-audit 2.10.1
   on PATH (the api venv has it); web needs npm. It reaches only PyPI's
   vulnerability API or the npm registry, and installs nothing. Its last
   line is a `::notice` with the count; an `::error` line names the
   package, the advisory and the fixed version.

**What blocks:**
- **Python:** any known vulnerability, of any severity, in what the app
  ships. That is the API's and the worker's whole locks (dev tools
  included, until they move to `requirements-dev.txt` files; BUILD-STATUS
  item 7) and parse's lock. There is no exceptions mechanism.
- **Web:** a high or critical advisory in a runtime dependency.
- **Dev-only findings** (parse's `requirements-dev.txt`, web's dev
  dependencies) warn and never block.

A production advisory found by the daily audit gets a fix PR the next
working day (8.5).

### 11.1 Moving one pin in a Python lock file (founder, 2026-10-27; D-223)

The Python lock files (`apps/api/requirements.lock.txt`,
`apps/worker/requirements.lock.txt`, `apps/parse/requirements.lock.txt`) are
plain `pip freeze` lists: one `name==version` per line, no hashes. There is
no lock tool. A pin is never typed in; pip writes it, and the diff proves
nothing else moved:

1. **A throwaway environment, outside the repository, on a short path**
   (Windows' path limit breaks lxml's install under a long one), with the
   Python CI uses (3.13):
   ```
   py -3.13 -m venv C:\Users\NK\AppData\Local\Temp\relock
   ```
2. **Today's pins, exactly:** `<venv>\Scripts\python.exe -m pip install
   --no-deps -r apps/<app>/requirements.lock.txt`, then `pip freeze` into a
   file. `--no-deps` stops pip resolving anything: each pin is installed as
   written.
3. **The one move:** `pip install --no-deps <package>==<fixed version>`, then
   `pip check`, which must print `No broken requirements found.` If the new
   version needs a newer dependency, `pip check` says so; move that pin the
   same way and name both in the PR.
4. **Write the lock from the second `pip freeze`:** keep every lock line
   whose package and version the freeze reports unchanged, and take the
   freeze's line only where the version moved. (pip spells some names
   differently from the lock, `RapidFuzz` for `rapidfuzz`; keeping the
   unchanged lines keeps the diff to the real move.) A package added or
   missing is a resolver change, not a pin move: stop. Keep the file's BOM
   and line endings.
5. **The proof:** `git diff` shows exactly the moved pin;
   `bash scripts/ci/predeploy_audit.sh <app>` exits 0; and the readers of
   the lock (D-216 item 6) are named in the PR.
6. **Staging suites:** a lock-only PR runs them only when the changed
   package is on a path the suites exercise (founder, 2026-10-27; D-223).
   The suites run from the local API environment, which does not follow
   the lock, so they would not test the new version; CI's `api` and
   `web-live` jobs install the exact lock.

Delete the throwaway environment afterwards.

### 11.2 A staging step through `fly proxy` (founder, 2026-10-27; D-220 item 11)

`fly proxy` traffic doesn't keep the machine up. `docflow-api-staging`
has auto-stop on (`auto_stop_machines = "stop"`, `min_machines_running =
0`) and no address, so nothing wakes a stopped machine. A deploy leaves it
stopped too.

1. **Start the machine first:** `fly machine start <id> --app <app>`.
   Then open `fly proxy 8000 --app <app>`, and `GET /healthz` through it.
2. **Expect it to stop after a few idle minutes.** On 2026-10-27 it
   stopped 6 to 8 minutes after each of three starts.
3. **A step that waits** (a step-up code older than 330 s, for example):
   right before the request, check the machine with `fly machine list`
   (read-only), and start it again if it has stopped. A start is a Fly
   action, so it needs the founder's Grant (D-213 item 6).

`fly.toml` stays as it is (founder).

## 12. The staging sweep: old test tenants (founder, 2026-10-27; D-224)

A stopped test run leaves its tenants behind on `docflow-staging`.
`scripts/sweep_test_tenants.py` lists the "Acme Test" tenants older than a
day, and removes one at a time. Run it from the repository root with
`apps/api/.venv`'s Python. The process environment must carry the two
flags; the root `.env` gives the database and Storage settings:

```
DOCFLOW_STAGING_RUN=1 DOCFLOW_ENV=staging apps/api/.venv/Scripts/python.exe scripts/sweep_test_tenants.py list
```

1. **`list`** is read-only: a read-only transaction and Storage list calls.
   It needs no Grant, and the report names it among the reads.
   - **What it shows:** each tenant's rows by table, its Storage objects,
     its users' e-mail addresses, and its marks.
   - **Marked tenants are never removed:** LIVE, DELETED BY THE PRODUCT,
     PENDING DELETION, NAMED IN RECORDS and SEED NAME.
2. **`delete <tenant id> --name "<exact name>"`** needs a Grant from the
   founder that names that tenant's id and name. One tenant per run.
   - **The backup folder:** `--backup-folder` names a new or empty folder
     outside the repository. The default is
     `~/docflow-sweep-backups/<id>-<UTC time>`.
   - **What it prints:** what will be lost, then each backup file with its
     SHA-256, then the Supabase Auth accounts left behind, which are out of
     scope.
3. **If it refuses:**
   - **Exit 2:** not staging.
   - **Exit 3:** a test suite holds the run lock. Wait for the suite to
     finish.
   - **Exit 4:** the tenant may not be swept. The message says why.

   In every case, nothing was removed.
4. **While a sweep runs,** an API or worker suite that starts refuses with
   exit 3, naming `docflow-sweep`. The core suite takes no lock (D-224 item
   6).
5. **After a delete:** the tenant is recorded by id only, never by name
   (D-224 item 9). Keep the backup folder until the end-of-build cleanup is
   done.
