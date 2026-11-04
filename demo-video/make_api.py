"""Build the "Meta API calls" page for version 2 from one real run on the FlowSME server: for each stage, the call
FlowSME made to Meta's Model API and what came back. Responses are copied from the run's own caches (the server keeps
every answer so it never pays twice); requests show the fields flowsme/llm.py sends, with long values trimmed.

    python make_api.py <recording folder R-...> <out.html>

capture.js reveals it stage by stage by setting <body data-step="N">."""
import base64
import html
import json
import re
import sys
from pathlib import Path

rec_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
record = json.loads((rec_dir / "record.json").read_text(encoding="utf-8"))
session = rec_dir / record["session"]
video_cache = json.loads(next((rec_dir / "video-cache").glob("*.json")).read_text(encoding="utf-8"))
ai_cache = json.loads((session / "analysis" / "ai_cache.json").read_text(encoding="utf-8"))
analysis = json.loads((session / "analysis" / "analysis.json").read_text(encoding="utf-8"))
said = json.loads((session / "analysis" / "descriptions.json").read_text(encoding="utf-8"))["steps"]
server = (rec_dir.parents[3] / "server.toml").read_text(encoding="utf-8")
setting = lambda name: re.search(rf'^{name}\s*=\s*"?([^"\n]+)"?', server, re.M).group(1)
BASE, MODEL, CAP = setting("base_url"), setting("vision_model"), float(setting("max_cost_usd"))


def parsed(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None  # a reply cut off mid-way: the server asked again


answers = [p for p in map(parsed, ai_cache.values()) if p]
vision = [p for p in map(parsed, video_cache.values()) if p and p.get("actions")]
voice = next(p for p in answers if "transcript" in p)
named = [p for p in answers if "name" in p and "summary" in p]
plan = next(p for p in answers if "assumptions" in p)
search = next(p for p in answers if "results" in p and "answer" in p)

# Stage 1: the reply whose first action pastes the vendor name into a cell, and the frame of that step.
reply, paste = next((r, r["actions"][0]) for r in vision if r["actions"][0]["action"] == "paste"
                    and r["actions"][0]["element_kind"] == "cell" and "vendor" in r["actions"][0]["description"])
step = next(s for s in analysis["steps"] if said.get(str(s["index"])) == paste["description"] and s.get("screenshot"))
frame = "data:image/jpeg;base64," + base64.b64encode((session / step["screenshot"]).read_bytes()).decode()
rule_turn = next(t for t in voice["turns"] if "net 30" in t["transcript"])
task = next(p for p in named if "30" in p["summary"])
rule = next(a for a in plan["assumptions"] if "30" in a)
cost = next(line for line in record["log"] if "AI cost so far" in line)
spent = float(re.search(r"\$([\d.]+)", cost).group(1))
frames_line = next(line for line in record["log"] if line.startswith("Video:"))
reading_line = next(line for line in record["log"] if line.startswith("Reading"))
heard_line = next(line for line in record["log"] if line.startswith("Narration:"))


def layout(obj, width: int, indent: int = 0, used: int = 0, cut: bool = True, rows: bool = False) -> str:
    """JSON that keeps anything fitting the width on one line. Strings too long for it are cut with an ellipsis
    (cut), and with rows each item of a list goes on one line of its own however long (the page wraps it)."""
    flat = json.dumps(obj, ensure_ascii=False)
    if indent + used + len(flat) <= width or not isinstance(obj, (dict, list)):
        if cut and isinstance(obj, str) and indent + used + len(flat) > width:
            return json.dumps(obj[:max(8, width - indent - used - 3)].rstrip() + "…", ensure_ascii=False)
        return flat
    pad = " " * (indent + 2)
    if isinstance(obj, dict):
        items = [f'{pad}{json.dumps(k)}: {layout(v, width, indent + 2, len(k) + 4, cut, rows)}' for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    item = (lambda v: json.dumps(v, ensure_ascii=False)) if rows else (lambda v: layout(v, width, indent + 2, 0, cut))
    return "[\n" + ",\n".join(pad + item(v) for v in obj) + "\n" + " " * indent + "]"


def code(obj, mark: str = "", width: int = 60, **how) -> str:
    """JSON (or text) escaped for <pre>, with every line that contains `mark` highlighted."""
    text = obj if isinstance(obj, str) else layout(obj, width, **how)
    return "\n".join(f'<span class="hl">{html.escape(line)}</span>' if mark and mark in line else html.escape(line)
                     for line in text.splitlines())


def short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


vision_request = f"""{{
  "model": "{MODEL}",
  "reasoning_effort": "low",
  "messages": [
    {{"role": "system", "content": "You watch screen recordings of office
       work and say exactly what the person did…"}},
    {{"role": "user", "content": [
      {{"type": "image_url", "image_url": {{"url": "data:image/jpeg;…"}}}},
      … 5 frames in all …
      {{"type": "text", "text": "These are frames of a screen recording…
         say what the person did… Return JSON with exactly 4 actions"}}]}}],
  "response_format": {{"type": "json_schema", "json_schema":
    {{"name": "result", "strict": true, "schema": {{"actions": […]}}}}}}
}}"""
vision_reply = {"actions": [{k: paste[k] for k in ("action", "app", "element", "element_kind", "window", "description")},
                            *({k: x[k] for k in ("action", "app", "element")} for x in reply["actions"][1:3]), "…"]}
voice_request = ("--boundary\nContent-Disposition: form-data; name=\"request\"\n\n"
                 + json.dumps({"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "ENDPOINTING"})
                 + "\n--boundary\nContent-Disposition: form-data; name=\"audio\"; filename=\"narration.wav\"\n\n"
                   f"<{voice['audioDurationMs'] / 60000:.1f} minutes of 16 kHz audio>")
voice_reply = {"sessionId": short(voice["sessionId"], 34),
               "turns": [{"startMs": t["startMs"], "endMs": t["endMs"], "transcript": t["transcript"]}
                         for t in voice["turns"][:3]] + ["…"]}
task_reply = {"name": task["name"], "summary": task["summary"], "inputs": task["inputs"], "outputs": task["outputs"]}
plan_reply = {"automation_potential": plan["automation_potential"], "automatable_fraction": plan["automatable_fraction"],
              "assumptions": [short(plan["assumptions"][0], 34), rule, "…"]}
search_request = {"model": MODEL, "input": "Which existing tools, connectors or official APIs can do this, and where are "
                                           "they documented? automate copying data from Notepad to Excel; …",
                  "tools": [{"type": "web_search", "search_context_size": "low"}],
                  "include": ["web_search_call.results"], "reasoning": {"effort": "low"}, "store": False}
hits = "".join(f'<li><b>{html.escape(short(r["title"], 62))}</b><span>{html.escape(r["url"].split("/")[2])}</span></li>'
               for r in search["results"][:4])

STAGES = ["Recording", "Screen changes", "Narration", "Tasks", "Recommendations", "Report"]


def track(*active: int) -> str:
    return '<ol class="track">' + "".join(
        f'<li class="{"on" if i in active else "done" if i < min(active) else ""}">{s}</li>'
        for i, s in enumerate(STAGES)) + "</ol>"


def call(method: str, path: str, model: str) -> str:
    return (f'<div class="call"><span class="verb">{method}</span><span class="url">{html.escape(BASE)}'
            f'<b>{path}</b></span><span class="model">{model}</span></div>')


views = [
    # 1-3: Muse Spark reads the screen changes.
    f"""<section class="view v1">{track(1)}
      <p class="log">{html.escape(frames_line)}<br>{html.escape(reading_line)}</p>
      {call("POST", "/chat/completions", MODEL + " · vision")}
      <div class="cols">
        <div class="pane req"><h4>Request <em>built by flowsme/video.py · trimmed</em></h4>
          <pre>{code(vision_request, width=64)}</pre></div>
        <div class="pane res"><h4>Response <em>verbatim from this run · schema-checked</em></h4>
          <img src="{frame}" alt=""><pre>{code(vision_reply, mark='"' + paste["element"] + '"', width=72)}</pre></div>
      </div></section>""",
    # 4-6: Muse Voice Transcribe, then where the rule went.
    f"""<section class="view v2">{track(2)}
      <p class="log">{html.escape(heard_line)}</p>
      {call("POST", "/asr/transcribe", "muse-voice-transcribe-1.0")}
      <div class="cols">
        <div class="pane req"><h4>Request <em>multipart, from flowsme/llm.py</em></h4><pre>{code(voice_request)}</pre></div>
        <div class="pane res"><h4>Response <em>verbatim from this run</em></h4>
          <pre>{code(voice_reply, mark="net 30", width=60, cut=False, rows=True)}</pre></div>
      </div>
      <div class="carry"><h4>Where that sentence went</h4>
        <div class="cols">
          <div class="pane"><h5>Task summary <em>{MODEL} · structured JSON</em></h5>
            <pre>{code({"summary": task["summary"]}, mark="30", width=60, cut=False)}</pre></div>
          <div class="pane"><h5>Top recommendation <em>{MODEL} · structured JSON</em></h5>
            <pre>{code({"assumptions": [rule]}, mark="30", width=60, cut=False, rows=True)}</pre></div>
        </div></div></section>""",
    # 7-8: tasks, web search, recommendation; 9: the cost meter.
    f"""<section class="view v3">{track(3, 4)}
      <div class="cols three">
        <div class="pane"><h4>Names the task {call("POST", "/chat/completions", MODEL)}</h4>
          <pre>{code(task_reply, width=46)}</pre></div>
        <div class="pane"><h4>Searches the web {call("POST", "/responses", "web_search")}</h4>
          <pre class="small">{code(search_request, width=52)}</pre><h5>What the search returned</h5><ul class="hits">{hits}</ul></div>
        <div class="pane"><h4>Writes the recommendation {call("POST", "/chat/completions", MODEL)}</h4>
          <pre>{code(plan_reply, mark="30", width=46, cut=False, rows=True)}</pre></div>
      </div>
      <div class="meter"><div class="mtext"><b>AI cost meter</b> {html.escape(cost.split("AI cost so far: ")[1])}
        <span>spending cap: ${CAP:.2f} per report (server.toml, max_cost_usd)</span></div>
        <div class="bar"><i style="width:{100 * spent / CAP:.1f}%"></i></div>
        <div class="ends"><span>$0</span><span>reading the video: ${spent:.2f}</span><span>cap ${CAP:.2f}</span></div>
      </div></section>""",
]

css = """
  * { box-sizing:border-box; margin:0; }
  body { width:1280px; height:720px; overflow:hidden; background:#f4f5f8; color:#1b1d22;
         font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif; }
  .view { position:absolute; inset:0; padding:18px 24px; display:none; }
  .track { list-style:none; display:flex; gap:6px; padding:0; margin-bottom:12px; }
  .track li { flex:1; text-align:center; font-size:12.5px; font-weight:600; color:#8a909b; background:#e6e8ee;
              border-radius:999px; padding:4px 0; }
  .track li.done { background:#dfe6fb; color:#3355d1; }
  .track li.on { background:linear-gradient(135deg,#3355d1,#7a5af8); color:#fff; }
  .log { font:12.5px/1.6 "Cascadia Code",Consolas,monospace; background:#14161b; color:#d7dbe4; border-radius:10px;
         padding:8px 14px; margin-bottom:10px; }
  .call { display:inline-flex; align-items:center; gap:10px; font:13px "Cascadia Code",Consolas,monospace;
          background:#fff; border:1px solid #d9dce4; border-radius:10px; padding:5px 12px; margin-bottom:10px; }
  .pane .call { display:flex; flex-wrap:wrap; margin:6px 0 2px; font-size:11px; padding:4px 8px; gap:4px 7px; }
  .pane .call .model { font-size:11px; padding:1px 8px; }
  .verb { font-weight:700; color:#067647; }
  .url { color:#3c4150; } .url b { color:#1b1d22; }
  .model { font-family:"Segoe UI",system-ui,sans-serif; font-weight:700; font-size:12px; color:#fff; border-radius:999px;
           padding:2px 10px; background:linear-gradient(135deg,#3355d1,#7a5af8); }
  .cols { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .cols.three { grid-template-columns:1fr 1fr 1fr; }
  .pane { background:#fff; border:1px solid #e1e4ea; border-radius:12px; padding:10px 12px; overflow:hidden; }
  .pane h4 { font-size:13.5px; margin-bottom:6px; }
  .pane h5 { font-size:12.5px; margin:6px 0 4px; color:#3c4150; }
  .pane em { font-style:normal; font-weight:500; color:#7a808c; margin-left:6px; font-size:12px; }
  pre { font:12.5px/1.5 "Cascadia Code",Consolas,monospace; white-space:pre-wrap; color:#2a2e36; }
  pre.small { font-size:11px; }
  .hl { background:#fff3c4; box-shadow:0 0 0 2px #fff3c4; border-radius:3px; font-weight:700; color:#1b1d22; }
  .res img { width:100%; height:220px; object-fit:contain; background:#1f1f1f; border-radius:8px; margin-bottom:8px; }
  .res pre, .req pre { opacity:1; }
  .hits { list-style:none; padding:0; display:grid; gap:5px; }
  .hits li { font-size:12px; line-height:1.3; } .hits b { display:block; font-weight:600; }
  .hits span { color:#067647; font-size:11.5px; }
  .carry { margin-top:12px; } .carry h4 { font-size:13.5px; margin-bottom:6px; }
  .meter { margin-top:12px; background:#fff; border:1px solid #e1e4ea; border-radius:12px; padding:10px 14px; }
  .mtext { font-size:13.5px; display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }
  .mtext span { color:#7a808c; }
  .bar { height:12px; background:#eceef3; border-radius:999px; margin:8px 0 4px; overflow:hidden; }
  .bar i { display:block; height:100%; background:linear-gradient(90deg,#12b76a,#3355d1); border-radius:999px; }
  .ends { display:flex; justify-content:space-between; font-size:12px; color:#7a808c; }
  /* Steps: 1 request, 2 response, 3 highlight | 4 voice, 5 highlight, 6 where it went | 7 three calls, 8 meter */
  .v1 .res, .v2 .res, .v2 .carry, .v3 .meter { visibility:hidden; }
  .hl { background:transparent; box-shadow:none; font-weight:inherit; }
  body[data-step="1"] .v1, body[data-step="2"] .v1, body[data-step="3"] .v1,
  body[data-step="4"] .v2, body[data-step="5"] .v2, body[data-step="6"] .v2,
  body[data-step="7"] .v3, body[data-step="8"] .v3 { display:block; }
  body[data-step="2"] .v1 .res, body[data-step="3"] .v1 .res, body[data-step="5"] .v2 .res,
  body[data-step="6"] .v2 .res, body[data-step="6"] .v2 .carry, body[data-step="8"] .v3 .meter { visibility:visible; }
  body[data-step="3"] .v1 .hl, body[data-step="5"] .v2 .hl, body[data-step="6"] .v2 .hl, body[data-step="7"] .v3 .hl,
  body[data-step="8"] .v3 .hl { background:#fff3c4; box-shadow:0 0 0 2px #fff3c4; font-weight:700; }
  body[data-step="6"] .v2 .req { display:none; }
  body[data-step="6"] .v2 > .cols { grid-template-columns:1fr; }
"""
out.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>FlowSME and Meta's Model API</title><style>{css}</style></head>
<body data-step="0">{"".join(views)}</body></html>""", encoding="utf-8")
print(out, "| paste:", paste["element"], paste["description"], "| frame:", step["screenshot"], "| rule:", rule_turn["transcript"],
      "| task:", task["name"], "| spent:", spent)
