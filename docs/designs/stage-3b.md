# Stage 3b: Supabase Storage (H6)

Moved here word for word from `docs/BUILD-STATUS.md` on 2026-10-05 (the founder's context
housekeeping, D-191). Nothing below the marker was edited: it is the text as it stood under
"Stage 3 -- agreed with the founder before building", 3b merged 2026-09-30 (PR #31, D-182). It keeps its
original wording, including statuses that were true when each part was written.

Other files cite these sections as BUILD-STATUS "<heading>". Each cited heading is still in
`docs/BUILD-STATUS.md`, as a one-line stub pointing here. "Above" and "below" in this text
refer to the order the blocks had there: 3a, 3b, 3c, 3d, card billing, 3e.

<!-- moved text starts on the next line -->
**3b -- H6, Supabase Storage.** Agreed:
- **fixed per-document keys for derived files** (preview, extracted text), so
  a repeated write overwrites instead of leaving an orphan (from 3a's
  side-effects review);
- a private bucket behind the existing `save_file` / `read_file` interface,
  keys prefixed `tenants/{id}/` and the prefix enforced on read as well as
  write;
- staging's files **copied** into the bucket, the local copies kept until the
  copy is verified.

**Downloads use our own signed links, and the API streams the file from
Storage** (founder). This **settles the H6 / D-170 note**: the expiry is
minted and verified by the API on one clock, so there is no foreign clock to
tolerate, and no customer ever sees a Supabase URL or the storage host.
Storage connects through Supavisor with a pool of its own (see 3e).

**3b detailed design -- Q1-Q5 ANSWERED 2026-09-30; awaiting the founder's
final sign-off. Nothing is built until then.** The founder's answers are
recorded in each item as **Decided**.

*Where things stand today.* `docflow_core/storage.py` writes to a folder on
the machine running the code (`storage/`, 2,973 files and 3.3 MB on this
machine). It already builds every path on the server (`tenants/{id}/{area}/
{random}{ext}`, and `staging/{intake}/...` before a tenant exists), and it
checks the prefix on **write** only: `read_file(path)` takes no tenant, so
nothing stops code from reading another tenant's path. Our own signed links
and the API streaming the file are already in place (D-089), so nothing
changes for customers or the web app. The API and the worker call storage
from 11 places. On Fly the API and the worker run on different machines, so
a local folder can't work in production.

1. **How DocFlow talks to Storage: Supabase's S3-compatible endpoint, with
   an access key used only for Storage (Q1).**
   - Recommended: `boto3`, pinned, with a Storage S3 access key (Supabase
     dashboard -> Storage -> S3 access keys). That key reaches Storage and
     nothing else.
   - The alternative is the Storage REST API with
     `SUPABASE_SERVICE_ROLE_KEY`. That key also administers sign-in (it
     creates users and makes invite links) and bypasses RLS on the REST
     API. Today only the API holds it. This route would put it on the worker
     too, the process that opens hostile files, until 3c moves parsing out.
     With the S3 key, a worker compromise reaches files but not accounts.
   - Cost of the recommendation: one more dependency (`boto3`) and three
     more settings: `STORAGE_S3_ENDPOINT`, `STORAGE_S3_ACCESS_KEY_ID`,
     `STORAGE_S3_SECRET_ACCESS_KEY` (documented in `.env.example`; the
     founder creates the key). The 3c parse service still holds no storage
     key.
   - **Decided (Q1): the S3 access key.** Recorded caveat: Supabase S3 keys
     are project-wide, not per-bucket or per-tenant. A leaked key reaches
     every tenant's files, so tenant isolation for files rests entirely on
     item 3's checks in our own code, not on the key.
   - Every call: 5 s connect and 30 s read timeouts, and up to 3 tries with
     backoff. Only on requests that are safe to repeat: every write goes to a
     key nobody else writes, so a repeated PUT is the same PUT. The worst
     case, about 2 minutes, fits inside every 3a limit (the tightest is
     export at 5 minutes).
2. **One private bucket, `docflow-files`, created by migration `0033`
   (Q2).**
   - The bucket is private, with a 25 MB object limit (`MAX_FILE_SIZE_BYTES`)
     and no public URL.
   - `storage.objects` gets **no policies**. A customer's own sign-in token
     reaches no file directly; only DocFlow's S3 key does. A test signs in
     as a tenant user and asserts Storage refuses them.
   - Why a migration and not the dashboard: CI's local stack gets the same
     bucket from the same file. `0033` inserts one row into
     `storage.buckets` and touches no existing table, so **I propose no
     backup for it** (the rule is backup-first; this is the question).
   - **Decided (Q2): no backup for `0033`,** as a standing rule rather than
     a one-off: a migration that only creates Storage buckets and touches
     no existing table skips the backup. RUNBOOK 1 records the rule.
     Separately, staging has had no backups since 2026-09-30 (RUNBOOK 1.3),
     so a fresh full backup is taken before the 3b rollout starts (item
     11, step 1) as a restore point for the cutover.
   - CI: `supabase/config.toml` turns `[storage]` on (it is off today).
3. **The tenant prefix is checked on read as well as write (agreed; this is
   how).**
   - `read_file(tenant_id, path)` replaces `read_file(path)`, and every
     caller passes the tenant from its own session.
   - `read_staging_file(intake_id, path)` is the only way to read a
     `staging/` file.
   - Before any network call, a path must match
     `tenants/{that tenant}/{uploads|exports|onboarding|derived}/...`, with no
     `..`, no empty segment and no backslash. Otherwise the call raises.
   - Test: tenant A's session asks for a path under tenant B, and the call
     is refused with Storage never contacted. This joins the 7.5 isolation
     tests.
4. **Fixed keys for derived files (agreed; this is the layout).**
   - Preview: `tenants/{t}/derived/{document_id}/preview`.
   - Extracted text: `tenants/{t}/derived/{document_id}/extracted.txt`.
   - A retry overwrites the same key, so it leaves no orphan. The preview's
     media type is already stored on the document (`preview_media_type`) and
     is also set on the object. `derived` joins `STORAGE_AREAS`.
   - Originals and exports keep their random names. Each is written once,
     before its row exists, so a retry can't repeat it.
   - Existing rows keep their old paths. Nothing is renamed.
5. **The copy of staging's files into the bucket (agreed: copy, keep the
   local files, only files a row still references, report orphans). This is
   how:**
   - A script, `scripts/copy_storage_to_bucket.py`. It is a dry run unless
     given `--apply`, and running it twice is safe.
   - It collects every path a row references:
     - `documents`: `storage_path`, `preview_storage_path` and
       `extracted_text_path`;
     - `exports.storage_path`;
     - `onboarding_intake_files.storage_path`;
     - `catalog_imports.storage_path`.

     Soft-deleted rows are included, because their data is kept until a hard
     delete.
   - Each file is uploaded under **the same key**, so **no database row
     changes**.
   - Each copy is checked by reading it back from the bucket and comparing
     its SHA-256 with the local file (and with the row's own SHA-256 where
     the table has one).
   - Report: copied; already there and identical; referenced but missing
     locally (listed); and the orphan count (files no row references, left
     behind).
   - The local `storage/` folder is kept until the founder says otherwise.
     It is only on this machine.
6. **A Storage outage (Q3).**
   - **Upload from the web app:** the file wasn't saved, so the document
     doesn't exist. Proposed new catalog entry **DOC-025** "We couldn't save
     this file", audience both:
     - message: "DocFlow couldn't store your file just now, so it wasn't
       received and nothing was processed.";
     - action: "Upload it again in a few minutes. DocFlow has already been
       alerted.";
     - a founder alert, `storage_unavailable`, **at most once per hour for
       the whole platform** (Decided, Q3), not once per tenant: an outage
       hits every tenant at once, and per-tenant alerts would send N alerts
       an hour for one incident.
     - How, with no new migration (settled 2026-09-30): the open-alert
       dedupe index (`idx_founder_alerts_open_dedupe`, 0011) is unique
       across all tenants and applies regardless of RLS. Each tenant session
       raises the alert for its own tenant (the existing `tenant_raise`
       policy) with the key `storage_unavailable:<UTC hour>`, the hour taken
       from the database's clock (as `dedupe_per_utc_day` does, D-170 #7).
       The first tenant in the hour writes the row; the rest get the dedupe
       conflict and write nothing. Trade-off: the alert names the first
       tenant that hit it, not a count, because a tenant session can't update
       another tenant's row. Every failure is still logged with its tenant.
   - **Email intake:** answer the webhook with a **5xx, never a 403**
     (Postmark retries non-2xx inbound webhooks but stops on a 403), so
     Postmark sends the email again later, and raise the same alert.
     Nothing is lost. A test asserts the status is 5xx.
   - **Worker reading the original (Decided, Q3: an outage must not fail
     documents).** Today's sweep would: `MAX_PROCESSING_ATTEMPTS` is 3 and
     `STUCK_PROCESSING_TIMEOUT_MIN` is 30, so an outage longer than about
     90 minutes ends in DOC-022 "worker stopped". So in 3b a Storage read
     failure logs `storage_read_failed` and returns the document to
     `pending` **without using an attempt** (the attempt counter is put
     back in the same update). *As built: it stays `processing` with its
     attempt given back -- 0027 has no `processing -> pending`; see "3b
     build" below.* The pending sweep re-enqueues it after the
     timeout, a pending document is never failed for waiting, and the
     sweep's existing `document_stuck` alert (once per tenant per day)
     tells the founder how many are waiting. This is a narrow, Storage-only
     version of 3d's `processing -> pending` wait; 3d extends the same
     transition to provider outages. That read is outside every broad
     `except`, so it can't be relabelled DOC-005.
     - **Only an outage waits.** Connection errors, timeouts and 5xx from
       Storage return the document to `pending`. A **404 (object missing)
       is not an outage**: it is a data fault, and waiting would retry it
       forever. It fails the document loudly, with the same catalog code and
       founder alert as a hash mismatch (item 9).
     - Tests: an unreachable Storage leaves the document `pending` with its
       attempt count unchanged, three times in a row; a 404 fails it once,
       with the alert.
   - **Previews and extracted text:** best effort, unchanged. A failure
     never touches the document's status.
   - **Export and catalog import:** they fail with their existing codes and
     are retried by the user. (Q3 covers these too, if you want DOC-025's
     wording there.)
7. **Tenant hard delete: remove the files first, then the rows (Decided,
   Q4).**
   - Today the rows are deleted and committed, then the folder is removed
     with errors ignored. A failure leaves the customer's files behind with
     nothing recording it.
   - On Storage, removing a prefix means listing it and deleting in batches
     of 1,000, so a partial failure is more likely.
   - Order: delete every object under `tenants/{id}/` and check the listing
     is empty. **Only then** run the existing database transaction.
   - If the file removal fails, nothing in the database has changed: the
     tenant is still `pending_deletion` (the only state `delete_tenant`
     accepts, LIFE-006), the founder sees the error, and runs the delete
     again.
   - If the database step fails after the files are gone, running the delete
     again finishes it. The founder has already typed the name to confirm an
     irreversible delete, so files going first is the direction already
     chosen.
   - **No new `deleting` status** (considered and dropped 2026-09-30). By
     the time a delete can run, the intake gate already blocks email and
     uploads for a `pending_deletion` tenant. The one write still possible
     is an export, which 7.14 keeps available until deletion. So, after the
     database transaction commits, a **final sweep** lists the prefix again
     and removes anything written during the delete.
   - The deletion event records how many objects were removed, including
     the final sweep's count separately. A test writes an export object
     between the file step and the database step and asserts the final
     sweep removes it.
   - No new alert and no new migration.
8. **Tenant creation's staging copy uses Storage's server-side copy.** The
   rollback is unchanged: a failed creation deletes the copies, and a
   committed one deletes the staging originals.
9. **Check the original's hash on every read (Decided, Q5: yes).**
   `documents.content_sha256` already exists. The worker compares it with
   the bytes it reads before parsing. A mismatch fails the document loudly
   with a new catalog code (audience both, with a founder alert), rather
   than extracting the wrong file or only logging it. The same code covers
   an original that is missing from Storage (item 6). It costs one hash
   per read. It guards against a wrong or corrupted object, which is rare.
10. **Tests.**
    - Product code has one backend, Supabase Storage. The suites run
      against the real bucket: staging's when run from this machine, the
      local stack's in CI. Unit tests that shouldn't touch the network get
      an in-memory fake, defined in the test folders and never importable by
      product code.
    - New tests: the read-side prefix check (3); a tenant token refused by
      Storage (2); fixed keys overwritten by a retry, with one object left
      (4); the copy script's dry run, apply, verify and orphan report
      against a fake (5); DOC-025, its platform-wide alert and the 5xx to
      Postmark (6); a Storage read failure returning the document to
      `pending` without using an attempt (6); the hash mismatch failing
      loudly (9); files removed before rows in a hard delete, a failed
      removal leaving the database untouched, and the final sweep (7).
    - **The rollback-test fix carried into 3b (approved earlier):**
      `test_a_failed_tenant_creation_rolls_back_everything_including_the_file_move`
      asserts that no tenant named "Acme Test Rollback" exists and that its
      own intake is unlinked, instead of counting every tenant on staging.
      The file check moves from the local folder to the bucket.
11. **Rollout, in order.**
    1. The founder takes a fresh full backup of staging (RUNBOOK 1.1),
       since staging has none left (item 2).
    2. The founder creates the S3 access key and puts the four settings in
       the root `.env` (four as built; see "3b build" below).
    3. The founder applies `0033` on staging (it creates the bucket).
    4. I run the copy script as a dry run, then with `--apply`, and report
       the counts.
    5. The switch: the code that writes to Storage is deployed.
    6. **Delta copy:** I run the copy script with `--apply` again straight
       after the switch. Anything uploaded between step 4 and step 5 was
       written to the local folder by the old code; this second run copies
       it (running twice is safe, and it verifies by SHA-256). Its report
       must show zero "referenced but missing".
    7. The staging suites run.
    8. The walkthrough: upload, review, preview, export and download, a
       Console catalog import from an intake file, tenant creation from an
       intake, and a hard delete of a test tenant.
    9. Merge.

    From the switch onward, nothing reads the local folder.
12. **Cost.** Storage holds 3.3 MB today. At the Section 5.1 envelope
    (7.15.3 puts it at about 50,000 documents), with previews and exports,
    that is a few GB. It is covered by the plan's included storage and egress, so
    there is no new line item. I'll confirm against the Supabase pricing
    page when building.

**3b build -- IN PROGRESS 2026-09-30 (branch `phase55/stage3b-design`).**
The offline part is built and tested; what is left needs the founder's
machine (staging and the local `storage/` folder). Where the build differs
from the design above, or adds to it:

- **Four settings, not three:** Supabase's S3 endpoint needs the project's
  region, so `STORAGE_S3_REGION` joins the other three (`.env.example`).
- **Upload checksums off:** Supabase supports no S3 upload checksums and
  current boto3 sends one on every PUT, so the client sends them only when
  an operation requires one. **3 tries in all** (`total_max_attempts`; boto3's
  `max_attempts` counts retries, which would have been 4).
- **An outage keeps the document `processing`, not `pending`** (item 6).
  0027's state-machine trigger has no `processing -> pending`, and 3b adds no
  migration for it. Same guarantee: the attempt is given back and the claim
  re-stamped, so the stuck sweep retries every `STUCK_PROCESSING_TIMEOUT_MIN`
  and `decide()` never reaches the cap. The document shows as Processing
  while it waits. 3d's wait brings the real `pending` with its own migration.
- **New catalog entries beyond DOC-025** (for the founder's review):
  - **DOC-026** "The stored file doesn't match this order" -- a missing
    original (404) or a hash mismatch (items 6 and 9); alert `document_failed`.
  - **EXP-010** "This file can't be downloaded right now" -- a ready export
    Storage can't hand over; alert `storage_unavailable`.
  - **LIFE-007** "The tenant's files couldn't all be removed" -- the
    founder's error when the hard delete's file step fails (item 7).
- **Console catalog import** during an outage: the upload answers DOC-025;
  a file that can't be read back fails the import as IMP-009 ("nothing was
  imported ... start it again"). Reads elsewhere in the Console fall to the
  general SYS-001.
- **The review screen's viewer** treats any Storage failure like a missing
  file: a 404 for the file, never a 500 for the screen. **Corrected after the
  founder's walkthrough (2026-09-30):**
  - The screen used to say "a format a browser can't display", beside an
    Open button that led to a bare 404.
  - The viewer link now also returns `unavailable`, the catalog entry
    **DOC-027** "The original can't be shown", whenever nothing can be read
    (missing, refused, or Storage down). The viewer shows DOC-027 with no
    link.
  - A preview that can't be read falls back to the original.
  - An outage here raises `storage_unavailable` like every other reader, and
    shows **DOC-028** "The original can't be shown right now" ("reload in a
    few minutes"), not DOC-027 ("ask the sender"). Founder's review of
    `aefd428`: the tenant's next step differs. DOC-028 does not claim the
    file is fine: during an outage DocFlow can't know that.
- **Tenant creation cleanup is best effort:** removing the copies after a
  failed creation, or the staging originals after a committed one, never
  replaces the error (or the success) the founder sees. Anything left is an
  orphan the copy script reports.
- **Hard delete's final sweep** is recorded as its own admin action,
  `tenant_delete_final_sweep`, with its count (or its failure); the deletion
  event and `tenant_delete` carry `objects_removed` from the first step.
- **CI:** `supabase/config.toml` turns Storage on; every job with a local
  stack runs `scripts/ci/local_storage_env.py`, which points the four
  settings at the runner's own S3 endpoint and refuses anything else.
- **Test files left in the staging bucket:** the staging suites now write to
  the real bucket. The Console tests clean up their own objects; other
  suites' test tenants may leave objects behind, which the copy script's
  orphan count and a later clean-up handle. Not a data risk (test data only).

Built and tested offline: core 671 passed (+3 live-bucket tests that run
where Storage is configured), worker 103 passed, API the same 24
environment-only failures as the base branch plus the new webhook test.
Still to do, on the founder's machine: the staging suites (including
`test_storage_outage_api.py`, the rewritten Console tests and the live-bucket
tests), then the rollout in item 11.

**Rollout on staging, 2026-09-30 (founder's machine):**
- Step 1: backup schema `backup_3b` (documents 54, exports 11,
  onboarding_intake_files 2, catalog_imports 22; live = backup on all four),
  the tables whose rows point at files. The files' own backup is the
  untouched `storage/` folder, plus a zip of it outside the repo (2,973
  files).
- Steps 2-3: the key's four settings are in `.env`, and `0033` is applied.
  The bucket exists, and it refuses both a public URL and the anon key for
  an object that exists (HTTP 400 for each).
- Step 4: dry run, then `--apply`. **89 copied and verified by SHA-256**,
  0 missing locally, 0 failed, 2,877 orphans left in place. **14 rows
  flagged and left out (founder: option (b)):**
  - **7 hash mismatches.** Seed and test scripts store an altered hash on
    purpose, to dodge duplicate detection: `seed_review_walkthrough.py`
    hashes file + document id (5 rows); `seed_demo_data.py`'s "resend"
    hashes the text rather than the `.xlsx` (1 row); the
    golden-with-examples row's hash has an extra `x` (1 row). Six of the
    seven are in Acme Test Distributor.
  - **7 made-up paths with no file behind them** (`tenants/seed/po.txt`,
    `tenants/{id}/seed/...`), written by the merge-demo seed and the
    acting-edit, reapprove and M5-lock tests.
  - Consequence: these orders show no original in the viewer. The script
    therefore exits 1 in this environment, not the 0 RUNBOOK 7.2 describes
    for a clean run.
  - **Pass criterion for the delta copy:** the flagged list is exactly
    these 14 and nothing new, and "referenced but missing locally" is 0.
    The baseline dry run is saved for the comparison.
- Step 7 (staging suites), first worker run: **28 failed / 110 passed / 2
  skipped.** The 35 database-backed worker tests had never run against the
  build; the offline "103 passed" didn't include them. Two causes, both
  fixed:
  - **Product bug: the bulk delete never worked on Supabase.**
    `DeleteObjects` answered 400 "must have required property 'Body'":
    Supabase reads that body only when it is labelled `application/xml`, and
    boto3 doesn't label it. Every tenant hard delete would have stopped at
    LIFE-007. The in-memory fake couldn't show it; single-object delete and
    server-side copy were checked on staging and work. Fixed in
    `S3Backend` with a request hook. New live test,
    `test_a_hard_delete_empties_the_tenants_folder_in_the_real_bucket`:
    it fails with the hook removed and passes with it. **Watch the first CI
    run:** CI runs this test against the local stack's Storage, not the
    hosted one.
  - **Test fixture:** `WorkerTestTenant.create_pending_document` stored a
    random `content_sha256`, so the hash check (Q5) failed every order it
    made with DOC-026. It now stores the real hash. In the API suite, a
    DOC-026 failure means the same fixture problem, not a storage one.
  - The failed run's clean-up removed its test tenants' rows but not their
    files, so about 28 dead test tenants' objects remain in the staging
    bucket. The copy script's orphan count is local files only, so they
    don't affect the delta-copy check. They are listed before anything is
    removed.
- **Staging suites on the final code (`e3e8641`, 2026-09-30):**
  - core: 678 passed, 1 skipped (the customer-token live test, which needs
    `SUPABASE_JWT_SECRET`, blank since D-174; the anon-key test covers it);
  - worker: 139 passed, 2 skipped (the prefork tests, Linux only; CI runs
    them);
  - API: **568 passed, 3 deselected**, nothing failed or skipped. Then, after
    the DOC-027 viewer fix `aefd428` (new API code and 3 new API tests),
    **571 passed, 3 deselected** on `aefd428`. RUNBOOK 1.4 says 571. The
    DOC-028 follow-up commit changed one API test's expectation and the
    viewer's cause-to-code branch. It was checked with the whole review API
    file on staging (35 passed), core (678 passed, 1 skipped) and every
    linter; CI's full run covers the rest.
  - web, on `aefd428`: Vitest 59 passed; the full browser suite 73 passed
    (72 plus the DOC-027 test). The DOC-028 commit doesn't touch the web app.
  - live (`pytest -m live_api`, paid): **3 passed**. That's the golden
    fixture, the golden fixture with examples, and the example-contamination
    check, required because this stage changed the example read path
    (7.13). **The live example tests use a stand-in read:** their examples
    come from a fixture file. Reading examples from the real bucket is
    proven separately against staging (`test_example_prompting_db.py`, 10
    passed: `save_file` into the bucket, then `select_examples` with no
    stand-in). So the bucket read and the model call with examples are each
    tested, but **not end to end in one run**. *Stage 5 item (founder,
    2026-09-30):* add a real-bucket read to a live example test.
  - The first API run (11 failed) found stale `storage._resolve` imports in
    the example-prompting tests, and a refused stored path answering 500 in
    the review viewer. The fix is D-182's addendum: every reader handles a
    refused path; a path under another tenant's folder raises a critical
    `storage_path_cross_tenant` alert; IMP-010; and a build guard.
- **Founder's walkthrough (`docs/walkthroughs/3b-storage.md`), 2026-09-30:
  steps 1-6 pass.** Each step was checked against the bucket and the
  database:
  - upload: the original sits under `uploads/` with a matching hash, and its
    extracted text is at the fixed `derived/` key;
  - export: under `exports/`, with its hash matching the one recorded;
  - catalog import: under `uploads/`, with a matching hash;
  - tenant from intake: the file was copied inside Storage to `onboarding/`,
    and the staging original is gone;
  - **hard delete: the folder went from 5 objects to 0, and the deletion
    record says `objects_removed: 5`** (the bulk-delete fix, proven live);
  - a bad path: the viewer shows DOC-027 (built after step 6 first showed
    the wrong message).

  Two faults outside 3b turned up, the Next.js dev cache and seed-script
  data, both recorded here.
- **Delta copy, 2026-09-30 (after the switch at 15:21 UTC):** nothing to
  copy. The pass criterion "0 referenced but missing locally" assumed
  nothing would be written after the switch, but the tests and the
  walkthrough wrote. **Restated: nothing left to copy, and every newly
  flagged row is either already in the bucket or explained.** It holds:
  - 49 new rows are in the bucket (written after the switch);
  - 1 is a made-up test path, `tenants/seed/seed.txt`, on "Acme Test
    Examples" (goes into the seed-data item below);
  - 1 is the intake file row of the hard-deleted walkthrough tenant (see
    the retention question below).

  The original 14 flagged rows are unchanged.
- **Bucket clean-up, 2026-09-30:** 228 tenant folders; 10 belong to
  existing tenants and 218 have no `tenants` row at all (310 objects).
  Every one of the 218 was written inside one of today's recorded test
  runs:
  - worker run 1 (the failed clean-up run): 58;
  - the three full API runs: 46, 46 and 47;
  - the API "affected files" run: 17;
  - worker runs 2 and 3: 2 and 1;
  - the viewer tests: 1.

  Staging folders: none at the rollout copy (no intake file row pointed at
  `staging/`). Those created since, by the walkthrough intake and the
  Console tests, were removed by tenant creation and by the tests' clean-up.
  No row points at `staging/` now.

  **Deleted, in this order:**
  1. The founder ran the backup check for all 218 ids against
     `backup_3b.documents`. The result was empty ("No rows returned").
  2. The founder approved.
  3. The 218 folders were deleted by their saved ids, with a `tenants`-row
     re-check before each one: **218 folders, 310 files removed**, none
     skipped.

  Before: 229 folders, 450 files (the 228 plus one in-flight test folder).
  After: **10 folders, all existing tenants.**

  *Out of order:* the delete ran **during** the final API run. The founder
  had since asked for it to run after (the D-160 kind of overlap). Nothing
  outside the list was touched, since the 310 files removed are exactly the
  list's, and the run passed.
- **Second clean-up, 2026-09-30 (after the final runs):** 48 leftover test
  folders (75 files), none with a `tenants` row. The founder ran backup
  check 2 against `backup_3b.documents` (empty: "No rows returned") and
  approved. **48 folders and 75 files deleted, 0 skipped.** The bucket went
  from 58 folders and 214 files to **10 folders and 139 files**, with **no
  tenant-less folder left**.
- **3b MERGED 2026-09-30 (PR #31, main `5810a54`).** PR run green: api 571,
  core 679, worker 141, web 73, web-live 3. Left to the founder: dropping
  `backup_3b` on staging (RUNBOOK 1.3), and deleting the temporary venvs in
  `C:/Users/NK/AppData/Local/Temp/dfv`. The local `storage/` folder and its
  zip are kept.
- **Stage 5 (founder, 2026-09-30): the API suite's test clean-up never
  deletes its tenants' files.** Each full staging API run leaves about 46
  tenant folders and 73 files in the bucket. The worker suite's clean-up
  deletes them. Fix the shared API test fixtures in Stage 5; until then,
  the clean-up procedure above clears them.
- **Question for the founder (data retention, 2026-09-30, from before 3b):
  a hard delete leaves the linked onboarding intake behind.** Intakes carry
  no `tenant_id`, only `linked_tenant_id`, so the delete doesn't reach
  them.
  - The files themselves are removed, because they were moved under
    `tenants/{id}/onboarding/` at tenant creation.
  - The `onboarding_intakes` row stays, with the prospect's name and contact
    email, and so do its file rows, pointing at objects that no longer
    exist.
  - The Console's intake page reads only those rows, so it lists the files
    as if present (no error page, but a stale listing).
  - CLAUDE.md 7.14 says deletion removes the tenant's business data.
    Whether a linked intake counts is the founder's decision. It doesn't
    block 3b.
- **Stage 5 question (founder, 2026-09-30): the review viewer finding an
  order's original missing tells no one.** The worker alerts on DOC-026
  (`document_failed`), but the viewer only logs
  `viewer_stored_file_missing`. A missing original is a data-integrity
  fault, so the founder probably wants an alert, deduplicated per order.
- **Stage 5 question (founder, 2026-09-30): a catalog import failed by the
  stuck sweep raises no alert.** The sweep marks an import left in `parsing`
  failed with IMP-009 and tells no one. CLAUDE.md 7.9 requires an alert for a
  document stuck past the timeout, and a stuck import is the same silent
  failure for the founder. Exports have `exports_not_finishing` (more than 3
  in a day); imports have nothing. To decide in Stage 5, not built in 3b.
- **Known issue for Stage 5** (the sweep and robust test cleanup): seeded
  rows with a faked `content_sha256` fail DOC-026 if they are ever read
  again for extraction (the hash check, Q5). Fix the seed scripts to store
  the real hash, and repair or remove these 14 rows. **Also (founder's
  walkthrough, 2026-09-30):** `seed_merge_demo.py` inserts orders with no
  order total and no lines, and never runs validation. The review screen
  therefore shows "Everything checked" with a required field empty.
  Approval re-validates (H2) and would refuse, so nothing wrong can be
  approved. Still, seeded orders must go through the pipeline, or at least
  through validation, so they don't look clean when they aren't.
- **CI, 2026-09-30: every test passed on `58c8008`; only the dependency
  audit failed**, on two advisories published that day (PyJWT, Next.js).
  They were fixed in their own PR (#30, "Security upgrades" below), merged to
  `main` first, then `main` was merged into this branch:
  - **no conflict**; both lock files now pin `boto3==1.43.105` and
    `PyJWT==2.15.0`;
  - the lock files are plain `pip freeze` output (there is no lock tool), so
    each was checked by installing it into a fresh Python 3.13 environment
    the way CI does, then core with `--no-deps`: `pip check` clean, a fresh
    `pip freeze` identical to the lock file, `pip-audit` clean (api and
    worker);
  - web: `npm ci`, next and eslint-config-next at 16.3.8, `npm audit`
    0 vulnerabilities;
  - DOC-028 re-checked at `58c8008` (founder): it doesn't contain "The file
    itself is fine".

