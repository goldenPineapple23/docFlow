# Fly staging gate for PR 1b-i -- 2026-10-16, image sha256:2e5d20af

RUNBOOK 8.1, gate only (founder, 2026-10-16: no A4, no N1-N3, no throwaway
app), on `docflow-parse-staging` (machine `863662ce743978`, iad,
shared-cpu-2x, 2 GB, cgroup v1). Deployed from branch
`phase55/stage4-pr1b-i-parse-error` at `a8dd48c`, a clean tree, after its
push run 38919537369 was green.

**Verdict: every line PASS. A-net IPv6 PASS.**

| Item | Result | File |
|---|---|---|
| Before | `docflow-worker-staging` has no machines; the parse machine stopped, shared-cpu-2x 2048 MB, as `fly.toml` says | 00 |
| Deploy | 15:09:32Z to 15:12:35Z, exit 0; image `deployment-01M52M5R8K2Q22H1M79GYGE4ZS`, digest `sha256:2e5d20af192cd8cd70e3927ad7e5f13afb3fb8298bbbbda725ff764f4ab5381a`, 214 MB; the deploy left the machine stopped | 00 |
| Canary (boot) | PASS, all 5, cgroup v1; port open 15:12:50Z | 00 |
| A (all run) | PASS, **A-net IPv6 PASS**, A3 Upstash PASS, A7 against the live server's port 8100 PASS | 01 |
| S (all) | PASS; S3 INFO (its control, as in the 3c runs) | 01 |
| B (all 18) | PASS | 01 |
| Not run (founder) | A4 stand-in, N1, N2, N3 | -- |

The list of checks is the 3c passing run's (`2026-10-01-sha256-acc99434`)
line for line, less A4 and the throwaway app's DNS name.

Times: machine started 15:12:47Z (`fly machine start`), self-tests
15:13:09Z to 15:14:27Z over SSH, stopped by the Fly proxy (auto-stop) at
15:19:53Z (exit 15:19:54Z, `requested_stop=true`). Deploy start to stopped:
10 min 22 s; the machine ran 7 min 7 s. The worker was not started, scaled
or deployed.

`run-on-machine.sh` is the script run on the machine; targets are resolved
there, outside the sandbox, before any job runs.
