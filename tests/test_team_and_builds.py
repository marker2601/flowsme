"""Team seat files (desktop team mode), the workflow files for every tool, and the split that keeps the analysis
out of the customers' app."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from flowsme.org import DEFAULT_POSITIONS, Org

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def team(tmp_path):
    o = Org(tmp_path)
    o.create_team("Acme", "4821", seats=2)
    return o


def test_pin(team):
    assert team.check_pin("4821") and not team.check_pin("0000")
    with pytest.raises(ValueError):
        team.create_team("Acme", "12", seats=1)


def test_seat_file_checks(team, tmp_path):
    ann = team.add_member("Ann", DEFAULT_POSITIONS[0]["name"])
    seat = team.seat_file(ann)
    assert "hourly_cost" not in json.dumps(seat)  # employees never get rates
    assert team.check_seat(seat)[0] == "ok"
    assert team.check_seat(dict(seat, position=DEFAULT_POSITIONS[-1]["name"]))[0] == "forged"
    assert team.check_seat(None)[0] == "none"
    other = Org(tmp_path / "other")
    other.create_team("Other", "1111", seats=1)
    assert other.check_seat(seat)[0] == "other team"
    team.revoke(ann["seat"])
    assert team.check_seat(seat)[0] == "revoked"


def test_seat_limit(team):
    position = DEFAULT_POSITIONS[0]["name"]
    team.add_member("A", position)
    b = team.add_member("B", position)
    with pytest.raises(ValueError):
        team.add_member("C", position)
    team.revoke(b["seat"])
    team.add_member("C", position)
    with pytest.raises(ValueError):
        team.restore(b["seat"])


def test_every_workflow_tool_gets_a_file(analysed_demo, tmp_path):
    from flowsme.build import _spec, n8n_workflow
    from flowsme.deploy import TARGETS, export_all

    path, result = analysed_demo
    report = json.loads((path / "analysis" / "analysis.json").read_text(encoding="utf-8"))
    task = next(t for t in report["tasks"] if (t.get("integration") or {}).get("data_flows") and not t.get("same_as"))
    spec = _spec(task)
    files = export_all(spec, n8n_workflow(task, spec), tmp_path)
    assert len(files) == len(TARGETS) - 1  # n8n_workflow.json is written by build.py
    for target, file in files.items():
        doc = json.loads(file.read_text(encoding="utf-8"))
        assert doc, target


def test_build_task_writes_build_notes(analysed_demo):
    from flowsme.build import build_task

    path, result = analysed_demo
    task = next(t for t in result["tasks"] if t.integration and t.integration.get("data_flows") and not t.same_as)
    files = build_task(path, task.id, log=lambda *_: None)
    names = {p.name for p in files.values()}
    assert "BUILD.md" in names


def test_customers_app_imports_no_server_code():
    """The FlowSME Recorder must run without the analysis, prompts, knowledge base or builders."""
    code = ("import sys, flowsme.client.app, flowsme.client.admin, flowsme.client.cloud, flowsme.client.account; "
            "print(' '.join(sorted(m for m in sys.modules if m.startswith('flowsme'))))")
    loaded = set(subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
                 .stdout.split())
    spec = importlib.util.spec_from_file_location("build_installer", ROOT / "packaging" / "build_installer.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    CLIENT_MODULES = installer.CLIENT_MODULES

    assert loaded <= CLIENT_MODULES, f"server code in the customers' app: {sorted(loaded - CLIENT_MODULES)}"


def test_recorded_text_cannot_break_generated_code(analysed_demo, tmp_path):
    """A website controls its title and labels: they must stay text inside the generated scripts."""
    import ast

    from flowsme.build import _spec, api_wrapper, playwright_script

    path, _ = analysed_demo
    report = json.loads((path / "analysis" / "analysis.json").read_text(encoding="utf-8"))
    task = next(t for t in report["tasks"] if (t.get("integration") or {}).get("data_flows") and not t.get("same_as"))
    evil = 'Portal"""\nimport os; os.system("calc")\n"""'
    for system in task["integration"]["systems"]:
        if system["kind"] == "web":
            old = system["system"]
            system["system"] = old + " " + evil
            for row in task["integration"]["data_flows"]:
                for end in ("to", "from"):
                    if (row.get(end) or {}).get("system") == old:
                        row[end]["system"] = system["system"]
            for a in task["integration"]["ui_actions"]:
                if a["system"] == old:
                    a["system"] = system["system"]
    spec = _spec(task)
    if not spec["web"]:
        pytest.skip("the demo task has no web form")
    for code in (playwright_script(spec), api_wrapper(spec)):
        tree = ast.parse(code)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "system"]
        assert not calls, "recorded text became code"
