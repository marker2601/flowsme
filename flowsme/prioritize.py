"""What to automate first: impact (hours and money saved a month) against effort (days to build), as a 2x2 matrix
and a Now / Next / Later roadmap with payback time. Effort starts from the recommendation's own estimate and grows
with what the recording shows makes building harder: systems without an API, values whose source is unknown."""
from __future__ import annotations

import html
from math import log10
from statistics import median

from .integration import DOCUMENTED, FOUND
from .models import Task

EFFORT_DAYS = {"hours": 0.5, "days": 3.0, "weeks": 15.0}
LOW_EFFORT_DAYS = 5.0
MAX_PAYBACK_MONTHS = 24  # slower than this is not worth building yet
QUADRANTS = {
    ("high", "low"): ("Quick win", "Now", "#1e8e3e"),
    ("high", "high"): ("Major project", "Next", "#2f5bd3"),
    ("low", "low"): ("Fill-in", "Later", "#b06d00"),
    ("low", "high"): ("Not worth it yet", "Hold", "#9aa0a6"),
}


def effort(task: Task) -> dict:
    recs = (task.recommendation or {}).get("recommendations") or []
    base = EFFORT_DAYS.get(str(recs[0].get("effort")) if recs else "", 3.0)
    reasons = [f"recommended approach: about {base:g} day{'s' if base != 1 else ''}"]
    days = base
    plan = task.integration or {}
    for system in plan.get("systems", []):
        if system["role"] == "used, no data moved":
            continue
        if not any(o["method"] in ("API", "n8n", "SQL") and o["status"] in (DOCUMENTED, FOUND)
                   for o in system["options"]):
            days += 2.0
            reasons.append(f"{system['system']} has no known API: screen automation, +2 days")
    unknown = len(plan.get("open_questions", []))
    if unknown:
        days += 0.5 * unknown
        reasons.append(f"{unknown} open question(s) to settle first, +{0.5 * unknown:g} days")
    return {"days": round(days, 1), "reasons": reasons}


def prioritize(tasks: list[Task], currency: str = "$", show_money: bool = True) -> dict | None:
    items = []
    for t in tasks:
        if t.misc or t.same_as or not t.estimate or not t.estimate.get("hours_saved_per_month"):
            continue
        e = effort(t)
        # The other pieces of the same process are built with it, so their time counts here.
        same = [t] + [o for o in tasks if o.same_as == t.id and o.estimate]
        hours = round(sum(o.estimate["hours_saved_per_month"] for o in same), 1)
        money = sum(o.estimate["cost_saved_per_month"] for o in same)
        # Building costs days x 8 hours of the same people's time, so payback needs no hourly rate.
        items.append({"id": t.id, "name": t.name, "hours_per_month": hours, "money_per_month": money,
                      "effort_days": e["days"], "effort_reasons": e["reasons"],
                      "payback_months": round(e["days"] * 8 / hours, 1)})
    if not items:
        return None
    # High impact: at least the median task and at least an hour a month.
    cut = max(1.0, median(i["hours_per_month"] for i in items))
    for i in items:
        impact = "high" if i["hours_per_month"] >= cut else "low"
        size = "low" if i["effort_days"] <= LOW_EFFORT_DAYS else "high"
        if i["payback_months"] is not None and i["payback_months"] > MAX_PAYBACK_MONTHS:
            impact, size = "low", "high"  # cheap to build, but it would take years to earn the time back
        i["quadrant"], i["phase"], i["colour"] = QUADRANTS[(impact, size)]
    order = {"Now": 0, "Next": 1, "Later": 2, "Hold": 3}
    items.sort(key=lambda i: (order[i["phase"]], -i["hours_per_month"]))
    return {"items": items, "impact_cut_hours": round(cut, 1), "effort_cut_days": LOW_EFFORT_DAYS,
            "svg": matrix_svg(items, cut, currency if show_money else None)}


def matrix_svg(items: list[dict], cut: float, currency: str | None = "$") -> str:
    """Impact (hours saved a month, up) against effort (days to build, right, log scale), one bubble per task."""
    w, h, left, right, top, bottom = 640, 360, 58, 20, 20, 44
    x_lo, x_hi = 0.4, max(40.0, max(i["effort_days"] for i in items) * 1.5)
    y_hi = max(max(i["hours_per_month"] for i in items) * 1.2, cut * 2, 1.0)
    x = lambda d: left + (w - left - right) * (log10(max(d, x_lo)) - log10(x_lo)) / (log10(x_hi) - log10(x_lo))
    y = lambda v: top + (h - top - bottom) * (1 - v / y_hi)
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" '
         'aria-label="Impact against effort, one bubble per task" xmlns="http://www.w3.org/2000/svg" '
         'font-family="system-ui,Segoe UI,sans-serif" font-size="11">']
    xc, yc = x(LOW_EFFORT_DAYS), y(cut)
    for (x0, x1, y0, y1, label, colour) in (
            (left, xc, top, yc, "Quick wins", "#1e8e3e"), (xc, w - right, top, yc, "Major projects", "#2f5bd3"),
            (left, xc, yc, h - bottom, "Fill-ins", "#b06d00"), (xc, w - right, yc, h - bottom, "Not worth it yet",
                                                                 "#9aa0a6")):
        s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" fill="{colour}" '
                 'fill-opacity="0.07"/>')
        s.append(f'<text x="{x0 + 8:.1f}" y="{y0 + 16:.1f}" fill="{colour}" font-weight="600">{label}</text>')
    s.append(f'<line x1="{xc:.1f}" x2="{xc:.1f}" y1="{top}" y2="{h - bottom}" stroke="#9aa0a6" stroke-dasharray="4 4"/>')
    s.append(f'<line x1="{left}" x2="{w - right}" y1="{yc:.1f}" y2="{yc:.1f}" stroke="#9aa0a6" stroke-dasharray="4 4"/>')
    for d in (0.5, 1, 2, 5, 10, 20):
        if d <= x_hi:
            s.append(f'<text x="{x(d):.1f}" y="{h - bottom + 16}" text-anchor="middle" fill="#5f6368">{d:g}</text>')
    s.append(f'<text x="{(left + w - right) / 2:.0f}" y="{h - 8}" text-anchor="middle" fill="#5f6368">'
             'effort: days to build (log scale)</text>')
    for v in (0, y_hi / 2, y_hi):
        s.append(f'<text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#5f6368">{v:.0f}</text>')
    s.append(f'<text transform="translate(14 {(top + h - bottom) / 2:.0f}) rotate(-90)" text-anchor="middle" '
             'fill="#5f6368">impact: hours saved a month</text>')
    for i in items:
        cx, cy = x(i["effort_days"]), y(i["hours_per_month"])
        r = 7 + min(14, i["hours_per_month"] / 5)
        money = f" ({currency}{i['money_per_month']:,})" if currency else ""
        tip = html.escape(f"{i['id']}. {i['name']}: {i['hours_per_month']} h a month{money},"
                          f" about {i['effort_days']:g} days to build")
        s.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{i["colour"]}" fill-opacity="0.85">'
                 f'<title>{tip}</title></circle>')
        s.append(f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" fill="#fff" font-weight="700">'
                 f'{i["id"]}</text>')
    s.append("</svg>")
    return "".join(s)
