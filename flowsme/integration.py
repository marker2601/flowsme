"""Integration design for a task, built only from evidence:

  data_flows   - every field-to-field move in the recording (copy-paste or typing), with the step numbers
  systems      - how each application could be connected (API, SQL, file, n8n, browser automation), each
                 option with a status and a link: official documentation, a web search hit, or "unknown"
  sequence     - the automation as ordered stages a business can read and decide on
  ui_actions   - the clicks and fills on each screen, used to generate browser-automation scripts

Values typed by hand have no recorded source; they become open questions instead of guesses."""
from __future__ import annotations

import re
from collections import Counter

from .models import BROWSER_LABELS, Step, Task, base_app
from .schemas import API_CHECK
from .search import search_web

DOCUMENTED = "documented"
FOUND = "found online, confirm"
UNKNOWN = "unknown, ask the vendor or IT"
FALLBACK = "works without an API"
VALUE_WINDOW = 180.0  # seconds to look back for where a typed value was copied or typed before
OPTION = "option"

_CELL = re.compile(r"^([A-Z]{1,3})(\d+)$")
_BUTTON_ROLES = {"ButtonControl", "HyperlinkControl", "MenuItemControl", "SplitButtonControl", "TabItemControl",
                 "ListItemControl", "TreeItemControl"}

GRAPH = "https://learn.microsoft.com/graph"
N8N = "https://docs.n8n.io/integrations/builtin"
OPTIONS = {
    "email": [
        {"method": "API", "name": "Microsoft Graph mail API", "status": DOCUMENTED,
         "url": f"{GRAPH}/api/resources/mail-api-overview",
         "note": "Microsoft 365 / Exchange Online mailboxes. Needs an app registration in Entra ID and admin consent."},
        {"method": "n8n", "name": "n8n Microsoft Outlook node", "status": DOCUMENTED,
         "url": f"{N8N}/app-nodes/n8n-nodes-base.microsoftoutlook/"},
        {"method": "Postman", "name": "Try the Graph calls in Postman first", "status": DOCUMENTED,
         "url": f"{GRAPH}/use-postman"},
    ],
    "spreadsheet": [
        {"method": "API", "name": "Microsoft Graph Excel API", "status": DOCUMENTED, "url": f"{GRAPH}/api/resources/excel",
         "note": "Only for workbooks stored in OneDrive or SharePoint, with the data formatted as an Excel table."},
        {"method": "n8n", "name": "n8n Microsoft Excel 365 node", "status": DOCUMENTED,
         "url": f"{N8N}/app-nodes/n8n-nodes-base.microsoftexcel/"},
        {"method": "SQL", "name": "Keep the tracker in a database table instead", "status": OPTION,
         "url": f"{N8N}/app-nodes/n8n-nodes-base.postgres/",
         "note": "Safer than a shared workbook for automation. `flowsme build` writes a starter SQL script."},
        {"method": "File", "name": "Read and write the file directly (n8n Read/Write Files)", "status": DOCUMENTED,
         "url": f"{N8N}/core-nodes/n8n-nodes-base.readwritefile/",
         "note": "If the workbook stays on a PC or file share."},
    ],
    "pdf": [
        {"method": "n8n", "name": "n8n Extract From File (PDF text)", "status": DOCUMENTED,
         "url": f"{N8N}/core-nodes/n8n-nodes-base.extractfromfile/",
         "note": "Works for PDFs with a text layer, not for scanned images."},
        {"method": "API", "name": "Azure Document Intelligence invoice model", "status": DOCUMENTED,
         "url": "https://learn.microsoft.com/azure/ai-services/document-intelligence/prebuilt/invoice",
         "note": "For scans and varied layouts; paid per page."},
    ],
    "web": [
        {"method": "Postman", "name": "Test the portal's API in Postman once you have its documentation",
         "status": "after API access", "url": "https://learning.postman.com/docs/introduction/overview/"},
        {"method": "SQL", "name": "Write to the portal's database directly", "status": UNKNOWN,
         "note": "Only if your company runs the portal and IT allows direct database access."},
        {"method": "Browser automation", "name": "Playwright script that fills the form", "status": FALLBACK,
         "url": "https://playwright.dev/python/docs/locators",
         "note": "No API needed, but breaks if the page layout changes. `flowsme build` writes a starter script."},
    ],
    "desktop": [
        {"method": "RPA", "name": "Power Automate Desktop or UiPath", "status": FALLBACK,
         "url": "https://learn.microsoft.com/power-automate/desktop-flows/introduction",
         "note": "Screen automation for desktop applications without an API."},
    ],
}


# ---- reading fields from steps ------------------------------------------------------------------

def field_of(step: Step) -> str:
    name = (step.target or "").strip()
    cell = _CELL.match(name)
    if cell:
        return f"column {cell.group(1)}"  # the row changes per item; the column is the field
    return name or "(field not captured)"


def document_of(step: Step) -> str:
    """The file or page a field belongs to; numbers that change per item become '#'."""
    title = step.title or ""
    if title.startswith("["):
        return ""
    parts = [p.strip() for p in title.replace(" — ", " - ").split(" - ") if p.strip()]
    return re.sub(r"\d{3,}", "#", parts[0]) if parts else ""


def endpoint(step: Step) -> dict:
    return {"system": step.app, "document": document_of(step), "field": field_of(step)}


# Typing here moves around (a cell address in Go To or the Name Box, a search term), it doesn't enter data.
_NAV_WINDOWS = re.compile(r"^(go ?to( special)?|find( and replace)?|replace|search|\[closed dialog\])$", re.I)
_NAV_FIELDS = re.compile(r"^(name box|desktop \d*|search( box)?|find what:?)$", re.I)


def _navigation(step: Step) -> bool:
    if step.app in ("System Idle Process", "Unknown app"):  # typed into a window that had already closed
        return True
    return bool(_NAV_WINDOWS.match((step.title or "").strip()) or _NAV_FIELDS.match((step.target or "").strip()))


def label(ep: dict | None) -> str:
    if not ep:
        return "typed by hand"
    doc = ep.get("document")
    show_doc = doc and doc.lower() not in ep["system"].lower()
    return f"{ep['system']}{' · ' + doc if show_doc else ''} · {ep['field']}"


def _key(ep: dict | None) -> tuple:
    return (ep["system"], ep["document"], ep["field"]) if ep else ("",)


def system_kind(system: str, documents: set[str]) -> str:
    base = base_app(system)
    if base == "Outlook":
        return "email"
    if base == "Excel":
        return "spreadsheet"
    if "Acrobat" in base or any(d.lower().endswith(".pdf") for d in documents):
        return "pdf"
    if base in BROWSER_LABELS:
        return "web" if " · " in system else "dialog"  # a browser window without a site: file picker etc.
    if base == "File Explorer":
        return "files"
    return "desktop"


# ---- data flow ------------------------------------------------------------------------------------

def data_flows(task: Task, context: list[Step] | None = None) -> list[dict]:
    """One row per field-to-field move, counted over every run of the task. `context` is the whole
    session, so a value copied just before a run's boundary still counts as that value's source."""
    numbers = {s.index: n for n, s in enumerate(task.representative, 1)}
    rows: dict[tuple, dict] = {}
    history = sorted(context or task.steps, key=lambda s: s.index)
    position = {s.index: i for i, s in enumerate(history)}

    def earlier_value(step: Step, here: dict) -> dict | None:
        """The most recent earlier place (within VALUE_WINDOW seconds) where a typed value was copied or typed."""
        for prev in reversed(history[:position.get(step.index, 0)]):
            if step.start - prev.start > VALUE_WINDOW:
                break
            if prev.excluded:
                continue
            hashes = set(prev.typed_hashes) | ({prev.copy_hash} if prev.copy_hash else set())
            if hashes & set(step.typed_hashes) and _key(endpoint(prev)) != _key(here):
                return endpoint(prev)
        return None

    def add(src, dst, how, step, run_no, chars=0, match=None, fingerprinted=False):
        row = rows.setdefault((how, _key(src), _key(dst)), {
            "from": src, "to": dst, "how": how, "steps": [], "chars": 0, "_runs": set(), "_matches": {},
            "_fingerprinted": set()})
        row["_runs"].add(run_no)
        row["chars"] = max(row["chars"], chars)
        if match:
            row["_matches"][run_no] = match
        if fingerprinted:
            row["_fingerprinted"].add(run_no)
        if step.index in numbers:
            row["steps"].append(numbers[step.index])

    def copied_before(step: Step) -> dict | None:
        """The last copy just before a run starts: a run boundary can fall between a copy and its paste."""
        for prev in reversed(history[:position.get(step.index, 0)]):
            if step.start - prev.start > VALUE_WINDOW:
                break
            if prev.copies and not prev.excluded:
                return endpoint(prev)
        return None

    for run_no, run in enumerate(task.iterations or [task.steps]):
        clipboard = copied_before(run[0]) if run else None
        for step in run:
            if step.excluded:
                continue
            here = endpoint(step)
            if step.pastes:
                src = clipboard or {"system": "?", "document": "", "field": "copied outside the recording"}
                add(src, here, "copy-paste", step, run_no)
            if step.chars_typed and not _navigation(step):
                add(None, here, "typed", step, run_no, step.chars_typed,
                    earlier_value(step, here) if step.typed_hashes else None, bool(step.typed_hashes))
            if step.copies:
                clipboard = here

    flows = sorted(rows.values(), key=lambda r: min(r["steps"]) if r["steps"] else 10**6)
    runs_total = len(task.iterations) or 1
    by_target = {_key(r["to"]): r for r in flows if r["how"] == "copy-paste"}

    def trace(ep):
        """Follow copies back to where a value really came from."""
        seen = set()
        while ep and _key(ep) in by_target and _key(ep) not in seen:
            seen.add(_key(ep))
            ep = by_target[_key(ep)]["from"]
        return ep

    for row in flows:
        runs = len(row.pop("_runs"))
        row["runs"] = f"{runs} of {runs_total}"
        # A typed value whose fingerprint equals a value copied or typed earlier in the same run is proven
        # to be that value (in at least half the runs where it appears).
        by_run = row.pop("_matches")
        fingerprinted = row.pop("_fingerprinted")
        matches = list(by_run.values())
        row["confirmed_source"] = row["value_match"] = None
        row["mismatched_runs"] = []
        if matches:
            best, count = Counter(_key(m) for m in matches).most_common(1)[0]
            if count * 2 >= runs:
                row["confirmed_source"] = next(m for m in matches if _key(m) == best)
                row["value_match"] = f"{count} of {runs} runs"
                # Runs where the value was fingerprinted but did NOT match: a likely wrong-item entry.
                row["mismatched_runs"] = sorted(r + 1 for r in fingerprinted
                                                if r not in by_run or _key(by_run[r]) != best)
        start = row["from"] if row["how"] == "copy-paste" else row["confirmed_source"]
        origin = trace(start)
        row["origin"] = origin if origin is not start else None

    # A typed field with the same name as a field read elsewhere, but no fingerprint match, is only a lead:
    # it is shown as "probably ... confirm" and stays an open question.
    read = {}
    for row in flows:
        if row["how"] == "copy-paste" and row["from"]["system"] != "?":
            src = row["origin"] or row["from"]
            read.setdefault(src["field"].lower(), src)
    for row in flows:
        row["likely_source"] = (read.get(row["to"]["field"].lower())
                                if row["how"] == "typed" and not row["confirmed_source"] else None)
    return flows


def source_of(row: dict) -> tuple[dict | None, str]:
    """Where a value comes from, and how sure we are: copied / same value / narrated / probably / unknown."""
    if row["how"] == "copy-paste":
        if row["from"]["system"] == "?":
            return None, "unknown"
        return row["origin"] or row["from"], "copied"
    if row.get("confirmed_source"):
        return row["origin"] or row["confirmed_source"], "same value"
    if row.get("narrated"):
        return None, "narrated"  # explained in the person's own words (a rule, not a field elsewhere)
    if row.get("likely_source"):
        return row["likely_source"], "probably"
    return None, "unknown"


def open_questions(flows: list[dict]) -> list[str]:
    questions = []
    for row in flows:
        if row["how"] == "typed" and (row.get("confirmed_source") or row.get("narrated")):
            continue  # proven by fingerprint, or explained in the narration: not a question
        if row["how"] == "typed" and row["likely_source"]:
            questions.append(f"Confirm that **{label(row['to'])}** is the same value as "
                             f"**{label(row['likely_source'])}**. They share a field name, but it was typed by hand.")
        elif row["how"] == "typed":
            questions.append(f"Where does **{label(row['to'])}** come from? It was typed by hand "
                             f"({row['chars']} characters). A fixed rule, a lookup, or a person's judgement?")
        elif row["from"]["system"] == "?":
            questions.append(f"What was pasted into **{label(row['to'])}**? It was copied outside the recording.")
    return questions


# ---- systems and how to connect them --------------------------------------------------------------

def _grounded_api_check(name: str, llm) -> tuple[dict | None, list[dict]]:
    """Ask the model, with its own web search, whether `name` has a public API. A documentation URL only
    counts if it appears in the search citations or results: the model can't invent evidence."""
    found = llm.grounded(f'Does the software or web portal called "{name}" offer a public API (REST, SOAP, '
                         "GraphQL, bulk import or webhooks) that another tool could use to create or read records? "
                         "Answer only from search results, and list official documentation URLs.", API_CHECK,
                         shared=True) if llm else None
    if not found or not isinstance(found["data"], dict):
        return None, []
    seen = {c["url"] for c in found["citations"]} | {r["url"] for r in found["results"]}
    urls = [u for u in found["data"].get("documentation_urls") or [] if u in seen]
    evidence = found["results"][:4] or [{**c, "snippet": ""} for c in found["citations"][:4]]
    if found["data"].get("has_public_api") == "yes" and urls:
        return {"method": "API", "name": f"{name} API", "status": FOUND, "url": urls[0],
                "note": f"{found['data'].get('how_to_connect') or ''} Found by a cited web search: confirm it is the "
                        "same product and that you can get access.".strip()}, evidence
    return {"method": "API", "name": f"{name} API", "status": UNKNOWN, "url": "",
            "note": (found["data"].get("notes") or "") + " No cited public API documentation: ask the vendor or IT."
            }, evidence


def systems(task: Task, flows: list[dict], web_search: bool, llm=None) -> list[dict]:
    sources = {r["from"]["system"] for r in flows if r["from"] and r["how"] == "copy-paste"}
    targets = {r["to"]["system"] for r in flows}
    found = []
    for system in task.apps:
        docs = {document_of(s) for s in task.steps if s.app == system} - {""}
        kind = system_kind(system, docs)
        if kind in ("files", "dialog"):
            continue
        role = ("source and target" if system in sources and system in targets else "source" if system in sources
                else "target" if system in targets else "used, no data moved")
        options = [dict(o) for o in OPTIONS.get(kind, [])]
        evidence: list[dict] = []
        if kind in ("web", "desktop"):
            name = system.split(" · ")[-1]
            api, evidence = _grounded_api_check(name, llm) if web_search else (None, [])
            if api is None:  # no grounded search available: plain web search and a name match
                evidence = search_web(f'"{name}" API documentation', max_results=4) if web_search else []
                words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 2]
                hit = next((r for r in evidence if words and all(w in (r["title"] + r["url"]).lower() for w in words)),
                           None)
                api = {"method": "API", "name": f"{name} API",
                       "status": FOUND if hit else UNKNOWN, "url": hit["url"] if hit else "",
                       "note": ("A search result mentions this system's API. Confirm it is the same product and that "
                                "you can get access." if hit else
                                "No public API documentation found in a web search. Internal or vendor portals often "
                                "have one anyway: ask the vendor or IT.")}
            options.insert(0, api)
        found.append({"system": system, "kind": kind, "role": role, "documents": sorted(docs),
                      "options": options, "search_results": evidence})
    return found


def _method(system: dict | None, op: str) -> tuple[str, str]:
    """The connection the sequence proposes for one operation, and its status."""
    if not system:
        return "Manual or screen automation", UNKNOWN
    kind, options = system["kind"], system["options"]
    if kind == "email":
        return ("Microsoft Graph mail API (n8n: Microsoft Outlook node or HTTP Request)", DOCUMENTED)
    if kind == "pdf":
        return ("n8n Extract From File + field parsing; Azure Document Intelligence for scans", DOCUMENTED)
    if kind == "spreadsheet":
        return ("Microsoft Graph Excel API (workbook in OneDrive/SharePoint), or a SQL table", DOCUMENTED)
    api = next((o for o in options if o["method"] == "API"), None)
    if api and api["status"] == FOUND:
        return (f"{api['name']} (n8n HTTP Request); browser automation as a fallback", FOUND)
    if kind == "web":
        return ("No public API found: ask the vendor for an API or bulk upload. Until then, browser "
                "automation (Playwright)", UNKNOWN)
    return ("Screen automation (Power Automate Desktop / UiPath) unless the vendor offers an API", UNKNOWN)


# ---- the automation sequence ----------------------------------------------------------------------

def ui_actions(task: Task, flows: list[dict]) -> list[dict]:
    """What happened on each screen of one run, with where each filled value comes from."""
    sources = {(row["how"], _key(row["to"])): source_of(row) for row in flows}
    actions = []
    for n, step in enumerate(task.representative, 1):
        if step.excluded:
            continue
        ep = endpoint(step)
        if step.pastes or step.chars_typed:
            src, how = sources.get(("copy-paste" if step.pastes else "typed", _key(ep)), (None, "unknown"))
            actions.append({"step": n, "system": step.app, "page": ep["document"], "element": step.target,
                            "role": step.role, "action": "fill", "source": src,
                            "confirmed": how in ("copied", "same value"), "typed_chars": step.chars_typed})
        elif step.action == "click" and step.target:
            actions.append({"step": n, "system": step.app, "page": ep["document"], "element": step.target,
                            "role": step.role, "action": "click", "copies": step.copies})
    return actions


def automation_sequence(task: Task, flows: list[dict], found: list[dict], actions: list[dict]) -> list[dict]:
    by_name = {s["system"]: s for s in found}
    kind_of = {s["system"]: s["kind"] for s in found}
    rep = [s for s in task.representative if not s.excluded]
    seq: list[dict] = []

    def stage(kind, system, what, fields=(), op="read"):
        method, status = _method(by_name.get(system), op) if system else ("n8n Schedule Trigger", OPTION)
        seq.append({"n": len(seq) + 1, "stage": kind, "system": system, "what": what, "fields": list(fields),
                    "method": method, "status": status})

    first = rep[0] if rep else None
    if first and kind_of.get(first.app) == "email":
        files = [a["element"] for a in actions if a["system"] == first.app and "." in a["element"][-6:]]
        stage("Trigger", first.app, "When a new email arrives" +
              (", download its attachment" if files else "") + " (checked on a schedule, e.g. every 15 minutes).")
    else:
        stage("Trigger", "", "Start on a schedule or by hand: the recording shows no clear trigger.")

    # Read every value once from where it really comes from (intermediate copies are skipped).
    origins: dict[str, list[str]] = {}
    targets_of = {_key(r["to"]) for r in flows}
    for row in flows:
        src, how = source_of(row)
        if src and how in ("copied", "same value") and _key(src) not in targets_of:
            origins.setdefault(src["system"], [])
            if src["field"] not in origins[src["system"]]:
                origins[src["system"]].append(src["field"])
    for system, fields in origins.items():
        doc = next((r["from"]["document"] for r in flows if r["from"] and r["from"]["system"] == system), "")
        stage("Extract", system, f"Read {', '.join(fields)} from {doc or system}.", op="read")

    targets: dict[str, list[dict]] = {}
    for row in flows:
        targets.setdefault(row["to"]["system"], []).append(row)
    for system, rows in targets.items():
        mapping = []
        for row in rows:
            src, how = source_of(row)
            if how == "same value":
                mapping.append(f"{row['to']['field']} ← {src['field']} ({src['system']}): typed by hand, but the "
                               f"fingerprints prove it is the same value ({row['value_match']})")
            elif how == "narrated":
                said = row["narrated"]
                mapping.append(f"{row['to']['field']} ← {said['rule'] or said['answer']} (from your narration: "
                               f"\"{said['quote']}\")")
            elif how == "probably":
                mapping.append(f"{row['to']['field']} ← probably {src['field']} ({src['system']}), same field "
                               "name but typed by hand: confirm")
            elif how == "unknown":
                mapping.append(f"{row['to']['field']} ← typed by hand ({row['chars']} characters): open question"
                               if row["how"] == "typed" else f"{row['to']['field']} ← copied outside the recording")
            else:
                mapping.append(f"{row['to']['field']} ← {src['field']} ({src['system']})")
        doc = rows[0]["to"]["document"]
        clicks = [a["element"] for a in actions if a["system"] == system and a["action"] == "click"
                  and a["role"] in _BUTTON_ROLES and not a.get("copies")]
        what = f"Write into {doc or system}" + (f", using: {', '.join(dict.fromkeys(clicks))}" if clicks else "")
        picked = [a["element"] for a in actions if a["system"] not in kind_of and a["action"] == "click"
                  and re.search(r"\.\w{2,4}$", a["element"])]
        if kind_of.get(system) == "web" and picked:  # a file chosen in a browser file dialog = an upload
            what += f", and attach the file ({re.sub(r'[0-9]{3,}', '#', picked[0])})"
        stage("Write", system, what + ".", mapping, op="write")

    last_write = max((a["step"] for a in actions if a["action"] == "fill"), default=0)
    for system in dict.fromkeys(a["system"] for a in actions):
        if system not in kind_of:  # file dialogs and File Explorer are part of other stages
            continue
        tail = [a["element"] for a in actions if a["system"] == system and a["step"] > last_write
                and a["action"] == "click" and system not in targets]
        if tail:
            stage("Finish", system, f"Then in {system}: {' → '.join(tail)}.", op="write")

    for question in open_questions(flows):
        seq.append({"n": len(seq) + 1, "stage": "Decide", "system": "", "what": question.replace("**", ""),
                    "fields": [], "method": "Business decision before building", "status": "open"})
    for point in task.decision_points:
        seq.append({"n": len(seq) + 1, "stage": "Human check", "system": "", "what": point, "fields": [],
                    "method": "Keep a person here, or define the rule", "status": "open"})
    return seq


def plan(task: Task, web_search: bool, context: list[Step] | None = None, llm=None,
         turns: list[dict] | None = None, explained: dict | None = None) -> dict:
    flows = data_flows(task, context)
    if turns:
        from .narration import answer_open_questions  # narration builds on this module

        answer_open_questions(task, flows, turns, llm, explained)
    found = systems(task, flows, web_search, llm)
    actions = ui_actions(task, flows)
    return {"data_flows": flows, "open_questions": open_questions(flows), "systems": found,
            "ui_actions": actions, "sequence": automation_sequence(task, flows, found, actions),
            "answered_by_narration": [{"field": label(r["to"]), **r["narrated"]} for r in flows if r.get("narrated")]}
