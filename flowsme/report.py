"""Write the analysis as report.md, a self-contained report.html, and analysis.json."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config
from .integration import label
from .knowledge import EVIDENCE, METHODS
from .measure import control_chart_svg
from .models import Step, Task, fmt_minutes
from .page import render
from .workflow import mermaid_apps, mermaid_workflow

APPROACH_LABELS = {
    "api_integration": "API integration", "rpa": "Desktop automation (RPA)", "script": "Script",
    "built_in_feature": "Built-in feature", "ai_extraction": "AI document extraction",
    "process_change": "Process change",
}
SKETCH_FENCE_LANG = {"python": "python", "n8n": "json"}



def write_report(session_dir: Path, meta: dict, steps: list[Step], tasks: list[Task], amap: dict,
                 cfg: Config, engine: str, name: str = "report") -> dict[str, Path]:
    """report.md, report.html and analysis.json; another `name` writes only that copy's Markdown and HTML."""
    out = session_dir / "analysis"
    title = f"Workflow analysis: {meta.get('name') or _process_name(session_dir.name)}"
    markdown = _markdown(title, session_dir.name, meta, steps, tasks, amap, cfg, engine)
    paths = {"markdown": out / f"{name}.md", "html": out / f"{name}.html", "json": out / "analysis.json"}
    paths["markdown"].write_text(markdown, encoding="utf-8")
    paths["html"].write_text(render(title, markdown, "report", {"kpis": _kpis(steps, tasks, cfg)}),
                             encoding="utf-8")
    if name != "report":
        del paths["json"]
        return paths
    paths["json"].write_text(json.dumps(_as_json(session_dir, meta, steps, tasks, amap, engine), indent=2,
                                        ensure_ascii=False), encoding="utf-8")
    return paths


def _process_name(folder: str) -> str:
    """'20261024-161537-ap-invoice-narrated' -> 'Ap invoice narrated'."""
    name = re.sub(r"^\d{8}-\d{6}-?", "", folder).replace("-", " ").strip()
    return (name[:1].upper() + name[1:]) if name else folder


def _kpis(steps: list[Step], tasks: list[Task], cfg: Config) -> list[dict]:
    """The summary cards at the top of the report page."""
    a = cfg.analysis
    main = [t for t in tasks if not t.misc]
    total = sum(s.duration for s in steps) / 60
    saved = sum(t.estimate.get("minutes_saved_per_run", 0) for t in main)
    hours = sum(t.estimate.get("hours_saved_per_month", 0) for t in main)
    share = f"{saved / total * 100:.0f}%" if total else "0%"
    cards = [{"label": "Recorded work", "value": fmt_minutes(total)},
             {"label": "Could be saved per run", "value": f"{fmt_minutes(saved)} ({share})"},
             {"label": f"Hours saved a month ({a.runs_per_month} runs)", "value": f"{hours:.1f} h"}]
    if a.show_money:
        money = sum(t.estimate.get("cost_saved_per_month", 0) for t in main)
        cards.append({"label": "Worth a month", "value": f"{a.currency}{money:,}"})
    processes = [t for t in main if not t.same_as]
    cards.append({"label": "Processes found", "value": str(len(processes))})
    if processes:
        top = max(processes, key=lambda t: t.score)
        cards.append({"label": f"Biggest opportunity · {top.level.lower()} potential", "value": top.name,
                      "wide": True})
    return cards


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
    engine, _, usage = engine.partition(". AI usage: ")  # the token counts go under "How to read this"
    n = len([t for t in main if not t.same_as])
    w(f"Recorded {started or 'n/a'} · {fmt_minutes(total)} of activity · {len(steps)} steps · "
      f"{len(amap['apps'])} applications · {n} task{'s' if n != 1 else ''} · analysed with {engine}\n")
    source = meta.get("source") or {}
    if source.get("kind") == "video":
        w(f"> **Made from a screen video** ({source.get('file', 'video')}). The AI model read the steps from "
          f"{source.get('frames', 'the')} frames where the screen changed, so step times are approximate and "
          "copying between applications is inferred from what was visible, not proven. Record with FlowSME for "
          "exact timings, field names and proven data flows.\n")

    w("## Summary\n")
    w(f"- **Recorded work:** {fmt_minutes(total)}")
    w(f"- **Time automation could save:** about {fmt_minutes(saved)} per run "
      f"({saved / total * 100 if total else 0:.0f}% of the recorded time)")
    if a.show_money:
        rate = f"the {a.position} rate, " if a.position else ""
        w(f"- **If this process runs {a.runs_per_month} times a month:** about {monthly:.1f} hours a month, roughly "
          f"{cur}{sum(t.estimate.get('cost_saved_per_month', 0) for t in main):,} at {rate}{cur}{a.hourly_cost:g} an hour")
    else:
        w(f"- **If this process runs {a.runs_per_month} times a month:** about {monthly:.1f} hours a month"
          + (f" of {a.position} time" if a.position else "") + ". Your team's admin sees the money figures.")
    if ranked:
        top = ranked[0]
        why = f" {top.reasons[0]}." if top.reasons else ""
        w(f"- **Biggest opportunity:** {top.name} ({top.level.lower()} potential).{why}")
    w("\n> Savings are estimates based on this one recording. Set how often the process runs (and, in the "
      "app's Admin panel, what an hour of each position costs) to match the real business.\n")

    w("## Opportunities\n")
    w("| # | Task | Time | Runs | Applications | Automation potential | Est. saving per run |")
    w("|---|---|---|---|---|---|---|")
    for t in ranked:
        potential = f"same process as task {t.same_as}" if t.same_as else f"{t.level} ({t.score:.0f}/100)"
        w(f"| {t.id} | {_cell(t.name)} | {fmt_minutes(t.minutes)} | {t.runs} | {_cell(', '.join(t.apps[:4]))} | "
          f"{potential} | {fmt_minutes(t.estimate.get('minutes_saved_per_run', 0))} |")
    w("")

    pri = meta.get("priorities")
    if pri:
        w("## What to automate first\n")
        w(f"Impact (hours saved a month) against effort (days to build). Quick wins come first: high impact for "
          f"under {pri['effort_cut_days']:g} days of work. High impact here means at least "
          f"{pri['impact_cut_hours']:g} hours a month.\n")
        w(pri["svg"] + "\n")
        w("| Phase | # | Task | Saves a month | Effort | Pays back | Why this effort |")
        w("|---|---|---|---|---|---|---|")
        for i in pri["items"]:
            back = f"{i['payback_months']:g} months" if i["payback_months"] is not None else "-"
            money = f" ({cur}{i['money_per_month']:,})" if a.show_money else ""
            w(f"| **{i['phase']}** ({i['quadrant']}) | {i['id']} | {_cell(i['name'])} | {i['hours_per_month']} h"
              f"{money} | {i['effort_days']:g} days | {back} | {_cell('; '.join(i['effort_reasons']))} |")
        w("\n<sub>Effort starts from the recommendation's estimate (hours 0.5, days 3, weeks 15) and adds 2 days per "
          "system without a known API and half a day per open question. Payback: the hours to build it (days × 8) "
          "against the hours saved each month. Planning estimates: confirm with whoever builds it.</sub>\n")

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
    if usage:
        w(f"- **AI used for this report:** {usage}")
    privacy = ("Typed text was not stored, only counted." if not cfg.recorder.capture_typed_text
               else "Typed text was stored (capture_typed_text = true).")
    w(f"- **Privacy:** {privacy} Excluded apps and windows were not captured.\n")
    return "\n".join(out)


def _task_section(w, t: Task, cfg: Config, session: str, turns: list[dict]) -> None:
    rep = t.representative
    w(f"### {t.id}. {t.name}\n")
    if t.same_as:  # the design, SOP and roadmap entry are under the task it repeats
        w(f"**The same process as task {t.same_as}** (the same fields filled in the same applications), recorded "
          f"outside its main run: {fmt_minutes(t.minutes)}, {len(t.steps)} steps in {', '.join(t.apps)}. Its "
          f"data flow, automation design, checks and SOP are described once, under task {t.same_as}, and the "
          f"roadmap counts this time there.\n")
        return
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
        money = f" or {cfg.analysis.currency}{est['cost_saved_per_month']:,}" if cfg.analysis.show_money else ""
        w(f"**Estimated saving:** about {fmt_minutes(est['minutes_saved_per_run'])} per run "
          f"({est['automatable_fraction'] * 100:.0f}% of this task), about {est['hours_saved_per_month']} hours"
          f"{money} a month.\n")

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
    if (t.improvement or {}).get("sop"):
        w(f"**Standard operating procedure:** [{t.improvement['sop']}]({t.improvement['sop']}) (a draft with "
          "a screenshot per step, the rules and checks; for the process owner to review and approve)\n")
    _integration_section(w, t, session)
    _tobe_section(w, t)
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


def _tobe_section(w, t: Task) -> None:
    imp = t.improvement
    if not imp or not imp.get("to_be"):
        return
    tb = imp["to_be"]
    w("#### To-be process\n")
    w("What the work looks like after the change, and who does each part: automation (blue) or a person "
      "(orange). People handle only the exceptions the checks flag.\n")
    w("| | As-is | To-be with your tools | To-be with suggested tools |")
    w("|---|---|---|---|")
    for row in tb["compare"]:
        w(f"| {row['measure']} | {row['as_is']} | {row['to_be_yours']} | {row['to_be']} |")
    w("")
    w(f"```mermaid\n{tb['mermaid']}\n```\n")
    w("| # | Who | What | How |")
    w("|---|---|---|---|")
    for s in tb["steps"]:
        w(f"| {s['n']} | {s['actor']} | {_cell(s['what'])} | {_cell(s['how'] or '-')} |")
    w("")
    stories = imp.get("stories") or []
    rules = imp.get("rules") or []
    if rules:
        w("#### Business rules\n")
        w("Every rule the process follows, in one place: what the recording proves, what you said, and what is "
          "recommended or still to confirm. Confirm the open ones with the process owner before building.\n")
        w("| ID | Type | Rule | Field | Evidence | Status |")
        w("|---|---|---|---|---|---|")
        for r in rules:
            w(f"| {r['id']} | {r['type']} | {_cell(r['rule'])} | {_cell(r['field'] or '-')} | {_cell(r['evidence'])} "
              f"| {r['status']} |")
        w("")
    if stories:
        w("#### User stories and acceptance tests\n")
        w("For whoever builds the automation: each criterion is a test to pass before go-live. `flowsme build` "
          "also writes them as `acceptance.feature` (Gherkin) and `user_stories.md`.\n")
        for st in stories:
            w(f"**{st['id']} {_cell(st['title'])}.** {_cell(st['story'])}\n")
            w("\n".join(f"- Given {_cell(c['given'])}, when {_cell(c['when'])}, then {_cell(c['then'])}."
                        for c in st["criteria"]) + "\n")


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
    var = imp.get("variation")
    if var:
        w(f"Run-to-run variation over {var['runs']} items: mean {var['mean']:.0f} s, standard deviation "
          f"{var['sd']:.0f} s (coefficient of variation {var['cv']:.0%}), fastest {var['min']:.0f} s (item "
          f"{var['fastest_run']}), slowest {var['max']:.0f} s (item {var['slowest_run']}).\n")
        w(control_chart_svg(var["seconds"], var["mean"], var["ucl"], var["lcl"], f"Seconds per item, {t.name}") + "\n")
        if var["out_of_control"]:
            w(f"Item(s) {', '.join(map(str, var['out_of_control']))} fall outside the control limits: something "
              "special happened there (an exception, a slow system, a correction). Find out what before "
              "automating.\n")
        else:
            w("<sub>Individuals control chart: limits at the mean ± 2.66 × the average change between items. All "
              "items are inside, so the process is stable and its time is predictable.</sub>\n")
        if var["variable_steps"]:
            w("Steps that vary most between items:\n")
            w("\n".join(f"- Step {v['step']}: {_cell(v['description'])} ({v['mean_seconds']:.0f} s on average, "
                        f"± {v['sd_seconds']:.0f} s)" for v in var["variable_steps"]) + "\n")
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
                "description": s.description, "screenshot": s.screenshot, "signature": s.signature}

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
            "integration": t.integration, "improvement": t.improvement, "same_as": t.same_as,
            "steps_one_run": [s.index for s in t.representative],
            "runs_step_indexes": [[s.index for s in run] for run in t.iterations],
        } for t in tasks],
        "steps": [step_dict(s) for s in steps],
    }
