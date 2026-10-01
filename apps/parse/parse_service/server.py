"""
The parse service's supervisor (Stage 3c, items 2 and 4). It never opens a
file: it checks the request, starts one sandboxed job, reads the job's
answer up to a cap, and replies.

    POST /v1/document   bytes in -> {"outcome": ...}   (a purchase order)
    POST /v1/table      bytes in -> {"outcome": ...}   (a catalog file)
    GET  /health        200 only after the canary passed

Every POST must carry `Authorization: Bearer <PARSE_SERVICE_TOKEN>`; a
missing or wrong token is a 401 before the body is read (founder's change
4, test N4). `X-File-Extension` (e.g. ".docx") is the only thing the job
learns about the sender's filename.

Answers, as JSON with HTTP 200:
- {"outcome": "ok", ...} or {"outcome": "rejected", "code": ...} -- the job's;
- {"outcome": "stopped", "cause": memory|cpu|wall_clock|output_too_large};
- {"outcome": "crashed", "cause": ...} -- the job died some other way.
HTTP 503 means the request never got in (no free slot, or the service can't
isolate a job); the worker waits and tries again.

Startup, in this order (any failure exits non-zero and the port never
opens): settings (production mode refuses isolation off, a missing token,
or any test-only switch); cgroups; the canary.
"""

from __future__ import annotations

import hmac
import json
import logging
import queue
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from docflow_core.file_types import MAX_FILE_SIZE_BYTES

from parse_service import config
from parse_service.launcher import run_job

logger = logging.getLogger("parse_service")

_EXTENSION = re.compile(r"^\.[a-z0-9]{1,8}$")
_KINDS = {"/v1/document": "document", "/v1/table": "table"}


class Service:
    def __init__(self, settings: config.Settings, cgroups) -> None:
        self.settings = settings
        self.cgroups = cgroups
        self.slots: queue.SimpleQueue[int] = queue.SimpleQueue()
        for slot in range(config.PARSE_SLOTS):
            self.slots.put(slot)
        self.healthy = False


class Handler(BaseHTTPRequestHandler):
    service: Service  # set on the class by serve()
    server_version = "docflow-parse"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:  # ids and statuses only
        logger.info("http %s", fmt % args)

    def _reply(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            if self.service.healthy:
                self._reply(200, {"ok": True})
            else:
                self._reply(503, {"ok": False})
            return
        self._reply(404, {"error": "not_found"})

    def _authorized(self) -> bool:
        token = self.service.settings.token
        if not token:
            return not self.service.settings.production  # dev without a token
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode())

    def do_POST(self) -> None:  # noqa: N802
        kind = _KINDS.get(self.path)
        if kind is None:
            self._reply(404, {"error": "not_found"})
            return
        if not self._authorized():
            self.close_connection = True
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.close_connection = True
            self._reply(411, {"error": "length_required"})
            return
        if length < 0 or length > MAX_FILE_SIZE_BYTES:
            self.close_connection = True
            self._reply(413, {"error": "too_large"})
            return
        extension = (self.headers.get("X-File-Extension") or "").lower()
        if not _EXTENSION.match(extension):
            self.close_connection = True
            self._reply(400, {"error": "bad_extension"})
            return
        try:
            slot = self.service.slots.get_nowait()
        except queue.Empty:
            self.close_connection = True
            self._reply(503, {"error": "busy"}, {"Retry-After": str(config.BUSY_RETRY_AFTER_SECONDS)})
            return
        try:
            body = self.rfile.read(length)
            result = run_job(
                kind,
                {"filename": f"upload{extension}"},
                body,
                slot=slot,
                settings=self.service.settings,
                cgroups=self.service.cgroups,
            )
        finally:
            self.service.slots.put(slot)
        logger.info("job kind=%s outcome=%s cause=%s seconds=%s", kind, result.outcome, result.cause, result.seconds)
        if result.outcome == "rejected":
            # Why, in the parser's or LibreOffice's own words: the log only.
            # The worker (and the tenant) get the catalog code alone.
            logger.warning(
                "job_rejected kind=%s code=%s detail=%s",
                kind,
                (result.answer or {}).get("code"),
                result.evidence.get("stderr_tail", "").strip(),
            )
        if result.outcome == "isolation_failed":
            logger.critical("isolation_failed cause=%s evidence=%s", result.cause, result.evidence)
            self._reply(503, {"error": "isolation_failed"}, {"Retry-After": "30"})
            return
        if result.outcome in ("ok", "rejected"):
            self._reply(200, result.answer or {})
            return
        self._reply(200, {"outcome": result.outcome, "cause": result.cause})


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, family: int) -> None:
        self.address_family = family
        super().__init__(address, handler)

    def server_bind(self) -> None:
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def serve() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = config.load()
    except config.StartupRefused as exc:
        print(f"parse service refused to start: {exc}", file=sys.stderr, flush=True)
        return 2
    cgroups = None
    if settings.isolation:
        from parse_service.cgroups import CgroupError, Cgroups
        from parse_service.launcher import start_reaper
        from parse_service.selftest import Runner

        try:
            start_reaper()  # killed jobs' orphans come here and are reaped on SIGCHLD (B5/B11)
            cgroups = Cgroups.detect()
            cgroups.setup()
        except (CgroupError, OSError) as exc:
            print(f"parse service refused to start: no working cgroups ({exc})", file=sys.stderr, flush=True)
            return 1
        print(f"canary: cgroup v{cgroups.version}", flush=True)
        if not Runner(settings, cgroups).canary():
            print("parse service refused to start: the canary failed", file=sys.stderr, flush=True)
            return 1
        print("canary: PASS", flush=True)
    else:
        print("parse service: isolation OFF (dev only; refused in production)", flush=True)
    if not settings.token:
        print("parse service: no PARSE_SERVICE_TOKEN (dev only; refused in production)", flush=True)

    service = Service(settings, cgroups)
    Handler.service = service
    if settings.production or settings.isolation:
        server = _Server(("::", settings.port), Handler, socket.AF_INET6)
    else:
        server = _Server(("127.0.0.1", settings.port), Handler, socket.AF_INET)
    service.healthy = True
    print(f"parse service listening on port {settings.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(serve())
