# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
A request refused before its route runs answers in the catalog's words
(D-240 item 9; D-241), and no signed link's token reaches the access log.

Found in task 7's smoke test (2026-11-15): the Console's "Create tenant" with
an `@example.test` owner got FastAPI's own 422 body, which no screen can
render, so the page said "We couldn't reach DocFlow ... DocFlow has been
alerted". Nothing had failed to arrive and no alert existed. And a page
image's token was printed in the API's access log on Fly.

What this proves:
  * REQ-002 when every failing field is an e-mail address, REQ-001 otherwise,
    each naming the fields and never the value or the library's wording;
  * a bad `document_id` in the path is still REV-006;
  * the Console still answers a non-admin's bad request with a bare 404;
  * the three token routes answer a missing token as a bad link, never 422;
  * every EmailStr field in the routers is in the handler's list;
  * the access line keeps the path and drops the query string, through
    real uvicorn as the API starts on Fly and as it starts in CI.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import http.client
import importlib
import inspect
import logging
import pkgutil
import subprocess
import sys
import typing
from uuid import uuid4

import pytest
from pydantic import BaseModel, EmailStr

from app import access_log
from app.errors import EMAIL_FIELDS, validation_refusal
from app.routers import __path__ as routers_path
from tests.conftest import requires_console_schema, requires_database
from tests.test_console_api import _Console, _environment, _scalar, stripe  # noqa: F401
from tests.test_quarantine_api import _Tenant
from tests.test_serve_dual_stack import _API_DIR, _env, _free_port, _wait_for

# What FastAPI's own body says about a bad address; none of it may go out.
LIBRARY_WORDS = ("value is not a valid email", "value_error", "email-validator", "pydantic", '"loc"', '"msg"')


# ── The refusal itself, from error locations only ───────────────────────────


def test_every_failing_field_an_email_address_is_req_002():
    errors = [{"loc": ("body", "owner_email"), "msg": "x", "input": "a@b.test"}]
    assert validation_refusal(errors) == ("REQ-002", ["owner_email"])


def test_any_other_failing_field_makes_it_req_001():
    errors = [
        {"loc": ("body", "owner_email"), "msg": "x"},
        {"loc": ("body", "name"), "msg": "Field required"},
    ]
    assert validation_refusal(errors) == ("REQ-001", ["owner_email", "name"])


def test_a_query_or_nested_field_is_named_by_its_path():
    assert validation_refusal([{"loc": ("query", "limit")}]) == ("REQ-001", ["limit"])
    nested = [{"loc": ("body", "lines", 0, "unit_price")}]
    assert validation_refusal(nested) == ("REQ-001", ["lines.0.unit_price"])


def test_a_location_made_of_what_was_sent_is_never_repeated():
    # An unexpected key, or a JSON body that didn't parse (pydantic puts the
    # byte offset there): the answer names no field rather than echo input.
    sent = [{"loc": ("body", "<img src=x>")}, {"loc": ("body", 17)}, {"loc": ("body",)}]
    for error in sent:
        assert validation_refusal([error]) == ("REQ-001", []), error
    # Nor does a bad address alongside one of them read as REQ-002.
    assert validation_refusal([{"loc": ("body", "owner_email")}, sent[0]]) == ("REQ-001", ["owner_email"])


def test_the_same_field_twice_is_named_once():
    errors = [{"loc": ("body", "contact_email")}, {"loc": ("body", "contact_email")}]
    assert validation_refusal(errors) == ("REQ-002", ["contact_email"])


def test_every_email_field_in_the_routers_is_in_the_handlers_list():
    """A new EmailStr field that isn't in EMAIL_FIELDS would answer REQ-001,
    which tells the reader nothing about the address; one in the list that no
    longer exists is dead wording. Either fails here."""

    def mentions_email(annotation) -> bool:
        if annotation is EmailStr:
            return True
        return any(mentions_email(arg) for arg in typing.get_args(annotation))

    found: set[str] = set()
    for module_info in pkgutil.iter_modules(routers_path):
        module = importlib.import_module(f"app.routers.{module_info.name}")
        for _, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, BaseModel) and member.__module__ == module.__name__:
                found |= {name for name, f in member.model_fields.items() if mentions_email(f.annotation)}
    assert found == set(EMAIL_FIELDS)


# ── Through the app, no database needed ─────────────────────────────────────


def test_a_bad_path_parameter_is_req_001_naming_it(client):
    response = client.get(f"/review/documents/{uuid4()}/pages/first?token=x")
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "REQ-001",
            "title": "DocFlow couldn't accept one of the values",
            "message": (
                "One or more values aren't in a form DocFlow accepts, so DocFlow didn't save anything."
            ),
            "action": "Check the field named above and try again.",
            "detail": {"fields": ["index"]},
        }
    }


def test_a_bad_document_id_is_still_rev_006(client):
    response = client.get("/review/documents/team/pages/1?token=x")
    assert (response.status_code, response.json()["detail"]["code"]) == (404, "REV-006")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"/review/documents/{uuid4()}/original/content", None),
        (f"/review/documents/{uuid4()}/pages/1", None),
        (f"/review/exports/{uuid4()}/download", "EXP-003"),
    ],
)
@pytest.mark.parametrize("query", ["", "?token="])
def test_a_link_without_its_token_is_a_bad_link_not_a_422(client, path, expected, query):
    response = client.get(path + query)
    assert response.status_code == 404
    if expected is None:
        assert response.json() == {"detail": "Not Found"}
    else:
        assert response.json()["detail"]["code"] == expected


def test_a_stranger_sending_a_bad_body_to_the_console_gets_the_404(client):
    response = client.post("/admin/tenants/new", json={"owner_email": "someone@example.test"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


NOT_JSON = {"content": b"{not json", "headers": {"Content-Type": "application/json"}}


CONSOLE_POSTS = ["/admin/tenants/new", "/admin/intakes", f"/admin/tenants/{uuid4()}/act/review/x"]


@pytest.mark.parametrize("path", CONSOLE_POSTS)
def test_a_stranger_sending_a_body_that_isnt_json_to_the_console_gets_the_404(client, path):
    # FastAPI refuses such a body before any gate runs; it answered 422, which
    # told a stranger the route exists (7.15.1; found building D-241).
    response = client.post(path, **NOT_JSON)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# ── Through the app, against the real database ──────────────────────────────


@requires_console_schema
def test_a_test_domain_owner_is_req_002_and_nothing_is_created(client, stripe):
    name = f"Acme Test REQ-002 {uuid4().hex[:6]}"
    with _Console() as console:
        response = console.create_tenant(client, name=name, owner_email="owner@example.test")
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "REQ-002"
        assert detail["title"] == "This e-mail address can't receive mail"
        assert detail["detail"] == {"fields": ["owner_email"]}
        assert "example.test" not in response.text
        for words in LIBRARY_WORDS:
            assert words not in response.text
        assert stripe.created == []
        assert _scalar("SELECT count(*) FROM tenants WHERE name = :n", n=name) == 0


@requires_console_schema
def test_a_bad_intake_contact_is_req_002(client):
    with _Console() as console:
        response = client.post(
            "/admin/intakes",
            headers=console.headers(),
            json={"prospect_name": "Acme Test Prospect REQ", "contact_email": "buyer@intake.invalid"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "REQ-002"
        assert response.json()["detail"]["detail"] == {"fields": ["contact_email"]}


@requires_console_schema
def test_a_bad_address_with_another_bad_field_is_req_001_naming_both(client, stripe):
    with _Console() as console:
        response = client.post(
            "/admin/tenants/new", headers=console.headers(), json={"owner_email": "owner@example.test"}
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "REQ-001"
        assert set(detail["detail"]["fields"]) == {"owner_email", "name"}
        assert stripe.created == []


@requires_console_schema
def test_a_tenant_user_sending_a_bad_body_to_the_console_gets_the_404(client):
    with _Tenant("Acme Test REQ Console 404") as tenant:
        bad = client.post(
            "/admin/tenants/new", headers=tenant.headers(), json={"owner_email": "someone@example.test"}
        )
        broken = client.post("/admin/tenants/new", headers={**tenant.headers(), **NOT_JSON["headers"]},
                             content=NOT_JSON["content"])
        for response in (bad, broken):
            assert response.status_code == 404
            assert response.json() == {"detail": "Not Found"}


@requires_console_schema
def test_a_platform_admins_body_that_isnt_json_is_req_001_naming_no_field(client, stripe):
    with _Console() as console:
        response = client.post("/admin/tenants/new", headers={**console.headers(), **NOT_JSON["headers"]},
                               content=NOT_JSON["content"])
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "REQ-001"
        assert response.json()["detail"]["detail"] == {"fields": []}
        assert stripe.created == []


# ── The access log ──────────────────────────────────────────────────────────


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        access_log.ACCESS_LOGGER, logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", path, "1.1", 200), None,
    )


def test_the_filter_keeps_the_path_and_drops_the_query_string():
    record = _access_record("/review/documents/abc/pages/1?token=SECRET-TOKEN&download=true")
    assert access_log.DropQueryStrings().filter(record) is True
    assert record.getMessage() == '127.0.0.1:5000 - "GET /review/documents/abc/pages/1 HTTP/1.1" 200'


def test_the_filter_leaves_a_line_without_a_query_string_alone():
    record = _access_record("/healthz")
    access_log.DropQueryStrings().filter(record)
    assert record.getMessage() == '127.0.0.1:5000 - "GET /healthz HTTP/1.1" 200'


def test_importing_the_app_installs_the_filter_once():
    import app.main  # noqa: F401 -- the import is what installs it

    access_log.install()
    filters = logging.getLogger(access_log.ACCESS_LOGGER).filters
    assert sum(isinstance(f, access_log.DropQueryStrings) for f in filters) == 1


def _get(port: int, path: str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("GET", path)
        return connection.getresponse().status
    finally:
        connection.close()


@requires_database
@pytest.mark.parametrize(
    "command",
    [["app.serve", "{port}"], ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "{port}"]],
    ids=["as-on-fly", "as-in-ci"],
)
def test_no_query_string_reaches_real_uvicorns_access_log(command):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", *[part.format(port=port) for part in command]],
        cwd=_API_DIR,
        # Unbuffered, so stopping the process can't lose a line still in its buffer.
        env=_env({"PYTHONUNBUFFERED": "1"}),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for(proc, "127.0.0.1", port)
        assert _get(port, "/healthz?token=SECRET-TOKEN&q=PRIVATE-TEXT") == 200
        assert _get(port, f"/review/documents/{uuid4()}/pages/1?token=SECRET-TOKEN") == 404
    finally:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=30)
    logged = (stdout + stderr).decode(errors="replace")
    # The lines are there, so their absence below means something.
    assert '"GET /healthz HTTP/1.1" 200' in logged, logged[-2000:]
    assert "/pages/1 HTTP/1.1" in logged, logged[-2000:]
    assert "SECRET-TOKEN" not in logged
    assert "PRIVATE-TEXT" not in logged
    assert "?" not in "".join(line for line in logged.splitlines() if "HTTP/1.1" in line)
