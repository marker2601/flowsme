"""Narration: what the person said while working, transcribed with Muse Voice Transcribe and lined up with the
recorded steps. Clicks and keystrokes show *what* happened; narration explains *why*: the rules and judgement
behind values typed by hand, which is exactly what the open questions ask about.

Answers taken from narration must quote the transcript word for word: a quote that isn't in it is rejected."""
from __future__ import annotations

import io
import json
import re
import wave
from pathlib import Path

from .integration import endpoint, label, _key
from .llm import LLM
from .models import Step, Task
from .schemas import NARRATION_ANSWER

MAX_CHUNK_SECONDS = 540  # Muse Voice Transcribe takes up to 10 minutes of audio per request
CONTEXT_BEFORE, CONTEXT_AFTER = 90.0, 30.0  # seconds of narration considered around a typed value

SYSTEM = ("You read a transcript of someone narrating their own office work. Answer only from what they said; "
          "never guess. Quote their exact words.")

QUESTION = """While recording their work, the person typed a value by hand into **{field}** ({chars} characters).
The recording cannot see where the value came from. Here is what they said around those moments (seconds from
the start of the recording):

{lines}

Does the narration say where this value comes from, or the rule used to decide it?
Return JSON: {{"answered": true/false, "answer": "where the value comes from, in one sentence",
"rule": "the rule as an automation could apply it, or empty", "quote": "the exact words from the transcript"}}
If the narration doesn't say, answered=false and leave the other fields empty."""


def transcribe_session(session_dir: Path, meta: dict, llm: LLM, log=print) -> list[dict]:
    """Spoken turns as [{"start", "end", "text"}] in seconds on the recording's timeline (cached)."""
    audio = meta.get("audio")
    path = session_dir / audio["file"] if audio else None
    if not path or not path.exists():
        return []
    cache = session_dir / "analysis" / "narration.json"
    stamp = f"{path.stat().st_size}:{int(path.stat().st_mtime)}"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("stamp") == stamp:
            return saved["turns"]
    if not llm.available("text"):
        log("Narration found, but no AI endpoint to transcribe it (use config.meta.toml).")
        return []
    turns = []
    for offset, chunk, seconds in _chunks(path):
        data = llm.transcribe(chunk, seconds)
        if data is None:
            return turns  # failed: don't cache a partial transcript
        found = data.get("turns") or []
        if not found and (data.get("transcript") or "").strip():
            found = [{"startMs": 0, "endMs": int(seconds * 1000), "transcript": data["transcript"]}]
        for t in found:
            text = (t.get("transcript") or "").strip()
            if text:
                turns.append({"start": round(audio["start"] + offset + (t.get("startMs") or 0) / 1000, 2),
                              "end": round(audio["start"] + offset + (t.get("endMs") or 0) / 1000, 2),
                              "text": text})
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"stamp": stamp, "turns": turns}, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"Narration: {len(turns)} spoken turns transcribed with {llm.cfg.transcribe_model}")
    return turns


def _chunks(path: Path):
    """(offset seconds, WAV bytes, length seconds) pieces small enough for one transcription request."""
    with wave.open(str(path)) as src:
        rate, width, channels = src.getframerate(), src.getsampwidth(), src.getnchannels()
        offset = 0
        while frames := src.readframes(MAX_CHUNK_SECONDS * rate):
            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(width)
                out.setframerate(rate)
                out.writeframes(frames)
            count = len(frames) // (width * channels)
            yield offset / rate, buf.getvalue(), count / rate
            offset += count


def between(turns: list[dict], start: float, end: float) -> list[dict]:
    return [t for t in turns if t["end"] >= start and t["start"] <= end]


def attach(steps: list[Step], turns: list[dict]) -> None:
    """Give each step what was said while it happened."""
    for step in steps:
        said = between(turns, step.start - 2, step.start + step.duration + 2)
        step.narration = " ".join(t["text"] for t in said)


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def answer_open_questions(task: Task, flows: list[dict], turns: list[dict], llm: LLM | None,
                          known: dict | None = None) -> int:
    """Use the narration to explain values typed by hand. Returns how many were answered.
    `known` carries answers between tasks: a rule said once applies to the same field everywhere in the session."""
    if not turns or not llm or not llm.available("text"):
        return 0
    known = {} if known is None else known
    answered = 0
    for row in flows:
        if row["how"] != "typed" or row.get("confirmed_source"):
            continue
        times = [s.start for run in (task.iterations or [task.steps]) for s in run
                 if s.chars_typed and _key(endpoint(s)) == _key(row["to"])]
        said = {}
        for t in times:
            for turn in between(turns, t - CONTEXT_BEFORE, t + CONTEXT_AFTER):
                said[(turn["start"], turn["text"])] = turn
        if not said:
            if _key(row["to"]) in known:  # explained while doing another task
                row["narrated"] = known[_key(row["to"])]
                answered += 1
            continue
        lines = "\n".join(f"[{turn['start']:.0f}s] {turn['text']}" for turn in sorted(said.values(),
                                                                                      key=lambda x: x["start"]))
        data = llm.chat_json(QUESTION.format(field=label(row["to"]), chars=row["chars"], lines=lines),
                             schema=NARRATION_ANSWER, role="text", system=SYSTEM, effort="low", max_tokens=600)
        if not isinstance(data, dict) or not data.get("answered") or not data.get("quote"):
            continue
        # The quote must really be in the transcript: otherwise the answer is a guess, and is rejected.
        if _normal(data["quote"]) not in _normal(" ".join(turn["text"] for turn in said.values())):
            continue
        row["narrated"] = {"answer": str(data.get("answer") or ""), "rule": str(data.get("rule") or ""),
                           "quote": str(data["quote"])}
        known.setdefault(_key(row["to"]), row["narrated"])
        answered += 1
    return answered
