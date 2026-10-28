"""Where sessions live on disk and how to read them back."""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


def new_session_dir(sessions_dir: Path, name: str | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    path = Path(sessions_dir) / (f"{stamp}-{slug}" if slug else stamp)
    path.mkdir(parents=True, exist_ok=False)
    return path


def list_sessions(sessions_dir: Path) -> list[Path]:
    root = Path(sessions_dir)
    if not root.exists():
        return []
    found = [p for p in root.iterdir() if (p / "events.jsonl").exists()]
    return sorted(found, key=lambda p: (p / "events.jsonl").stat().st_mtime)


def resolve_session(arg: str, sessions_dir: Path) -> Path:
    if arg == "latest":
        sessions = list_sessions(sessions_dir)
        if not sessions:
            raise SystemExit("No recorded sessions yet. Record one with: python -m flowsme record")
        return sessions[-1]
    for candidate in (Path(arg), Path(sessions_dir) / arg):
        if (candidate / "events.jsonl").exists():
            return candidate
    raise SystemExit(f"Session not found: {arg}")


def load_events(session_dir: Path) -> list[dict]:
    events = []
    with open(session_dir / "events.jsonl", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a crash can leave a half-written last line
    events.sort(key=lambda e: e.get("t", 0))
    return events


def load_meta(session_dir: Path) -> dict:
    path = session_dir / "meta.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def import_recording(zip_path: Path, sessions_dir: Path) -> Path:
    """Unpack a recording someone zipped with "Send to FlowSME team" into the recordings folder."""
    with zipfile.ZipFile(zip_path) as archive:
        events = sorted((n for n in archive.namelist() if n.endswith("events.jsonl")), key=len)
        if not events:
            raise ValueError("This zip has no FlowSME recording in it (no events.jsonl).")
        top = events[0][:-len("events.jsonl")].rstrip("/")
        name = Path(top).name if top else Path(zip_path).stem.removeprefix("FlowSME-")
        dest, n = Path(sessions_dir) / name, 2
        while dest.exists():
            dest, n = Path(sessions_dir) / f"{name}-{n}", n + 1
        root = dest.resolve()
        planned = []
        for member in archive.infolist():  # every path is checked before anything is written
            if member.is_dir() or (top and not member.filename.startswith(top + "/")):
                continue
            rel = member.filename[len(top) + 1:] if top else member.filename
            target = (dest / rel).resolve()
            if root not in target.parents:  # a path like ../../x would land outside the folder
                raise ValueError(f"Unsafe path in the zip: {member.filename}")
            planned.append((member, target))
        try:
            for member, target in planned:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)  # no half-imported recording left behind
            raise
    return dest
