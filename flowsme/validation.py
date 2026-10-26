"""Validation checklist and error exposure for a task.

Checks come from three places, each with its evidence:
  - the data flow: every value moved by hand is a chance for an error (an "opportunity" in Six Sigma terms)
  - the kind of field: IDs must be unique, amounts must match, dates must be valid, names must exist
  - what went wrong in the recording: retries (Submit clicked twice) and runs that needed extra steps
"""
from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

from .integration import label, source_of
from .models import Task

FIELD_RULES = [
    (r"invoice|number|\bid\b|reference|\bref\b|#", "unique",
     "Must be unique: check it is not already recorded before writing it (stops duplicates, e.g. paying twice)."),
    (r"amount|total|price|cost|sum|value|due$", "amount",
     "Must be a number in the expected currency, above zero, and equal to the source document. "
     "Flag anything above the approval limit for a person."),
    (r"date|deadline|period", "date",
     "Must be a valid date in one agreed format and a sensible range (not in the past, not years ahead)."),
    (r"vendor|supplier|customer|client|company|payee", "master_data",
     "Must exist in the master list (e.g. vendor master): pick from a list instead of typing."),
    (r"e-?mail|recipient|\bto\b", "recipient",
     "Must be a valid address from the approved recipients."),
    (r"\bgl\b|account|cost cent|code", "code_list",
     "Must exist in the code list (e.g. chart of accounts): use a drop-down."),
]


def _rules_for(*names: str) -> list[tuple[str, str]]:
    found = []
    for pattern, kind, text in FIELD_RULES:
        if any(n and re.search(pattern, n, re.I) for n in names) and kind not in [k for k, _ in found]:
            found.append((kind, text))
    return found


def _rework(task: Task, flows: list[dict], context: list | None = None) -> list[dict]:
    """Retries and extra steps seen in the recording."""
    seen = []
    runs = task.iterations or [task.steps]
    rep_shape = [s.signature for s in task.representative]  # where + what, not the per-item data
    for run_no, run in enumerate(runs, 1):
        for a, b in zip(run, run[1:]):
            if a.action == b.action == "click" and (a.app, a.target) == (b.app, b.target) and a.target and not b.double:
                seen.append({"run": run_no, "kind": "retry",
                             "text": f"'{a.target}' in {a.app} was clicked twice in a row (run {run_no})."})
        if len(runs) > 1 and run is not task.representative:
            shape = [s.signature for s in run]
            extra = [run[j] for op, _, _, j1, j2 in SequenceMatcher(None, rep_shape, shape).get_opcodes()
                     if op in ("insert", "replace") for j in range(j1, j2)]
            extra = [s for s in extra if not any(r["kind"] == "retry" and r["run"] == run_no and s.target in r["text"]
                                                 for r in seen)]
            if extra:
                seen.append({"run": run_no, "kind": "extra_steps",
                             "text": f"Run {run_no} needed {len(extra)} extra step(s): "
                                     + "; ".join(s.description for s in extra[:3]) + "."})
    # A value whose fingerprint usually matches its source but not in some run: likely the wrong item.
    for row in flows:
        for run_no in row.get("mismatched_runs") or []:
            seen.append({"run": run_no, "kind": "mismatch",
                         "text": f"In run {run_no}, {label(row['to'])} did not match {label(row['confirmed_source'])} "
                                 "for that item (the fingerprints differ): a likely wrong-item or duplicate entry."})
    # The same cell or field written more than once: something had to be corrected.
    apps, writes = set(task.apps), defaultdict(list)
    for s in context or task.steps:
        if s.app in apps and s.target and not s.excluded and (s.pastes or s.chars_typed or "ctrl+x" in s.shortcuts):
            writes[(s.app, s.title, s.target)].append(s)
    for (app, _, target), hits in writes.items():
        if len(hits) > 1 and not re.fullmatch(r"(text editor|document|edit cell)", target, re.I):
            seen.append({"run": None, "kind": "overwrite",
                         "text": f"'{target}' in {app} was written {len(hits)} times: a value had to be corrected."})
    return seen


def checklist(task: Task, plan: dict, context: list | None = None) -> dict:
    checks = []
    flows = plan["data_flows"]
    for row in flows:
        target = label(row["to"])
        src, how = source_of(row)
        names = (row["to"]["field"], src["field"] if src else "")
        at = ", ".join(map(str, sorted(set(row["steps"]))))
        if how in ("copied", "same value"):
            evidence = (f"copied by hand at step {at}" if how == "copied" else
                        f"typed by hand at step {at}; the fingerprints show it is the same value as {label(src)} "
                        f"({row['value_match']})")
            checks.append({"where": target, "type": "prevent", "method": "poka_yoke",
                           "check": f"Pass the value from {label(src)} directly instead of "
                                    f"{'re-keying' if how == 'copied' else 're-typing'} it; after writing, compare "
                                    "the two automatically.", "evidence": evidence})
        else:
            checks.append({"where": target, "type": "prevent", "method": "poka_yoke",
                           "check": "Replace free typing with a rule, a lookup or a drop-down"
                                    + (f" (probably the same as {label(src)}: confirm)" if src else "") + ".",
                           "evidence": f"typed by hand ({row['chars']} characters) at step {at}"})
        for kind, text in _rules_for(*names):
            checks.append({"where": target, "type": "detect", "method": "validation_rule", "check": text,
                           "evidence": f"field '{row['to']['field']}'" + (f" holds '{src['field']}'" if src else "")})
    rework = _rework(task, flows, context)
    for item in [r for r in rework if r["kind"] == "retry"]:
        checks.append({"where": item["text"].split(" was ")[0], "type": "prevent", "method": "defects",
                       "check": "Capture the confirmation or reference number after submitting, and check it "
                                "before trying again, so a slow page never creates a duplicate.",
                       "evidence": item["text"]})
    extra = [r for r in rework if r["kind"] == "extra_steps"]
    if extra:
        checks.append({"where": "exceptions", "type": "detect", "method": "defects",
                       "check": "Route items that need extra handling (unclear scans, missing data) to a person "
                                "instead of forcing them through the automation.",
                       "evidence": " ".join(r["text"] for r in extra)})
    for item in [r for r in rework if r["kind"] == "mismatch"]:
        checks.append({"where": item["text"].split(", ")[1].split(" did not")[0], "type": "detect", "method": "defects",
                       "check": "Before saving, check that the fields of one row belong to the same item (e.g. "
                                "invoice number and reference); stop and ask a person if they don't.",
                       "evidence": item["text"]})
    fixes = [r for r in rework if r["kind"] == "overwrite"]
    if fixes:
        checks.append({"where": "corrections", "type": "detect", "method": "defects",
                       "check": "Values had to be corrected after entry. Check each value against its source before "
                                "writing, and log corrections so their causes can be found (5 Whys).",
                       "evidence": " ".join(r["text"] for r in fixes[:5])})
    checks += [
        {"where": "every run", "type": "control", "method": "control_plan",
         "check": "Reconcile counts: items received = rows written = records submitted. Investigate any gap.",
         "evidence": "standard control for multi-system hand-offs"},
        {"where": "every run", "type": "control", "method": "control_plan",
         "check": "Log each item with its result (done / exception / failed) and review exceptions daily.",
         "evidence": "needed to measure error rate after go-live"},
    ]
    manual_before = len(flows)
    manual_after = sum(1 for r in flows if source_of(r)[1] == "unknown")
    return {"checks": checks, "rework": rework, "manual_entries_per_item": manual_before,
            "manual_entries_after": manual_after}
