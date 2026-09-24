# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
An order address that leads nowhere answers REV-006 ("We can't find that
order"), not "We couldn't reach DocFlow". Found in the 5.8d walkthrough: typing
`/review/team` sent the order screen a non-id, the API answered 422, and the
screen blamed the connection.

The three ways an address can lead nowhere must be indistinguishable -- a
tenant cannot tell "no such order" from "another account's order" (Section
7.5) -- and the change must not swallow other validation errors.
All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

from tests.conftest import requires_database
from tests.test_console_api import _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant


@requires_database
def test_a_missing_a_foreign_and_a_malformed_order_all_read_the_same(client):
    with _Tenant("Acme Test Not Found A") as a, _Tenant("Acme Test Not Found B") as b:
        foreign = b.seed(1, status="needs_review")[0]

        answers = [
            client.get(f"/review/documents/{ident}", headers=a.headers())
            for ident in ("team", str(uuid4()), str(foreign))
        ]
        assert [r.status_code for r in answers] == [404, 404, 404]
        assert [r.json()["detail"]["code"] for r in answers] == ["REV-006"] * 3
        assert answers[0].json() == answers[1].json() == answers[2].json()

        # A malformed id on the order's exports list reads the same way.
        exports = client.get("/review/documents/team/exports", headers=a.headers())
        assert (exports.status_code, exports.json()["detail"]["code"]) == (404, "REV-006")


@requires_database
def test_other_validation_errors_keep_their_own_answer(client):
    with _Tenant("Acme Test Not Found Other") as t:
        response = client.get("/activity?limit=0", headers=t.headers())
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
