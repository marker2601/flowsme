"""The FlowSME server's API. Customers' apps sign in with a seat key and get results; everything that makes the
results (the analysis, prompts, knowledge base, automation builders and the AI key) runs only here.

Employees see their own recordings and hours saved. Managers see the whole team, money at each position's
rate, and the Admin panel's seats and rates. A revoked seat stops working on its next request."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response

from .. import __version__
from .jobs import Jobs, build_zip, report_html, sop_html, summary
from .store import Store

MAX_UPLOAD = 1024 ** 3  # 1 GB per recording


def create_app(data: Path, workers: int = 2) -> FastAPI:
    data = Path(data)
    store = Store(data)
    jobs = Jobs(store, data, workers)
    app = FastAPI(title="FlowSME", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.jobs = store, jobs

    def caller(authorization: str = Header("")) -> tuple[dict, dict]:
        key = authorization.removeprefix("Bearer").strip()
        found = store.who(key) if key else None
        if not found:
            raise HTTPException(401, "Sign in with your FlowSME seat key.")
        org, member = found
        if member["status"] != "active":
            raise HTTPException(403, "Your seat was revoked. Ask your manager for a new one.")
        return org, member

    def manager(who: tuple[dict, dict] = Depends(caller)) -> tuple[dict, dict]:
        if who[1]["role"] != "manager":
            raise HTTPException(403, "Only your team's manager can do this.")
        return who

    def visible(who: tuple[dict, dict], rec_id: str) -> dict:
        org, member = who
        try:
            record = store.record(org["id"], rec_id)
        except KeyError:
            raise HTTPException(404, "No such recording.") from None
        if member["role"] != "manager" and record["seat"] != member["seat"]:
            raise HTTPException(404, "No such recording.")
        return record

    def names(org: dict) -> dict:
        return {m["seat"]: m["name"] for m in org["members"]}

    def listing(org: dict, record: dict, manager_view: bool) -> dict:
        row = {k: record.get(k) for k in ("id", "title", "started", "seconds", "uploaded", "status", "error")}
        row["by"] = names(org).get(record["seat"], "?")
        row["kind"] = "video" if record.get("video") else "recording"
        if manager_view:
            row["position"] = record.get("position")
        return row

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/v1/me")
    def me(who: tuple[dict, dict] = Depends(caller)) -> dict:
        org, m = who
        out = {"name": m["name"], "role": m["role"], "position": m["position"], "team": org["name"],
               "seat": m["seat"]}
        if m["role"] == "manager":
            out.update(seats=org["seats"], used=len(store.employees(org)), currency=org["currency"],
                       positions=[p["name"] for p in org["positions"]], default_position=org["default_position"])
        return out

    # ---- recordings -------------------------------------------------------------------------------

    @app.post("/api/v1/recordings")
    def upload(file: UploadFile = File(...), options: str = Form("{}"),
               who: tuple[dict, dict] = Depends(caller)) -> dict:
        org, m = who
        try:
            opts = json.loads(options)
            if not isinstance(opts, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Bad report options.") from None
        position = opts.pop("position", None)
        if m["role"] != "manager" or position not in [p["name"] for p in org["positions"]]:
            position = m["position"]  # employees always report as their own position
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        try:
            size = 0
            with os.fdopen(fd, "wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "This recording is over 1 GB.")
                    out.write(chunk)
            try:
                record = store.add_recording(org, m, Path(tmp), opts)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        finally:
            Path(tmp).unlink(missing_ok=True)
        record["position"] = position
        jobs.submit(record)
        return listing(org, record, m["role"] == "manager")

    @app.post("/api/v1/videos")
    def upload_video(file: UploadFile = File(...), options: str = Form("{}"),
                     who: tuple[dict, dict] = Depends(caller)) -> dict:
        """A screen video (Teams, Loom, Snipping Tool...) instead of a FlowSME recording: the AI model reads the
        steps from its frames, so the report's timings are approximate."""
        org, m = who
        try:
            opts = json.loads(options)
            if not isinstance(opts, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Bad report options.") from None
        position = opts.pop("position", None)
        if m["role"] != "manager" or position not in [p["name"] for p in org["positions"]]:
            position = m["position"]
        fd, tmp = tempfile.mkstemp(suffix=Path(file.filename or "video.mp4").suffix)
        try:
            size = 0
            with os.fdopen(fd, "wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "This video is over 1 GB.")
                    out.write(chunk)
            try:
                record = store.add_video(org, m, Path(tmp), file.filename or "video.mp4", opts)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        finally:
            Path(tmp).unlink(missing_ok=True)
        record["position"] = position
        jobs.submit(record)
        return listing(org, record, m["role"] == "manager")

    @app.get("/api/v1/recordings")
    def recordings(who: tuple[dict, dict] = Depends(caller)) -> list[dict]:
        org, m = who
        boss = m["role"] == "manager"
        return [listing(org, r, boss) for r in store.records(org["id"]) if boss or r["seat"] == m["seat"]]

    @app.get("/api/v1/recordings/{rec_id}")
    def recording(rec_id: str, who: tuple[dict, dict] = Depends(caller)) -> dict:
        record = visible(who, rec_id)
        boss = who[1]["role"] == "manager"
        return {**listing(who[0], record, boss), **summary(store, record, money=boss)}

    @app.get("/api/v1/recordings/{rec_id}/report", response_class=HTMLResponse)
    def report(rec_id: str, who: tuple[dict, dict] = Depends(caller)) -> str:
        record = visible(who, rec_id)
        if record["status"] != "ready":
            raise HTTPException(409, "The report isn't ready yet.")
        return report_html(store, record, money=who[1]["role"] == "manager")

    @app.get("/api/v1/recordings/{rec_id}/sop", response_class=HTMLResponse)
    def sop(rec_id: str, task: int | None = None, who: tuple[dict, dict] = Depends(caller)) -> str:
        record = visible(who, rec_id)
        try:
            return sop_html(store, record, task)
        except (KeyError, OSError):
            raise HTTPException(404, "This recording has no SOP.") from None

    @app.post("/api/v1/recordings/{rec_id}/build")
    def build(rec_id: str, body: dict, who: tuple[dict, dict] = Depends(caller)) -> Response:
        record = visible(who, rec_id)
        try:
            data = build_zip(store, record, int(body.get("task")))
        except (StopIteration, TypeError, ValueError, SystemExit):
            raise HTTPException(400, "That task has no automation design.") from None
        return Response(data, media_type="application/zip")

    @app.post("/api/v1/recordings/{rec_id}/reanalyse")
    def reanalyse(rec_id: str, body: dict, who: tuple[dict, dict] = Depends(caller)) -> dict:
        org, m = who
        record = visible(who, rec_id)
        if record["status"] in ("queued", "analysing"):
            raise HTTPException(409, "It is already being analysed.")
        opts = {k: body[k] for k in ("runs_per_month", "tools", "other_tools") if k in body}
        record["options"].update(opts)
        if m["role"] == "manager" and body.get("position") in [p["name"] for p in org["positions"]]:
            record["position"] = body["position"]
        jobs.submit(record)
        return listing(org, record, m["role"] == "manager")

    @app.delete("/api/v1/recordings/{rec_id}")
    def delete(rec_id: str, who: tuple[dict, dict] = Depends(caller)) -> dict:
        record = visible(who, rec_id)
        if record["status"] in ("queued", "analysing"):
            raise HTTPException(409, "Wait until the analysis has finished.")
        store.delete_recording(record)
        return {"deleted": rec_id}

    # ---- the manager's Admin panel ----------------------------------------------------------------

    def admin_view(org: dict) -> dict:
        return {"team": org["name"], "seats": org["seats"], "used": len(store.employees(org)),
                "currency": org["currency"], "default_position": org["default_position"],
                "positions": org["positions"],
                "members": [{k: m[k] for k in ("seat", "name", "email", "position", "role", "status", "issued")}
                            for m in org["members"]]}

    @app.get("/api/v1/admin")
    def admin(who: tuple[dict, dict] = Depends(manager)) -> dict:
        return admin_view(who[0])

    @app.post("/api/v1/admin/members")
    def add_member(body: dict, who: tuple[dict, dict] = Depends(manager)) -> dict:
        org = who[0]
        try:
            member, key = store.add_member(org, str(body.get("name", "")), str(body.get("position", "")),
                                           str(body.get("email", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"seat": member["seat"], "key": key, "admin": admin_view(store.org(org["id"]))}

    @app.post("/api/v1/admin/members/{seat}")
    def change_member(seat: str, body: dict, who: tuple[dict, dict] = Depends(manager)) -> dict:
        org, me_ = who
        try:
            member = store.member(org, seat)
        except KeyError:
            raise HTTPException(404, "No such seat.") from None
        action, key = body.get("action"), None
        try:
            if member["seat"] == me_["seat"] and action in ("revoke", "new_key"):
                raise ValueError("You can't change your own seat here. Ask FlowSME.")
            if action == "revoke":
                store.set_status(org, seat, False)
            elif action == "restore":
                store.set_status(org, seat, True)
            elif action == "position":
                if body.get("position") not in [p["name"] for p in org["positions"]]:
                    raise ValueError("Choose one of the positions.")
                member["position"] = body["position"]
                store.save_org(org)
            elif action == "new_key":
                key = store.new_key(org, seat)
            else:
                raise ValueError("Unknown change.")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"key": key, "admin": admin_view(store.org(org["id"]))}

    @app.put("/api/v1/admin/positions")
    def positions(body: dict, who: tuple[dict, dict] = Depends(manager)) -> dict:
        org = who[0]
        try:
            store.set_positions(org, list(body.get("positions") or []), str(body.get("currency", "$")),
                                str(body.get("default_position", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return admin_view(store.org(org["id"]))

    # ---- the manager's comparisons ----------------------------------------------------------------

    @app.post("/api/v1/compare", response_class=HTMLResponse)
    def compare(body: dict, who: tuple[dict, dict] = Depends(manager)) -> str:
        from ..measure import compare as run_compare

        before, after = visible(who, str(body.get("before"))), visible(who, str(body.get("after")))
        if before["status"] != "ready" or after["status"] != "ready":
            raise HTTPException(409, "Both reports must be ready.")
        try:
            paths = run_compare(store.session_dir(before), store.session_dir(after), log=lambda *_: None,
                                before_items=body.get("before_items"), after_items=body.get("after_items"))
        except SystemExit as exc:
            raise HTTPException(400, str(exc)) from None
        return paths["html"].read_text(encoding="utf-8")

    @app.post("/api/v1/team", response_class=HTMLResponse)
    def team(body: dict, who: tuple[dict, dict] = Depends(manager)) -> str:
        from ..team import team_view

        records = [visible(who, str(r)) for r in body.get("recordings") or []]
        if len(records) < 2 or any(r["status"] != "ready" for r in records):
            raise HTTPException(400, "Choose two or more recordings whose reports are ready.")
        people = names(who[0])
        out = Path(tempfile.mkdtemp(prefix="flowsme-team-"))
        try:
            team_view([store.session_dir(r) for r in records], [people.get(r["seat"], "?") for r in records], out,
                      log=lambda *_: None, items=body.get("items"))
            return (out / "team.html").read_text(encoding="utf-8")
        except SystemExit as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            shutil.rmtree(out, ignore_errors=True)

    return app
