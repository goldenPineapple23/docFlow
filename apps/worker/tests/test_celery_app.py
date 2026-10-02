from app.celery_app import celery_app


def test_interactive_and_bulk_queues_configured():
    """
    CLAUDE.md Section 5.1: one tenant's 500-document backfill must not
    starve other tenants' documents -- the queue split is the mechanism.
    """
    queue_names = set(celery_app.conf.task_queues.keys())
    assert {"interactive", "bulk"}.issubset(queue_names)


def test_default_queue_is_interactive():
    assert celery_app.conf.task_default_queue == "interactive"


def test_a_worker_started_from_this_app_registers_the_extraction_task():
    """
    The API enqueues by name (`send_task("docflow.parse_and_extract")`), so
    nothing imports the task module for the worker. A worker started with
    `celery -A app.celery_app worker` must find it by itself. Checked in a
    fresh interpreter: inside this test run, other tests have already
    imported the task module, which would hide the gap.
    """
    import subprocess
    import sys
    from pathlib import Path

    probe = (
        "from app.celery_app import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "assert 'docflow.parse_and_extract' in celery_app.tasks\n"
        "assert 'docflow.generate_export' in celery_app.tasks\n"
        "assert 'docflow.parse_import' in celery_app.tasks\n"
        "assert 'docflow.run_scheduled_jobs' in celery_app.tasks\n"
        "assert 'docflow.run_lifecycle_sweep' in celery_app.tasks\n"
        "assert 'docflow.sweep_stuck_documents' in celery_app.tasks\n"
        "assert 'docflow.dispatch' in celery_app.tasks\n"
    )
    worker_root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", probe], cwd=worker_root, capture_output=True)
    assert result.returncode == 0, "the worker would start with no extraction task registered"


def test_beat_sweeps_scheduled_jobs_regularly():
    """D-113: the first-week check-in and every later lifecycle job depend on
    beat sending the sweep. Its task must be registered (above) and scheduled."""
    from app.celery_app import SCHEDULED_JOBS_SWEEP_SECONDS, celery_app

    entry = celery_app.conf.beat_schedule["run-scheduled-jobs"]
    assert entry["task"] == "docflow.run_scheduled_jobs"
    assert entry["schedule"] == SCHEDULED_JOBS_SWEEP_SECONDS <= 600


def test_beat_sweeps_the_tenant_lifecycle_regularly():
    """Section 7.15.4: "a scheduled job moves cancelling tenants to suspended
    at their effective date." Its task must be registered and scheduled."""
    from app.celery_app import LIFECYCLE_SWEEP_SECONDS, celery_app

    entry = celery_app.conf.beat_schedule["run-lifecycle-sweep"]
    assert entry["task"] == "docflow.run_lifecycle_sweep"
    assert entry["schedule"] == LIFECYCLE_SWEEP_SECONDS <= 600


def test_beat_sweeps_for_stuck_documents_regularly():
    """Section 7.9 / H3 (D-158): a document a dead worker left in processing
    is only found if the sweep runs, well inside the stuck timeout."""
    from docflow_core.constants import STUCK_PROCESSING_TIMEOUT_MIN

    from app.celery_app import STUCK_SWEEP_SECONDS, celery_app

    entry = celery_app.conf.beat_schedule["sweep-stuck-documents"]
    assert entry["task"] == "docflow.sweep_stuck_documents"
    assert entry["schedule"] == STUCK_SWEEP_SECONDS < STUCK_PROCESSING_TIMEOUT_MIN * 60


def test_a_redelivery_waits_longer_than_any_job_should_run():
    """Tasks acknowledge late, so Redis redelivers a job whose worker died.
    It must not redeliver one that is merely still running (H3)."""
    from docflow_core.constants import STUCK_PROCESSING_TIMEOUT_MIN

    from app.celery_app import celery_app

    timeout = celery_app.conf.broker_transport_options["visibility_timeout"]
    assert timeout > STUCK_PROCESSING_TIMEOUT_MIN * 60


def test_beat_sends_the_dispatchers_backstop_pass():
    """Stage 3d (Q4): beat every DISPATCH_INTERVAL_SECONDS, on the dispatch
    queue, expiring rather than piling up behind a busy worker."""
    from docflow_core.constants import DISPATCH_INTERVAL_SECONDS, DISPATCH_QUEUE

    entry = celery_app.conf.beat_schedule["dispatch-waiting-documents"]
    assert entry["task"] == "docflow.dispatch"
    assert entry["schedule"] == DISPATCH_INTERVAL_SECONDS == 30
    assert entry["options"] == {"queue": DISPATCH_QUEUE, "expires": DISPATCH_INTERVAL_SECONDS}
    assert DISPATCH_QUEUE in celery_app.conf.task_queues


def test_the_document_worker_has_exactly_the_dispatchers_target_and_the_dispatch_worker_one_process():
    """Stage 3d (Q2, gap 1; founder's condition 2026-10-01): the document
    slots are the in-flight target, from the one setting. The dispatch pass
    has one process of its own that reads ONLY the dispatch queue, and the
    document worker doesn't read that queue at all."""
    from docflow_core.config import get_settings
    from docflow_core.constants import DISPATCH_QUEUE

    from app.run_workers import commands

    target = get_settings().dispatch_in_flight_target
    assert celery_app.conf.worker_concurrency == target

    def option(argv: list[str], flag: str) -> str:
        return argv[argv.index(flag) + 1]

    workers = commands()
    assert set(workers) == {"documents", "dispatch"}
    documents, dispatcher = workers["documents"], workers["dispatch"]
    assert option(documents, "-Q").split(",") == ["interactive", "bulk"]
    assert f"--concurrency={target}" in documents
    assert option(dispatcher, "-Q") == DISPATCH_QUEUE
    assert "--concurrency=1" in dispatcher
    assert option(documents, "-n") != option(dispatcher, "-n")
    for argv in workers.values():
        assert "-B" not in argv and "--beat" not in argv


def test_the_fly_worker_runs_exactly_one_beat_and_never_starts_a_second_in_a_deploy():
    """Stage 3d, change C (founder's conditions): beat is its own process
    group; the deploy strategy is rolling, which updates machines in place --
    canary and blue-green start a new machine beside the old one. The worker
    group runs both workers through app.run_workers, restarted on failure."""
    import tomllib
    from pathlib import Path

    worker_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((worker_root / "fly.toml").read_text(encoding="utf-8"))
    assert set(config["processes"]) == {"worker", "beat"}
    assert config["processes"]["beat"].startswith("celery -A app.celery_app beat")
    assert config["processes"]["worker"] == "python -m app.run_workers"
    assert config["deploy"]["strategy"] == "rolling"
    assert [r for r in config["restart"] if "worker" in r["processes"]] == [
        {"policy": "on-failure", "retries": 10, "processes": ["worker"]}
    ]
    dockerfile = (worker_root / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["python", "-m", "app.run_workers"]' in dockerfile


def test_only_the_dispatch_task_is_ever_sent_to_the_dispatch_queue_and_it_nowhere_else():
    """Founder's condition (2026-10-01): the dispatch process never holds a
    document. Every `send_task` in the API, the worker and core is read: the
    dispatch task goes to DISPATCH_QUEUE, and nothing else does."""
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    roots = [repo / "apps/api/app", repo / "apps/worker/app", repo / "packages/core/docflow_core"]
    sends: list[tuple[str, str, str]] = []
    for root in roots:
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "send_task"):
                    continue
                name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else "?"
                queue = next((k.value for k in node.keywords if k.arg == "queue"), None)
                shown = (
                    queue.id if isinstance(queue, ast.Name)
                    else queue.value if isinstance(queue, ast.Constant) else repr(queue)
                )
                sends.append((str(path.relative_to(repo)), name, shown))
    assert len(sends) >= 8, sends  # the scan found the senders
    for where, name, queue in sends:
        to_dispatch = queue in ("DISPATCH_QUEUE", "dispatch")
        assert to_dispatch == (name == "docflow.dispatch"), (where, name, queue)
