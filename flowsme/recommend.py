"""Turn each promising task into concrete automation recommendations (AI model or built-in rules)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import AnalysisConfig
from .integration import label
from .llm import LLM
from .models import Task, base_app
from .schemas import RECOMMENDATION
from .search import catalog_matches, search_web, task_tags
from .workflow import handoffs, step_lines

SYSTEM = (
    "You are a senior business process analyst and automation architect. You give specific, practical "
    "advice tied to the exact steps you are shown, prefer official APIs and built-in features over screen "
    "automation, and are honest when something should stay manual."
)

PROMPT = """A user's desktop work was recorded. Here is one task from that recording.

Task: {name}
Summary: {summary}
Runs in this recording: {runs} (about {per_run:.1f} min each, {total:.1f} min in total)
Applications: {apps}
Signals: {signals}
Values copied by hand between apps: {handoffs}

Steps of one run:
{steps}

Field-to-field data flow seen in the recording:
{flows}

How each system can be connected (status: documented / found online, confirm / unknown):
{connections}

Open questions the recording cannot answer:
{questions}

Lean Six Sigma findings (computed from the recording):
{lean}

Tools the customer already has: {customer_tools}
Tool plan: {tool_plan}

Existing tools found online (may be irrelevant):
{web}

Common automation options for these apps:
{catalog}

Principles to reason with: ECRS (eliminate, combine, rearrange, simplify BEFORE automating, so you don't
automate waste); Lean wastes; poka-yoke (make errors impossible rather than checking for them later); Theory of
Constraints (fix the bottleneck first); control plan (measure after go-live). Prefer the customer's own tools;
only suggest a new tool when it clearly saves more.

Return JSON with this shape:
{{
  "automation_potential": "high" | "medium" | "low",
  "assumptions": [string: everything your advice assumes that the recording or the evidence above does not show],
  "automatable_fraction": number from 0 to 1 (share of this task's time automation could remove),
  "bottlenecks": [string],
  "recommendations": [
    {{"title": string,
      "approach": "api_integration" | "rpa" | "script" | "built_in_feature" | "ai_extraction" | "process_change",
      "steps_covered": "step numbers, e.g. 3-9",
      "tools": [string],
      "how": "2-4 concrete sentences",
      "why": "the principle it applies (e.g. ECRS eliminate, poka-yoke, bottleneck) and the evidence for it",
      "effort": "hours" | "days" | "weeks",
      "time_saved_pct": number from 0 to 100}}
  ],
  "quick_wins": [string],
  "risks": [string],
  "automation_sketch": {{"language": "python" | "power_automate" | "n8n" | "pseudocode",
                         "description": string, "code": string}}
}}
Order recommendations by impact (at most 4). Only name tools, products and APIs you are sure exist; prefer
the ones listed above. Do not assume an API exists when its status is "unknown": say what to ask the vendor
instead, and list it under assumptions. Never invent where a typed value comes from. The sketch must be
working code or a step-by-step flow (not a link) that implements the top recommendation."""


def candidates(tasks: list[Task], cfg: AnalysisConfig) -> list[Task]:
    """Tasks worth an integration design and recommendations, best first."""
    ranked = sorted((t for t in tasks if not t.misc and t.score >= 15), key=lambda t: -t.score)
    return ranked[:cfg.max_recommended_tasks]


SKETCH_EXT = {"python": ".py", "n8n": ".json", "power_automate": ".txt", "pseudocode": ".txt"}


def recommend(tasks: list[Task], llm: LLM, cfg: AnalysisConfig, out_dir: Path, concurrency: int = 4,
              log=print) -> None:
    chosen = candidates(tasks, cfg)
    # Searches run one after another (search engines rate-limit); the AI calls run in parallel.
    found = [(_search(t, llm) if cfg.web_search else [], catalog_matches(t)) for t in chosen]

    def ask(item):
        task, (web, catalog) = item
        return _ai_recommendation(task, web, catalog, llm) if llm.available("text") else None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        answers = list(pool.map(ask, zip(chosen, found)))
    for task, (web, catalog), rec in zip(chosen, found, answers):
        if rec:
            rec["source"] = "ai"
        else:
            rec = _rule_recommendation(task, catalog)
            rec["source"] = "rules"
        rec["web_results"] = web
        rec["catalog"] = catalog
        sketch = rec.get("automation_sketch")
        if isinstance(sketch, dict) and sketch.get("code"):
            folder = out_dir / "automations"
            folder.mkdir(exist_ok=True)
            name = f"task{task.id}{SKETCH_EXT.get(sketch.get('language'), '.txt')}"
            (folder / name).write_text(str(sketch["code"]), encoding="utf-8")
            sketch["file"] = f"automations/{name}"
        task.recommendation = rec
        log(f"Recommendations ready for task {task.id} ({rec['source']}, {len(web)} web results)")


def _search(task: Task, llm: LLM | None = None) -> list[dict]:
    queries = []
    top = handoffs(task.steps).most_common(1)
    if top:
        (src, dst), _ = top[0]
        queries.append(f"automate copying data from {base_app(src)} to {base_app(dst)}")
    if task.name and not task.name.startswith(("Repeated routine", "Work in", "Work across")):
        queries.append(f"automate {task.name}")
    else:
        apps = list(dict.fromkeys(base_app(a) for a in task.apps))
        queries.append(f"automate {' '.join(apps[:3])} workflow")
    grounded = llm.grounded("Which existing tools, connectors or official APIs can do this, and where are they "
                            f"documented? {'; '.join(queries)}. Prefer official vendor documentation.") if llm else None
    if grounded and grounded["citations"]:
        # The model's own cited web search: keep only sources it actually cited.
        snippets = {r["url"]: r["snippet"] for r in grounded["results"]}
        return [{**c, "snippet": snippets.get(c["url"], "")} for c in grounded["citations"][:6]]
    seen, results = set(), []
    for query in queries:
        for r in search_web(query):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append(r)
    return results[:6]


def _lean_summary(task: Task) -> str:
    lean = (task.improvement or {}).get("lean")
    if not lean:
        return "- none"
    lines = [f"- {w['name']}: {w['evidence']}" for w in lean["wastes"]]
    lines.append(f"- Time per item by value class (minutes): {lean['time_by_class']}; process cycle efficiency "
                 f"{lean['process_cycle_efficiency']:.0%}")
    if lean["bottleneck"]:
        b = lean["bottleneck"]
        lines.append(f"- Bottleneck: {b['stage']} in {b['system']} ({b['minutes']} min per item)")
    lines += [f"- ECRS {e['step']}: {e['idea']}" for e in lean["ecrs"]]
    return "\n".join(lines)


def _tool_summary(task: Task) -> str:
    tools = (task.improvement or {}).get("tools")
    if not tools:
        return "none"
    stages = "; ".join(f"{s['stage']} {s['system']}: {s['with_your_tools']['method']} now possible, "
                       f"best {s['with_suggestions']['method']}" for s in tools["stages"])
    adds = ", ".join(f"{a['tool']} (-{a['minutes_saved_per_run']} min/item)" for a in tools["suggested_additions"])
    return (f"{tools['minutes_per_run']} min/item now, {tools['with_your_tools']} with their tools, "
            f"{tools['with_suggestions']} with additions [{adds or 'none needed'}]. Stages: {stages}")


def _ai_recommendation(task: Task, web: list[dict], catalog: list[dict], llm: LLM) -> dict | None:
    f = task.features
    plan = task.integration or {}
    flows = "\n".join(
        f"- {label(r['from']) if r['how'] == 'copy-paste' else 'typed by hand'} -> {label(r['to'])}"
        + (f" (typed, but proven the same value as {label(r['confirmed_source'])})" if r.get("confirmed_source")
           else "")
        + (f" (originally {label(r['origin'])})" if r.get("origin") else "") for r in plan.get("data_flows", []))
    connections = "\n".join(
        f"- {s['system']}: " + "; ".join(f"{o['method']} {o['name']} [{o['status']}]" for o in s["options"])
        for s in plan.get("systems", []))
    prompt = PROMPT.format(
        name=task.name, summary=task.summary, runs=task.runs, per_run=f["minutes_per_run"], total=f["minutes"],
        apps=", ".join(task.apps), signals="; ".join(task.reasons) or "none",
        handoffs=", ".join(f"{a} → {b} ×{n}" for (a, b), n in handoffs(task.steps).most_common(4)) or "none",
        flows=flows or "- none", connections=connections or "- none",
        lean=_lean_summary(task), customer_tools=", ".join(
            t["given"] for t in (task.improvement or {}).get("tools", {}).get("customer_tools", [])) or "none given",
        tool_plan=_tool_summary(task),
        questions="\n".join(f"- {q.replace('**', '')}" for q in plan.get("open_questions", [])) or "- none",
        steps=step_lines(task.representative),
        web="\n".join(f"- {r['title']} ({r['url']}): {r['snippet'][:160]}" for r in web) or "- none",
        catalog="\n".join(f"- {c['name']}: {c['fit']}" for c in catalog) or "- none",
    )
    data = llm.chat_json(prompt, schema=RECOMMENDATION, role="text", system=SYSTEM, max_tokens=12000)
    if not isinstance(data, dict) or not isinstance(data.get("recommendations"), list):
        return None
    data["recommendations"] = [r for r in data["recommendations"] if isinstance(r, dict) and r.get("title")]
    sketch = data.get("automation_sketch")
    code = str(sketch.get("code") or "").strip() if isinstance(sketch, dict) else ""
    if len(code.splitlines()) < 3 or code.startswith("http"):  # a link or a one-liner is not a draft
        data["automation_sketch"] = _pseudocode(task)
    return data if data["recommendations"] else None


def _rule_recommendation(task: Task, catalog: list[dict]) -> dict:
    f = task.features
    tags = task_tags(task)
    rep = task.representative
    top = handoffs(task.steps).most_common(1)
    catalog_names = [c["name"] for c in catalog]

    def steps_where(pred) -> str:
        return _ranges([i for i, s in enumerate(rep, 1) if pred(s)])

    recs = []
    if top:
        (src, dst), count = top[0]
        recs.append({
            "title": f"Stop re-keying data from {src} into {dst}", "approach": "api_integration",
            "steps_covered": steps_where(lambda s: s.copies or s.pastes), "tools": catalog_names[:3],
            "how": (f"{count} values were copied by hand from {src} to {dst} in this recording. Read them from the "
                    f"source directly (API, export, or document extraction for PDFs) and write them into {dst} "
                    "automatically (API or bulk import; desktop automation only if it has neither)."),
            "effort": "days", "time_saved_pct": 60})
    if "pdf" in tags:
        recs.append({
            "title": "Extract document fields automatically", "approach": "ai_extraction",
            "steps_covered": steps_where(lambda s: "acrobat" in s.app.lower() or ".pdf" in s.title.lower()),
            "tools": ["Azure Document Intelligence", "Google Document AI", "AWS Textract"],
            "how": ("The values read off PDFs can be pulled out by a document-extraction service. A person then "
                    "only checks the documents where the service is unsure."),
            "effort": "days", "time_saved_pct": 50})
    if task.repeated:
        recs.append({
            "title": f"Automate the {len(rep)}-step routine as a loop", "approach": "rpa",
            "steps_covered": f"1-{len(rep)}", "tools": ["Power Automate Desktop", "UiPath", "Python + Playwright"],
            "how": (f"The same routine ran {task.runs} times (about {f['minutes_per_run']:.1f} min each). Build it "
                    "once as a desktop flow or script, loop over the work queue, and send only exceptions to a person."),
            "effort": "days", "time_saved_pct": 70})
    if "web" in tags:
        recs.append({
            "title": "Check the web portal for an API or bulk upload", "approach": "script",
            "steps_covered": steps_where(lambda s: base_app(s.app) in ("Chrome", "Edge", "Firefox", "Brave")),
            "tools": ["Portal API or CSV upload", "Playwright"],
            "how": ("Many portals accept an API call, a CSV upload or an email-in address. If this one does not, "
                    "Playwright can fill the form from the spreadsheet."),
            "effort": "days", "time_saved_pct": 40})
    if f["chars_typed"] >= 150:
        recs.append({
            "title": "Cut down manual typing", "approach": "built_in_feature",
            "steps_covered": steps_where(lambda s: s.chars_typed > 0), "tools": ["Templates", "Autofill", "Drop-down lists"],
            "how": (f"{f['chars_typed']:,} characters were typed by hand. Templates, quick parts, drop-down lists or "
                    "a bulk import can replace most of it."),
            "effort": "hours", "time_saved_pct": 25})
    if f["app_switches"] / max(f["steps"], 1) >= 0.3:
        recs.append({
            "title": "Reduce back-and-forth between windows", "approach": "process_change",
            "steps_covered": "", "tools": [],
            "how": (f"There were {f['app_switches']} window switches. Batch the work (collect all values first, then "
                    "enter them in one pass) or keep source and target side by side."),
            "effort": "hours", "time_saved_pct": 15})
    if not recs:
        recs.append({
            "title": "Record this task a few more times", "approach": "process_change", "steps_covered": "",
            "tools": [], "how": "There is no strong automation signal in one recording. More runs show whether it repeats.",
            "effort": "hours", "time_saved_pct": 5})

    quick_wins = []
    if top:
        quick_wins.append(f"Put {top[0][0][0]} and {top[0][0][1]} side by side to avoid switching windows")
    if task.repeated:
        quick_wins.append("Work through the items as a batch instead of one at a time")
    unknown_apis = [o["name"] for s in (task.integration or {}).get("systems", []) for o in s["options"]
                    if o["method"] == "API" and o["status"] != "documented"]
    return {
        "automation_potential": task.level.lower(),
        "assumptions": [f"{name}: not confirmed. Ask the vendor or IT whether it exists and how to get access."
                        for name in unknown_apis],
        "automatable_fraction": None,
        "bottlenecks": task.reasons,
        "recommendations": recs[:5],
        "quick_wins": quick_wins,
        "risks": ["Check automated entries against the source before submitting (IDs, amounts, dates)",
                  "Keep a person in the loop for exceptions and anything that needs judgement"],
        "automation_sketch": _pseudocode(task),
    }


def _pseudocode(task: Task) -> dict:
    lines = ["# Draft generated from the recording. Review before building.", ""]
    indent = ""
    if task.repeated:
        lines.append(f"for each item in the work queue:   # ran {task.runs} times in the recording")
        indent = "    "
    lines += [f"{indent}{i:>2}. [{s.app}] {s.description}" for i, s in enumerate(task.representative, 1)]
    return {"language": "pseudocode", "description": "The routine as recorded: a starting point for a script or desktop flow.",
            "code": "\n".join(lines)}


def _ranges(numbers: list[int]) -> str:
    parts, start, prev = [], None, None
    for n in numbers + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            parts.append(f"{start}-{prev}" if prev != start else f"{start}")
            start = prev = n
    return ", ".join(parts)
