"""The offline analysis: a report, SOPs and a roadmap, each process described once, money only where it belongs."""
from __future__ import annotations

import json
import re


def test_report_files(analysed_demo):
    path, result = analysed_demo
    analysis = path / "analysis"
    for name in ("report.md", "report.html", "report-hours.html", "analysis.json"):
        assert (analysis / name).exists(), name
    assert result["paths"]["html"].exists()
    html = (analysis / "report.html").read_text(encoding="utf-8")
    assert "<title>" in html and "FlowSME" in html


def test_tasks_found(analysed_demo):
    _, result = analysed_demo
    tasks = [t for t in result["tasks"] if not t.misc]
    assert tasks, "the demo recording should give at least one task"
    for t in tasks:
        assert 0 <= t.score <= 100
        assert t.estimate["minutes_saved_per_run"] >= 0


def test_same_process_points_to_a_real_task(analysed_demo):
    _, result = analysed_demo
    ids = {t.id for t in result["tasks"]}
    for t in result["tasks"]:
        if t.same_as:
            assert t.same_as in ids and t.same_as != t.id


def test_hours_copy_has_no_money(analysed_demo):
    path, _ = analysed_demo
    text = (path / "analysis" / "report-hours.md").read_text(encoding="utf-8")
    assert not re.search(r"\$\s?\d", text), "the employees' copy must not show amounts"


def test_grammar(analysed_demo):
    path, _ = analysed_demo
    text = (path / "analysis" / "report.md").read_text(encoding="utf-8")
    assert not re.search(r"(?<!of )(?<![\d.,])\b1 (tasks|steps|runs|minutes|hours)\b", text)


def test_sops_written_once_per_process(analysed_demo):
    path, result = analysed_demo
    sops = list((path / "analysis").glob("sop-*.html"))
    merged = [t for t in result["tasks"] if t.same_as]
    assert sops, "the demo should get at least one SOP"
    for t in merged:
        assert not (path / "analysis" / f"sop-task{t.id}.html").exists()


def test_tasks_json_is_valid(analysed_demo):
    path, _ = analysed_demo
    data = json.loads((path / "analysis" / "analysis.json").read_text(encoding="utf-8"))
    assert isinstance(data, (list, dict))

