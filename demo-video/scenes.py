"""Write one capture.js scene per chapter, timed from timeline.json, and record them all in parallel.

    python scenes.py [scene ...]      (no names = every scene)

Pages come from media/pages (the run's report.html, sop.html and the generated panel.html and build.html)."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
PAGES = HERE / "media" / "pages"
FRAMES = HERE / "media" / "frames"
T = {s["id"]: s for s in json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))}


def length(*ids: str, extra: float = 0.5) -> float:
    return round(T[ids[-1]]["end"] - T[ids[0]]["start"] + extra, 2)


def url(name: str) -> str:
    return (PAGES / name).as_uri()


# Ids for headings, tabs and the narration paragraph, so the actions below can point at them.
TAG = """
document.querySelectorAll("main h2, main h3, main h4").forEach(h => {
  if (!h.id) h.id = "k-" + h.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40); });
document.querySelectorAll(".tab").forEach(t => t.id = "tab-" + t.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-"));
const said = [...document.querySelectorAll("main strong")].find(s => s.textContent.startsWith("What you said"));
if (said) said.closest("p").id = "said";
"""
step = lambda at, n: {"at": at, "do": "eval", "js": f'document.body.dataset.step = "{n}"'}

SCENES = {
    # Chapter 1: the finished report, the title card goes on top in compose.py.
    "report_open": {"url": url("report.html"), "seconds": length("s1"), "setup": TAG, "actions": [
        {"at": 5.4, "do": "move", "to": ".kpi:nth-child(2)", "dur": 1.3},
        {"at": 7.4, "do": "move", "to": ".kpi:nth-child(3)", "dur": 1.0},
        {"at": 8.8, "do": "move", "to": ".kpi:nth-child(6)", "dur": 1.2}]},
    # Chapter 4: what the server did, revealed piece by piece.
    "panel": {"url": url("panel.html"), "seconds": length("s4"), "actions": [
        step(0.3, 1), step(3.6, 2), step(6.6, 3), step(9.4, 4), step(11.6, 5), step(15.2, 6)]},
    # Chapter 5: summary, what to automate first, the roadmap row.
    "report_plan": {"url": url("report.html"), "seconds": length("s5"), "setup": TAG, "actions": [
        {"at": 0, "do": "scroll", "to": "#k-summary", "dur": 0, "offset": 60},
        {"at": 0.8, "do": "move", "to": "section.card:has(#k-summary) li:nth-child(1)", "dur": 1.2},
        {"at": 3.0, "do": "move", "to": "section.card:has(#k-summary) li:nth-child(4)", "dur": 1.0},
        {"at": 6.2, "do": "scroll", "to": "#k-what-to-automate-first", "dur": 1.6, "offset": 60},
        {"at": 8.2, "do": "move", "to": "section.card:has(#k-what-to-automate-first) svg circle", "dur": 1.0},
        {"at": 10.2, "do": "scroll", "to": "section.card:has(#k-what-to-automate-first) .table-wrap", "dur": 1.4, "offset": 260},
        {"at": 12.0, "do": "move", "to": "section.card:has(#k-what-to-automate-first) tbody tr:first-child td:nth-child(4)", "dur": 1.0}]},
    # Chapter 6: what was said, then the recommendation that uses it.
    "report_task": {"url": url("report.html"), "seconds": length("s6"), "setup": TAG, "actions": [
        {"at": 0, "do": "scroll", "to": "#said", "dur": 0, "offset": 300},
        {"at": 0.6, "do": "move", "to": "#said + ul li", "dur": 1.2},
        {"at": 4.6, "do": "move", "to": "#tab-recommendations", "dur": 1.0},
        {"at": 5.8, "do": "click", "to": "#tab-recommendations"},
        {"at": 5.9, "do": "scroll", "to": "details.task .pane:not([hidden])", "dur": 0.8, "offset": 150},
        {"at": 7.2, "do": "move", "to": "details.task .pane:not([hidden]) li strong", "dur": 1.0}]},
    # Chapter 7, first part: the SOP from its steps to the approval block.
    "sop": {"url": url("sop.html"), "seconds": round(T["s7"]["end"] - T["s7"]["start"] - 4.0, 2), "setup": TAG, "actions": [
        {"at": 0.6, "do": "scroll", "to": "#k-procedure", "dur": 1.4, "offset": 60},
        {"at": 2.4, "do": "move", "to": "article.step img", "dur": 1.0},
        {"at": 4.4, "do": "scroll", "to": "#k-approval", "dur": 1.8, "offset": 60},
        {"at": 6.4, "do": "move", "to": "section.card:has(#k-approval) table", "dur": 0.8}]},
    # Chapter 7, second part: the user stories tab of the report.
    "report_stories": {"url": url("report.html"), "seconds": 4.5, "setup": TAG, "cursor": [700, 300], "actions": [
        {"at": 0, "do": "click", "to": "#tab-user-stories"},
        {"at": 0, "do": "scroll", "to": "details.task .pane:not([hidden])", "dur": 0, "offset": 150},
        {"at": 0, "do": "move", "to": "#tab-user-stories", "dur": 0},
        {"at": 1.4, "do": "move", "to": "details.task .pane:not([hidden]) p:nth-of-type(3) strong", "dur": 1.2}]},
    # Chapter 8: the build output.
    "build": {"url": url("build.html"), "seconds": length("s8"), "actions": [step(0.3, 1), step(2.6, 2), step(6.4, 3)]},
}


def main(names: list[str]) -> None:
    procs = []
    for name in names or SCENES:
        scene = {**SCENES[name], "zoom": 1.5, "out": str(FRAMES / name)}
        path = HERE / "media" / f"scene-{name}.json"
        path.write_text(json.dumps(scene, indent=1), encoding="utf-8")
        procs.append((name, subprocess.Popen(["node", str(HERE / "capture.js"), str(path)])))
    for name, p in procs:
        print(name, "ok" if p.wait() == 0 else "FAILED", flush=True)


main(sys.argv[1:])
