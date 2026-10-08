# The first worker deploy on Fly staging -- steps 9 to 15, 2026-10-07 and 2026-10-08

RUNBOOK 9.7, steps 9 (G1, G2, G3), 10 (A4), 11 (memory), 12 (the four
heartbeat drills), 13 (budget), 14 (the 500 + 1 run) and 15 (shutdown), on
`docflow-worker-staging`, `docflow-api-staging` and `docflow-parse-staging`
(iad). Steps 2 to 8 are DECISIONS D-194. The record is DECISIONS D-196 and
CHECKPOINTS.md, Stage 3, "The first worker deploy".

Deployed from `main` `cec8f0b` on both days (worker images
`deployment-01M4B84Y0T7ZC1S1SD6E2H8DRR` and
`deployment-01M4C0DCEDY90A2MYTJY6A1XMT` on 2026-10-07,
`deployment-01M4E12V4BCCNH60TGAGKSA96G` on 2026-10-08). On 2026-10-08
`origin/main` was already `e990f10`; the difference is D-195, documents only.

**Verdict: every gate passed. All 501 documents of the 500 + 1 run reached
`needs_review`, and the founder accepted the run (2026-10-08).**

| Item | Result | Files |
|---|---|---|
| G1: three uploads through the deployed API to `needs_review` | PASS: golden `sample_po.txt` 24.4 s, `po.doc` 25.3 s, `po.png` 14.1 s; $0.0466; golden values 35 of 35 | `2026-10-07/g1_*` |
| G2: a catalog import by the same path | PASS: import `03d27e3c` committed, 4 rows, parse job 0.311 s | `2026-10-07/g2_read.*` |
| G3: Redis commands, an idle hour and a busy one | idle 13,600 to 13,700 an hour; busy hour within noise of idle; 19 of 20 orders (the 20th upload was cut off by the API machine stopping itself) | `2026-10-07/g3_*` |
| A4 against the real API and worker | PASS: API private address port 8000 and the worker's port 22, outside reached, inside blocked; all 22 group-A checks PASS | `2026-10-07/a4*` |
| Memory, 1 GB worker | 22 MB scan: documents process 328 MiB, machine 699 of 962 MiB; a 24.4 MB scan failed at the model's request limit (DOC-008) | `2026-10-07/mem_*`, `worker_memory_*` |
| Drill 1: worker stopped | PASS: "down" e-mail 10 minutes after the last ping (mark 12) | founder's screenshots; D-196 |
| Drill 2: worker started | PASS: "up" e-mail at the first ping | `2026-10-07/worker_start_drill2*` |
| Drill 3: three starts in an hour | PASS: `worker_restarting` raised at the third start, e-mail held | `2026-10-07/drill3_*` |
| Drill 4: documents worker not taking work | PASS: `/fail` sent at 20 min 24 s unclaimed | `2026-10-07/drill4_*` |
| The 500 + 1 run | PASS: 501 of 501 `needs_review`, $6.2675, about 8.5 s a document, largest unclaimed age 4.34 s; the single order claimed ahead of 332 waiting documents, 14.0 s from save to processed | `2026-10-08/load_*`, `plus_one_read.py`, `unclaimed_read.py`, `worker_live_2026-10-08.log.txt` |
| Two 22 MB scans back to back, 2 GB worker | both `needs_review`; documents process peaks 322 and 333 MiB (its own high-water mark 359 MiB), 330 MiB held afterwards | `2026-10-08/mem_*`, `worker_memory_samples_2026-10-08.txt` |

**About these files.** They are the scripts as run and their output, copied
from the session folders by `copy_evidence.py`'s rule: byte for byte, except
that every Redis URL is replaced by `redis://<redacted>` (Celery had already
masked its password), a UTF-8 byte-order mark is dropped, colour codes
are stripped from Fly's log lines, and git stores the line endings its own
way. `.log` files carry `.log.txt` because the
repository ignores `*.log`. The scripts print ids, statuses, times, token
counts, cost and memory figures only, never a document's content
(CLAUDE.md 7.10). Every tenant, buyer and order in them is fictional.

**Not here:** the generated scanned PDFs (22 to 24 MB each; the scripts that
make them are); the Healthchecks.io e-mails and the Upstash readings, which
are the founder's screenshots and are quoted in D-196; the logs of 2026-10-06
(steps 2 to 8, D-194).

**`load_500_plus_1.py` as stored is the file after the run started.** The
`--resume` mode and the `processed_at` reading were added at about 15:40Z,
while the first watching process was running; the run was handed to a second
process in that mode at 15:45Z (`load_run.log.txt` is the first process,
`load_run2.log.txt` the second). D-196 has the handover.
