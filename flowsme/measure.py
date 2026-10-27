"""Measure: how long each run of a routine takes and how much runs differ (Six Sigma's Measure phase), and the
before/after comparison that shows what an improvement really saved, beyond run-to-run noise."""
from __future__ import annotations

import html
import json
from difflib import SequenceMatcher
from math import exp, lgamma, log, sqrt
from pathlib import Path
from statistics import mean, median, pstdev, stdev

from .models import PASTE_KEYS, Task, fmt_minutes

MR_FACTOR = 2.66  # individuals chart: limits at mean ± 2.66 × average moving range (3 sigma)


def stats(values: list[float]) -> dict:
    n = len(values)
    sd = stdev(values) if n > 1 else 0.0
    avg = mean(values)
    return {"n": n, "mean": round(avg, 1), "median": round(median(values), 1), "sd": round(sd, 1),
            "min": round(min(values), 1), "max": round(max(values), 1), "cv": round(sd / avg, 3) if avg else 0.0}


def variation(task: Task) -> dict | None:
    """Seconds per run, their spread, control limits, and the steps that vary most. None for a single run."""
    runs = task.iterations
    if len(runs) < 2:
        return None
    seconds = [sum(s.duration for s in run) for run in runs]
    st = stats(seconds)
    moving = [abs(b - a) for a, b in zip(seconds, seconds[1:])]
    mr_bar = mean(moving)
    ucl, lcl = st["mean"] + MR_FACTOR * mr_bar, max(0.0, st["mean"] - MR_FACTOR * mr_bar)
    # Line each run up with the typical run, step by step, to see which steps vary.
    rep = task.representative
    shape = [s.signature for s in rep]
    per_step: dict[int, list[float]] = {}
    extra = []
    for run in runs:
        matcher = SequenceMatcher(None, shape, [s.signature for s in run], autojunk=False)
        matched = 0
        for a, b, size in matcher.get_matching_blocks():
            for k in range(size):
                per_step.setdefault(a + k, []).append(run[b + k].duration)
            matched += size
        extra.append(len(run) - matched)
    spread = sorted(((pstdev(v), i) for i, v in per_step.items() if len(v) > 1), reverse=True)[:3]
    return {
        **st, "runs": len(runs), "seconds": [round(x, 1) for x in seconds],
        "ucl": round(ucl, 1), "lcl": round(lcl, 1), "mr_bar": round(mr_bar, 1),
        "out_of_control": [i + 1 for i, x in enumerate(seconds) if x > ucl or x < lcl],
        "slowest_run": seconds.index(max(seconds)) + 1, "fastest_run": seconds.index(min(seconds)) + 1,
        "extra_steps": extra,
        "variable_steps": [{"step": i + 1, "description": rep[i].description, "sd_seconds": round(sd, 1),
                            "mean_seconds": round(mean(per_step[i]), 1)} for sd, i in spread if sd >= 0.5],
    }


def control_chart_svg(seconds: list[float], centre: float, ucl: float, lcl: float, title: str = "") -> str:
    """Individuals control chart as inline SVG: one point per run, the mean, and the control limits."""
    w, h, left, right, top, bottom = 640, 220, 56, 16, 18, 34
    hi = max(max(seconds), ucl) * 1.08 or 1
    lo = min(min(seconds), lcl) * 0.9
    x = lambda i: left + (w - left - right) * (i / max(len(seconds) - 1, 1))
    y = lambda v: top + (h - top - bottom) * (1 - (v - lo) / ((hi - lo) or 1))
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" '
             f'aria-label="{html.escape(title or "Seconds per run")}" xmlns="http://www.w3.org/2000/svg" '
             'font-family="system-ui,Segoe UI,sans-serif" font-size="11">']
    for value, name, dash, colour in ((ucl, "UCL", "5 4", "#c5221f"), (centre, "mean", "", "#5f6368"),
                                      (lcl, "LCL", "5 4", "#c5221f")):
        if name == "LCL" and value <= 0:
            continue
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="{colour}" '
                     f'stroke-width="1.2"{f" stroke-dasharray={chr(34)}{dash}{chr(34)}" if dash else ""}/>')
        parts.append(f'<text x="{left - 6}" y="{y(value) + 4:.1f}" text-anchor="end" fill="{colour}">'
                     f'{name} {value:.0f}s</text>')
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(seconds))
    parts.append(f'<polyline points="{points}" fill="none" stroke="#2f5bd3" stroke-width="2"/>')
    for i, v in enumerate(seconds):
        outside = v > ucl or v < lcl
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="{5 if outside else 4}" '
                     f'fill="{"#c5221f" if outside else "#2f5bd3"}"><title>Run {i + 1}: {v:.0f} s</title></circle>')
        parts.append(f'<text x="{x(i):.1f}" y="{h - 12}" text-anchor="middle" fill="#5f6368">{i + 1}</text>')
    parts.append(f'<text x="{(left + w - right) / 2:.0f}" y="{h - 1}" text-anchor="middle" fill="#5f6368">run</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---- before / after --------------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((a - 1 + m2) * (a + m2)), -(a + m) * (a + b + m) * x / ((a + m2) * (a + 1 + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 3e-14:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def welch(before: list[float], after: list[float]) -> dict | None:
    """Two-sample t-test without assuming equal spread: is the difference bigger than run-to-run noise?"""
    if len(before) < 2 or len(after) < 2:
        return None
    vb, va = stdev(before) ** 2, stdev(after) ** 2
    se2 = vb / len(before) + va / len(after)
    if se2 == 0:
        return None
    t = (mean(before) - mean(after)) / sqrt(se2)
    df = se2 ** 2 / ((vb / len(before)) ** 2 / (len(before) - 1) + (va / len(after)) ** 2 / (len(after) - 1))
    return {"t": round(t, 2), "df": round(df, 1), "p": round(_betai(df / 2, 0.5, df / (df + t * t)), 4)}


def summarize(session_dir: Path, task_id: int | None = None, items: int | None = None) -> dict:
    """One analysed recording's routine, measured from analysis.json: time, steps and manual entries per item.
    `items`: how many items the recording covered, when the person knows better than the runs FlowSME found
    (a short routine can look like one run). Per-item times are then averages only, with no spread."""
    data = json.loads((session_dir / "analysis" / "analysis.json").read_text(encoding="utf-8"))
    tasks = [t for t in data["tasks"] if not t.get("misc")]
    if not tasks:
        raise SystemExit(f"{session_dir.name}: no task found in the report.")
    if task_id is not None:
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            raise SystemExit(f"{session_dir.name}: no task {task_id}. Tasks: {', '.join(str(t['id']) for t in tasks)}")
    else:  # the routine: most runs first, then the best opportunity
        task = max(tasks, key=lambda t: (t["runs"], t["score"]))
    steps = {s["index"]: s for s in data["steps"]}
    runs = task.get("runs_step_indexes") or [task["steps_one_run"]]
    seconds = [sum(steps[i]["duration"] for i in run) for run in runs]
    entries = [sum(1 for i in run if steps[i]["chars_typed"] or set(steps[i]["shortcuts"]) & PASTE_KEYS)
               for run in runs]
    switches = [sum(1 for a, b in zip(run, run[1:]) if steps[a]["app"] != steps[b]["app"]) for run in runs]
    counts = [len(r) for r in runs]
    separable = not items or items == len(runs)
    if not separable:  # the person's own count: totals divided by it, no per-item spread
        n = items
        time = {"n": n, "mean": round(sum(seconds) / n, 1), "median": None, "sd": None, "min": None, "max": None,
                "cv": None}
        seconds, entries, switches, counts = [], [sum(entries) / n], [sum(switches) / n], [sum(counts) / n]
    else:
        time = stats(seconds)
    options = {}
    with_options = session_dir / "analysis" / "options.json"
    if with_options.exists():
        options = json.loads(with_options.read_text(encoding="utf-8"))
    return {"session": session_dir.name, "task_id": task["id"], "task": task["name"],
            "runs": time["n"], "detected_runs": len(runs), "separable": separable,
            "seconds": [round(x, 1) for x in seconds], "time": time,
            "steps_per_run": round(mean(counts), 1), "entries_per_run": round(mean(entries), 1),
            "switches_per_run": round(mean(switches), 1),
            "estimated_minutes_saved": (task.get("estimate") or {}).get("minutes_saved_per_run"),
            "runs_per_month": options.get("runs_per_month", 20), "hourly_cost": options.get("hourly_cost", 30),
            "currency": options.get("currency", "$")}


def compare(before_dir: Path, after_dir: Path, before_task: int | None = None, after_task: int | None = None,
            log=print, before_items: int | None = None, after_items: int | None = None) -> dict[str, Path]:
    """Before/after report for one routine: what the change really saved, and whether it is more than noise."""
    for d in (before_dir, after_dir):
        if not (d / "analysis" / "analysis.json").exists():
            raise SystemExit(f"Make the report for {d.name} first.")
    b, a = summarize(before_dir, before_task, before_items), summarize(after_dir, after_task, after_items)
    saved = b["time"]["mean"] - a["time"]["mean"]
    test = welch(b["seconds"], a["seconds"])
    per_month = b["runs_per_month"]
    result = {"before": b, "after": a, "seconds_saved_per_run": round(saved, 1),
              "percent_saved": round(saved / b["time"]["mean"], 3) if b["time"]["mean"] else 0.0,
              "hours_saved_per_month": round(saved * per_month / 3600, 1),
              "money_saved_per_month": round(saved * per_month / 3600 * b["hourly_cost"]), "test": test}
    if test is None and not (b["separable"] and a["separable"]):
        result["verdict"] = ("Averages only: the items of one recording couldn't be told apart, so the saving can't "
                             "be tested against run-to-run variation. It is still the average over the items you "
                             "entered.")
    elif test is None:
        result["verdict"] = ("Not enough runs to tell saving from noise: record at least 2 items before and after "
                             "(3 or more is better).")
    elif test["p"] < 0.05:
        result["verdict"] = (f"The change is real: {'faster' if saved > 0 else 'slower'} by more than the "
                             f"run-to-run variation (p = {test['p']:.3f}, two-sample t-test).")
    else:
        result["verdict"] = (f"Not proven yet: the difference is within the run-to-run variation (p = "
                             f"{test['p']:.2f}). Record more items to be sure.")

    out = after_dir / "analysis" / f"compare-{before_dir.name}"
    out.mkdir(parents=True, exist_ok=True)
    md = _comparison_markdown(result)
    from .report import HTML  # the report's page shell renders the Markdown

    title = f"Before and after: {a['task']}"
    paths = {"markdown": out / "comparison.md", "html": out / "comparison.html", "json": out / "comparison.json"}
    paths["markdown"].write_text(md, encoding="utf-8")
    paths["html"].write_text(HTML.replace("__TITLE__", html.escape(title)).replace(
        "__MARKDOWN__", md.replace("</script", "<\\/script")), encoding="utf-8")
    paths["json"].write_text(json.dumps(result, indent=2), encoding="utf-8")
    log(f"Before {fmt_minutes(b['time']['mean'] / 60)} per item, after {fmt_minutes(a['time']['mean'] / 60)}. "
        f"{result['verdict']}")
    return paths


def _items(side: dict) -> str:
    if side["separable"]:
        return str(side["runs"])
    return f"{side['runs']} (as entered; FlowSME found {side['detected_runs']})"


def _comparison_markdown(r: dict) -> str:
    b, a = r["before"], r["after"]
    lines = [f"# Before and after: {a['task']}\n",
             f"Before: `{b['session']}` (task {b['task_id']}, {b['task']}) · After: `{a['session']}` "
             f"(task {a['task_id']}, {a['task']})\n",
             f"**{r['verdict']}**\n"]
    est = b["estimated_minutes_saved"]
    lines.append(f"Saved per item: **{fmt_minutes(r['seconds_saved_per_run'] / 60)}** "
                 f"({r['percent_saved']:.0%}). At {b['runs_per_month']} items a month: "
                 f"**{r['hours_saved_per_month']} hours**, about {b['currency']}{r['money_saved_per_month']:,}."
                 + (f" The report estimated {fmt_minutes(est)} per item." if est else "") + "\n")
    sec = lambda v: "-" if v is None else f"{v:.0f} s"
    rows = [("Items recorded", _items(b), _items(a)),
            ("Time per item (mean)", fmt_minutes(b["time"]["mean"] / 60), fmt_minutes(a["time"]["mean"] / 60)),
            ("Run-to-run spread (standard deviation)", sec(b["time"]["sd"]), sec(a["time"]["sd"])),
            ("Fastest / slowest item", f"{sec(b['time']['min'])} / {sec(b['time']['max'])}",
             f"{sec(a['time']['min'])} / {sec(a['time']['max'])}"),
            ("Steps per item", b["steps_per_run"], a["steps_per_run"]),
            ("Values typed or pasted by hand per item", b["entries_per_run"], a["entries_per_run"]),
            ("Switches between applications per item", b["switches_per_run"], a["switches_per_run"])]
    lines += ["| | Before | After |", "|---|---|---|"] + [f"| {n} | {x} | {y} |" for n, x, y in rows] + [""]
    seconds = b["seconds"] + a["seconds"]
    if len(seconds) > 1 and b["separable"] and a["separable"]:
        lines.append("Seconds per item, before then after:\n")
        centre = mean(b["seconds"])
        mr = mean(abs(q - p) for p, q in zip(b["seconds"], b["seconds"][1:])) if len(b["seconds"]) > 1 else 0.0
        lines.append(control_chart_svg(seconds, centre, centre + MR_FACTOR * mr, max(0.0, centre - MR_FACTOR * mr),
                                       "Seconds per item before and after") + "\n")
        lines.append(f"<sub>Items 1 to {b['runs']} are before, {b['runs'] + 1} to {len(seconds)} after. The lines "
                     "are the before mean and its control limits: points below the lower limit are faster than "
                     "the old process could be by chance.</sub>\n")
    if r["test"]:
        t = r["test"]
        lines.append(f"<sub>Welch two-sample t-test: t = {t['t']}, {t['df']} degrees of freedom, p = {t['p']}. "
                     "Under 0.05 means the difference is very unlikely to be chance.</sub>\n")
    return "\n".join(lines)
