"""User stories and acceptance tests for the developer who builds the automation: one story per to-be step, each
with Given / When / Then criteria taken from the business rules (mappings, calculations, validations), plus
duplicates, exception review and the run log. Also written as a Gherkin .feature file that test tools can run."""
from __future__ import annotations

from .tobe import AUTOMATION, PERSON

ROLE = "the person who does this work today"


def user_stories(task_name: str, to_be: dict, rules: list[dict], minutes_after: float | None = None) -> list[dict]:
    stories: list[dict] = []

    def story(title, want, benefit, criteria, actor=ROLE):
        stories.append({"id": f"US-{len(stories) + 1}", "title": title,
                        "story": f"As {actor}, I want {want}, so that {benefit}.", "criteria": criteria})

    mappings = [r for r in rules if r["type"] == "mapping"]
    calculations = [r for r in rules if r["type"] == "calculation"]
    validations = [r for r in rules if r["type"] == "validation"]
    unknown = [r for r in rules if r["type"] == "unknown"]

    for step in to_be["steps"]:
        what = step["what"].rstrip(".")
        low = what[:1].lower() + what[1:]
        if step["actor"] == AUTOMATION and what.startswith(("When ", "Start ")):
            story(f"Start by itself: {step['system'] or 'trigger'}", f"the automation to start by itself ({low})",
                  "nobody has to remember to run it",
                  [_c("a new item is ready", "the trigger fires", "the automation starts and picks up the item")])
        elif step["actor"] == AUTOMATION and what.startswith("Read "):
            story(f"Read the source: {step['system']}", f"the automation to {low}",
                  "nobody has to open each item by hand",
                  [_c("a new item arrives", "the automation runs", f"it does this: {low}")])
        elif step["actor"] == AUTOMATION and what.startswith("Apply the rules"):
            story("Apply the business rules", "the automation to work out derived values with the agreed rules",
                  "the values are calculated the same way every time",
                  [_c("an item with the values the rule needs", "the automation runs",
                      f"{r['field'] or 'the value'} follows {r['id']}: {r['rule']}") for r in calculations])
        elif step["actor"] == AUTOMATION and what.startswith("Check every item"):
            criteria = [_c(f"an item that breaks {r['id']} ({r['field']})", "the automation checks it",
                           "nothing is written and the item goes to the review queue with the reason")
                        for r in validations]
            criteria.append(_c("an item that is already recorded", "it arrives again",
                               "it is not written a second time and goes to review as a possible duplicate"))
            story("Check before writing", "every item checked before it is written",
                  "mistakes are caught before they reach the records", criteria)
        elif step["actor"] == PERSON and "Review" in what:
            story("Review only the exceptions", "to see only the items that fail a check, with the reason",
                  "I review exceptions instead of every item",
                  [_c("an item fails a check", "I open the review queue", "I see the item, the failed rule and its "
                      "source, and can fix and approve it or reject it"),
                   _c("I approve a fixed item", "the automation continues", "it is written like any other item")])
        elif step["actor"] == PERSON:
            story("Enter values without a known source", "to enter only the values whose source isn't recorded",
                  "the rest is automated while those rules are settled",
                  [_c(f"the source of {r['field']} is not yet known", "an item is processed",
                      "the item waits for a person to enter that value") for r in unknown])
        elif step["actor"] == AUTOMATION and step["system"] and not what.startswith("Log each run"):
            targets = [r for r in mappings if r["field"].startswith(step["system"])]
            criteria = [_c("an item passed its checks", "the automation writes it",
                           f"{r['field']} = {r['rule'].split(' = ', 1)[-1]} ({r['id']})") for r in targets]
            criteria = criteria or [_c("an item passed its checks", "the automation runs", f"it does this: {low}")]
            story(f"Write to {step['system']}", f"the automation to {low}", "nobody re-keys these values", criteria)
    done = _c("a normal item", "the automation processes it", "it needs no hands-on work"
              + (f" and the remaining manual part takes under {minutes_after:g} min" if minutes_after else ""))
    story("Log every run", "every run logged with time, result and exceptions",
          "I can see the time saved and the error rate, and prove the benefit",
          [done, _c("a run finishes", "I open the KPI report", "I see items processed, exceptions, and minutes "
                    "per item against the old process")], actor="the process owner")
    return stories


def _c(given: str, when: str, then: str) -> dict:
    return {"given": given, "when": when, "then": then}


def gherkin(task_name: str, stories: list[dict]) -> str:
    lines = [f"Feature: {task_name}", "  Generated by FlowSME from the recording. Replace the general wording with "
             "real example values before running.", ""]
    for s in stories:
        lines.append(f"  # {s['id']}: {s['story']}")
        for n, c in enumerate(s["criteria"], 1):
            lines += [f"  Scenario: {s['id']}.{n} {s['title']}", f"    Given {c['given']}", f"    When {c['when']}",
                      f"    Then {c['then']}", ""]
    return "\n".join(lines)


def stories_markdown(task_name: str, stories: list[dict]) -> str:
    lines = [f"# User stories: {task_name}", "",
             "Generated from the recording's to-be process and business rules. Each criterion is a test to pass "
             "before go-live (user acceptance testing).", ""]
    for s in stories:
        lines += [f"## {s['id']} {s['title']}", "", s["story"], "", "| Given | When | Then |", "|---|---|---|"]
        lines += [f"| {c['given']} | {c['when']} | {c['then']} |" for c in s["criteria"]] + [""]
    return "\n".join(lines)
