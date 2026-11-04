"""Where a task's web sources come from: the model's own web search first, the plain web search only without it."""
from __future__ import annotations

from flowsme import recommend
from flowsme.models import Task


class Searching:
    """A model whose web search found pages but, as Meta's often does, marked no citations in its answer."""

    def grounded(self, question, schema=None, shared=False):
        page = {"title": "Excel actions reference", "url": "https://learn.microsoft.com/excel", "snippet": ""}
        return {"answer": "Use the Excel actions.", "data": None, "citations": [], "results": [page, dict(page)]}


def test_search_results_used_without_citations(monkeypatch):
    def plain_search(query):
        raise AssertionError("fell back to the plain web search")
    monkeypatch.setattr(recommend, "search_web", plain_search)
    found = recommend._search(Task(id=1, steps=[], name="Enter supplier invoices"), Searching())
    assert [r["url"] for r in found] == ["https://learn.microsoft.com/excel"]
