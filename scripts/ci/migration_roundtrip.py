"""
Migration 0036, forward -> reverse -> forward, on the CI database (Stage 3e;
founder's Q5 condition 2: "the reverse script is tested, not just checked in").

Run after `supabase start` has applied every migration, 0036 included:
  1. snapshot the forward state; it must equal
     supabase/reverse/0036_post_snapshot.json, the committed state after
     0036 (founder, 2026-10-05), which is what staging's and production's
     cutovers compare against (RUNBOOK 10.2 step 5);
  2. run supabase/reverse/0036_reverse.sql; the snapshot must equal
     supabase/reverse/0036_pre_snapshot.json (staging before 0036, read
     2026-10-02), so the reverse restores exactly what was there;
  3. run 0036 again; the snapshot must equal step 1's.

docflow_app is created NOLOGIN first if it doesn't exist, because the
pre-0036 state grants it EXECUTE and the reverse grants it back.

CI only. Prints each difference and exits 1 on any. Both states' SHA-256s
are GitHub annotations on every run, so the run's summary page shows which
two states CI proved. On a difference from the committed post-0036 state,
the differences are annotations too, and so is the state CI actually found
(gzip + base64, in parts), so the reference is never changed by guesswork.
That state is also written to 0036-forward-state.json on every run, which
ci.yml keeps as an artifact.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_snapshot import canonical, differences, load, sha256, snapshot

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "supabase" / "migrations" / "0036_separate_logins.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0036_reverse.sql"
PRE = ROOT / "supabase" / "reverse" / "0036_pre_snapshot.json"
POST = ROOT / "supabase" / "reverse" / "0036_post_snapshot.json"

# Written to the job's working directory and uploaded by ci.yml.
FORWARD_FOUND = "0036-forward-state.json"

# GitHub shows at most 10 annotations of a kind per step.
_MAX_ANNOTATIONS = 10
_PART_CHARS = 3000


def _run(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:  # one transaction, committed on exit
        conn.execute(path.read_text(encoding="utf-8"))


def _take(url: str) -> dict[str, Any]:
    with psycopg.connect(url) as conn:
        return snapshot(conn)


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def encode_parts(state: dict[str, Any]) -> list[str]:
    """A snapshot as gzip + base64 text, cut into annotation-sized parts."""
    packed = base64.b64encode(gzip.compress(canonical(state).encode("utf-8"), mtime=0)).decode("ascii")
    return [packed[i : i + _PART_CHARS] for i in range(0, len(packed), _PART_CHARS)]


def decode_parts(parts: list[str]) -> dict[str, Any]:
    return json.loads(gzip.decompress(base64.b64decode("".join(parts))).decode("utf-8"))


def annotate_problems(title: str, problems: list[str]) -> None:
    """The differences as error annotations, grouped to fit GitHub's limit."""
    if not problems:
        return
    size = max(1, -(-len(problems) // _MAX_ANNOTATIONS))
    for start in range(0, len(problems), size):
        chunk = problems[start : start + size]
        print(
            f"::error title={title} {start + 1}-{start + len(chunk)} of {len(problems)}::"
            + _escape("\n".join(chunk))
        )


def annotate_state(title: str, state: dict[str, Any]) -> None:
    parts = encode_parts(state)
    for index, part in enumerate(parts, start=1):
        print(f"::notice title={title} (gzip+base64) part {index}/{len(parts)}::{part}")


def check_states(
    forward: dict[str, Any],
    reversed_: dict[str, Any],
    again: dict[str, Any],
    pre: dict[str, Any],
    post: dict[str, Any] | None,
) -> int:
    """Compare the three states CI took with the two committed ones; print
    the result, with annotations. `post` is None when the file is missing."""
    print(
        f"::notice title=0036 pre-0036 state (after the reverse)::sha256={sha256(reversed_)} "
        f"committed {PRE.name} sha256={sha256(pre)} "
        f"policies={len(reversed_['policies'])} functions={len(reversed_['security_definer_functions'])}"
    )
    print(
        f"::notice title=0036 post-0036 state (forward)::sha256={sha256(forward)} "
        f"committed {POST.name} sha256={sha256(post) if post is not None else 'MISSING'} "
        f"policies={len(forward['policies'])} functions={len(forward['security_definer_functions'])}"
    )

    reverse_problems = differences("reverse vs staging pre-0036", reversed_, pre)
    again_problems = differences("forward again vs forward", again, forward)
    if post is None:
        post_problems = [f"forward vs committed post-0036: {POST.name} is not in the repository"]
    else:
        post_problems = differences("forward vs committed post-0036", forward, post)

    problems = post_problems + reverse_problems + again_problems
    for line in problems:
        print(line)
    annotate_problems("0036 forward state differs from the committed one:", post_problems)
    annotate_problems("0036 reverse does not restore the pre-0036 state:", reverse_problems)
    annotate_problems("0036 forward again differs from forward:", again_problems)
    if post_problems:
        # What CI found, so the reference can be reviewed against it.
        annotate_state("0036 forward state CI found", forward)
    print(
        f"migration_roundtrip: forward {len(forward['policies'])} policies, "
        f"reverse {len(reversed_['policies'])}, forward again {len(again['policies'])}; "
        f"{len(problems)} difference(s)"
    )
    return 1 if problems else 0


def main() -> int:
    url = os.environ["SUPERUSER_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'docflow_app'").fetchone() is None:
            conn.execute("CREATE ROLE docflow_app NOLOGIN NOBYPASSRLS")

    forward = _take(url)
    # Kept as a CI artifact (ci.yml), whether or not it matches the committed one.
    Path(FORWARD_FOUND).write_text(canonical(forward), encoding="utf-8", newline="
")
    _run(url, REVERSE)
    reversed_ = _take(url)
    _run(url, FORWARD)
    again = _take(url)

    return check_states(forward, reversed_, again, load(PRE), load(POST) if POST.exists() else None)


if __name__ == "__main__":
    sys.exit(main())
