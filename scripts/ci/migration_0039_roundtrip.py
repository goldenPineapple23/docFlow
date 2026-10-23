"""
Migration 0039, forward -> reverse -> forward, on the CI database (Phase 5.5
Stage 5, M7; DECISIONS.md D-214), in two steps of the core job.

0039 changes policies and SECURITY DEFINER grants, which 0036's round trip
(scripts/ci/migration_roundtrip.py) compares against 0036's committed
post-state. So 0039 is taken off first and put back after the older round
trips have run on the state they were written for:

  python migration_0039_roundtrip.py reverse   (before 0036's round trip)
    1. snapshot the forward state (scripts/ci/grants_snapshot.py); it must
       equal supabase/reverse/0039_post_snapshot.json;
    2. run supabase/reverse/0039_reverse.sql; the snapshot must equal
       supabase/reverse/0039_pre_snapshot.json (staging before 0039, read
       2026-10-21);

  python migration_0039_roundtrip.py forward   (after 0038's round trip)
    3. run 0039 again; the snapshot must equal step 1's (kept in
       0039-forward-state.json between the two steps, and uploaded by ci.yml).

CI only. Prints each difference and exits 1 on any. Each state's SHA-256 is
a GitHub annotation, and on a difference from a committed file so is the
state CI found (gzip + base64, in parts), so a committed file is never
changed by guesswork.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grants_snapshot import canonical, differences, load, sha256, snapshot
from migration_roundtrip import annotate_problems, annotate_state

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "supabase" / "migrations" / "0039_m7_alerts_and_data_api_roles.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0039_reverse.sql"
PRE = ROOT / "supabase" / "reverse" / "0039_pre_snapshot.json"
POST = ROOT / "supabase" / "reverse" / "0039_post_snapshot.json"

# Written to the job's working directory by the reverse step, read by the
# forward step, and uploaded by ci.yml.
FORWARD_FOUND = "0039-forward-state.json"


def _run(url: str, path: Path) -> None:
    import psycopg

    with psycopg.connect(url) as conn:  # one transaction, committed on exit
        conn.execute(path.read_text(encoding="utf-8"))


def _take(url: str) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(url, prepare_threshold=None) as conn:
        return snapshot(conn)


def _report(title: str, problems: list[str], found: dict[str, Any] | None) -> None:
    for line in problems:
        print(line)
    annotate_problems(title, problems)
    if problems and found is not None:
        annotate_state(f"{title} -- the state CI found", found)


def check_reverse_step(forward: dict[str, Any], reversed_: dict[str, Any], pre: dict[str, Any],
                       post: dict[str, Any]) -> list[str]:
    """Step 1 and 2's comparisons; empty when both hold."""
    post_problems = differences("forward vs committed post-0039", forward, post)
    pre_problems = differences("reverse vs staging pre-0039", reversed_, pre)
    print(
        f"::notice title=0039 forward state::sha256={sha256(forward)} committed {POST.name} "
        f"sha256={sha256(post)}"
    )
    print(
        f"::notice title=0039 reversed state::sha256={sha256(reversed_)} committed {PRE.name} "
        f"sha256={sha256(pre)}"
    )
    _report("0039 forward state differs from the committed one:", post_problems, forward)
    _report("0039 reverse does not restore staging's pre-0039 state:", pre_problems, reversed_)
    return post_problems + pre_problems


def check_forward_step(again: dict[str, Any], forward: dict[str, Any]) -> list[str]:
    """Step 3's comparison; empty when it holds."""
    problems = differences("forward again vs forward", again, forward)
    print(f"::notice title=0039 forward-again state::sha256={sha256(again)}")
    _report("0039 forward again differs from the first forward state:", problems, again)
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("reverse", "forward"):
        print(__doc__, file=sys.stderr)
        return 2
    url = os.environ["SUPERUSER_DATABASE_URL"]
    if argv[1] == "reverse":
        forward = _take(url)
        Path(FORWARD_FOUND).write_bytes(canonical(forward).encode("utf-8"))
        _run(url, REVERSE)
        reversed_ = _take(url)
        problems = check_reverse_step(forward, reversed_, load(PRE), load(POST))
    else:
        _run(url, FORWARD)
        problems = check_forward_step(_take(url), load(Path(FORWARD_FOUND)))
    print(f"migration_0039_roundtrip {argv[1]}: {len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
