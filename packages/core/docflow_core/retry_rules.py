"""
How many tries a document gets, and when trying stops (Stage 3a, changed in
Stage 3c by the founder's Q9). Pure: no database, no queue, so the stuck
sweep and the worker's task apply the same decision, and its table is tested
without either.
"""

from __future__ import annotations

from docflow_core.constants import MAX_PROCESSING_ATTEMPTS

STUCK_CODE = "DOC-022"
CAUSE_TIMEOUT = "timeout"
CAUSE_WORKER_STOPPED = "worker_stopped"


def decide(
    processing_attempts: int,
    timeout_attempts: list[int],
    *,
    max_attempts: int = MAX_PROCESSING_ATTEMPTS,
    parse_lost_attempts: list[int] | tuple[int, ...] = (),
) -> tuple[str, str | None]:
    """
    What to do with a document stuck in `processing`, or whose parse request
    was lost: ("retry", None) or ("fail", cause). One decision, used by the
    sweep and by the worker, in this order (Stage 3c, item 6a; founder Q9,
    2026-10-01):

    | State                                                   | Outcome                     |
    |---------------------------------------------------------|-----------------------------|
    | max_attempts tries used                                 | fail: `timeout` if any try  |
    |                                                         | was timeout-class, else     |
    |                                                         | `worker_stopped`            |
    | a timeout-class try, and a try has run since the first  | fail, cause timeout         |
    | otherwise                                               | retry                       |

    A timeout-class try is one that hit the document task's hard time limit
    (`timeout_attempts`, Stage 3a) or whose parse request got in and never
    came out (`parse_lost_attempts`, Stage 3c): either way the file is the
    suspect, and a file that hangs or kills a parser will do it again.

    The guarantee, whatever the mix: at most `max_attempts` tries in all, and
    at most one try after the first timeout-class try. Before 3c, a timeout's
    one retry was allowed past the cap (crash, crash, timeout got a 4th try);
    the founder reversed that (Q9). Pure, so the table is tested without a
    database.
    """
    timeout_class = set(timeout_attempts) | set(parse_lost_attempts)
    if processing_attempts >= max_attempts:
        return ("fail", CAUSE_TIMEOUT if timeout_class else CAUSE_WORKER_STOPPED)
    if timeout_class and processing_attempts > min(timeout_class):
        return ("fail", CAUSE_TIMEOUT)
    return ("retry", None)


def failure_detail(attempts: int, timeout_attempts: list[int], parse_lost_attempts: list[int]) -> dict:
    """The DOC-022 alert's numbers (Section 7.10: numbers only, it is
    emailed). `cause_detail` says which timeout-class tries there were, so a
    lost parse is never hidden behind a task time limit (Stage 3c, Q9)."""
    detail: dict = {"attempts": attempts, "timed_out_attempts": timeout_attempts}
    if parse_lost_attempts:
        detail["parse_lost_attempts"] = parse_lost_attempts
    kinds = [name for name, tries in (("task_time_limit", timeout_attempts),
                                      ("parse_lost", parse_lost_attempts)) if tries]
    if kinds:
        detail["cause_detail"] = "+".join(kinds)
    return detail
