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
