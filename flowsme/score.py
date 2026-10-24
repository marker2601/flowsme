"""Score each task's automation potential and estimate the time it could save."""
from __future__ import annotations

from .config import AnalysisConfig
from .models import Task
from .workflow import handoffs

# Share of a task's time assumed automatable when the AI model gave no estimate of its own.
LEVEL_FRACTION = {"High": 0.6, "Medium": 0.35, "Low": 0.1}


def task_features(task: Task) -> dict:
    steps = task.steps
    return {
        "steps": len(steps),
        "steps_per_run": len(task.representative),
        "runs": task.runs,
        "minutes": round(task.minutes, 1),
        "minutes_per_run": round(task.minutes / task.runs, 1),
        "pastes": sum(s.pastes for s in steps),
        "cross_app_transfers": sum(handoffs(steps).values()),
        "chars_typed": sum(s.chars_typed for s in steps),
        "app_switches": sum(1 for a, b in zip(steps, steps[1:]) if a.app != b.app),
        "apps": len(task.apps),
    }


def score_task(f: dict) -> tuple[int, str, list[str]]:
    """0-100 signal score from repetition, copy-paste, typing, window switching and time spent."""
    n = max(f["steps"], 1)
    repetition = 35 * min(f["runs"] - 1, 9) / 9
    transfer = 25 * min(f["pastes"] / n / 0.2, 1) * min(f["pastes"] / 3, 1)
    typing = 15 * min(f["chars_typed"] / n / 15, 1) * min(f["chars_typed"] / 100, 1)
    switching = 15 * min(f["app_switches"] / n / 0.35, 1)
    volume = 10 * min(f["minutes"] / 30, 1)
    total = round(repetition + transfer + typing + switching + volume)
    level = "High" if total >= 55 else "Medium" if total >= 30 else "Low"

    reasons = []
    if f["runs"] >= 3:
        reasons.append(f"The same {f['steps_per_run']}-step routine ran {f['runs']} times")
    if f["cross_app_transfers"]:
        reasons.append(f"{f['cross_app_transfers']} values copied by hand from one application to another")
    elif f["pastes"] >= 3:
        reasons.append(f"{f['pastes']} copy-paste actions")
    if f["chars_typed"] >= 100:
        reasons.append(f"{f['chars_typed']:,} characters typed by hand")
    if f["app_switches"] >= 5:
        reasons.append(f"{f['app_switches']} switches between applications")
    if f["minutes"] >= 10:
        reasons.append(f"{f['minutes']:.0f} minutes of the recording")
    return total, level, reasons


def apply_estimates(tasks: list[Task], cfg: AnalysisConfig) -> None:
    for task in tasks:
        tools = (task.improvement or {}).get("tools")
        if tools and tools["minutes_per_run"]:
            # Stage-by-stage plan from the recording beats a single guess: use it when there is one.
            fraction = 1 - tools["with_suggestions"] / tools["minutes_per_run"]
        else:
            fraction = _as_fraction((task.recommendation or {}).get("automatable_fraction"))
        if fraction is None:
            fraction = LEVEL_FRACTION.get(task.level, 0.1)
        if task.misc:
            fraction = 0.0
        saved = task.minutes * fraction
        hours = saved * cfg.runs_per_month / 60
        task.estimate = {
            "automatable_fraction": round(fraction, 2),
            "minutes_saved_per_run": round(saved, 1),
            "hours_saved_per_month": round(hours, 1),
            "cost_saved_per_month": round(hours * cfg.hourly_cost),
        }


def _as_fraction(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value > 1:  # the model answered in percent
        value /= 100
    return value if 0 <= value <= 1 else None
