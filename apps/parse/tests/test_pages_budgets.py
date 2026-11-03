"""
Page images' budgets and caps (viewer A2; D-228), each made to bite by
lowering it. In-process only, on this machine and in CI's unit job: the
running service's settings can't be changed from a test, so CI's image job
doesn't run this file. test_pages.py runs everything else both ways.
"""

from __future__ import annotations

from parse_service import config
from parse_service.parsing import pages
from tests import fixture_builders as fb


def _all_pages(content: bytes, filename: str) -> list[dict]:
    """Every page, asking again from `next_page` as the worker will (A3)."""
    collected: list[dict] = []
    first_page = 0
    while True:
        answer = pages.render(content, filename, first_page)
        collected.extend(answer["pages"])
        if answer["next_page"] is None:
            return collected
        assert answer["next_page"] > first_page
        first_page = answer["next_page"]


def test_an_answer_stops_at_its_budget_and_the_next_one_carries_on(monkeypatch):
    content = fb.build_pdf([f"Acme Test page {i}" for i in range(1, 7)])
    sizes = [len(page["data"]) for page in pages.render(content, "po.pdf", 0)["pages"]]
    monkeypatch.setattr(config, "PAGES_ANSWER_BUDGET_BYTES", sizes[0] + sizes[1])
    first = pages.render(content, "po.pdf", 0)
    assert len(first["pages"]) == 2 and first["next_page"] == 2
    assert [page["index"] for page in _all_pages(content, "po.pdf")] == list(range(6))


def test_an_answer_stops_at_its_time_but_always_carries_a_page(monkeypatch):
    monkeypatch.setattr(config, "PAGES_TIME_BUDGET_SECONDS", -1)
    content = fb.build_pdf(["one", "two", "three"])
    answer = pages.render(content, "po.pdf", 0)
    assert [page["index"] for page in answer["pages"]] == [0]
    assert answer["next_page"] == 1
    assert [page["index"] for page in _all_pages(content, "po.pdf")] == [0, 1, 2]


def test_a_page_over_its_byte_cap_is_answered_as_not_rendered_never_made_worse(monkeypatch):
    monkeypatch.setattr(config, "PAGE_MAX_BYTES", 10)
    answer = pages.render(fb.build_pdf(["one", "two"]), "po.pdf", 0)
    assert answer["pages"] == [
        {"index": 0, "rendered": False, "reason": "too_large"},
        {"index": 1, "rendered": False, "reason": "too_large"},
    ]
