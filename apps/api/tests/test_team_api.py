# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The Team page's API (slice 5.8d, D-132; migration 0023) against the real
database and RLS: the account's admin lists, invites, resends and removes;
nobody else can; a removed person is refused from their next request with a
catalog answer; an address already signed in elsewhere is refused without
revealing where; another account's people can neither be seen nor touched; and
every invite and removal lands on the Activity page.

Supabase's link generation is replaced at the `external_services` boundary, the
same way the Console's tests do it. All data is fictional (CLAUDE.md Section 0
rule 4).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import jwt
import pytest
from docflow_core import external_services
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_database
from tests.test_console_api import JWT_SECRET, _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant


def _schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT first_signed_in_at FROM users LIMIT 0"))
        return True
    except Exception:
        return False


requires_team_schema = pytest.mark.skipif(
    not _schema_available(),
    reason="supabase/migrations/0023_team.sql has not been applied yet -- see D-132.",
)


class _Links:
    """Stands in for Supabase's admin link API. `auth_ids` maps an address to
    the sign-in Supabase already has for it; `fail` makes the service
    unreachable."""

    def __init__(self):
        self.auth_ids: dict[str, UUID] = {}
        self.issued: list[str] = []
        self.fail = False

    def generate(self, *, email, redirect_to):
        if self.fail:
            raise external_services.ExternalServiceError("supabase", "test refusal")
        self.auth_ids.setdefault(email, uuid4())
        url = f"https://auth.example.test/verify?token={len(self.issued)}"
        self.issued.append(url)
        return external_services.InviteLink(auth_user_id=self.auth_ids[email], url=url)


@pytest.fixture
def links(monkeypatch):
    fake = _Links()
    monkeypatch.setattr(external_services, "generate_invite_link", fake.generate)
    return fake


def _bearer(auth_user_id: UUID | str) -> dict:
    token = jwt.encode(
        {"sub": str(auth_user_id), "email": "x@example.test", "aud": "authenticated"},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _user(email: str) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text(
                    "SELECT id, tenant_id, role, is_active, auth_user_id, first_signed_in_at "
                    "FROM users WHERE email = :e"
                ),
                {"e": email},
            ).mappings().one()
        )


def _invite(client, t: _Tenant, email: str, role: str = "owner"):
    return client.post("/team/invite", headers=t.headers(role), json={"email": email})


@requires_database
@requires_team_schema
def test_the_admin_sees_the_team_and_invites_a_reviewer(client, links):
    with _Tenant("Acme Test Team Invite") as t:
        listed = client.get("/team", headers=t.headers()).json()["members"]
        assert len(listed) == 1
        me = listed[0]
        assert (me["role"], me["is_you"], me["can_remove"], me["can_resend"]) == ("owner", True, False, False)

        email = f"new-reviewer-{uuid4().hex[:6]}@example.test"
        response = _invite(client, t, f"  {email.upper()} ")
        assert response.status_code == 200, response.text
        assert response.json()["email"] == email  # trimmed and lower-cased
        assert response.json()["held"] is True  # no provider in tests

        row = _user(email)
        assert (row["role"], row["is_active"]) == ("reviewer", True)
        assert row["auth_user_id"] == links.auth_ids[email]

        [mail] = t.outbox("team_invite")
        assert mail["to_address"] == email
        assert links.issued[-1] in mail["body_text"]
        assert "Acme Test Team Invite" in mail["body_text"]

        members = client.get("/team", headers=t.headers()).json()["members"]
        new = next(m for m in members if m["email"] == email)
        assert (new["signed_in"], new["can_resend"], new["can_remove"]) == (False, True, True)

        # The Activity page names who invited whom.
        activity = client.get("/activity?kind=invited", headers=t.headers()).json()
        assert activity["total"] == 1
        item = activity["items"][0]
        assert (item["kind"], item["detail"], item["document_id"]) == ("invited", email, None)
        assert item["by"] == t.owner_email() and item["by_docflow_support"] is False


@requires_database
@requires_team_schema
def test_only_the_admin_can_manage_the_team(client, links):
    with _Tenant("Acme Test Team Roles") as t:
        t.add_user("reviewer")
        reviewer_id = str(t.users["reviewer"][0])
        for method, path, body in (
            ("GET", "/team", None),
            ("POST", "/team/invite", {"email": "someone@example.test"}),
            ("POST", f"/team/{reviewer_id}/resend", None),
            ("POST", f"/team/{reviewer_id}/remove", None),
        ):
            response = client.request(method, path, headers=t.headers("reviewer"), json=body)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["detail"]["code"] == "AUTH-003"
        assert _user_active(reviewer_id)


def _user_active(user_id: str) -> bool:
    with platform_session() as session:
        return bool(
            session.execute(text("SELECT is_active FROM users WHERE id = :u"), {"u": user_id}).scalar_one()
        )


@requires_database
@requires_team_schema
def test_a_removed_reviewer_is_refused_from_their_next_request(client, links):
    with _Tenant("Acme Test Team Remove") as t:
        t.add_user("reviewer")
        reviewer_id, reviewer_auth = t.users["reviewer"]
        assert client.get("/review/documents", headers=t.headers("reviewer")).status_code == 200

        response = client.post(f"/team/{reviewer_id}/remove", headers=t.headers())
        assert response.status_code == 200, response.text
        assert not _user_active(str(reviewer_id))

        # Their Supabase session is still valid; DocFlow refuses it anyway.
        refused = client.get("/review/documents", headers=_bearer(reviewer_auth))
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "AUTH-004"
        me = client.get("/auth/me", headers=_bearer(reviewer_auth)).json()
        assert (me["access_removed"], me["tenant_id"], me["role"]) == (True, None, None)
        # The page sign-in lands on shows this, in the catalog's words.
        assert me["refusal"]["code"] == "AUTH-004"

        # Gone from the list, still in the history.
        emails = [m["email"] for m in client.get("/team", headers=t.headers()).json()["members"]]
        assert emails == [t.owner_email()]
        activity = client.get("/activity?kind=removed", headers=t.headers()).json()
        assert activity["total"] == 1 and activity["items"][0]["detail"].startswith("reviewer-")

        # Inviting the same address again restores the same person.
        address = _user_email(str(reviewer_id))
        links.auth_ids[address] = UUID(reviewer_auth)  # Supabase knows them already
        again = _invite(client, t, address)
        assert again.status_code == 200, again.text
        assert again.json()["restored"] is True and again.json()["user_id"] == str(reviewer_id)
        assert _user_active(str(reviewer_id))


def _user_email(user_id: str) -> str:
    with platform_session() as session:
        return str(
            session.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user_id}).scalar_one()
        )


@requires_database
@requires_team_schema
def test_the_admin_can_remove_neither_themselves_nor_another_admin(client, links):
    with _Tenant("Acme Test Team Guards") as t:
        owner_id = str(t.users["owner"][0])
        refused = client.post(f"/team/{owner_id}/remove", headers=t.headers())
        assert (refused.status_code, refused.json()["detail"]["code"]) == (403, "TEAM-006")

        # An `admin` (equivalent to the owner, D-128) cannot remove the owner.
        t.add_user("admin")
        refused = client.post(f"/team/{owner_id}/remove", headers=t.headers("admin"))
        assert (refused.status_code, refused.json()["detail"]["code"]) == (403, "TEAM-007")
        assert _user_active(owner_id)


@requires_database
@requires_team_schema
def test_bad_duplicate_and_unreachable_invites_are_refused_and_leave_nothing(client, links):
    with _Tenant("Acme Test Team Refusals") as t:
        bad = _invite(client, t, "not-an-address")
        assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "TEAM-001")

        duplicate = _invite(client, t, t.owner_email())
        assert (duplicate.status_code, duplicate.json()["detail"]["code"]) == (409, "TEAM-002")

        links.fail = True
        email = f"unreachable-{uuid4().hex[:6]}@example.test"
        down = _invite(client, t, email)
        assert (down.status_code, down.json()["detail"]["code"]) == (503, "TEAM-004")
        assert t.rows("SELECT id FROM users WHERE tenant_id = :t AND email = :e", e=email) == []
        assert t.outbox("team_invite") == []


@requires_database
@requires_team_schema
def test_an_address_signed_in_on_another_account_is_refused_without_saying_where(client, links):
    with _Tenant("Acme Test Team A") as a, _Tenant("Acme Test Team B") as b:
        b.add_user("reviewer")
        b_reviewer_id, b_reviewer_auth = b.users["reviewer"]
        taken = _user_email(str(b_reviewer_id))
        # Supabase already knows this address: it hands back B's sign-in.
        links.auth_ids[taken] = UUID(b_reviewer_auth)

        response = _invite(client, a, taken)
        assert (response.status_code, response.json()["detail"]["code"]) == (409, "TEAM-003")
        assert "Acme Test Team B" not in response.text
        assert a.rows("SELECT id FROM users WHERE tenant_id = :t AND email = :e", e=taken) == []
        # B's person is untouched.
        assert _user_active(str(b_reviewer_id))


@requires_database
@requires_team_schema
def test_one_account_can_neither_see_nor_touch_another_accounts_people(client, links):
    with _Tenant("Acme Test Team Iso A") as a, _Tenant("Acme Test Team Iso B") as b:
        b.add_user("reviewer")
        b_reviewer = str(b.users["reviewer"][0])

        listed = [m["email"] for m in client.get("/team", headers=a.headers()).json()["members"]]
        assert listed == [a.owner_email()]

        for action in ("remove", "resend"):
            response = client.post(f"/team/{b_reviewer}/{action}", headers=a.headers())
            assert (response.status_code, response.json()["detail"]["code"]) == (404, "TEAM-008")
        assert _user_active(b_reviewer)


@requires_database
@requires_team_schema
def test_resend_is_for_people_who_have_not_signed_in_yet(client, links):
    with _Tenant("Acme Test Team Resend") as t:
        email = f"resend-{uuid4().hex[:6]}@example.test"
        user_id = _invite(client, t, email).json()["user_id"]

        again = client.post(f"/team/{user_id}/resend", headers=t.headers())
        assert again.status_code == 200, again.text
        assert len(t.outbox("team_invite")) == 2

        # Their first visit to any signed-in screen stamps the sign-in.
        me = client.get("/auth/me", headers=_bearer(links.auth_ids[email])).json()
        assert (me["role"], me["access_removed"]) == ("reviewer", False)
        assert _user(email)["first_signed_in_at"] is not None

        member = next(
            m for m in client.get("/team", headers=t.headers()).json()["members"] if m["email"] == email
        )
        assert (member["signed_in"], member["can_resend"]) == (True, False)
        refused = client.post(f"/team/{user_id}/resend", headers=t.headers())
        assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "TEAM-005")
