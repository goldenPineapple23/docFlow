# Stage 3d: per-tenant fairness and the dispatcher (H4)

Moved here word for word from `docs/BUILD-STATUS.md` on 2026-10-05 (the founder's context
housekeeping, D-191). Nothing below the marker was edited: it is the text as it stood under
"Stage 3 -- agreed with the founder before building", 3d merged 2026-10-02 (PR #33, D-184). It keeps its
original wording, including statuses that were true when each part was written.

Other files cite these sections as BUILD-STATUS "<heading>". Each cited heading is still in
`docs/BUILD-STATUS.md`, as a one-line stub pointing here. "Above" and "below" in this text
refer to the order the blocks had there: 3a, 3b, 3c, 3d, card billing, 3e.

<!-- moved text starts on the next line -->
**3d -- H4, per-tenant fairness.** Agreed design:
- Documents wait as `pending` in the database.
- A dispatcher takes turns between tenants, with a cap per tenant on
  documents already on the queue.
- `pg_trgm` matching stays in Stage 4.

**Interplay with the stuck-document sweep** (founder: written down before
building; each point that changes existing sweep behaviour is marked
**CHANGES**):

1. **CHANGES: the sweep stops enqueueing `pending` documents directly.**
   - Today every `pending` document older than
     `STUCK_PROCESSING_TIMEOUT_MIN` (30 min) is put on the queue again by the
     sweep, on the theory that its job was lost (D-095).
   - Under the dispatcher, `pending` is the normal state for a backfill still
     waiting its turn. The sweep enqueueing it would bypass the per-tenant cap
     and push a 500-document backfill onto the queue 30 minutes in, undoing
     fairness.
   - Proposed: `pending` splits into *waiting* (not yet dispatched) and
     *dispatched* (sent to the queue, not yet claimed), recorded by a
     `dispatched_at` column on `documents`, which is a migration. Only a
     document dispatched and unclaimed past the timeout counts as a lost job.
     The sweep hands it back to the dispatcher, which re-sends it within the
     cap. Nothing puts it on the queue except the dispatcher.
2. **CHANGES: the `document_stuck` "waiting" alert.**
   - Today one warning per tenant per day fires for any `pending` document
     past 30 minutes.
   - A backfill legitimately waits longer than that, so the alert would fire
     every day of every large backfill.
   - Proposed: the alert fires for a *dispatched* document unclaimed past the
     timeout (a lost job, as today). A document still *waiting* alerts only
     when the dispatcher itself has not run for a set time, which means the
     dispatcher has stopped. That would be a new constant, raised with the 3d
     design.
3. **Unchanged:** the handling of `processing` (retry, then DOC-022 and an
   alert after `MAX_PROCESSING_ATTEMPTS`). Quarantined and staged documents
   stay out of both the dispatcher and the sweep. A released document enters
   the dispatcher's turn order like a new one.
4. **To decide in 3d's design, before code:**
   - the per-tenant cap (a constant);
   - whether interactive single uploads skip ahead of a tenant's own
     backfill (Section 5.1 says interactive is always drained first);
   - what triggers the dispatcher: beat, each completion, each upload, or all
     three.

**3d also: when the model provider is down, documents wait instead of
failing.** **Required before the first pilot** (founder, 2026-09-29). Built in
3d. The requirements are the founder's; everything marked *proposed* is mine
and waits for an answer.

*Why.* Today a provider outage fails every document that arrives during it:
- the SDK retries twice in a few seconds;
- then the document is `failed` with DOC-008, and the customer has to
  upload it again;
- the founder gets one `document_failed` alert per tenant per day, which
  never says the provider is down.

Section 7.9's "retry with exponential backoff" and 7.15.3's "repeated
model-API failure" alert are not met. Found during the 2026-09-29 Anthropic
incident, which failed the Stage 2 golden run at 10:18.

1. **What waits, and what still fails at once** (founder):
   - **Waits:** HTTP 5xx, 529 (overloaded), 429 (rate limited), and
     network, connect or silent-stream timeouts, including a connection
     dropped mid-stream. Classified by status code, not by SDK class name,
     so an SDK upgrade can't silently move a code between the groups.
   - **Fails at once, as today:** every other 4xx (a bad request, a schema
     error), a malformed answer (DOC-009) and the read deadline (DOC-020).
   - *Question:* 401 and 403 mean our API key or account is broken. That
     fails every document for every tenant. *Proposed:* they still fail the
     document at once (founder's rule), but also raise the provider alert
     with cause `our_credentials`, because one alert is more use than a
     `document_failed` per tenant.
2. **Waiting is recorded in the database, not in Celery.** Celery's delayed
   tasks sit in a worker's memory and are lost with it.
   - On a waiting error, the task moves the document `processing ->
     pending`. That is a new transition in 0027's state machine, so it needs
     a migration.
   - It records, as *proposed* new columns on `documents`:
     `provider_wait_started_at` (set on the first wait, kept until the
     document leaves `pending`), `provider_retry_at`, and
     `provider_last_error` (the status code only).
   - The dispatcher skips the document until `provider_retry_at`.
   - The return to `pending` subtracts the claim from
     `processing_attempts`, so waiting never uses up the crash-retry budget
     (DOC-022) and never looks like a timeout (3a).
3. **Backoff and the maximum wait** (*proposed*):
   - Retries at 1, 2, 4, 8 and 15 minutes, then every 15 minutes
     (`PROVIDER_RETRY_MINUTES`).
   - A 429 waits at least as long as the provider's `Retry-After`.
   - **Maximum total wait: 6 hours** (`PROVIDER_MAX_WAIT_HOURS`), counted
     from the first wait. Why 6: it covers provider incidents measured in
     hours within one working day (today's was about 40 minutes of errors),
     and the "delayed" state (point 5) tells the customer all along, so a
     customer with an urgent order can enter it by hand without waiting for
     a failure. Longer keeps a same-day order in limbo; shorter fails orders
     that would have gone through.
4. **After the maximum wait: failed, with a code that blames the provider,
   not the file.** *Proposed* new catalog entry, **DOC-024** "The reading
   service was unavailable":
   - message: "DocFlow tried to read this order for 6 hours, but the service
     it uses to read orders did not respond. Nothing is wrong with the file.";
   - action: "Upload the same file again, or enter the order by hand if it
     is urgent. DocFlow has already been alerted.";
   - severity high, audience both.
   - It goes in `FAILURE_ALERTS`, so the promise of an alert is kept.
5. **The customer sees "delayed", not an error** (founder). *Proposed:* no
   new status. A `pending` document with `provider_wait_started_at` set is
   shown as **Delayed**, with a new info-level catalog entry, **DOC-023**
   "Reading delayed":
   - message: "The service DocFlow uses to read orders isn't responding
     right now, so this order is waiting. Nothing is wrong with the file.";
   - action: "Nothing to do. DocFlow retries automatically and has been
     alerted. If it is still waiting after 6 hours, this page will say so.";
   - one badge, reusing `PILL`.
   - The Console's document list shows the same.
6. **One provider-down alert across all tenants, and a recovery notice**
   (founder). *Proposed:*
   - A **global** table, `model_provider_state`, with one row per provider
     and no `tenant_id`. It is named here as a genuinely global table
     (Section 10) and written only by the worker. Its columns: status (up or
     down), `down_since`, the last error, and the last success.
   - The provider is marked **down** after 3 waiting-class failures within 5
     minutes, from any tenants, with no success between them
     (`PROVIDER_DOWN_FAILURES`, `PROVIDER_DOWN_WINDOW_MIN`).
   - Being marked down raises **one** high-severity founder alert with no
     tenant: the new type `model_api_failure` (7.9's "repeated model-API
     failure"). Its dedupe key is the outage's `down_since`, so the outage is
     one alert whatever its length. The payload carries the cause (`5xx`,
     `overloaded`, `rate_limited`, `network`, `our_credentials`) and the
     count of waiting documents.
   - The **first success** marks it up and raises an info-level
     `model_api_recovered` alert: how long it was down, how many documents
     waited, and how many reached the 6-hour limit. Both are emailed from
     their rows (7.9: one alert, one row, two channels). Acknowledging stays
     a human action; recovery does not acknowledge the down alert.
   - A tenant-less alert needs an insert policy. In 3d that is a flag
     policy, as `rollup_raise` (0017) and `intake_refusal` (0029) are, and
     3e moves it to the worker's login. **Sequencing note:** if 3e went
     first, the policy would be granted to `docflow_worker` from the start.
     The agreed order stays as is unless the founder moves it.
7. **While the provider is down, the dispatcher holds everything and
   probes.**
   - It dispatches no document except one probe every 2 minutes
     (`PROVIDER_PROBE_MINUTES`): the oldest waiting document, so there is no
     made-up request. An error costs nothing, and a success is a real order
     read.
   - On recovery the backlog goes out through the normal turn-taking, so the
     tenant with the biggest backlog does not go first.

**How it fits with the rest:**
- **Dispatcher (3d):** waiting documents are `pending` with a
  `provider_retry_at` in the future. They take their turn once it passes.
  While the provider is down, only the probe goes out. Interactive-first and
  per-tenant turn-taking are unchanged.
- **Stuck sweep:** a waiting document goes back to `pending` with
  `dispatched_at` cleared, so it counts as *waiting*, not a lost job, and
  the sweep leaves it alone. The sweep's "dispatcher has stopped" alert
  (interplay point 2) must not fire while the dispatcher is deliberately
  holding, so the dispatcher records its heartbeat on every pass, holding or
  not. `processing` handling (retry, DOC-022, 3a's timeout rule) is
  unchanged, because waiting never touches `processing_attempts` or
  `timeout_attempts`.
- **Allowance counter:** no change. Metering counts documents not `failed`
  or `quarantined`, so a waiting document counts from arrival, as `pending`
  does today, and one that ends as DOC-024 drops out of the count, as a
  failed document does today.
- **Cost circuit breaker:** errors are not billed and add nothing. A
  connection dropped after the answer began was billed for its input; it
  stays on the cost record as today (D-163) and counts. A retry pays again,
  and that is on the record too.
- **Example prompting:** a routing call that fails still means "no
  examples". If only the routing model is down, extraction goes ahead
  without examples, as today.
- **Resumed documents (H3):** a document whose answer is already saved
  makes no model call, so it never waits.
- **Quarantine and test batches:** a release or "Run extraction" puts
  documents into `pending`, and from there they follow the same path.
- **Needs a migration:** the transition, the three columns, the global table
  and the alert policy. Backup first, deletes nothing.

**3d detailed design -- PROPOSED 2026-10-01; nothing is built until the
founder approves.** It builds on the two agreed outlines above (fairness
with its sweep interplay, and "documents wait when the model provider is
down") and answers the points they left open. Everything marked
*proposed* is mine; Q1-Q8 at the end are the decisions needed.

*Where things stand today* (read from the code on `main`, `085a2a5`):
- **Seven places put a document straight on the queue**, with no limit per
  tenant:

  | Where | Queue |
  |---|---|
  | upload (`documents.py`; the web page sends one request per file, so a 500-file upload is 500 requests) | `interactive` |
  | email intake (`email_intake.py`) | `interactive` |
  | Console "Run extraction" on a test batch (`admin.py`) | `interactive` |
  | quarantine release, Console and tenant (`admin.py`, `held.py`) | `interactive` up to 10 released, else `bulk` |
  | the task's own retry after a lost parse try (`parse_and_extract.py`) | `interactive` |
  | the stuck sweep, `processing` retries and every `pending` past 30 min (`stuck_sweep.py`) | `interactive` |

  So one tenant's 500 files put 500 jobs ahead of every other tenant's
  next order: Section 5.1's burst row is not met (review H4).
- **"Interactive is always drained first" is not true today.**
  `celery_app.py` says so, but Celery's Redis transport takes turns
  between the queues a worker reads (kombu 5.6.2, `queue_order_strategy =
  'round_robin'`), so `bulk` gets equal turns. A doc/code contradiction
  for the Stage 5 list; under the dispatcher it stops mattering for
  documents (below), and the docstring is corrected in 3d.
- **Three waits exist, two of them as stopgaps.** A Storage outage (3b)
  and a parse service that can't be reached (3c) leave the document in
  `processing` with its attempt given back and its claim re-stamped; the
  sweep takes it over after 30 minutes, again and again, because 0027 has
  no `processing -> pending`. The code says 3d adds the real wait. A
  model-provider error fails the document at once (DOC-008).
- **The staging worker runs one document at a time:** Celery's default
  concurrency is the CPU count, and the worker's Fly machine is
  shared-cpu-1x, 1 GB, with a 700 MiB per-process cap (3a).

**1. `pending` splits into waiting and dispatched** (interplay point 1,
agreed). A new column `documents.dispatched_at`: NULL is *waiting* (not
yet sent), set is *dispatched* (sent to the queue, not yet claimed). The
claim clears nothing; `processing` is already distinct. Every path that
today sends a document to the queue instead leaves it `pending` with
`dispatched_at` NULL, and only the dispatcher sends. A document is
in flight when it is dispatched or `processing`.

**2. The dispatcher.**
- **Where it runs:** in the worker, as a Celery task `docflow.dispatch`,
  never in the API. Choosing between tenants means reading every tenant's
  waiting counts, and a cross-tenant read from the API would be an
  admin-style path outside the Console (7.15.1).
- **How it reads across tenants** (*proposed*, Q8): two SECURITY DEFINER
  functions, so the worker's session gains no new read rights.
  `dispatch_candidates()` returns, per tenant, the in-flight count and the
  oldest few waiting documents (ids, lane, arrival time, `retry_at`):
  ids, counts and times only, never a document's content.
  `mark_dispatched(document_id)` sets `dispatched_at` by compare-and-set
  (only if still waiting) and returns whether it did. EXECUTE revoked from
  PUBLIC **and from `anon` and `authenticated`** (D-175), granted to
  `docflow_app` now and moved to `docflow_worker` in 3e.
- **One pass:**
  1. Take a transaction-level advisory lock; if another pass holds it,
     return at once (passes never overlap).
  2. Free slots = the in-flight target minus what is in flight (Q2).
  3. Fill them one at a time. The next slot goes to the tenant with the
     **fewest documents in flight**; a tie goes to the tenant whose oldest
     waiting document arrived first. Within a tenant: interactive lane
     before bulk (Q3), then oldest first. A document whose `retry_at` is in
     the future is skipped.
  4. **A tenant at the per-tenant cap (Q1) is skipped while any other
     tenant has a document waiting**; when no one else is waiting it may
     use the free slots, so a lone backfill never leaves workers idle.
  5. `mark_dispatched`, commit, then send each one to `interactive` (after
     the commit, as every enqueue today). If a send fails, `dispatched_at`
     is cleared again so it isn't counted in flight.
  6. Record the pass's time (the heartbeat, below), holding or not.
- **What starts a pass** (Q4, *proposed*: all three):
  - a nudge after every intake commit (upload, email, release, test-batch
    run): the API sends `docflow.dispatch` instead of the document task;
  - inline at the end of every document task, in the worker process that
    has just freed a slot;
  - celery beat every 30 s (`DISPATCH_INTERVAL_SECONDS`), the backstop.
  No extra process is needed: the queue never holds more than the target,
  so a nudge waits at most behind that many documents, and when every slot
  is busy the next completion dispatches anyway.
- **What it gives:** a tenant arriving during another tenant's 500-file
  upload gets the very next free slot; it never waits behind the backlog,
  only behind documents already being read (at most one document's time
  per slot). Measured, not assumed: the Phase 6 load test records it, and
  3d's own tests show it (item 8).

**3. The stuck sweep** (interplay points 1-2, agreed, made concrete):
- It stops sending `pending` documents to the queue.
- A document *dispatched* and unclaimed past `STUCK_PROCESSING_TIMEOUT_MIN`
  is a lost job: the sweep clears `dispatched_at` (back to waiting, the
  dispatcher re-sends it within the cap) and raises today's
  `document_stuck` warning, one per tenant per day.
- A document *waiting* never alerts on its own. Instead, if the
  dispatcher's heartbeat is older than `DISPATCHER_STALE_MIN` (*proposed*:
  10 minutes) while anything is waiting, the sweep raises one high-severity
  tenant-less alert, new type `dispatcher_stopped`, deduped per hour. The
  heartbeat is written on every pass, including passes that hold because
  the provider is down, so holding never looks like stopping.
- `processing` handling is unchanged (retry, DOC-022, 3a's timeout rule).

**4. A real wait: `processing -> pending`** (agreed for the provider;
*proposed* for Storage and the parse service too, Q6). A new transition in
0027's state machine. On a waiting error the task moves the document back
to `pending`, clears `dispatched_at`, gives back the attempt (so waiting
never uses up `MAX_PROCESSING_ATTEMPTS` or looks like a timeout), and sets:
- `waiting_since` (the first wait; kept until the document leaves
  `pending`),
- `wait_cause`: `model_provider`, `storage` or `parse_service`,
- `retry_at`: when the dispatcher may send it again,
- `last_wait_error`: a status code or reason name only (Section 7.10).

These generalise the three provider columns proposed earlier (one set of
columns for every wait, not one per cause). Per cause:

| Cause | Backoff | Maximum wait | Customer sees | Alert |
|---|---|---|---|---|
| `model_provider` | 1, 2, 4, 8, 15, then every 15 min; a 429 waits at least its `Retry-After` | 6 h, then DOC-024 | **Delayed** (DOC-023) | `model_api_failure` / `model_api_recovered` |
| `storage` | every 5 min | none (as today) | as today | `storage_unavailable`, hourly (as today) |
| `parse_service` | every 2 min | none (as today) | as today | `parse_service_unavailable`, hourly (as today) |

The provider row is the earlier proposal unchanged; Storage and the parse
service keep today's behaviour and alerts and only stop being re-claimed
every 30 minutes. The 3b/3c helper `release_after_storage_outage` goes.

**5. Model provider down.** As proposed above (points 1-7 of "documents
wait instead of failing"): what waits and what fails, the global
`model_provider_state` table, down after 3 waiting-class failures in 5
minutes, one `model_api_failure` alert per outage and a
`model_api_recovered` notice, the dispatcher holding everything and
probing with the oldest waiting document every 2 minutes. One addition
(*proposed*): **on recovery every provider-waiting document's `retry_at`
is cleared**, so the backlog goes out at once through the normal
turn-taking instead of each waiting out its own backoff. All of these
still wait for the founder's answer (Q7).

**6. What people see.**
- Tenant surface: a provider-waiting document shows **Delayed** with
  DOC-023 (one badge, reusing `PILL`). A document waiting its turn shows
  as `pending` does today.
- Console: the document list shows the same; the health strip's existing
  queue numbers stay, and "oldest waiting document" becomes "oldest
  waiting (not yet dispatched)" so a backfill reads as a backlog, not as
  stuck.

**7. Migration `0035`** (backup first, deletes nothing, changes no
existing value):
- `documents`: `dispatched_at`, `dispatch_lane` (`interactive` or `bulk`;
  existing rows `interactive`), `waiting_since`, `wait_cause`, `retry_at`,
  `last_wait_error`; a partial index on waiting documents;
- `processing -> pending` added to `document_status_transition_allowed`
  (and to `document_status.ALLOWED`, held equal by the existing test);
- `model_provider_state`, a **genuinely global table** (Section 10), RLS
  on, readable and writable only through the worker's functions;
- `dispatcher_state` (one row: the heartbeat), global the same way;
- `dispatch_candidates()`, `mark_dispatched()`, the provider-state
  functions, with the grants in item 2;
- insert policies for the tenant-less alerts `model_api_failure`,
  `model_api_recovered` and `dispatcher_stopped`: flag policies as
  `rollup_raise` (0017) and `intake_refusal` (0029) are, moved to
  `docflow_worker` in 3e.
- Existing `pending` documents start as waiting (`dispatched_at` NULL), so
  the dispatcher sends them; any already on the queue are claimed once
  (the claim makes a duplicate a no-op).

**Cutover order** (RUNBOOK, written with the build): `0035` applied, then
the worker (with the dispatcher and beat entry), then the API (which stops
sending the document task). An API without a dispatcher behind it would
leave every new document waiting, and the `dispatcher_stopped` alert says
so within 10 minutes.

**8. Tests** (real systems where the thing under test is real; RUNBOOK 1.6
evidence in each):
- **Fairness, real database and real queue** (CI worker job: Postgres and
  Redis services; a real Celery worker): tenant A uploads 500, tenant B 1
  during it; B's document is claimed before A's next one. A lone tenant
  fills every slot. The cap holds while others wait. A tenant's
  interactive upload goes ahead of its own bulk lane. The model call is
  replaced at the Anthropic boundary here: what is proven is the
  dispatcher, the queue and the claim, all real.
- **Passes never double-dispatch:** 20 concurrent passes on the real
  database; every document dispatched exactly once.
- **The sweep:** a dispatched-unclaimed document past the timeout goes back
  to waiting with one alert; a waiting document never alerts; a stale
  heartbeat raises `dispatcher_stopped`, a holding dispatcher doesn't.
- **Provider errors, through the real SDK:** a local HTTP server standing in
  for the provider answers 529, 503, 500, 429 with `Retry-After`, 401, 400,
  and drops the connection mid-stream; each lands in its group (wait or
  fail) by status code. Then: down after 3 in 5 minutes, one alert, holding
  and probing, recovery notice, the backlog released, DOC-024 at 6 hours
  (time moved in the database, not slept).
- **Waiting never costs a try:** a document that waits five times, then
  succeeds, has `processing_attempts` 1 and no `timeout_attempts`.
- **Staging, at the checkpoint:** the 500 + 1 run end to end against staging
  with the real parse service and the real model on a few documents (the
  budget goes to the founder first), plus the Phase 6 load test later.
  *(Moved by the founder, 2026-10-01: to the first worker deploy after 3e,
  with G; see "3d on staging".)*

**Not in 3d:** `pg_trgm` matching (Stage 4); the cost measurement of the
costliest documents (before the first pilot, its own budget); F-1's logins
(3e); the worker's machine count and concurrency (Phase 6, with the load
test).

**Questions for the founder:**
- **Q1. The per-tenant cap** (`TENANT_IN_FLIGHT_CAP`). *Recommend 2:* with
  the "may borrow when no one else is waiting" rule in item 2, the cap only
  decides how much of the worker one tenant keeps while others wait; 2
  lets a backfill keep moving without holding more than two slots against
  a newcomer.
- **Q2. A global in-flight target as well** (an addition to the agreed
  design). *Recommend yes:* `DISPATCH_IN_FLIGHT_TARGET`, equal to the
  worker's document slots (Celery concurrency, set explicitly from the
  same setting so the two can't drift; staging 1). It keeps the queue as
  short as the worker, so every choice is made by the dispatcher with full
  knowledge, not by Redis's first-in-first-out. Cost: when workers are
  added, the setting is raised with them (a RUNBOOK step). Without it, the
  queue may hold cap x active tenants, and a newcomer waits behind all of
  it.
- **Q3. Does an interactive upload skip ahead of its own tenant's
  backfill?** *Recommend yes,* by lane, decided at intake: a web upload of
  up to 10 files (the page sends how many the person chose), every email,
  a release of up to 10 (today's rule) and a test-batch run are
  interactive; a larger upload or release is bulk. The lane only orders a
  tenant's own documents, so a client that lies about its batch size can
  only reorder its own queue. The alternative is first-in-first-out within
  a tenant, which makes an urgent single order wait behind its own
  backfill for hours.
- **Q4. What starts the dispatcher.** *Recommend all three* (item 2): the
  intake nudge, each completion, beat every 30 s.
- **Q5. `DISPATCHER_STALE_MIN` = 10 minutes** and the new tenant-less
  `dispatcher_stopped` alert (item 3)?
- **Q6. Storage and parse-service waits move onto the same
  `processing -> pending` wait** (item 4), with today's alerts and display
  and no maximum? *Recommend yes:* it ends the 30-minute re-claim loop the
  3b/3c code calls a stopgap.
- **Q7. The provider proposals** (points 1-7 above, plus "clear `retry_at`
  on recovery"): approve as written, or change any. Includes 401/403 still
  failing at once but raising the provider alert as `our_credentials`, the
  backoff, 6 hours, DOC-023 and DOC-024's wording, the thresholds (3 in 5
  minutes, a probe every 2 minutes).
- **Q8. The dispatcher's database access through two SECURITY DEFINER
  functions** (item 2) rather than a flag policy that lets the worker read
  every tenant's documents? *Recommend the functions:* they return ids,
  counts and times only, and 3e moves one grant.

**3d -- APPROVED WITH CHANGES (founder, 2026-10-01); building.** The
founder's answers, then the changes, then what the build settled.

*Answers.* Q1 cap 2: approved. Q2 global target = worker slots from one
setting: approved, with a RUNBOOK step for raising it with workers. Q3 lanes
by intake: approved. Q4 all three triggers: approved. Q5 10 minutes and
`dispatcher_stopped`: approved, with gap 1 below. Q6: approved. Only "never
got in" (`ParseUnavailable`) is a wait; "got in, no answer" (`ParseLost`)
still uses up tries per 3c, so a poisoned file can't wait forever. Q8:
approved; both functions (and every other function 0035 adds) set a fixed
`search_path` and name everything fully, with a test. Q7: approved except
our-side errors (change A).

*A. Our own errors never fail a customer's document* (founder). Checked
against Anthropic's error reference and rate-limits page, read 2026-10-01,
and the installed SDK (anthropic 1.6.0):

| The provider answers | Group | Cause |
|---|---|---|
| 500-599 (incl. 504 `timeout_error`), 529 | wait | `5xx` / `overloaded` |
| 429 with `retry-after` | wait, at least `retry-after` | `rate_limited` |
| connect, read or silent-stream timeout; a connection dropped mid-stream | wait | `network` |
| 401, 402 (`billing_error`: the documented code for credit and billing), 403, 404 (model retired or renamed) | wait | `our_configuration` |
| 400 whose message begins "You have reached your specified" (our own organisation or workspace spend limit; documented as a 400) | wait | `our_configuration` |
| 429 with `error.details.error_code = enforced_spend_limit_reached` (the tier's monthly spend cap; no `retry-after`) | wait | `our_configuration` |
| every other 400, 413 and other 4xx; a malformed answer (DOC-009); the read deadline (DOC-020) | fail at once, as today | -- |

- `our_configuration` marks the provider down at its **first** occurrence (no
  3-in-5 threshold) and raises `model_api_failure` at once, severity high.
  Dispatch is held and probed, with the same 6-hour maximum and the same
  DOC-023/DOC-024 for the customer.
- **Found while checking (no decision needed, recorded):** an `error` event
  in the middle of a streamed answer reaches the SDK after HTTP 200 and is
  raised with `status_code` 200 (`anthropic/_streaming.py`). Classified by
  status alone, an overloaded mid-stream answer would fail at once. So a
  status of 200 is classified by the error's `type`, mapped to the status
  the reference gives that type (`overloaded_error` -> 529, `api_error` ->
  500, ...). An unknown type mid-stream counts as the provider's side
  (wait), since the request itself was already accepted.
- Each row is tested through the local stand-in HTTP server and the real SDK.

*B. Gap 1 -- worker death is visible from outside the worker* (founder).
- `GET /healthz` (no auth) adds `dispatcher: {heartbeat_age_seconds,
  stale}`: the age only, read through a SECURITY DEFINER function that
  returns nothing else.
- It stays HTTP 200 either way. It is the API's liveness answer, and a 503
  would get a healthy API restarted if a platform check is ever pointed at
  it. The external monitor matches `"stale":false` *(corrected 2026-10-01:
  compact JSON, no space; see "3d on staging")*.
- The Console health strip shows the heartbeat age, red past
  `DISPATCHER_STALE_MIN`. RUNBOOK: an external uptime monitor checks it from
  Phase 6 *(moved by the founder, 2026-10-01: from the first worker
  deploy)*. Test: a stale heartbeat makes health show stale.

*C. Gap 2 -- where beat runs* (founder, with two conditions).
- **Beat is its own process, never embedded with `-B`.** Locally it stays
  the README's second terminal. The stuck sweep already runs on beat, every
  5 minutes; the dispatcher's 30-second backstop joins it.
- **On Fly**, the worker app gets two process groups, `worker` and `beat`,
  with `beat` scaled to exactly 1. This ends 3c's "no beat on Fly"
  (founder's choice). RUNBOOK: run only one stack, local or Fly, against
  staging at a time.
- **Condition 1: every beat task is safe if two beats fire it.** No new
  guard is needed:

  | Task | If two copies run at once |
  |---|---|
  | `run_scheduled_jobs` (5 min) | Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so each job runs once. |
  | `run_daily_rollup` (03:15 UTC) | Each tenant-day is an upsert (`ON CONFLICT (tenant_id, day) DO UPDATE`) of the same numbers. Only `rollup_runs` gets two rows, which is harmless because the dashboard reads the latest. `rollup_stale` is deduped. |
  | `run_lifecycle_sweep` (5 min) | Suspension is a compare-and-set claim. The Stripe cancel holds the tenant row lock across its calls, so the second copy waits and then sees nothing owed. The ready-to-delete and quarantine alerts are deduped. |
  | `sweep_stuck_documents` (5 min) | Every status change is a compare-and-set. Lost-call outcomes are `ON CONFLICT DO NOTHING`, and alerts are deduped. Returning a lost job to waiting is a compare-and-set on `dispatched_at`. |
  | `dispatch` (30 s, new) | A transaction-level advisory lock: the second pass returns at once. |

- **Condition 2: a deploy never runs two beats.** Fly's canary and
  blue-green strategies start a new machine beside the old one; rolling and
  immediate update machines in place. `apps/worker/fly.toml` pins
  `[deploy] strategy = "rolling"`, and RUNBOOK forbids `--strategy
  canary|bluegreen` and scaling `beat` above 1 for this app. Condition 1 is
  the backstop if it happens anyway.

*D. Gap 3 -- the cap rule counts only documents that can go now* (founder).
"Another tenant has a document waiting" means one whose `retry_at` is NULL
or past. Test: a capped tenant uses idle slots while another tenant's only
waiting document has a future `retry_at`.

*E. `routing_model_failure`* (founder: "alert, don't hold", with two
conditions).
- When the routing call (the cheaper model) fails with an `our_configuration`
  answer, a tenant-less **warning** alert is raised, once per UTC day.
  Dispatch is not held and extraction goes ahead without examples.
- Payload: the cause, the first failure's time, and
  `documents_without_examples`, which counts every later failure that day.
  The count is kept on the open alert by a SECURITY DEFINER function, so the
  email carries the count at the time it was raised and the Console row
  shows the running count.
- **Condition 1:** the type is registered in `ALERT_TYPES`, and a test
  raises it end to end through the real code path. One test also raises
  **every** registered type, and a guard fails the build if any
  `alert_type="..."` in the code is not registered. That test fails on
  `rollup_stale` today, so 3d also registers it: **review finding M6 is
  fixed here**.
- **Condition 2:** the count above.
- Not folded in (founder): fixing a retired model still needs a code deploy
  until M10 (model IDs from settings) is done. That is a separate decision.

*Settled while building (inside the approved design; listed so nothing is a
surprise):*
- `waiting_since` is kept through each wait-and-retry cycle, so the 6-hour
  maximum counts from the first wait. It restarts when the cause changes and
  is cleared when the document leaves for review or failure. The backoff
  step comes from the time since `waiting_since`, so no counter column is
  needed.
- While the provider is down, waiting documents aren't claimed, so the stuck
  sweep fails the ones past 6 hours with DOC-024 (`pending -> failed`, an
  existing transition).
- A `processing` document the sweep retries is still sent straight to the
  queue, as today: it is already counted in flight, so the cap and target
  hold. The same goes for 3c's immediate retry of a lost parse. Only
  `pending` documents go through the dispatcher.
- The lane comes from a `batch_size` field that the upload page sends with
  each file: 10 or fewer files is interactive, more is bulk. Email,
  test-batch runs and releases of 10 or fewer are interactive.
- The tenant-less alerts (`model_api_failure`, `model_api_recovered`,
  `dispatcher_stopped`, `routing_model_failure`) are written under one flag
  policy, `app.dispatcher`, through `dispatcher_session()`: insert only,
  those four types, `tenant_id` NULL. 3e moves this to the worker's login.
- `worker_concurrency` is set from `DISPATCH_IN_FLIGHT_TARGET`, so the two
  can't drift. That holds for one worker machine. The machine count is
  Phase 6.

**3d build -- BUILT 2026-10-01 (D-184; migration `0035` awaiting the
founder's backup and staging apply).** Built as approved above, with three
things found while building. Each is raised with the founder; none is a new
feature:
1. **The tie-break, fixed.** The agreed rule broke ties in favour of the
   tenant whose oldest ready document arrived first. At one slot (staging),
   whenever the slot frees both tenants have 0 in flight, so that rule gave
   every slot to the backfill's older documents, and a newcomer waited
   behind all of them: the opposite of "gets the very next free slot". Ties
   now go to the tenant **served least recently**, then the oldest.
   `0035`'s `dispatch_candidates` returns each tenant's last dispatch, with
   an index. A regression test fails under the old rule.
2. **"Delayed" is shown only while the provider is marked down.** DOC-023's
   approved wording says "DocFlow has been alerted". `model_api_failure`
   exists only once the provider is marked down (3 failures in 5 minutes, or
   at once for our own configuration). A document showing "Delayed" during
   the first minute of an isolated overload would make a false promise, and
   D-145 (enforced by `test_alert_promises.py`) forbids that. Before that
   point it reads "Waiting to be read". *Founder: confirm, or reword DOC-023
   instead.*
3. **The worker runs target + 1 processes.** With exactly the target (1 on
   staging), a 15-minute order would block the dispatch pass and the
   sweeps, and `/healthz` would read "stale" because one order was slow. The
   extra process is one the dispatcher never fills, since it never puts more
   than the target in flight.

Also settled: a claim refuses a `pending` document whose `retry_at` is still
ahead, so a stray duplicate job can't retry it early. The dispatcher clears
`retry_at` when it sends a document, including a probe.

*What changed where:* `0035` (columns, transition, the global tables, 11
functions, the flag policies); `docflow_core`: `dispatch.py`,
`model_provider.py`, `provider_errors.py`, `document_status.to_waiting`,
the stuck sweep, `founder_alerts` (4 types + `rollup_stale`), catalog
DOC-023/DOC-024; the worker task (waits, provider state, routing alert,
pass at the end), `docflow.dispatch` and its beat entry, `fly.toml` (beat
group, rolling); the API (nudge in place of every send, `batch_size` lane,
`/healthz`, the health strip, `delayed` on the review list and detail); the
web (Delayed badge and banner, the strip's heartbeat and provider). RUNBOOK
9 (cutover order, adding capacity, beat, the uptime monitor, the alerts,
the constants) and 7.3/8.4 updated. `.env.example`:
`DISPATCH_IN_FLIGHT_TARGET`.

*Tests:* core `test_provider_errors.py` (every row of change A through the
real SDK and a local stand-in server, a mid-stream error event, a dropped
stream, nothing listening), `test_dispatch_rules.py`,
`test_alert_types_registered.py`; worker `test_dispatch_db.py` (20 tests on
the real database: candidates and gap 3, newcomer-first, lanes, target, 20
concurrent passes, a failed send, the heartbeat, `dispatcher_stopped`,
provider down/probe/recover, our configuration, DOC-024 at 6 hours, five
waits then success with one try, the backoff claim, `routing_model_failure`
with its count, every alert type, M6 through the rollup's own session, the
functions' `search_path` and grants, the flag's limits);
`test_dispatch_real_worker.py` (a real Celery worker and queue, Linux/CI);
the task's branches in `test_parse_and_extract.py`; `/healthz` in
`test_healthz_dispatcher.py`. The worker suite stubs the dispatcher and
provider state by default (`conftest.py`), so no test can mark the shared
database's provider down by accident.

*CI:* the first run (`b7df093`) failed in test code only: ruff on an API test
string the shell had mangled; 15 worker DB tests passing Python lists to
`ANY()` (lists bind as jsonb in this codebase, so arrays are built with
`string_to_array`, as elsewhere); the real-worker test missing
`content_sha256`. Fixed in `f96db83`, which also deletes test alerts before
their emails (foreign key) and adds `docflow.dispatch` to the
fresh-interpreter registration probe. **CI GREEN on `f96db83`** (run
36947657091): core 747, worker 161 (the real-worker fairness test included,
on Linux), api 585, parse unit 128, parse HTTP 56, all 0 failed / 0 skipped;
web, web-live and the parse self-tests as before (A-net IPv6 NOT-RUN on CI,
as in 3c). **Next:** the founder backs up and applies `0035` on staging, then
the staging suites (RUNBOOK 1.4), the PR, and the 500 + 1 staging run (its
budget to the founder first).

**3d on staging, and the founder's decisions on the build (2026-10-01).**
*`0035` on staging:* applied by the founder after `backup_0035` (documents
105, founder_alerts 15, email_outbox 73; the same before and after). I
checked it: the six `documents` columns, the 11 functions (security
definer, `search_path=""`), both tables (RLS on), `processing -> pending`
allowed, both flag policies, `backup_0035`'s three tables (RLS on), and the
seeded provider row reading `up`.

*The three findings:*
1. Tie-break by least recently served: **approved.**
2. "Delayed" only while the provider is marked down: **approved, DOC-023
   unchanged**, with "confirm `our_configuration` holds show Delayed at
   once". Confirmed: the worker puts the order in the `model_provider` wait
   first, then `record_failure` marks the provider down at the first
   `our_configuration` failure. Two new tests: one real 401 through the
   worker task (`test_dispatch_db.py`: the order waits on the provider, the
   provider is down, one high alert), and that state on the review queue and
   detail as "Delayed" with DOC-023, then not delayed after recovery
   (`apps/api/tests/test_review_api.py`; nothing tested the screen side
   before).
3. The extra worker process: **approved if it reads only a dedicated
   dispatch queue (tested never to claim a document) and its memory on the
   1 GB machine is measured with headroom.** Built:
   `python -m app.run_workers` runs two Celery workers that live and die
   together. `documents` has `DISPATCH_IN_FLIGHT_TARGET` processes on
   `interactive,bulk`. `dispatch` has one process on the `dispatch` queue
   only (`DISPATCH_QUEUE`). If either exits, the launcher stops the other
   and exits non-zero; `fly.toml` sets `[[restart]] on-failure`. Beat, the
   API nudge and email intake send `docflow.dispatch` there, and nothing
   else goes there (a source scan of every `send_task` checks both
   directions). **A guard I added to meet the condition:** a document task
   delivered on the dispatch queue claims nothing and is put on
   `interactive`. Tests: `test_run_workers.py` (real child processes: one
   exits -> the other stopped, exit 1; SIGINT -> both stopped, exit 0, Linux),
   `test_celery_app.py` (the command lines, `fly.toml`, the Dockerfile, the
   source scan), the guard as a unit test, and
   `test_dispatch_real_worker.py`'s dispatch-worker test. That test runs the
   real prefork worker from `run_workers`' own command line on the real
   dispatch queue: its pass sends the order on and the order stays
   unclaimed, and a document task put straight on the dispatch queue is
   handed back, unclaimed (Linux, CI).

*Founder, later the same day: where the 500 + 1 run and the memory
measurement happen.* The worker and API stay off Fly until 3e (their own
database logins). So **the 500 + 1 staging run and the worker memory
measurement move to the first worker deploy after 3e, alongside G**, and
gate that deploy, not the 3d merge. **The 3d merge gate:** CI green
(including the real-Celery fairness test and the dispatch-worker test) and
the staging suites green. At that deploy (RUNBOOK 9.2): idle and under real
documents, **`.doc` and scanned PDFs included**, with both Celery main
processes, the dispatch process, and the document process at its peak.
Expect 700 MiB per process not to fit with headroom on 1 GB (a document
process at the limit plus three at roughly 100 MiB each is about the whole
machine). The founder gets the numbers with two options: a lower
per-process limit, or a 2 GB worker machine and its monthly cost. **Neither
changes until the founder chooses.**

*Restart policy, as asked:* `on-failure`, 10 retries; Fly counts them within
a 5-minute window and then leaves the machine stopped (Fly's restart-policy
docs, read 2026-10-01). A worker that is **down** (can't start, or out of
retries) runs no passes. The heartbeat ages, and `/healthz` reads stale
after 10 minutes. `/healthz` is the only place this shows:
`dispatcher_stopped` is raised by the stuck sweep, on the same machine. A
worker that **restarts slowly** (up a few minutes, then a worker exits,
again and again) runs passes between exits. Its heartbeat stays fresh, so
`/healthz` does not show it. Today that is visible only in `fly logs`, Fly's
machine events, and indirectly as `document_stuck` alerts. **Founder: not
in 3d. Build it in 3e's migration:** the launcher records each start,
`/healthz` shows the starts in the last hour, and an alert fires past a
threshold (proposed: 3 starts in 60 minutes; see "3e -- F-1" below). It
gates the first worker deploy, with G, the 500 + 1 run and the memory
measurement. Until 3e the worker runs locally, not on Fly, so there is no
Fly restart loop to miss.

<!-- docs/BUILD-STATUS.md keeps the block that stood here: "Gates for the first worker deploy (after 3e)" -->

**Found while writing this: the monitor's keyword was wrong.** RUNBOOK 9.4,
`main.py`'s docstring and the 3d records gave it as `"stale": false`, with a
space. The API sends compact JSON (`"stale":false`; Starlette's
`separators=(",", ":")`), so a monitor set up from the RUNBOOK would never
have matched, and would have alerted for ever. Fixed in all three places.
`test_healthz_dispatcher.py` now pins the bytes: present only when fresh,
absent when stale or unreadable. It also checks that RUNBOOK 9.4 gives
exactly them.

*Staging suites (RUNBOOK 1.4, one at a time), 2026-10-01:*
- **core:** `746 passed, 1 skipped in 38.77s` (before the dispatch-queue
  change; core's code changes after it are the `DISPATCH_QUEUE` constant and
  the email nudge's queue, both covered by the API and worker runs below).
- **worker, before the dispatch-queue change:** `157 passed, 4 skipped in
  1191.64s`. That is 161 tests, CI's count on `f96db83`.
- **worker, final code:** `161 passed, 6 skipped in 1243.29s (0:20:43)`.
  That is 167 tests: the 161 plus 6 new ones (the 401 test, the guard unit
  test, the send_task scan, two launcher tests, and the real dispatch-worker
  test). The 6 skipped are all Linux-only (two real-worker tests, one
  launcher signal test, two prefork time-limit tests, the real parse
  container): CI runs them.
- **API, final code:** `585 passed, 1 skipped, 3 deselected, 657 warnings in
  2572.03s (0:42:52)`. 586 run: CI's 585 plus the new "Delayed" test. The
  skip is `test_parse_token_boundary.py` (needs the real parse service with
  a token; CI's API job); the 3 deselected are the `live_api` tests.
  **Duration noted:** 42:52, against about 34 minutes on earlier runs. The
  run was checked mid-way: output still being written, the parse service
  answering 200, the same two pytest processes since start. So it was
  slower, not stalled. Probably staging; noted so a trend can be seen.
- **The keyword test**, added after the API run had started, run on its own:
  `test_healthz_dispatcher.py` `5 passed in 0.60s`.

*Founder's pre-merge questions (2026-10-01), answered:*
1. **API counts reconcile.**
   - Staging: 585 passed + 1 skipped = 586 run.
   - The 3 deselected are the `live_api` tests: the golden fixture, the
     golden fixture with examples, and the contamination check. They are
     deselected everywhere by `-m "not live_api"` (`apps/api/pyproject.toml`)
     and run only at checkpoints.
   - CI's 587 = the 585 + the 1 staging skipped
     (`test_parse_token_boundary.py`, which passes in CI with the real parse
     service and its token) + the keyword test.
   - The keyword test's file has 5 tests, but **only 1 is new**; the other
     4 were already in the 585.
2. **"Every beat task is safe if fired twice":** the five beat tasks, each
   with its guard, its code and its test, are now a table in RUNBOOK 9.3.
   Three have a test that fires them twice: the rollup, the lifecycle
   claim, and 20 concurrent dispatch passes. **Two rest on the guard
   alone:** the scheduled-jobs sweep (`SKIP LOCKED`) and the stuck sweep
   (compare-and-set, tested in general). Proposed: a two-at-once test for
   each. *Not built; founder's call whether before merge or in Stage 5.*
   **Decided at the merge: built in 3e, gating the first worker deploy.**
3. **`backup_0035`:** the same rule as `backup_0034` (RUNBOOK 1.3).
   - It is dropped by the founder after 3d has run cleanly on staging for 3
     days, counted from the merge.
   - Before that, Claude reports the check: staging suites green on `main`;
     no `document_failed`, `document_stuck`, `dispatcher_stopped` or
     `model_api_failure` alert since the merge.
   - The date is recorded in RUNBOOK 1.3 and here.
4. **Stage 5's open findings:** now Medium 3 (M7, M10, M11). M6 is fixed in
   3d (the Stage 5 row above).

*CI on `2d18b0f`* (run 36956558799): **worker failed at mypy**, so pytest
never ran: `supervise()` took `dict[str, Sequence[str]]`, and a
`dict[str, list[str]]` doesn't match (dict is invariant). I had run mypy
on core and the API but not on the worker app. Fixed with `Mapping`.
Every other job was green on that run: core 747 tests, 0 skipped; api 587,
0 skipped; parse unit 128 and HTTP 56; web 73; web-live 3.

**CI GREEN on `d16e4e0`** (run 36957133781), every job, from each job's
own counts notice: core `747 tests, 0 failed, 0 skipped, 0 unapproved`;
api `587 tests, 0 failed, 0 skipped, 0 unapproved`; **worker `167 tests, 0
failed, 0 skipped, 0 unapproved`**; parse unit 128 and HTTP 56 (both 0
failed, 0 skipped); web 73 passed; web-live 3 passed. The parse
self-tests are as before (canary 5, S 5, B 17, A with only IPv6 NOT-RUN).
`.github/approved-skips.txt` is empty and a skip fails the job, so worker
167 / 0 skipped means the Linux-only tests ran and passed. That includes
the real-Celery fairness test and the dispatch-worker test. **The 3d merge
gate is met:** CI green including those two, and the staging suites green.
Next: the founder opens the PR; its own CI run must pass too.

