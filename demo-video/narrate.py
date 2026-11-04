"""Speak every segment of delivery.json with the approved voice, trim the silence the voice leaves at each end, and
write narration.wav plus timeline.json (when each segment starts and ends) and word timings for the captions.

    python narrate.py        -> delivery.json, media/narration/, timeline.json
    python narrate.py v2     -> delivery-v2.json, media/narration-v2/, timeline-v2.json"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import edge_tts

HERE = Path(__file__).parent
CUT = f"-{sys.argv[1]}" if len(sys.argv) > 1 else ""
MEDIA = HERE / "media" / f"narration{CUT}"


async def speak(seg: dict, voice: str, volume: str) -> list[dict]:
    """Saves the segment's mp3 and returns its word boundaries (seconds from the start of the segment)."""
    comm = edge_tts.Communicate(seg["text"], voice, rate=seg["rate"], pitch=seg["pitch"], volume=volume,
                                boundary="WordBoundary")
    words = []
    with open(MEDIA / f"{seg['id']}.mp3", "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({"start": chunk["offset"] / 1e7, "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                              "text": chunk["text"]})
    return words


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def main() -> None:
    profile = json.loads((HERE / f"delivery{CUT}.json").read_text(encoding="utf-8"))
    MEDIA.mkdir(parents=True, exist_ok=True)
    timeline, t, parts = [], 0.0, []
    for seg in profile["segments"]:
        for attempt in range(4):  # the voice service now and then answers with no audio: ask again
            try:
                words = asyncio.run(speak(seg, profile["voice"], profile["volume"]))
                break
            except edge_tts.exceptions.NoAudioReceived:
                if attempt == 3:
                    raise
                time.sleep(3)
        lead = words[0]["start"]
        # Cut the voice's own lead-in and tail silence, keep 0.08 s of air after the last word, then add the
        # deliberate pause from the profile.
        speech = words[-1]["end"] - lead + 0.08
        wav = MEDIA / f"{seg['id']}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{lead:.3f}", "-t", f"{speech:.3f}",
                        "-i", str(MEDIA / f"{seg['id']}.mp3"), "-af", f"apad=pad_dur={seg['pause_after']}",
                        "-ar", "48000", "-ac", "2", str(wav)], check=True)
        length = duration(wav)
        timeline.append({"id": seg["id"], "start": round(t, 3), "speech_end": round(t + speech, 3),
                         "end": round(t + length, 3), "text": seg["text"],
                         "words": [{**w, "start": round(t + w["start"] - lead, 3), "end": round(t + w["end"] - lead, 3)}
                                   for w in words]})
        parts.append(wav)
        t += length
    (MEDIA / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(MEDIA / "list.txt"),
                    "-c", "copy", str(MEDIA / "joined.wav")], check=True)
    (HERE / f"timeline{CUT}.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
    for s in timeline:
        print(f"{s['id']}: {s['start']:6.2f} - {s['end']:6.2f}  ({len(s['text'].split())} words)")
    print(f"total {t:.2f} s")


main()
