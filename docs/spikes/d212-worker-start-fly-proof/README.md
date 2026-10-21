# The D-212 worker-start proof on Fly staging -- 2026-10-21, image sha256:48760780

D-212 item 4's proof, run under three grants on `docflow-worker-staging`
(iad). It deployed PR #73's final head `e95b095` (the workers start without
mingle and gossip), stopped and restarted the worker machine with beat
running, and then scaled the app to 0. The record is DECISIONS D-213 item 4.

**Verdict: passed. At start 2, each worker received its first waiting task
within 2 s of `ready`: documents 0.005 s, dispatch 0.058 s.**

| Item | Result | Files |
|---|---|---|
| Before the deploy | two read-only `first_start_due.py` readings (16:33:00Z, 16:35:55Z): 0 model calls, 0 Stripe calls, 0 e-mails due; six counts 35, 4, 30, 35, 12, 15 | `transcribed_console_outputs.txt` blocks 1-2, `first_start_due.py` |
| Grant 1: deploy | 16:36:08Z to 16:37:05Z, exit 0; image `deployment-01M5FN3YGFXMT9J8F14838VTPX`; worker 1024 MB, beat 256 MB | `fly_deploy_e95b095.log`, `deploy_start.txt`, `task_bhopcx08p.output.txt` |
| Start 1 (not scored: no task waiting) | login check 16:37:05Z; dispatch 5.0 s, documents 4 min 35 s from `ready` to the first `Received task` | `logs_read1.txt` to `logs_read4.txt` |
| Database after the deploy | 0 alerts, 0 e-mails, 0 extraction runs; `worker_starts_last_hour` 1 | `transcribed_console_outputs.txt` block 3, `since_deploy.py` |
| Grant 2: stop | 16:52:16.6Z; exit 0 at 16:52:27Z (about 10 s) | `logs_stop.txt`, `logs_stop2.txt`, `task_bsh788jhz.output.txt`, block 4 |
| Beat while the worker was stopped | 15 sends: 12 dispatch, 3 sweeps at 16:56:45Z | `logs_beat.txt` |
| Start 2, the proof | started 16:58:28Z; documents 0.005 s, dispatch 0.058 s, both with a task waiting | `logs_start2.txt`, `task_bp4h0kmck.output.txt` |
| Database after start 2 | 0 alerts, 0 e-mails, 0 extraction runs; `worker_starts_last_hour` 2 | block 5 |
| Grant 3: scale-down | worker exit 0 in 9 s (SIGTERM at +6 s from `fly scale count`), beat exit 0 in 0 s; no machines left | `logs_down_8655604b9e4058.txt`, `logs_down_2879390f916548.txt`, blocks 6-7 |
| Database after the scale-down | 0 alerts, 0 e-mails, 0 extraction runs since 16:36Z; six counts +0 | block 8 |

`transcribed_console_outputs.txt` holds console output that was never saved
to a file, transcribed word for word from the session (it says so at its
head). The `task_*.output.txt` files are the outputs of background commands.

**Edited before commit (founder, 2026-10-21):** in `task_bx8vgfgan.output.txt`
and `task_bii1jenrr.output.txt`, the local scratchpad folder's full path (on
the founder's machine, under the user's Temp folder) was replaced with
`<scratchpad>`, once in each. Nothing else in the folder was changed. The
Upstash Redis host name and Fly's private `fdaa:` addresses are left as
captured.

**Secret scan before commit** (D-213 item 7):
- an exact match for every root-`.env` value of 8 characters or more, the
  passwords inside connection strings included
- patterns for tokens, keys, ping URLs, connection strings and e-mail
  addresses
- a read of every distinct line

No secret was found.
