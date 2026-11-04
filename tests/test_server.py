"""The FlowSME server through its HTTP API: seats and roles, uploads, the analysis job, what each role may see."""
from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowsme.demo import build_demo
from flowsme.server.api import create_app

OFFLINE = '[llm]\nprovider = "offline"\n\n[analysis]\nweb_search = false\n'


@pytest.fixture()
def server(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "server.toml").write_text(OFFLINE, encoding="utf-8")
    app = create_app(data, workers=1)
    store = app.state.store
    org = store.create_org("Test Co", seats=2)
    boss, boss_key = store.add_member(org, "Boss", org["default_position"], role="manager")
    worker, worker_key = store.add_member(store.org(org["id"]), "Ann", org["default_position"])
    other = store.create_org("Other Co", seats=1)
    _, outsider_key = store.add_member(other, "Eve", other["default_position"], role="manager")
    with TestClient(app) as client:
        yield {"client": client, "store": store, "org": org, "worker": worker, "tmp": tmp_path,
               "boss": {"Authorization": f"Bearer {boss_key}"}, "ann": {"Authorization": f"Bearer {worker_key}"},
               "eve": {"Authorization": f"Bearer {outsider_key}"}}


def recording_zip(tmp: Path) -> bytes:
    """The demo recording, zipped the way the FlowSME Recorder sends it."""
    session = build_demo(tmp / "local")
    shutil.rmtree(session / "analysis", ignore_errors=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path in session.rglob("*"):
            if path.is_file():
                archive.write(path, f"{session.name}/{path.relative_to(session).as_posix()}")
    return buf.getvalue()


def upload(s, who: str, data: bytes, options: dict | None = None):
    return s["client"].post("/api/v1/recordings", headers=s[who],
                            files={"file": ("recording.zip", data, "application/zip")},
                            data={"options": json.dumps(options or {})})


def wait_ready(s, who: str, rec_id: str) -> dict:
    for _ in range(240):
        got = s["client"].get(f"/api/v1/recordings/{rec_id}", headers=s[who]).json()
        if got["status"] in ("ready", "failed"):
            return got
        time.sleep(0.25)
    raise AssertionError("the analysis did not finish in 60 seconds")


def test_health_needs_no_key(server):
    assert server["client"].get("/api/v1/health").json()["ok"] is True


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer fsk_wrong"}, {"Authorization": "Bearer "}])
def test_bad_keys_are_refused(server, headers):
    assert server["client"].get("/api/v1/me", headers=headers).status_code == 401


def test_api_docs_are_off(server):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert server["client"].get(path).status_code == 404


def test_roles(server):
    c = server["client"]
    me = c.get("/api/v1/me", headers=server["ann"]).json()
    assert me["role"] == "employee" and "positions" not in me and "currency" not in me
    assert c.get("/api/v1/admin", headers=server["ann"]).status_code == 403
    assert c.put("/api/v1/admin/positions", headers=server["ann"], json={"positions": []}).status_code == 403
    admin = c.get("/api/v1/admin", headers=server["boss"]).json()
    assert {p["name"] for p in admin["positions"]} and all("hourly_cost" in p for p in admin["positions"])


def test_seat_limit_and_revoke(server):
    c = server["client"]
    position = server["org"]["default_position"]
    assert c.post("/api/v1/admin/members", headers=server["boss"], json={"name": "Bob"}).status_code == 400
    bob = c.post("/api/v1/admin/members", headers=server["boss"], json={"name": "Bob", "position": position})
    assert bob.status_code == 200 and bob.json()["key"].startswith("fsk_")  # the manager takes no seat
    full = c.post("/api/v1/admin/members", headers=server["boss"], json={"name": "Cy", "position": position})
    assert full.status_code == 400 and "seat" in full.json()["detail"]
    seat = server["worker"]["seat"]
    assert c.post(f"/api/v1/admin/members/{seat}", headers=server["boss"], json={"action": "revoke"}).status_code == 200
    assert c.get("/api/v1/me", headers=server["ann"]).status_code == 403
    assert c.post(f"/api/v1/admin/members/{seat}", headers=server["boss"],
                  json={"action": "restore"}).status_code == 200
    assert c.get("/api/v1/me", headers=server["ann"]).status_code == 200


def test_rejects_bad_uploads(server):
    assert upload(server, "ann", b"not a zip").status_code == 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("rec/events.jsonl", "{}\n")
        archive.writestr("rec/../../escape.txt", "x")
    assert upload(server, "ann", buf.getvalue()).status_code == 400
    assert not (server["tmp"] / "escape.txt").exists()
    bad_options = server["client"].post("/api/v1/recordings", headers=server["ann"],
                                        files={"file": ("r.zip", b"PK", "application/zip")},
                                        data={"options": "[1]"})
    assert bad_options.status_code == 400


def test_recording_end_to_end(server):
    c = server["client"]
    sent = upload(server, "ann", recording_zip(server["tmp"]), {"runs_per_month": 20})
    assert sent.status_code == 200, sent.text
    rec_id = sent.json()["id"]
    done = wait_ready(server, "ann", rec_id)
    assert done["status"] == "ready", done["error"]
    assert done["tasks"] and all(t["money_saved_per_month"] is None for t in done["tasks"])
    assert done["currency"] is None

    employee_page = c.get(f"/api/v1/recordings/{rec_id}/report", headers=server["ann"])
    assert employee_page.status_code == 200 and "data:image/" in employee_page.text
    boss = c.get(f"/api/v1/recordings/{rec_id}", headers=server["boss"]).json()
    assert any(t["money_saved_per_month"] for t in boss["tasks"]) and boss["currency"]

    # other organisations can't see it, by id or in their list
    assert c.get(f"/api/v1/recordings/{rec_id}", headers=server["eve"]).status_code == 404
    assert c.get(f"/api/v1/recordings/{rec_id}/report", headers=server["eve"]).status_code == 404
    assert c.get("/api/v1/recordings", headers=server["eve"]).json() == []

    assert c.get(f"/api/v1/recordings/{rec_id}/sop", headers=server["ann"]).status_code == 200
    buildable = [t for t in done["tasks"] if t["can_build"]]
    if buildable:
        made = c.post(f"/api/v1/recordings/{rec_id}/build", headers=server["ann"], json={"task": buildable[0]["id"]})
        assert made.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(made.content)).namelist()
        assert "deploy/n8n.json" in names

    assert c.delete(f"/api/v1/recordings/{rec_id}", headers=server["ann"]).status_code == 200
    assert c.get(f"/api/v1/recordings/{rec_id}", headers=server["ann"]).status_code == 404


def test_position_rename_keeps_people(server):
    c = server["client"]
    admin = c.get("/api/v1/admin", headers=server["boss"]).json()
    first = admin["positions"][0]
    renamed = [dict(p) for p in admin["positions"]]
    renamed[0] = {"name": first["name"] + " II", "hourly_cost": first["hourly_cost"], "was": first["name"]}
    got = c.put("/api/v1/admin/positions", headers=server["boss"],
                json={"positions": renamed, "currency": "$", "default_position": renamed[0]["name"]})
    assert got.status_code == 200, got.text
    ann = next(m for m in got.json()["members"] if m["name"] == "Ann")
    assert ann["position"] == first["name"] + " II"


def test_upload_refused_before_reading_without_a_key(server):
    c = server["client"]
    assert c.post("/api/v1/recordings", files={"file": ("r.zip", b"x" * 1000)}).status_code == 401
    big = c.post("/api/v1/recordings", headers={**server["ann"], "Content-Length": str(2 * 1024 ** 3)},
                 content=b"")
    assert big.status_code == 413


def test_bad_keys_are_slowed_down(server):
    c = server["client"]
    for _ in range(20):
        c.get("/api/v1/me", headers={"Authorization": "Bearer fsk_guess"})
    assert c.get("/api/v1/me", headers={"Authorization": "Bearer fsk_guess"}).status_code == 429
    assert c.get("/api/v1/me", headers=server["ann"]).status_code == 200  # people behind the same proxy
    assert c.get("/api/v1/health").status_code == 200


def test_rename_keeps_recordings_rate_and_bad_rates_refused(server):
    c = server["client"]
    sent = upload(server, "ann", recording_zip(server["tmp"]))
    rec_id = sent.json()["id"]
    assert wait_ready(server, "ann", rec_id)["status"] == "ready"
    admin = c.get("/api/v1/admin", headers=server["boss"]).json()
    first = admin["positions"][0]
    renamed = [dict(p) for p in admin["positions"]]
    renamed[0] = {"name": first["name"] + " II", "hourly_cost": first["hourly_cost"], "was": first["name"]}
    assert c.put("/api/v1/admin/positions", headers=server["boss"], json={"positions": renamed}).status_code == 200
    assert c.post(f"/api/v1/recordings/{rec_id}/reanalyse", headers=server["boss"], json={}).status_code == 200
    again = wait_ready(server, "boss", rec_id)
    assert any(t["money_saved_per_month"] for t in again["tasks"]), "the renamed position lost its rate"
    nan = [dict(p, hourly_cost="nan") for p in renamed]
    assert c.put("/api/v1/admin/positions", headers=server["boss"], json={"positions": nan}).status_code == 400
    assert c.put("/api/v1/admin/positions", headers=server["boss"], json={"positions": "abc"}).status_code == 400


def test_parallel_reanalysis_runs_once(server):
    from concurrent.futures import ThreadPoolExecutor

    c = server["client"]
    rec_id = upload(server, "ann", recording_zip(server["tmp"])).json()["id"]
    assert wait_ready(server, "ann", rec_id)["status"] == "ready"
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(lambda _: c.post(f"/api/v1/recordings/{rec_id}/reanalyse", headers=server["ann"],
                                                json={}).status_code, range(6)))
    assert codes.count(200) == 1 and set(codes) <= {200, 409, 429}


def test_damaged_files_do_not_stop_the_server(server, tmp_path):
    from flowsme.server.api import create_app

    store = server["store"]
    other = store.create_org("Broken Co", seats=1)
    (store.root / "orgs" / other["id"] / "org.json").write_text('{"id": "O-', encoding="utf-8")
    create_app(store.root, workers=1)  # starts anyway
    assert server["client"].get("/api/v1/me", headers=server["ann"]).status_code == 200
    assert server["client"].get("/api/v1/me", headers={"Authorization": "Bearer fsk_x"}).status_code == 401


def test_broken_zip_leaves_nothing(server):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("rec/events.jsonl", "{}\n")
        archive.writestr("rec/meta.json", "[1, 2]")
    got = upload(server, "ann", buf.getvalue())
    assert got.status_code == 400
    folder = server["store"].recordings_dir(server["org"]["id"])
    assert not folder.exists() or not any(folder.iterdir())


def test_parallel_admin_changes_all_count(server):
    """Two admin requests at once must not undo each other (they read, change and save the same file)."""
    from concurrent.futures import ThreadPoolExecutor

    c, store, org = server["client"], server["store"], server["org"]
    store.set_seats(store.org(org["id"]), 12)
    position = org["default_position"]

    def add(i):
        return c.post("/api/v1/admin/members", headers=server["boss"], json={"name": f"P{i}", "position": position})

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(add, range(10)))
    assert all(r.status_code == 200 for r in results)
    names = {m["name"] for m in store.org(org["id"])["members"]}
    assert {f"P{i}" for i in range(10)} <= names


def test_daily_report_allowance(server):
    store, org = server["store"], server["org"]
    fresh = store.org(org["id"])
    fresh["daily_reports"] = 1
    store.save_org(fresh)
    data = recording_zip(server["tmp"])
    assert upload(server, "ann", data).status_code == 200
    second = upload(server, "boss", data)
    assert second.status_code == 429 and "today" in second.json()["detail"]


def test_ai_budget(server):
    """With a budget, a report is refused once what was spent plus the most it may cost would go over."""
    from flowsme.server.store import REPORT_RESERVE_USD

    store, org = server["store"], server["org"]
    store.spend(org["id"], 1.25)
    store.spend(org["id"], 0.25)
    assert store.org(org["id"])["spent_usd"] == 1.5
    store.set_budget(store.org(org["id"]), 1.5 + REPORT_RESERVE_USD - 0.01)
    data = recording_zip(server["tmp"])
    refused = upload(server, "ann", data)
    assert refused.status_code == 429 and "budget" in refused.json()["detail"]
    store.set_budget(store.org(org["id"]), 1.5 + REPORT_RESERVE_USD)
    first = upload(server, "ann", data)
    assert first.status_code == 200
    assert wait_ready(server, "ann", first.json()["id"])["status"] == "ready"
    assert store.org(org["id"])["spent_usd"] == 1.5  # the built-in rules cost nothing
    store.set_budget(store.org(org["id"]), None)
    assert "budget_usd" not in store.org(org["id"])


def test_budget_counts_reports_being_made(server):
    from flowsme.server.store import Store

    org = {"budget_usd": 12.0, "spent_usd": 1.0}
    Store.check_budget(org, in_flight=1)  # 1 + 2 x 5 = 11
    with pytest.raises(ValueError, match="being made"):
        Store.check_budget(org, in_flight=2)
    with pytest.raises(ValueError, match="used up"):
        Store.check_budget({"budget_usd": 5.0, "spent_usd": 1.0}, in_flight=0)
    Store.check_budget({"spent_usd": 99.0}, in_flight=5)  # no budget: no limit


def test_report_inlines_only_screenshots(server):
    """A recorded field name that points at another file must not pull that file into the employee's page."""
    from flowsme.server.jobs import report_html

    sent = upload(server, "ann", recording_zip(server["tmp"]))
    rec_id = sent.json()["id"]
    assert wait_ready(server, "ann", rec_id)["status"] == "ready"
    store = server["store"]
    record = store.record(server["org"]["id"], rec_id)
    page = store.session_dir(record) / "analysis" / "report-hours.html"
    page.write_text(page.read_text(encoding="utf-8") + '<img src="../analysis/options.json">'
                    '<img src="../analysis/report.html">', encoding="utf-8")
    html = report_html(store, record, money=False)
    assert "hourly_cost" not in html and "Worth a month" not in html
