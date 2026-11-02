"""Make the 15-second voice auditions from the real opening narration (segments 1 and 2), one per voice."""
import asyncio
import json
import sys
from pathlib import Path

import edge_tts

HERE = Path(__file__).parent
OUT = HERE / "media" / "audition"


async def speak(text: str, voice: str, rate: str, pitch: str, path: Path) -> None:
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(path))


async def main(voices: list[str]) -> None:
    profile = json.loads((HERE / "delivery.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for voice in voices:
        for seg in profile["segments"][:2]:
            await speak(seg["text"], voice, seg["rate"], seg["pitch"], OUT / f"{voice}-{seg['id']}.mp3")
        print(voice, "done")


asyncio.run(main(sys.argv[1:] or [json.loads((HERE / "delivery.json").read_text(encoding="utf-8"))["voice"]]))
