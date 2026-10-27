"""Standard operating procedure (SOP) from a recorded routine: purpose, scope, what to open first, numbered steps
with a screenshot of the window, why each step matters (from the narration), the rules and checks that apply, what
to do when something is off, and an approval block. A draft for the process owner to review, not a final SOP."""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from .describe import _encode_screenshot
from .integration import _navigation, document_of, endpoint, label
from .models import Task, base_app, fmt_minutes
from .schemas import SOP

MAX_IMAGES = 30
NAVIGATE = "Goes to the target cell or place: types its address in the Go To box and presses Enter."
DIALOGS = re.compile(r"^(open|save( as)?|print|go ?to|find( and replace)?|replace)$", re.I)  # not things to open
PROMPT = """Below are the recorded steps of one routine, in order. Rewrite each as one short instruction for a new
colleague, in the imperative ("Copy the invoice number from the invoice in Notepad."). Keep the order and exactly
{n} instructions. Use only what each step says: don't add values, names or details that aren't there.

Routine: {name}. {summary}

{lines}

Return JSON: {{"purpose": "one sentence: what this procedure achieves and for whom", "steps": [{n} instructions]}}"""
SYSTEM = "You write clear, plain standard operating procedures from recorded office work. Never invent details."


def write_sop(session_dir: Path, task: Task, plan: dict, checks: dict, rules: list[dict], variation: dict | None,
              llm=None, log=print) -> Path:
    steps = [s for s in task.representative if not s.excluded]
    # Typing into a Go To or Find box moves to a place; say so instead of "type 3 characters".
    said = [NAVIGATE if _navigation(s) and s.chars_typed else s.description for s in steps]
    purpose, instructions = task.summary, [_imperative(d) for d in said]
    if llm and llm.available("text") and steps:
        lines = "\n".join(f"{i}. [{s.app}] {d}" for i, (s, d) in enumerate(zip(steps, said), 1))
        data = llm.chat_json(PROMPT.format(n=len(steps), name=task.name, summary=task.summary, lines=lines),
                             schema=SOP, role="text", system=SYSTEM, effort="low", max_tokens=200 + 60 * len(steps))
        if isinstance(data, dict) and isinstance(data.get("steps"), list) and len(data["steps"]) == len(steps):
            instructions = [str(t).strip() or i for t, i in zip(data["steps"], instructions)]
            purpose = str(data.get("purpose") or purpose)
    out = session_dir / "analysis"
    name = f"sop-task{task.id}"
    md = _markdown(session_dir, task, steps, instructions, purpose, plan, checks, rules, variation, embed=False)
    page = _markdown(session_dir, task, steps, instructions, purpose, plan, checks, rules, variation, embed=True)
    from .report import HTML  # the report's page shell renders the Markdown

    (out / f"{name}.md").write_text(md, encoding="utf-8")
    (out / f"{name}.html").write_text(HTML.replace("__TITLE__", html.escape(f"SOP: {task.name}")).replace(
        "__MARKDOWN__", page.replace("</script", "<\\/script")), encoding="utf-8")
    log(f"SOP written for task {task.id}: {name}.html")
    return out / f"{name}.html"


def _imperative(sentence: str) -> str:
    """'Copies the invoice number...' -> 'Copy the invoice number...' (used when no AI model is available)."""
    words = (sentence or "").strip().split(" ", 1)
    if not words[0]:
        return sentence
    verb, rest = words[0], (" " + words[1] if len(words) > 1 else "")
    low = verb.lower()
    if low in ("goes", "does"):
        verb = verb[:-2]
    elif low.endswith("ies") and len(low) > 4:
        verb = verb[:-3] + "y"
    elif re.search(r"(ss|sh|ch|x|z)es$", low):
        verb = verb[:-2]
    elif low.endswith("s") and not low.endswith("ss") and len(low) > 3:
        verb = verb[:-1]
    return verb + rest


def _markdown(session_dir: Path, task: Task, steps, instructions, purpose, plan, checks, rules, variation,
              embed: bool) -> str:
    w: list[str] = []
    runs = variation["runs"] if variation else task.runs
    per_item = (f"about {fmt_minutes(variation['mean'] / 60)} per item (fastest {variation['min']:.0f} s, slowest "
                f"{variation['max']:.0f} s)" if variation else f"about {fmt_minutes(task.minutes / max(task.runs, 1))} "
                                                                  "per item")
    w.append(f"# SOP: {task.name}\n")
    w.append(f"<sub>Standard operating procedure, version 1 (draft). Written by FlowSME from the recording "
             f"`{session_dir.name}` on {date.today().isoformat()}. Review and approve before use.</sub>\n")
    w.append("| | |\n|---|---|")
    w.append(f"| Purpose | {_cell(purpose)} |")
    w.append(f"| Applies to | Each item of this routine (seen {runs} times in the recording, {per_item}) |")
    w.append(f"| Applications | {', '.join(dict.fromkeys(base_app(s.app) for s in steps))} |")
    if task.inputs:
        w.append(f"| Inputs | {_cell('; '.join(task.inputs))} |")
    if task.outputs:
        w.append(f"| Outputs | {_cell('; '.join(task.outputs))} |")
    w.append("| Process owner | _to fill in_ |\n")

    docs = list(dict.fromkeys(f"{d} ({base_app(s.app)})" for s in steps
                              if (d := document_of(s)) and not DIALOGS.match(d)))
    w.append("## Before you start\n")
    w.append("\n".join(f"- Open {d}" for d in docs) or "- Open the applications listed above.")
    w.append("- Pause any recording or screen sharing before handling personal or confidential data.\n")

    w.append("## Procedure\n")
    by_field_checks: dict[str, list[str]] = {}
    for c in checks["checks"]:
        by_field_checks.setdefault(c["where"], [])
        if c["check"] not in by_field_checks[c["where"]]:
            by_field_checks[c["where"]].append(c["check"])
    by_field_rules: dict[str, list[dict]] = {}
    for r in rules:
        if r["type"] in ("mapping", "calculation") and r["field"]:
            by_field_rules.setdefault(r["field"], []).append(r)
    shown, last_shot = 0, None
    for n, (step, text) in enumerate(zip(steps, instructions), 1):
        where = f"{step.app}" + (f" · {step.target}" if step.target and step.action != "type" else "")
        w.append(f"### {n}. {text}\n")
        w.append(f"<sub>{_cell(where)} · about {max(step.duration, 1):.0f} s</sub>\n")
        if step.screenshot and step.screenshot != last_shot and shown < MAX_IMAGES:
            image = _image(session_dir, step, embed)
            if image:
                w.append(image + "\n")
                shown += 1
                last_shot = step.screenshot
        if step.narration:
            w.append(f"> **Why:** \"{_cell(step.narration[:300])}\"\n")
        field = label(endpoint(step)) if (step.pastes or step.chars_typed) else ""
        for r in by_field_rules.get(field, []):
            w.append(f"- **Rule {r['id']}:** {_cell(r['rule'])}")
        for check in by_field_checks.get(field, [])[:3]:
            w.append(f"- **Check:** {_cell(check)}")
        w.append("")

    if rules:
        w.append("## Business rules\n")
        w.append("| ID | Rule | Field | Status |\n|---|---|---|---|")
        w.append("\n".join(f"| {r['id']} | {_cell(r['rule'])} | {_cell(r['field'])} | {r['status']} |" for r in rules))
        w.append("")
    w.append("## When something is off\n")
    off = [f"**{_cell(p)}:** decide as the process owner has agreed, and write the rule into this SOP."
           for p in task.decision_points]
    off += [f"{_cell(r['text'])} Check the source before saving." for r in checks.get("rework", [])]
    off.append("A value doesn't match its source, or a field is missing: stop, don't guess, and ask the process "
               "owner.")
    w.append("\n".join(f"- {line}" for line in off) + "\n")
    if variation and variation["variable_steps"]:
        w.append("## Where time varies\n")
        w.append("\n".join(f"- Step {v['step']} takes {v['mean_seconds']:.0f} s on average but varies by about "
                           f"{v['sd_seconds']:.0f} s: {_cell(v['description'])}" for v in variation["variable_steps"]))
        w.append("")
    w.append("## Approval\n")
    w.append("| Role | Name | Date | Signature |\n|---|---|---|---|")
    w.append(f"| Drafted by | FlowSME | {date.today().isoformat()} | |")
    w.append("| Process owner | | | |\n| Approved by | | | |\n")
    return "\n".join(w)


def _image(session_dir: Path, step, embed: bool) -> str:
    alt = f"Step {step.index}"
    if not embed:
        return f"![{alt}](../{step.screenshot})"
    data = _encode_screenshot(session_dir, step, 760, crop=True)
    return f'<img alt="{alt}" src="data:image/jpeg;base64,{data}" width="560">' if data else ""


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")
