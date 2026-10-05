# Stage 3e: separate database logins (F-1), the restart record, the heartbeat

Moved here word for word from `docs/BUILD-STATUS.md` on 2026-10-05 (the founder's context
housekeeping, D-191). Nothing below the marker was edited: it is the text as it stood under
"Stage 3 -- agreed with the founder before building", 3e merged 2026-10-02 (PR #35, D-185). It keeps its
original wording, including statuses that were true when each part was written.

Other files cite these sections as BUILD-STATUS "<heading>". Each cited heading is still in
`docs/BUILD-STATUS.md`, as a one-line stub pointing here. "Above" and "below" in this text
refer to the order the blocks had there: 3a, 3b, 3c, 3d, card billing, 3e.

<!-- moved text starts on the next line -->
**3e -- F-1, separate database logins.** Approved as proposed:
- **`docflow_api`**: tenant requests, plus the intake-token, sign-in-identity
  and refusal-alert policies.
- **`docflow_admin`**: the Console and the maintenance scripts, holding every
  `platform_admin_access` policy, 0029's `platform_admin_read` included.
- **`docflow_worker`**: the rollup, scheduler, pipeline-sweep and lifecycle
  policies, plus tenant sessions for jobs.
- **`docflow_stripe`**: the Stripe lookup policy and EXECUTE on
  `record_stripe_subscription_event()`, with EXECUTE revoked from
  `docflow_app`. This closes D-173's residual risk.

Counted on `main` 2026-09-29: 52 policy uses of session flags across 19
migrations, and each flag session is opened by one service only.

**Founder's condition: the pooler limits are checked before the migration is
written.** Checked 2026-09-29:
- **Each login gets its own pool.** Supabase's Supavisor FAQ: the pool size
  is the most direct connections the pooler keeps "per unique user, database,
  and mode combination"; two combinations at pool size 120 may form 120 each.
  Supabase Storage is one more combination with the same pool size.
- **Staging's limit:** `max_connections` = 60 (read from the database), with 3
  reserved for the superuser. That is Supabase's Nano or Micro size. **The
  dashboard shows Nano** (founder, 2026-09-29). My first reading, Micro from
  the memory settings, was wrong. Supabase's own services held 13
  connections at the time of reading.
- **Headroom:**
  - After 3b and 3e there are five combinations: four logins plus Storage
    (`docflow_app` is retired by F-1).
  - The worst case is 5 x the pool size, against about 44 usable connections.
  - Supabase's guidance for several combinations is to keep the pooler under
    40% of `max_connections` (24 here).
  - So four logins fit with headroom **only at a pool size of about 4-5**. The
    pool size is a dashboard setting (Database -> Settings -> Connection
    pooling -> Default Pool Size), readable by the founder only.
- **Options, for the founder:**
  - keep the compute and set the pool size to 4-5 (recommended for staging);
  - upgrade staging to Small (90 connections, about $15/mo against Nano's
    $0);
  - merge `docflow_stripe` into `docflow_api`, not recommended because it
    reopens D-173's risk.

- **Decided (founder, 2026-09-29): keep the compute (Nano) and set the pool size to 5.**
  Default Pool Size was **15** (founder, 2026-09-29). At 15, the five
  combinations could ask for 75 connections, more than the database's 60,
  which is why it comes down. **The founder changed it to 5 on 2026-09-29**,
  after the Stage 2 checkpoint's database suites had finished, so the change
  could not drop a running suite's connections. **Compute is Nano** (founder,
  from the dashboard). Five combinations at 5 is 25 connections
  at most, against about 44 usable. The first staging suite run after the
  change shows whether 5 is enough for the tests that hold one connection
  while probing with another; the result is reported.
- **Production's pool size and compute are decided in Phase 6, with real load,
  not inherited from staging** (founder).

**Also in 3e, carried from 3d (founder, 2026-10-01): the worker's restart
record.** A worker that restarts slowly (up a few minutes, then a worker
exits, again and again) keeps the heartbeat fresh, so `/healthz` can't show
it (RUNBOOK 9.2). It goes in **3e's migration**, so there is no separate
backup-and-apply round:
- `app.run_workers` records each start (a row or a timestamp array behind a
  SECURITY DEFINER function, granted to `docflow_worker`).
- `/healthz` shows the number of starts in the last hour.
- An alert fires past a threshold. **Approved by the founder (2026-10-01):**
  a new alert type `worker_restarting`, high, when **3 or more starts fall
  within 60 minutes**, at most hourly. A deploy is one start; a deploy plus one
  crash is two; three in an hour isn't normal running. It is raised by the
  launcher itself at start-up, right after recording the start. That's why
  it works when the worker is crash-looping: the stuck sweep, which raises
  `dispatcher_stopped`, doesn't get the chance. The every-alert-type test
  covers it once it is registered.
- **It gates the first worker deploy**, with G, the 500 + 1 staging run and
  the worker memory measurement. It is not a "later".

**Also in 3e, carried from 3d (founder, 2026-10-01): the external monitor's
approach.** Staging API is private-only; Better Stack can't poll `/healthz`.
Monitor approach to be decided in 3e. The 3e design must:
- **Evaluate a push heartbeat instead of polling:** the dispatcher pings a
  Better Stack heartbeat URL after each successful pass (the URL kept as a
  secret), and Better Stack alerts when the pings stop. That covers a worker
  that is down and one crash-looping because it can't reach the database,
  since no successful pass means no ping. It needs no public API.
- **Check that Better Stack's free plan includes heartbeat monitors.** Its
  heartbeat docs, read 2026-10-01, don't say.
- **Say whether production's API will be public, and if so whether it
  needs its own polled check** (a heartbeat says nothing about the API
  itself).
- Rework the alert timing and the pass mark for whichever approach is
  chosen (RUNBOOK 9.4 has the polled version: 17 minutes).
- It gates the first worker deploy, with the rest of that list.

**Also in 3e, decided at the 3d merge (founder, 2026-10-01): the two
two-at-once sweep tests.** RUNBOOK 9.3's table has two beat tasks that rest
on their guard alone: the scheduled-jobs sweep (`SKIP LOCKED`) and the
stuck sweep (compare-and-set). 3e adds a test for each that runs the sweep
twice at once against the real database and asserts nothing is done twice.
They gate the first worker deploy.

### 3e detailed design -- PROPOSED (2026-10-02); nothing built until the founder approves

Branch `phase55/stage3e-design`, from `main` `f191e27`. Five parts: A the
four logins, B the restart record, C the external monitor, D the two sweep
tests, E tests and proof. The questions are at the end (Q1-Q8). The
founder's go of 2026-10-02 settled two things first:
- **Production's API is public.** Browsers call it directly (the web app's
  `NEXT_PUBLIC_API_BASE_URL`, used by client components), and Postmark's
  inbound webhook and Stripe's webhooks must reach it. Staging's API stays
  private (no public IP).
- **Monitor shape: a push heartbeat for the worker, plus a polled
  `/healthz` check on production only.** The founder asked for two points
  to be covered: what the heartbeat proves beyond the dispatcher (C2), and
  throttling the ping (C3).

#### A. F-1: four database logins

**A1. The roles.** Migration `0036` creates `docflow_api`,
`docflow_admin`, `docflow_worker` and `docflow_stripe` as **NOLOGIN, with no
password in the file** (D-159). The founder then turns each on in the SQL
Editor (`ALTER ROLE ... WITH LOGIN PASSWORD '...'`), as for `docflow_app`
(D-013). All four are NOBYPASSRLS, with no CREATE on `public`, and
`idle_in_transaction_session_timeout = '5min'` (as 0028 sets for
`docflow_app`).
- **Table privileges come from one NOLOGIN group role, `docflow_tables`**:
  today's `docflow_app` grants (DML on every table and sequence, plus the
  default privileges). Each login is a member of it and of nothing else.
  This matters: a policy `TO docflow_admin` also applies to any role that is
  a member of `docflow_admin`, so no login may ever be a member of another.
  A test checks it (E1).
- **Who uses which login:**

  | Login | Used by | Secret held by |
  |---|---|---|
  | `docflow_api` | every tenant request; the intake-token lookup, the sign-in lookups (users and `platform_admins`), the refusal alert; `/healthz` | the API (`DATABASE_URL`) |
  | `docflow_admin` | `admin_data_access` (the Console) and the maintenance and seed scripts | the API (`ADMIN_DATABASE_URL`), and the founder's machine for scripts |
  | `docflow_worker` | every worker task: tenant sessions for jobs, the rollup, scheduler, lifecycle, stuck sweep, dispatcher | the worker (`DATABASE_URL`) |
  | `docflow_stripe` | the Stripe webhook only: the customer lookup, then that tenant's update and the two event functions | the API (`STRIPE_DATABASE_URL`) |

**A2. The policies.** `tenant_isolation` stays as it is: keyed on
`app.tenant_id`, open to every login. Each login serves many tenants, so
which tenant a request belongs to stays the job of the single data-access
layer (Section 7.5). Every other flag policy gets a `TO` naming exactly one
login. The migration files hold 54 such uses (52 counted 2026-09-29, plus
0035's 2). The build lists the live ones from staging's `pg_policies`, and
`0036` alters each one by name (`ALTER POLICY ... TO ...`):

| Flag | Policies | `TO` |
|---|---|---|
| `app.is_platform_admin` | `platform_admin_access` on every tenant-scoped table, 0029's `platform_admin_read`, the platform tables (0018) | `docflow_admin` |
| `app.rollup`, `app.scheduler`, `app.lifecycle`, `app.pipeline_sweep`, `app.dispatcher` | `rollup_*` (0017), `scheduler_access` (0013), `lifecycle_read` (0019), `pipeline_sweep_read` (0027), `dispatcher_raise` / `dispatcher_enqueue` (0035) | `docflow_worker` |
| `app.intake_token`, `app.auth_user_id`, `app.intake_refusal` | `token_lookup` (0003), `self_lookup` on `users` (0001) and on `platform_admins` (0020), `intake_refusal_raise` / `_enqueue` (0029) | `docflow_api` |
| `app.stripe_webhook` | `stripe_webhook_lookup` (0019) | `docflow_stripe` |

**Proposed: keep the flag condition and add the `TO`** (Q3). The `TO` is
the database's guarantee: a tenant connection that sets a flag sees nothing
extra. The flag keeps each narrow session narrow *inside* a login, so a
worker tenant session can't insert a tenant-less alert by accident.

**A3. The functions** (13 SECURITY DEFINER functions; EXECUTE revoked from
`docflow_app`):

| Function | Granted to |
|---|---|
| `record_stripe_subscription_event`, `record_stripe_card_event` | `docflow_stripe` only. **This closes D-173's residual risk.** |
| `dispatch_candidates`, `probe_candidate`, `mark_dispatched`, `clear_dispatched`, `dispatcher_heartbeat`, `provider_record_failure`, `provider_record_success`, `provider_take_probe`, `count_routing_model_failure` | `docflow_worker` |
| `dispatcher_status` | `docflow_api` (`/healthz`), `docflow_worker` (stuck sweep), `docflow_admin` (Console) |
| `provider_state` | `docflow_api` (review queue's Delayed), `docflow_worker`, `docflow_admin` (Console, onboarding) |
| new in B: `record_worker_start`, `worker_starts_last_hour` | `docflow_worker`; `docflow_api` and `docflow_admin` |

**A4. The code** (`docflow_core.db`):
- One engine per login. `DATABASE_URL` is the process's own login: the API's
  is `docflow_api`, the worker's is `docflow_worker`. `platform_session()`
  binds to the admin engine. `stripe_webhook_session()` and a new
  `stripe_tenant_session()` bind to the Stripe engine.
- **Each process checks its own logins at start-up** and refuses to start
  if `current_user` isn't the expected one. That catches a pasted wrong URL
  before it can serve a request. The worker also refuses to start if
  `ADMIN_DATABASE_URL` or `STRIPE_DATABASE_URL` is set, as the API refuses
  `PARSE_SERVICE_TOKEN` (3c, Q12).
- **Import boundaries:** `stripe_tenant_session` may be imported only by
  `billing_webhooks`, checked by the same kind of test as
  `adminDataAccess`'s.
- **The limit, as D-159 named it:** the API process holds three secrets,
  because the Console and the webhooks live in it (Section 3: one deploy).
  The database now refuses a tenant connection that sets a flag. But code
  in the API that deliberately picks the admin or Stripe engine is stopped
  only by the import-boundary tests.

**A5. Pooler.** Five combinations (four logins plus Storage) at pool size 5
is 25 connections at most, against about 44 usable on Nano. That's the
headroom the founder approved on 2026-09-29. The first staging suite run
after the cutover reports whether 5 is enough.

**A6. Cutover on staging** (proposed; Q5): **one migration, a hard
cutover.** Staging has no deployed API or worker, only local stacks, so a
few minutes' gap costs nothing. Production (Phase 6) is created straight
into the final state.
1. `0036` (policies, grants, roles, B's table). It changes no rows, so the
   only "backup" is the reverse script, written and checked in with it (Q5).
2. The founder sets the four passwords in the SQL Editor; the local `.env`
   and `apps/web` e2e settings get the new URLs.
3. Staging suites (core, worker, API) as the new logins.
4. **`docflow_app` is set NOLOGIN by the founder** (SQL Editor) once the
   suites pass. It is dropped after 3e has run cleanly for 3 days, the same
   rule as the backups (RUNBOOK 1.3).
- CI: `scripts/ci/create_app_role.py` becomes `create_logins.py`. It only
  turns the four roles on with CI-local passwords; the grants are the
  migration's own, since the roles now exist when it runs.
- RUNBOOK gains: creating the logins, rotating one login's password, and
  which secret goes on which app.

#### B. The worker's restart record (approved 2026-10-01; detail proposed here)

- **Table `worker_starts`** (`0036`): `id`, `started_at`, `machine`
  (`FLY_MACHINE_ID`, or the hostname locally), `image` (`FLY_IMAGE_REF` when
  set). It is a **global table, named as such**: an operational record with
  no tenant and no customer data. RLS is on with no policies, so it is
  reached only through two SECURITY DEFINER functions:
  - `record_worker_start(machine, image)` inserts the row and returns the
    starts in the last 60 minutes, this one included (`docflow_worker`);
  - `worker_starts_last_hour()` returns that count (`docflow_api` for
    `/healthz`, `docflow_admin`).
  - Retention: rows are kept. A few a day is negligible (Q6).
- **The launcher** (`app.run_workers`), before it starts the two Celery
  workers:
  1. records the start;
  2. if the count is 3 or more, raises **`worker_restarting`** (high,
     tenant-less, deduped per UTC hour) with the count and the machine. The
     founder email comes from the row (7.9).
  - **Recording never stops the worker from starting.** If the database
    can't be reached, the launcher logs the error type and starts the
    workers anyway. A worker that can't reach the database is C's job.
  - The insert policy for the alert: `TO docflow_worker`, flag
    `app.dispatcher`, extended to this alert type (as 0035 did for
    `routing_model_failure`). The every-alert-type test covers it once it
    is registered. Founder-facing catalog wording comes with the build, as
    for `dispatcher_stopped`.
- **`/healthz`** adds `"worker_starts_last_hour": n`. It still returns
  HTTP 200 always and still contains the bytes `"stale":false` when fresh,
  so the pinned-bytes test stays as it is.

#### C. The external monitor

**C1. The tool.** Checked 2026-10-02 against the pages themselves (raw
page text, not a summary):
- **Better Stack.** Its free plan does include heartbeats ("10 monitors &
  heartbeats, 1 status page"). **But the pricing page's free-plan heading
  reads "Free for personal projects."** The Terms of Use say nothing about
  plan type. **Correction:** RUNBOOK 9.4 records "no restriction on
  commercial use stated" (read 2026-10-01). Either the page changed or I
  missed the heading. It is the same kind of wording that ruled out
  UptimeRobot ("hobby and non-profit projects").
- **Healthchecks.io.** Free "Hobbyist" plan: 20 checks, email alerts. No
  commercial or personal-use restriction on its pricing page or in its
  Terms. Its paid plans add checks and log entries, not permission. It
  can't poll a URL, though: it only receives pings. Pings are limited to 5
  a minute per check ("may get rate limited and not recorded"). A check has
  a period and a grace time, and accepts a `/fail` signal for an immediate
  alert.
- **Recommendation (Q1):** Healthchecks.io free for the worker heartbeats
  (one check each for staging and production). The polled check on
  production's API is decided in Phase 6, when production's API exists,
  with its tool chosen then. It isn't needed for the first worker deploy,
  since staging's API is private.

**C2. What the ping proves** (founder's first point). The dispatch process
pings after a pass that succeeded. So a ping proves:
- the dispatch process is running and can reach the database (the pass
  writes the heartbeat row);
- the documents worker hasn't exited: the launcher stops the dispatch
  worker when the documents worker exits (3d), so the pings stop.

On its own it doesn't prove **the documents worker is taking work.** A
documents worker that is up but not consuming (a hung main process, or a
lost broker connection) leaves dispatched documents unclaimed. **Found
while checking: today nothing alerts on that.** After 30 minutes the stuck
sweep returns them to waiting (D-095) and the dispatcher sends them again,
around and around, silently. `document_stuck` covers only documents
already `processing`.

**Proposed (Q2): make the ping depend on the documents side.**
`dispatcher_status()` also returns the age of the oldest document
dispatched but not yet claimed. The dispatcher sends only into free slots,
so a healthy worker claims within seconds.
- Under `DISPATCH_UNCLAIMED_ALERT_MIN` (proposed 10 minutes): a normal
  ping.
- Over it: a `/fail` ping, which alerts at once, and no success pings until
  it clears.

What the heartbeat still doesn't cover, said plainly:
- a document that hangs inside a task: the 27-minute hard limit (3a) and
  `document_stuck`;
- the parse service: `parse_service_unavailable`;
- the model provider: `model_api_failure`.

**C3. Throttling** (founder's second point). At most one ping every
`HEARTBEAT_PING_MIN` = **5 minutes** (a named constant). That's 288 a day
per environment, far under the 5-a-minute limit. The dispatch process keeps
the last-ping time in memory; it is a single process, and a restart pinging
early is harmless.
- Each ping is a GET with a 5-second timeout and no body or data (7.10).
- A failed ping is logged by error type and never fails the pass.
- The URL is a secret (`HEARTBEAT_URL`). It is never logged. When unset
  (locally and in CI) no ping is sent.

**C4. Timing and pass mark.** The check is set to period 5 minutes, grace
5 minutes.
- **Stopping the worker:** the email within **12 minutes** of the last
  ping. That is 5 to the expected ping, 5 of grace, about 1 for email, and
  1 of slack, since the docs don't say how often lateness is evaluated. The
  polled design's was 17.
- **An unclaimed document:** an alert within 10 minutes plus one pass,
  from the `/fail` ping.
- RUNBOOK 9.4 is rewritten for the heartbeat. The polled notes stay for
  Phase 6.

#### D. The two two-at-once sweep tests (decided at the 3d merge)

Real database, CI and staging. Two threads, each on its own connection,
released together by a barrier:
- **Scheduled jobs:** seed several due jobs and run `run_scheduled_jobs`
  twice at once. Each job runs exactly once (its side effect counted), and
  each is marked done once.
- **Stuck sweep:** seed stuck documents across two tenants (`processing`
  past the timeout, and dispatched but unclaimed), then run the sweep twice
  at once. Each document is changed once, its attempt counted once, at most
  one lost-call row, and one alert per document.

#### E. Tests and proof against the real thing

- **E1 (real database, CI + staging):**
  - For every flag, set it on each of the four logins: only the owning
    login sees more than its tenant.
  - A `docflow_api` tenant session that sets every flag sees only its own
    tenant.
  - The EXECUTE matrix: every SECURITY DEFINER function is callable by
    exactly its logins, and refused for `docflow_app`, `anon` and
    `authenticated`. This includes `docflow_api` being refused
    `record_stripe_subscription_event` (D-173 closed).
  - A catalog test: every policy whose expression names an `app.*` flag
    other than `app.tenant_id` has exactly its expected `TO`. A future
    migration that adds a flag policy without one fails CI.
  - No login is BYPASSRLS, a member of another login, or able to CREATE.
- **E2:** start-up refuses a wrong `current_user`, and the worker refuses
  admin or Stripe URLs. Import-boundary test for `stripe_tenant_session`.
  `test_rls_flags.py` rewritten for logins.
- **E3, restart record:**
  - a start is recorded;
  - the third start within 60 minutes raises one alert, the fourth none in
    the same hour;
  - the database down at start still starts both workers;
  - `/healthz` shows the count, and the pinned bytes are unchanged.
- **E4, heartbeat (unit, with a fake ping server):**
  - a ping after a successful pass, none after a failed pass;
  - at most one per 5 minutes;
  - `/fail` past the unclaimed limit, and back to success after it clears;
  - a ping error never fails the pass;
  - no URL, no request; the URL never in a log line.
- **Against the real thing, at the first worker deploy (gate list):**
  1. Stop the worker on Fly: the Healthchecks.io email within 12 minutes.
  2. Start it again: the recovery email.
  3. Kill the documents worker 3 times within the hour (`fly ssh`): the
     `worker_restarting` email.
  4. Stop the documents worker's consumption with the dispatch process
     left running: the `/fail` alert.
  - Then the full gate list (G, A4, fresh token, 500 + 1, memory).

**Effort:** about 4-5 days: F-1 3-4, as estimated, plus about 1 for B and
C. Cost $0 if Q1 goes to Healthchecks.io free.

**Questions for the founder:**
1. **Monitor tool:**
   - (a) Healthchecks.io free for the heartbeats (recommended), with
     production's polled API check decided in Phase 6;
   - (b) Better Stack free anyway: its Terms don't restrict it, but its
     pricing page says "personal projects";
   - (c) a paid Better Stack plan (price checked on request).
2. **Should the ping depend on the documents worker** (C2's
   unclaimed-document check and `/fail`, recommended)? Or dispatcher only,
   with the gap recorded? If yes: `DISPATCH_UNCLAIMED_ALERT_MIN` = 10?
3. **Policies: keep the flag and add `TO` (recommended), or `TO` only?**
4. **Table privileges:** the same grants for all four logins through
   `docflow_tables` (recommended; RLS is the boundary)? Or narrower
   per-login table grants now (for example `docflow_stripe` only on the
   tables the webhook touches)? Narrower adds about a day and a list to
   keep in step. It could be a Phase 6 hardening item instead.
5. **Cutover:** one migration with a hard cutover on staging, a reverse
   script instead of a backup schema (no rows change), `docflow_app` set
   NOLOGIN after the suites pass, and dropped after 3 clean days?
6. **`worker_starts` kept without pruning?**
7. **Heartbeat timing:** ping every 5 minutes, period 5 / grace 5, pass
   mark 12 minutes?
8. **Logins for scripts:** the maintenance and seed scripts run as
   `docflow_admin` for everything, tenant sessions included (recommended:
   one secret on the founder's machine). Or a second URL for their tenant
   sessions?

**3e -- APPROVED WITH CONDITIONS (founder, 2026-10-02).** The founder read
the design and checked Healthchecks.io's pricing page themselves.
1. **Q1: (a), Healthchecks.io free.** No personal or hobby restriction was
   found; the only rule is not using several accounts to get round the
   limits. Two checks of 20.
2. **Q2: yes, the ping depends on the documents worker; limit 10
   minutes.** Condition: "claimed" must mean the task actually started.
   Confirm the documents worker's prefetch is 1, or that dispatch slots
   account for prefetch. Record the largest unclaimed age seen during the
   500 + 1 run, so the limit is measured, not guessed.
   - *Checked:* `worker_prefetch_multiplier=1` with `task_acks_late=True`
     (`celery_app.py`), so a process holds no message beyond the one it is
     running. A message that has been taken has been started.
   - ***Found while checking, which reopens the value (Q9 below):*** the
     documents worker's processes also run every non-document task:
     exports, imports and the manual rollup on `interactive`; the four beat
     sweeps on `bulk`. The dispatcher counts only documents as in flight. So
     a slot busy with one of those makes a dispatched document wait in the
     broker on a healthy worker. Their hard limits are rollup 15 min,
     scheduled jobs 10, lifecycle 10, export and import 5, stuck sweep 4. On
     staging (one slot), a rollup or a lifecycle sweep held up by Stripe
     could pass 10 minutes with nothing wrong.
3. **Q3: keep the flag and add `TO`.**
4. **Q4: shared grants through `docflow_tables` now.** **Phase 6 item
   (named):** narrow `docflow_stripe` to the tables the webhook touches. It
   is the login with an internet-facing caller. The other three are not
   narrowed.
5. **Q5: hard cutover, approved with three conditions:**
   1. **`0036` is not applied to staging until Claude has reported the
      `backup_0035` check** (on or after 2026-10-05 03:18 UTC), so 3e's
      cutover can't land inside 3d's 3-day clean-run window. Written into
      the cutover steps (A6, step 0).
   2. **The reverse script is tested, not just checked in.** CI runs
      forward, reverse, forward. On staging, `pg_policies` and the function
      grants are snapshotted before applying, and the reverse is shown to
      restore them exactly. That snapshot is the backup.
   3. **Before `docflow_app` goes NOLOGIN, every place its URL lives is
      listed:** the local `.env`, any Fly secrets left from 3c's staging
      run, GitHub secrets, and anything else found.
6. **Q6: yes,** rows kept. A crash loop writes few rows and the alert is
   deduped per hour.
7. **Q7: yes.** Pings land 5:00-5:30 apart (passes every 30 s), and 5
   minutes of grace covers that.
8. **Q8: `docflow_admin` for all script work,** one secret on the
   founder's machine.

**A6, step 0 (added):** before step 1, Claude reports the `backup_0035`
check, not before 2026-10-05 03:18 UTC; the founder confirms; then the
`pg_policies` and function-grant snapshot is taken; then `0036`.

**Q9 (open, from the Q2 check): the unclaimed limit, given shared slots.**
Options:
- (a) **Recommended:** `DISPATCH_UNCLAIMED_ALERT_MIN` above the longest
  non-document hard limit: **20 minutes** (the rollup's is 15). The
  measurement condition stays: record the largest unclaimed age during the
  500 + 1 run. Also run the rollup and a sweep during it, so the shared-slot
  case is in the number. Cost: the "not taking work" alert comes after 20
  minutes, not 10. It is still the only alert for that failure.
- (b) Keep 10. Accept a false `/fail` when a long non-document task holds
  the only slot (staging: one slot; production: rarer with more slots).
- (c) Count running non-document tasks as in flight, so the dispatcher
  never sends into a slot they hold. That changes 3d's dispatcher; not
  recommended inside 3e.
- Building goes ahead meanwhile. The limit is one named constant, and
  nothing else depends on its value.

**Q9 -- DECIDED (founder, 2026-10-02): 20 minutes, with two conditions.**
1. **It stays below the stuck sweep's 30.** Checked: the sweep clears
   `dispatched_at` at `STUCK_PROCESSING_TIMEOUT_MIN`, and the next dispatch
   stamps it afresh (`mark_dispatched`), so the unclaimed age restarts and a
   limit at or above 30 could never fire. 20 leaves 10 minutes of margin.
   Pinned: `test_heartbeat.py` holds the limit above every non-document
   task's hard limit and below the sweep's timeout, and
   `test_dispatch_db.py` proves the restart on the real database.
2. **The 500 + 1 measurement includes the slow case.** Partway through the
   run, the rollup and a sweep are triggered by hand, and the largest
   unclaimed age is recorded. Well under 20: keep it. Near 20: back to the
   founder before raising it (RUNBOOK 9.4).

The founder also asked that the record say plainly: **two stuck sweeps at
once can put a duplicate retry job on the queue, by design.** The document
task's claim runs it once and the second job does nothing. The test checks
that property; nobody should later "fix" it into counting jobs (D-185).

#### 3e build -- as built (2026-10-02), branch `phase55/stage3e-design`

Commits: `b1f909a` (code and tests), `c38281b` (`create_logins.py` fixed,
docs), `a7ba990` (Q9, CI fixes), `229cc4b` (`/healthz` keys pinned). Full record in D-185. `0036` is **not** on
staging: it waits for the `backup_0035` check (on or after 2026-10-05 03:18
UTC; Q5 condition 1, RUNBOOK 10.2 step 0). Until then CI is the only
database proof; the staging suites run after the cutover (RUNBOOK 10.2 step
5). The PR can merge before the cutover.

- **A, the logins:** `0036` (four NOLOGIN roles, `docflow_tables`, 51 `ALTER
  POLICY ... TO`, 16 function grants), `supabase/reverse/0036_reverse.sql`,
  `supabase/reverse/0036_pre_snapshot.json` (staging, 2026-10-02);
  `docflow_core.db` (one engine per login, `use_own_login`,
  `stripe_tenant_session`, `verify_logins`); start-up checks in the API, the
  Celery worker and the launcher; CI on the four logins
  (`scripts/ci/create_logins.py`, `migration_roundtrip.py`,
  `policy_snapshot.py`).
- **B, the restart record:** `worker_starts` and its two functions;
  `docflow_core.worker_starts`; the launcher records each start and raises
  `worker_restarting`; `/healthz` shows `worker_starts_last_hour`.
- **C, the heartbeat:** `docflow_core.heartbeat`, called only by the
  dispatch task; `HEARTBEAT_URL`; `/fail` past `DISPATCH_UNCLAIMED_ALERT_MIN`
  (20).
- **D, the sweeps twice at once:** `test_sweeps_twice_at_once_db.py`.
- **Docs:** RUNBOOK 10 (new), 9.2-9.6; SETUP Step 1.6; `.env.example`; both
  `fly.toml` secret lists; D-185.

*CI on `b1f909a`* (run 37028938291): **the `0036` round trip passed**
(forward -> reverse -> forward against staging's snapshot). Every Python
job then stopped at "Turn on the four logins": `create_logins.py` called
`.scalar()` on a psycopg cursor. Its cause was only in the step log; the
script now prints each problem as an annotation.

*CI on `c38281b`* (run 37029391585): parse 128 tests, web and web-live green
(the real API on its three logins, the seed scripts as `docflow_admin`).
Core 744 tests with 1 failed, API 626 with 11 failed, worker 179 with 1
failed. All 13 failures were in tests, not in the code under test:
- core: the idle-timeout test read the deleted `create_app_role.py`;
- API: id lists bound as jsonb (7, one fixture); three Stripe tests called
  the event function as `docflow_api`, which `0036` now refuses (as
  designed: D-173 closed); a `/healthz` test compared the whole body;
- worker: the start count read as `docflow_worker`, which `0036` correctly
  doesn't grant.

*Founder's review of the `a7ba990` test fixes (2026-10-02):* the Stripe,
jsonb, start-count and timeout fixes keep the same checks on the right
login or file; the Stripe tests now call the function through
`stripe_tenant_session`, and the API login's refusal stays covered by
`test_logins_db.py`'s grant matrix. **One test had been weakened:** the
`/healthz` full-body comparison became two key checks, so the public
endpoint could grow a field unnoticed. `229cc4b` pins the top-level keys to
exactly `status`, `dispatcher`, `worker_starts_last_hour`.

**CI GREEN on `a7ba990`** (run 37030082446), every job, from each job's
own counts notice: core `745 tests, 0 failed, 0 skipped, 0 unapproved`
(744 + the Q9 constants test); api `626 tests, 0 failed, 0 skipped`;
worker `180 tests, 0 failed, 0 skipped` (179 + the unclaimed-age restart test);
parse unit 128 and HTTP 56 (both 0 failed, 0 skipped); web and web-live
green. `.github/approved-skips.txt` is empty and a skip fails the job, so
the Linux-only worker tests (real Celery, the dispatch worker, the
launcher's signals) ran and passed on `docflow_worker`.

**CI GREEN on `229cc4b`** (run 37030351858), every job, from each job's
own counts notice: core `745 tests, 0 failed, 0 skipped, 0 unapproved`;
api `626 tests, 0 failed, 0 skipped, 0 unapproved` (the pinned-keys check is
inside an existing test, so the count is unchanged); worker `180 tests, 0
failed, 0 skipped, 0 unapproved`; parse unit 128 and HTTP 56 (both 0
failed, 0 skipped); web and web-live green. **The 3e merge gate is met:**
CI green, including the `0036` round trip and the four-login tests. The PR
can merge before the cutover; `0036` reaches staging only after the
`backup_0035` check (on or after 2026-10-05 03:18 UTC), and the staging
suites run then (RUNBOOK 10.2).

**Every test 3e removed, by name (founder, 2026-10-02: "a deleted test is
the one change a green run can't show you").** Counts against main
`f191e27` (run 37023497763): core 747 -> 745, api 587 -> 626, worker 167 ->
180. Read from `pytest --collect-only` on both commits, which reproduces
CI's counts exactly; the totals hide removals, so each suite was diffed by
test ID. 17 IDs are gone; each one's check is still made, by the test named
beside it:

| Suite | Gone from main | Why | Where the check is now |
|---|---|---|---|
| core | `test_each_stripe_event_function_is_granted_the_same_way_in_the_migration_and_in_ci`, 13 cases (one per function: the 2 Stripe functions, 11 of 0035's) | It compared each function's `GRANT EXECUTE ... TO docflow_app` in its migration with the same grant in `scripts/ci/create_app_role.py`. 3e deleted that script: CI's roles now come from `0036` itself, so there is no second copy to keep in step | `test_migration_0036_grants_each_function_to_exactly_its_logins` (core): one test, all 16 functions (the 13, plus 0036's 3 new), the exact set of logins for each, and that each is revoked from `docflow_app` and PUBLIC first. Against the live database: `test_each_security_definer_function_is_callable_by_exactly_its_logins` (api, 16 cases; the same table, checked identical; also asserts `anon` and `authenticated` are refused) |
| core | `test_ci_connects_as_the_non_bypassing_app_role` | CI connects as `docflow_app` no more | Renamed `test_ci_connects_as_the_four_logins`: each of the 5 URLs names its login, the worker job's own URL is `docflow_worker`, and `docflow_app` appears nowhere in `ci.yml`. NOBYPASSRLS is checked by `create_logins.py` and `test_each_login_connects_as_itself_and_is_only_a_member_of_docflow_tables` (api) |
| core | `test_the_idle_transaction_timeout_is_the_same_in_the_migration_and_in_ci` | Read the timeout from the deleted script | Renamed `test_the_idle_transaction_timeout_is_the_same_for_docflow_app_and_the_four_logins`: `0028`'s value for `docflow_app`, and the same `5min` set four times in `0036` |
| api | `test_only_the_app_role_may_execute_the_function` | The Stripe function moves from `docflow_app` to `docflow_stripe` (D-173) | Renamed `test_only_the_stripe_login_may_execute_the_function`: `docflow_stripe` may; the API, worker and admin logins, `anon` and `authenticated` are refused; no PUBLIC grant |
| worker | `test_the_flag_lets_the_dispatcher_raise_only_its_four_tenant_less_alerts` | Name only: 3e adds a fifth (`worker_restarting`); the body is unchanged | Renamed `test_the_flag_lets_the_dispatcher_raise_only_its_tenant_less_alerts` |

So core's net -2 is 15 IDs gone (the 13 cases and 2 renames) and 13 added
(the grants test, the round-trip order test, 2 renames, 9 heartbeat tests).
api: 1 renamed, 39 new. worker: 1 renamed, 13 new.
