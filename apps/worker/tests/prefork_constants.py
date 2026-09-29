"""
Constants shared by tests/prefork_app.py and test_time_limits_prefork.py.

They live here, not in prefork_app, because importing prefork_app patches the
real document task (a 3 s limit, a parser that hangs, a fake model). That is
meant only for the separate worker process; imported into pytest, it broke
every other test of the task.
"""

PREFORK_TEST_LIMIT_SECONDS = 3
HANG_MARKER = b"PREFORK-TEST-HANG"
