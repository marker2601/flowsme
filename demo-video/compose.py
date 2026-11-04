"""Put the demo video together: one clip per chapter (the captured product frames inside a dark stage, with the
chapter label on top), the narration at -16 LUFS, and captions made from the locked narration text.

    python compose.py [source video]            -> out/final.mp4     (version 1)
    python compose.py v2 [source video]         -> out/final-v2.mp4  (version 2: adds FlowSME Recorder footage,
                                                   the Meta API calls and the before/after card)

Needs ffmpeg, node with puppeteer-core on NODE_PATH (for the label images), media/frames/* from scenes.py (version 2:
media/v2/frames/* from scenes.py v2 and the Recorder screen captures in media/v2/recorder) and the narration from
narrate.py."""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent.resolve()
V2 = sys.argv[1:2] == ["v2"]
ARGS = sys.argv[2:] if V2 else sys.argv[1:]
MEDIA, OUT = HERE / "media", HERE / "out"
WORK = MEDIA / "v2" if V2 else MEDIA  # this cut's frames, clips and overlays
CLIPS, LABELS, FRAMES = WORK / "clips", WORK / "overlays", WORK / "frames"
NARRATION = MEDIA / ("narration-v2" if V2 else "narration") / "joined.wav"
MASTER = OUT / ("final-v2.mp4" if V2 else "final.mp4")
SOURCE = Path(ARGS[0]) if ARGS else MEDIA / "supplier-invoice-entry.mp4"
FPS, TAIL = 30, 1.0          # seconds the closing card stays after the last word
STAGE = "0x10131c"
T = {s["id"]: s for s in json.loads((HERE / ("timeline-v2.json" if V2 else "timeline.json")).read_text(encoding="utf-8"))}
LAST = list(T)[-1]
END = T[LAST]["end"] + TAIL

NOTE = "Test video made for this demo · synthetic invoices · 4× speed"
# (clip, start, end, source, label[, note]). Labels: ("pill", text) = built during the hackathon; ("plain", text).
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
] if not V2 else []

if V2:
    REC = MEDIA / "v2" / "recorder"
    # Screen captures of FlowSME Recorder (gdigrab). The 1920-wide window is cropped where another window covered
    # its right edge; the later captures are of a 1728 x 972 window. (file, from s, to s, crop or None)
    WIDE = "crop=1740:979:0:0"
    WELCOME = ("welcome.mkv", 2.8, 16.8, None)              # first run: what a recording keeps; Record button
    IMPORT = [("import-menu.mkv", 7.6, 10.4, WIDE),         # More > Import a video...
              ("send.mkv", 14.0, 16.0, WIDE),               # report options, Get report
              ("send.mkv", 17.6, 19.46, WIDE)]              # Activity: sending the video
    RESULTS = [("send.mkv", 180.9, 183.4, WIDE),            # the same row turns ready, results fill in
               ("results.mkv", 15.8, 20.4, None),           # the result summary
               ("results.mkv", 21.3, 23.69, None)]          # the Narration tab
    BUILD_AT = 6.9                                          # as in scenes.py
    BUILD = [("results.mkv", 26.8, 29.3, None),             # Build automation... opens
             ("results.mkv", 40.3, 41.7, None),             # picks n8n
             ("results.mkv", 43.3, 46.3, None)]             # n8n address and key, "switched off until I turn it on"
    s = lambda i: (T[i]["start"], T[i]["end"])
    CHAPTERS = [
        ("01-open", 0, T["s1"]["end"], "report_open", None),
        ("02-work", *s("s2"), "video", ("plain", "The work: invoices typed from Notepad into Excel")),
        ("03-recorder", *s("s3"), ("rec", [WELCOME, *IMPORT]), ("pill", "FlowSME Recorder · screen video import")),
        ("04-vision", *s("s4"), "api_vision", ("pill", "Muse Spark reads each screen change of the video")),
        ("05-voice", *s("s5"), "api_voice", ("pill", "Muse Voice Transcribe · what was said, into the analysis")),
        ("06-tasks", *s("s6"), "api_tasks", ("pill", "AI cost meter and spending cap"),
         "Naming, search, recommendations: pre-hackathon"),
        ("07-results", *s("s7"), ("rec", RESULTS), ("pill", "Results in FlowSME Recorder"),
         "Same run, ready about 3 minutes after sending"),
        ("08-plan", *s("s8"), "report_plan", ("pill", "What to automate first, roadmap and payback")),
        ("09-sop", T["s9"]["start"], T["s9"]["end"] - 4.0, "sop", ("pill", "Draft SOP")),
        ("10-stories", T["s9"]["end"] - 4.0, T["s9"]["end"], "report_stories", ("pill", "User stories and acceptance tests")),
        ("11-build", T["s10"]["start"], T["s10"]["start"] + BUILD_AT, ("rec", BUILD), ("pill", "Build: workflows for six tools")),
        ("12-built", T["s10"]["start"] + BUILD_AT, T["s10"]["end"], "build", ("pill", "Build: workflows for six tools")),
        ("13-impact", *s("s11"), ("cards", [("ba.png", 0), ("ba2.png", 11.4)]), None),
        ("14-close", T["s12"]["start"], END, ("cards", [("close2.png", 0)]), None),
    ]
VIDEO_FROM, SPEED = 18.0, 4.0  # where the work starts in the source video, and how much faster it plays


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def card_png(name: str, hash_: str) -> Path:
    path = LABELS / name
    page = (HERE / "cards.html").as_uri() + "#" + quote(hash_)
    subprocess.run(["node", str(HERE / "still.js"), page, str(path)], check=True)
    return path


def label_png(name: str, label: tuple | None, note: str | None) -> Path | None:
    if not label and not note:
        return None
    parts = []
    if label:
        parts.append(("label" if label[0] == "pill" else "plain") + "=" + label[1])
    if note:
        parts.append("note=" + note)
    return card_png(f"label-{name}.png", "&".join(parts))


def clip(name: str, start: float, end: float, source, label: tuple | None, note: str | None = None) -> Path:
    out = CLIPS / f"{name}.mp4"
    dur = round(end - start, 3)
    frames = round(dur * FPS)
    lab = label_png(name, label, note if note else NOTE if source == "video" else None)
    inputs = ["-f", "lavfi", "-i", f"color=c={STAGE}:s=1920x1080:r={FPS}:d={dur}"]
    if source == "close" or (isinstance(source, tuple) and source[0] == "cards"):
        # Full-frame cards, each fading in at its own time over the one before.
        cards = [("close.png", 0)] if source == "close" else source[1]
        graph, last = "", "0:v"
        for k, (png, at) in enumerate(cards, 1):
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(LABELS / png)]
            graph += (f"[{k}:v]format=rgba,fade=t=in:st={at}:d=0.4:alpha=1[c{k}];"
                      f"[{last}][c{k}]overlay=0:0:shortest=1[o{k}];")
            last = f"o{k}"
        graph += f"[{last}]null[v]"
    else:
        if source == "video":
            skip = VIDEO_FROM + (start - T["s2"]["start"]) * SPEED
            inputs += ["-ss", f"{skip:.3f}", "-i", str(SOURCE)]
            prod = (f"[1:v]setpts=PTS/{SPEED},fps={FPS},scale=-2:864:flags=lanczos,"
                    f"pad=1536:864:(ow-iw)/2:0:color=0x1f1f1f[p]")
        elif isinstance(source, tuple) and source[0] == "rec":
            # Recorder footage: each piece trimmed, cropped and scaled to the product frame, then joined; the last
            # frame holds if the pieces are shorter than the chapter.
            parts = []
            for k, (file, t0, t1, crop) in enumerate(source[1], 1):
                inputs += ["-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(REC / file)]
                parts.append(f"[{k}:v]fps={FPS},{crop + ',' if crop else ''}scale=1536:864:flags=lanczos,"
                             f"setsar=1,format=yuv420p[r{k}]")
            n = len(source[1])
            prod = (";".join(parts) + ";" + "".join(f"[r{k}]" for k in range(1, n + 1)) +
                    f"concat=n={n}:v=1:a=0,tpad=stop_mode=clone:stop_duration={dur}[p]")
        else:
            inputs += ["-framerate", str(FPS), "-i", str(FRAMES / source / "%05d.png")]
            prod = "[1:v]scale=1536:864:flags=lanczos[p]"
        graph = (f"{prod};[0:v]drawbox=x=190:y=58:w=1540:h=868:color=0x2a3040:t=2[s];"
                 f"[s][p]overlay=192:60:shortest=0:eof_action=repeat[v0]")
        last = "v0"
        if source == "report_open":  # the title card over the real report, then away
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(LABELS / "title.png")]
            n = len([i for i in inputs if i == "-i"]) - 1
            graph += (f";[{n}:v]format=rgba,fade=t=in:st=0.2:d=0.5:alpha=1,fade=t=out:st=4.6:d=0.6:alpha=1[t];"
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
        # A chunk must fit on one line of 42, or split into two such lines at a word boundary.
        fits = lambda a, b: width(a, b) <= 42 or any(max(width(a, k), width(k, b)) <= 42 for k in range(a + 1, b))
        spans = []
        for a, b in parts:
            while not fits(a, b):
                cut = max((i + 1 for i in range(a, b - 1) if tokens[i].endswith(",") and fits(a, i + 1)), default=None)
                if cut is None:  # before a joining word if one fits, so a phrase isn't split
                    ends = [i for i in range(a + 1, b) if fits(a, i)]
                    joins = [i for i in ends if tokens[i].lower() in
                             ("as", "and", "so", "instead", "to", "with", "while", "from", "for", "but", "because")]
                    cut = max(joins or ends)
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
    path = WORK / "captions.ass"
    path.write_text(head + "".join(f"Dialogue: 0,{fmt(a)},{fmt(b)},Cap,,0,0,0,,{t}\n" for a, b, t in events),
                    encoding="utf-8")
    return path


def main() -> None:
    CLIPS.mkdir(parents=True, exist_ok=True)
    LABELS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    for name in (["title", "ba", "ba2", "close2"] if V2 else []):
        card_png(f"{name}.png", name)
    clips = [clip(*c) for c in CHAPTERS]
    (CLIPS / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    run("-f", "concat", "-safe", "0", "-i", str(CLIPS / "list.txt"), "-c", "copy", str(WORK / "picture.mp4"))
    ass = captions()
    # Loudness in two passes: measure, then apply the measured values (linear) to reach -16 LUFS, -1.5 dBTP.
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(NARRATION), "-af",
                            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    norm = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    sub = str(ass).replace("\\", "/").replace(":", "\\:")
    run("-i", str(WORK / "picture.mp4"), "-i", str(NARRATION),
        "-filter_complex", f"[0:v]subtitles='{sub}'[v];[1:a]{norm},aresample=48000,apad[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{END:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
        str(MASTER))
    print(MASTER, f"{END:.2f} s")


main()
