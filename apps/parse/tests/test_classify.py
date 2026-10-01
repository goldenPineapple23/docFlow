"""How the launcher decides how a job ended (Stage 3c; founder, 2026-10-01).

From its own records first: its kill decisions (wall clock, CPU budget,
answer cap) and the cgroup's OOM count (memory). Isolation is confirmed only
by the hardened child's first message on the private pipe, sent before the
parser existed (Q13). After that, the parser's end -- the reaper's record,
never an exit status -- is only ever "the parser failed on its own".
B14, B15 and B16 run these cases inside the real sandbox.
"""

from __future__ import annotations

from parse_service import config
from parse_service.launcher import _classify, _job_status, _records

HARDENED = {"hardened": True, "seccomp": True}


def _from(records: list[dict], cause: str | None = None, seccomp: bool = True):
    status, isolation_cause = _job_status(records, seccomp_expected=seccomp)
    return _classify(status, cause, b"", isolation_cause=isolation_cause)


def test_a_parser_that_exits_137_itself_is_a_parser_failure_not_a_kill():
    result = _from([HARDENED, {"exited": 137}])
    assert (result.outcome, result.cause) == ("crashed", "exit_137")


def test_exit_codes_that_look_like_signals_are_never_read_as_signals():
    for code in (128 + 9, 128 + 31, 255):
        assert _from([HARDENED, {"exited": code}]).cause == f"exit_{code}"


def test_a_death_by_signal_is_named_from_the_reapers_record():
    result = _from([HARDENED, {"signaled": 31}])  # seccomp's SIGSYS (S5)
    assert (result.outcome, result.cause) == ("crashed", "signal_31")


def test_a_parser_exiting_70_after_the_hardened_message_is_a_crash_not_an_isolation_failure():
    result = _from([HARDENED, {"exited": 70}])
    assert (result.outcome, result.cause) == ("crashed", "exit_70")


def test_without_the_hardened_message_isolation_failed_whatever_came_next():
    for records in ([], [{"exited": 0}], [{"exited": 70}], [{"signaled": 9}]):
        result = _from(records)
        assert (result.outcome, result.cause) == ("isolation_failed", "not_hardened"), records
    assert _from([{"sandbox": "failed"}]).cause == "sandbox_init"


def test_only_the_exact_first_message_counts():
    # the wrong seccomp setting for what was asked, or anything extra
    assert _from([{"hardened": True, "seccomp": False}, {"exited": 0}]).cause == "not_hardened"
    assert _from([{"hardened": True, "seccomp": True, "x": 1}, {"exited": 0}]).cause == "not_hardened"
    assert _from([{"exited": 0}, HARDENED]).cause == "not_hardened"  # second place is too late
    # a self-test control that asked for no filter
    assert _from([{"hardened": True, "seccomp": False}, {"exited": 3}], seccomp=False).cause == "exit_3"


def test_hardened_but_no_end_record_is_the_sandbox_failing():
    assert (_from([HARDENED]).outcome, _from([HARDENED]).cause) == ("isolation_failed", "reaper_lost")
    assert _from([HARDENED, {"exited": "137"}]).cause == "reaper_lost"  # not a number: not trusted
    assert _from([HARDENED, {"exited": True}]).cause == "reaper_lost"


def test_a_self_reported_memory_error_is_a_parser_failure_not_doc_029():
    result = _from([HARDENED, {"exited": 71}])
    assert (result.outcome, result.cause) == ("crashed", "self_reported_memory_error")


def test_a_real_memory_overrun_is_named_from_the_cgroup():
    result = _from([HARDENED, {"exited": 71}], cause="memory")
    assert (result.outcome, result.cause) == ("stopped", "memory")


def test_the_per_process_backstop_sits_above_the_jobs_cgroup_limit():
    """So a real overrun reaches the cgroup (DOC-029) before RLIMIT_AS."""
    assert config.RLIMIT_AS_BYTES >= config.JOB_MEMORY_BYTES


def test_the_supervisors_own_decisions_win_over_anything_the_job_reports():
    for cause in ("memory", "cpu", "wall_clock", "output_too_large"):
        result = _from([HARDENED, {"exited": 137}], cause=cause)
        assert (result.outcome, result.cause) == ("stopped", cause)
    assert _from([], cause="wall_clock").cause == "wall_clock"  # a killed sandbox writes nothing


def test_the_records_are_read_strictly():
    assert _records(b'{"hardened": true, "seccomp": true}\n{"exited": 0}\n') == [HARDENED, {"exited": 0}]
    assert _records(b"") == []
    assert _records(b"not json\n{}") == []
    assert _records(b"[1]") == []
    assert len(_records(b'{"a": 1}\n{"b": 2}\n{"c": 3}\n')) == 2  # nothing past the second line
