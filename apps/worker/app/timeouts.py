"""
The document task's timeouts, recorded by the worker's MAIN process (Phase
5.5 Stage 3a, review H5).

The document task has a hard time limit only (DOCUMENT_TASK_TIME_LIMIT_
SECONDS): Celery kills the process running it. That process can write
nothing -- and a parser hung inside C code would never have reacted to a
soft limit anyway. But Celery tells the main process about every timeout,
through the task's Request class (`Request.on_timeout`, celery/worker/
request.py), so that is where the timeout is written down, for the stuck
sweep to read (its `decide`: a timeout gets one retry).

If the write fails (the database unreachable, say), it is logged and the
document is handled as a stopped worker, which is today's behaviour.
Production must run Celery's prefork pool: time limits do nothing under the
`solo` pool used on Windows (the Linux CI test is the evidence they work).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery.worker.request import Request
from docflow_core.db import tenant_session
from docflow_core.document_status import record_timeout

logger = logging.getLogger(__name__)


class DocumentTaskRequest(Request):
    def on_timeout(self, soft: bool, timeout: float) -> None:
        super().on_timeout(soft, timeout)
        tenant_id, document_id = self._ids()
        if tenant_id is None or document_id is None:
            logger.error("document_timeout_unrecorded reason=no_ids task_id=%s", self.id)
            return
        try:
            with tenant_session(tenant_id) as session:
                written = record_timeout(session, document_id)
        except Exception as exc:  # noqa: BLE001 -- the main process must never die over this
            logger.error(
                "document_timeout_unrecorded document_id=%s error_type=%s", document_id, type(exc).__name__
            )
            return
        # IDs only (Section 7.10).
        logger.warning(
            "document_timeout document_id=%s soft=%s limit_s=%s recorded=%s",
            document_id,
            soft,
            timeout,
            written,
        )

    def _ids(self) -> tuple[UUID | None, UUID | None]:
        """The task's (tenant_id, document_id), positional or by keyword."""
        args: Any = self.args or ()
        kwargs: Any = self.kwargs or {}
        try:
            tenant = args[0] if len(args) > 0 else kwargs.get("tenant_id")
            document = args[1] if len(args) > 1 else kwargs.get("document_id")
            return (UUID(str(tenant)) if tenant else None, UUID(str(document)) if document else None)
        except (TypeError, ValueError):
            return (None, None)
