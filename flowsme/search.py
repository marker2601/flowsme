"""Find existing tools for a task: a web search (DuckDuckGo) plus a built-in catalog of common options."""
from __future__ import annotations

from .models import BROWSER_LABELS, Task, base_app

CATALOG = [
    {"name": "Microsoft Power Automate", "url": "https://www.microsoft.com/power-platform/products/power-automate",
     "fit": "Desktop flows (RPA) plus cloud flows for Outlook, Excel, SharePoint, Teams and many other connectors",
     "tags": {"excel", "outlook", "word", "teams", "file explorer", "rpa", "email"}},
    {"name": "UiPath", "url": "https://www.uipath.com",
     "fit": "Enterprise RPA for desktop and legacy apps without APIs (SAP GUI, Citrix, mainframe screens)",
     "tags": {"sap gui", "rpa"}},
    {"name": "n8n", "url": "https://n8n.io",
     "fit": "Self-hostable workflow automation that connects web apps through their APIs, with AI steps",
     "tags": {"api", "web", "email"}},
    {"name": "Zapier", "url": "https://zapier.com",
     "fit": "No-code integrations between cloud apps", "tags": {"api", "web", "email"}},
    {"name": "Make", "url": "https://www.make.com",
     "fit": "Visual multi-step integrations between cloud apps", "tags": {"api", "web"}},
    {"name": "Excel Power Query and Office Scripts", "url": "https://learn.microsoft.com/office/dev/scripts/overview/excel",
     "fit": "Repeatable imports, clean-up and transformations inside Excel", "tags": {"excel"}},
    {"name": "Python with pandas / openpyxl", "url": "https://pandas.pydata.org",
     "fit": "Scripts that read, merge and write spreadsheets and CSV files", "tags": {"excel"}},
    {"name": "Playwright", "url": "https://playwright.dev/python/",
     "fit": "Scripted browser automation for web portals that have no API", "tags": {"web"}},
    {"name": "Document extraction (Azure Document Intelligence, Google Document AI, AWS Textract)",
     "url": "https://learn.microsoft.com/azure/ai-services/document-intelligence/",
     "fit": "Reads fields out of invoices, receipts, forms and PDFs", "tags": {"pdf"}},
]


def task_tags(task: Task) -> set[str]:
    tags: set[str] = set()
    for app in task.apps:
        base = base_app(app)
        tags.add(base.lower())
        if base in BROWSER_LABELS:
            tags.add("web")
        if "acrobat" in base.lower():
            tags.add("pdf")
    if any(".pdf" in s.title.lower() for s in task.steps):
        tags.add("pdf")
    if "outlook" in tags:
        tags.add("email")
    if task.repeated:
        tags.add("rpa")
    return tags


def catalog_matches(task: Task, limit: int = 4) -> list[dict]:
    tags = task_tags(task)
    scored = [(len(tags & item["tags"]), i, item) for i, item in enumerate(CATALOG)]
    return [{k: v for k, v in item.items() if k != "tags"}
            for overlap, _, item in sorted(scored, key=lambda x: (-x[0], x[1])) if overlap][:limit]


def search_web(query: str, max_results: int = 4) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    try:
        results = DDGS().text(query, max_results=max_results) or []
    except Exception:
        return []  # offline, rate-limited, or the search backend changed
    return [{"title": r.get("title", ""), "url": r.get("href") or r.get("url", ""),
             "snippet": (r.get("body") or "")[:300]} for r in results if r.get("href") or r.get("url")]
