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


def test_the_launcher_checks_its_login_records_the_start_then_runs_the_workers(launcher):
    run_workers, calls, _ = launcher
    assert run_workers.main() == 0
    assert calls == ["verify ('worker',)", "record", "supervise"]


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


def test_the_launcher_refuses_a_url_that_connects_as_another_login(launcher):
    from docflow_core import db

    run_workers, calls, monkeypatch = launcher

    def wrong(_logins):
        raise db.WrongLoginError("the docflow_worker URL connects as 'docflow_api'")

    monkeypatch.setattr(db, "verify_logins", wrong)
    assert run_workers.main() == 1
    assert calls == []


def test_the_launcher_starts_the_workers_when_the_database_is_unreachable(launcher):
    """Recording never stops the worker starting; the heartbeat reports it."""
    from docflow_core import db

    run_workers, calls, monkeypatch = launcher

    def unreachable(_logins):
        raise ConnectionError("acme test: no database")

    monkeypatch.setattr(db, "verify_logins", unreachable)
    assert run_workers.main() == 0
    assert calls == ["record", "supervise"]
