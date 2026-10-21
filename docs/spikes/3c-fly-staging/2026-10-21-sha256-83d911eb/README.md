# Fly staging gate for PR B (Pillow 13.0.0) -- 2026-10-21, image sha256:83d911eb

RUNBOOK 8.1, gate only (no A4, no N1-N3), run under the founder's grant on
`docflow-parse-staging` (machine `863662ce743978`, iad, shared-cpu-2x,
2 GB, cgroup v1). Deployed from branch `phase55/stage5-pillow-13` at
`b1af531`, a clean tree, after PR #75's runs were green on that head. The
record is DECISIONS D-214 item 10.

**Verdict: every line PASS. A-net IPv6 PASS.**

| Item | Result | File |
|---|---|---|
| Before | `docflow-worker-staging` has no machines; the parse machine stopped, shared-cpu-2x 2048 MB, running 1b-ii's image | `00-state-before.txt` |
| Deploy | 19:32:19Z to 19:34:43Z, exit 0; image `deployment-01M5FZ6GM647TWWRFE982WWBRW`, digest `sha256:83d911ebe1d462a871abc0c1b46f6053aa723ce0de511bf36066941086198255`, 214 MB; the deploy left the machine stopped | `deploy.log` |
| Start | `fly machine start` at 19:34:59.7Z, exit 0 | `01-machine-start.txt` |
| Canary (boot) | PASS, all 5, cgroup v1 | `02-logs-boot.txt` |
| Machine | shared-cpu-2x, 2048 MB | `03-machine-status.txt` |
| A, S, B (`selftest all`, over SSH, 19:35:37Z to 19:36:57Z) | every line PASS, **A-net IPv6 PASS**; S3 INFO (its control, as in every run); B 17 | `04-selftest-all.txt` |
| Stop | stopped by the Fly proxy (auto-stop) at 19:42:17Z, exit 0; the poll saw it at 19:42:27Z | `05-wait-stopped.txt`, `06-state-after.txt` |
| After | the worker app still has no machines | `06-state-after.txt` |

The 48-line summary is 2026-10-18's (`2026-10-18-sha256-e1ac59b6`) line for
line. Deploy start to stopped: 9 min 59 s; the machine ran 7 min 17 s. The
worker and the API were not started, scaled or deployed.

`run-on-machine.sh` is the script run on the machine. It was sent
base64-encoded into `sh -c` over SSH, as the gate's plan said. Its header
still reads "PR 1b-ii gate"; only its date was changed for this run.

**Edited before commit (founder, 2026-10-21):** in `06-state-after.txt`,
line 33, the founder's e-mail address in Fly's `New SSH session email=...`
log line was replaced with `<founder e-mail>`. Nothing else in the folder
was changed. The Upstash Redis host name, the Supabase host names and Fly's
private `fdaa:` addresses are left as captured, as in the earlier gates
(D-214 item 11 parks stopping that).

**Secret scan before commit** (D-213 item 7's three parts):
- an exact match for every root-`.env` value of 8 characters or more, the
  passwords inside connection strings included
- patterns for tokens, keys, ping URLs, connection strings and e-mail
  addresses
- a read of every distinct line

No secret was found. The one private value found was the e-mail address
above. The scan's other match is the region inside the pooler host name in
`run-on-machine.sh`, the same line as in the committed 2026-10-01 and
2026-10-16 scripts.
