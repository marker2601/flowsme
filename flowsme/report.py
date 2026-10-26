"""Write the analysis as report.md, a self-contained report.html, and analysis.json."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .config import Config
from .integration import label
from .knowledge import EVIDENCE, METHODS
from .models import Step, Task, fmt_minutes
from .workflow import mermaid_apps, mermaid_workflow

APPROACH_LABELS = {
    "api_integration": "API integration", "rpa": "Desktop automation (RPA)", "script": "Script",
    "built_in_feature": "Built-in feature", "ai_extraction": "AI document extraction",
    "process_change": "Process change",
}
SKETCH_FENCE_LANG = {"python": "python", "n8n": "json"}

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#fbfaf8; --fg:#1d1d1f; --muted:#5f6368; --line:#e3e1dc; --accent:#2f5bd3; --card:#ffffff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16171a; --fg:#e8e8ea; --muted:#a0a3a8; --line:#2c2e33; --accent:#8fb0ff; --card:#1d1f23; }
  }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
  main { max-width:980px; margin:0 auto; padding:32px 16px 80px; }
  h1 { font-size:1.9rem; margin-bottom:.3rem; }
  h2 { margin-top:2.6rem; padding-bottom:.3rem; border-bottom:1px solid var(--line); }
  h3 { margin-top:2.2rem; }
  table { border-collapse:collapse; width:100%; font-size:.93rem; display:block; overflow-x:auto; }
  th, td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  code, pre { font-family:ui-monospace,Consolas,monospace; font-size:.86rem; }
  pre { background:var(--card); border:1px solid var(--line); padding:12px; overflow-x:auto; border-radius:6px; }
  .mermaid { background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px; overflow-x:auto; margin:1rem 0; }
  img { max-width:100%; border:1px solid var(--line); border-radius:4px; }
  a { color:var(--accent); }
  blockquote { color:var(--muted); border-left:3px solid var(--line); margin-left:0; padding-left:1rem; }
</style>
</head>
<body>
<main id="content">Loading report...</main>
<script type="text/markdown" id="md">__MARKDOWN__</script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
  const main = document.getElementById("content");
  main.innerHTML = marked.parse(document.getElementById("md").textContent);
  main.querySelectorAll("code.language-mermaid").forEach((code) => {
    const div = document.createElement("div");
    div.className = "mermaid";
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  });
  mermaid.initialize({ startOnLoad: false, theme: "default", flowchart: { htmlLabels: true } });
  mermaid.run();
</script>
</body>
</html>
"""


def write_report(session_dir: Path, meta: dict, steps: list[Step], tasks: list[Task], amap: dict,
                 cfg: Config, engine: str) -> dict[str, Path]:
    out = session_dir / "analysis"
    title = f"Workflow analysis: {meta.get('name') or session_dir.name}"
    markdown = _markdown(title, session_dir.name, meta, steps, tasks, amap, cfg, engine)
    paths = {"markdown": out / "report.md", "html": out / "report.html", "json": out / "analysis.json"}
    paths["markdown"].write_text(markdown, encoding="utf-8")
    page = HTML.replace("__TITLE__", html.escape(title)).replace("__MARKDOWN__", markdown.replace("</script", "<\\/script"))
    paths["html"].write_text(page, encoding="utf-8")
    paths["json"].write_text(json.dumps(_as_json(session_dir, meta, steps, tasks, amap, engine), indent=2,
                                        ensure_ascii=False), encoding="utf-8")
    return paths


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _markdown(title: str, session: str, meta: dict, steps: list[Step], tasks: list[Task], amap: dict, cfg: Config,
              engine: str) -> str:
    a = cfg.analysis
    cur = a.currency
    main = [t for t in tasks if not t.misc]
    ranked = sorted(main, key=lambda t: -t.score)
    total = sum(s.duration for s in steps) / 60
    saved = sum(t.estimate.get("minutes_saved_per_run", 0) for t in main)
    monthly = saved * a.runs_per_month / 60
    out: list[str] = []
    w = out.append

    w(f"# {title}\n")
    started = (meta.get("started") or "")[:16].replace("T", " ")
    w(f"Recorded {started or 'n/a'} · {fmt_minutes(total)} of activity · {len(steps)} steps · "
      f"{len(amap['apps'])} applications · {len(main)} tasks · analysed with {engine}\n")

    w("## Summary\n")
    w(f"- **Recorded work:** {fmt_minutes(total)}")
    w(f"- **Time automation could save:** about {fmt_minutes(saved)} per run "
      f"({saved / total * 100 if total else 0:.0f}% of the recorded time)")
    w(f"- **If this process runs {a.runs_per_month} times a month:** about {monthly:.1f} hours a month, roughly "
      f"{cur}{monthly * a.hourly_cost:,.0f} at {cur}{a.hourly_cost:g} an hour")
    if ranked:
        top = ranked[0]
        why = f" {top.reasons[0]}." if top.reasons else ""
        w(f"- **Biggest opportunity:** {top.name} ({top.level.lower()} potential).{why}")
    w("\n> Savings are estimates based on this one recording. Set `runs_per_month` and `hourly_cost` in "
      "`config.toml` to match how often the process really runs.\n")

    w("## Opportunities\n")
    w("| # | Task | Time | Runs | Applications | Automation potential | Est. saving per run |")
    w("|---|---|---|---|---|---|---|")
    for t in ranked:
        w(f"| {t.id} | {_cell(t.name)} | {fmt_minutes(t.minutes)} | {t.runs} | {_cell(', '.join(t.apps[:4]))} | "
          f"{t.level} ({t.score:.0f}/100) | {fmt_minutes(t.estimate.get('minutes_saved_per_run', 0))} |")
    w("")

    w("## Workflow map\n")
    w("Tasks in the order they happened. Double-bordered boxes are routines that repeated. "
      "Colour shows automation potential: red high, amber medium, green low.\n")
    w(f"```mermaid\n{mermaid_workflow(tasks)}\n```\n")

    w("## How the applications connect\n")
    w("Solid arrows: switching from one application to another. Dotted arrows: values copied in one "
      "application and pasted into another.\n")
    w(f"```mermaid\n{mermaid_apps(amap)}\n```\n")
    w("| Application | Time | Steps |")
    w("|---|---|---|")
    for app in amap["apps"]:
        w(f"| {_cell(app['app'])} | {fmt_minutes(app['minutes'])} | {app['steps']} |")
    w("")

    w("## Task details\n")
    for t in ranked:
        _task_section(w, t, cfg, session, meta.get("narration") or [])

    misc = [t for t in tasks if t.misc]
    if misc:
        m = misc[0]
        w(f"### Other short activities\n\n{len(m.steps)} short steps ({fmt_minutes(m.minutes)}) between the main "
          "tasks, such as quick checks and interruptions. Not analysed further.\n")

    if any(t.improvement for t in tasks):
        w("## Methods used\n")
        w("| Method | What it means | Sources |")
        w("|---|---|---|")
        for m in METHODS.values():
            links = ", ".join(f"[{i + 1}]({u})" for i, u in enumerate(m.get("sources", [])))
            w(f"| {m['name']} | {_cell(m['definition'])} | {links or '-'} |")
        w("")

    w("## How to read this\n")
    w("- **One recording shows one path.** Exceptions and variations only appear across several recordings of "
      "the same process, so record it a few times before building anything.")
    w("- **Timing** comes from clicks, keystrokes and window changes. Reading and thinking time before the next "
      "action counts toward the step before it.")
    w("- **Automation potential** is a 0-100 signal score built from repetition, copy-paste between applications, "
      "typing, window switching and time spent.")
    w("- **Savings** use the AI model's estimate of the automatable share when one was available, otherwise 60%, "
      "35% or 10% for high, medium or low potential.")
    w("- **Automation drafts** are starting points. Review and test them on sample data before using them on real work.")
    privacy = ("Typed text was not stored, only counted." if not cfg.recorder.capture_typed_text
               else "Typed text was stored (capture_typed_text = true).")
    w(f"- **Privacy:** {privacy} Excluded apps and windows were not captured.\n")
    return "\n".join(out)


def _task_section(w, t: Task, cfg: Config, session: str, turns: list[dict]) -> None:
    rep = t.representative
    w(f"### {t.id}. {t.name}\n")
    runs = f"{t.runs} runs of about {fmt_minutes(t.minutes / t.runs)}" if t.repeated else "1 run"
    w(f"**Automation potential: {t.level} ({t.score:.0f}/100)** · {fmt_minutes(t.minutes)} in total · {runs} · "
      f"{', '.join(t.apps)}\n")
    if t.summary:
        w(f"{t.summary}\n")
    for heading, items in (("Inputs", t.inputs), ("Outputs", t.outputs), ("Decision points", t.decision_points)):
        if items:
            w(f"**{heading}:** {'; '.join(items)}  ")
    w("")
    if t.reasons:
        w("**Why it stands out:**\n")
        w("\n".join(f"- {r}" for r in t.reasons) + "\n")
    est = t.estimate
    if est.get("minutes_saved_per_run"):
        w(f"**Estimated saving:** about {fmt_minutes(est['minutes_saved_per_run'])} per run "
          f"({est['automatable_fraction'] * 100:.0f}% of this task), about {est['hours_saved_per_month']} hours "
          f"or {cfg.analysis.currency}{est['cost_saved_per_month']:,} a month.\n")

    shots = [s for s in rep if s.screenshot and s.action == "click"]
    if shots:
        picks = [shots[round(i * (len(shots) - 1) / 2)] for i in range(min(3, len(shots)))]
        picks = list({s.index: s for s in picks}.values())
        w(" ".join(f'<img src="../{s.screenshot}" alt="Step {rep.index(s) + 1}" width="31%">' for s in picks) + "\n")

    w(f"**Steps{' (one run)' if t.repeated else ''}:**\n")
    w("\n".join(f"{i}. [{s.app}] {s.description}" for i, s in enumerate(rep[:60], 1)))
    if len(rep) > 60:
        w(f"\n_...and {len(rep) - 60} more steps._")
    w("")
    said = [x for x in turns if x["end"] >= t.start - 2 and x["start"] <= t.end + 2]
    if said:
        w("**What you said while working** (from the narration):\n")
        w("\n".join(f"- _{int(x['start'] // 60)}:{int(x['start'] % 60):02d}_ “{x['text']}”" for x in said[:15]))
        if len(said) > 15:
            w(f"\n_...and {len(said) - 15} more._")
        w("")
    _integration_section(w, t, session)
    _improvement_section(w, t, cfg)

    rec = t.recommendation
    if not rec:
        return
    source = "AI analysis" if rec.get("source") == "ai" else "built-in rules; connect an AI model for task-specific advice"
    w(f"**Recommendations** _({source})_\n")
    for i, r in enumerate(rec.get("recommendations", []), 1):
        bits = [APPROACH_LABELS.get(r.get("approach"), str(r.get("approach") or ""))]
        if r.get("effort"):
            bits.append(f"effort: {r['effort']}")
        if isinstance(r.get("time_saved_pct"), (int, float)):
            bits.append(f"about {r['time_saved_pct']:.0f}% of this task's time")
        if r.get("steps_covered"):
            bits.append(f"steps {r['steps_covered']}")
        w(f"{i}. **{r.get('title', '')}**: _{' · '.join(b for b in bits if b)}_\n")
        if r.get("how"):
            w(f"   {r['how']}\n")
        if r.get("why"):
            w(f"   *Why:* {r['why']}\n")
        if r.get("tools"):
            w(f"   Tools: {', '.join(map(str, r['tools']))}\n")
    for heading, key in (("Assumptions to confirm", "assumptions"), ("Quick wins", "quick_wins"),
                         ("Check before automating", "risks")):
        items = rec.get(key) or []
        if items:
            w(f"**{heading}:**\n")
            w("\n".join(f"- {x}" for x in items) + "\n")
    if rec.get("web_results"):
        w("**Existing tools and guides found online:**\n")
        w("\n".join(f"- [{_cell(r['title']) or r['url']}]({r['url']}): {_cell(r['snippet'][:160])}"
                    for r in rec["web_results"]) + "\n")
    if rec.get("catalog"):
        w("**Tools that usually fit these applications:**\n")
        w("\n".join(f"- [{c['name']}]({c['url']}): {c['fit']}" for c in rec["catalog"]) + "\n")
    sketch = rec.get("automation_sketch")
    if isinstance(sketch, dict) and sketch.get("code"):
        lang = str(sketch.get("language", "text"))
        saved_to = f", saved to `{sketch['file']}`" if sketch.get("file") else ""
        w(f"**Automation draft** ({lang}{saved_to})\n")
        if sketch.get("description"):
            w(f"{sketch['description']}\n")
        code = str(sketch["code"])
        fence = "````" if "```" in code else "```"
        w(f"{fence}{SKETCH_FENCE_LANG.get(lang, 'text')}\n{code}\n{fence}\n")


def _integration_section(w, t: Task, session: str) -> None:
    plan = t.integration
    if not plan:
        return
    w("#### Data flow: field to field\n")
    w("Taken from the recording: every value that was copied or typed, where from and where to.\n")
    if plan["data_flows"]:
        w("| Step | From | To | How | Seen in runs |")
        w("|---|---|---|---|---|")
        for r in plan["data_flows"]:
            src = label(r["from"]) if r["how"] == "copy-paste" else "typed by hand"
            if r.get("confirmed_source"):
                src += f"; **same value as {label(r['confirmed_source'])}** (fingerprint match, {r['value_match']})"
            if r.get("origin"):
                src += f" (originally {label(r['origin'])})"
            if r.get("narrated"):
                src += f"; **explained in your narration:** “{r['narrated']['quote']}”"
            elif r.get("likely_source"):
                src += f"; probably {label(r['likely_source'])}, same field name: confirm"
            how = "copy-paste" if r["how"] == "copy-paste" else f"typed ({r['chars']} characters)"
            steps = ", ".join(str(n) for n in sorted(set(r["steps"]))) or "-"
            w(f"| {steps} | {_cell(src)} | {_cell(label(r['to']))} | {how} | {r['runs']} |")
        w("")
    else:
        w("No values were copied or typed in this task.\n")
    if plan.get("answered_by_narration"):
        w("**Answered by your narration** (quoted word for word from the transcript):\n")
        w("\n".join(f"- {a['field']}: {a['answer']}" + (f" Rule: {a['rule']}." if a["rule"] else "")
                    + f" (“{a['quote']}”)" for a in plan["answered_by_narration"]) + "\n")
    if plan["open_questions"]:
        w("**Decide before automating:**\n")
        w("\n".join(f"- {q}" for q in plan["open_questions"]) + "\n")

    w("#### How the systems can connect\n")
    w("Each option has a status: *documented* (official documentation, access still needed), *found online, "
      "confirm* (a web search hit), *unknown* (no evidence: ask the vendor or IT), or a fallback.\n")
    w("| System | Role | Options |")
    w("|---|---|---|")
    for s in plan["systems"]:
        options = "<br>".join(
            f"{o['method']}: {'[' + _cell(o['name']) + '](' + o['url'] + ')' if o.get('url') else _cell(o['name'])}"
            f" *({o['status']})*" for o in s["options"])
        w(f"| {_cell(s['system'])} | {s['role']} | {options} |")
    w("")

    w("#### Proposed automation sequence\n")
    for s in plan["sequence"]:
        w(f"{s['n']}. **{s['stage']}**{' · ' + s['system'] if s['system'] else ''}: {s['what']}")
        for f in s["fields"]:
            w(f"   - {f}")
        w(f"   - _How: {s['method']} ({s['status']})_")
    w("")
    w(f"**To build it**, once the questions above are answered, run "
      f"`python -m flowsme build {session} --task {t.id}`. It writes an n8n workflow to import, a Postman "
      "collection to test each connection, and SQL and Playwright starters.\n")


def _improvement_section(w, t: Task, cfg: Config) -> None:
    imp = t.improvement
    if not imp:
        return
    tools, checks, lean = imp["tools"], imp["validation"], imp["lean"]
    per_month = t.runs * cfg.analysis.runs_per_month
    w("#### Process improvement (Lean Six Sigma, DMAIC)\n")

    w("**Define.** What must go right (critical to quality):\n")
    w("\n".join(f"- {c}" for c in lean["ctqs"]) + "\n")
    s = lean["sipoc"]
    w("| Suppliers | Inputs | Process | Outputs | Customers |")
    w("|---|---|---|---|---|")
    w("| " + " | ".join("<br>".join(_cell(x) for x in s[k]) or "-"
                        for k in ("suppliers", "inputs", "process", "outputs", "customers")) + " |\n")

    total = sum(lean["time_by_class"].values()) or 1
    names = {"VA?": "value-adding? (judgement, confirm)", "BVA": "business-required", "NVA": "waste (non-value-adding)"}
    w(f"**Measure.** {tools['minutes_per_run']} min of hands-on work per item, {t.runs} items in this recording, "
      f"{checks['manual_entries_per_item']} manual entries per item.\n")
    w("| Time per item | Minutes | Share |")
    w("|---|---|---|")
    for cls, minutes in sorted(lean["time_by_class"].items(), key=lambda kv: -kv[1]):
        w(f"| {names.get(cls, cls)} | {minutes:.2f} | {minutes / total:.0%} |")
    w(f"\nProcess cycle efficiency: **{lean['process_cycle_efficiency']:.0%}** (value-adding time ÷ total).\n")
    exposure = lean["error_exposure"]
    if exposure["by_rate"]:
        w(f"Error exposure: {exposure['manual_entries_per_item']} manual entries per item now, "
          f"{exposure['manual_entries_after']} after automation, about {exposure['items_per_month']} items a month.\n")
        w("| Human error rate (source) | Errors a month now | After automation | DPMO | Sigma level |")
        w("|---|---|---|---|---|")
        for r in exposure["by_rate"]:
            w(f"| {r['per_field']:.2%} per entry: [{_cell(r['context'])}]({r['source']}) | {r['errors_per_month_now']} "
              f"| {r['errors_per_month_after']} | {r['dpmo']:,} | {r['sigma']} |")
        w("")

    w("**Analyze.** Waste found in the recording:\n")
    w("| Waste | Evidence | Min per item |")
    w("|---|---|---|")
    for item in lean["wastes"]:
        w(f"| {item['name']} | {_cell(item['evidence'])} | {item['minutes_per_item'] or '-'} |")
    if lean["bottleneck"]:
        b = lean["bottleneck"]
        w(f"\nBottleneck (Theory of Constraints): **{b['stage']} · {b['system']}** at {b['minutes']} min per item. "
          "Improve this stage first: speeding up any other stage doesn't shorten the whole.\n")

    w("**Improve.** First remove waste (ECRS), then automate what's left:\n")
    w("\n".join(f"- **{e['step']}:** {e['idea']}" for e in lean["ecrs"]) + "\n")
    given = ", ".join(c["given"] for c in tools["customer_tools"]) or "none given (add them with --tools)"
    w(f"Tool plan. Your tools: {given}. Runs the workflow: {tools['runner_yours'] or 'nothing you listed can'}.\n")
    w("| Stage | Now (min/item) | With your tools | With suggested tools |")
    w("|---|---|---|---|")
    for st in tools["stages"]:
        yours, best = st["with_your_tools"], st["with_suggestions"]
        w(f"| {st['n']}. {st['stage']} · {_cell(st['system'])} | {st['minutes']:.2f} | {yours['method']}"
          f"{' (' + ', '.join(yours['tools']) + ')' if yours['tools'] else ''}: {yours['minutes_after']:.2f} | "
          f"{best['method']}{' (' + ', '.join(best['tools']) + ')' if best['tools'] else ''}: "
          f"{best['minutes_after']:.2f} |")
    w(f"| **Total** | **{tools['minutes_per_run']}** | **{tools['with_your_tools']}** | "
      f"**{tools['with_suggestions']}** |\n")
    month = lambda m: f"{m * per_month / 60:.1f} h"
    w(f"Per month ({per_month} items): {month(tools['minutes_per_run'])} now, "
      f"{month(tools['with_your_tools'])} with your tools, {month(tools['with_suggestions'])} with the additions.\n")
    if tools["suggested_additions"]:
        w("Adding tools, biggest gain first:\n")
        w("\n".join(f"- **{a['tool']}** saves another {a['minutes_saved_per_run']} min per item "
                    f"({a['minutes_saved_per_run'] * per_month / 60:.1f} h a month). Cost: {a['cost']}."
                    for a in tools["suggested_additions"]) + "\n")
    vendor = [st["vendor_api"] for st in tools["stages"] if st.get("vendor_api")]
    for api in vendor:
        if api["status"] != "documented":
            w(f"If the vendor provides an API key for **{api['name']}**, use it instead of browser automation: "
              "it is more reliable and won't break when the page changes.\n")
    rates = ", ".join(f"{k.replace('_', ' ')} {v:.0%}" for k, v in tools["rates"].items())
    w(f"<sub>Minutes after automation assume each method removes this share of a stage's time (people still "
      f"review exceptions): {rates}. These are planning estimates; measure after the pilot.</sub>\n")

    w("**Control.** Validation checklist: error-proofing first, then detection. Why that order: "
      + " ".join(f"{e['finding']} ([{e['who']}]({e['source']}))" for e in EVIDENCE) + "\n")
    w("| Where | Type | Check | Evidence |")
    w("|---|---|---|---|")
    for c in checks["checks"]:
        w(f"| {_cell(c['where'])} | {c['type']} | {_cell(c['check'])} | {_cell(c['evidence'])} |")
    w("\nMeasure after go-live, in the final report (Power BI, Looker Studio or Excel, fed by the run log "
      f"from `flowsme build`; suggested: {tools['report_tool']}):\n")
    w("\n".join(f"- {k}" for k in lean["kpis"]) + "\n")


def _as_json(session_dir: Path, meta: dict, steps: list[Step], tasks: list[Task], amap: dict, engine: str) -> dict:
    def step_dict(s: Step) -> dict:
        return {"index": s.index, "start": s.start, "duration": round(s.duration, 1), "app": s.app,
                "action": s.action, "target": s.target, "chars_typed": s.chars_typed, "shortcuts": s.shortcuts,
                "description": s.description, "screenshot": s.screenshot}

    return {
        "session": session_dir.name,
        "meta": meta,
        "engine": engine,
        "apps": amap,
        "tasks": [{
            "id": t.id, "name": t.name, "summary": t.summary, "misc": t.misc, "runs": t.runs,
            "minutes": round(t.minutes, 1), "apps": t.apps, "inputs": t.inputs, "outputs": t.outputs,
            "decision_points": t.decision_points, "score": t.score, "level": t.level, "reasons": t.reasons,
            "features": t.features, "estimate": t.estimate, "recommendation": t.recommendation,
            "integration": t.integration, "improvement": t.improvement,
            "steps_one_run": [s.index for s in t.representative],
            "runs_step_indexes": [[s.index for s in run] for run in t.iterations],
        } for t in tasks],
        "steps": [step_dict(s) for s in steps],
    }
