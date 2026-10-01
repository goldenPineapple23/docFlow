"""
Turn the parse service's self-test output into GitHub annotations, so the
evidence can be read from a run's summary page and its check-runs API
(job logs and artifacts need a token this machine doesn't have; RUNBOOK 1.6:
the suite reports its own evidence).

  python scripts/ci/selftest_annotations.py parse-selftest.txt

- every FAIL or NOT-RUN line: an ::error:: or ::warning:: annotation with
  its evidence (grouped, since GitHub shows at most 10 of each per step). A
  NOT-RUN check's control didn't hold, so it proved nothing here; it is
  reported as not run, never as a pass;
- one ::notice:: per group (canary, A, S, B) listing what passed, and the
  cgroup layout the run used.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict

LINE = re.compile(r"^RESULT (\S+) (PASS|FAIL|NOT-RUN|INFO) -- (.*)$")
LIMIT = 3800  # annotation messages are truncated around 4 KB


def emit(kind: str, title: str, message: str) -> None:
    message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{kind} title={title}::{message[:LIMIT]}")


def main(path: str) -> int:
    text = open(path, encoding="utf-8", errors="replace").read()
    header = next((line for line in text.splitlines() if line.startswith("cgroup v")), "cgroup layout unknown")
    by_group: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for line in text.splitlines():
        match = LINE.match(line)
        if not match:
            continue
        check_id, verdict, evidence = match.groups()
        group = "canary" if check_id.startswith("canary") else check_id[0]
        by_group[group].append((check_id, verdict, evidence))
    if not by_group:
        emit("error", "parse self-tests", "no RESULT lines found -- the self-tests did not run")
        return 1
    bad = [(g, c, v, e) for g, rows in by_group.items() for c, v, e in rows if v in ("FAIL", "NOT-RUN")]
    for index in range(0, len(bad), max(1, -(-len(bad) // 9))):
        chunk = bad[index : index + max(1, -(-len(bad) // 9))]
        kind = "error" if any(v == "FAIL" for _, _, v, _ in chunk) else "warning"
        title = "parse self-tests: failed" if kind == "error" else "parse self-tests: NOT RUN (no control)"
        emit(kind, title, "\n".join(f"{v} {c}: {e}" for _, c, v, e in chunk))
    for group, rows in sorted(by_group.items()):
        counts = defaultdict(int)
        for _, verdict, _ in rows:
            counts[verdict] += 1
        summary = ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
        body = "\n".join(f"{v} {c}: {e[:300]}" for c, v, e in rows)
        emit("notice", f"parse self-tests {group} ({summary})", f"{header}\n{body}")
    return 1 if any(v == "FAIL" for _, _, v, _ in bad) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
