"""Lean Six Sigma analysis of a task, from recorded evidence only.

  value classification  each step is value-adding (VA), business-required (BVA) or non-value-adding (NVA)
  wastes                Lean wastes (transport, motion, over-processing, defects, waiting, skills) with evidence
  bottleneck            Theory of Constraints: the stage that takes longest per item
  ECRS                  Eliminate, Combine, Rearrange, Simplify, in that order, before automating
  SIPOC                 suppliers, inputs, process, outputs, customers
  DMAIC                 the whole analysis framed as Define, Measure, Analyze, Improve, Control
  error exposure        manual entries per item, expected errors at sourced human error rates, DPMO, sigma

Definitions and sources live in knowledge.py."""
from __future__ import annotations

import re

from .integration import label, source_of
from .knowledge import ERROR_RATES, METHODS, sigma_level
from .models import Task

BVA_WORDS = re.compile(r"submit|send|save|approve|confirm|post|sign|file|move|processed", re.I)


def classify_steps(task: Task, plan: dict) -> list[dict]:
    """VA / BVA / NVA for each step of one run, with the waste it represents and why."""
    typed_known = {(r["to"]["system"], r["to"]["field"]) for r in plan["data_flows"]
                   if r["how"] == "typed" and source_of(r)[1] != "unknown"}
    typed_unknown = {(r["to"]["system"], r["to"]["field"]) for r in plan["data_flows"]
                     if r["how"] == "typed" and source_of(r)[1] == "unknown"}
    out = []
    for n, step in enumerate(task.representative, 1):
        field = re.sub(r"^([A-Z]{1,3})\d+$", r"column \1", step.target or "")
        key = (step.app, field)
        if step.pastes or step.copies:
            cls, waste, why = "NVA", "transportation", "moves data between applications by hand"
        elif step.chars_typed and key in typed_known:
            cls, waste, why = "NVA", "over_processing", "re-types a value that already exists elsewhere"
        elif step.chars_typed and key in typed_unknown:
            cls, waste, why = "VA?", "", "enters information whose source the recording doesn't show: may be judgement"
        elif step.action == "click" and BVA_WORDS.search(step.target or ""):
            cls, waste, why = "BVA", "", "required by the business (submit, send, file), adds no value to the item itself"
        else:
            cls, waste, why = "NVA", "motion", "navigating: opening, finding, switching, scrolling"
        out.append({"step": n, "class": cls, "waste": waste, "why": why, "seconds": step.duration,
                    "description": step.description})
    return out


def analyse(task: Task, plan: dict, tools: dict, checks: dict, items_per_month: float) -> dict:
    steps = classify_steps(task, plan)
    scale = (task.minutes / task.runs * 60) / (sum(s["seconds"] for s in steps) or 1)  # to per-item seconds
    time_by = {}
    for s in steps:
        time_by[s["class"]] = time_by.get(s["class"], 0) + s["seconds"] * scale / 60
    total = sum(time_by.values()) or 1
    va = time_by.get("VA?", 0) + time_by.get("VA", 0)

    flows = plan["data_flows"]
    features = task.features
    rework = checks["rework"]
    wastes = []

    def waste(wid, evidence, steps_hit):
        if evidence:
            minutes = sum(s["seconds"] for s in steps if s["step"] in steps_hit) * scale / 60
            wastes.append({"id": wid, "name": METHODS[wid]["name"], "evidence": evidence,
                           "steps": steps_hit, "minutes_per_item": round(minutes, 2)})

    moved = [r for r in flows if r["how"] == "copy-paste"]
    waste("transportation", f"{len(moved)} values per item carried between applications by copy-paste"
          if moved else "", [s["step"] for s in steps if s["waste"] == "transportation"])
    switches = features.get("app_switches", 0) / max(task.runs, 1)
    waste("motion", f"{switches:.0f} window switches and "
          f"{sum(1 for s in steps if s['waste'] == 'motion')} navigation steps per item" if switches else "",
          [s["step"] for s in steps if s["waste"] == "motion"])
    twice = [r for r in flows if r["how"] == "typed" and source_of(r)[1] != "unknown"]
    hops = [r for r in flows if r["how"] == "copy-paste" and r.get("origin")]
    over = []
    proven = [r for r in twice if r.get("confirmed_source")]
    if proven:
        over.append(f"{len(proven)} value(s) typed by hand that the fingerprints prove already exist elsewhere ("
                    + ", ".join(f"{r['to']['field']} = {label(r['confirmed_source'])}" for r in proven) + ")")
    if len(twice) > len(proven):
        over.append(f"{len(twice) - len(proven)} typed value(s) that probably exist elsewhere (same field name: "
                    + ", ".join(r["to"]["field"] for r in twice if not r.get("confirmed_source")) + ")")
    if hops:
        over.append(f"{len(hops)} value(s) copied into one file only to be copied out again ("
                    + ", ".join(f"{r['from']['field']} in {r['from']['system']}" for r in hops) + ")")
    waste("over_processing", "; ".join(over), [s["step"] for s in steps if s["waste"] == "over_processing"])
    waste("defects", " ".join(r["text"] for r in rework), [])
    slow = [s for s in steps if s["seconds"] * scale >= 45]
    waste("waiting", f"{len(slow)} step(s) of 45 seconds or more (waiting for pages or reading)" if slow else "",
          [s["step"] for s in slow])
    nva_share = time_by.get("NVA", 0) / total
    if nva_share >= 0.5:
        wastes.append({"id": "skills", "name": METHODS["skills"]["name"], "steps": [], "minutes_per_item": 0,
                       "evidence": f"{nva_share:.0%} of this person's time on this task is moving and re-typing data"})

    stages = tools.get("stages", [])
    bottleneck = max(stages, key=lambda s: s["minutes"]) if stages else None

    ecrs = []
    if hops:
        ecrs.append({"step": "Eliminate", "idea": "Stop copying values into an in-between file that is then copied "
                     f"out again ({', '.join(sorted({r['from']['system'] for r in hops}))}). Keep it only if someone "
                     "needs it as a record, and then let the automation write it."})
    if twice:
        ecrs.append({"step": "Eliminate", "idea": "Stop re-typing values that already exist: "
                     + ", ".join(f"{r['to']['field']} in {r['to']['system']}" for r in twice) + "."})
    targets = sorted({r["to"]["system"] for r in flows})
    if len(targets) > 1:
        ecrs.append({"step": "Combine", "idea": f"Capture each value once and write it to all {len(targets)} "
                     f"destinations in one step ({', '.join(targets)})."})
    if task.repeated:
        ecrs.append({"step": "Rearrange", "idea": f"The routine ran {task.runs} times one item at a time. Validate "
                     "every item first, then submit in one batch; exceptions go to a person."})
    unknown = [r for r in flows if r["how"] == "typed" and source_of(r)[1] == "unknown"]
    if unknown:
        ecrs.append({"step": "Simplify", "idea": "Turn free typing into rules, lookups or drop-downs: "
                     + ", ".join(f"{r['to']['field']} ({r['to']['system']})" for r in unknown) + "."})
    ecrs.append({"step": "Automate", "idea": "Then automate what is left, as in the tool plan below."})

    sources = sorted({(r.get("origin") or r["from"])["system"] for r in moved} - {"?"})
    trigger = next((s for s in plan["sequence"] if s["stage"] == "Trigger"), None)
    sipoc = {
        "suppliers": ([f"whoever sends to {trigger['system']}"] if trigger and trigger["system"] else []) + sources,
        "inputs": sorted({label(r.get("origin") or r["from"]) for r in moved if r["from"]["system"] != "?"}),
        "process": [f"{s['stage']}: {s['system'] or s['what']}" for s in plan["sequence"]
                    if s["stage"] in ("Trigger", "Extract", "Write", "Finish")],
        "outputs": sorted({f"{r['to']['system']} · {r['to']['document']}".rstrip(" ·") for r in flows}),
        "customers": ["Who uses these outputs? (not visible in the recording: ask)"],
    }

    manual = checks["manual_entries_per_item"]
    after = checks["manual_entries_after"]
    exposure = []
    for rate in ERROR_RATES:
        r = rate["per_field"]
        exposure.append({**rate, "errors_per_month_now": round(manual * items_per_month * r, 1),
                         "errors_per_month_after": round(after * items_per_month * r, 1),
                         "dpmo": round(r * 1_000_000), "sigma": sigma_level(r * 1_000_000)})

    ctqs = []
    for r in flows:
        name = (r.get("origin") or r["from"] or r["to"])["field"] if r["how"] == "copy-paste" else r["to"]["field"]
        if re.search(r"amount|total|price|cost", name, re.I):
            ctqs.append(f"{name} must match the source exactly")
        elif re.search(r"invoice|number|\bid\b|reference", name, re.I):
            ctqs.append(f"{name} must be correct and never processed twice")
    ctqs = list(dict.fromkeys(ctqs)) + [f"Each item done within the agreed time (now about "
                                        f"{task.minutes / task.runs:.1f} min of hands-on work)"]

    return {
        "steps": steps,
        "time_by_class": {k: round(v, 2) for k, v in time_by.items()},
        "process_cycle_efficiency": round(va / total, 2),
        "wastes": wastes,
        "bottleneck": {"stage": bottleneck["stage"], "system": bottleneck["system"],
                       "minutes": round(bottleneck["minutes"], 2)} if bottleneck else None,
        "ecrs": ecrs,
        "sipoc": sipoc,
        "ctqs": ctqs,
        "error_exposure": {"manual_entries_per_item": manual, "manual_entries_after": after,
                           "items_per_month": round(items_per_month), "by_rate": exposure},
        "kpis": ["Items processed per day", "Hands-on minutes per item (target: "
                 f"{tools.get('with_suggestions', 0):.1f} or less)", "Exceptions sent to a person (%)",
                 "First-pass yield: items done with no correction (%)", "Errors caught by checks, by type",
                 "Duplicates blocked"],
    }
