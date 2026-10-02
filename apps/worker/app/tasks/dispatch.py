"""
The dispatcher's task (Stage 3d, review H4). One pass of
`docflow_core.dispatch.run_pass`: waiting documents go to the queue, turn by
turn between tenants, within the in-flight target and the per-tenant cap.

Three things start a pass (founder, Q4): the API's nudge after every intake
commit (upload, email, release, test-batch run), the end of every document
task (app.tasks.parse_and_extract.dispatch_after_task), and celery beat every
DISPATCH_INTERVAL_SECONDS as the backstop. Passes never overlap (an advisory
lock), so any number of nudges is safe.

Stage 3e (part C): after a pass that succeeded -- nothing raised, every send
went out -- this task pings the external heartbeat (docflow_core.heartbeat).
Only this task: the pass at the end of a document task never pings, so a
stopped dispatch process can't be hidden by a busy documents worker.
"""

from __future__ import annotations

from docflow_core import dispatch, heartbeat
from docflow_core.constants import DISPATCH_TASK_TIME_LIMIT_SECONDS

from app.celery_app import celery_app
from app.tasks.parse_and_extract import send_document


@celery_app.task(name="docflow.dispatch", time_limit=DISPATCH_TASK_TIME_LIMIT_SECONDS)
def run_dispatch() -> int:
    result = dispatch.run_pass(send_document)
    heartbeat.after_pass(succeeded=not result.send_failed)
    return len(result.sent)
