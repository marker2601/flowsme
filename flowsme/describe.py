"""Describe each step in plain business language: a vision model when configured, metadata otherwise."""
from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageDraw

from .config import LLMConfig
from .llm import LLM
from .models import Step, Task

SYSTEM = (
    "You document desktop workflows for business process analysis. Write factual, concise descriptions "
    "in business terms. Never copy personal or confidential data (names, emails, phone or account numbers, "
    "amounts); refer to it generically, e.g. 'the vendor name' or 'the invoice total'."
)

ROLE_WORDS = {
    "ButtonControl": "button", "EditControl": "field", "DataItemControl": "cell", "ListItemControl": "item",
    "HyperlinkControl": "link", "MenuItemControl": "menu item", "TabItemControl": "tab", "TreeItemControl": "item",
    "CheckBoxControl": "checkbox", "RadioButtonControl": "option", "ComboBoxControl": "drop-down",
    "DocumentControl": "document", "TextControl": "text", "SplitButtonControl": "button",
}
SHORTCUT_WORDS = {
    "ctrl+c": "copy", "ctrl+x": "cut", "ctrl+v": "paste", "ctrl+shift+v": "paste as plain text",
    "ctrl+s": "save", "ctrl+z": "undo", "ctrl+y": "redo", "ctrl+f": "search", "ctrl+p": "print",
    "ctrl+a": "select all", "ctrl+n": "create new", "ctrl+w": "close tab", "ctrl+t": "open a new tab",
    "ctrl+l": "go to the address bar", "ctrl+enter": "send", "ctrl+end": "jump to the end",
    "ctrl+home": "jump to the start", "alt+tab": "switch window",
}


def shortcut_phrase(combo: str) -> str:
    return SHORTCUT_WORDS.get(combo, f"press {combo.title()}")


def heuristic_description(step: Step) -> str:
    if step.action == "click":
        role = ROLE_WORDS.get(step.role, "")
        what = f'"{step.target}"{" " + role if role else ""}' if step.target else "in the window"
        head = f"{'Double-click' if step.double else 'Click'} {what}"
    elif step.action == "type":
        head = f"Type {step.chars_typed} characters"
    elif step.action == "shortcut":
        head = shortcut_phrase(step.target).capitalize()
    elif step.action == "key":
        head = f"Press {step.target.title()}"
    else:
        head = "Scroll"
    extras = []
    if step.action != "type" and step.chars_typed:
        extras.append(f"type {step.chars_typed} characters")
    extras += [shortcut_phrase(s) for s in (step.shortcuts[1:] if step.action == "shortcut" else step.shortcuts)]
    extras += [f"press {k.title()}" for k in (step.keys[1:] if step.action == "key" else step.keys)]
    if step.scrolls and step.action != "scroll":
        extras.append("scroll")
    return f"{head}{', then ' + ', '.join(extras) if extras else ''} in {step.app}"


def describe_steps(steps: list[Step], tasks: list[Task], llm: LLM, cfg: LLMConfig, session_dir: Path,
                   cache_path: Path, log=print) -> int:
    """Fill step.description. Returns how many steps have a description from the vision model."""
    cache = _load_cache(cache_path, cfg.vision_model)
    for step in steps:
        step.description = cache.get(str(step.index)) or heuristic_description(step)

    # Later runs of a repeated routine reuse the first run's descriptions instead of new model calls.
    mirrors: dict[int, Step] = {}
    for task in tasks:
        if task.repeated:
            rep = task.representative
            for run in task.iterations[1:]:
                if len(run) == len(rep):
                    mirrors.update({a.index: b for a, b in zip(run, rep)})

    by_index = {s.index: s for s in steps}
    if llm.available("vision") and cfg.send_screenshots:
        todo = [s for s in steps if s.index not in mirrors and str(s.index) not in cache
                and s.screenshot and not s.excluded][:cfg.max_vision_steps]
        groups = _group(todo, max(1, cfg.screenshots_per_call))
        if todo:
            log(f"Describing {len(todo)} steps with {cfg.vision_model}: {len(groups)} requests, "
                f"{max(1, cfg.concurrency)} at a time (other steps reuse earlier runs or the cache)...")
        state = {"group_failures": 0}
        done = described = 0
        with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
            futures = [pool.submit(_describe_group, g, by_index, llm, cfg, session_dir, state) for g in groups]
            for future in as_completed(futures):
                for step, text in future.result():
                    if text:
                        step.description = text
                        cache[str(step.index)] = text
                        described += 1
                done += 1
                if done % 10 == 0:
                    _save_cache(cache_path, cfg.vision_model, cache)
                    log(f"  {done}/{len(groups)} requests done")
        _save_cache(cache_path, cfg.vision_model, cache)
        if todo:
            log(f"  {described}/{len(todo)} steps described from screenshots")

    for index, rep in mirrors.items():
        if str(rep.index) in cache:
            by_index[index].description = rep.description
    return len(cache)


INSTRUCTION = (
    "say what the user is doing in business terms in ONE sentence of at most 20 words, e.g. 'Copies the invoice "
    "number from the PDF into the tracker'. Only state what the screenshots and details show: if you cannot see "
    "what was typed or who it is for, say it generically (e.g. 'Enters a value in column D of the tracker') "
    "instead of guessing."
)


def _group(todo: list[Step], size: int) -> list[list[Step]]:
    """Consecutive steps go together, so the model sees what happened before and after each one."""
    groups: list[list[Step]] = []
    for step in todo:
        if groups and len(groups[-1]) < size and step.index == groups[-1][-1].index + 1:
            groups[-1].append(step)
        else:
            groups.append([step])
    return groups


def _describe_group(group: list[Step], by_index: dict, llm: LLM, cfg: LLMConfig, session_dir: Path,
                    state: dict) -> list[tuple[Step, str | None]]:
    """Runs in a worker thread. One request for the whole group; one per step if the model can't do that."""
    if len(group) > 1 and state["group_failures"] < 2:
        images = [_encode_screenshot(session_dir, s, cfg.vision_image_width) for s in group]
        after = by_index.get(group[-1].index + 1)
        if after and after.screenshot and not after.excluded:
            images.append(_encode_screenshot(session_dir, after, cfg.vision_image_width))
        if all(images):
            n = len(group)
            lines = [f"Step {i} (image {i}): {_facts(s)}" for i, s in enumerate(group, 1)]
            if len(images) > n:
                lines.append(f"Image {n + 1}: the screen right after step {n}, to show the result.")
            prompt = (f"These screenshots show {n} consecutive steps of a recorded desktop workflow, in order, each "
                      "taken at the moment of the action (a red circle marks the click).\n\n" + "\n".join(lines) +
                      f"\n\nFor each step, {INSTRUCTION} Use the neighbouring screenshots to work out what was typed "
                      f'or changed.\n\nReturn JSON: {{"steps": [one sentence per step, exactly {n} sentences]}}')
            data = llm.chat_json(prompt, role="vision", system=SYSTEM, images=images, max_tokens=60 * n + 60)
            texts = data.get("steps") if isinstance(data, dict) else None
            if isinstance(texts, list) and len(texts) == n and all(isinstance(t, str) and t.strip() for t in texts):
                state["group_failures"] = 0
                return [(s, _one_sentence(t)) for s, t in zip(group, texts)]
            state["group_failures"] += 1  # after two misses in a row, stop trying several images per request
    return [(s, _describe_one(s, by_index, llm, cfg, session_dir)) for s in group]


def _describe_one(step: Step, by_index: dict, llm: LLM, cfg: LLMConfig, session_dir: Path) -> str | None:
    image = _encode_screenshot(session_dir, step, cfg.vision_image_width)
    if image is None:
        return None
    lines = [_facts(step)]
    if step.shot_xy and step.action == "click":
        lines.append("The red circle on the screenshot marks where the user clicked.")
    prev = by_index.get(step.index - 1)
    if prev:
        lines.append(f"Previous step: {heuristic_description(prev)}")
    lines.append(f"Looking at the screenshot, {INSTRUCTION} Reply with the sentence only.")
    text = llm.chat("\n".join(lines), role="vision", system=SYSTEM, images=[image], max_tokens=80)
    return _one_sentence(text) if text else None


def _facts(step: Step) -> str:
    facts = f'{step.app}, window "{step.title}". Recorded action: {heuristic_description(step)}'
    if step.target:
        facts += f' (UI element "{step.target}", {step.role or "unknown type"})'
    return facts


def _encode_screenshot(session_dir: Path, step: Step, max_width: int = 1024) -> str | None:
    path = session_dir / step.screenshot
    if not path.exists():
        return None
    with Image.open(path) as im:
        img = im.convert("RGB")
    if step.shot_xy and step.action == "click":
        x, y = step.shot_xy
        r = max(12, img.width // 60)
        ImageDraw.Draw(img).ellipse([x - r, y - r, x + r, y + r], outline=(255, 0, 0), width=4)
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _one_sentence(text: str) -> str:
    line = text.strip().splitlines()[0].strip().strip('"').strip()
    return line[:200]


def _load_cache(path: Path, model: str) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("steps", {}) if data.get("model") == model else {}


def _save_cache(path: Path, model: str, cache: dict) -> None:
    path.write_text(json.dumps({"model": model, "steps": cache}, indent=1, ensure_ascii=False), encoding="utf-8")
