# Fly staging gate for PR 1b-ii -- 2026-10-18, image sha256:e1ac59b6

RUNBOOK 8.1, gate only (founder, 2026-10-18: no A4, no N1-N3), on
`docflow-parse-staging` (machine `863662ce743978`, iad, shared-cpu-2x,
2 GB, cgroup v1). Deployed from branch
`phase55/stage4-pr1b-ii-unwrap-and-import` at `f8cc120`, a clean tree, after
PR #68's pull-request run 39169747110 was green on that head.

**Verdict: every line PASS. A-net IPv6 PASS.**

| Item | Result | File |
|---|---|---|
| Before | `docflow-worker-staging` has no machines; the parse machine stopped, shared-cpu-2x 2048 MB, as `fly.toml` says | 00 |
| Deploy | 23:48:55Z to 23:50:31Z, exit 0; image `deployment-01M58PP6C38ZXKS3M266HSNYRW`, digest `sha256:e1ac59b666501f073a36095a4751466c2d4e048ad88f3723e322cd7f5b226834`, 214 MB; the deploy left the machine stopped | 00 |
| Canary (boot) | PASS, all 5, cgroup v1; port open 23:51:04Z | 00 |
| A (all run) | PASS, **A-net IPv6 PASS**, A3 Upstash PASS, A7 against the live server's port 8100 PASS | 01 |
| S (all) | PASS; S3 INFO (its control, as in every earlier run) | 01 |
| B (all 17) | PASS | 01 |
| Not run (founder) | A4 stand-in, N1, N2, N3 | -- |

The summary is PR 1b-i's gate (`2026-10-16-sha256-2e5d20af`) line for
line: 48 lines, no difference. (That run's README says "B (all 18)"; its
own output has 17 B lines, as this one does.)

Times: machine started 23:51:00Z (`fly machine start`), self-tests
23:51:14Z to 23:52:32Z over SSH, stopped by the Fly proxy (auto-stop) at
23:56:19Z (exit 23:56:19.887Z, `requested_stop=true`). Deploy start to
stopped: 7 min 25 s; the machine ran 5 min 19 s. The worker and the API
were not started, scaled or deployed.

`run-on-machine.sh` is the script run on the machine; targets are resolved
there, outside the sandbox, before any job runs.
