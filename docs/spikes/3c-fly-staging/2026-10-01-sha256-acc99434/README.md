# Fly staging run 2 -- 2026-10-01, image sha256:acc99434

RUNBOOK 8.1, second run on `docflow-parse-staging` (machine
`863662ce743978`, iad, shared-cpu-2x, 2 GB, kernel 6.12.105-fly, cgroup v1),
after the fixes for run 1 (`f725c55`, `0f5850b`; CI green on `0f5850b`).
Deployed from `28ca444` (docs only after `0f5850b`).

**Verdict: the 3c merge gate is met. Every item passed in this one run.**

| Item | Result | File |
|---|---|---|
| Canary (boot) | PASS, all 5, cgroup v1 | 00 |
| The live server's own zombies | none (run 1: its canary's orphan was still a zombie 9 minutes after boot) | 00 |
| N1 | PASS: one private IPv6 (`fdaa:cb:76d5:0:1::3`), nothing else | 00 |
| A (all) | PASS, **A-net IPv6 PASS**, A3 Upstash PASS, A7 against the live server's port 8100 PASS | 02 |
| A4 stand-in | PASS: control reached, blocked inside. The stand-in (created 20:27:46Z, listening 20:28:06Z) was `started` before (20:31:09Z) and after (20:32:37Z) the self-tests and logged exactly one connection, the parse machine's control, at 20:31:18Z | 01, 02 |
| S (all) | PASS | 02 |
| B (all 18) | PASS. B5: no slot-user process left; it reaped three zombies shown in the root cgroup (uid 10001, job PID namespace), the ones run 1 left. B10: control (root lowers the limit) succeeded. B11: 100 requests, 200 each, nothing left, backstop found nothing | 02 |
| N2 | PASS: no public A/AAAA for the name (control fly.io resolves); private addresses unreachable from outside; Fly's edge with the name forced gives the same 301 as a made-up name; nothing but the keep-awake polls reached the app | 03 |
| N3 | PASS: machine stopped; a no-token POST over Flycast started it ("Starting machine" 1 s later, canary PASS) and got 401 in 5.5 s | 04 |

Throwaway app `docflow-3c-probe` (machines `28744540b15958` a4-standin,
`8747905c42d758` keepawake, `78111123c3e418` n3-client) was destroyed at
20:34:39Z; the parse machine was stopped.

Not run here (founder, 2026-10-01): G, and A4 against the real staging API
and worker. They gate the first worker/API deploy, after Stage 3e.
