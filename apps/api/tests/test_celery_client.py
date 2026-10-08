"""
The API only sends tasks: its two Celery clients store and read no result
(founder, 2026-10-08; D-196). With a result backend, every task the worker ran
left a key in Redis for a day that nothing fetched. The worker's own side, and
the scan showing that no `send_task` result is kept anywhere, are in
apps/worker/tests/test_celery_app.py.
"""

from __future__ import annotations


def test_the_apis_celery_clients_have_no_result_backend():
    import docflow_core.email_intake as email_intake

    from app.celery_client import celery_client

    for client in (celery_client, email_intake.celery_client):
        assert client.conf.result_backend is None, client.main
        assert type(client.backend).__name__ == "DisabledBackend", client.main
