"""How the launcher decides how a job ended (Stage 3c; founder, 2026-10-01).

From its own records first: its kill decisions (wall clock, CPU budget,
answer cap) and the cgroup's OOM count (memory). The parser's own end -- the
reaper's private record, never an exit status the parser could imitate --
decides only when none of those applies, and then only as "the parser
failed on its own". B14 runs the same case inside the real sandbox.
"""

from __future__ import annotations

from parse_service.launcher import _classify, _end_record, _job_status


def _from_record(record: dict | None, cause: str | None = None):
    status, isolation_cause = _job_status(record)
    return _classify(status, cause, b"", isolation_cause=isolation_cause)


def test_a_parser_that_exits_137_itself_is_a_parser_failure_not_a_kill():
    result = _from_record({"exited": 137})
    assert (result.outcome, result.cause) == ("crashed", "exit_137")


def test_exit_codes_that_look_like_signals_are_never_read_as_signals():
    for code in (128 + 9, 128 + 31, 255):
        assert _from_record({"exited": code}).cause == f"exit_{code}"


def test_a_death_by_signal_is_named_from_the_reapers_record():
    result = _from_record({"signaled": 31})  # seccomp's SIGSYS (S5)
    assert (result.outcome, result.cause) == ("crashed", "signal_31")


def test_the_supervisors_own_decisions_win_over_anything_the_job_reports():
    for cause in ("memory", "cpu", "wall_clock", "output_too_large"):
        result = _from_record({"exited": 137}, cause=cause)
        assert (result.outcome, result.cause) == ("stopped", cause)
    # A killed job's reaper dies with it and writes nothing: still the kill.
    assert _from_record(None, cause="wall_clock").cause == "wall_clock"


def test_no_record_or_a_failed_sandbox_is_the_service_failing_to_isolate():
    assert (_from_record(None).outcome, _from_record(None).cause) == ("isolation_failed", "no_end_record")
    failed = _from_record({"sandbox": "failed"})
    assert (failed.outcome, failed.cause) == ("isolation_failed", "sandbox_init")
    assert _from_record({"exited": "137"}).cause == "no_end_record"  # not a number: not trusted


def test_the_record_is_read_strictly():
    assert _end_record(b'{"exited": 0}\n') == {"exited": 0}
    assert _end_record(b"") is None
    assert _end_record(b"not json") is None
    assert _end_record(b"[1]") is None
