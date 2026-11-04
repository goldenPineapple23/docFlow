"""
A seccomp kill's alert payload (D-232): a catalog code goes in when the
caller has one (the document task's DOC-030, the import's), and None leaves
the field out (a page render's kill fails no document; founder, 2026-11-04).
No database: `raise_alert` is recorded instead of run.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from docflow_core import founder_alerts


class _Savepoint:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _Session:
    def begin_nested(self) -> _Savepoint:
        return _Savepoint()


@pytest.mark.parametrize(
    "where, error_code",
    [("pages", None), ("worker", "DOC-030")],
    ids=["render: no code", "document: its code"],
)
def test_a_none_error_code_is_left_out_of_the_payload(monkeypatch, where, error_code):
    raised: list[dict] = []
    monkeypatch.setattr(founder_alerts, "raise_alert", lambda session, **kw: raised.append(kw) or True)
    ref_id = uuid4()
    assert founder_alerts.raise_seccomp_kill(
        _Session(), tenant_id=uuid4(), where=where, ref_id=ref_id, error_code=error_code
    )
    [alert] = raised
    assert alert["dedupe_key"] == f"parse_seccomp_kill:{where}:{ref_id}"
    assert alert["payload"]["where"] == where
    if error_code is None:
        assert "error_code" not in alert["payload"]
    else:
        assert alert["payload"]["error_code"] == error_code
