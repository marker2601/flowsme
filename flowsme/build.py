"""Turn an approved task into something that runs:

  n8n_workflow.json        import into n8n (or deploy with --deploy-n8n); calls Microsoft Graph and the portal
  postman_collection.json  the same calls, to test each connection by hand first
  tracker.sql              a database table to replace the spreadsheet (if one was written to)
  fill_web_form.py         Playwright script for web forms without an API (if a web form was filled)
  BUILD.md                 the sequence, open questions, and how to use each file

Only what the recording or documentation shows is filled in; everything else is marked with a warning
sign for a person to complete."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .integration import label, source_of

GRAPH = "https://graph.microsoft.com/v1.0"
WARN = "⚠"
ROLE_TO_ARIA = {"ButtonControl": "button", "SplitButtonControl": "button", "HyperlinkControl": "link",
                "MenuItemControl": "menuitem", "TabItemControl": "tab", "ListItemControl": "listitem",
                "CheckBoxControl": "checkbox", "RadioButtonControl": "radio", "ComboBoxControl": "combobox"}


def build_task(session_dir: Path, task_id: int | None = None, deploy: bool = False, log=print) -> dict[str, Path]:
    path = session_dir / "analysis" / "analysis.json"
    if not path.exists():
        raise SystemExit(f"Analyse the session first: python -m flowsme analyze {session_dir.name}")
    tasks = [t for t in json.loads(path.read_text(encoding="utf-8"))["tasks"] if t.get("integration")]
    if not tasks:
        raise SystemExit("No task in this session has an integration design (none had automation potential).")
    if task_id is None:
        task = max(tasks, key=lambda t: t["score"])
    else:
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            raise SystemExit(f"Task {task_id} has no integration design. Tasks that have one: "
                             f"{', '.join(str(t['id']) for t in tasks)}")
    spec = _spec(task)
    out = session_dir / "analysis" / "automations" / f"task{task['id']}"
    out.mkdir(parents=True, exist_ok=True)

    workflow = n8n_workflow(task, spec)
    files = {"n8n workflow": out / "n8n_workflow.json", "Postman collection": out / "postman_collection.json"}
    files["n8n workflow"].write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding="utf-8")
    files["Postman collection"].write_text(json.dumps(postman_collection(task, spec), indent=2, ensure_ascii=False),
                                           encoding="utf-8")
    if spec["sheet"]:
        files["SQL table"] = out / "tracker.sql"
        files["SQL table"].write_text(tracker_sql(spec), encoding="utf-8")
    if spec["web"]:
        files["Playwright script"] = out / "fill_web_form.py"
        files["Playwright script"].write_text(playwright_script(spec), encoding="utf-8")
        files["API for the web form"] = out / "portal_api.py"
        files["API for the web form"].write_text(api_wrapper(spec), encoding="utf-8")
    files["Run log and KPIs"] = out / "report_kpis.sql"
    files["Run log and KPIs"].write_text(kpi_sql(spec), encoding="utf-8")
    stories = (task.get("improvement") or {}).get("stories")
    if stories:
        from .stories import gherkin, stories_markdown

        files["User stories"] = out / "user_stories.md"
        files["User stories"].write_text(stories_markdown(task["name"], stories), encoding="utf-8")
        files["Acceptance tests (Gherkin)"] = out / "acceptance.feature"
        files["Acceptance tests (Gherkin)"].write_text(gherkin(task["name"], stories), encoding="utf-8")
    files["Guide"] = out / "BUILD.md"
    files["Guide"].write_text(build_guide(task, spec, files), encoding="utf-8")
    for name, file in files.items():
        log(f"  {name}: {file}")
    if deploy:
        workflow_id = deploy_n8n(workflow)
        log(f"  Created in n8n (inactive) with id {workflow_id}. Open it, add credentials, test, then activate.")
    return files


# ---- what to build, from the integration design ---------------------------------------------------

def _spec(task: dict) -> dict:
    plan = task["integration"]
    kinds = {s["system"]: s["kind"] for s in plan["systems"]}
    flows = plan["data_flows"]

    def source(row):
        src, how = source_of(row)
        if how == "copied":
            return src, "recorded"
        if how == "same value":
            return src, "recorded (typed by hand; fingerprints prove it is the same value)"
        if how == "narrated":
            said = row["narrated"]
            return None, f"rule from your narration: {said['rule'] or said['answer']}"
        if how == "probably":
            return src, "probably (same field name): confirm"
        if row["how"] == "copy-paste":
            return None, "copied outside the recording: source unknown"
        return None, f"typed by hand ({row['chars']} characters): source unknown"

    pdf_fields = []
    for row in flows:
        src, _ = source(row)
        if src and kinds.get(src["system"]) == "pdf" and src["field"] not in pdf_fields:
            pdf_fields.append(src["field"])

    def target(kind):
        rows = [r for r in flows if kinds.get(r["to"]["system"]) == kind]
        if not rows:
            return None
        system = rows[0]["to"]["system"]
        rows = [r for r in rows if r["to"]["system"] == system]
        fields = []
        for row in rows:
            if any(f["field"] == row["to"]["field"] for f in fields):
                continue
            src, status = source(row)
            fields.append({"field": row["to"]["field"], "source": src, "status": status,
                           "from_pdf": bool(src and kinds.get(src["system"]) == "pdf")})
        if kind == "spreadsheet":
            fields.sort(key=lambda f: (len(f["field"]), f["field"]))  # column A, B, ... AA
        return {"system": system, "document": rows[0]["to"]["document"], "fields": fields}

    seq = plan["sequence"]
    trigger_email = next((s["system"] for s in seq if s["stage"] == "Trigger" and kinds.get(s["system"]) == "email"),
                         None)
    finish = next((s for s in seq if s["stage"] == "Finish" and kinds.get(s["system"]) == "email"), None)
    folder = finish["what"].rstrip(".").split("→")[-1].strip() if finish and "→" in finish["what"] else None
    web = target("web")
    if web:
        steps = [a for a in plan["ui_actions"] if a["system"] == web["system"]]
        first, last = steps[0]["step"], steps[-1]["step"]
        web["actions"] = [a for a in plan["ui_actions"] if first <= a["step"] <= last + 3
                          and (a["system"] == web["system"] or a["system"] not in kinds)]
        web["site"] = web["system"].split(" · ")[-1]
    return {"name": task["name"], "kinds": kinds, "trigger_email": trigger_email, "pdf_fields": pdf_fields,
            "sheet": target("spreadsheet"), "web": web, "move_to_folder": folder,
            "open_questions": plan["open_questions"], "sequence": seq, "systems": plan["systems"]}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "field"


# ---- n8n -------------------------------------------------------------------------------------------

class _Workflow:
    def __init__(self, name: str):
        self.name, self.nodes, self.connections, self.last, self.x = name, [], {}, None, 0

    def add(self, name: str, node_type: str, version: float, params: dict) -> None:
        self.nodes.append({"parameters": params, "id": str(uuid.uuid4()), "name": name, "type": node_type,
                           "typeVersion": version, "position": [self.x, 300]})
        if self.last:
            self.connections.setdefault(self.last, {"main": [[]]})["main"][0].append(
                {"node": name, "type": "main", "index": 0})
        self.last, self.x = name, self.x + 260

    def note(self, content: str, x: int, y: int, width: int, height: int) -> None:
        self.nodes.append({"parameters": {"content": content, "width": width, "height": height},
                           "id": str(uuid.uuid4()), "name": f"Note {len(self.nodes)}",
                           "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [x, y]})

    def code(self, name: str, js: str) -> None:
        self.add(name, "n8n-nodes-base.code", 2, {"jsCode": js})

    def http(self, name: str, method: str, url: str, body: str | None = None, graph: bool = True) -> None:
        params = {"method": method, "url": url, "options": {}}
        params.update({"authentication": "genericCredentialType", "genericAuthType": "oAuth2Api"} if graph
                      else {"authentication": "none"})
        if body:
            params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        self.add(name, "n8n-nodes-base.httpRequest", 4.2, params)

    def json(self) -> dict:
        return {"name": self.name, "nodes": self.nodes, "connections": self.connections,
                "settings": {"executionOrder": "v1"}}


def _value_js(f: dict, fields_var: str = "f") -> str:
    if f["from_pdf"]:
        value = f'{fields_var}.json[{json.dumps(f["source"]["field"])}]'
        return value if f["status"].startswith("recorded") else f"{value} /* {WARN} {f['status']} */"
    note = f"{WARN} {f['field']}: {f['status']}"
    return f'"" /* {note} */'


def n8n_workflow(task: dict, spec: dict) -> dict:
    wf = _Workflow(f"FlowSME: {spec['name']}")
    wf.add("Every 15 minutes", "n8n-nodes-base.scheduleTrigger", 1.2,
           {"rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}})
    if spec["trigger_email"]:
        query = urllib.parse.quote("isRead eq false and hasAttachments eq true")
        wf.http("Get new emails (Graph)", "GET", f"{GRAPH}/me/mailFolders/inbox/messages?$filter={query}&$top=10")
        wf.code("One item per email",
                "// Graph returns { value: [messages] }: make one n8n item per email\n"
                "return $input.all().flatMap((item, i) => (item.json.value || []).map(m => ({\n"
                "  json: { emailId: m.id, subject: m.subject, from: m.from?.emailAddress?.address },\n"
                "  pairedItem: { item: i },\n})));")
    if spec["pdf_fields"]:
        if spec["trigger_email"]:
            wf.http("Get attachments (Graph)", "GET", f"={GRAPH}/me/messages/{{{{ $json.emailId }}}}/attachments")
            wf.code("PDF attachment to file",
                    "// The first PDF attachment of each email becomes a file n8n can read\n"
                    "const emails = $('One item per email').all();\nconst out = [];\n"
                    "$input.all().forEach((item, i) => {\n"
                    "  const pdf = (item.json.value || []).find(a => (a.name || '').toLowerCase().endsWith('.pdf'));\n"
                    "  if (!pdf) return; // no PDF in this email: skip it\n"
                    "  out.push({\n    json: { emailId: emails[i]?.json.emailId, fileName: pdf.name },\n"
                    "    binary: { data: { data: pdf.contentBytes, mimeType: 'application/pdf', fileName: pdf.name } },\n"
                    "    pairedItem: { item: i },\n  });\n});\nreturn out;")
        wf.add("Read PDF text", "n8n-nodes-base.extractFromFile", 1, {"operation": "pdf", "options": {}})
        source_node = "PDF attachment to file" if spec["trigger_email"] else None
        carry = (f"const files = $('{source_node}').all();\n" if source_node else "const files = [];\n")
        wf.code("Pick out fields",
                f"// {WARN} Check these patterns on a few real PDFs: each looks for '<label>: value' on one line.\n"
                f"const labels = {json.dumps(spec['pdf_fields'])};\n{carry}"
                "return $input.all().map((item, i) => {\n  const text = item.json.text || '';\n"
                "  const fields = { emailId: files[i]?.json.emailId, fileName: files[i]?.json.fileName };\n"
                "  for (const label of labels) {\n"
                "    const escaped = label.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');\n"
                "    const match = text.match(new RegExp(escaped + '\\\\s*[:#]?\\\\s*(.+)', 'i'));\n"
                "    fields[label] = match ? match[1].trim() : null;\n  }\n"
                "  return { json: fields, pairedItem: { item: i } };\n});")
    fields_node = "Pick out fields" if spec["pdf_fields"] else None
    fields_js = f"$('{fields_node}').all()" if fields_node else "$input.all()"

    if spec["sheet"]:
        sheet = spec["sheet"]
        values = ",\n    ".join(_value_js(f) for f in sheet["fields"])
        wf.code(f"Row for {sheet['document'] or 'the sheet'}",
                f"// Column order: {', '.join(f['field'] for f in sheet['fields'])}\n"
                f"return {fields_js}.map((f, i) => ({{\n  json: {{ values: [[\n    {values}\n  ]] }},\n"
                "  pairedItem: { item: i },\n}));")
        workbook = urllib.parse.quote(sheet["document"] or "workbook.xlsx")
        wf.http(f"Add row to {sheet['document'] or 'the sheet'} (Graph)", "POST",
                f"{GRAPH}/me/drive/root:/{WARN}folder/{workbook}:/workbook/tables/{WARN}Table1/rows/add",
                body="={{ JSON.stringify({ values: $json.values }) }}")
    if spec["web"]:
        web = spec["web"]
        body = ",\n    ".join(f"{json.dumps(f['field'])}: {_value_js(f)}" for f in web["fields"])
        wf.code(f"Request for {web['site']}",
                f"// {WARN} Field names copied from the form; the real API may name them differently.\n"
                f"return {fields_js}.map((f, i) => ({{\n  json: {{ body: {{\n    {body}\n  }} }},\n"
                "  pairedItem: { item: i },\n}));")
        wf.http(f"Send to {web['site']} API ({WARN} unknown)", "POST",
                f"https://{WARN}portal-api-url/{_slug(web['document'] or 'records')}",
                body="={{ JSON.stringify($json.body) }}", graph=False)
    if spec["trigger_email"] and spec["move_to_folder"]:
        email_ref = f"$('{fields_node}').all()[$itemIndex].json.emailId" if fields_node else "$json.emailId"
        wf.http(f"Move email to {spec['move_to_folder']} (Graph)", "POST",
                f"={GRAPH}/me/messages/{{{{ {email_ref} }}}}/move",
                body=f'={{{{ JSON.stringify({{ destinationId: "{WARN} id of the {spec["move_to_folder"]} folder" }}) }}}}')

    lines = [f"## {spec['name']}", "Generated by FlowSME from a recording. Nothing here has run yet.", "",
             "**Before the first run**",
             "1. Create an OAuth2 credential for Microsoft Graph (Entra ID app; scopes `Mail.ReadWrite "
             "Files.ReadWrite offline_access`) and select it on every *(Graph)* node.",
             f"2. Replace every {WARN} value (file path, table name, folder id, portal URL).",
             "3. Run once by hand with a test email and check each node's output.", "",
             "**Open questions from the recording**"]
    lines += [f"- {q.replace('**', '')}" for q in spec["open_questions"]] or ["- none"]
    wf.note("\n".join(lines), x=0, y=-260, width=max(520, 260 * 4), height=360)
    return wf.json()


# ---- Postman ---------------------------------------------------------------------------------------

def postman_collection(task: dict, spec: dict) -> dict:
    def request(name, method, url, body=None, description=""):
        req = {"method": method, "header": [{"key": "Content-Type", "value": "application/json"}], "url": url,
               "description": description}
        if body is not None:
            req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2, ensure_ascii=False),
                           "options": {"raw": {"language": "json"}}}
        return {"name": name, "request": req}

    items = []
    variables = [{"key": "graph_token", "value": "", "description": "Access token from Graph Explorer or your Entra app"}]
    if spec["trigger_email"]:
        items.append(request("1. List new emails with attachments", "GET",
                             f"{GRAPH}/me/mailFolders/inbox/messages?$filter=isRead eq false and hasAttachments eq true&$top=10",
                             description="Copy one message id into the email_id variable."))
        items.append(request("2. List attachments of one email", "GET", f"{GRAPH}/me/messages/{{{{email_id}}}}/attachments"))
        variables.append({"key": "email_id", "value": ""})
    if spec["sheet"]:
        sheet = spec["sheet"]
        row = [f"<{f['source']['field']}>" if f["source"] else f"<{f['field']}: {WARN} source unknown>"
               for f in sheet["fields"]]
        items.append(request(f"3. Add a row to {sheet['document']}", "POST",
                             f"{GRAPH}/me/drive/root:/{{{{workbook_path}}}}:/workbook/tables/{{{{table_name}}}}/rows/add",
                             {"values": [row]}, description="Columns in order: " +
                             ", ".join(f["field"] for f in sheet["fields"])))
        variables += [{"key": "workbook_path", "value": f"folder/{sheet['document']}"},
                      {"key": "table_name", "value": "Table1"}]
    if spec["trigger_email"] and spec["move_to_folder"]:
        items.append(request(f"4. Move the email to {spec['move_to_folder']}", "POST",
                             f"{GRAPH}/me/messages/{{{{email_id}}}}/move",
                             {"destinationId": "{{processed_folder_id}}"},
                             description=f"Find the {spec['move_to_folder']} folder id with GET {GRAPH}/me/mailFolders"))
        variables.append({"key": "processed_folder_id", "value": ""})
    if spec["web"]:
        web = spec["web"]
        body = {f["field"]: (f"<{f['source']['field']}>" if f["source"] else f"<{WARN} source unknown>")
                for f in web["fields"]}
        items.append(request(f"5. Create a record in {web['site']} ({WARN} API unknown)", "POST",
                             "{{portal_api_url}}", body,
                             description="No public API was found. Ask the vendor for the endpoint, its field "
                                         "names and how to authenticate, then update this request."))
        variables.append({"key": "portal_api_url", "value": ""})
    return {
        "info": {"name": f"FlowSME: {spec['name']}", "_postman_id": str(uuid.uuid4()),
                 "description": "Each call the automation makes, to test connections before building the flow.",
                 "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{graph_token}}", "type": "string"}]},
        "item": items,
        "variable": variables,
    }


# ---- SQL -------------------------------------------------------------------------------------------

def tracker_sql(spec: dict) -> str:
    sheet = spec["sheet"]
    table = _slug(Path(sheet["document"] or "tracker").stem)
    names, cols = [], []
    for f in sheet["fields"]:
        name = _slug(f["source"]["field"]) if f["status"].startswith("recorded") else _slug(f["field"])
        while name in names:
            name += "_2"
        names.append(name)
        note = f"{f['field']} <- {label(f['source'])}" if f["source"] else f"{WARN} {f['field']}: {f['status']}"
        cols.append(f"    {name:<20} TEXT,  -- {note}")
    params = ", ".join(f"${i}" for i in range(1, len(names) + 1))
    return "\n".join([
        f"-- Table to replace {sheet['document']}, generated from the recording (PostgreSQL syntax).",
        f"-- {WARN} Every column is TEXT: choose real types (dates, amounts) once the data is confirmed.",
        f"CREATE TABLE IF NOT EXISTS {table} (",
        "    id                   SERIAL PRIMARY KEY,",
        *cols,
        "    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ");",
        "",
        "-- One row per processed item. In n8n, use the Postgres node with these parameters.",
        f"INSERT INTO {table} ({', '.join(names)})",
        f"VALUES ({params});",
        "",
    ])


# ---- Playwright ------------------------------------------------------------------------------------

def _form_actions(web: dict, indent: str, attachment_expr: str) -> tuple[list[str], bool]:
    """The recorded clicks and fills on the web form, as Playwright calls."""
    body, attach = [], False
    for a in web["actions"]:
        name = json.dumps(a["element"])
        if a["system"] != web["system"]:  # file dialog after "Attach file": handled by the file chooser
            continue
        if a["action"] == "fill":
            body.append(f"{indent}page.get_by_label({name}).fill(record.get({name}, \"\"))  # step {a['step']}")
        elif re.search(r"attach|upload|browse", a["element"], re.I):
            attach = True
            body += [f"{indent}with page.expect_file_chooser() as chooser:  # steps {a['step']}+ (file dialog)",
                     f"{indent}    page.get_by_role(\"button\", name={name}).click()",
                     f"{indent}chooser.value.set_files({attachment_expr})"]
        else:
            role = ROLE_TO_ARIA.get(a["role"], "button")
            body.append(f"{indent}page.get_by_role(\"{role}\", name={name}).click()  # step {a['step']}")
    return body, attach


def api_wrapper(spec: dict) -> str:
    """A small API in front of a web form that has none, so any tool can call it."""
    web = spec["web"]
    body, attach = _form_actions(web, "        ", "attachment")
    fields = "\n".join(f"    {_slug(f['field'])}: str = Field(\"\", alias={json.dumps(f['field'])})"
                       f"  # {label(f['source']) if f['source'] else WARN + ' ' + f['status']}"
                       for f in web["fields"])
    attach_fields = ("\n    attachment_name: str | None = None\n    attachment_base64: str | None = None"
                     if attach else "")
    attach_code = ('''    path = None
    if record.attachment_base64:
        path = Path(tempfile.mkdtemp()) / (record.attachment_name or "attachment.pdf")
        path.write_bytes(base64.b64decode(record.attachment_base64))
''' if attach else "    path = None\n")
    return f'''"""An API for the "{web['document']}" form on {web['site']}, which has none of its own.

Any tool can now call it (n8n, Power Automate, Zapier, a script): POST /records with JSON, header X-API-Key.
Behind the API, Playwright fills in the form exactly as recorded. {WARN} Ask the vendor for a real API first:
a page change can break this. Generated by FlowSME.

  pip install fastapi uvicorn playwright && playwright install chromium
  set PORTAL_URL=https://...               ({WARN} the page before the form)
  set WRAPPER_API_KEY=<a long random string, shared only with the calling tool>
  python portal_api.py --login             (sign in once; saves the session to portal_login.json)
  uvicorn portal_api:app --port 8000
"""
import base64
import os
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, ConfigDict, Field

PORTAL_URL = os.environ.get("PORTAL_URL", "")
API_KEY = os.environ.get("WRAPPER_API_KEY", "")
LOGIN_STATE = Path("portal_login.json")

app = FastAPI(title="{web['site']}: {web['document']} API (generated)")


class Record(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
{fields}{attach_fields}


def fill_form(record: dict, attachment: str | None) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(storage_state=str(LOGIN_STATE) if LOGIN_STATE.exists() else None)
        page = context.new_page()
        page.goto(PORTAL_URL)
{chr(10).join(body) if body else "        pass"}
        # {WARN} Replace with the locator of the confirmation or reference number the portal shows.
        result = page.locator("body").inner_text()[:300]
        browser.close()
        return result


@app.post("/records")
async def create_record(record: Record, x_api_key: str = Header("")):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Missing or wrong X-API-Key")
{attach_code}    result = await run_in_threadpool(fill_form, record.model_dump(by_alias=True), str(path) if path else None)
    return {{"status": "submitted", "portal_says": result}}


def login() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.new_page().goto(PORTAL_URL)
        input("Sign in in the browser window, then press Enter here... ")
        context.storage_state(path=str(LOGIN_STATE))
        browser.close()


if __name__ == "__main__" and "--login" in sys.argv:
    login()
'''


def kpi_sql(spec: dict) -> str:
    """A run log (the Control step) and the views the final report reads."""
    return "\n".join([
        f"-- Control and reporting for: {spec['name']} (PostgreSQL syntax). Generated by FlowSME.",
        "-- The automation writes one row per item and stage. Power BI, Looker Studio or Excel read the views.",
        "CREATE TABLE IF NOT EXISTS automation_log (",
        "    id           SERIAL PRIMARY KEY,",
        "    item_ref     TEXT NOT NULL,          -- e.g. the invoice number",
        "    stage        TEXT NOT NULL,          -- Trigger, Extract, Write, Finish",
        "    status       TEXT NOT NULL CHECK (status IN ('done', 'exception', 'failed', 'duplicate')),",
        "    check_failed TEXT,                   -- which validation check stopped it, if any",
        "    started_at   TIMESTAMP NOT NULL,",
        "    finished_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        ");",
        "",
        "-- Items per day, and how many went straight through (first-pass yield)",
        "CREATE OR REPLACE VIEW kpi_daily AS",
        "SELECT date(finished_at) AS day,",
        "       count(DISTINCT item_ref) AS items,",
        "       count(DISTINCT item_ref) FILTER (WHERE status = 'exception') AS exceptions,",
        "       count(DISTINCT item_ref) FILTER (WHERE status = 'duplicate') AS duplicates_blocked,",
        "       round(100.0 * count(DISTINCT item_ref) FILTER (WHERE item_ref NOT IN (",
        "           SELECT item_ref FROM automation_log WHERE status <> 'done')) / NULLIF(count(DISTINCT item_ref), 0), 1)",
        "           AS first_pass_yield_pct",
        "FROM automation_log GROUP BY date(finished_at);",
        "",
        "-- Which checks catch the most problems (Pareto)",
        "CREATE OR REPLACE VIEW kpi_checks AS",
        "SELECT check_failed, count(*) AS times FROM automation_log",
        "WHERE check_failed IS NOT NULL GROUP BY check_failed ORDER BY times DESC;",
        "",
        "-- End-to-end time per item",
        "CREATE OR REPLACE VIEW kpi_cycle_time AS",
        "SELECT item_ref, min(started_at) AS started, max(finished_at) AS finished,",
        "       extract(epoch FROM max(finished_at) - min(started_at)) / 60 AS minutes",
        "FROM automation_log GROUP BY item_ref;",
        "",
    ])


def playwright_script(spec: dict) -> str:
    web = spec["web"]
    mapping = "\n".join(
        f"    {json.dumps(f['field'])}: {json.dumps(f['source']['field'] if f['source'] else None)},"
        f"  # {label(f['source']) + ' (' + f['status'] + ')' if f['source'] else WARN + ' ' + f['status']}"
        for f in web["fields"])
    body, attach_pending = _form_actions(web, "    ", 'record["attachment"]')
    columns = [f["field"] for f in web["fields"]] + (["attachment"] if attach_pending else [])
    return f'''"""Fill the "{web['document']}" form in {web['site']}, generated from the recording.

Use this only if the portal has no API. {WARN} Set PORTAL_URL and check each locator once: they use the
labels and button names the recording captured, which usually match what Playwright sees.

  pip install playwright && playwright install chromium
  python fill_web_form.py items.csv

items.csv needs these columns: {", ".join(columns)}
"""
import csv
import sys

from playwright.sync_api import sync_playwright

PORTAL_URL = "{WARN} https://the-portal/page-before-the-form"

# Form field -> where its value comes from in the recording
SOURCES = {{
{mapping}
}}


def submit(page, record: dict) -> None:
{chr(10).join(body) if body else "    pass"}


def main(csv_path: str) -> None:
    with open(csv_path, encoding="utf-8") as fp:
        records = list(csv.DictReader(fp))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(PORTAL_URL)
        input("Sign in to the portal in the browser window, then press Enter here... ")
        for record in records:
            page.goto(PORTAL_URL)
            submit(page, record)
            print("submitted:", {{k: record.get(k) for k in list(SOURCES)[:2]}})
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1])
'''


# ---- guide -----------------------------------------------------------------------------------------

POWER_AUTOMATE_STEPS = {
    ("Trigger", "email"): "Office 365 Outlook: *When a new email arrives (V3)*, with *Only with attachments* on",
    ("Extract", "pdf"): "AI Builder: *Extract information from invoices* (premium), or HTTP to Azure Document Intelligence",
    ("Write", "spreadsheet"): "Excel Online (Business): *Add a row into a table*",
    ("Write", "web"): "HTTP (premium): POST to `portal_api.py` at /records with the X-API-Key header, or a "
                      "Power Automate Desktop flow that fills in the form",
    ("Finish", "email"): "Office 365 Outlook: *Move email (V2)*",
}


def build_guide(task: dict, spec: dict, files: dict[str, Path]) -> str:
    improvement = task.get("improvement") or {}
    tools = improvement.get("tools") or {}
    kinds = spec["kinds"]
    lines = [f"# Build guide: {spec['name']}", "",
             "Generated from the recording. Values marked with a warning sign must be filled in by a person.", ""]
    if tools:
        given = ", ".join(t["given"] for t in tools["customer_tools"]) or "none given"
        lines += ["## Tools", "",
                  f"- **Your tools:** {given}",
                  f"- **Runs the workflow:** {tools['runner_yours'] or 'nothing you listed can'}"
                  + (f" (suggested: {tools['runner_suggested']})" if tools["runner_suggested"] != tools["runner_yours"]
                     else ""),
                  f"- **Hands-on minutes per item:** {tools['minutes_per_run']} now, {tools['with_your_tools']} with "
                  f"your tools, {tools['with_suggestions']} with the suggested additions", ""]
        lines += ["| Stage | Now (min/item) | With your tools | With suggested tools |", "|---|---|---|---|"]
        for st in tools["stages"]:
            yours, best = st["with_your_tools"], st["with_suggestions"]
            lines.append(f"| {st['n']}. {st['stage']} · {st['system']} | {st['minutes']:.1f} | {yours['method']}"
                         f"{' (' + ', '.join(yours['tools']) + ')' if yours['tools'] else ''} | {best['method']}"
                         f"{' (' + ', '.join(best['tools']) + ')' if best['tools'] else ''} |")
        lines.append("")
        runner = tools["runner_yours"] or tools["runner_suggested"] or ""
        if "Power Automate (cloud" in runner:
            lines += ["### Building it in Power Automate", ""]
            for st in tools["stages"]:
                step = POWER_AUTOMATE_STEPS.get((st["stage"], kinds.get(st["system"], "")))
                if step:
                    lines.append(f"{st['n']}. {st['stage']} · {st['system']}: {step}")
            lines += ["", "Add a *Condition* after each write for the checks below, and log each item "
                      "(see `report_kpis.sql`).", ""]

    lines += ["## Automation sequence", ""]
    for s in spec["sequence"]:
        lines.append(f"{s['n']}. **{s['stage']}**{' · ' + s['system'] if s['system'] else ''}: {s['what']}")
        lines += [f"   - {f}" for f in s["fields"]]
        lines.append(f"   - How: {s['method']} *({s['status']})*")
    lines += ["", "## Decide before building", ""]
    lines += [f"- {q}" for q in spec["open_questions"]] or ["- Nothing open."]
    checks = (improvement.get("validation") or {}).get("checks", [])
    if checks:
        lines += ["", "## Validation checklist", "", "| Where | Type | Check | Evidence |", "|---|---|---|---|"]
        lines += [f"| {c['where']} | {c['type']} | {c['check']} | {c['evidence']} |" for c in checks]
    lean = improvement.get("lean") or {}
    if lean.get("kpis"):
        lines += ["", "## Measure after go-live (final report)", ""] + [f"- {k}" for k in lean["kpis"]]
    lines += ["", "## Files", ""]
    howto = {
        "n8n workflow": "One way to run it, if you use n8n. **Workflows → Import from file**, add a Microsoft Graph "
                        "OAuth2 credential, replace the warning-sign values, test with one email, then activate. "
                        "`--deploy-n8n` creates it for you when N8N_URL and N8N_API_KEY are set.",
        "Postman collection": "**Import** into Postman, set `graph_token` and the other variables, and send the "
                              "requests in order to prove each connection works before building anything.",
        "SQL table": "Optional: keep the tracker in a database table instead of a workbook.",
        "Playwright script": "Fills the web form from a CSV, for one-off batches while there is no API.",
        "API for the web form": "Gives the web form an API so any tool (n8n, Power Automate HTTP, Zapier, scripts) can "
                                "submit to it with an API key. Use it until the vendor provides a real API.",
        "Run log and KPIs": "The control step: one log row per item, plus views for Power BI, Looker Studio or Excel.",
    }
    for name, path in files.items():
        if name in howto:
            lines.append(f"- `{path.name}`: {howto[name]}")
    lines += ["", "## Connection options per system", ""]
    for s in spec["systems"]:
        lines.append(f"**{s['system']}** ({s['kind']}, {s['role']})")
        for o in s["options"]:
            link = f" ([docs]({o['url']}))" if o.get("url") else ""
            lines.append(f"- {o['method']}: {o['name']}: *{o['status']}*{link}. {o.get('note', '')}".rstrip())
        lines.append("")
    return "\n".join(lines)


# ---- deploy ----------------------------------------------------------------------------------------

def deploy_n8n(workflow: dict) -> str:
    url, key = os.environ.get("N8N_URL"), os.environ.get("N8N_API_KEY")
    if not url or not key:
        raise SystemExit("Set N8N_URL (e.g. http://localhost:5678) and N8N_API_KEY (n8n: Settings → n8n API) "
                         "to deploy, or import n8n_workflow.json by hand.")
    body = {k: workflow[k] for k in ("name", "nodes", "connections", "settings")}
    request = urllib.request.Request(f"{url.rstrip('/')}/api/v1/workflows", data=json.dumps(body).encode(),
                                     headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read()).get("id", "?")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"n8n refused the workflow ({exc.code}): {exc.read().decode(errors='replace')[:300]}")
