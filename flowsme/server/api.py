"""The FlowSME server's API. Customers' apps sign in with a seat key and get results; everything that makes the
results (the analysis, prompts, knowledge base, automation builders and the AI key) runs only here.

Employees see their own recordings and hours saved. Managers see the whole team, money at each position's
rate, and the Admin panel's seats and rates. A revoked seat stops working on its next request."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .. import __version__
from .jobs import Jobs, build_zip, report_html, sop_html, summary
from .store import Store

MAX_UPLOAD = 1024 ** 3  # 1 GB per recording
UPLOADS = ("/api/v1/recordings", "/api/v1/videos")
BAD_KEYS_PER_MINUTE = 20  # from one address; more get "try again later" without being checked
WAITING_PER_SEAT = 3      # reports queued or being made at once for one person


class BadKeys:
    """Remembers recent failed sign-ins per address, so guessing or flooding with keys is slowed down."""

    def __init__(self):
        self.seen: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self.lock:
            q = self.seen[ip]
            while q and q[0] < time.monotonic() - 60:
                q.popleft()
            return len(q) >= BAD_KEYS_PER_MINUTE

    def add(self, ip: str) -> None:
        with self.lock:
            self.seen[ip].append(time.monotonic())
            if len(self.seen) > 10_000:  # forget the oldest addresses rather than grow forever
                self.seen.pop(next(iter(self.seen)))


def create_app(data: Path, workers: int = 2) -> FastAPI:
    data = Path(data)
    store = Store(data)
    jobs = Jobs(store, data, workers)
    app = FastAPI(title="FlowSME", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.jobs = store, jobs
    bad_keys = BadKeys()

    def address(request: Request) -> str:
        return request.client.host if request.client else "?"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Before any body is read: too many bad keys from this address, or an upload that is unsigned, of
        unknown size or too big, is refused here, so nobody can fill the server's disk without a seat."""
        if request.url.path != "/api/v1/health" and bad_keys.blocked(address(request)):
            # a valid key still gets in: everyone behind one proxy shares its address
            key = request.headers.get("authorization", "").removeprefix("Bearer").strip()
            if not (key and store.who(key)):
                return JSONResponse({"detail": "Too many wrong seat keys. Try again in a minute."}, 429)
        if request.method == "POST" and request.url.path in UPLOADS:
            length = request.headers.get("content-length", "")
            if not length.isdigit():
                return JSONResponse({"detail": "Send the file with its size."}, 411)
            if int(length) > MAX_UPLOAD + 1024 ** 2:
                return JSONResponse({"detail": "This file is over 1 GB."}, 413)
            key = request.headers.get("authorization", "").removeprefix("Bearer").strip()
            found = store.who(key) if key else None
            if not found:
                bad_keys.add(address(request))
                return JSONResponse({"detail": "Sign in with your FlowSME seat key."}, 401)
            if found[1]["status"] != "active":
                return JSONResponse({"detail": "Your seat was revoked. Ask your manager for a new one."}, 403)
        return await call_next(request)

    def caller(request: Request, authorization: str = Header("")) -> tuple[dict, dict]:
        key = authorization.removeprefix("Bearer").strip()
        found = store.who(key) if key else None
        if not found:
            bad_keys.add(address(request))
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

    admission = threading.Lock()  # parallel requests can't all slip past the limits

    def allowance(org: dict, m: dict) -> None:
        """Each report costs AI money: a few waiting per person, and a daily allowance per organisation."""
        with admission:
            waiting = sum(1 for r in store.records(org["id"])
                          if r.get("seat") == m["seat"] and r.get("status") in ("queued", "analysing"))
            if waiting >= WAITING_PER_SEAT:
                raise HTTPException(429, f"You have {waiting} reports being made. Wait until one is ready.")
            try:
                store.use_report(org)
            except ValueError as exc:
                raise HTTPException(429, str(exc)) from None

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
        allowance(org, m)
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
                store.use_report(org, refund=True)  # a rejected file doesn't count
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
        allowance(org, m)
        suffix = Path(file.filename or "video.mp4").suffix.lower()
        fd, tmp = tempfile.mkstemp(suffix=suffix if suffix.isascii() and len(suffix) <= 6 else ".bin")
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
                store.use_report(org, refund=True)
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
        allowance(org, m)
        opts = {k: body[k] for k in ("runs_per_month", "tools", "other_tools") if k in body}
        record["options"].update(opts)
        if m["role"] == "manager" and body.get("position") in [p["name"] for p in org["positions"]]:
            record["position"] = body["position"]
        try:
            jobs.claim(record)
        except ValueError as exc:
            store.use_report(org, refund=True)
            raise HTTPException(409, str(exc)) from None
        return listing(org, record, m["role"] == "manager")

    @app.delete("/api/v1/recordings/{rec_id}")
    def delete(rec_id: str, who: tuple[dict, dict] = Depends(caller)) -> dict:
        record = visible(who, rec_id)
        if record["status"] in ("queued", "analysing"):
            raise HTTPException(409, "Wait until the analysis has finished.")
        try:
            store.delete_recording(record)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
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
                                           str(body.get("email", "")))  # employees only: managers come from FlowSME
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
                store.set_member_position(org, seat, str(body.get("position")))
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
        positions = body.get("positions")
        if not isinstance(positions, list) or not all(isinstance(p, dict) for p in positions):
            raise HTTPException(400, "Send the positions as a list of names and hourly costs.")
        try:
            store.set_positions(org, positions, str(body.get("currency", "$")), str(body.get("default_position", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return admin_view(store.org(org["id"]))

    # ---- the manager's comparisons ----------------------------------------------------------------

    @app.post("/api/v1/compare", response_class=HTMLResponse)
    def compare(body: dict, who: tuple[dict, dict] = Depends(manager)) -> str:
        from ..measure import compare as run_compare

        before, after = visible(who, str(body.get("before"))), visible(who, str(body.get("after")))
        for key in ("before_items", "after_items"):
            if body.get(key) is not None and not (isinstance(body[key], int) and 0 < body[key] < 10_000):
                raise HTTPException(400, "Items must be whole numbers.")
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

        items = body.get("items")
        if items is not None and not (isinstance(items, list) and all(isinstance(n, int) and 0 < n < 10_000
                                                                        for n in items)):
            raise HTTPException(400, "Items must be whole numbers, one per recording.")
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
