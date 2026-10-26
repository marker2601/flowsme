"""Command line: python -m flowsme {app, record, analyze, build, sessions, demo}."""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .models import fmt_minutes
from .session import list_sessions, load_meta, new_session_dir, resolve_session


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="flowsme", description="Record desktop work, map the workflow, "
                                     "and find what can be automated.")
    parser.add_argument("--config", help="path to config.toml (default: ./config.toml if it exists)")
    sub = parser.add_subparsers(dest="command", required=True)

    app = sub.add_parser("app", help="open the FlowSME window: Record, Pause, Stop and Report (Windows)")
    app.add_argument("--self-test", action="store_true", help="check that recording and reports work, then exit")

    rec = sub.add_parser("record", help="record your screen activity (Windows)")
    rec.add_argument("--name", help="short label for the session, e.g. 'invoice processing'")
    rec.add_argument("--duration", type=float, help="stop automatically after this many seconds")
    rec.add_argument("--narrate", action="store_true",
                     help="also record the microphone: explain what you do and why while you work")

    ana = sub.add_parser("analyze", help="analyse a recorded session and write the report")
    ana.add_argument("session", nargs="?", default="latest", help="session id or folder (default: latest)")
    _analysis_flags(ana)

    bld = sub.add_parser("build", help="turn an analysed task into an n8n workflow, Postman collection, SQL and "
                                       "Playwright starters")
    bld.add_argument("session", nargs="?", default="latest", help="session id or folder (default: latest)")
    bld.add_argument("--task", type=int, help="task number from the report (default: the top opportunity)")
    bld.add_argument("--deploy-n8n", action="store_true",
                     help="also create the workflow in n8n (needs N8N_URL and N8N_API_KEY)")

    sub.add_parser("sessions", help="list recorded sessions")

    demo = sub.add_parser("demo", help="generate a synthetic invoice-processing session and analyse it")
    _analysis_flags(demo)

    args = parser.parse_args(argv)
    if args.command == "app":
        from .app import main as app_main

        app_main(args.config, check=args.self_test)  # its own settings: %LOCALAPPDATA%\FlowSME\config.toml unless --config is given
        return
    cfg = load_config(args.config)
    if getattr(args, "offline", False):
        cfg.llm.provider = "offline"
    if getattr(args, "no_search", False):
        cfg.analysis.web_search = False
    if getattr(args, "runs_per_month", None):
        cfg.analysis.runs_per_month = args.runs_per_month
    if getattr(args, "hourly_cost", None):
        cfg.analysis.hourly_cost = args.hourly_cost
    if getattr(args, "tools", None):
        sep = ";" if ";" in args.tools else ","
        cfg.customer.tools = [t.strip() for t in args.tools.split(sep) if t.strip()]

    if args.command == "record":
        from .recorder import Recorder

        if args.narrate:
            cfg.recorder.narrate = True
        path = Recorder(cfg, new_session_dir(cfg.sessions_dir, args.name)).run(duration=args.duration)
        print(f"Analyse it with:  python -m flowsme analyze {path.name}")
    elif args.command == "sessions":
        _list(cfg)
    elif args.command == "build":
        from .build import build_task

        path = resolve_session(args.session, cfg.sessions_dir)
        print(f"Building task {args.task or '(top opportunity)'} from {path}")
        build_task(path, args.task, deploy=args.deploy_n8n)
        print("Read BUILD.md in that folder first: it lists what must be decided and filled in.")
    elif args.command == "analyze":
        _analyze(resolve_session(args.session, cfg.sessions_dir), cfg)
    elif args.command == "demo":
        from .demo import build_demo

        path = build_demo(cfg.sessions_dir)
        print(f"Demo session written to {path}")
        _analyze(path, cfg)


def _analysis_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--offline", action="store_true", help="don't call the AI model; use heuristics only")
    p.add_argument("--no-search", action="store_true", help="don't search the web for tools")
    p.add_argument("--runs-per-month", type=int, help="how often this process runs (for monthly savings)")
    p.add_argument("--hourly-cost", type=float, help="cost of an hour of this person's time")
    p.add_argument("--tools", help="tools the customer already has, e.g. \"Microsoft 365, Power BI\" "
                                   "(use ; to separate when describing one: \"n8n; MyERP: database, reporting\")")


def _list(cfg) -> None:
    sessions = list_sessions(cfg.sessions_dir)
    if not sessions:
        print("No sessions yet. Record one with: python -m flowsme record")
        return
    for path in sessions:
        meta = load_meta(path)
        secs = meta.get("duration_seconds")
        done = "analysed" if (path / "analysis" / "report.html").exists() else ""
        print(f"{path.name:40} {(meta.get('started') or '')[:16]:17} {fmt_minutes(secs / 60) if secs else '?':>8}  {done}")


def _analyze(path, cfg) -> None:
    from .analyze import analyze_session

    print(f"Analysing {path}")
    result = analyze_session(path, cfg)
    ranked = sorted((t for t in result["tasks"] if not t.misc), key=lambda t: -t.score)
    print("\nTop opportunities:")
    for t in ranked[:5]:
        print(f"  {t.id}. {t.name}: {t.level} ({t.score:.0f}/100), "
              f"about {fmt_minutes(t.estimate['minutes_saved_per_run'])} saved per run")
    print(f"\nReport: {result['paths']['html'].resolve()}")


if __name__ == "__main__":
    main()
