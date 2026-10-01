# Fly staging run 1 -- 2026-10-01, image sha256:f9719965

RUNBOOK 8.1, first run on `docflow-parse-staging` (machine
`863662ce743978`, iad, shared-cpu-2x, 2 GB, kernel 6.12.105-fly, cgroup v1).
Branch `phase55/stage3c-design` at `21e9de7`.

**Verdict: the merge gate is NOT met.** B5 and B11 FAIL, B10 NOT-RUN.
Nothing merges until a fix is approved, CI is green, and a full run on Fly
passes (RUNBOOK 8.1).

| Item | Result | File |
|---|---|---|
| Canary (boot) | PASS, all 5, cgroup v1 | 00 |
| N1 | PASS: one private IPv6 (`fdaa:cb:76d5:0:1::3`), nothing else | 00 |
| A (all) | PASS, A-net IPv6 PASS; A4 NOT-RUN in the full run (control timed out), PASS in the A rerun | 01, 02 |
| A3 Upstash | PASS (reached outside, blocked inside) | 01, 02 |
| A4 stand-in | full run NOT-RUN; rerun PASS (control reached; the stand-in logged one connection, the parse machine's own) | 01, 02 |
| S (all) | PASS | 01 |
| B | 15 PASS; **B5 FAIL, B11 FAIL** (zombie orphans not reaped on cgroup v1); **B10 NOT-RUN** (its root control can't raise a v1 memory limit above the swap limit) | 01, 05 |
| N2 | PASS: the public name has no A/AAAA in two public resolvers (control fly.io resolves); Fly's edge with the name forced answers the same 301 as for a made-up app name; nothing reached the parse app | 03 |
| N3 | PASS: parse machine stopped; a no-token POST over Flycast from a throwaway app started it (proxy "Starting machine" 1 s later, canary PASS) and got 401 in 5.2 s | 04 |

Throwaway app `docflow-3c-probe` (machines `d8911d61a47178` a4-standin,
`18577297c13078` keepawake, `837013a7949448` n3-client; two failed launches
`827514a7939218`, `18577296f19538` destroyed at once) was destroyed at
19:56:40Z. The keep-awake client polled `/health` over Flycast during the
self-tests so the machine did not auto-stop (SSH is not proxy traffic).

`run-on-machine.sh` is the script run on the machine; targets are resolved
there, outside the sandbox, before any job runs.
