"""Which tools can automate each stage of a task: the customer's own tools first, then what adding a tool
would save, in minutes per run. Not tied to any one product: n8n, Power Automate, Python, UiPath... are all
just tools with capabilities.

Customers list their tools by name ("Microsoft 365", "Power BI") or describe any other application by what it
can do ("MyERP: database, reporting"). The share of a stage's time each method removes is a planning
estimate (AUTOMATION_RATES), shown in the report so it can be challenged."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .integration import field_of, source_of
from .models import Task

CAPABILITIES = {
    "email": "read, send and file emails through an API",
    "spreadsheet": "read and write spreadsheet rows through an API",
    "pdf_text": "read the text of PDF files",
    "document_extraction": "pull fields out of PDFs and scans with AI",
    "orchestration": "run multi-step workflows on a schedule and call any API",
    "browser_automation": "fill in and click web pages that have no API",
    "desktop_automation": "drive desktop applications (RPA)",
    "api_wrapper": "build a small API in front of a system that has none",
    "database": "keep records in database tables",
    "reporting": "dashboards and summary reports",
    "api_testing": "try API calls by hand before automating",
    "validation_forms": "data-entry forms with validation rules",
}
SYNONYMS = {"api": "orchestration", "workflow": "orchestration", "integration": "orchestration", "rpa": "desktop_automation",
            "sql": "database", "db": "database", "bi": "reporting", "dashboard": "reporting", "reports": "reporting",
            "ocr": "document_extraction", "browser": "browser_automation", "forms": "validation_forms",
            "mail": "email", "sheets": "spreadsheet", "pdf": "pdf_text"}

# Share of a stage's manual time a method removes, after a person reviews exceptions. Planning estimates.
AUTOMATION_RATES = {"api": 0.95, "database": 0.95, "ai_extraction": 0.85, "pdf_rules": 0.7,
                    "api_wrapper": 0.9, "browser": 0.85, "rpa": 0.8}


@dataclass
class Tool:
    name: str
    aliases: list[str]
    caps: list[str]
    cost: str = ""
    custom: bool = False


CATALOG = [
    Tool("Microsoft 365 (Outlook and Excel through Microsoft Graph)",
         ["microsoft 365", "office 365", "m365", "o365", "microsoft graph", "exchange online", "sharepoint", "onedrive"],
         ["email", "spreadsheet"], "often already licensed; API access needs an Entra ID app and admin consent"),
    Tool("Google Workspace (Gmail, Sheets)", ["google workspace", "gmail", "google sheets", "g suite"],
         ["email", "spreadsheet"], "often already licensed"),
    Tool("Power Automate (cloud flows)", ["power automate"], ["orchestration", "email", "spreadsheet"],
         "standard connectors come with many Microsoft 365 plans; premium connectors cost extra"),
    Tool("Power Automate Desktop", ["power automate desktop", "pad"], ["desktop_automation", "browser_automation", "pdf_text"],
         "free on Windows for attended runs; unattended runs need a licence"),
    Tool("n8n", ["n8n"], ["orchestration", "pdf_text"], "free to self-host, or a paid cloud plan"),
    Tool("Zapier", ["zapier"], ["orchestration", "email", "spreadsheet"], "paid by task volume"),
    Tool("Make", ["make", "make.com", "integromat"], ["orchestration"], "paid by operation volume"),
    Tool("Python scripts (with FastAPI and Playwright)", ["python", "fastapi"],
         ["orchestration", "pdf_text", "api_wrapper", "browser_automation"], "free; needs someone to maintain the code"),
    Tool("Playwright", ["playwright"], ["browser_automation"], "free"),
    Tool("UiPath", ["uipath"], ["desktop_automation", "browser_automation", "orchestration", "pdf_text"], "paid licences"),
    Tool("Azure Document Intelligence", ["azure document intelligence", "document intelligence", "form recognizer"],
         ["document_extraction"], "paid per page"),
    Tool("PostgreSQL", ["postgres", "postgresql"], ["database"], "free"),
    Tool("SQL Server / Azure SQL", ["sql server", "mssql", "azure sql"], ["database"], "licensed"),
    Tool("Power BI", ["power bi", "powerbi"], ["reporting"], "Pro licence per user to share reports"),
    Tool("Looker Studio", ["looker studio", "data studio"], ["reporting"], "free"),
    Tool("Excel (desktop)", ["excel"], ["reporting"], "already installed; no API on its own"),
    Tool("Postman", ["postman"], ["api_testing"], "free tier"),
    Tool("Power Apps / Microsoft Forms", ["power apps", "microsoft forms"], ["validation_forms"], "licensed"),
]
# When several tools fit, suggest the cheapest-to-adopt first.
SUGGEST_ORDER = ["n8n", "Python scripts (with FastAPI and Playwright)", "Playwright", "Azure Document Intelligence",
                 "PostgreSQL", "Looker Studio", "Power Automate Desktop", "Postman"]


def parse_tools(names: list[str]) -> list[dict]:
    """Match the customer's tool list to the catalog; anything else is a custom tool."""
    found = []
    for raw in names:
        text = raw.strip()
        if not text:
            continue
        name, _, caps_text = text.partition(":")
        lowered = name.strip().lower()
        # The longest matching alias wins: "power automate desktop" is not the cloud "power automate".
        hits = [(len(a), t) for t in CATALOG for a in t.aliases if re.search(rf"(^|\b){re.escape(a)}(\b|$)", lowered)]
        tool = max(hits, key=lambda hit: hit[0])[1] if hits else None
        caps = [SYNONYMS.get(c, c) for c in re.findall(r"[a-z_]+", caps_text.lower())]
        caps = [c for c in caps if c in CAPABILITIES]
        if tool and not caps:
            found.append({"given": text, "tool": tool.name, "caps": tool.caps, "note": tool.cost})
        elif caps:
            found.append({"given": text, "tool": name.strip(), "caps": caps, "note": "custom tool"})
        else:
            found.append({"given": text, "tool": name.strip(), "caps": [],
                          "note": "not in the catalog: say what it can do, e.g. 'MyERP: database, reporting'"})
    return found


# ---- stages and their options -------------------------------------------------------------------------

def _options(stage: dict, system: dict | None) -> list[dict]:
    kind = system["kind"] if system else ""
    if stage["stage"] in ("Trigger", "Finish") and kind == "email":
        return [{"method": "Email API", "caps": ["email"], "rate": AUTOMATION_RATES["api"]},
                {"method": "Desktop automation", "caps": ["desktop_automation"], "rate": AUTOMATION_RATES["rpa"]}]
    if stage["stage"] == "Extract" and kind == "pdf":
        return [{"method": "AI document extraction (people review unsure fields)", "caps": ["document_extraction"],
                 "rate": AUTOMATION_RATES["ai_extraction"]},
                {"method": "PDF text + parsing rules (text PDFs only)", "caps": ["pdf_text"],
                 "rate": AUTOMATION_RATES["pdf_rules"]}]
    if stage["stage"] == "Write" and kind == "spreadsheet":
        return [{"method": "Spreadsheet API", "caps": ["spreadsheet"], "rate": AUTOMATION_RATES["api"]},
                {"method": "Database table instead of the workbook", "caps": ["database"], "rate": AUTOMATION_RATES["database"]},
                {"method": "Desktop automation", "caps": ["desktop_automation"], "rate": AUTOMATION_RATES["rpa"]}]
    if stage["stage"] == "Write" and kind == "web":
        return [{"method": "Build a small API in front of the page (browser automation inside)",
                 "caps": ["api_wrapper", "browser_automation"], "rate": AUTOMATION_RATES["api_wrapper"]},
                {"method": "Browser automation", "caps": ["browser_automation"], "rate": AUTOMATION_RATES["browser"]},
                {"method": "Desktop automation", "caps": ["desktop_automation"], "rate": AUTOMATION_RATES["rpa"]}]
    if stage["stage"] in ("Extract", "Write", "Finish"):
        return [{"method": "Desktop automation", "caps": ["desktop_automation"], "rate": AUTOMATION_RATES["rpa"]}]
    return []


def stage_minutes(task: Task, plan: dict) -> dict:
    """Minutes per run spent in each automatable stage, from the recorded step durations."""
    seq = [s for s in plan["sequence"] if s["stage"] in ("Trigger", "Extract", "Write", "Finish")]
    write = {s["system"]: s["n"] for s in seq if s["stage"] == "Write"}
    extract = {s["system"]: s["n"] for s in seq if s["stage"] == "Extract"}
    finish = {s["system"]: s["n"] for s in seq if s["stage"] == "Finish"}
    trigger = next((s for s in seq if s["stage"] == "Trigger"), None)
    known = {s["system"] for s in plan["systems"]}
    unknown = {(r["to"]["system"], r["to"]["field"]) for r in plan["data_flows"]
               if r["how"] == "typed" and source_of(r)[1] == "unknown"}
    seconds: dict = defaultdict(float)
    manual: dict = defaultdict(float)
    current, left_trigger = (trigger["n"] if trigger else None), False
    for step in task.representative:
        app = step.app
        if app in write:
            current = write[app]
        elif app in extract:
            current = extract[app]
        elif trigger and app == trigger["system"]:
            current = finish[app] if left_trigger and app in finish else trigger["n"]
        elif app in finish:
            current = finish[app]
        elif app in known:
            current = "other"
        # anything else (file dialogs, File Explorer) belongs to the stage around it
        if app in known and (not trigger or app != trigger["system"]):
            left_trigger = True
        seconds[current] += step.duration
        if step.chars_typed and (step.app, field_of(step)) in unknown:
            manual[current] += step.duration
    total = sum(seconds.values()) or 1
    per_run = task.minutes / task.runs
    return ({n: per_run * secs / total for n, secs in seconds.items()},
            {n: per_run * secs / total for n, secs in manual.items()})


def _after(stage: dict, option: dict | None) -> float:
    """Minutes left in a stage: values with no known source stay manual until the business decides."""
    if not option:
        return stage["minutes"]
    return stage["manual"] + (stage["minutes"] - stage["manual"]) * (1 - option["rate"])


def _evaluate(stages: list[dict], caps: set[str]) -> float:
    """Minutes per run left after automating every stage the capabilities allow."""
    can_run = bool(caps & {"orchestration", "desktop_automation"})  # something has to run the steps in order
    left = 0.0
    for st in stages:
        option = next((o for o in st["options"] if set(o["caps"]) <= caps), None) if can_run else None
        left += _after(st, option)
    return left


def tool_plan(task: Task, plan: dict, customer_tools: list[str]) -> dict:
    systems = {s["system"]: s for s in plan["systems"]}
    mins, manual = stage_minutes(task, plan)
    stages = []
    for s in plan["sequence"]:
        if s["stage"] not in ("Trigger", "Extract", "Write", "Finish") or not s["system"]:
            continue
        system = systems.get(s["system"])
        stages.append({"n": s["n"], "stage": s["stage"], "system": s["system"], "minutes": mins.get(s["n"], 0.0),
                       "manual": manual.get(s["n"], 0.0), "options": _options(s, system),
                       "vendor_api": next((o for o in (system or {}).get("options", []) if o["method"] == "API"
                                           and system["kind"] in ("web", "desktop")), None)})
    in_stages = {st["n"] for st in stages}
    other = sum(m for n, m in mins.items() if n not in in_stages)  # time no stage can automate

    owned = parse_tools(customer_tools)
    owned_caps = {c for t in owned for c in t["caps"]}
    total = sum(st["minutes"] for st in stages) + other

    def chosen(available: list[dict]) -> list[dict]:
        caps = {c for t in available for c in t["caps"]}
        can_run = bool(caps & {"orchestration", "desktop_automation"})
        picks = []
        for st in stages:
            option = next((o for o in st["options"] if set(o["caps"]) <= caps), None) if can_run else None
            tools = [t["tool"] for t in available if option and set(t["caps"]) & set(option["caps"])]
            picks.append({"n": st["n"], "method": option["method"] if option else "stays manual", "tools": tools,
                          "minutes_after": _after(st, option)})
        return picks

    def runner(available: list[dict]) -> str | None:
        """The tool that runs the steps in order (orchestration first, else an RPA tool)."""
        for cap in ("orchestration", "desktop_automation"):
            hit = next((t["tool"] for t in available if cap in t["caps"]), None)
            if hit:
                return hit
        return None

    # Add tools one at a time, biggest saving first, until nothing more helps.
    caps, steps, current = set(owned_caps), [], _evaluate(stages, owned_caps) + other
    candidates = [t for t in CATALOG if t.name not in {o["tool"] for o in owned}]
    candidates.sort(key=lambda t: SUGGEST_ORDER.index(t.name) if t.name in SUGGEST_ORDER else 99)
    while True:
        best = None
        for tool in candidates:
            left = _evaluate(stages, caps | set(tool.caps)) + other
            if left < current - 0.01 and (best is None or left < best[1] - 0.01):
                best = (tool, left)
        if not best:
            break
        tool, left = best
        steps.append({"tool": tool.name, "adds": [c for c in tool.caps if c not in caps], "cost": tool.cost,
                      "minutes_saved_per_run": round(current - left, 1), "minutes_left_per_run": round(left, 1)})
        caps |= set(tool.caps)
        candidates.remove(tool)
        current = left

    with_yours = _evaluate(stages, owned_caps) + other
    catalog_caps = {t.name: t.caps for t in CATALOG}
    expanded = owned + [{"tool": s["tool"], "caps": catalog_caps[s["tool"]]} for s in steps]
    reporting = [t["tool"] for t in owned if "reporting" in t["caps"]]
    return {
        "customer_tools": owned,
        "stages": [{**st, "with_your_tools": yours, "with_suggestions": best}
                   for st, yours, best in zip(stages, chosen(owned), chosen(expanded))],
        "runner_yours": runner(owned),
        "runner_suggested": runner(expanded),
        "other_minutes": round(other, 1),
        "minutes_per_run": round(total, 1),
        "with_your_tools": round(with_yours, 1),
        "with_suggestions": round(current, 1),
        "suggested_additions": steps,
        "report_tool": reporting[0] if reporting else "Looker Studio (free) or Power BI",
        "rates": AUTOMATION_RATES,
    }
