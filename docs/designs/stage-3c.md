# Stage 3c: the parse service (H5, the full part)

Moved here word for word from `docs/BUILD-STATUS.md` on 2026-10-05 (the founder's context
housekeeping, D-191). Nothing below the marker was edited: it is the text as it stood under
"Stage 3 -- agreed with the founder before building", 3c merged 2026-10-01 (PR #32, D-183). It keeps its
original wording, including statuses that were true when each part was written.

Other files cite these sections as BUILD-STATUS "<heading>". Each cited heading is still in
`docs/BUILD-STATUS.md`, as a one-line stub pointing here. "Above" and "below" in this text
refer to the order the blocks had there: 3a, 3b, 3c, 3d, card billing, 3e.

<!-- moved text starts on the next line -->
**3c -- H5, the full part: the parse service.** Agreed:
- **Bytes in, text and images out.** The worker sends the file, the service
  returns the parts. The service holds **no storage key, no database login
  and no model key**, and **refuses to start in production mode unless
  isolation is active**.
- **Every parse process** gets:
  - its own network namespace, and a mount namespace hiding `/.fly` and `/sys`
    (the D-150 spike's two findings);
  - an unprivileged user;
  - `setrlimit` memory and CPU caps;
  - a SIGKILL wall-clock timeout;
  - one subprocess per file, never reused.
- Tier 2 conversion (LibreOffice, image libraries, `.msg`) moves into it.
- **CI requirement (founder):** CI builds the parse service from **the same
  Linux image production uses**, runs it, and exercises **the real
  namespaces and limits**, including the hostile fixtures against the running
  service, not only unit tests of the isolation code. Dev is Windows, where
  none of this can run, so **CI is the only place isolation runs before
  production**. In dev the service runs the same code path without
  namespaces; in production mode that is a refusal to start.
- **The re-probe against the real Upstash and the real API happens in Stage
  3**, on a Fly staging deployment. It is priced first, and the founder
  approves the monthly number before anything is stood up.

**3c detailed design -- APPROVED 2026-10-01; BUILDING.** Proposed
2026-09-30 and revised twice after the founder's reviews. Q1-Q10 are
answered (end of this section), and the founder's second review added
changes 1-5 (io_uring, userfaultfd and x32 in the filter; swap; a CPU
quota as well as a CPU budget; token and alert-dedupe tests; the Fly
gate before any production deploy), all folded in below. Then: "build
3c".

*Where things stand today.* Every hostile file is opened inside the Celery
worker process, the same process that holds the database login, the
Storage S3 key and the Anthropic key. LibreOffice is the only subprocess
(a 120 s timeout, nothing else). No namespace, no `setrlimit`, no
unprivileged user: D-003's controls were never built (review H5). The
places that open a file a stranger sent:

| Where | What it opens | Library |
|---|---|---|
| worker, `parse_and_extract` | re-validation (`validate_upload`), Tier 2 conversion and `.msg`/`.eml` unwrapping (`conversion.prepare_artifacts`), Tier 1 text (`_inner_blocks`) | pdfplumber, python-docx, openpyxl, Pillow, pillow-heif, olefile, xlrd, defusedxml, LibreOffice |
| worker, `_store_preview` | TIFF/HEIC preview (`previews.build_preview`) | Pillow, pillow-heif |
| worker, `parse_import` | catalog and customer-list files (`catalog_parsing.parse_table`) | openpyxl, xlrd, csv |
| API, upload / email intake / Console staging | the gate before storing: magic bytes, the zip's entry list, the first bytes of each XML part (`validate_upload`) | Python's own `zipfile`, `zlib`, `re` |

**The founder's pre-build check on a real Fly machine (2026-09-30).** The
founder asked, before anything is built, whether the service can create a
per-job cgroup v2 memory limit on a Fly machine. Checked on a throwaway
app (`docflow-spike-cgroup-ekqxmh`: shared-cpu-1x, 1 GB, `iad`, no
services, no IP, no credential; destroyed afterwards, `fly apps list`: no
apps). Script, Dockerfile and output: `docs/spikes/3c-cgroup-check/`.
Every check that needs a control had one beside it (RUNBOOK 1.7).
- **cgroup v2 memory limits are not available as Fly delivers the
  machine.** Kernel `6.12.105-fly`, booted with `cgroup_enable=memory
  swapaccount=1`. Fly's init mounts the cgroup **v1** controllers (memory,
  pids, cpu/cpuacct and the rest) under `/sys/fs/cgroup`, and a v2
  hierarchy at `/sys/fs/cgroup/unified` with **no controllers**. A
  controller can belong to only one hierarchy, so v2 has no `memory.max`.
- **Moving `memory` to v2 failed.** Root could unmount the v1 memory
  hierarchy, but the controller was not handed to v2: `/proc/cgroups`
  still showed it on v1 hierarchy 8 with 5 cgroups lingering. Taking
  Fly's setup apart at startup would be fragile anyway.
- **The cgroup v1 memory controller works per job** (run 2, all PASS):
  one process over a 256 MiB limit was killed, while 400 MiB with no limit
  survived as the control; **4 processes x 120 MiB, together over 256 MiB,
  were stopped**; the limit held inside the full sandbox (namespaces, uid
  10001, no capabilities); the sandboxed job couldn't raise its limit or
  move out (EACCES), and with `/sys` hidden the files weren't there at
  all (ENOENT); root could still change the limit; emptied job cgroups
  were removed cleanly; a v1 `pids.max` of 20 stopped a fork loop at 19.
- **The founder's point, confirmed:** the same 4 x 120 MiB processes, each
  under its own 256 MiB `setrlimit` and no cgroup, **all survived**.
  `setrlimit` multiplies with the processes, as the founder said.
- **v1 has no "kill the whole group" switch** (v2's `memory.oom.group`):
  after the family test one process was still left in the cgroup. So the
  supervisor must kill the whole job itself on any out-of-memory kill
  (item 3).
- **seccomp works on Fly's kernel** (run 1, 2b/2c/2e PASS): every call on
  the founder's list was answered by the filter, both as root and when
  uid 10001 loaded it itself under `no_new_privs`. `clone3` got ENOSYS,
  and a normal thread and a normal fork still worked.
- **Why seccomp matters here, measured:** without the filter, uid 10001
  with no capabilities **could** create a user namespace
  (`unshare(CLONE_NEWUSER)` succeeded; `max_user_namespaces` is 3716),
  then clone a new network namespace from inside it, and could use
  `keyctl`. With the filter, none of those worked.
- **Block devices are visible on the machine:** `vda`, `vdb`, `vdc`, 8
  `loop`, 16 `nbd`, all root-only. That is the control for the new `/dev`
  test (A13).
- No swap on the machine (`/proc/swaps` empty); 985 MB of usable memory
  on a 1 GB machine.
- Method notes, stated plainly: run 1's 1l and 1m results said nothing
  about the design (the job files they probed were never created, because
  v2 had no controllers), and are superseded by run 2's A6 and A7. A
  re-check line produced from PowerShell (`controllers=[]`) was read on
  this machine, not on Fly; the evidence file says so, and the valid
  re-read is beside it.

1. **What moves into the parse service.** Everything in the first three
   rows of the table above. The worker keeps: reading the original from
   Storage and checking its SHA-256 (3b), sending the bytes, checking the
   answer (item 6), turning returned text into the text preview (string
   work, no file), the model call, and everything after it. Exports stay
   in the worker: it writes those files itself, and the round-trip check
   reads back only bytes DocFlow wrote. **The API's gate (last row) stays
   in the API (Q1: yes);** the same check runs again in the sandbox before
   anything else.
   - **Afterwards the worker and the API can't parse.** pdfplumber,
     python-docx, Pillow, pillow-heif, olefile, xlrd and defusedxml leave
     both requirement files, and LibreOffice leaves the worker's machine.
     openpyxl stays in the worker for writing `.xlsx` exports only. A
     dependency-graph test (like the `adminDataAccess` one) fails the build
     if `conversion`, `catalog_parsing` or any of those libraries is
     importable from `apps/worker` or `apps/api` product code.
2. **The service.** A new `apps/parse/`, its own requirements and lock
   file, its own Dockerfile.
   - **One image, built once.** Base `debian:trixie-slim` (Debian 13),
     pinned by digest, so Debian's own Python is 3.13 (what CI uses) and
     its `python3-seccomp` binding is available. LibreOffice and
     `python3-seccomp` are pinned by Debian package version. CI and Fly run
     the same file, and the dependency audit covers its lock file. (The
     check machine ran Debian 12; CI is the first place trixie is proven.)
   - **Private only, reached over Flycast (founder's item 4, confirmed).**
     The parse app has **no public IP address** and no `[http_service]`.
     Flycast still needs a services section for Fly's proxy to route to the
     app at all (Fly's Flycast docs: "either an `[http_service]` section or
     `[services]` sections"). So the app has a `[[services]]` block, plain
     HTTP (Flycast is HTTP-only; no TLS handler, no `force_https`), with
     `auto_stop_machines` and `auto_start_machines` on: a worker request to
     `docflow-parse-staging.flycast` starts a stopped machine. The address
     is private because the app holds only a private IPv6 address
     (`fly ips allocate-v6 --private`). Fly's docs warn that any public IP
     on the app would expose the services section to the internet, so
     **tests N1-N2 check the IP list after every deploy.**
   - **Two calls:** `POST /v1/document` (bytes in; text parts, page or
     image parts, and the preview out) and `POST /v1/table` (bytes in;
     rows of text cells out, for catalog imports). Each answer is one of
     `ok`, `rejected` (a catalog code, exactly as today: DOC-001, DOC-005,
     DOC-010 to DOC-019 and IMP-004), or `stopped` (a limit was hit,
     item 3).
   - **The supervisor never opens a file.** It is a small HTTP process
     that caps the request size (`MAX_FILE_SIZE_BYTES`), starts one
     sandboxed job per request, reads the job's answer up to a cap, checks
     the answer's shape, and replies. Everything that reads the file's
     contents runs in the job.
   - **Slots:** 2 jobs at once per machine (`PARSE_SLOTS`). A request
     that finds no free slot gets a 503 with `Retry-After`, which the
     worker treats as "wait", never as a failure.
   - **Who can call it (Q2: yes):** a shared token, `PARSE_SERVICE_TOKEN`,
     held by the worker and the supervisor only, never passed into a job.
3. **Every job.** The supervisor runs as root (it must, to create
   namespaces and cgroups) and, for each request:
   1. **creates the job's cgroups** (Q8: approved; v1 as Fly delivers it,
      v2 where the machine offers it):
      - **memory:** the job's memory limit, **and the memory+swap limit
        set to the same value** (v1 `memory.memsw.limit_in_bytes`; v2
        `memory.swap.max` = 0). v1's `memory.limit_in_bytes` doesn't count
        swap, so without this a machine with swap would let a job go past
        its limit into swap (founder's change 2). The supervisor refuses to
        start a job if the swap limit can't be set, and the canary also
        confirms the machine has no active swap;
      - **pids:** the job's process cap;
      - **cpu (founder's change 3, both):** a **quota** (v1
        `cpu.cfs_quota_us` / `cpu.cfs_period_us`; v2 `cpu.max`) of one CPU
        per job, which *slows a job down* so it can't starve the other
        slot; and a **CPU-seconds budget** read from cpuacct (v1
        `cpuacct.usage`; v2 `cpu.stat` `usage_usec`), which *kills* the
        job when its processes together pass it.

      The job's first process is moved into them before it starts
      anything, so everything it starts is born inside the limits;
   2. starts the job in new network, mount, PID, IPC and UTS namespaces
      (`unshare --net --mount --pid --ipc --uts --fork`). The network
      namespace has only a loopback that is down. Killing the PID
      namespace's first process kills every process in it, LibreOffice's
      helpers included;
   3. inside the mount namespace (founder's items 3 and Q7):
      - **the root filesystem is read-only** (Q7: yes);
      - **writable, size-capped tmpfs only:** `/work` (the input file and
        temporary files) and a separate home for LibreOffice's profile
        (`HOME`). Their pages count against the job's memory limit too;
      - an empty tmpfs over `/.fly` and `/sys` (the D-150 spike's two
        findings), which also hides the job's own cgroup files;
      - a fresh `/proc` for the new PID namespace;
      - **its own minimal `/dev`** (founder's item 3): a new tmpfs holding
        only `null`, `zero`, `full`, `random` and `urandom`, plus a
        size-capped `/dev/shm`. No block device, nothing else;
   4. `setrlimit` **as backstops** (founder: the cgroup is the primary
      cap): address space per process, CPU seconds per process, processes
      per user, open files, file size, no core dumps;
   5. an empty environment (a fixed `PATH`, `HOME`, `TMPDIR=/work`,
      `LANG`);
   6. drops to an unprivileged user with `setpriv`: no capabilities, an
      empty bounding set, `no_new_privs`, no supplementary groups, **one
      user ID per slot** (10001, 10002);
   7. execs the job's Python entry point, which **first** (founder's item
      1) sets `PR_SET_NO_NEW_PRIVS` itself and loads the seccomp filter
      (below), then checks `/proc/self/status` shows `NoNewPrivs: 1` and
      `Seccomp: 2`, and only then opens the input. If either check fails,
      it exits without reading the file and the supervisor answers as for
      an isolation failure. The filter is inherited by everything the job
      starts, LibreOffice included.

   **The seccomp filter (founder's item 1 and change 1).** Default allow,
   with these refused (each answers EPERM):
   - the founder's list: `unshare`; `clone` with any new-namespace flag;
     `setns`; `mount` and `umount2`; `ptrace`; `bpf`; `keyctl`;
     `perf_event_open`; `init_module`, `finit_module`, `delete_module`;
     `kexec_load`, `kexec_file_load`;
   - **the calls that do the same jobs by another name**, without which
     the list could be stepped around (*proposed*, inside the founder's
     intent): the newer mount calls (`fsopen`, `fsconfig`, `fsmount`,
     `fspick`, `move_mount`, `open_tree`, `mount_setattr`); the rest of
     the key family (`add_key`, `request_key`); and `ptrace`'s relatives
     `process_vm_readv` and `process_vm_writev`;
   - **founder's change 1:** `io_uring_setup`, `io_uring_enter`,
     `io_uring_register` (io_uring performs I/O in kernel threads the
     filter never sees), and `userfaultfd` (a common tool for winning
     kernel race conditions);
   - **`clone3` answers ENOSYS**, not EPERM. It passes its flags in a
     memory block the filter can't read, so it is refused in a way that
     makes the C library fall back to `clone`, whose flags the filter
     can read (Fly check 2c: threads and fork still work);
   - calls from any other CPU architecture are killed (the filter is
     built for x86_64 only, with its "wrong architecture" action set
     explicitly to kill the process), so a 32-bit call can't slip past the
     rules; **and any x32 call, a call number with the `0x40000000` bit
     set, kills the process** (founder's change 1). x32 calls report the
     same architecture as x86_64, so the architecture check alone doesn't
     catch them; the filter checks the bit itself.

   **The supervisor's own limits:**
   - a wall-clock timer that SIGKILLs the job's first process, so the
     whole PID namespace dies;
   - a watch on the job's cgroups every 100 ms while it runs. **Any
     out-of-memory kill in the job kills the whole job** (v1 kills one
     process at a time; the Fly check left one behind). So does the job's
     **total** CPU passing its budget (Q8: approved; CPU seconds multiply
     with processes exactly as memory does);
   - a cap on the answer's size (it stops reading and kills the job past
     it);
   - after the job, the cgroups are removed and `/work` and the home are
     gone, so nothing outlives a file. Every job is a new process: **one
     subprocess per file, never reused.**

   *Proposed numbers, then measured (the real Tier 1 and Tier 2 POs must
   all parse under them in CI and on Fly, D1, before they are fixed):*

   | Limit | Proposed | Kind |
   |---|---|---|
   | `PARSE_JOB_WALL_SECONDS` | 180 | supervisor; above LibreOffice's own 120 s (kept), well inside the 20-minute read budget (D-163) |
   | Job memory (cgroup) | 768 MiB | **primary**; two slots fit a 2 GB machine with room for the supervisor |
   | Job CPU quota (cfs / `cpu.max`) | 1 CPU per job | **primary**: slows, never kills; one job can't starve the other slot |
   | Job CPU budget (cpuacct) | 150 s | **primary**: kills the whole job |
   | Job memory+swap | = job memory | **primary**: no swap past the limit |
   | Job processes (cgroup pids) | 128 | **primary**; LibreOffice runs a few dozen threads, and threads count |
   | Address space per process | 2 GiB | backstop, set high on purpose: LibreOffice reserves far more than it uses |
   | CPU seconds per process | 170 | backstop |
   | Processes per slot user | 256 | backstop |
   | Open files | 256 | |
   | Largest file the job may write | 64 MiB | |
   | `/work` tmpfs | 256 MiB | counts against job memory |
   | LibreOffice home tmpfs | 128 MiB | counts against job memory |
   | `/dev/shm` | 64 MiB | counts against job memory |
   | Answer cap | 48 MiB | a 25 MB scanned PDF returned base64 is about 34 MB |

   **CI and Fly take different cgroup paths, stated plainly.** GitHub's
   runners are cgroup v2 only, and Fly is v1. The supervisor detects which
   one it has at startup: v2 memory if it is available, otherwise v1. If
   Fly ever moves to v2, the same image follows it. So CI proves the
   supervisor's logic on v2, and **the v1 path production uses is proven
   only on Fly**. That is why the B tests are required on Fly before the
   merge, not only in CI.

   **Gate (founder's change 5): the Fly B tests must pass before any
   production deploy that touches the parse service** -- its code, its
   image, its base image or packages, its `fly.toml`, or the worker's
   `parse_client` -- not only at the 3c checkpoint. The procedure: deploy
   the change to Fly staging first, run B1-B12 there, keep the evidence,
   then deploy to production. RUNBOOK section 8 holds it, and the 3c PR
   description repeats it.

   **A test-only switch that breaks a cap** (E4, if CI needs one) is
   refused whenever `FLY_APP_NAME` is set: the service exits at startup.
   Test E5 checks that.
4. **Refuses to start in production mode unless isolation is active.**
   - Production mode is on when `FLY_APP_NAME` is set (Fly sets it on
     every machine) or `DOCFLOW_ENV=production`. `PARSE_ISOLATION=off`,
     the dev setting, is refused in production mode, so a stray setting
     can't switch it off on Fly.
   - **At startup, before the port opens,** the supervisor runs a canary
     job through the same launcher. It is a fixed program built into the
     image and takes no input. It checks that:
     - every network probe fails;
     - `/.fly`, `/sys` and the block devices are hidden;
     - the root filesystem is read-only;
     - it has the slot's user, no capabilities, `NoNewPrivs: 1` and
       `Seccomp: 2`;
     - a few calls from the seccomp list are refused;
     - **a memory limit really kills** (a small allocation past a small
       cgroup limit must be stopped);
     - **the machine has no active swap** (`/proc/swaps`), and the job's
       memory+swap limit is set (founder's change 2);
     - the CPU quota is set and the CPU budget kills.

     Any unexpected success, or a cgroup or namespace that can't be
     created, exits with an error. Fly restarts it, it fails again, and the
     health check never passes, so the worker gets "unavailable" (item 6)
     and the founder an alert.
   - `/health` answers OK only after the canary passed.
   - There is **no HTTP endpoint that runs a probe or a test program.**
     The canary and the self-test programs in item 11 are fixed programs in
     the image, started through the same launcher from a shell on the
     machine (`fly ssh console`, or `docker exec` in CI), never over the
     network.
5. **(Folded into item 2: the shared token, Q2.)**
6. **The worker's side: what each answer does.** A new `parse_client`
   module. Connect timeout 5 s; read timeout `PARSE_JOB_WALL_SECONDS` +
   30 s.

   | Answer | Document | Catalog import |
   |---|---|---|
   | `ok` | parts checked (below), then the read goes ahead as today | rows checked, then as today |
   | `rejected` with a code | `failed` with that code, as today | `failed` with that code, as today |
   | `stopped` (a limit) | `failed` with **DOC-029** (Q3), founder alert. Final: no retry | `failed` with IMP-004, as today for an unreadable file |
   | **Never got in:** connection refused, DNS failure, 503 no free slot, the service unhealthy | **waits, as a Storage outage does (3b):** stays `processing`, the try given back, the sweep retries; `parse_service_unavailable` alert, at most once an hour platform-wide (Q4) | IMP-009 ("start again"), same alert |
   | **Got in, never came out:** the connection dropped or the read timed out after the file was sent | **a timeout-class try** (item 6a); the worker applies the sweep's own decision at once | IMP-009 |
   | An answer that fails the worker's checks | `failed` DOC-005 and the DOC-029 founder alert (Q3) | IMP-004 and the same alert |

   - **The worker checks every answer** (a converter's output is still
     untrusted, Section 7.11): only the part types the model call takes;
     media types from a fixed list; base64 that decodes; text and total
     sizes within caps. The worker never decodes an image or PDF to check
     it, because that would be parsing again.
   - **All of this sits outside the broad `except` blocks**, as the 3b
     Storage read does, so no service failure can be relabelled DOC-005.
   - Previews stay best effort: a preview the service can't produce never
     touches the document's status.

   **6a. How "got in, never came out" combines with 3a's timeout rule
   (founder's item 5).** Today there are two rules: a crash gets retries
   up to `MAX_PROCESSING_ATTEMPTS` (3) tries in all, and a timeout gets at
   most one retry. **Found while answering this: today's `decide()` can
   already break the 3-try cap.** Its timeout branch is checked first and
   never looks at the cap. So a crash, a crash, then a timeout on try 3
   (`processing_attempts` 3, `timeout_attempts` [3]) returns "retry" and
   allows a **4th** try (`stuck_documents.py`, `decide()`). It's a 3a
   defect, not something 3c introduces, and the founder's rule names
   exactly this case.

   *Proposed* (Q9):
   - A "got in, never came out" try is recorded as a **timeout-class
     try**, alongside 3a's hard time limit, because in both cases the file
     is the suspect.
   - **One decision for both, in `decide()`, checked in this order:**

     | State | Outcome |
     |---|---|
     | 3 tries used | fail DOC-022: cause `timeout` if any try was timeout-class, else `worker_stopped` |
     | a timeout-class try happened, and a try has run since the first one | fail DOC-022, cause `timeout` |
     | otherwise | retry |

   - **The guarantee, whatever the mix:** at most 3 tries in all, and at
     most one try after the first timeout-class try.

     | Sequence | Today | Proposed |
     |---|---|---|
     | crash, crash, crash | 3 tries, `worker_stopped` | same |
     | timeout, timeout | 2 tries, `timeout` | same |
     | crash, timeout, anything | 3 tries, `timeout` | same |
     | **crash, crash, timeout** | **4th try** | **3 tries, `timeout`** |
     | lost, lost | -- | 2 tries, `timeout` (cause detail `parse_lost`) |
     | crash, lost, crash | -- | 3 tries, `timeout` |
     | never got in (any number of times) | -- | no try used; waits |
     | stopped by a limit | -- | 1 try, DOC-029, final |

   - The worker, which is still alive after a lost try, records it and
     applies `decide()` at once, so a lost file is retried or failed in
     seconds, not after the 30-minute sweep. The sweep calls the same
     function. **One decision, one place.**
   - *Recording (Q9):* proposed, a `documents.parse_lost_attempts
     integer[]` column in `0034` beside 3a's `timeout_attempts`. Both
     count as timeout-class in `decide()`, and the DOC-022 alert's payload
     can then say which happened (`cause_detail: parse_lost` or
     `task_time_limit`). The alternative is no column: a lost try goes
     into `timeout_attempts`, and the alert can't tell the two apart.
7. **Dev on Windows.** The same service runs locally with
   `PARSE_ISOLATION=off`. It still uses one subprocess per file, still has
   the wall-clock kill, and uses LibreOffice from the existing Windows
   install. It has no namespaces, cgroups, user switch, seccomp or
   `setrlimit`, none of which Windows has. The worker reaches it at
   `PARSE_SERVICE_URL` (`http://127.0.0.1:8100` in dev). The worker's test
   suite starts it as a fixture. **None of this counts as proof of
   isolation:** CI and Fly are the only places isolation runs.
8. **The API's gate (Q1: stays).** `validate_upload` in the API reads the
   zip's entry list (no extraction) and inflates at most 16 KB from the
   start of each XML part, 8 MB in total, to look for a DOCTYPE or ENTITY.
   It uses only Python's standard library. It decides whether a file is
   stored at all, and gives the uploader an immediate answer. The same
   check runs again inside the sandbox.
9. **D-163: a run row before the model call (Q5: option 1, decided).**
   - **A "started" row, then the outcome as a second row**, as agreed for
     `admin_actions` (Stage 5, D-178). Migration `0034`:
     - `extraction_runs.run_state` (`started` | `finished`; existing rows
       become `finished`);
     - `started_run_id` (the outcome row points at its start row);
     - `succeeded` may be NULL only on a `started` row (a check
       constraint).

     Nothing is ever updated.
   - Before each paid call (routing and extraction), the worker counts the
     input tokens (`count_tokens`, free, 15 s per try, D-163) and commits
     the started row with the model ID and that count. After the call, it
     writes the outcome row as today.
   - **A started row with no outcome** is found by the stuck sweep when it
     takes the document over. The sweep writes the outcome row itself:
     `succeeded false`, error `worker_lost_during_call`, the input cost
     from the counted tokens, and `cost_complete: false` (the output tokens
     are unknown, as for a dropped stream, D-163). So the cost breaker, the
     cost per document and the KPIs see a lower bound instead of nothing.
   - Every reader that sums cost or counts runs reads outcome rows only. A
     test holds that for each of them: the breaker, the rollup, the health
     strip and cost per document.
   - Cost: one extra free API call per paid call, about 0.2-0.5 s.
   - **Backup first** (founder): `extraction_runs` and `documents` (which
     also gains `parse_lost_attempts` if Q9 is yes). Backup SQL and the
     row counts come before the PR link. `0034` deletes nothing.
10. **What else changes.**
    - A new `DECISIONS.md` entry, D-183 (3c as built).
    - `RUNBOOK.md`:
      - running and deploying the parse service;
      - the parser-upgrade process (CLAUDE.md 7.11 requires it, and it now
        means rebuilding one image: base digest, LibreOffice and
        `python3-seccomp` versions, the lock file);
      - reading the canary's startup log;
      - what to do when `parse_service_unavailable` fires;
      - stopping the staging machines at the end of a test session.
    - `CLAUDE.md`'s parsing-worker bullet says the per-file limits are
      "still to be built and tested in Stage 3". When 3c passes, I'll
      **propose** new wording to the founder; I won't edit it.
    - `SETUP.md`: starting the parse service in dev.
    - Catalog: DOC-029 (Q3, wording approved); founder-facing wording for
      the `parse_service_unavailable` alert and the DOC-029 alert.
11. **Tests, and where each runs.** See "3c test table" directly below this
    section. It has 55 tests. The first version had 33; the founder's
    additions (seccomp, `/dev`, Flycast, the retry rule, the token, the
    alert limits, the test-only switch, the CPU quota) and the cgroup
    findings added the rest.
12. **Fly staging: what is stood up, and the price.** Q6 decided: **stopped
    between test sessions**, Upstash pay-as-you-go. The founder sets the
    secrets.
    - Rates read from Fly's pricing page on 2026-09-30. The page computes
      them from shared vCPU $0.00000075/s and RAM $0.00000193/GB-s, region
      `iad` = 1.0x.

    | App | Size | If left running, per month |
    |---|---|---|
    | `docflow-parse-staging` | shared-cpu-2x, 2 GB (two 768 MiB slots) | $11.39 |
    | `docflow-worker-staging` | shared-cpu-1x, 1 GB (no parsing any more) | $5.70 |
    | `docflow-api-staging` | shared-cpu-1x, 512 MB, **private only, no public IP** | $3.19 |
    | Upstash Redis | pay-as-you-go, $0.20 per 100k commands | per use |

    - Stopped, each machine costs only its disk: $0.15 per GB per 30 days,
      about $0.50 a month for all three. Running, all three cost about
      $0.03 an hour plus Upstash's commands. **A month with 40 hours of
      testing: roughly $2-5.**
    - **Celery's Redis command count is measured in the first session and
      reported** (founder): commands before and after a timed idle hour
      and a timed busy one, read from Upstash. **If the idle hour x 720
      costs more than $10 a month, switch to the fixed $10 plan** (Q10).
    - **The spending cap: not available as far as I can find (Q10).**
      `fly redis create` and `fly redis update` have no budget or cap flag
      (flyctl v0.4.108, read 2026-09-30), and Fly's Upstash page mentions
      none: only 10,000 commands/s and 10 GB storage limits. Usage is
      billed hourly on the Fly bill. The Upstash console (`fly redis
      dashboard`) may have a budget setting; I can only see that once the
      database exists. Options are in Q10.
    - The worker on Fly runs **no Celery beat**: no scheduled sweeps or
      lifecycle jobs from Fly against staging. It only takes documents and
      imports enqueued through the Fly API. The local worker and API keep
      using the local Redis, so the two never share a queue.
    - Not included: no dedicated IPv4 (nothing public), no volumes, and
      egress at $0.02/GB (megabytes here). Builds run on Fly's remote
      builder; whether it is charged will show on the first bill (today's two
      check deploys ran under a cent of machine time).
    - **Secrets on Fly, set by the founder** (exact commands below). Each
      app gets only what its process reads. Non-secret settings
      (`DOCFLOW_ENV`, `PARSE_SERVICE_URL`, `PARSE_ISOLATION`) go in each
      app's `fly.toml`. If the build finds a process needs a setting not
      listed here, it comes back to the founder before it is set.
    - **Not in 3c:** pointing Postmark's or Stripe's webhooks at Fly, the
      web app, a public API, and production. Those are Phase 6.
13. **Order of work.**
    1. The D2 baseline: today's parser output over every fixture, saved.
    2. The `decide()` fix and its tests (Q9), on its own commit.
    3. `apps/parse/`: service, launcher, job, filter, self-test programs,
       Dockerfile. Dev mode on this machine. The worker switched to
       `parse_client`, and the parsing libraries removed from the worker
       and the API.
    4. The CI job (the A, S, B, C, D and E rows with the real image); CI
       green.
    5. D-163 and `0034`: backup SQL and row counts first, then the founder
       applies it on staging; staging suites.
    6. Fly: I create the three apps (no secrets) and the Upstash database;
       **the founder sets the secrets**; deploy; check the canary log; run
       the N, A, S, B, C, D and G rows on Fly. Evidence goes under
       `docs/spikes/3c-fly-staging/`.
    7. The machines stopped, the command count reported, the checkpoint,
       the PR.
14. **Not proposed** (so the founder knows they were considered): an
    allowlist-style seccomp filter (default deny). It is stricter, but
    LibreOffice's syscall set would have to be mapped and kept up to date
    on every upgrade. The deny list above blocks the calls that matter for
    escaping and hiding.

**Questions for the founder (3c).**

*Answered 2026-09-30:*
- **Q1** yes, the API's gate stays.
- **Q2** yes, `PARSE_SERVICE_TOKEN`.
- **Q3** yes: DOC-029 as worded, plus the founder alert.
- **Q4** yes: `parse_service_unavailable`, and the wait/count rule.
- **Q5** option 1 (a started row plus an outcome row, append-only), with
  the backup first.
- **Q6** stopped between test sessions; Upstash pay-as-you-go with a cap;
  Celery's command count measured in the first session; the founder sets
  the Fly secrets.
- **Q7** yes, a read-only root, with a writable size-capped tmpfs for
  LibreOffice's home and profile.

*Answered 2026-10-01 (raised by the pre-build check):*
- **Q8** approved: cgroup v1 memory and pids per job as the primary caps
  (v2 where offered), the whole job killed on any out-of-memory kill, the
  canary proving the cap kills before the service opens, `setrlimit` as
  backstops, and a job-wide CPU budget. Change 3 adds a CPU quota beside
  the budget.
- **Q9** approved: `decide()` checks the 3-try cap first, and
  `documents.parse_lost_attempts` goes in `0034`.
- **Q10** (a): pay-as-you-go. A spending cap is set in the Upstash console
  if one exists. **Rule (founder): after G3, if the idle-hour command
  count x 720 costs more than $10 a month at $0.20 per 100k commands
  (that is, more than about 69,400 commands in the idle hour), switch to
  the fixed $10 plan.**

**Fly secrets, the founder's commands** (PowerShell; placeholders in
`<...>`):
- `--stage` stores each secret without restarting machines; the next
  deploy applies them.
- PowerShell keeps typed commands in its history file
  (`(Get-PSReadLineOption).HistorySavePath`). Either remove these lines
  from it afterwards, or put the `NAME=value` lines in a file and pipe it
  with `Get-Content <file> | fly secrets import --app <app> --stage`,
  then delete the file.

```powershell
$fly = "$env:USERPROFILE\.fly\bin\fly.exe"

# The token: any long random string, the same value on both apps.
& $fly secrets set --app docflow-parse-staging --stage `
  PARSE_SERVICE_TOKEN=<random-token>

& $fly secrets set --app docflow-worker-staging --stage `
  PARSE_SERVICE_TOKEN=<random-token> `
  DATABASE_URL=<staging docflow_app connection string, as in the root .env> `
  REDIS_URL=<the private redis:// URL printed by `fly redis create`> `
  ANTHROPIC_API_KEY=<the staging Anthropic key> `
  STORAGE_S3_ENDPOINT=<as in the root .env> `
  STORAGE_S3_REGION=<as in the root .env> `
  STORAGE_S3_ACCESS_KEY_ID=<as in the root .env> `
  STORAGE_S3_SECRET_ACCESS_KEY=<as in the root .env>

& $fly secrets set --app docflow-api-staging --stage `
  DATABASE_URL=<staging docflow_app connection string, as in the root .env> `
  REDIS_URL=<the same Upstash URL> `
  SUPABASE_URL=<as in the root .env> `
  STORAGE_S3_ENDPOINT=<as in the root .env> `
  STORAGE_S3_REGION=<as in the root .env> `
  STORAGE_S3_ACCESS_KEY_ID=<as in the root .env> `
  STORAGE_S3_SECRET_ACCESS_KEY=<as in the root .env>

# Check: names only, never values.
& $fly secrets list --app docflow-parse-staging
& $fly secrets list --app docflow-worker-staging
& $fly secrets list --app docflow-api-staging
```

The parse app must list **only** `PARSE_SERVICE_TOKEN` (test A12 checks
this as well). The API needs `SUPABASE_URL` to check sign-in tokens (JWKS)
for the upload test. It gets no Stripe, Postmark, service-role or
Anthropic key in 3c.

**3c test table.** 61 tests (55 agreed, plus B13-B16, A15 and S4's second check, 2026-10-01).

Where each test runs:
- **L** = this Windows machine, dev mode. Logic only, never counted as
  proof of isolation.
- **CI** = a new CI job. It builds `apps/parse/Dockerfile` (the production
  image) and runs it with `--privileged`: on Fly the container is root in
  its own VM, and this is the closest a GitHub runner gets. It tests the
  running image from the runner over HTTP, and with `docker exec` for the
  self-test programs. CI's cgroups are v2.
- **Fly** = the same image on Fly staging, cgroups v1 (the production
  path).

Every isolation and limit test runs its control first: the same probe
**outside** the sandbox, or the same program without the limit. Each test
reports its own evidence. RUNBOOK 1.7 applies to every run: an isolation
failure means stop and report.

| # | Test | Control | L | CI | Fly |
|---|---|---|---|---|---|
| **N. Network placement** | | | | | |
| N1 | The parse app has exactly one IP address, a private IPv6 (`fly ips list`), checked after every deploy; the API app has no public IP either | -- | | | yes |
| N2 | From outside Fly's network (this machine, WireGuard off), the parse and API apps have no public address that answers | the same request over `fly proxy` answers | | | yes |
| N3 | A stopped parse machine is started by a worker request to `docflow-parse-staging.flycast` | the machine shows `stopped` before | | | yes |
| N4 | **The token (founder's change 4):** a request with no token and one with a wrong token are both refused (401) before the body is read; nothing starts a job | the right token parses | | yes | yes |
| **A. Isolation, per job** | | | | | |
| A1 | Internet unreachable: IPv4 and IPv6 TCP, HTTPS by name | reached outside | | yes | yes |
| A2 | DNS: the system resolver; Fly's resolver `[fdaa::3]:53` | answered outside | | yes (system) | yes (both) |
| A3 | **The real Upstash** (its private IPv6 address, resolved outside first) | reached outside | | | yes |
| A4 | **The real staging API and the real worker** (by private address) | reached outside | | | yes |
| A5 | The Fly Machines API, `_api.internal:4280` | reached outside | | | yes |
| A6 | The hosts that hold our data: the Supabase database and Storage hosts, `api.anthropic.com` | reached outside | | yes | yes |
| A7 | The supervisor's own port, from inside the job | reached outside | | yes | yes |
| A8 | `/.fly` empty (the socket isn't there, not just refused); `/sys` empty; the only interface is `lo`, down | present outside | | yes (`/sys`) | yes (both) |
| A9 | Identity: the slot's user, `CapEff` 0, empty bounding set, `NoNewPrivs` 1, no supplementary groups | root outside | | yes | yes |
| A10 | No way out: `nsenter` into the machine's namespaces, bringing up an interface, `mount` -- all refused | work as root outside | | yes | yes |
| A11 | The job sees only its own processes; two jobs running at once can't see each other's files or processes, **including `/tmp`: a marker the first job leaves in its `/tmp` is not in the second's (Q14)** | -- | | yes | yes |
| A12 | The job's environment holds none of DocFlow's setting names (database, Storage, Anthropic, Supabase, Stripe, Postmark, the parse token); on Fly, the parse app's secret list is the token alone | the supervisor's own environment has the token | | yes | yes |
| A13 | **`/dev` (founder's item 3):** no block device at all, and only `null`, `zero`, `full`, `random`, `urandom` and `shm` | block devices listed outside (CI: the runner's; Fly: `vda`-`vdc`, `loop`, `nbd`) | | yes | yes |
| A14 | **Read-only root (Q7):** writing to `/`, `/usr`, `/app`, `/etc` or `/opt` fails (EROFS); `/work` and LibreOffice's home are writable up to their caps; **`/tmp` and `/var/tmp` are writable only as the job's `/work` tmpfs (same filesystem; Q14, 2026-10-01)** | the same writes as root outside succeed (into a throwaway path) | | yes | yes |
| **S. seccomp (founder's item 1)** | | | | | |
| S1 | **The shipped rule set, with a test-only errno in place of EPERM, inside the real sandbox:** every refused call returns that errno, so the filter (not a missing privilege) answered: `unshare`, `clone` with each new-namespace flag, `setns`, `mount`, `umount2`, the newer mount calls, `ptrace`, `process_vm_readv/writev`, `bpf`, `keyctl`, `add_key`, `request_key`, `perf_event_open`, `init_module`, `finit_module`, `delete_module`, `kexec_load`, `kexec_file_load`, **`io_uring_setup`, `io_uring_enter`, `io_uring_register`, `userfaultfd`** | -- | | yes | yes |
| S2 | **The filter as shipped (EPERM), inside the real sandbox:** every call in S1 fails, io_uring and `userfaultfd` included; `/proc/self/status` shows `Seccomp: 2` and `NoNewPrivs: 1` | -- | | yes | yes |
| S3 | **What the filter alone stops:** the same calls in the sandbox **without** the filter, reported call by call (on Fly today: `unshare(CLONE_NEWUSER)`, `clone` after it, and `keyctl` succeed) | is the control | | yes | yes |
| S4 | Two checks. `S4:normal-work-under-the-filter`: `clone3` answers ENOSYS; a thread and a fork still work under the filter. **`S4:real-doc-under-the-filter-alone`** (corrected 2026-10-01: the row used to say S4 converts a `.doc`, "with D1"; it didn't): the real document code converts the committed `po.doc` under the seccomp filter and no-new-privileges alone, as the slot user, with no namespaces, no read-only root, no cgroup and no rlimits, and finds its PO number. With B13 (the full sandbox), a `.doc` failure points at the filter (S4 fails) or at the filesystem and limits (only B13 fails) | -- | | yes | yes |
| S5 | The filter allows only x86_64 (its exported form is checked), so calls from another architecture are killed; **an x32 call (`getpid` with the `0x40000000` bit set) kills the process with SIGSYS** | the same x32 call without the filter, reported (ENOSYS if the kernel has no x32) | | yes | yes |
| **B. Limits** (fixed self-test programs in the image, through the real launcher) | | | | | |
| B1 | Memory, one process: a program allocating past the job's cgroup limit is stopped as `stopped: memory`; the service answers the next request. **The job's memory+swap limit equals its memory limit, and the machine has no active swap** | the same program with no cgroup limit survives | | yes (v2) | yes (v1) |
| B2 | **Memory, the founder's point:** four processes, each under its own per-process backstop, together over the job's limit, are stopped; the swap limit is checked as in B1 | the same four with only `setrlimit` all survive | | yes (v2) | yes (v1) |
| B3 | **One out-of-memory kill ends the whole job:** no process of that job is left afterwards (v1 kills one at a time); the swap limit is checked as in B1 | -- | | yes (v2) | yes (v1) |
| B4 | **CPU budget (kills):** a family of spinning processes is killed when their CPU seconds together pass the job's budget, before the wall clock, as `stopped: cpu` | one process alone stays under it | | yes | yes |
| B5 | Wall clock: a program that ignores SIGTERM and starts children is killed at the limit, **and no process of that job user is left** | -- | | yes | yes |
| B6 | Fork bomb: stopped at the job's `pids` cap; the machine stays healthy | -- | | yes | yes |
| B7 | Answer flood: the supervisor stops reading at the cap and kills the job | -- | | yes | yes |
| B8 | Disk: `/work`, LibreOffice's home and `/dev/shm` each stop at their size, and their pages count against the job's memory; **`/tmp` counts against `/work`'s cap (with 128 MiB kept in `/work`, `/tmp` stops at what is left; Q14)** | -- | | yes | yes |
| B9 | One process per file: two jobs in a row have different processes and namespaces, and nothing of the first is left in `/work` or the home | -- | yes (process only) | yes | yes |
| B10 | The job can't raise its own limit or leave its cgroup (the files are hidden; and refused even if visible) | root can change it (lowers it to 200 MiB; v1 refuses raising a limit above memory+swap, Fly run 1) | | yes | yes |
| B11 | **No leak, through the real request path** (and nothing found by the after-job backstop) (founder, 2026-10-01): the service's own Service and Handler on a loopback port, 100 real POSTs (a text order, a catalog table, the committed `po.doc`), two at a time; all answer 200, none stopped or crashed; afterwards every job cgroup is gone and the slot users own no process. **While they run, every job's namespace PID 1 is sampled from outside and must be the reaper (`sandbox_init`), never the parser or LibreOffice,** and a running job must be seen as its PID 2 | -- | | yes | yes |
| B12 | **CPU quota (slows):** a job spinning on every core gets at most one CPU's worth of time over a timed window, and the cgroup reports throttling; the other slot's job still finishes | the same program with no quota uses more than one CPU | | yes | yes |
| B13 | **LibreOffice runs in the sandbox** (added 2026-10-01 after `po.doc` gave DOC-017 in the real image): the committed `po.doc`, converted inside a real job with `conversion.py`'s flags and Word 97 filter, exits 0 and produces a file; reports its exit status and stderr tail, whether `/tmp` and `/var/tmp` are writable, and the same run with LibreOffice's pipe pointed at the work directory (evidence) | -- | | yes | yes |
| B14 | **A parser's own exit code is never read as a kill** (founder, 2026-10-01): a job that exits 137 by itself, in the real sandbox, is `crashed: exit_137`, with no OOM kill and the reaper's record `{"exited": 137}`; unit tests cover every combination (`apps/parse/tests/test_classify.py`) | -- | | yes | yes |
| B15 | **Exit 70 after the hardened message is a parser failure** (founder, Q13): `crashed: exit_70`, with the confirmation `{"hardened": true, "seccomp": true}` in the evidence | -- | | yes | yes |
| B16 | **A self-reported memory error vs a real overrun** (Q13): a parser's own MemoryError (exit 71) is `crashed: self_reported_memory_error` with no OOM kill; 1200 MiB under the default 768 MiB cgroup is `stopped: memory` with an OOM kill | -- | | yes | yes |
| A15 | **noexec** (founder, 2026-10-01): a binary copied into `/work`, `/tmp`, `/var/tmp` and `/lohome` can't run, directly (EACCES) or through the dynamic loader | the same copy outside the sandbox runs | | yes | yes |
| **C. Hostile files** (through `POST /v1/document`, the real path. After each one, a known-good PO parses correctly, so the service is shown healthy) | | | | | |
| C1 | Zip bomb; XXE payload; oversized image; 500-page PDF; `.exe` renamed `.pdf`; password-protected PDF; a `.zip` holding a valid PO. Each gets the catalog code it gets today | -- | yes | yes | yes |
| C2 | A malformed file of **each** Tier 2 format (`.doc`, `.xls`, `.tif`, `.heic`, `.msg`, `.odt`, `.ods`) that kills or hangs its converter: a clean `rejected` or `stopped`, never a crash | -- | yes | yes | yes |
| **D. Real orders still parse** | | | | | |
| D1 | A real PO in every Tier 1 and Tier 2 format parses **under the limits**, which is what fixes the numbers in item 3 | -- | yes | yes | yes |
| D2 | **Same answer as today:** before anything moves, today's code runs over the whole fixture set and its parts are saved; the service must return the same parts, byte for byte | -- | yes | yes | |
| D3 | Catalog files (`.xlsx`, `.xls`, `.csv`) through `POST /v1/table` give the same rows as today | -- | yes | yes | yes |
| **E. Refusal to start** | | | | | |
| E1 | Production mode with namespaces unavailable: CI runs the same image **without** `--privileged`, so creating them really fails; the process exits and never opens its port | the privileged run starts | | yes | |
| E2 | `PARSE_ISOLATION=off` with `FLY_APP_NAME` set: refused | without `FLY_APP_NAME`, dev mode starts | yes | yes | |
| E3 | The startup log shows every canary check, line by line, ending in PASS | -- | | yes | yes |
| E4 | No working memory cap: CI runs the image with the cgroup filesystem mounted read-only, so no job cgroup can be created; the canary fails and the service never opens its port. *If Docker won't allow that mount, I come back to the founder before using any other way* | the normal run starts | | yes | |
| E5 | **Any test-only switch that weakens a cap is refused when `FLY_APP_NAME` is set:** the service exits at startup | without `FLY_APP_NAME` the switch is accepted (CI only) | yes | yes | |
| **F. The worker's side** | | | | | |
| F1 | Each row of item 6's table, against the running service: unreachable, busy, `stopped`, dropped mid-request, a malformed answer | -- | yes (dev service) | yes (real image) | |
| F2 | The dependency-graph test (as built, a static scan): no import of a parsing library (PDF, image, Word, legacy Excel, LibreOffice's bindings) in the worker's, core's or the API's product code, none in their requirements; **the XML guard** (founder, 2026-10-01): no direct lxml import and no `openpyxl.load_workbook` use in `apps/api/app` or `apps/worker/app` | -- | yes | yes | |
| F3 | D-163: a worker killed during a (stubbed) model call leaves a started row; the sweep writes its outcome with the counted input cost; every cost reader ignores started rows | -- | yes (DB, staging) | yes (DB) | |
| F4 | **The retry rule (6a):** `decide()` over every sequence in 6a's table, including crash, crash, timeout (3 tries, not 4) and lost, lost | today's code fails the crash-crash-timeout case | yes | yes | |
| F5 | A lost try against the real service (CI kills the parse container mid-request): recorded at once, and retried or failed by `decide()` within seconds; the next try after a restart succeeds | -- | | yes | |
| F6 | **DOC-029's founder alert fires at most once per tenant per UTC day** (founder's change 4): many stopped files in one tenant, one alert; another tenant gets its own | -- | yes (DB, staging) | yes (DB) | |
| F7 | **`parse_service_unavailable` fires at most once per UTC hour across all tenants:** failures from several tenants in one hour, one alert; the next hour, a new one | -- | yes (DB, staging) | yes (DB) | |
| **G. End to end on Fly staging** | | | | | |
| G1 | An upload through the staging API (over `fly proxy`), through the real Upstash, the worker on Fly and the parse service, to `needs_review`: the golden fixture, a `.doc`, and a scanned image. Paid: a few cents | -- | | | yes |
| G2 | A catalog import through the same path | -- | | | yes |
| G3 | **Celery's Redis command count** (founder): an idle hour and a busy one, reported | -- | | | yes |

The live golden run (`pytest -m live_api`, 3 tests) also runs at the 3c
checkpoint: the content sent to the model is built on a new path, even if
D2 shows it is unchanged.

**3c build -- CI GREEN 2026-10-01 on `0be5422` (branch `phase55/stage3c-design`); `0034` not yet on staging; Fly staging run owed before the merge (RUNBOOK 8.1).**
Built and tested on this machine (dev mode; never counted as proof of
isolation). Nothing is on Fly.

**First CI run (`09c68ed`, `899efdb`, 2026-10-01): failed, three causes,
none of them a sandbox finding; all three fixed in the next commit.**
1. **The image didn't build** (parse and worker jobs; `apt-get` exit 100).
   I pinned LibreOffice at `25.2.3-2+deb13u6` from the main archive without
   checking the security archive, which already had `deb13u7`; apt takes the
   security version for the writer's exact-version dependencies, so u6
   can't be installed. Pin moved to `deb13u7` (RUNBOOK 8.5's case, before
   the first build). The other pinned packages have no newer security
   version. So the self-tests, E1, E4 and the canary haven't run yet.
2. **Core: 5 xlsx export failures** (pinned digest, OS-independence, the
   hostile round trip). I took `lxml` out of the worker's and the API's
   locks with the parsing libraries. openpyxl writes the .xlsx through lxml
   when it's installed and through its own writer otherwise, and the bytes
   differ. My local venv still had lxml, so it passed here. Reproduced
   locally with `OPENPYXL_LXML=False` (5 failed, 36 passed). `lxml==6.1.3`
   restored exactly as on `main` (both locks, both requirements files,
   core's `files` extra), with a comment saying why; it is never handed a
   file. `pip-audit`: no known vulnerabilities.
3. **API: 33 failures** (catalog import, and onboarding and card billing,
   which need a committed catalog). The catalog tests call the worker's
   parse step (`catalog_import.run_parse`), which now reads through the
   parse service, and the API job started none. Locally my dev service was
   running. The API job now builds and starts the real image, as the
   worker job does.

**Second CI run (`5a00275`): the image built; the sandbox ran for the first
time.** core 699 tests, 0 failed; api 571, 0 failed; web and web-live
passed; parse unit 110, 0 failed. In the real sandbox (cgroup v2 on the
runner; Fly is v1, so the v1 path is proven only on Fly): canary 5 of 5;
S 4 of 4; A all PASS except A-net IPv6, NO-CONTROL (the runner has no IPv6
outside either, so the control can't show the block matters; Fly has IPv6);
E1 and E4 refused to start as they should. Failures:
1. **B5 and B11:** processes owned by the slot user still exist after the
   job, while the job's cgroup was empty and removed (B5: 6, B11: 8; the 100
   normal jobs in B11 added none). *Not yet diagnosed.* My guess, unproven:
   zombies of killed jobs. When the cgroup is killed, `unshare` dies with
   its child, the child is reparented to the container's PID 1 (the
   self-test program, with no init to reap it), and its `/proc` entry stays.
   The next run reports each one's state, parent and PID 1 before anything
   is changed.
2. **`po.doc` gives DOC-017 in the real image** (parse HTTP test, the
   worker's preview test, and F5, whose kill window needs a slow LibreOffice
   parse but got a 0.2 s refusal). LibreOffice fails inside the sandbox; the
   answer says only DOC-017. New self-test B13 reports LibreOffice's own
   exit status and stderr from inside a real job.
3. **18 worker database tests: documents stay in `processing`** (H1, H3, M1,
   M3, F3, cost, the time-limit tests). These need migration `0034`, so on
   this machine they skip (staging doesn't have it); CI is their first run.
   **Cause: a cascade from item 2, not 18 defects.** F5 kills the parse
   container and restarted it only after its assertions; `po.doc` came back
   in 0.2 s, an assertion failed, and the container stayed down. Every one
   of the 18 runs after F5 (they sort after it), found no service, and their
   documents were held in `processing` as designed for an unreachable
   service (`release_after_storage_outage`). F5 now restarts the container
   in a `finally`. Their real result is the next run's.

**Third and fourth runs (`bbc0932`, `fb8e538`).** With F5 restarting the
container, worker 131 tests, 2 failed, both `po.doc` (the preview test and
F5's kill window); the 18 database tests pass. The evidence:
- **B5/B11: zombies.** Every survivor (6 in B5, 8 in B11) is
  `State: Z (zombie)`, named `python` (a sandbox's PID 1), parent PID 1,
  and PID 1 is `python -m parse_service.selftest all`. Cause: killing a
  job's cgroup kills `unshare` and its child together; the child is
  reparented to PID 1, which never waits for it. **Fix (founder: reaping in
  the supervisor):** the service and the self-test make themselves the
  child subreaper (`prctl(PR_SET_CHILD_SUBREAPER)`), so on Fly, where they
  aren't PID 1, the orphans still come to them; after a killed job the
  launcher reaps what was in the job's cgroup, recording each one's state,
  cgroup and PID namespace in `evidence["reaped"]` just before. Only our own
  children can be reaped, and a live job's own child never is (a lock covers
  starting a job and reaping). B5 and B11 are unchanged: still any process
  owned by a slot user after the job fails them. The founder's cgroup /
  namespace question is answered by the next run's `reaped` evidence: a
  process outside the job's cgroup or PID namespace there is a B10 failure,
  and I stop and report it.
- **B13: `ERROR: no valid pipe path found.`**, exit 1 in 0.0 s. That is
  LibreOffice failing to find a writable directory for its IPC pipe; it
  tries `/tmp`, then `/var/tmp`, and the sandbox's root is read-only (the
  canary shows `/tmp: EROFS`). **Correction to the founder's reading:** S4
  as built doesn't convert a `.doc`; its row says "(with D1)", and D1 is
  the failing parity test. S4 checks `clone3`, a thread and a fork. So S4
  doesn't rule out the filter; B13's error is what points at the pipe
  directory. B13's variants (above) test that next run. **No fix is made:**
  whichever it is (a pipe path inside `/work`, or a writable `/tmp`) comes
  to the founder first.

**Fifth run (`749a5cd`).** core 700 tests, 0 failed; api 580, 0 failed (the
token tests run, none skipped); worker 132, 2 failed (both `po.doc`); web
and web-live passed; parse unit 110, 0 failed; parse HTTP 56, 1 failed
(`po.doc`). Self-tests: canary 5 of 5, S 4 of 4, A all PASS with A-net IPv6
reported **NOT-RUN**, B 12 of 15 (B5, B11, B13 failed). Earlier runs for the
record: `bbc0932` core 699/0, api 575/0, worker 131/20 (the F5 cascade);
`fb8e538` core 699/0, api 575/0, worker 131/2.
- **B5/B11: the founder's question answered, and a cleanup bug, not an
  escape.** The reaper took the killed jobs' sandbox PID 1s (B1 reaped pid
  101, B5's own job 165). Three zombies remained, each in **its job's own
  cgroup** (`/docflow-jobs/job-0-... (deleted)`) and **its job's own PID
  namespace** (`pid:[4026532468]` and others, never the supervisor's
  `pid:[4026532403]`); B10 passed. They came from OOM-killed jobs: a
  process the kernel has killed leaves `cgroup.procs` at once, so the
  snapshot I reaped from never listed it. **Fixed:** after every job the
  launcher reaps every orphan that is now its own child (any child that is
  not a live job's Popen child), and after any limit stop it keeps looking
  for 5 s. The checks are unchanged.
- **B13: LibreOffice ignores the pipe redirect.** `/tmp` and `/var/tmp` are
  EROFS, `TMPDIR` is `/work/tmp`; as `conversion.py` runs it, with
  `OSL_SOCKET_PATH` in the environment, and with `-env:OSL_SOCKET_PATH`, all
  three fail in 0.0 s with `ERROR: no valid pipe path found.` My reading:
  the message comes from LibreOffice's launcher (`oosplash`, which `soffice`
  runs first), which checks only `/tmp` and `/var/tmp` and exits before the
  office itself (`soffice.bin`), which honours `OSL_SOCKET_PATH`, ever
  starts. The next run adds the evidence for that: `soffice.bin` run
  directly with `OSL_SOCKET_PATH` in `/work` (retrying once on exit 81, the
  restart oosplash would do on a fresh profile). **Q14, the founder's
  decision; nothing is changed until then:**
  1. *Call `soffice.bin` directly, pipe in `/work`.* No filesystem change.
     Costs: we bypass LibreOffice's own launcher, so we take on its one job
     that matters here (restart on exit 81), and depend on an internal path
     (`/usr/lib/libreoffice/program/soffice.bin`).
  2. *Bind-mount `/work/tmp` over `/tmp` inside the sandbox.* LibreOffice
     runs as shipped. Not a second writable area: `/tmp` would be the same
     tmpfs as `/work`, under `/work`'s existing cap (B8 would add a line
     showing writes to `/tmp` count against it). It is a change to the
     sandbox's filesystem view.
  **Founder's decision (2026-10-01): option 2, decided without waiting**:
  option 1 ties DocFlow to LibreOffice internals (the binary's path, the
  exit-81 restart) that the weekly Debian snapshot can change; option 2
  runs LibreOffice as shipped, and each job's `/tmp` is its own, under
  `/work`'s cap.

**Sixth run (`974ad9e`).** core 700, 0 failed; api 580, 0 failed; worker
132, 2 failed (`po.doc`); web and web-live passed; parse unit 116, 0
failed; parse HTTP 56, 1 failed (`po.doc`). Self-tests: canary 5 of 5;
**S 5 of 5, including `S4:real-doc-under-the-filter-alone`** (the real
`po.doc` converts under the seccomp filter alone in 0.9 s, PO number found:
the filter is not the cause, the filesystem is); A all PASS, IPv6 NOT-RUN;
B: B5 PASS (nothing left after the wall-clock kill), B14 PASS
(`crashed: exit_137`, no OOM kill, record `{"exited": 137}`); B13 and B11
failed:
- **B13:** the three redirects fail as before; `soffice.bin` run directly
  with `OSL_SOCKET_PATH` asked for its restart (exit 81) and then converted
  the file (`input.docx`). That confirms the diagnosis; option 2 was chosen
  regardless.
- **B11: a sampling artifact, not a leak.** 100 requests, all 200 (txt and
  csv `ok`; `.doc` rejected, the known cause); no job cgroup, no slot-user
  process left. Every live namespace PID 1 sampled was `sandbox_init`; the
  parser was PID 2. But 6 PID 1 samples had an empty command line: a
  process caught at the instant it exits (its memory released). Fixed: the
  sampler records each one's state and names those `<exiting: State>`,
  reported, not judged.

**Built after the sixth run (founder, 2026-10-01):**
- **Q14 option 2:** `sandbox_init` bind-mounts the job's `/work/tmp` over
  `/tmp` and `/var/tmp` (`config.TMP_DIRS`), `nosuid,nodev` like `/work`,
  after the rest of the root is read-only. Tests: A14 (`/tmp` and
  `/var/tmp` writable and the same filesystem as `/work`; `/`, `/usr`,
  `/etc`, `/opt` still EROFS; the canary checks the same); B8 (`/tmp` runs
  out at `/work`'s cap, and with 128 MiB kept in `/work` at what is left);
  A11 (a concurrent job doesn't see the first job's `/tmp` marker); D1 and
  the worker's `po.doc` tests should now pass. B13 keeps only the run as
  `conversion.py` does it.
- **The reaper is event-driven (no timing window):** SIGCHLD wakes a reaper
  thread (through Python's wakeup fd, written at C level whichever thread
  takes the signal, so it never waits on a blocked main thread), which
  waits by pid for every zombie child in a job cgroup that is not a live
  job's own process. **Not `waitpid(-1)` literally:** that would also take
  the exit status of the supervisor's legitimate children (the jobs' Popen
  processes, the self-test's other subprocesses), and Python then reports a
  crashed child as exit 0. The 5-second window is gone. **The backstop:**
  after every job that ended normally (which leaves no orphans), that job's
  orphans, if any, are reaped, counted (`BACKSTOP_FOUND`) and logged; B11
  fails if any appear during its 100 requests. B5 waits (at most 30 s,
  reported) until the killed job's processes are gone and shows the latest
  SIGCHLD reaps.

Done, in the agreed order:
1. **D2 baseline** (`b819e8b`): 41 committed fixtures under
   `apps/parse/tests/fixtures/` (22 positive, 4 catalog tables, 15 hostile)
   and `baseline.json`, recorded by the worker's own code before anything
   moved.
2. **`decide()`** (`c4c8a88`, Q9): the 3-try cap first. One correction to
   the design's wording: the 4-try case was not an accident. 3a's own test
   asserted it (`(3, [3]) -> retry  # the timeout's one retry beats the
   attempt count`). The founder's Q9 reverses it.
3. **The parse service** (`apps/parse/`): supervisor, launcher, the
   `sandbox_init` root step, the job, the seccomp filter, cgroups v1/v2,
   the canary and self-test programs, the Dockerfile, and the moved parsing
   code (`git mv`, so history follows). The worker uses `parse_client`; the
   parsing libraries are out of the worker's and the API's requirements and
   lock files (only removals; versions unchanged; fresh-venv `pip check`
   clean).
4. **CI**: a new `parse` job (E1, E4, E3, the HTTP tests, the A/S/B
   self-tests, audit) and the worker job testing against the real image.
5. **D-163 and migration `0034`** (Q5): written. Not yet applied to staging.

Local results (database off where noted):
- parse 110 passed, with every positive and hostile fixture giving
  byte-identical answers to the baseline, both in-process and through the
  dev service over HTTP (56 passed);
- core 698 passed, 1 skipped;
- worker 83 passed, 46 skipped (database, prefork, and F5, which needs CI's
  container);
- API: the same 24 environment-only failures as the base branch (20
  `test_console_mfa`, 4 `test_d170_clock`), nothing else.

**Where the build differs from the design or adds to it. The founder
reviews each before the merge:**
- **A crashed job:** a job that dies some other way than our limits, such
  as a parser's segfault, or SIGSYS from the seccomp filter. The design
  covered an answer that fails the worker's checks but not this. Built the
  same way: DOC-005 to the customer, plus a founder alert, once per tenant
  per cause per day.
- **The DOC-029 alert isn't in `FAILURE_ALERTS`.** That map may only hold
  codes whose wording tells the customer "DocFlow has been alerted" (a test
  enforces it), and the founder's DOC-029 wording doesn't. So the alert is
  raised explicitly (`founder_alerts.raise_parse_alert`), with the agreed
  once-per-tenant-per-day limit.
- **The retry decision moved** to its own pure module,
  `docflow_core.retry_rules`. `stuck_documents` may be imported only by
  the sweep task (its cross-tenant session; `test_rls_flags.py`), and the
  worker's task now applies the same decision. The sweep re-exports it, so
  existing callers are unchanged.
- **`extraction_runs.counted_input_tokens`**, a column in `0034` beside
  `run_state` and `started_run_id`. The design said the started row
  carries the counted tokens, but not where. Kept apart from
  `input_tokens`, so a reader that sums tokens can never count a call
  twice.
- **The client's patience:** "never got in" (no connection, or a 503) is
  retried for up to 45 s before the document waits, because a stopped Fly
  machine is started by the first request. A 502 or 504 from Fly's proxy
  counts as "got in, never came out". A 401 (token mismatch) is "never got
  in", is not retried, and raises the hourly alert with reason
  `unauthorized`.
- **`TABLE_FORMATS` moved into `file_types`** (standard library only), so
  the parse image doesn't need `catalog_import` and SQLAlchemy.
  `catalog_import.TABLE_FORMATS` still exists.
- **The image's Debian sources (changed 2026-10-01, founder):** first built
  with exact version pins against the live archive, which broke on the first
  security update (above) and could have held a vulnerable LibreOffice in
  place. Now: the base image by digest, and apt reads only a dated
  snapshot of the archive (snapshot.debian.org, main and security,
  `DEBIAN_SNAPSHOT` in the Dockerfile, first date `20261001T000000Z`), with
  an `apt-get upgrade` so the base image's own packages also come from that
  date. No version pins: the date is the pin. A weekly workflow
  (`.github/workflows/debian-snapshot.yml`, Mondays 06:00 UTC) runs the whole
  CI workflow on today's date; green, it pushes `deps/debian-snapshot-<date>`
  with the new date and leaves the PR link as a notice; red, it pushes
  nothing and GitHub emails the failure. The founder opens the PR; 8.1 (Fly
  staging) comes before production. Recorded in D-183. *My choice, yours to
  change:* the job pushes a branch rather than only testing, because the
  date has to change in the repository for the tested image to be the one
  that ships. *Risk:* snapshot.debian.org is slower and less available than
  the live archive; apt retries 5 times, and an outage fails a build rather
  than changing what it installs.
- **lxml stays in the worker and API (founder: listed here).** openpyxl
  writes the .xlsx export through lxml when it's installed, and the bytes
  differ without it (first CI run, above), so `lxml==6.1.3` is pinned beside
  openpyxl as on `main`. It is never handed a file. **F2 as built:** a
  static scan of import statements and of the requirements files. lxml was
  never on its lists, so F2 neither changed nor failed. It never checked
  what is installed, so "no parsing library importable" overstated it. Now
  (founder, 2026-10-01): F2 keeps the import check for the real parsing
  libraries (PDF, image, Word, legacy Excel, and now LibreOffice's bindings
  `uno`/`unohelper`), and adds **the XML guard**: any direct lxml import
  (including `importlib.import_module("lxml...")`) or any
  `openpyxl.load_workbook` use, in any form, in `apps/api/app` or
  `apps/worker/app` fails CI, with a test that each form is caught. **Q11
  (founder): the guard covers `packages/core` too, with one exception named
  by file and function:** `exports.py::parse_xlsx`, which reads back the
  .xlsx `build_export` rendered a moment before (Section 7.4's round trip,
  EXP-004). The guard requires exactly its two uses there (the import and
  one `load_workbook(...)` call) and none anywhere else, so a second call
  fails the build (checked by planting one: both tests failed; file
  restored). What it is handed is checked twice: statically
  (`test_parse_xlsx_reads_only_the_bytes_just_rendered`: `load_workbook`
  gets `io.BytesIO(content)`, `content` is `render(...)`'s result in the
  same function, `PARSERS[fmt](content)` is the only call, and nothing
  outside exports.py names `parse_xlsx` or `PARSERS`) and at runtime
  (core's `test_the_xlsx_read_back_gets_only_the_bytes_just_rendered`: it
  receives the very bytes object `render()` returned, and a path string is
  a TypeError).
- **The parse token in CI (founder, 2026-10-01):** the API process never
  holds `PARSE_SERVICE_TOKEN`. In `5a00275` the API job did put it in the
  test step's environment, so the API's settings had it in that run. Now
  the step has `WORKER_HARNESS_PARSE_TOKEN` instead, read only by
  `as_the_worker` (apps/api/tests/conftest.py) around the worker's own
  `catalog_import.run_parse`. `test_parse_token_boundary.py`: the API's
  settings (environment and root .env) carry no token; a request made with
  the API's settings is refused by the real service as `unauthorized`, and
  the same request with the worker's token gets in (the control). Checked
  locally against a tokened dev service, and both tests fail when the API
  is given the token. **Q12 (founder): on Fly the API refuses to start
  holding the token**, as the parse service refuses to start without
  isolation: `parse_token_startup_refusal()` runs when `app.main` is
  imported, before the app exists, so uvicorn never opens its port.
  `fly_app_name` is a new settings field (Fly sets `FLY_APP_NAME`; never in
  .env). Tests: the rule in all four combinations, and a real `uvicorn`
  start with `FLY_APP_NAME` and the token exits non-zero, prints the refusal
  and never accepts a connection; the control (no token) opens its port.
- **Self-test additions:** B5 and B11 report each leftover process's name,
  state, parent, cgroup and PID namespace, PID 1 and the supervisor's own
  PID namespace. **B13** runs LibreOffice once inside the real sandbox, the
  way `conversion.py` does (that run decides the check), plus evidence:
  whether `/tmp` and `/var/tmp` are writable there, and the same run with
  LibreOffice's pipe pointed at the work directory (`OSL_SOCKET_PATH` as an
  environment variable, and as `-env:`). Evidence only: no limit or
  filesystem changed.
- **NOT-RUN, not NO-CONTROL (founder, 2026-10-01):** a check whose control
  didn't hold is reported as `NOT-RUN` ("proved nothing on this machine"),
  never as a pass, in the self-test output and its annotations ("parse
  self-tests: NOT RUN (no control)"). A-net IPv6 on CI is NOT-RUN: **IPv6
  isolation is unproven until the Fly run (8.1) passes it**, where the
  machine has IPv6. The startup canary's IPv6 line shows "blocked" on the
  runner too, but there it proves the same nothing; the canary is a gate,
  not proof.
- **The orphan reaper (B5/B11; founder: fix by reaping, never by filtering
  the check):** see the third run below.
- **PID 1 inside a real job is now a reaper (founder, 2026-10-01).** It was
  the parser: `sandbox_init` ran as the namespace's PID 1 and then `exec`'d
  into `setpriv` and the job, so anything LibreOffice left behind was
  reparented to a parser that never waits; the self-tests hid it, because
  their own PID 1 behaved differently. Now `sandbox_init` forks the job and
  stays PID 1 (`_reap_as_init`): it drops to the slot's user,
  no-new-privileges, non-dumpable, the job's seccomp filter, and only
  waits; when the job exits it exits, which ends every other process in the
  namespace. B11 checks it through the real request path (the row above).
- **How a job ended is decided from the supervisor's own records (founder,
  2026-10-01).** First built as "a signal N arrives as exit 128+N", which a
  parser exiting 137 by itself would imitate. Now: the supervisor's kill
  decisions (wall clock, CPU budget, answer cap) and the cgroup's OOM count
  (memory) decide; only when none applies does the parser's own end, and
  then only as a parser failure. That end comes from the reaper as one
  record on a pipe only the reaper holds (the job's copy is closed before
  it starts; the reaper is non-dumpable, so the job can't reach it through
  `/proc`): `{"exited": n}` or `{"signaled": n}`. No record, or
  `{"sandbox": "failed"}`, is the service failing to isolate. B14 and
  `test_classify.py`. **Q13 (founder, 2026-10-01): no exit code decides an
  outcome any more.**
  - **70 is replaced by a positive confirmation.** `sandbox_init`'s forked
    child does the hardening itself (no supplementary groups; empty
    bounding, inheritable and ambient capability sets; the slot's uid and
    gid; no-new-privileges; the seccomp filter), checks every one in
    `/proc/self/status`, then sends `{"hardened": true, "seccomp": ...}` on
    the private pipe, closes its copy, and only then `exec`s the parser.
    The supervisor accepts only that exact first line, for the seccomp
    setting it asked for (`seccomp` is false only for a self-test control).
    No confirmation: `isolation_failed` (the document waits; the hourly
    alert). With it, any exit is the parser's, whatever the code. This
    **replaces `setpriv`**, which did the same steps but couldn't confirm
    them before the parser started; the canary and A9 check the result as
    before. The reaper's later end record only names how a parser failed
    (`exit_N` or `signal_N`); hardened with no end record is `reaper_lost`
    (isolation failed).
  - **71 is a plain parser failure** (`crashed: self_reported_memory_error`,
    DOC-005, logged as `job_self_reported_memory_error`). Real overruns get
    DOC-029 from the cgroup: `RLIMIT_AS_BYTES` (2 GiB) is above
    `JOB_MEMORY_BYTES` (768 MiB), and a unit test keeps it that way. *One
    case to know:* a parser asking for more than 2 GiB in a single
    allocation is refused by RLIMIT_AS before touching memory, so it ends as
    the parser's own MemoryError (DOC-005), not DOC-029.
  - Tests: `test_classify.py` (12); B15 (a parser exiting 70 after the
    hardened message is `crashed: exit_70`); B16 (a self-reported
    MemoryError is `crashed`, no OOM kill; 1200 MiB under the default
    768 MiB cgroup is `stopped: memory` with an OOM kill).
- **`noexec` (founder, 2026-10-01)** on `/work` and the `/tmp`, `/var/tmp`
  bind mounts. A15: `/bin/true` copied into each can't run directly
  (EACCES) or through the dynamic loader (which must map the file
  executable); the control, the same copy outside the sandbox, runs.
  Interpreted code (`python file.py`) is not stopped by noexec. Whether
  LibreOffice still converts with it: B13, D1 and the worker's `po.doc`
  tests (all PASS on `0be5422`). **`/lohome` too (founder, 2026-10-01):** the
  profile folder holds settings, not programs; A15 covers it and B13/D1
  show `po.doc` still converts.
- **B11's exiting samples (founder):** accepted only when that same pid was
  earlier sampled live as `sandbox_init`; any other is listed and fails B11.

**Seventh run (`b013b6d`, the bind mount and the SIGCHLD reaper).** core 700,
0 failed; api 580, 0 failed; parse unit 116, 0 failed; **parse HTTP 56, 0
failed (D1 `po.doc` parity passes)**; worker 132, 1 failed (F5; **the
worker's `po.doc` preview test passes**); web and web-live passed.
Self-tests: canary 5 of 5; S 5 of 5; A all PASS (A14 and A11 with the bind
mount), IPv6 NOT-RUN; **B: B5, B11 and B13 PASS** (nothing left after the
kill or the 100 real requests; LibreOffice converts in the sandbox); B8
failed. Both failures were test bugs, fixed in the next commit:
- **F5** killed the container after a fixed 1 s; `po.doc` now parses in
  under a second, so the answer came first. Now it kills the moment
  `docker top` shows a parse job process in the container.
- **B8's new step** kept 128 MiB in `/work` as one file, over the 64 MiB
  per-file limit (RLIMIT_FSIZE): EFBIG, exit 1. Now four 32 MiB files.

**Eighth and ninth runs (`d1e1afe`, `0867e3f`).** `0867e3f`: core 700, 0
failed; api 580, 0 failed; **the parse job green for the first time**
(unit 122, HTTP 56, all 0 failed; canary 5 of 5, S 5 of 5, **B 17 of 17**,
A all PASS with IPv6 NOT-RUN; E1, E3, E4 as designed); web and web-live
passed; worker 132, 1 failed (F5). New checks all pass: A15 (`/work`:
EACCES directly, exit 127 through the loader; the control outside runs),
B13 with noexec (**LibreOffice still converts**), B14, B15 (`exit_70`), B16
(`self_reported_memory_error`, no OOM kill; the real overrun `memory`), B11
with the exiting rule.
- **F5, still a test problem:** `docker top` never showed the job process,
  so the kill waited 30 s and came after the answer. Now F5 watches the
  container's job cgroups straight from the runner (the container has its
  own cgroup namespace, so they are under its scope there): a job is seen
  the moment its cgroup is created, before the parser starts; if the
  directory isn't found, F5 says where it looked.

**`0034` applied on `docflow-staging` (founder, 2026-10-01).** Backup
`backup_0034` first (documents, extraction_runs; RLS on). Row counts before:
documents 105, extraction_runs 17; after: documents 105, extraction_runs 17;
live and backup match on both. `backup_0034` stays until 3c has merged and
run cleanly on staging for a few days, then it is dropped and recorded
(RUNBOOK 1.3). **Founder, 2026-10-01: drop it after 3c has run cleanly on
staging for 3 days** -- 3c merged 2026-10-01 20:53 UTC, so not before
**2026-10-04 20:53 UTC**. "Cleanly" is checked then and reported: the
staging suites green on `main`, and no `document_failed`, `document_stuck`,
`parse_service_unavailable` or `parse_seccomp_kill` alert on staging since
the merge. Then the founder runs the drop (RUNBOOK 1.3) and the date is
recorded here.

**Departures reviewed (founder, 2026-10-01): 14 approved as written; two
changed, built in the next commit:**
- **#1: SIGSYS is its own alert,** `parse_seccomp_kill`, raised for every
  seccomp kill, never rate-limited (one row per document or import, no
  daily window), from the worker and from catalog import (which raised no
  alert for a crashed parse before). The syscall number can't be had: the
  filter answers listed calls with EPERM and kills only a call from a
  foreign architecture, and KILL_PROCESS reports nothing to anyone but the
  kernel's audit log. The alert says so (`syscall: null`,
  `syscall_unavailable`). Test:
  `test_F6_every_seccomp_kill_alerts_and_says_why_there_is_no_syscall_number`.
- **#5: the 5xx status is named.** The unavailable reason for a 503 is now
  `http_503` (was `busy_or_isolation_failed`); a lost parse's DOC-022 alert
  detail carries `last_lost_reason` (`http_502`, `http_504`, or the read
  error), so a Fly start failure can be told apart from a parser crash.
  Tests: `test_parse_client.py`, `test_retry_rules_detail.py`. RUNBOOK 8.4.

**Fly staging stood up (2026-10-01, design item 13 step 6; RUNBOOK 8.1):**
- Created, empty (no machines, no IPs, no secrets): `docflow-parse-staging`,
  `docflow-worker-staging`, `docflow-api-staging`; and the Upstash database
  `docflow-staging-redis` (pay-as-you-go, eviction disabled so the queue
  never drops a task, no replicas, ProdPack and auto-upgrade declined).
- New deploy files: `apps/parse/fly.toml` (private: `--no-public-ips`, then
  one private Flycast IPv6; stopped when idle, started on request;
  shared-cpu-2x, 2 GB), `apps/worker/{Dockerfile,fly.toml}` (Celery worker
  only, no beat, no services, no IP), `apps/api/{Dockerfile,fly.toml}`
  (private only). The worker and API images use the parse image's base digest
  and the same `DEBIAN_SNAPSHOT`, run as an unprivileged user, and the weekly
  snapshot job now moves all three dates. All three images built on Fly's
  remote builder (build only).
- Waiting on the founder: the secrets (`Desktop/3c-fly-secrets.txt`, the
  design's list). **`DOCUMENT_URL_SIGNING_SECRET`** (API only; not on the
  design's list, so asked first): **Fly staging gets its own newly generated
  value** (`secrets.token_urlsafe(48)`), not the root .env's or Supabase
  staging's, and production another, so a link signed in one environment
  never works in another; the founder generates and sets it and never
  pastes it (founder, 2026-10-01).
- **Database logins (founder, 2026-10-01): the worker and the API on Fly
  each get their own login from F-1, never `docflow_app` shared, never
  `postgres` or the service role.** F-1 (3e: `docflow_worker`,
  `docflow_api`) isn't built, so those logins don't exist on staging; the
  local `.env` connects as `docflow_app` (NOBYPASSRLS, no CREATE) through
  Supabase's transaction pooler. So for now only the parse app gets its
  secret (`PARSE_SERVICE_TOKEN`), which is all the RUNBOOK 8.1 merge gate
  needs; the worker and the API wait for their logins. **Founder's call
  (2026-10-01): option (a), narrowed.** Only G moves to after 3e. N2 runs
  from outside Fly against the parse app alone; N3 uses a client machine
  in a throwaway app calling the parse app's Flycast address with no token
  (a 401 proves the request started the machine and the token is
  enforced), destroyed afterwards and recorded in the evidence. A4 runs now
  against a stand-in listener on the private network, control first, and
  must not come out NOT-RUN; it runs again against the real API and worker
  after 3e. **3c merge gate:** canary, every A/S/B self-test (IPv6 PASS),
  A4 against the stand-in, N1-N3, all on the Fly machine. **G and the
  real-target A4 gate the first worker/API deploy, not the 3c merge**
  (RUNBOOK 8.1). At that deploy a fresh `PARSE_SERVICE_TOKEN` is generated
  and set on the parse app and the worker together.
- `ovntvsmhekqefsrpjtih` confirmed by the founder as `docflow-staging`;
  `PARSE_SERVICE_TOKEN` set (staged) on `docflow-parse-staging`.
- Secrets are typed in a PowerShell window with history saving off
  (`Set-PSReadLineOption -HistorySaveStyle SaveNothing`; RUNBOOK 8.1).
- **Core on staging: `701 passed, 1 skipped`** -- the skip is
  `test_a_customers_own_signed_in_token_reaches_no_file`
  (`test_storage_bucket_live.py`): it mints a token with the project's JWT
  secret, which is blank on this machine (D-174); CI sets a throwaway one,
  so it runs there. **Worker on staging: `130 passed, 3 skipped`** -- the two
  prefork tests (Linux only) and F5 (needs CI's container); all three run in
  CI. **API on staging: `579 passed, 1 skipped, 3 deselected`** (580 in CI)
  -- the skip is `test_a_request_carrying_the_api_settings_is_refused`
  (`test_parse_token_boundary.py`): it needs a parse service holding a token
  (CI's API job); the dev service here has none. The 3 deselected are the
  `live_api` tests, excluded from every default run. That run is context
  only: the local dev parse service hit its 30-minute background limit
  partway through and was restarted (no test failed). **Founder: re-run the
  whole suite once with the service up; that run's line is the record.**
  **The record (full re-run, dev parse service up throughout): `579
  passed, 1 skipped, 3 deselected, 653 warnings in 2498.67s (0:41:38)`**,
  exit 0; the same skip (`test_a_request_carrying_the_api_settings_is_refused`)
  and the 3 `live_api` deselections.
  **The 1 skip, and why:** `test_parse_token_boundary.py::test_a_request_carrying_the_api_settings_is_refused`
  sends a request with the API's own settings to a parse service that holds
  a token and expects it refused; the local dev parse service runs without a
  token, so the test skips here and runs in CI's API job (which starts the
  real image with a token).

**Fly staging run 1 (2026-10-01, image `sha256:f9719965`, machine
`863662ce743978`, kernel 6.12.105-fly, cgroup v1): merge gate NOT met.**
Evidence: `docs/spikes/3c-fly-staging/2026-10-01-sha256-f9719965/`.
- PASS: canary (5 of 5, cgroup v1), N1 (one private IPv6, nothing else), N2
  (no public A/AAAA; Fly's edge with the name forced gives the same 301 as
  a made-up name; nothing reached the app), N3 (stopped machine started by
  a no-token Flycast POST, 401 in 5.2 s), every A check with **A-net IPv6
  PASS** and A3 Upstash PASS, every S check, 15 of 18 B checks (B13
  LibreOffice, B16 and the rest).
- **A4 (stand-in):** NOT-RUN in the full run (the control's 3 s connect
  timed out; the stand-in logged no connection); PASS in a group-A rerun
  two minutes later (control reached, blocked inside). Cause of the first
  timeout not known.
- **B5 FAIL, B11 FAIL: zombie orphans of killed jobs are not reaped on
  cgroup v1.** Every survivor is a zombie (state Z), uid of a slot user,
  in its own job PID namespace, parent the supervisor; `/proc/<pid>/cgroup`
  shows `/` on every controller, so the reaper (which reaps only zombies
  whose cgroup contains `/docflow-jobs/`) and the after-job backstop never
  match them. The live server has the same leak: pid 712, left by its own
  boot canary's memory kill, still a zombie of the server (pid 643) nine
  minutes later. B11's 100 requests left nothing new (no job cgroups, the
  backstop found nothing); it failed on the 7 earlier zombies. Per the
  founder's rule (a survivor outside its cgroup is reported as a B10
  failure), stopped and reported.
- **B10 NOT-RUN:** the real check held (inside ENOENT, slot user EACCES),
  but its control (root raises the limit to 300 MiB) is refused on v1 by
  design: `memory.limit_in_bytes` can't exceed `memory.memsw.limit_in_bytes`
  (256 MiB). Shown on the machine with a throwaway cgroup (file 05).
- Throwaway app `docflow-3c-probe` destroyed; parse machine stopped.

**Founder, 2026-10-01: stopping was right; all three fixes approved.**
1. **Reaper:** a zombie child is reaped when it is in a PID namespace other
   than the supervisor's and runs as a slot's user, and isn't a live job's
   own process -- never by cgroup path. Jobs are created and registered in
   `_live_children` under the reaper's lock (already so; now tested: a job
   that dies before it's registered keeps its own exit status, and a
   control shows an unregistered child would lose it). A killed job's
   orphan is also reaped by `run_job` before it returns, so no slot starts
   a job with one left. The backstop looks at its own slot's user. The "/"
   unit test feeds Fly run 1's view. B5 and B11 on Fly are the proof.
   **One change from the approved wording, flagged to the founder:** "a
   slot's user" alone would also match the self-tests' own `setpriv`
   children (they run as slot 0's user, in the supervisor's namespace), so
   the PID-namespace condition is added.
2. **B10 control:** root lowers the limit to 200 MiB.
3. **A4:** the control's connect timeout is 10 s (inside stays 3 s).
   **Cause checked first:** the founder's guess (the stand-in auto-stopped)
   doesn't fit run 1. The stand-in had no services, so Fly's proxy could
   neither stop nor start it, and its restart policy was `no`. It was
   listed `started` at 19:49:52 (run 1 began 19:50:52), accepted
   connections at 19:53:06 and 19:54:45, and was `started` at teardown
   (19:56:40), so it was never stopped. Cause still unknown; the stand-in
   was 3 minutes old, so a new machine's private route not yet everywhere
   is a guess. Run 2 records the stand-in's state and log right before and
   after the self-tests. Run 1's NOT-RUN stays in its evidence; the gate is
   every item passing in one run.
   **Run 2: no retries.** The probe makes exactly one connect per target,
   with no retry loop; run 2's control reached the stand-in on that single
   attempt (the stand-in logged one connection, at 20:31:18, five seconds
   after the run began, the canary running first).
   **OPEN: the cause of run 1's A4 control timeout** (3 s, stand-in up and
   never stopped). Not explained; the new-machine-route guess is unproven.
   Watch for it in every later Fly run (the 10 s control would show it as a
   slow "reached", not a NOT-RUN).
- `--ha=false` is staging only; production's machine count is a Phase 6
  decision (RUNBOOK 8.2). A7 against the real port 8100 is preferred.

**CI green on `0f5850b`** (after one fix to the reaper's control test,
which had looked for the reap in the wrong place): every job passed; parse
unit 128 and HTTP 56, 0 failed; self-tests canary 5, S 5, B 17 (B5 leaves
none), A with only IPv6 NOT-RUN (CI's runner). **API staging full re-run:**
recorded above.

**Fly staging run 2 (2026-10-01, image `sha256:acc99434`, same machine):
the 3c merge gate is MET -- every item passed in one run.** Evidence:
`docs/spikes/3c-fly-staging/2026-10-01-sha256-acc99434/`. Canary 5 of 5
(cgroup v1); the live server held no zombie after boot; N1; every A check
with **A-net IPv6 PASS**, A3 Upstash, A7 against the live port 8100, and
**A4 against the stand-in PASS** (stand-in created first, `started` before
and after, one connection logged: the control's); every S check; **all 18
B checks**, B5 reaping exactly the root-cgroup zombies run 1 left, B10's
control succeeding, B11 clean; N2; N3 (401 in 5.5 s, machine started by the
request). Throwaway app destroyed, parse machine stopped. G and the
real-target A4 wait for the first worker/API deploy (after 3e).

**Tenth run (`0be5422`): GREEN.** core 700 tests, 0 failed; api 580, 0 failed; worker 132, 0 failed (F5 included); parse unit 122, 0 failed; parse HTTP 56, 0 failed (D1 parity on every fixture, `po.doc` included); web and web-live passed; dependency audits clean. Self-tests in the real sandbox: canary 5 of 5, A 13 of 13 PASS with A-net IPv6 NOT-RUN, S 5 of 5, B 17 of 17; E1, E3 and E4 as designed. Recorded in D-183. Next: the
founder backs up and applies `0034` on staging (the PR text leads with it:
`Desktop/PR-stage3c-parse-service.md`), I run the staging suites, the
founder reviews the departures (above), then the Fly staging run (RUNBOOK
8.1: all the self-tests; IPv6 and cgroup v1 are proven only there).
**Founder, 2026-10-01: the Fly run comes before the merge**, not only before
production (RUNBOOK 8.1 and the PR checklist updated); and `/lohome` is
noexec too.
- **The service log gets LibreOffice's real error (founder):** a rejected
  conversion's reason, with the last 300 characters of LibreOffice's own
  stderr, goes to the job's stderr and from there to the service's log
  (`job_rejected kind=... code=... detail=...`). The answer to the worker,
  and so everything a tenant sees, still carries DOC-017 alone.
- **The seed script and the walkthrough file maker** now use the parse
  service (dev) and the parse venv.
