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
    )
    worker_root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", probe], cwd=worker_root, capture_output=True)
    assert result.returncode == 0, "the worker would start with no extraction task registered"
