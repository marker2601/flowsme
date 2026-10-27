"""Team view: the same process recorded by several people. Who is fastest and most consistent, how much
hands-on work each does per item, and exactly which steps the others add or skip compared with the fastest
method. The fastest consistent method is the candidate standard: its SOP is the one to train everyone on."""
from __future__ import annotations

import html
import json
import os
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .measure import summarize
from .models import PASTE_KEYS, fmt_minutes

ROUTINE_SHARE = 0.7  # the routine must cover this much of the work for its per-item spread to count


def _routine_steps(session_dir: Path, task_id: int) -> list[dict]:
    data = json.loads((session_dir / "analysis" / "analysis.json").read_text(encoding="utf-8"))
    steps = {s["index"]: s for s in data["steps"]}
    task = next(t for t in data["tasks"] if t["id"] == task_id)
    return [steps[i] for i in task["steps_one_run"]]


def _shape(step: dict) -> str:
    # Where + what, without the per-item data: comparable across people (the signature, when recorded).
    return step.get("signature") or f"{step['app']}|{step['action']}|{step['target'] if step['action'] != 'type' else ''}"


def _person(session_dir: Path, items: int | None) -> dict:
    """All the work in one recording, per item: a detected routine can be just a fragment of the process."""
    s = summarize(session_dir)
    data = json.loads((session_dir / "analysis" / "analysis.json").read_text(encoding="utf-8"))
    steps = {x["index"]: x for x in data["steps"]}
    work = sorted({i for t in data["tasks"] if not t.get("misc") for i in t["steps_one_run"] + sum(
        t.get("runs_step_indexes") or [], [])})
    seconds = sum(steps[i]["duration"] for i in work)
    n = items or s["detected_runs"]
    entries = sum(1 for i in work if steps[i]["chars_typed"] or set(steps[i]["shortcuts"]) & PASTE_KEYS)
    switches = sum(1 for a, b in zip(work, work[1:]) if steps[a]["app"] != steps[b]["app"])
    routine_share = sum(s["seconds"]) / seconds if seconds and s["seconds"] else 0.0
    spread = s["time"]["sd"] if (s["detected_runs"] == n and routine_share >= ROUTINE_SHARE) else None
    return {**s, "runs": n, "time": {"mean": round(seconds / n, 1), "sd": spread}, "routine_share": round(routine_share, 2),
            "steps_per_run": round(len(work) / n, 1), "entries_per_run": round(entries / n, 1),
            "switches_per_run": round(switches / n, 1)}


def team_view(session_dirs: list[Path], names: list[str] | None = None, out_dir: Path | None = None,
              log=print, items: list[int] | None = None) -> dict[str, Path]:
    if len(session_dirs) < 2:
        raise SystemExit("A team view needs at least two recordings of the same process.")
    people = []
    for n, d in enumerate(session_dirs):
        if not (d / "analysis" / "analysis.json").exists():
            raise SystemExit(f"Make the report for {d.name} first.")
        s = _person(d, items[n] if items and n < len(items) else None)
        sop = next(iter(sorted((d / "analysis").glob(f"sop-task{s['task_id']}.html"))), None)
        people.append({**s, "name": (names[n] if names and n < len(names) and names[n] else d.name),
                       "steps": _routine_steps(d, s["task_id"]), "sop": str(sop) if sop else ""})
    # The standard: fastest per item, and steady (spread under a third of the mean) when there is a choice.
    steady = [p for p in people if p["time"]["sd"] is not None and p["time"]["sd"] <= p["time"]["mean"] / 3]
    best = min(steady or people, key=lambda p: p["time"]["mean"])
    for p in people:
        p["vs_best_seconds"] = round(p["time"]["mean"] - best["time"]["mean"], 1)
        if p is best:
            p["adds"], p["skips"] = [], []
            continue
        matcher = SequenceMatcher(None, [_shape(s) for s in best["steps"]], [_shape(s) for s in p["steps"]],
                                  autojunk=False)
        adds, skips = [], []
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op in ("insert", "replace"):
                adds += [s["description"] for s in p["steps"][j1:j2]]
            if op in ("delete", "replace"):
                skips += [s["description"] for s in best["steps"][i1:i2]]
        p["adds"], p["skips"] = adds[:6], skips[:6]
    result = {"best": best["name"], "people": [{k: v for k, v in p.items() if k != "steps"} for p in people]}
    entries = [p["entries_per_run"] for p in people if p["entries_per_run"]]
    if entries and max(entries) > 2 * min(entries):
        result["scope_warning"] = (f"Check that these recordings cover the same work: values entered by hand per "
                                   f"item range from {min(entries):g} to {max(entries):g}. Correct the item counts "
                                   "if FlowSME miscounted, or leave out recordings of a different process.")
    month = best["runs_per_month"]
    gap = sum(max(p["vs_best_seconds"], 0) for p in people) * month / 3600
    result["hours_per_month_if_all_matched_best"] = round(gap, 1)

    out = out_dir or (session_dirs[0].parent / "team-views" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    md = _markdown(result, out)
    from .report import HTML  # the report's page shell renders the Markdown

    paths = {"markdown": out / "team.md", "html": out / "team.html", "json": out / "team.json"}
    paths["markdown"].write_text(md, encoding="utf-8")
    paths["html"].write_text(HTML.replace("__TITLE__", "Team view").replace(
        "__MARKDOWN__", md.replace("</script", "<\\/script")), encoding="utf-8")
    paths["json"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Team view of {len(people)} recordings: fastest method {best['name']} at "
        f"{fmt_minutes(best['time']['mean'] / 60)} per item.")
    return paths


def _markdown(r: dict, out: Path) -> str:
    people = r["people"]
    best = next(p for p in people if p["name"] == r["best"])
    lines = [f"# Team view: {best['task']}\n",
             f"{len(people)} recordings of the same process. The fastest steady method is **{best['name']}**, at "
             f"{fmt_minutes(best['time']['mean'] / 60)} per item. If everyone worked at that pace, the team would "
             f"save about **{r['hours_per_month_if_all_matched_best']} hours a month** (at {best['runs_per_month']} "
             "items a month each) before any automation.\n"]
    if r.get("scope_warning"):
        lines.append(f"> **{r['scope_warning']}**\n")
    sd = lambda p: "-" if p["time"]["sd"] is None else f"{p['time']['sd']:.0f} s"
    lines += ["| Person | Items | Time per item | Spread | Steps per item | Values typed or pasted by hand | "
              "App switches | vs fastest |", "|---|---|---|---|---|---|---|---|"]
    for p in sorted(people, key=lambda p: p["time"]["mean"]):
        mark = " (fastest)" if p is best else ""
        gap = "-" if p is best else f"+{p['vs_best_seconds']:.0f} s"
        lines.append(f"| {html.escape(p['name'])}{mark} | {p['runs']} | {fmt_minutes(p['time']['mean'] / 60)} | "
                     f"{sd(p)} | {p['steps_per_run']} | {p['entries_per_run']} | {p['switches_per_run']} | {gap} |")
    lines.append("")
    lines.append(_bars_svg(people, best) + "\n")
    lines.append("## What the others do differently\n")
    for p in people:
        if p is best:
            continue
        lines.append(f"**{html.escape(p['name'])}** ({fmt_minutes(p['time']['mean'] / 60)} per item):\n")
        if p["adds"]:
            lines.append("- Extra steps the fastest method doesn't need: " + "; ".join(p["adds"]))
        if p["skips"]:
            lines.append("- Steps of the fastest method they do differently or skip: " + "; ".join(p["skips"]))
        if not p["adds"] and not p["skips"]:
            lines.append("- Same steps as the fastest method: the difference is pace (reading, waiting, typing).")
        lines.append("")
    lines.append("## Standard work\n")
    if best["sop"]:
        rel = os.path.relpath(best["sop"], out).replace("\\", "/")
        lines.append(f"Use **{html.escape(best['name'])}**'s method as the standard: train with its SOP "
                     f"([{Path(best['sop']).name}]({rel})). Then record each person again and compare, to check the "
                     "gap has closed.\n")
    else:
        lines.append(f"Use **{html.escape(best['name'])}**'s method as the standard, and record each person again "
                     "after training to check the gap has closed.\n")
    lines.append("<sub>Time per item: all the work in each recording divided by the items done. Spread is shown "
                 "only where the repeated routine covers most of the work. Fastest steady method: the lowest time per "
                 "item among people whose items vary by less than a third of their own mean. Steps are matched by "
                 "where and what was done, not by the values.</sub>\n")
    return "\n".join(lines)


def _bars_svg(people: list[dict], best: dict) -> str:
    """Time per item per person: a bar for the mean and a whisker for ± one standard deviation."""
    ordered = sorted(people, key=lambda p: p["time"]["mean"])
    w, row, left, right = 640, 30, 170, 60
    h = row * len(ordered) + 30
    hi = max(p["time"]["mean"] + (p["time"]["sd"] or 0) for p in ordered) * 1.1 or 1
    x = lambda v: left + (w - left - right) * v / hi
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" '
         'aria-label="Seconds per item, per person" xmlns="http://www.w3.org/2000/svg" '
         'font-family="system-ui,Segoe UI,sans-serif" font-size="11">']
    for n, p in enumerate(ordered):
        y = 10 + n * row
        colour = "#1e8e3e" if p is best else "#2f5bd3"
        s.append(f'<text x="{left - 8}" y="{y + 15}" text-anchor="end" fill="#5f6368">'
                 f'{html.escape(p["name"][:26])}</text>')
        s.append(f'<rect x="{left}" y="{y + 4}" width="{x(p["time"]["mean"]) - left:.1f}" height="16" rx="3" '
                 f'fill="{colour}" fill-opacity="0.8"><title>{p["time"]["mean"]:.0f} s per item</title></rect>')
        if p["time"]["sd"]:
            lo, hi_ = x(max(p["time"]["mean"] - p["time"]["sd"], 0)), x(p["time"]["mean"] + p["time"]["sd"])
            s.append(f'<line x1="{lo:.1f}" x2="{hi_:.1f}" y1="{y + 12}" y2="{y + 12}" stroke="#1d1d1f" '
                     'stroke-width="1.5"/>')
        s.append(f'<text x="{x(p["time"]["mean"]) + 6:.1f}" y="{y + 16}" fill="#5f6368">'
                 f'{p["time"]["mean"]:.0f} s</text>')
    s.append(f'<text x="{(left + w - right) / 2:.0f}" y="{h - 4}" text-anchor="middle" fill="#5f6368">'
             'seconds per item (line: ± one standard deviation)</text>')
    s.append("</svg>")
    return "".join(s)
