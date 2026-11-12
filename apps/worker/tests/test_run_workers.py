"""
app.run_workers keeps the document worker and the dispatch worker together
(founder's condition on the extra process, 2026-10-01). Real processes and
real signals; the children are small Python programs standing in for the two
Celery workers, so what is proven is the supervision itself. The Celery
command lines are checked in test_celery_app.py, and the dispatch worker's
real behaviour in test_dispatch_real_worker.py.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]

SHORT = [sys.executable, "-c", "import time; time.sleep(1)"]
LONG = [sys.executable, "-c", "import time; time.sleep(120)"]


def _supervisor(children: dict[str, list[str]]) -> subprocess.Popen:
    program = (
        "import sys\n"
        "from app.run_workers import supervise\n"
        f"sys.exit(supervise({children!r}))\n"
    )
    return subprocess.Popen([sys.executable, "-c", program], cwd=WORKER_ROOT)


def test_when_one_worker_exits_the_other_is_stopped_and_the_launcher_fails():
    """So Fly restarts the machine (`on-failure`) instead of leaving the
    document worker running with no dispatch process, or the other way round."""
    started = time.monotonic()
    supervisor = _supervisor({"dispatch": SHORT, "documents": LONG})
    code = supervisor.wait(timeout=60)
    took = time.monotonic() - started
    assert code == 1
    # The partner would have slept 120 s: it was stopped, not waited for.
    assert took < 45, took


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; Fly sends SIGINT to stop a machine")
def test_a_stop_signal_reaches_both_workers_and_the_launcher_exits_cleanly():
    supervisor = _supervisor({"dispatch": LONG, "documents": LONG})
    time.sleep(2)  # both children started
    assert supervisor.poll() is None
    started = time.monotonic()
    supervisor.send_signal(signal.SIGINT)
    code = supervisor.wait(timeout=60)
    assert code == 0  # a requested stop: Fly does not restart it
    assert time.monotonic() - started < 10  # both ended on the signal, not on the 30 s kill


# ── Stage 3e: what the launcher does before either worker starts ────────────


OTHER_LOGINS = ("ADMIN_DATABASE_URL", "STRIPE_DATABASE_URL", "API_DATABASE_URL")


def _env_file_with_all_four_logins(path: Path) -> Path:
    """What the founder's machine holds since the cutover (RUNBOOK 10.2 step
    4): one .env with every login. Made-up URLs; nothing connects to them."""
    names = ("DATABASE_URL", "API_DATABASE_URL", "WORKER_DATABASE_URL", *OTHER_LOGINS)
    path.write_text(
        "".join(f"{name}=postgresql://acme-test-not-a-real-url/{name.lower()}\n" for name in names),
        encoding="utf-8",
    )
    return path


def _read_settings_from(monkeypatch, env_file: Path | None) -> None:
    """Settings as a machine would give them: from this .env file (or none),
    with the other services' logins absent from the environment."""
    from docflow_core.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", env_file)
    for name in OTHER_LOGINS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


@pytest.fixture(params=["no .env file", "a .env file holding all four logins"])
def launcher(request, monkeypatch, tmp_path):
    """app.run_workers with its database calls and supervise() recorded, not run.

    The settings these tests see hold none of the other services' logins,
    wherever a machine keeps them. Removing them from the environment is not
    enough: the settings are also read from the root .env, and since the
    cutover the founder's .env holds all four. On 2026-10-05 (RUNBOOK 10.2
    step 5) two of these tests failed on that machine only, the launcher
    correctly refusing logins the tests thought they had removed. So the .env
    is taken out as well, and every test here runs twice, the second time on
    a machine whose .env holds all four logins.
    """
    from docflow_core import db, worker_starts
    from docflow_core.config import get_settings

    import app.run_workers as run_workers

    if request.param == "a .env file holding all four logins":
        _read_settings_from(monkeypatch, _env_file_with_all_four_logins(tmp_path / ".env"))
        # The file is being read: on such a machine the launcher refuses.
        assert run_workers.foreign_login_refusal() is not None
    _read_settings_from(monkeypatch, None)

    calls: list[str] = []
    monkeypatch.setattr(db, "verify_logins", lambda logins: calls.append(f"verify {logins}") or list(logins))
    monkeypatch.setattr(worker_starts, "record_start", lambda: calls.append("record") or 1)
    monkeypatch.setattr(run_workers, "supervise", lambda cmds: calls.append("supervise") or 0)
    yield run_workers, calls, monkeypatch
    get_settings.cache_clear()


def test_the_launcher_checks_its_login_records_the_start_then_runs_the_workers(launcher, capsys):
    run_workers, calls, _ = launcher
    assert run_workers.main() == 0
    assert calls == ["verify ('worker',)", "record", "supervise"]
    # The lines the deploy's checks read: the in-flight target (RUNBOOK 9.2;
    # A6, D-236), the models (D-239), then the login (RUNBOOK 9.7, step 7; D-194).
    assert capsys.readouterr().err.splitlines() == [
        "run_workers: dispatch_in_flight_target=1",
        "run_workers: model_ids: extraction=claude-sonnet-5 routing=claude-haiku-4-5",
        "run_workers: login check passed as docflow_worker",
    ]


@pytest.mark.parametrize(
    ("name", "model", "says"),
    [
        ("DOCFLOW_ROUTING_MODEL", "claude-haiku-4-5-20251001", "is not in MODEL_PRICES"),
        ("DOCFLOW_EXTRACTION_MODEL", "claude-haiku-4-5", "may not fill the extraction role"),
    ],
)
def test_the_launcher_refuses_a_model_settings_refuses_in_one_line(launcher, name, model, says, capsys):
    """M10 (D-239): a model not priced for its role stops the worker before
    anything is checked, recorded or started, in one line, not a traceback."""
    from docflow_core.config import get_settings

    run_workers, calls, monkeypatch = launcher
    monkeypatch.setenv(name, model)
    get_settings.cache_clear()
    assert run_workers.main() == 1
    assert calls == []
    err = capsys.readouterr().err
    assert err.splitlines() == [err.strip()]
    assert err.startswith(f"run_workers refused to start: {name}={model!r} {says}")


# ── A6 (founder, 2026-11-11; D-236): the in-flight target the worker got ────


@pytest.mark.parametrize("value", ["0", "-1", "2", "two", "1.5", ""])
def test_the_launcher_refuses_an_in_flight_target_the_parse_slots_were_not_sized_for(launcher, value, capsys):
    """Only a whole number of at least 1 equal to the setting's default, the
    value test_ci_guards.py holds against PARSE_SLOTS. The CI guard can't see
    a Fly secret; this is where the value set on the app is checked."""
    from docflow_core.config import get_settings

    run_workers, calls, monkeypatch = launcher
    monkeypatch.setenv("DISPATCH_IN_FLIGHT_TARGET", value)
    get_settings.cache_clear()
    assert run_workers.main() == 1
    assert calls == []  # nothing checked, recorded or started
    err = capsys.readouterr().err
    assert err.startswith("run_workers refused to start: DISPATCH_IN_FLIGHT_TARGET is ")
    # It names the value it got and the value it expected.
    assert repr(value) in err or f"is {value};" in err
    assert "expected 1." in err
    assert "dispatch_in_flight_target=" not in err


def test_the_launcher_starts_with_the_in_flight_target_set_to_its_default(launcher, capsys):
    from docflow_core.config import get_settings

    run_workers, calls, monkeypatch = launcher
    monkeypatch.setenv("DISPATCH_IN_FLIGHT_TARGET", "1")
    get_settings.cache_clear()
    assert run_workers.main() == 0
    assert calls == ["verify ('worker',)", "record", "supervise"]
    assert "run_workers: dispatch_in_flight_target=1" in capsys.readouterr().err.splitlines()


def test_the_launcher_expects_the_default_the_ci_guard_holds_against_parse_slots():
    """The refusal's expected value is the setting's default, nothing typed
    twice: test_ci_guards.py asserts that default is 1 with PARSE_SLOTS 2."""
    import inspect

    from docflow_core.config import Settings

    import app.run_workers as run_workers

    assert Settings.model_fields["dispatch_in_flight_target"].default == 1
    source = inspect.getsource(run_workers.in_flight_target_refusal)
    assert 'Settings.model_fields["dispatch_in_flight_target"].default' in source


@pytest.mark.parametrize("setting", ["ADMIN_DATABASE_URL", "STRIPE_DATABASE_URL", "API_DATABASE_URL"])
def test_the_launcher_refuses_to_start_holding_another_services_login(launcher, setting, capsys):
    from docflow_core.config import get_settings

    run_workers, calls, monkeypatch = launcher
    monkeypatch.setenv(setting, "postgresql://acme-test-not-a-real-url")
    get_settings.cache_clear()
    assert run_workers.main() == 1
    assert calls == []  # nothing recorded, no worker started
    err = capsys.readouterr().err
    assert setting in err and "acme-test-not-a-real-url" not in err


def test_the_launcher_refuses_logins_held_in_the_env_file_as_it_does_in_the_environment(
    monkeypatch, tmp_path, capsys
):
    """The refusal reads the settings, wherever they came from. This is what
    `python -m app.run_workers` does on a machine whose .env holds all four."""
    from docflow_core import db, worker_starts
    from docflow_core.config import get_settings

    import app.run_workers as run_workers

    started: list[str] = []
    monkeypatch.setattr(db, "verify_logins", lambda logins: started.append("verify") or list(logins))
    monkeypatch.setattr(worker_starts, "record_start", lambda: started.append("record") or 1)
    monkeypatch.setattr(run_workers, "supervise", lambda cmds: started.append("supervise") or 0)
    _read_settings_from(monkeypatch, _env_file_with_all_four_logins(tmp_path / ".env"))
    try:
        assert run_workers.main() == 1
    finally:
        get_settings.cache_clear()
    assert started == []
    err = capsys.readouterr().err
    assert all(name in err for name in OTHER_LOGINS) and "acme-test-not-a-real-url" not in err


def test_the_launcher_refuses_a_url_that_connects_as_another_login(launcher, capsys):
    from docflow_core import db

    run_workers, calls, monkeypatch = launcher

    def wrong(_logins):
        raise db.WrongLoginError("the docflow_worker URL connects as 'docflow_api'")

    monkeypatch.setattr(db, "verify_logins", wrong)
    assert run_workers.main() == 1
    assert calls == []
    assert "login check passed" not in capsys.readouterr().err


def test_the_launcher_starts_the_workers_when_the_database_is_unreachable(launcher, capsys):
    """Recording never stops the worker starting; the heartbeat reports it."""
    from docflow_core import db

    run_workers, calls, monkeypatch = launcher

    def unreachable(_logins):
        raise ConnectionError("acme test: no database")

    monkeypatch.setattr(db, "verify_logins", unreachable)
    assert run_workers.main() == 0
    assert calls == ["record", "supervise"]
    # Not checked is not passed: the line must not appear.
    err = capsys.readouterr().err
    assert "database unreachable at start (ConnectionError)" in err and "login check passed" not in err
