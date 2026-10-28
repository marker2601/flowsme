"""Analysis and build jobs on the server: the AI settings, the Meta API key and the organisation's rates are all
here, so none of them reach a customer's computer."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import sys
import threading
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config, load_config
from .store import Store

# The server's analysis settings, written to the data folder on first start. The key is read from the
# MODEL_API_KEY environment variable of the server process; without it, reports use the built-in rules.
SERVER_CONFIG = """# FlowSME server: analysis settings. Customers never see this file or the API key.
sessions_dir = "orgs"

[llm]
provider = "openai_compatible"
base_url = "https://api.meta.ai/v1"
api_key_env = "MODEL_API_KEY"
vision_model = "muse-spark-1.3"
text_model = "muse-spark-1.3"
reasoning_effort = "medium"
vision_reasoning_effort = "low"
min_output_tokens = 4096
temperature = 1.0
grounded_search = true
search_context_size = "low"
search_reasoning_effort = "low"
max_cost_usd = 2.0
send_screenshots = true
concurrency = 8
screenshots_per_call = 4
vision_image_width = 1024
timeout = 180.0

[analysis]
runs_per_month = 20
"""


def server_config(data: Path) -> Config:
    path = data / "server.toml"
    if not path.exists():
        path.write_text(SERVER_CONFIG, encoding="utf-8")
    cfg = load_config(path)
    if cfg.llm.provider != "offline":
        import os

        if not os.environ.get(cfg.llm.api_key_env):
            cfg.llm.provider = "offline"  # no key on this server: the built-in rules
    return cfg


class Jobs:
    def __init__(self, store: Store, data: Path, workers: int = 2):
        self.store, self.data = store, data
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="flowsme-job")
        self.lock = threading.Lock()
        store.sweep()
        for org in store.orgs():  # pick up what was waiting when the server stopped
            for record in store.records(org["id"]):
                if record.get("status") in ("queued", "analysing"):
                    self.submit(record)

    def submit(self, record: dict) -> None:
        record.update(status="queued", error=None)
        self.store.save_record(record)
        self.pool.submit(self._run, record["org"], record["id"])

    def claim(self, record: dict) -> None:
        """Queue a re-run unless one is already queued or running (checked and set under one lock)."""
        with self.lock:
            now_on_disk = self.store.record(record["org"], record["id"])
            if now_on_disk["status"] in ("queued", "analysing"):
                raise ValueError("It is already being analysed.")
            record.update(status="queued", error=None)
            self.store.save_record(record)
        self.pool.submit(self._run, record["org"], record["id"])

    def _run(self, org_id: str, rec_id: str) -> None:
        """Whatever happens, the recording ends ready or failed, and the server log says why."""
        try:
            self._analyse(org_id, rec_id)
        except BaseException as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            for _ in range(30):  # a file held by antivirus or a backup: keep trying for a while
                try:
                    record = self.store.record(org_id, rec_id)
                    if record["status"] in ("queued", "analysing"):
                        record.update(status="failed", error=_public(exc))
                        self.store.save_record(record)
                    return
                except KeyError:
                    return  # deleted meanwhile
                except (OSError, ValueError):
                    time.sleep(1)

    def _analyse(self, org_id: str, rec_id: str) -> None:
        from ..analyze import analyze_session

        store = self.store
        record = store.record(org_id, rec_id)
        org = store.org(org_id)
        record.update(status="analysing", log=[])
        store.save_record(record)

        last_save = [0.0]

        def log(line: str) -> None:  # progress for the app; saved at most every 2 seconds
            with self.lock:
                record["log"] = (record["log"] + [str(line)[:300]])[-40:]
                if time.monotonic() - last_save[0] > 2:
                    last_save[0] = time.monotonic()
                    with contextlib.suppress(OSError):
                        store.save_record(record)

        try:
            cfg = server_config(self.data)
            cfg.sessions_dir = store.recordings_dir(org_id)  # the shared AI cache stays inside the organisation
            options = record["options"]
            member = next((m for m in org["members"] if m["seat"] == record["seat"]), None)
            position = record.get("position") or (member or {}).get("position") or org["default_position"]
            if store.rate(org, position) is None:  # the position was removed since: the person's, else the default
                position = next((p for p in ((member or {}).get("position"), org["default_position"])
                                 if store.rate(org, p) is not None), org["positions"][0]["name"])
            an = cfg.analysis
            an.runs_per_month = max(1, int(options.get("runs_per_month") or an.runs_per_month))
            an.position, an.currency, an.show_money = position, org["currency"], True
            an.show_ai_usage = False  # what the analysis cost FlowSME is not the customer's business
            an.hourly_cost = store.rate(org, position) or 0.0
            tools = [str(t)[:80] for t in options.get("tools") or []][:40]
            cfg.customer.tools = tools + [t.strip()[:80] for t in str(options.get("other_tools") or "").split(";")
                                          if t.strip()][:20]
            if record.get("video") and not record.get("session"):  # a screen video: read it first
                from ..video import video_to_session

                folder = store.recordings_dir(org_id) / rec_id
                source = folder / "source" / record["video"]
                made = video_to_session(source, folder, record["title"], cfg, log=log)
                meta = json.loads((made / "meta.json").read_text(encoding="utf-8"))
                record.update(session=made.name, seconds=meta.get("duration_seconds"))
                with self.lock:
                    store.save_record(record)
                source.unlink(missing_ok=True)  # the frames FlowSME needs are in the recording now
            session = store.session_dir(record)
            (session / "analysis").mkdir(exist_ok=True)
            (session / "analysis" / "options.json").write_text(json.dumps(
                {"runs_per_month": an.runs_per_month, "hourly_cost": an.hourly_cost, "currency": an.currency,
                 "position": position, "show_money": True}), encoding="utf-8")
            analyze_session(session, cfg, log=log, hours_copy=True)
            record.update(status="ready", position=position)
        except BaseException as exc:  # noqa: BLE001 - SystemExit included; the record says what went wrong
            if not isinstance(exc, (SystemExit, ValueError)):
                traceback.print_exc(file=sys.stderr)
            record.update(status="failed", error=_public(exc))
        with self.lock:
            store.save_record(record)


def _public(exc: BaseException) -> str:
    """An error message fit for the customer: no server folders in it."""
    text = re.sub(r"([A-Za-z]:)?[\\/][^\s'\"]*[\\/]", "", str(exc))
    return text[:300] or exc.__class__.__name__


# ---- what the client may see -----------------------------------------------------------------------

def summary(store: Store, record: dict, money: bool) -> dict:
    """The Results tab: headline numbers and tasks. Money only for the people allowed to see rates."""
    out = {"status": record["status"], "error": record["error"], "log": record["log"][-8:], "tasks": [],
           "narration": [], "currency": None}
    if not record.get("session"):  # a video not read yet
        return out
    session = store.session_dir(record)
    report = _json(session / "analysis" / "analysis.json")
    if report:
        phases = {i["id"]: i["phase"] for i in ((report["meta"].get("priorities") or {}).get("items") or [])}
        for t in sorted((t for t in report["tasks"] if not t.get("misc")), key=lambda t: -t["score"]):
            est = t.get("estimate") or {}
            out["tasks"].append({
                "id": t["id"], "name": t["name"], "level": t["level"], "score": round(t["score"]),
                "minutes_saved_per_run": est.get("minutes_saved_per_run", 0),
                "hours_saved_per_month": est.get("hours_saved_per_month", 0),
                "money_saved_per_month": est.get("cost_saved_per_month", 0) if money else None,
                "phase": f"with task {t['same_as']}" if t.get("same_as") else phases.get(t["id"], "-"),
                "can_build": bool((t.get("integration") or {}).get("data_flows")) and not t.get("same_as"),
                "has_sop": bool((t.get("improvement") or {}).get("sop"))})
        if money:
            out["currency"] = (_json(session / "analysis" / "options.json") or {}).get("currency", "$")
    turns = (_json(session / "analysis" / "narration.json") or {}).get("turns") or []
    out["narration"] = [{"start": t["start"], "text": t["text"]} for t in turns]
    return out


def report_html(store: Store, record: dict, money: bool) -> str:
    """The report, with its screenshots inlined so it opens anywhere; without money unless allowed."""
    session = store.session_dir(record)
    page = (session / "analysis" / ("report.html" if money else "report-hours.html")).read_text(encoding="utf-8")
    shots = (session / "shots").resolve()

    def inline(match: re.Match) -> str:  # only this recording's screenshots, whatever the page text says
        shot = (session / match.group(1)).resolve()
        if shots not in shot.parents or shot.suffix.lower() not in (".png", ".jpg", ".jpeg") or not shot.is_file():
            return 'src=""'
        kind = "png" if shot.suffix.lower() == ".png" else "jpeg"
        return f'src="data:image/{kind};base64,{base64.b64encode(shot.read_bytes()).decode()}"'
    return re.sub(r'src="\.\./(shots/[^"]+)"', inline, page)


def sop_html(store: Store, record: dict, task_id: int | None) -> str:
    session = store.session_dir(record)
    report = _json(session / "analysis" / "analysis.json") or {"tasks": []}
    tasks = [t for t in report["tasks"] if (t.get("improvement") or {}).get("sop")]
    if task_id is not None:
        tasks = [t for t in tasks if t["id"] == task_id]
    if not tasks:
        raise KeyError("no SOP")
    best = max(tasks, key=lambda t: t["score"])
    name = Path(best["improvement"]["sop"]).name.replace(".md", ".html")
    return (session / "analysis" / name).read_text(encoding="utf-8")


def build_zip(store: Store, record: dict, task_id: int) -> bytes:
    """The automation files for one task, plus each workflow tool's workflow under deploy/ for the client app
    to send to the customer's own tool."""
    from ..build import _spec, build_task, n8n_workflow
    from ..deploy import rendered

    session = store.session_dir(record)
    files = build_task(session, task_id, log=lambda *_: None)
    report = _json(session / "analysis" / "analysis.json") or {"tasks": []}
    task = next(t for t in report["tasks"] if t["id"] == task_id)
    spec = _spec(task)
    docs = rendered(spec, n8n_workflow(task, spec))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files.values():
            archive.write(path, path.name)
        for target, doc in docs.items():
            archive.writestr(f"deploy/{target}.json", json.dumps(doc, ensure_ascii=False, indent=1))
        archive.writestr("deploy/slug.txt", re.sub(r"[^a-z0-9]+", "-", task["name"].lower()).strip("-")[:40])
    return buf.getvalue()


def _json(path: Path) -> dict | None:
    with contextlib.suppress(OSError, ValueError):
        return json.loads(path.read_text(encoding="utf-8"))
    return None
