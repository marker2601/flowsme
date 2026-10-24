"""Name tasks, map how applications connect, and draw Mermaid diagrams."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .describe import SYSTEM
from .llm import LLM
from .models import Step, Task, fmt_minutes

TASK_PROMPT = """Below are the steps of one task from a recorded desktop session{runs_note}.

{steps}

Return JSON with this shape:
{{"name": "task name, 3-7 words, starting with a verb",
  "summary": "2-3 sentences: what the task achieves and how it is done",
  "inputs": ["data or documents the task starts from"],
  "outputs": ["what the task produces or updates"],
  "decision_points": ["places where the user has to make a judgement call"]}}"""


def handoffs(steps: list[Step]) -> Counter:
    """Copy in one app followed by paste in another = data carried between apps by hand."""
    found: Counter = Counter()
    source = None
    for step in steps:
        if step.pastes and source and source != step.app:
            found[(source, step.app)] += 1
        if step.copies:
            source = step.app
    return found


def step_lines(steps: list[Step], limit: int = 60) -> str:
    lines = [f"{i}. [{s.app}] {s.description}" for i, s in enumerate(steps[:limit], 1)]
    if len(steps) > limit:
        lines.append(f"... and {len(steps) - limit} more steps")
    return "\n".join(lines)


def summarize_tasks(tasks: list[Task], llm: LLM, concurrency: int = 4, log=print) -> None:
    def ask(task: Task):
        if not llm.available("text") or task.misc:
            return None
        note = f" (this routine ran {task.runs} times; one run is shown)" if task.repeated else ""
        return llm.chat_json(TASK_PROMPT.format(runs_note=note, steps=step_lines(task.representative)),
                             role="text", system=SYSTEM, max_tokens=600)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        answers = list(pool.map(ask, tasks))
    named = 0
    for task, data in zip(tasks, answers):
        if isinstance(data, dict) and data.get("name"):
            task.name = str(data["name"]).strip()
            task.summary = str(data.get("summary", "")).strip()
            task.inputs = [str(x) for x in data.get("inputs") or []]
            task.outputs = [str(x) for x in data.get("outputs") or []]
            task.decision_points = [str(x) for x in data.get("decision_points") or []]
            named += 1
        else:
            task.name, task.summary = _fallback_name(task)
    if named:
        log(f"Named {named} tasks with {llm.model('text')}")


def _fallback_name(task: Task) -> tuple[str, str]:
    apps = task.apps
    route = " → ".join(apps[:4]) + (" …" if len(apps) > 4 else "")
    if task.misc:
        name = "Other short activities"
    elif task.repeated:
        name = f"Repeated routine across {route}"
    elif len(apps) == 1:
        name = f"Work in {apps[0]}"
    else:
        name = f"Work across {route}"
    actions = list(dict.fromkeys(s.description for s in task.representative))
    summary = "Main actions: " + "; ".join(actions[:4]) + ("; …" if len(actions) > 4 else ".")
    return name, summary


def app_map(steps: list[Step]) -> dict:
    time: Counter = Counter()
    count: Counter = Counter()
    for step in steps:
        time[step.app] += step.duration
        count[step.app] += 1
    switches = Counter((a.app, b.app) for a, b in zip(steps, steps[1:]) if a.app != b.app)
    return {
        "apps": [{"app": app, "minutes": round(secs / 60, 1), "steps": count[app]} for app, secs in time.most_common()],
        "switches": [{"from": a, "to": b, "count": n} for (a, b), n in switches.most_common()],
        "handoffs": [{"from": a, "to": b, "count": n} for (a, b), n in handoffs(steps).most_common()],
    }


# ---- Mermaid ------------------------------------------------------------------------------------

CLASS_DEFS = [
    "  classDef high fill:#fde2e1,stroke:#c0392b,color:#1a1a1a",
    "  classDef medium fill:#fff3cd,stroke:#b8860b,color:#1a1a1a",
    "  classDef low fill:#e6f4ea,stroke:#2e7d32,color:#1a1a1a",
]


def _esc(text: str, limit: int = 60) -> str:
    text = text if len(text) <= limit else text[:limit - 1] + "…"
    return text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def mermaid_workflow(tasks: list[Task]) -> str:
    lines = ["flowchart TD", "  S0([Start])"]
    prev = "S0"
    main = [t for t in tasks if not t.misc]
    for task in main:
        node = f"T{task.id}"
        parts = [_esc(f"{task.id}. {task.name}"),
                 _esc(f"{fmt_minutes(task.minutes)} · {', '.join(task.apps[:3])}")]
        if task.repeated:
            parts.append(f"×{task.runs} runs")
        shape = ("[[", "]]") if task.repeated else ("[", "]")
        lines.append(f'  {node}{shape[0]}"{"<br/>".join(parts)}"{shape[1]}')
        lines.append(f"  {prev} --> {node}")
        if task.repeated:
            lines.append(f'  {node} -->|"repeat ×{task.runs}"| {node}')
        prev = node
    lines.append(f"  {prev} --> E0([End])")
    lines += CLASS_DEFS
    lines += [f"  class T{t.id} {t.level.lower()}" for t in main if t.level]
    return "\n".join(lines)


def mermaid_apps(amap: dict, max_edges: int = 14) -> str:
    ids = {a["app"]: f"A{i}" for i, a in enumerate(amap["apps"], 1)}
    lines = ["flowchart LR"]
    for a in amap["apps"]:
        lines.append(f'  {ids[a["app"]]}["{_esc(a["app"])}<br/>{fmt_minutes(a["minutes"])}"]')
    for edge in [e for e in amap["switches"] if e["count"] >= 2][:max_edges]:
        lines.append(f'  {ids[edge["from"]]} -->|"{edge["count"]}× switch"| {ids[edge["to"]]}')
    for edge in amap["handoffs"][:max_edges]:
        lines.append(f'  {ids[edge["from"]]} -.->|"{edge["count"]}× copy-paste"| {ids[edge["to"]]}')
    return "\n".join(lines)
