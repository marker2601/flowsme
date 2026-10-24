"""Turn raw recorder events into steps, and steps into tasks (including repeated routines)."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .models import Step, Task, context_label

FOLLOW_WINDOW = 6.0   # seconds a click keeps "owning" the typing/shortcuts that follow it
DOUBLE_CLICK = 0.6
MENU_WINDOW = 15.0    # seconds between clicking a field and choosing Copy/Paste from a menu
CLIPBOARD_WINDOW = 5.0
MENU_VERB = re.compile(r"^(copy|cut|paste|keep text only|keep source formatting|merge formatting)\b", re.I)
MENU_ROLES = {"MenuItemControl", "ButtonControl", "SplitButtonControl"}  # context menus and ribbons
MAX_TASK_STEPS = 40
STEP_EVENTS = ("click", "type", "shortcut", "key", "scroll")


def build_steps(events: list[dict], idle_gap: float) -> list[Step]:
    steps: list[Step] = []
    current: Step | None = None
    last_field: Step | None = None  # the last thing clicked: what a menu or ribbon Copy/Paste acts on
    for ev in events:
        kind = ev.get("type")
        window = (ev.get("app", ""), ev.get("title", ""))
        if kind == "clipboard":
            # The clipboard changed, so the step that just happened copied something, even through a button
            # the recorder can't name. Only a fingerprint of the content is recorded.
            host = current or last_field
            if host and ev["t"] - host.end <= CLIPBOARD_WINDOW:
                if not host.copies:
                    host.shortcuts.append("clipboard")
                host.copy_hash = ev.get("hash") or host.copy_hash
            continue
        if kind not in STEP_EVENTS:
            # focus / pause / resume: moving to another window ends the current step
            if current and window != (current.exe, current.title):
                current = None
            continue
        same_window = current is not None and window == (current.exe, current.title)
        if kind == "click":
            element = ev.get("element") or {}
            target = element.get("name", "")
            verb = MENU_VERB.match(target) if element.get("role") in MENU_ROLES else None
            if verb and last_field and ev["t"] - last_field.end <= MENU_WINDOW:
                # Copy or Paste chosen from a right-click menu or the ribbon acts on the field clicked before.
                word = verb.group(1).lower()
                last_field.shortcuts.append("menu:" + ("paste" if word.startswith(("paste", "keep", "merge"))
                                                       else word))
                last_field.end = ev["t"]
                current = last_field
                continue
            if (same_window and current.action == "click" and ev["t"] - current.end <= DOUBLE_CLICK
                    and target == current.target):
                current.double = True
                current.end = ev["t"]
                continue
            current = _new_step(len(steps), ev)
            steps.append(current)
            last_field = current
        elif same_window and ev["t"] - current.end <= FOLLOW_WINDOW:
            _merge(current, ev)
        else:
            current = _new_step(len(steps), ev)
            steps.append(current)

    for i, step in enumerate(steps):
        nxt = steps[i + 1].start if i + 1 < len(steps) else None
        own = max(step.end - step.start, 0.0)
        # Time until the next action counts toward this step (reading, checking, thinking)...
        step.duration = nxt - step.start if nxt is not None and nxt - step.start <= idle_gap else own + 3.0
        step.duration = max(step.duration, 0.5)  # ...unless the user went idle.
        step.signature = _signature(step)
    return steps


def _new_step(index: int, ev: dict) -> Step:
    exe, title = ev.get("app", ""), ev.get("title", "")
    element = ev.get("element") or {}
    step = Step(
        index=index, start=ev["t"], end=ev["t"], app=context_label(exe, title), exe=exe, title=title,
        action=ev["type"], target=element.get("name", "") if ev["type"] == "click" else ev.get("keys", ""),
        role=element.get("role", ""), excluded=bool(ev.get("excluded")), right_click=ev.get("button") == "right",
    )
    _merge(step, ev)
    return step


def _merge(step: Step, ev: dict) -> None:
    kind = ev["type"]
    if kind == "type":
        step.chars_typed += ev.get("chars", 0)
        step.text += ev.get("text", "")
        if ev.get("value_hash"):
            step.typed_hashes.append(ev["value_hash"])
    elif kind == "shortcut":
        step.shortcuts.append(ev["keys"])
    elif kind == "key":
        step.keys.append(ev["keys"])
    elif kind == "scroll":
        step.scrolls += ev.get("count", 1)
    step.end = max(step.end, ev.get("t_end", ev["t"]))
    if not step.screenshot and ev.get("shot"):
        step.screenshot, step.shot_xy = ev["shot"], ev.get("shot_xy")


def _signature(step: Step) -> str:
    """Coarse fingerprint used to spot repeated routines: where + what, not the exact data."""
    sig = f"{step.app}|{step.action}"
    if step.copies:
        sig += "+copy"
    if step.pastes:
        sig += "+paste"
    if step.chars_typed and step.action != "type":
        sig += "+type"
    return sig


# ---- tasks ---------------------------------------------------------------------------------------

def build_tasks(steps: list[Step], idle_gap: float) -> list[Task]:
    tasks: list[Task] = []
    for block in _split_idle(steps, idle_gap):
        tasks.extend(_tasks_in_block(block))
    tasks.sort(key=lambda t: t.start)
    main, small = [], []
    for task in tasks:
        (main if task.repeated or len(task.steps) > 2 or task.minutes >= 1 else small).append(task)
    if main and small:  # fold tiny interruptions into one bucket so they don't clutter the map
        tasks = main + [Task(id=0, steps=sorted((s for t in small for s in t.steps), key=lambda s: s.index),
                             misc=True)]
    for i, task in enumerate(tasks, 1):
        task.id = i
    return tasks


def _split_idle(steps: list[Step], idle_gap: float):
    block = [steps[0]]
    for prev, step in zip(steps, steps[1:]):
        if step.start - prev.end > idle_gap:
            yield block
            block = []
        block.append(step)
    yield block


def _tasks_in_block(block: list[Step]) -> list[Task]:
    sigs: list[str | None] = [s.signature for s in block]
    covered = [False] * len(block)
    tasks: list[Task] = []
    for _ in range(3):  # up to three different routines per block
        found = _best_exact_pattern(sigs)
        if not found:
            break
        length, starts = found
        pattern = sigs[starts[0]:starts[0] + length]
        spans = _absorb_gaps(sigs, _add_variants(sigs, pattern, starts), length)
        iterations = [block[a:b] for a, b in spans]
        tasks.append(Task(id=0, steps=[s for it in iterations for s in it], iterations=iterations))
        for a, b in spans:
            for k in range(a, b):
                sigs[k] = None
                covered[k] = True
    run: list[Step] = []
    for k, step in enumerate(block):
        if not covered[k]:
            run.append(step)
        elif run:
            tasks.extend(_chunk(run))
            run = []
    if run:
        tasks.extend(_chunk(run))
    return tasks


def _best_exact_pattern(sigs, min_len=4, max_len=40, min_count=3):
    """Longest-coverage sequence of step signatures that repeats at least min_count times."""
    n = len(sigs)
    best = None  # ((coverage, count), length, starts)
    for length in range(min_len, min(max_len, n // min_count) + 1):
        positions: dict[tuple, list[int]] = defaultdict(list)
        for i in range(n - length + 1):
            gram = tuple(sigs[i:i + length])
            if None not in gram:
                positions[gram].append(i)
        for gram, starts in positions.items():
            if len(starts) < min_count or len(set(gram)) < 2:
                continue
            chosen, last_end = [], -1
            for s in starts:
                if s >= last_end:
                    chosen.append(s)
                    last_end = s + length
            key = (len(chosen) * length, len(chosen))
            if len(chosen) >= min_count and (best is None or key > best[0]):
                best = (key, length, chosen)
    return (best[1], best[2]) if best else None


def _add_variants(sigs, pattern, starts, max_dist=0.3):
    """Also claim runs that differ slightly from the exact pattern (an extra click, a retry)."""
    length = len(pattern)
    spans = [(s, s + length) for s in starts]
    taken = [sig is None for sig in sigs]
    for a, b in spans:
        for k in range(a, b):
            taken[k] = True
    lo, hi = max(2, int(length * 0.7)), int(length * 1.3) + 1
    i = max(0, starts[0] - 2 * length)
    stop = min(len(sigs), starts[-1] + 3 * length)
    while i < stop:
        if taken[i]:
            i += 1
            continue
        best = None
        for width in range(1, hi + 1):
            j = i + width
            if j > len(sigs) or taken[j - 1]:
                break
            if width < lo:
                continue
            dist = _edit_distance(sigs[i:j], pattern) / length
            if best is None or dist < best[0]:
                best = (dist, j)
        if best and best[0] <= max_dist:
            spans.append((i, best[1]))
            for k in range(i, best[1]):
                taken[k] = True
            i = best[1]
        else:
            i += 1
    return sorted(spans)


def _absorb_gaps(sigs, spans, length):
    """Exact matching often finds only the stable core of a routine. Hand the short stretches between
    consecutive runs (e.g. 'file the email, open the next one') to the runs: each gap is split into the
    previous run's tail and the next run's head, rotated so the first run's head and the last run's tail
    match what was recorded before the first and after the last run."""
    limit = max(3, int(0.6 * length))
    gaps = [(b, a2) for (_, b), (a2, _) in zip(spans, spans[1:])
            if 0 < a2 - b <= limit and all(sigs[k] is not None for k in range(b, a2))]
    if not gaps:
        return spans
    typical = Counter(tuple(sigs[a:b]) for a, b in gaps).most_common(1)[0][0]
    first, last_end = spans[0][0], spans[-1][1]

    def fit(p):
        head, tail = typical[p:], typical[:p]
        head_ok = first - len(head) >= 0 and tuple(sigs[first - len(head):first]) == head
        tail_ok = tuple(sigs[last_end:last_end + len(tail)]) == tail
        return head_ok + tail_ok, head_ok, tail_ok

    p = max(range(len(typical) + 1), key=fit)
    _, head_ok, tail_ok = fit(p)

    def split(a, b):
        gap = sigs[a:b]
        return a + min(range(len(gap) + 1), key=lambda q: _edit_distance(gap[:q], typical[:p])
                       + _edit_distance(gap[q:], typical[p:]))

    cut = {gap: split(*gap) for gap in gaps}
    result = []
    for k, (a, b) in enumerate(spans):
        if k == 0:
            start = a - (len(typical) - p) if head_ok else a
        else:
            start = cut.get((spans[k - 1][1], a), a)
        if k == len(spans) - 1:
            end = b + p if tail_ok else b
        else:
            end = cut.get((b, spans[k + 1][0]), b)
        result.append((start, end))
    return result


def _edit_distance(a, b) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _chunk(run: list[Step]) -> list[Task]:
    """Split long one-off stretches into tasks, preferring to cut where the app changes."""
    chunks, cur = [], []
    for step in run:
        if cur and ((len(cur) >= MAX_TASK_STEPS and step.app != cur[-1].app)
                    or len(cur) >= MAX_TASK_STEPS * 1.5):
            chunks.append(cur)
            cur = []
        cur.append(step)
    if cur:
        chunks.append(cur)
    return [Task(id=0, steps=c) for c in chunks]
