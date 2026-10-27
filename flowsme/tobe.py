"""The to-be process and its business rules: what the work looks like after the improvement, who does each part
(automation or a person), and every rule it follows in one table, the business analyst's decision table. Each rule
says where it comes from: the recording, the person's own words, or a recommendation still to confirm."""
from __future__ import annotations

from .integration import label, source_of
from .models import Task

AUTOMATION, PERSON = "Automation", "Person"


def business_rules(task: Task, plan: dict, checks: dict) -> list[dict]:
    rules: list[dict] = []

    def add(kind, rule, field, evidence, status):
        rules.append({"id": f"BR-{len(rules) + 1}", "type": kind, "rule": rule, "field": field,
                      "evidence": evidence, "status": status})

    for row in plan["data_flows"]:
        target = label(row["to"])
        src, how = source_of(row)
        if how in ("copied", "same value"):
            add("mapping", f"{row['to']['field']} = {src['field']} from {label(src)}", target,
                f"{'copied' if how == 'copied' else 'typed, and the fingerprints match'} in {row['runs']} runs",
                "confirmed by the recording")
        elif how == "narrated":
            said = row["narrated"]
            add("calculation", said["rule"] or said["answer"], target, f"you said: \"{said['quote']}\"",
                "stated by the person")
        elif how == "probably":
            add("mapping", f"{row['to']['field']} = {src['field']} from {label(src)}", target,
                "same field name, typed by hand", "to confirm")
        else:
            add("unknown", f"Where {row['to']['field']} comes from is not recorded", target,
                f"{'typed' if row['how'] == 'typed' else 'pasted'} by hand in {row['runs']} runs",
                "ask the process owner")
    for c in checks["checks"]:
        if c["method"] == "validation_rule":
            add("validation", c["check"], c["where"], c["evidence"], "recommended")
    for point in task.decision_points:
        add("decision", point, "", "a judgement call seen in the recording", "write down the rule")
    return rules


def to_be(task: Task, plan: dict, tools: dict, checks: dict, rules: list[dict]) -> dict:
    """The improved process, step by step, with who does each step."""
    steps: list[dict] = []

    def step(actor, what, system="", how=""):
        steps.append({"n": len(steps) + 1, "actor": actor, "what": what, "system": system, "how": how})

    sequence = plan["sequence"]
    trigger = next((s for s in sequence if s["stage"] == "Trigger"), None)
    step(AUTOMATION, trigger["what"] if trigger else "Start on a schedule or by hand.",
         trigger["system"] if trigger else "", trigger["method"] if trigger else "")
    for s in sequence:
        if s["stage"] == "Extract":
            step(AUTOMATION, s["what"], s["system"], s["method"])
    calc = [r for r in rules if r["type"] == "calculation"]
    if calc:
        step(AUTOMATION, "Apply the rules: " + "; ".join(r["rule"] for r in calc) + ".")
    unknown = [r for r in rules if r["type"] == "unknown"]
    if unknown:
        step(PERSON, "Enter the values whose source the recording doesn't show: "
             + ", ".join(r["field"] for r in unknown) + " (until a rule or lookup replaces them).")
    validation = [r for r in rules if r["type"] == "validation"]
    step(AUTOMATION, f"Check every item before writing it ({len(validation)} validation rules, plus duplicates "
                     "and mismatches between fields).", "", "checks in the workflow")
    step(PERSON, "Review only the items the checks flag, fix or approve them.", "", "exception queue")
    for s in sequence:
        if s["stage"] in ("Write", "Finish"):
            step(AUTOMATION, s["what"], s["system"], s["method"])
    step(AUTOMATION, "Log each run (time, result, exceptions) for the KPI report.", "", "run log")

    person = [s for s in steps if s["actor"] == PERSON]
    return {
        "steps": steps,
        "mermaid": _mermaid(steps),
        "compare": [
            {"measure": "Hands-on minutes per item", "as_is": tools["minutes_per_run"],
             "to_be_yours": tools["with_your_tools"], "to_be": tools["with_suggestions"]},
            {"measure": "Values typed or pasted by hand per item", "as_is": checks["manual_entries_per_item"],
             "to_be_yours": checks["manual_entries_after"], "to_be": checks["manual_entries_after"]},
            {"measure": "Steps a person does per item", "as_is": len([s for s in task.representative
                                                                       if not s.excluded]),
             "to_be_yours": len(person), "to_be": len(person)},
        ],
    }


def _mermaid(steps: list[dict]) -> str:
    def text(s: str) -> str:
        s = s.replace('"', "'").replace("\n", " ")
        return s if len(s) <= 70 else s[:67] + "..."

    lines = ["flowchart TD"]
    review = next((s["n"] for s in steps if s["actor"] == PERSON and "Review" in s["what"]), None)
    check = next((s["n"] for s in steps if s["what"].startswith("Check every item")), None)
    for s in steps:
        label = text(s["what"]) + (f"<br/><i>{text(s['how'])}</i>" if s["how"] else "")
        if s["n"] == check:
            lines.append(f'  S{s["n"]}{{"{text("Checks pass?")}"}}')
        elif s["actor"] == PERSON:
            lines.append(f'  S{s["n"]}[/"Person: {label}"/]')
        else:
            lines.append(f'  S{s["n"]}["{label}"]')
    order = [s["n"] for s in steps if s["n"] != review]
    for a, b in zip(order, order[1:]):
        lines.append(f'  S{a} -->{"|yes|" if a == check else ""} S{b}')
    if review and check:
        after = next((n for n in order if n > check), None)
        lines.append(f"  S{check} -->|no| S{review}")
        if after:
            lines.append(f"  S{review} --> S{after}")
    lines.append("  classDef person fill:#f9eeda,stroke:#b06d00,color:#1d1d1f")
    lines.append("  classDef auto fill:#e7f1fb,stroke:#2f5bd3,color:#1d1d1f")
    lines.append("  class " + ",".join(f"S{s['n']}" for s in steps if s["actor"] == PERSON) + " person"
                 if any(s["actor"] == PERSON for s in steps) else "")
    lines.append("  class " + ",".join(f"S{s['n']}" for s in steps if s["actor"] == AUTOMATION) + " auto")
    return "\n".join(line for line in lines if line)
