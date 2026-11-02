"""Put the demo video together: one clip per chapter (the captured product frames inside a dark stage, with the
chapter label on top), the narration at -16 LUFS, and captions made from the locked narration text.

    python compose.py            -> out/final.mp4

Needs ffmpeg, node with puppeteer-core on NODE_PATH (for the label images), media/frames/* from scenes.py and
media/narration/joined.wav from narrate.py."""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent.resolve()
MEDIA, OUT = HERE / "media", HERE / "out"
CLIPS, LABELS = MEDIA / "clips", MEDIA / "overlays"
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else MEDIA / "supplier-invoice-entry.mp4"
FPS, TAIL = 30, 1.0          # seconds the closing card stays after the last word
STAGE = "0x10131c"
T = {s["id"]: s for s in json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))}
END = T["s9"]["end"] + TAIL

NOTE = "Test video made for this demo · synthetic invoices · 4× speed"
# (clip, start, end, source, label). Labels: ("pill", text) = built during the hackathon; ("plain", text).
CHAPTERS = [
    ("01-open", 0, T["s1"]["end"], "report_open", None),
    ("02-work", T["s2"]["start"], T["s2"]["end"], "video", ("plain", "The work: invoices typed from Notepad into Excel")),
    ("03-import", T["s3"]["start"], T["s3"]["end"], "video", ("pill", "Screen video import")),
    ("04-server", T["s4"]["start"], T["s4"]["end"], "panel", ("pill", "Analysis on the FlowSME server")),
    ("05-plan", T["s5"]["start"], T["s5"]["end"], "report_plan", ("pill", "What to automate first, roadmap and payback")),
    ("06-said", T["s6"]["start"], T["s6"]["end"], "report_task", ("pill", "Narration used in the recommendations")),
    ("07-sop", T["s7"]["start"], T["s7"]["end"] - 4.0, "sop", ("pill", "Draft SOP")),
    ("08-stories", T["s7"]["end"] - 4.0, T["s7"]["end"], "report_stories", ("pill", "User stories and acceptance tests")),
    ("09-build", T["s8"]["start"], T["s8"]["end"], "build", ("pill", "Build: workflows for six tools")),
    ("10-close", T["s9"]["start"], END, "close", None),
]
VIDEO_FROM, SPEED = 18.0, 4.0  # where the work starts in the source video, and how much faster it plays


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def label_png(name: str, label: tuple | None, note: bool) -> Path | None:
    if not label and not note:
        return None
    parts = []
    if label:
        parts.append(("label" if label[0] == "pill" else "plain") + "=" + label[1])
    if note:
        parts.append("note=" + NOTE)
    path = LABELS / f"label-{name}.png"
    page = (HERE / "cards.html").as_uri() + "#" + quote("&".join(parts))
    subprocess.run(["node", str(HERE / "still.js"), page, str(path)], check=True)
    return path


def clip(name: str, start: float, end: float, source: str, label: tuple | None) -> Path:
    out = CLIPS / f"{name}.mp4"
    dur = round(end - start, 3)
    frames = round(dur * FPS)
    lab = label_png(name, label, source == "video")
    inputs = ["-f", "lavfi", "-i", f"color=c={STAGE}:s=1920x1080:r={FPS}:d={dur}"]
    if source == "close":
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(LABELS / "close.png")]
        graph = "[1:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1[c];[0:v][c]overlay=0:0:shortest=1[v]"
    else:
        if source == "video":
            skip = VIDEO_FROM + (start - T["s2"]["start"]) * SPEED
            inputs += ["-ss", f"{skip:.3f}", "-i", str(SOURCE)]
            prod = (f"[1:v]setpts=PTS/{SPEED},fps={FPS},scale=-2:864:flags=lanczos,"
                    f"pad=1536:864:(ow-iw)/2:0:color=0x1f1f1f[p]")
        else:
            inputs += ["-framerate", str(FPS), "-i", str(MEDIA / "frames" / source / "%05d.png")]
            prod = "[1:v]scale=1536:864:flags=lanczos[p]"
        graph = (f"{prod};[0:v]drawbox=x=190:y=58:w=1540:h=868:color=0x2a3040:t=2[s];"
                 f"[s][p]overlay=192:60:shortest=0:eof_action=repeat[v0]")
        last = "v0"
        if source == "report_open":  # the title card over the real report, then away
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(LABELS / "title.png")]
            graph += (f";[2:v]format=rgba,fade=t=in:st=0.2:d=0.5:alpha=1,fade=t=out:st=4.6:d=0.6:alpha=1[t];"
                      f"[v0][t]overlay=0:0:shortest=1[v1]")
            last = "v1"
        if lab:
            n = len([i for i in inputs if i == "-i"])
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(lab)]
            graph += f";[{last}][{n}:v]overlay=0:0:shortest=1[v]"
        else:
            graph += f";[{last}]null[v]"
    run(*inputs, "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", str(out))
    return out


def captions() -> Path:
    """Timed chunks of at most two lines of 42 characters, cut at word boundaries, each inside its segment, that
    join back into the narration text exactly."""
    def fmt(t: float) -> str:
        cs = round(t * 100)
        return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    events = []
    for seg in T.values():
        tokens = seg["text"].split()
        words = [w for w in seg["words"] if norm(w["text"])]
        # Each token starts where the boundary word that begins it starts (the voice splits some tokens).
        starts, wi, buf = [], 0, ""
        for tok in tokens:
            starts.append(words[min(wi, len(words) - 1)]["start"])
            need = norm(tok)
            while need and wi < len(words) and len(buf) < len(need):
                buf += norm(words[wi]["text"])
                wi += 1
            buf = buf[len(need):] if buf.startswith(need) else ""
        # Chunks end at sentences; a sentence too long for two lines is split at commas, then between words.
        width = lambda a, b: len(" ".join(tokens[a:b]))
        parts, a = [], 0
        for i, tok in enumerate(tokens):
            if tok.endswith((".", ":", "?", "!")) or i == len(tokens) - 1:
                parts.append((a, i + 1))
                a = i + 1
        spans = []
        for a, b in parts:
            while width(a, b) > 84:
                cut = max((i + 1 for i in range(a, b - 1) if tokens[i].endswith(",") and width(a, i + 1) <= 84),
                          default=None)
                if cut is None:  # before a joining word if one fits, so a phrase isn't split
                    fits = [i for i in range(a + 1, b) if width(a, i) <= 84]
                    joins = [i for i in fits if tokens[i].lower() in
                             ("as", "and", "so", "instead", "to", "with", "while", "from", "for", "but", "because")]
                    cut = max(joins or fits)
                spans.append((a, cut))
                a = cut
            spans.append((a, b))
        chunks = []
        for a, b in spans:
            if width(a, b) <= 42:
                chunks.append((a, b, [" ".join(tokens[a:b])]))
                continue
            k = min((k for k in range(a + 1, b) if max(width(a, k), width(k, b)) <= 42),
                    key=lambda k: max(width(a, k), width(k, b)))
            chunks.append((a, b, [" ".join(tokens[a:k]), " ".join(tokens[k:b])]))
        assert " ".join(" ".join(c[2]) for c in chunks) == seg["text"], seg["id"]
        for k, (a, b, lines) in enumerate(chunks):
            begin = seg["start"] if k == 0 else starts[a]
            end = starts[chunks[k + 1][0]] if k + 1 < len(chunks) else min(seg["speech_end"] + 0.35, seg["end"])
            events.append((begin, end - 0.02, "\\N".join(lines)))
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            "Style: Cap,Segoe UI Semibold,38,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,1.2,0,2,"
            "120,120,22,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    path = MEDIA / "captions.ass"
    path.write_text(head + "".join(f"Dialogue: 0,{fmt(a)},{fmt(b)},Cap,,0,0,0,,{t}\n" for a, b, t in events),
                    encoding="utf-8")
    return path


def main() -> None:
    CLIPS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    clips = [clip(*c) for c in CHAPTERS]
    (CLIPS / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    run("-f", "concat", "-safe", "0", "-i", str(CLIPS / "list.txt"), "-c", "copy", str(MEDIA / "picture.mp4"))
    ass = captions()
    # Loudness in two passes: measure, then apply the measured values (linear) to reach -16 LUFS, -1.5 dBTP.
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(MEDIA / "narration" / "joined.wav"), "-af",
                            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    norm = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    sub = str(ass).replace("\\", "/").replace(":", "\\:")
    run("-i", str(MEDIA / "picture.mp4"), "-i", str(MEDIA / "narration" / "joined.wav"),
        "-filter_complex", f"[0:v]subtitles='{sub}'[v];[1:a]{norm},aresample=48000,apad[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{END:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
        str(OUT / "final.mp4"))
    print(OUT / "final.mp4", f"{END:.2f} s")


main()
