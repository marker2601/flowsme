"""The analysis pipeline: events -> steps -> tasks -> descriptions -> scores -> recommendations -> report."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Config
from .describe import describe_steps
from .llm import LLM
from .integration import plan
from .measure import variation
from .methods import analyse
from .narration import attach, transcribe_session
from .toolplan import tool_plan
from .validation import checklist
from .recommend import candidates, recommend
from .report import write_report
from .score import apply_estimates, score_task, task_features
from .segment import build_steps, build_tasks
from .sop import write_sop
from .tobe import business_rules, to_be
from .session import load_events, load_meta
from .workflow import app_map, summarize_tasks


def analyze_session(session_dir: Path, cfg: Config, log=print) -> dict:
    events = load_events(session_dir)
    meta = load_meta(session_dir)
    steps = build_steps(events, cfg.analysis.idle_gap_seconds)
    if not steps:
        raise SystemExit(f"No clicks or keystrokes found in {session_dir}")
    tasks = build_tasks(steps, cfg.analysis.idle_gap_seconds)
    repeated = sum(t.repeated for t in tasks)
    log(f"{len(events)} events -> {len(steps)} steps -> {len(tasks)} tasks ({repeated} repeated routines)")

    out = session_dir / "analysis"
    out.mkdir(exist_ok=True)
    llm = LLM(cfg.llm)
    llm.use_cache(out / "ai_cache.json")  # re-runs don't pay twice for the same request
    llm.use_shared_cache(Path(cfg.sessions_dir) / "shared_ai_cache.json")  # vendor API checks, across reports
    turns = transcribe_session(session_dir, meta, llm, log)
    if turns:
        attach(steps, turns)
        meta = {**meta, "narration": turns}
    concurrency = cfg.llm.concurrency
    from_screenshots = describe_steps(steps, tasks, llm, cfg.llm, session_dir, out / "descriptions.json", log)
    summarize_tasks(tasks, llm, concurrency, log)
    for task in tasks:
        task.features = task_features(task)
        task.score, task.level, task.reasons = (0, "", []) if task.misc else score_task(task.features)
    explained: dict = {}  # field -> rule from the narration, shared by all tasks
    # In recording order: people explain a rule the first time they apply it, and later tasks reuse it.
    for task in sorted(candidates(tasks, cfg.analysis), key=lambda t: t.start):
        task.integration = plan(task, cfg.analysis.web_search, steps, llm, turns, explained)
        tools = tool_plan(task, task.integration, cfg.customer.tools)
        checks = checklist(task, task.integration, steps)
        lean = analyse(task, task.integration, tools, checks, task.runs * cfg.analysis.runs_per_month)
        rules = business_rules(task, task.integration, checks)
        task.improvement = {"tools": tools, "validation": checks, "lean": lean, "variation": variation(task),
                            "rules": rules, "to_be": to_be(task, task.integration, tools, checks, rules)}
    log("Integration design ready: data flow, connections, tool plan, checklist, Lean Six Sigma analysis"
        + (f" (customer tools: {', '.join(cfg.customer.tools)})" if cfg.customer.tools else " (no customer tools given)"))
    recommend(tasks, llm, cfg.analysis, out, concurrency, log)
    apply_estimates(tasks, cfg.analysis)
    designed = [t for t in tasks if t.improvement]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:  # one SOP per designed task
        sops = list(pool.map(lambda t: write_sop(session_dir, t, t.integration, t.improvement["validation"],
                                                 t.improvement["rules"], t.improvement["variation"], llm, log),
                             designed))
    for task, path in zip(designed, sops):
        task.improvement["sop"] = path.name

    parts = []
    if from_screenshots:
        parts.append(f"{cfg.llm.vision_model} for screenshots")
    if "text" in llm.used_roles:
        parts.append(f"{cfg.llm.text_model} for text")
    if cfg.llm.provider == "offline":
        engine = "built-in heuristics (no AI model)"
    elif not parts:
        engine = "built-in heuristics (the AI model could not be used)"
    else:
        engine = " and ".join(parts)
        if not from_screenshots:
            engine += " (steps described from window and UI-element names, not screenshots)"
        if llm.failed_roles or llm.over_budget:
            engine += "; heuristics filled in where a model failed or the spending cap was reached"
    if cfg.llm.provider != "offline":
        log(f"AI usage: {llm.cost_summary()}")
        engine += f". AI usage: {llm.cost_summary()}"
        meta = {**meta, "ai_usage": llm.usage}
    paths = write_report(session_dir, meta, steps, tasks, app_map(steps), cfg, engine)
    return {"steps": steps, "tasks": tasks, "paths": paths}
