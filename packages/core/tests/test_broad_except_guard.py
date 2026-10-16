"""
The guard on broad `except` blocks (Stage 4, PR 1a; DECISIONS.md D-203).

A handler that catches `Exception`, `BaseException` or everything turns ANY
failure into whatever it does next. On the document path that has meant a
bug of ours shown to a customer as a problem with their file. So every such
handler in `apps/` and `packages/` is either

- exempt by rule: its body ends in a bare `raise`, so it changes nothing
  about what the caller sees (the session managers' roll-back-and-re-raise);
  or
- listed below with its kind and one line saying why broad is right there.

Adding one without listing it fails this test, and so does an entry that no
longer points at a handler. Entries are keyed by file, function and the
handler's ordinal among that function's broad handlers, never by line
number, so an edit elsewhere in the file changes nothing here. `scripts/`
and the tests are out (founder, 2026-10-13).

The kinds, from the audit (BUILD-STATUS "Stage 4 detailed design", B1):
  BLAMES_INPUT   any failure becomes "this file / this request is the
                 problem". The audit's target: narrowed to what the library
                 raises on a bad input: done in PR 1b-i, none left.
  DROPS_CONTENT  content is skipped with no record. PR 1b-ii adds the warning.
  OUR_STEP       a step of ours failed and says so honestly (a catalog code
                 that doesn't blame the input, and an alert).
  BEST_EFFORT    a side effect that must never fail the main thing: logged,
                 carry on.
  FAIL_CLOSED    any failure means refuse. Broad is the point.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ROOTS = ("apps", "packages")
SKIPPED_DIRECTORIES = {".venv", "venv", "node_modules", "tests", "__pycache__", ".next", ".next-live"}

BLAMES_INPUT = "blames_input"
DROPS_CONTENT = "drops_content"
OUR_STEP = "our_step"
BEST_EFFORT = "best_effort"
FAIL_CLOSED = "fail_closed"
KINDS = {BLAMES_INPUT, DROPS_CONTENT, OUR_STEP, BEST_EFFORT, FAIL_CLOSED}

_API = "apps/api/app"
_PARSE = "apps/parse/parse_service"
_WORKER = "apps/worker/app"
_CORE = "packages/core/docflow_core"
_TASK = f"{_WORKER}/tasks/parse_and_extract.py"
_ALERTS = f"{_CORE}/founder_alerts.py"
_NOT_RAISED = "an alert that can't be written is logged; the caller's own record stands"

# (file, function, ordinal among the function's broad handlers) -> (kind, why)
LISTED: dict[tuple[str, str, int], tuple[str, str]] = {
    # ── the API ──────────────────────────────────────────────────────────────
    (f"{_API}/deps.py", "_decode_bearer_token", 1): (
        FAIL_CLOSED,
        "a token that can't be verified is refused",
    ),
    (f"{_API}/main.py", "login_startup_check", 1): (BEST_EFFORT, "database unreachable at start: logged"),
    (f"{_API}/main.py", "_CatchUnexpected.dispatch", 1): (
        OUR_STEP,
        "the last handler: any unhandled error is logged with its frames and answered SYS-001",
    ),
    (f"{_API}/main.py", "healthz", 1): (BEST_EFFORT, "an unreadable heartbeat reads as stale"),
    (f"{_API}/main.py", "healthz", 2): (BEST_EFFORT, "unreadable worker starts read as null"),
    (f"{_API}/routers/admin.py", "_console_mfa_notice", 1): (
        BEST_EFFORT,
        "a notice never blocks the Console",
    ),
    (f"{_API}/routers/admin.py", "_queue_depths", 1): (
        BEST_EFFORT,
        "the health strip degrades, the page doesn't",
    ),
    (f"{_API}/routers/admin.py", "_worker_heartbeat", 1): (
        BEST_EFFORT,
        "the health strip degrades, the page doesn't",
    ),
    # ── the parse service ────────────────────────────────────────────────────
    # PR 1b-i narrowed the parsing blocks that blamed the file for anything
    # (DOC-005, DOC-017, IMP-004): each catches only what its library raises
    # on a malformed file (`fault_origin`). What they don't catch is ours.
    (f"{_PARSE}/job.py", "main", 1): (FAIL_CLOSED, "hardening failed: the file is never read"),
    (f"{_PARSE}/job.py", "_parse", 1): (
        OUR_STEP,
        "anything no parsing block claimed is ours: `error`, DOC-030 and the keyed alert",
    ),
    (f"{_PARSE}/parsing/conversion.py", "unwrap_eml", 1): (
        DROPS_CONTENT,
        "an undecodable body; VAL-018 in 1b-ii",
    ),
    (f"{_PARSE}/parsing/conversion.py", "unwrap_eml", 2): (
        DROPS_CONTENT,
        "an undecodable attachment; VAL-017 in 1b-ii",
    ),
    (f"{_PARSE}/parsing/documents.py", "image_preview", 1): (BEST_EFFORT, "no preview is the right outcome"),
    (f"{_PARSE}/sandbox_init.py", "main", 1): (FAIL_CLOSED, "the sandbox could not be set up"),
    (f"{_PARSE}/sandbox_init.py", "main", 2): (FAIL_CLOSED, "no confirmation was sent: isolation failed"),
    (f"{_PARSE}/sandbox_init.py", "_reap_as_init", 1): (
        FAIL_CLOSED,
        "never leave a root process beside the job",
    ),
    # ── the worker ──────────────────────────────────────────────────────────
    (f"{_WORKER}/celery_app.py", "_check_own_login", 1): (
        BEST_EFFORT,
        "database unreachable at start: logged",
    ),
    (f"{_WORKER}/run_workers.py", "main", 1): (BEST_EFFORT, "database unreachable at start: start anyway"),
    (f"{_WORKER}/timeouts.py", "DocumentTaskRequest.on_timeout", 1): (
        BEST_EFFORT,
        "a timeout that can't be recorded: the stuck sweep takes the document",
    ),
    (_TASK, "_store_preview", 1): (BEST_EFFORT, "a preview is a convenience"),
    (_TASK, "_store_extracted_text", 1): (BEST_EFFORT, "the text copy is for later examples only"),
    (_TASK, "_plan_examples", 1): (BEST_EFFORT, "falls back to no examples"),
    (_TASK, "_record_unsaved_extraction", 1): (
        BEST_EFFORT,
        "the document must still end failed; a lost cost record raises cost_record_lost (Q11)",
    ),
    (_TASK, "_alert_lost_cost_record", 1): (BEST_EFFORT, _NOT_RAISED),
    (_TASK, "_parse_alert", 1): (BEST_EFFORT, _NOT_RAISED),
    (_TASK, "_alert_failure", 1): (BEST_EFFORT, _NOT_RAISED),
    (_TASK, "_fail_parse_ours", 1): (BEST_EFFORT, _NOT_RAISED),
    (_TASK, "dispatch_after_task", 1): (BEST_EFFORT, "beat's backstop runs the next pass"),
    (_TASK, "_parse_and_extract", 1): (
        OUR_STEP,
        "saving: a database outage waits and extracts once more (D-206); else DOC-021 and the alert",
    ),
    (_TASK, "_finish", 1): (
        OUR_STEP,
        "buyer identification: a database outage waits (D-206); else VAL-016 and the fault's alert",
    ),
    (_TASK, "_finish", 2): (OUR_STEP, "matching: a database outage waits (D-206); else VAL-016, alert"),
    (_TASK, "_finish", 3): (
        OUR_STEP,
        "duplicate detection: a database outage waits (D-206); else VAL-016 and the fault's alert",
    ),
    (_TASK, "_finish", 4): (BEST_EFFORT, "the review digest is a notification, not the document"),
    (_TASK, "_finish", 5): (OUR_STEP, "validation: a database outage waits (D-206); else DOC-021, alert"),
    (_TASK, "_wait_for_database", 1): (
        BEST_EFFORT,
        "the wait can't be written with the database out: the stuck sweep takes the claim over (D-206)",
    ),
    (_TASK, "_may_extract_again", 1): (
        BEST_EFFORT,
        "the count can't be read with the database out: wait; the check before the next call holds the limit",
    ),
    # ── core ────────────────────────────────────────────────────────────────
    (f"{_CORE}/admin_data_access.py", "_cleanup_quietly", 1): (
        BEST_EFFORT,
        "clean-up after a failure that is re-raised",
    ),
    (f"{_CORE}/admin_data_access.py", "create_tenant", 2): (
        BEST_EFFORT,
        "the Stripe customer's clean-up; the creation's own error is what goes out",
    ),
    (f"{_CORE}/dispatch.py", "run_pass", 1): (BEST_EFFORT, "a send that failed: back to waiting"),
    (f"{_CORE}/dispatch.py", "run_pass", 2): (
        BEST_EFFORT,
        "an unmark that failed: the stuck sweep returns it",
    ),
    (f"{_CORE}/dispatch.py", "check_stopped", 1): (BEST_EFFORT, "the check failed: the sweep goes on"),
    (f"{_CORE}/email_intake.py", "alert_webhook_refused", 1): (BEST_EFFORT, _NOT_RAISED),
    (f"{_CORE}/email_intake.py", "_accept_attachment", 1): (
        OUR_STEP,
        "checking one attachment failed on our side: DOC-030 for it, the rest of the email is read",
    ),
    (f"{_CORE}/exports.py", "build_export", 1): (
        FAIL_CLOSED,
        "a file that can't be read back fails its integrity check (EXP-004)",
    ),
    (f"{_CORE}/extraction.py", "count_request_tokens", 1): (
        BEST_EFFORT,
        "a failed count returns None; the call goes ahead",
    ),
    (f"{_CORE}/extraction.py", "_announce", 1): (
        BEST_EFFORT,
        "the hook never stops the call; the worker's hook raises cost_record_lost (Q11)",
    ),
    (f"{_CORE}/extraction.py", "_count_example_tokens", 1): (BEST_EFFORT, "a failed count returns None"),
    (_ALERTS, "raise_for_failure", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "raise_storage_unavailable", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "report_refused_storage_path", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "raise_seccomp_kill", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "alert_seccomp_kill", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "raise_parse_service_unavailable", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "raise_parse_alert", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "raise_intake_fault", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "alert_parse_service_unavailable", 1): (BEST_EFFORT, _NOT_RAISED),
    (_ALERTS, "alert_storage_unavailable", 1): (BEST_EFFORT, _NOT_RAISED),
    (f"{_CORE}/heartbeat.py", "after_pass", 1): (BEST_EFFORT, "no reading, no ping"),
    (f"{_CORE}/heartbeat.py", "after_pass", 2): (BEST_EFFORT, "a failed ping never fails a pass"),
    (f"{_CORE}/model_provider.py", "record_failure", 1): (BEST_EFFORT, "the provider's state is best effort"),
    (f"{_CORE}/model_provider.py", "record_success", 1): (BEST_EFFORT, "the provider's state is best effort"),
    (f"{_CORE}/model_provider.py", "alert_routing_failure", 1): (BEST_EFFORT, _NOT_RAISED),
    (f"{_CORE}/scheduled_jobs.py", "run_job", 1): (
        OUR_STEP,
        "a job's failure is recorded, retried, then alerted",
    ),
    (f"{_CORE}/stuck_documents.py", "sweep_all", 1): (BEST_EFFORT, "one tenant never stops the sweep"),
    (f"{_CORE}/worker_starts.py", "record_start", 1): (BEST_EFFORT, "never stop the worker starting"),
}


@dataclass(frozen=True)
class Handler:
    function: str
    ordinal: int
    line: int
    reraises: bool


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = [n.id if isinstance(n, ast.Name) else getattr(n, "attr", None) for n in caught]
    return any(name in ("Exception", "BaseException") for name in names)


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.seen: dict[str, int] = {}
        self.handlers: list[Handler] = []

    def _enter(self, node: ast.AST) -> None:
        self.scope.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _enter

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad(node):
            function = ".".join(self.scope) or "<module>"
            self.seen[function] = self.seen.get(function, 0) + 1
            last = node.body[-1]
            reraises = isinstance(last, ast.Raise) and last.exc is None
            self.handlers.append(Handler(function, self.seen[function], node.lineno, reraises))
        self.generic_visit(node)


def scan(source: str) -> list[Handler]:
    """Every broad handler in a module's source, in source order."""
    scanner = _Scanner()
    scanner.visit(ast.parse(source))
    return scanner.handlers


def is_product_file(path: Path) -> bool:
    relative = path.relative_to(REPO)
    return (
        relative.parts[0] in ROOTS
        and not SKIPPED_DIRECTORIES & set(relative.parts)
        and not path.name.startswith("test_")
        and path.name != "conftest.py"
    )


def _product_files() -> list[Path]:
    return sorted(p for root in ROOTS for p in (REPO / root).rglob("*.py") if is_product_file(p))


@pytest.fixture(scope="module")
def found() -> dict[tuple[str, str, int], Handler]:
    handlers = {}
    for path in _product_files():
        for handler in scan(path.read_text(encoding="utf-8")):
            handlers[(path.relative_to(REPO).as_posix(), handler.function, handler.ordinal)] = handler
    return handlers


# ── the guard ────────────────────────────────────────────────────────────────


def test_every_broad_except_is_exempt_by_rule_or_listed_with_its_kind(found):
    unlisted = [
        f"{file}:{handler.line} in {function} (broad handler {ordinal})"
        for (file, function, ordinal), handler in found.items()
        if not handler.reraises and (file, function, ordinal) not in LISTED
    ]
    assert not unlisted, (
        "a broad `except` with no entry in LISTED. Narrow it to the exceptions it expects, or end it "
        "in a bare `raise`, or list it with its kind and why broad is right there (D-203):\n  "
        + "\n  ".join(unlisted)
    )


def test_no_entry_points_at_nothing(found):
    stale = [
        f"{file} {function} #{ordinal}"
        for (file, function, ordinal) in LISTED
        if (file, function, ordinal) not in found
    ]
    assert not stale, "LISTED names a handler that is no longer there:\n  " + "\n  ".join(stale)


def test_no_entry_is_for_a_handler_the_rule_already_exempts(found):
    doubled = [key for key in LISTED if key in found and found[key].reraises]
    assert not doubled, f"these end in a bare raise and need no entry: {doubled}"


def test_every_entry_has_a_known_kind_and_says_why():
    for key, (kind, why) in LISTED.items():
        assert kind in KINDS, key
        assert len(why.strip()) >= 10, key


def test_the_kinds_the_audit_still_has_to_change_are_only_where_1b_ii_changes_them():
    """BLAMES_INPUT and DROPS_CONTENT are the audit's targets. PR 1b-i
    narrowed every BLAMES_INPUT block; the two DROPS_CONTENT blocks left are
    the `.eml` body and attachment in `conversion.py`, which PR 1b-ii gives
    VAL-018 and VAL-017. One anywhere else is a new instance of the defect."""
    blames = {key for key, (kind, _) in LISTED.items() if kind == BLAMES_INPUT}
    drops = {key[0] for key, (kind, _) in LISTED.items() if kind == DROPS_CONTENT}
    assert blames == set()
    assert drops == {f"{_PARSE}/parsing/conversion.py"}


def test_the_guard_reads_the_product_code_and_nothing_else():
    files = {p.relative_to(REPO).as_posix() for p in _product_files()}
    assert f"{_TASK}" in files and f"{_CORE}/founder_alerts.py" in files and f"{_PARSE}/job.py" in files
    assert not any("/tests/" in f or "/.venv/" in f or f.startswith("scripts/") for f in files)
    assert not is_product_file(REPO / "scripts" / "ci" / "create_logins.py")
    assert not is_product_file(REPO / "packages" / "core" / "tests" / "test_faults.py")


# ── the scanner itself ───────────────────────────────────────────────────────


def _one(source: str) -> Handler:
    (handler,) = scan(source)
    return handler


@pytest.mark.parametrize(
    "clause",
    [
        "except Exception:",
        "except Exception as exc:",
        "except BaseException:",
        "except:",
        "except (ValueError, Exception):",
        "except builtins.Exception:",
    ],
)
def test_the_scanner_sees_every_form_of_a_broad_handler(clause):
    handler = _one(f"def f():\n    try:\n        g()\n    {clause}\n        log()\n")
    assert (handler.function, handler.ordinal, handler.reraises) == ("f", 1, False)


@pytest.mark.parametrize(
    "clause", ["except ValueError:", "except (KeyError, OSError) as exc:", "except MyError:"]
)
def test_a_narrow_handler_is_not_broad(clause):
    assert scan(f"def f():\n    try:\n        g()\n    {clause}\n        log()\n") == []


def test_only_a_body_that_ends_in_a_bare_raise_is_exempt():
    bare = _one("def f():\n    try:\n        g()\n    except Exception:\n        undo()\n        raise\n")
    assert bare.reraises
    wrapped = _one(
        "def f():\n    try:\n        g()\n    except Exception as e:\n        raise Other() from e\n"
    )
    assert not wrapped.reraises, "raising something else changes what the caller sees"
    early = _one(
        "def f():\n    try:\n        g()\n    except Exception:\n"
        "        if x:\n            raise\n        log()\n"
    )
    assert not early.reraises, "a raise on one branch only is not the rule"


def test_handlers_are_keyed_by_class_and_function_and_counted_per_function():
    source = (
        "class A:\n"
        "    def m(self):\n"
        "        try:\n            g()\n        except Exception:\n            log()\n"
        "        try:\n            g()\n        except ValueError:\n            log()\n"
        "        try:\n            g()\n        except Exception:\n            log()\n"
        "def f():\n"
        "    try:\n        g()\n    except Exception:\n        log()\n"
        "try:\n    g()\nexcept Exception:\n    log()\n"
    )
    assert [(h.function, h.ordinal) for h in scan(source)] == [
        ("A.m", 1),
        ("A.m", 2),
        ("f", 1),
        ("<module>", 1),
    ]


# ── two neighbours of the same rule ──────────────────────────────────────────


def test_no_product_code_logs_an_exceptions_message_with_logger_exception():
    """`logger.exception` prints the exception's message with its traceback,
    and a database error's message carries the statement's bound values
    (Section 7.10). The sixteen that did were changed to `faults.for_log`:
    the type, our code location and the frames (founder, 2026-10-13).
    `exc_info=` on any logging call prints the same message, so it is
    refused too (founder, 2026-10-14)."""
    users = [
        (p.relative_to(REPO).as_posix(), pattern)
        for p in _product_files()
        for pattern in (".exception(", "exc_info")
        if pattern in p.read_text(encoding="utf-8")
    ]
    assert users == []


def test_no_product_code_swallows_everything_with_contextlib_suppress():
    """`suppress(Exception)` is a broad handler the scanner can't see."""
    users = [
        p.relative_to(REPO).as_posix()
        for p in _product_files()
        if "suppress(Exception" in p.read_text(encoding="utf-8")
        or "suppress(BaseException" in p.read_text(encoding="utf-8")
    ]
    assert users == []
