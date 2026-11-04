"""Turn an ordinary screen video (Teams, Loom, Snipping Tool, OBS...) into a recording the analysis can read.

A video has no clicks or field names, so the AI model reads them from the picture: FlowSME keeps a frame each
time the screen changes, asks the model what the person did between one frame and the next (which application,
which button or field, typed, copied or pasted), and writes the same events FlowSME's recorder writes. The
soundtrack becomes the narration. Timings are therefore approximate, and copying between applications is
inferred from what was visible, not proven by value fingerprints as with the recorder."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops

from .config import Config
from .describe import _encode_screenshot
from .llm import LLM
from .models import APP_NAMES, Step
from .schemas import VIDEO_ACTIONS
from .segment import build_steps
from .session import new_session_dir

VIDEO_TYPES = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".m4v")
MAX_MINUTES = 60
MAX_FRAMES = 240         # frames sent to the model at most; the change threshold rises until it fits
CHANGE = 0.0004          # share of pixels (of a 320x180 grey copy) that must change: one Excel cell filled in is
                         # about 0.001, a moving mouse pointer or blinking cursor stays below
# The title bar names the application more reliably than the model's guess (Notepad's tabs look like an editor).
TITLE_APPS = ((" - excel", "excel.exe"), (" - word", "winword.exe"), (" - powerpoint", "powerpnt.exe"),
              (" - notepad", "notepad.exe"), (" - outlook", "outlook.exe"), ("outlook", "olk.exe"),
              (" - google chrome", "chrome.exe"), (" - microsoft edge", "msedge.exe"), ("edge", "msedge.exe"),
              (" - mozilla firefox", "firefox.exe"), (" - visual studio code", "code.exe"),
              (" - adobe acrobat", "acrord32.exe"), ("acrobat reader", "acrord32.exe"), ("file explorer", "explorer.exe"),
              ("microsoft teams", "ms-teams.exe"), (" - onenote", "onenote.exe"), ("notepad", "notepad.exe"))
EXES = {name.lower(): exe for exe, name in APP_NAMES.items()}
KINDS = {"button": "ButtonControl", "field": "EditControl", "cell": "DataItemControl", "tab": "TabItemControl",
         "link": "HyperlinkControl", "menu item": "MenuItemControl", "list item": "ListItemControl",
         "document": "DocumentControl", "checkbox": "CheckBoxControl", "drop-down": "ComboBoxControl"}
SYSTEM = ("You watch screen recordings of office work and say exactly what the person did, in the order they did "
          "it. Only report what the screenshots show. Never copy personal or confidential data (names, emails, "
          "account numbers, amounts) into your answer: describe it generically, e.g. 'the invoice number'.")
NO_NARRATION = 0.3
VALUE_LIKE = re.compile(r"[\d\s.,:/()$€£¥%+-]{2,}")  # amounts, dates, phone numbers: data, not field names


def ffmpeg() -> str:
    found = os.environ.get("FLOWSME_FFMPEG") or shutil.which("ffmpeg")
    if not found:
        try:
            import imageio_ffmpeg

            found = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass
    if not found:
        raise SystemExit("Reading videos needs ffmpeg: install it (winget install ffmpeg, or apt install ffmpeg).")
    return found


FFMPEG_TIMEOUT = 30 * 60  # seconds for one ffmpeg run; a broken or endless file must not block the server

def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                              errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                              timeout=FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SystemExit("Reading the video took too long. Try a shorter video (under an hour).") from None


def probe(video: Path) -> tuple[float, bool]:
    """(seconds, has sound) from ffmpeg's description of the file."""
    info = _run(["-i", str(video)]).stderr
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not match:
        raise SystemExit("That file isn't a video FlowSME can read.")
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s), bool(re.search(r"Stream #.*Audio:", info))


def key_frames(video: Path, seconds: float, out: Path, log=print) -> list[tuple[float, Path]]:
    """A frame at every visible change: (time in seconds, image), each about 1280 pixels wide."""
    fps = 2 if seconds <= 180 else 1 if seconds <= 1200 else 0.5
    raw = out / "raw"
    raw.mkdir(parents=True)
    done = _run(["-i", str(video), "-vf", f"fps={fps},scale='min(1280,iw)':-2", "-q:v", "4",
                 str(raw / "%06d.jpg")])
    frames = sorted(raw.glob("*.jpg"))
    if not frames:
        raise SystemExit(f"ffmpeg couldn't read frames from the video: {done.stderr[-200:]}")
    small = []
    for path in frames:
        with Image.open(path) as im:
            small.append(im.convert("L").resize((320, 180)))

    def changed(a: Image.Image, b: Image.Image) -> float:
        histogram = ImageChops.difference(a, b).histogram()
        return sum(histogram[25:]) / (320 * 180)

    threshold, kept = CHANGE, []
    while True:
        kept, last = [0], small[0]
        for i in range(1, len(small)):
            if changed(small[i], last) > threshold:
                kept.append(i)
                last = small[i]
        if len(kept) <= MAX_FRAMES:
            break
        threshold *= 1.5
    log(f"Video: {seconds / 60:.1f} min, {len(frames)} frames looked at, {len(kept)} where the screen changed")
    return [(i / fps, frames[i]) for i in kept]


def _read_batch(llm: LLM, cfg: Config, batch: list[tuple[float, Path]], first: int) -> list[dict]:
    """What happened between each pair of consecutive frames in the batch."""
    images, lines = [], []
    for n, (t, path) in enumerate(batch, 1):
        step = Step(index=0, start=t, end=t, app="", exe="", title="", action="", screenshot=str(path))
        images.append(_encode_screenshot(Path("/"), step, cfg.llm.vision_image_width, crop=False))
        lines.append(f"Image {n}: the screen at {int(t // 60)}:{int(t % 60):02d}")
    n = len(batch) - 1
    prompt = ("These are frames of a screen recording of someone doing their work, in order, taken whenever the "
              "screen changed.\n\n" + "\n".join(lines) +
              f"\n\nFor each change from one image to the next ({n} changes: image 1 to 2, 2 to 3, ...), say what "
              "the person did to cause it. app: the application in front (Excel, Outlook, Notepad, Chrome, Edge, "
              "Acrobat Reader, File Explorer, Word, Teams...). window: the title bar text exactly as shown, "
              "including the program name after the dash (e.g. 'INV-1002.txt - Notepad', 'AP Tracker - Excel'), "
              "or for a browser the page title. action: click (a button, tab, link, cell or field), type (typed into a field: also give "
              "chars, about how many characters), copy, paste, shortcut (another keyboard shortcut), scroll, "
              "switch (brought another window to the front), or none (nothing the person did, e.g. a page "
              "loading or a notification). element: the name of the button, field, cell (e.g. 'B4') or tab acted "
              "on, as shown on screen. element_kind: button, field, cell, tab, link, menu item, list item, "
              "document, checkbox, drop-down or other. description: one sentence in business terms, e.g. 'Copies "
              f"the invoice number from the PDF'.\n\nReturn JSON with exactly {n} actions, in order.")
    data = llm.chat_json(prompt, schema=VIDEO_ACTIONS, role="vision", system=SYSTEM, images=images,
                         max_tokens=160 * n + 100)
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list) or len(actions) != n:
        return []
    for k, action in enumerate(actions):
        action["from"], action["to"] = first + k, first + k + 1
    return actions


def _title_exe(title: str) -> str | None:
    low = (title or "").lower()
    for marker, exe in TITLE_APPS:
        if (low.endswith(marker) if marker.startswith(" - ") else marker in low):
            return exe
    return None


def _exe(app: str, title: str = "") -> str:
    name = (app or "").strip()
    return _title_exe(title) or EXES.get(name.lower()) or EXES.get(name.lower().replace("microsoft ", "")) or \
        (re.sub(r"[^A-Za-z0-9]+", "", name) or "app") + ".exe"


def _name_apps(actions: list[dict]) -> None:
    """Each action's program: from its title bar; else from other windows showing the same kind of file
    ("INV-1003.txt" is in Notepad when "INV-1002.txt - Notepad" was); else the model's name for it."""
    by_kind: dict[str, Counter] = defaultdict(Counter)
    for a in actions:
        a["title"] = (a["window"] or a["app"] or "").strip()[:200]
        a["exe"] = _title_exe(a["title"])
        ext = re.search(r"\.([A-Za-z]{2,4})\b", a["title"])
        if a["exe"] and ext:
            by_kind[ext.group(1).lower()][a["exe"]] += 1
    for a in actions:
        if not a["exe"]:
            ext = re.search(r"\.([A-Za-z]{2,4})\b", a["title"])
            seen = by_kind.get(ext.group(1).lower()) if ext else None
            a["exe"] = seen.most_common(1)[0][0] if seen else _exe(a["app"])
    # The model reads a title from one batch of frames at a time and may name its program differently in each:
    # a title keeps the program it was given most often (ties: the program seen most in the whole video).
    overall = Counter(a["exe"] for a in actions)
    per_title: dict[str, Counter] = defaultdict(Counter)
    for a in actions:
        per_title[a["title"].lower()][a["exe"]] += 1
    for a in actions:
        votes = per_title[a["title"].lower()]
        a["exe"] = max(votes, key=lambda exe: (votes[exe], overall[exe]))


def coarse_signatures(steps: list[Step]) -> None:
    """A video can't show a copy (nothing on screen changes), so routines are matched on where each step
    happened, not on whether it copied or pasted."""
    for step in steps:
        step.signature = f"{step.app}|{step.action}"


def video_to_session(video: Path, sessions_dir: Path, name: str, cfg: Config, log=print, on_llm=None) -> Path:
    """Make a FlowSME recording folder from a video file. Needs the AI model (it reads the frames). `on_llm` is
    given the AI client, so a caller can read what reading the video cost even if it fails."""
    video = Path(video)
    if video.suffix.lower() not in VIDEO_TYPES:
        raise SystemExit(f"FlowSME reads {', '.join(VIDEO_TYPES)} videos.")
    llm = LLM(cfg.llm)
    if on_llm:
        on_llm(llm)
    if not llm.available("vision"):
        raise SystemExit("Reading a video needs the AI model (it reads each frame): set up the Meta API key.")
    cache = Path(sessions_dir) / "video-cache"
    cache.mkdir(parents=True, exist_ok=True)
    with open(video, "rb") as fp:  # reading the same video again costs nothing
        llm.use_cache(cache / f"{hashlib.file_digest(fp, 'sha256').hexdigest()[:24]}.json")
    seconds, sound = probe(video)
    if seconds > MAX_MINUTES * 60:
        raise SystemExit(f"Videos up to {MAX_MINUTES} minutes: split longer ones.")
    work = Path(tempfile.mkdtemp(prefix="flowsme-video-"))
    session = None
    try:
        frames = key_frames(video, seconds, work, log)
        if len(frames) < 2:
            raise SystemExit("The screen hardly changes in this video: there is nothing to analyse.")
        size = max(2, cfg.llm.screenshots_per_call + 1)
        batches = [(i, frames[i:i + size]) for i in range(0, len(frames) - 1, size - 1)]
        batches = [(i, b) for i, b in batches if len(b) > 1]
        log(f"Reading {len(frames) - 1} changes with {cfg.llm.vision_model}: {len(batches)} requests...")
        with ThreadPoolExecutor(max_workers=max(1, cfg.llm.concurrency)) as pool:
            results = list(pool.map(lambda job: _read_batch(llm, cfg, job[1], job[0]), batches))
        actions = [a for batch in results for a in batch]
        missed = sum(1 for (_, b), r in zip(batches, results) if not r)
        if not actions:
            raise SystemExit("The AI model couldn't read this video. Try again, or check the AI settings.")
        _name_apps(actions)

        session = new_session_dir(sessions_dir, name or video.stem)
        (session / "shots").mkdir()
        events, window, used = [], None, {}

        def shot(index: int) -> str:
            if index not in used:
                used[index] = f"shots/{len(used) + 1:06d}.jpg"
                shutil.copy(frames[index][1], session / used[index])
            return used[index]

        for a in actions:
            kind = a["action"]
            if kind == "none":
                continue
            t_from, t_to = frames[a["from"]][0], frames[a["to"]][0]
            t = round(max(t_from, t_to - min(0.5, (t_to - t_from) / 2)), 2)
            title, exe = a["title"], a["exe"]
            if (exe, title) != window:
                events.append({"t": t, "type": "focus", "app": exe, "title": title})
                window = (exe, title)
            field = (a["element"] or "").strip()[:120]
            if VALUE_LIKE.fullmatch(field) or "@" in field:  # a selected value, not a field's name: keep it out
                field = "selected text"
            element = {"name": field, "role": KINDS.get(a["element_kind"], "")}
            base = {"t": t, "app": exe, "title": title}
            if kind in ("click", "type", "copy", "paste", "switch"):
                events.append({**base, "type": "click", "button": "left", "element": element,
                               "shot": shot(a["from"])})
            if kind == "type":
                events.append({**base, "t": t + 0.2, "type": "type", "chars": max(1, int(a.get("chars") or 1)),
                               "focus": element})
            elif kind == "copy":
                events.append({**base, "t": t + 0.2, "type": "shortcut", "keys": "ctrl+c", "focus": element})
                events.append({**base, "t": t + 0.21, "type": "clipboard", "chars": 0})
            elif kind == "paste":
                events.append({**base, "t": t + 0.2, "type": "shortcut", "keys": "ctrl+v", "focus": element})
            elif kind == "shortcut":
                events.append({**base, "type": "shortcut", "keys": "keyboard shortcut", "focus": element,
                               "shot": shot(a["from"])})
            elif kind == "scroll":
                events.append({**base, "type": "scroll", "count": 1, "shot": shot(a["from"])})
            a["t"] = t
        with open(session / "events.jsonl", "w", encoding="utf-8") as out:
            for event in events:
                out.write(json.dumps(event, ensure_ascii=False) + "\n")

        # The model's sentence for each action is the step's description, so the analysis needn't read the
        # frames a second time.
        steps = build_steps([e for e in events], cfg.analysis.idle_gap_seconds)
        said = {a["t"]: a["description"] for a in actions if "t" in a and a.get("description")}
        (session / "analysis").mkdir()
        (session / "analysis" / "descriptions.json").write_text(json.dumps(
            {"model": cfg.llm.vision_model,
             "steps": {str(s.index): said[s.start][:200] for s in steps if s.start in said}},
            indent=1, ensure_ascii=False), encoding="utf-8")

        meta = {"session": session.name, "name": name or video.stem,
                "started": datetime.now().isoformat(timespec="seconds"), "duration_seconds": round(seconds, 1),
                "events": len(events), "screenshots": len(used),
                "source": {"kind": "video", "file": video.name, "frames": len(frames),
                           "unread_requests": missed, "ai_usage": llm.usage},
                "privacy": {"capture_typed_text": False}}
        if sound and _run(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
                           str(session / "narration.wav")]).returncode == 0:
            if (session / "narration.wav").stat().st_size > 16000 * 2 * NO_NARRATION:
                meta["audio"] = {"file": "narration.wav", "start": 0.0, "rate": 16000}
            else:
                (session / "narration.wav").unlink()
        (session / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log(f"Video read: {len(steps)} steps in {len({e['app'] for e in events})} applications"
            + (", with narration" if "audio" in meta else "") + f". AI cost so far: {llm.cost_summary()}")
        return session
    except BaseException:
        if session is not None:
            shutil.rmtree(session, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
