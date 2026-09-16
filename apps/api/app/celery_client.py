"""
A minimal Celery client the API uses only to enqueue tasks by name -- it
never defines or imports task bodies (those live in apps/worker, per
CLAUDE.md Section 7.11: parsing/extraction never runs in the web process).
Points at the same broker as apps/worker/app/celery_app.py so tasks sent
from here land on the same queues.
"""

from __future__ import annotations

from celery import Celery
from docflow_core.config import get_settings

settings = get_settings()

celery_client = Celery("docflow_api_client", broker=settings.redis_url, backend=settings.redis_url)
