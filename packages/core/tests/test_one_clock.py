"""
D-170's rule, checked on every build: app-clock time is never passed into SQL.

"A timestamp the database will later compare is written by the database"
(D-170). Three defects of that shape reached the code before the rule existed
(#2, #4, #7). This fails the build when a database-access module -- one that
calls `.execute(` -- passes the app's clock into a SQL statement:

  - directly: `session.execute(text(...), {"t": datetime.now(UTC)})`
  - through a name: `now = datetime.now(UTC)`, then `now` (or anything built
    from it, `now + timedelta(days=30)`, `now.date()`) in the parameters, or
    in a parameters dict built beforehand

The app's clock here is `datetime.now()`, `datetime.utcnow()`,
`datetime.today()`, `date.today()` and `time.time()`.

**An exception needs a written reason in the code**, on a line of the
`.execute(...)` call: `# app-clock-ok: <why the app's clock is the right one
here>`. A marker with no reason is itself a failure.

What it does not see, stated so it isn't mistaken for more: a value passed
into a helper function that does the `.execute` (the check reads one
function at a time), and a comparison made in Python between a value read
from the database and the app's clock (D-170 #1, #2 and #5 were that shape).
Those stay review questions.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SOURCE_DIRECTORIES = (
    REPO / "packages" / "core" / "docflow_core",
    REPO / "apps" / "api" / "app",
    REPO / "apps" / "worker" / "app",
)
MARKER = re.compile(r"#\s*app-clock-ok:(.*)$")
_APP_CLOCK = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
}


def _is_app_clock_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and (node.func.value.id, node.func.attr) in _APP_CLOCK
    )


def _reads_app_clock(node: ast.AST, tainted: set[str]) -> bool:
    """Whether this expression's value is the app's clock, or built from it
    (arithmetic, a method on it, an f-string, a literal holding it, str()).
    Another function's return value is not followed: `enqueue(params={...now...})`
    returns an id, not a time."""
    if _is_app_clock_call(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in ("datetime", "date"):
                # datetime.fromtimestamp(time.time()) and the like
                return any(
                    _reads_app_clock(a, tainted) for a in [*node.args, *(k.value for k in node.keywords)]
                )
            return _reads_app_clock(receiver, tainted)  # now.date(), (now + x).isoformat()
        if isinstance(node.func, ast.Name) and node.func.id in ("str", "int", "float"):
            return any(_reads_app_clock(a, tainted) for a in node.args)
        return False
    return any(_reads_app_clock(child, tainted) for child in ast.iter_child_nodes(node))


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _violations_in(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()
    found: set[str] = set()
    for function in _functions(tree):
        # Assignments and calls in source order, so a name counts as the app's
        # clock from the line it is assigned on.
        steps = sorted(
            (
                n
                for n in ast.walk(function)
                if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Call))
            ),
            key=lambda n: (n.lineno, n.col_offset),
        )
        tainted: set[str] = set()
        for node in steps:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if node.value is not None and _reads_app_clock(node.value, tainted):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    tainted |= {
                        n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)
                    }
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
                continue
            if not any(
                _reads_app_clock(arg, tainted) for arg in [*node.args, *(k.value for k in node.keywords)]
            ):
                continue
            span = lines[node.lineno - 1 : node.end_lineno]
            marker = next((m for m in (MARKER.search(line) for line in span) if m), None)
            if marker and len(marker.group(1).split()) >= 3:
                continue
            why = "an app-clock-ok marker with no reason" if marker else "app-clock time passed into SQL"
            found.add(f"{filename}:{node.lineno}: {why}")
    return sorted(found)


def _database_access_modules() -> list[Path]:
    return [
        path
        for directory in SOURCE_DIRECTORIES
        for path in sorted(directory.rglob("*.py"))
        if ".execute(" in path.read_text(encoding="utf-8")
    ]


def test_no_database_access_module_passes_the_app_clock_into_sql():
    modules = _database_access_modules()
    assert len(modules) > 10, f"only {len(modules)} database-access modules found -- the scan is broken"
    violations = [
        v
        for path in modules
        for v in _violations_in(path.read_text(encoding="utf-8"), str(path.relative_to(REPO)))
    ]
    assert not violations, "D-170: the database's clock writes what the database compares:\n" + "\n".join(
        violations
    )


# ── The check checks something ───────────────────────────────────────────────
#
# The shapes D-170 #2, #4 and #7 had before they were fixed, and the exception
# rule. Each must be caught (or, with a real reason, let through).

_DIRECT = """
def f(session):
    session.execute(text("UPDATE t SET d = :d"), {"d": datetime.now(UTC) + timedelta(days=30)})
"""
_THROUGH_A_NAME = """
def f(session):
    deletion_at = datetime.now(UTC) + timedelta(days=30)
    session.execute(text("UPDATE t SET d = :d"), {"d": deletion_at})
"""
_THROUGH_A_PARAMS_DICT = """
def f(session):
    day = datetime.now(timezone.utc).date().isoformat()
    params = {"k": f"stuck:{day}"}
    session.execute(text("INSERT INTO a (k) VALUES (:k)"), params)
"""
_THROUGH_ANOTHER_FUNCTIONS_RESULT = """
def f(session):
    outbox_id = enqueue(session, params={"raised_at": datetime.now(UTC).isoformat()})
    session.execute(text("INSERT INTO a (o) VALUES (:o)"), {"o": outbox_id})
"""
_EXCEPTION_WITH_A_REASON = """
def f(session):
    session.execute(  # app-clock-ok: a log line only; the database never compares it
        text("INSERT INTO log (at) VALUES (:at)"), {"at": datetime.now(UTC)}
    )
"""
_EXCEPTION_WITHOUT_A_REASON = """
def f(session):
    session.execute(text("INSERT INTO log (at) VALUES (:at)"), {"at": time.time()})  # app-clock-ok:
"""
_THE_DATABASES_CLOCK = """
def f(session):
    session.execute(text("UPDATE t SET d = now() + make_interval(days => :n)"), {"n": 30})
"""


def test_the_check_catches_app_clock_time_passed_directly_through_a_name_and_through_a_params_dict():
    for source, line in ((_DIRECT, 3), (_THROUGH_A_NAME, 4), (_THROUGH_A_PARAMS_DICT, 5)):
        assert _violations_in(source, "planted.py") == [f"planted.py:{line}: app-clock time passed into SQL"]


def test_an_exception_needs_a_written_reason():
    assert _violations_in(_EXCEPTION_WITH_A_REASON, "planted.py") == []
    assert _violations_in(_EXCEPTION_WITHOUT_A_REASON, "planted.py") == [
        "planted.py:3: an app-clock-ok marker with no reason"
    ]


def test_the_databases_own_clock_passes_and_so_does_an_id_returned_by_another_function():
    assert _violations_in(_THE_DATABASES_CLOCK, "planted.py") == []
    assert _violations_in(_THROUGH_ANOTHER_FUNCTIONS_RESULT, "planted.py") == []
