"""
Every Console write records its outcome (D-178, D-220).

In the admin router a write goes through `_console_write`, which writes the
intent, runs the work, then writes an outcome row pointing back at it.
`_console_act` writes the audit row alone, so it is for reads only. A write
that called it would look done on the Audit tab whatever happened, which is
the defect D-178 fixes, so this test holds every call to its kind.

The same-transaction actions (create tenant, deal terms, tier change, invite,
intake create and add file, hard delete) record their own outcomes inside
admin_data_access (`_attempt`); test_console_outcomes.py covers them.
"""

from __future__ import annotations

import ast
from pathlib import Path

ADMIN_ROUTER = Path(__file__).resolve().parents[3] / "apps" / "api" / "app" / "routers" / "admin.py"
READS = {"read", "acting_as_read", "quarantine_read"}


def _calls(name: str) -> list[ast.Call]:
    tree = ast.parse(ADMIN_ROUTER.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _action(call: ast.Call) -> str:
    """The action argument: a string, or an f-string's fixed parts."""
    arg = call.args[2]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in arg.values)
    raise AssertionError(f"line {call.lineno}: the action must be written out, not computed")


def test_console_act_is_only_ever_called_for_a_read():
    calls = _calls("_console_act")
    assert len(calls) > 10, "found too few _console_act calls for the walk to be trusted"
    writes = {(c.lineno, _action(c)) for c in calls if _action(c) not in READS}
    assert not writes, f"a write through _console_act records no outcome (D-178): {sorted(writes)}"


def test_console_write_is_never_used_for_a_read():
    calls = _calls("_console_write")
    assert len(calls) > 20, "found too few _console_write calls for the walk to be trusted"
    reads = {(c.lineno, _action(c)) for c in calls if _action(c) in READS}
    assert not reads, f"a read needs no outcome row: {sorted(reads)}"
