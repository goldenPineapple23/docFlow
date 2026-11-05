# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Viewer A4 (D-233): page images and stored text shown in the page, against the
real database, RLS and Storage.

What must hold:

  * the mint answer says what the panel shows, and the frame is its answer
    only while a drawn format has no pages (D-228 item 1's answer);
  * a document's pages come with one token, purpose `page`, which opens only
    that document's pages: never the original, never another document;
  * the page route serves only image/png and image/jpeg, whatever the list says;
  * the text route serves the text DocFlow stored, never the sender's bytes,
    cut where the text previews are cut, and says so.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
import logging
import struct
import zlib
from uuid import UUID

import pytest
from docflow_core.db import platform_session
from docflow_core.signed_urls import mint_document_token, mint_export_token, mint_page_token
from docflow_core.storage import delete_tenant_storage, save_derived, save_file, save_page
from sqlalchemy import text

from tests.conftest import requires_console_schema, requires_review_schema
from tests.test_acting_as import _act, _forget_admin_actions
from tests.test_console_api import _Console, _environment  # noqa: F401 -- fixtures
from tests.test_review_api import CLEAN_HEADER, CLEAN_LINES, _ReviewTenant, _secrets  # noqa: F401

PLAIN_TEXT = "text/plain; charset=utf-8"
FAKE_PDF = b"%PDF-1.4\n% Fake order for testing\n%%EOF\n"


def _png(width: int = 2, height: int = 3) -> bytes:
    """A real, tiny PNG: grey pixels, nothing drawn from any document."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _drawn(index: int, media_type: str = "image/png") -> dict:
    return {"index": index, "media_type": media_type, "width": 1700, "height": 2200}


def _set(document_id: UUID, **columns) -> None:
    assignments = ", ".join(
        f"{name} = CAST(:{name} AS jsonb)" if name == "pages" else f"{name} = :{name}" for name in columns
    )
    values = {name: json.dumps(value) if name == "pages" else value for name, value in columns.items()}
    with platform_session() as session:
        session.execute(
            text(f"UPDATE documents SET {assignments} WHERE id = :id"), {**values, "id": str(document_id)}
        )


def _with_pages(tenant, entries: list[dict], images: dict[int, bytes] | None = None) -> UUID:
    """A document whose page list the worker wrote (A3), with these images
    stored at their fixed keys."""
    document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
    for index, content in (images or {}).items():
        save_page(tenant.tenant_id, document, index, content, content_type="image/png")
    _set(document, page_count=len(entries), pages=entries)
    return document


def _with_original(tenant, filename: str, content: bytes) -> UUID:
    document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
    _set(document, storage_path=save_file(tenant.tenant_id, filename, content), original_filename=filename)
    return document


def _store_text(tenant, document_id: UUID, content: bytes) -> None:
    """The text the worker kept from the parse (`extracted_text_path`)."""
    path = save_derived(tenant.tenant_id, document_id, "extracted_text", content, content_type=PLAIN_TEXT)
    _set(document_id, extracted_text_path=path)


def _mint(client, tenant, document_id: UUID) -> dict:
    response = client.get(f"/review/documents/{document_id}/original", headers=tenant.headers())
    assert response.status_code == 200
    return response.json()


def _page(client, document_id: UUID, index: int, token: str):
    return client.get(f"/review/documents/{document_id}/pages/{index}", params={"token": token})


# -- what the panel shows -----------------------------------------------------


@requires_review_schema
def test_drawn_pages_come_with_one_page_token_and_are_served_with_their_headers(client):
    with _ReviewTenant("Acme Test Distributor -- pages") as tenant:
        try:
            first, second = _png(2, 3), _png(4, 5)
            document = _with_pages(tenant, [_drawn(0), _drawn(1)], {0: first, 1: second})

            minted = _mint(client, tenant, document)

            assert minted["view"] == "pages"
            assert minted["page_unavailable"]["code"] == "DOC-032"
            assert [(p["index"], p["drawn"], p["width"], p["height"]) for p in minted["pages"]] == [
                (0, True, 1700, 2200),
                (1, True, 1700, 2200),
            ]
            tokens = {p["url"].split("token=")[1] for p in minted["pages"]}
            assert len(tokens) == 1  # one token for the document, minted with the list
            assert all("tenants/" not in p["url"] for p in minted["pages"])

            for page, content in zip(minted["pages"], (first, second), strict=True):
                served = client.get(page["url"])
                assert served.status_code == 200
                assert served.content == content
                assert served.headers["content-type"] == "image/png"
                assert served.headers["cross-origin-resource-policy"] == "cross-origin"
                assert served.headers["content-security-policy"] == "default-src 'none'; sandbox"
                assert served.headers["x-content-type-options"] == "nosniff"
                assert served.headers["cache-control"] == "private, no-store"
                assert served.headers["referrer-policy"] == "no-referrer"
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
def test_a_page_that_wasnt_drawn_has_no_link_and_its_route_is_404(client):
    with _ReviewTenant("Acme Test Distributor -- page not drawn") as tenant:
        try:
            document = _with_pages(
                tenant,
                [_drawn(0), {"index": 1, "rendered": False, "reason": "too_large"}],
                {0: _png()},
            )

            minted = _mint(client, tenant, document)

            assert minted["pages"][1] == {"index": 1, "drawn": False}
            token = minted["pages"][0]["url"].split("token=")[1]
            assert _page(client, document, 1, token).status_code == 404
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
@pytest.mark.parametrize("media_type", ["image/svg+xml", "text/html", "image/gif"])
def test_the_page_route_serves_only_png_and_jpeg(client, media_type, caplog):
    """Whatever the stored list says (founder, 2026-11-04; D-233)."""
    with _ReviewTenant("Acme Test Distributor -- page media type") as tenant:
        try:
            document = _with_pages(tenant, [_drawn(0, media_type)], {0: _png()})
            token, _ = mint_page_token(document, tenant.tenant_id)

            with caplog.at_level(logging.ERROR):
                served = _page(client, document, 0, token)

            assert served.status_code == 404
            assert served.content != _png()
            assert f"page_media_type_refused document_id={document} index=0" in caplog.text
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
def test_a_page_past_the_end_or_of_an_undrawn_document_is_404(client):
    with _ReviewTenant("Acme Test Distributor -- page range") as tenant:
        try:
            drawn = _with_pages(tenant, [_drawn(0)], {0: _png()})
            undrawn = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
            token, _ = mint_page_token(drawn, tenant.tenant_id)
            undrawn_token, _ = mint_page_token(undrawn, tenant.tenant_id)

            for index in (1, 100, -1):
                assert _page(client, drawn, index, token).status_code == 404
            assert _page(client, undrawn, 0, undrawn_token).status_code == 404
            assert _mint(client, tenant, undrawn)["view"] != "pages"
        finally:
            delete_tenant_storage(tenant.tenant_id)


# -- the token ----------------------------------------------------------------


@requires_review_schema
def test_page_original_and_export_tokens_open_only_their_own_routes(client):
    with _ReviewTenant("Acme Test Distributor -- token purposes") as tenant:
        try:
            document = _with_pages(tenant, [_drawn(0)], {0: _png()})
            original = save_file(tenant.tenant_id, "po.png", _png())
            _set(document, storage_path=original, original_filename="po.png")
            page_token, _ = mint_page_token(document, tenant.tenant_id)
            original_token, _ = mint_document_token(document, tenant.tenant_id)
            export_token, _ = mint_export_token(document, tenant.tenant_id)

            # A page token never opens the original, the sender's bytes...
            content_route = f"/review/documents/{document}/original/content"
            for params in ({"token": page_token}, {"token": page_token, "download": 1}):
                assert client.get(content_route, params=params).status_code == 404
            # ...and no other token opens a page.
            for token in (original_token, export_token):
                assert _page(client, document, 0, token).status_code == 404
            # The control: the right token does.
            assert _page(client, document, 0, page_token).status_code == 200
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
def test_a_page_token_for_one_document_opens_no_other_documents_pages(client):
    """The founder's test (2026-11-04): a valid page token with another
    document's id in the path, in the same tenant and in another tenant."""
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        try:
            mine = _with_pages(a, [_drawn(0)], {0: _png()})
            same_tenant = _with_pages(a, [_drawn(0)], {0: _png()})
            other_tenant = _with_pages(b, [_drawn(0)], {0: _png()})
            token, _ = mint_page_token(mine, a.tenant_id)

            for other in (same_tenant, other_tenant):
                assert _page(client, other, 0, token).status_code == 404
            assert _page(client, mine, 0, token).status_code == 200
        finally:
            delete_tenant_storage(a.tenant_id)
            delete_tenant_storage(b.tenant_id)


@requires_review_schema
def test_a_page_token_edited_to_name_another_tenant_is_refused(client):
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        try:
            document = _with_pages(a, [_drawn(0)], {0: _png()})
            token, _ = mint_page_token(document, a.tenant_id)
            forged = token.replace(str(a.tenant_id), str(b.tenant_id))

            assert _page(client, document, 0, forged).status_code == 404
        finally:
            delete_tenant_storage(a.tenant_id)


# -- the frame only while a drawn format has no pages ---------------------------


@requires_review_schema
def test_the_frame_is_the_answer_only_while_a_drawn_format_has_no_pages(client):
    """D-228 item 1's answer: in A4 the frame shows a sender's image, or a TIFF
    or HEIC's stored preview, only while its pages aren't drawn."""
    with _ReviewTenant("Acme Test Distributor -- frame fallback") as tenant:
        try:
            image = _with_original(tenant, "po.png", _png())
            assert _mint(client, tenant, image)["view"] == "frame"
            save_page(tenant.tenant_id, image, 0, _png(), content_type="image/png")
            _set(image, page_count=1, pages=[_drawn(0)])
            assert _mint(client, tenant, image)["view"] == "pages"

            scan = _with_original(tenant, "po.tif", b"II*\x00" + b"\x00" * 64)
            preview = save_derived(tenant.tenant_id, scan, "preview", _png(), content_type="image/png")
            _set(
                scan,
                preview_storage_path=preview,
                preview_media_type="image/png",
                preview_kind="converted_image",
            )
            assert _mint(client, tenant, scan)["view"] == "frame"

            minted = _mint(client, tenant, _with_original(tenant, "po.pdf", FAKE_PDF))
            assert minted["view"] == "panel"
            assert (minted["not_shown"]["code"], minted["no_text"]) == ("DOC-031", None)
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
def test_a_text_file_shows_its_stored_text_and_without_it_says_doc_033(client):
    with _ReviewTenant("Acme Test Distributor -- text view") as tenant:
        try:
            with_text = _with_original(tenant, "po.txt", b"PO Number: BCH-2291\n")
            _store_text(tenant, with_text, b"PO Number: BCH-2291")
            without = _with_original(tenant, "po.csv", b"po_number,total\nBCH-2291,570.00\n")

            assert _mint(client, tenant, with_text)["view"] == "text"
            minted = _mint(client, tenant, without)
            assert (minted["view"], minted["no_text"]["code"]) == ("panel", "DOC-033")
            refused = client.get(f"/review/documents/{without}/text", headers=tenant.headers())
            assert refused.status_code == 404
            assert refused.json()["detail"]["code"] == "DOC-033"
        finally:
            delete_tenant_storage(tenant.tenant_id)


# -- the text route -------------------------------------------------------------


@requires_review_schema
def test_the_text_route_serves_the_stored_text_not_the_senders_bytes(client):
    """Q2 (founder, 2026-11-04): DocFlow's reading, not the original. Markup
    comes back as characters: escaping it is the screen's job, and the
    screen's test proves it."""
    with _ReviewTenant("Acme Test Distributor -- stored text") as tenant:
        try:
            document = _with_original(tenant, "po.txt", b"the sender's own bytes")
            stored = "<b>PO Number</b>: BCH-2291 <script>alert(1)</script>"
            _store_text(tenant, document, stored.encode("utf-8"))

            body = client.get(f"/review/documents/{document}/text", headers=tenant.headers()).json()

            assert body == {"kind": "extracted_text", "text": stored, "truncated": False, "cut_at": 200_000}
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
def test_stored_text_and_text_previews_are_cut_at_200000_characters_and_say_so(client):
    with _ReviewTenant("Acme Test Distributor -- text cut") as tenant:
        try:
            long_text = _with_original(tenant, "po.txt", b"PO Number: BCH-2291\n")
            _store_text(tenant, long_text, b"x" * 200_001)
            # A preview the worker cut: its first 200,000 characters and its note.
            preview = _with_original(tenant, "po.rtf", b"{\\rtf1 PO Number: BCH-2291}")
            cut = ("y" * 200_000 + "\n\n[…truncated for viewing]").encode("utf-8")
            path = save_derived(tenant.tenant_id, preview, "preview", cut, content_type=PLAIN_TEXT)
            _set(
                preview,
                preview_storage_path=path,
                preview_media_type=PLAIN_TEXT,
                preview_kind="extracted_text",
            )

            for document, letter in ((long_text, "x"), (preview, "y")):
                body = client.get(f"/review/documents/{document}/text", headers=tenant.headers()).json()
                assert body["truncated"] is True
                assert body["text"] == letter * 200_000
        finally:
            delete_tenant_storage(tenant.tenant_id)


@requires_review_schema
def test_another_tenants_text_is_404(client):
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        try:
            document = _with_original(a, "po.txt", b"PO Number: BCH-2291\n")
            _store_text(a, document, b"PO Number: BCH-2291")

            assert client.get(f"/review/documents/{document}/text", headers=b.headers()).status_code == 404
        finally:
            delete_tenant_storage(a.tenant_id)


# -- the Console's acting-as mount ----------------------------------------------


@requires_console_schema
@requires_review_schema
def test_pages_and_text_answer_on_the_console_mount(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- acting pages") as tenant:
        try:
            pages = _with_pages(tenant, [_drawn(0)], {0: _png()})
            words = _with_original(tenant, "po.txt", b"PO Number: BCH-2291\n")
            _store_text(tenant, words, b"PO Number: BCH-2291")
            act = _act(tenant.tenant_id)

            minted = client.get(f"{act}/documents/{pages}/original", headers=console.headers())
            assert minted.status_code == 200 and minted.json()["view"] == "pages"
            assert client.get(minted.json()["pages"][0]["url"]).status_code == 200
            # The page route itself on the Console's mount: the page token and
            # the Console's gate; without the founder's sign-in, the gate's 404.
            token = minted.json()["pages"][0]["url"].split("token=", 1)[1]
            page_on_act = f"{act}/documents/{pages}/pages/0"
            on_act = client.get(page_on_act, params={"token": token}, headers=console.headers())
            assert on_act.status_code == 200
            assert client.get(page_on_act, params={"token": token}).status_code == 404
            body = client.get(f"{act}/documents/{words}/text", headers=console.headers())
            assert body.status_code == 200 and body.json()["text"] == "PO Number: BCH-2291"
            # The tenant's own owner on the Console mount: still 404.
            assert client.get(f"{act}/documents/{words}/text", headers=tenant.headers()).status_code == 404
        finally:
            _forget_admin_actions(tenant.tenant_id)
            delete_tenant_storage(tenant.tenant_id)
