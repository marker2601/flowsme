"""Build the "what the server did" page for chapter 4 from a real run: the server's log lines, frames from the video
with the sentence Muse Spark wrote for each, and what Muse Voice Transcribe heard. Nothing on it is typed by hand.

    python make_panel.py <session folder> <recording.json> <out.html>

capture.js then reveals it piece by piece by setting <body data-step="N">."""
import base64
import html
import json
import sys
from pathlib import Path

session, record_file, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
record = json.loads(record_file.read_text(encoding="utf-8"))
analysis = json.loads((session / "analysis" / "analysis.json").read_text(encoding="utf-8"))
said = json.loads((session / "analysis" / "descriptions.json").read_text(encoding="utf-8"))
turns = json.loads((session / "analysis" / "narration.json").read_text(encoding="utf-8"))["turns"]

# The first copy-and-paste of an invoice: a step in Notepad, then the steps that put values into Excel.
steps = [s for s in analysis["steps"] if str(s["index"]) in said["steps"] and s.get("screenshot")][:4]
rule = next(t for t in turns if "30" in t["text"])


def img(rel: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode((session / rel).read_bytes()).decode()


log = [line for line in record["log"] if not line.startswith("AI usage")][:6]
cards = "".join(
    f'<figure class="shot s{i}"><img src="{img(s["screenshot"])}" alt=""><figcaption><b>Step {i + 1} · '
    f'{html.escape(s["app"])}</b>{html.escape(said["steps"][str(s["index"])])}'
    f'</figcaption></figure>' for i, s in enumerate(steps))
lines = "".join(f'<li class="l{i}">{html.escape(line)}</li>' for i, line in enumerate(log))
m, sec = divmod(int(rule["start"]), 60)

out.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>FlowSME server run</title>
<style>
  * {{ box-sizing:border-box; margin:0; }}
  body {{ width:1280px; height:720px; overflow:hidden; background:#f4f5f8; color:#1b1d22;
          font:15px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif; padding:22px 26px; }}
  h2 {{ font-size:15px; color:#626873; font-weight:600; letter-spacing:.3px; margin-bottom:8px; }}
  .log {{ background:#14161b; color:#d7dbe4; border-radius:12px; padding:12px 16px; list-style:none; height:168px;
          font:13px/1.65 "Cascadia Code",Consolas,monospace; }}
  .log li {{ opacity:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .log li::before {{ content:"› "; color:#8ea8ff; }}
  .row {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:16px; }}
  .tag {{ display:inline-block; font-size:12px; font-weight:700; color:#fff; border-radius:999px; padding:2px 10px;
          background:linear-gradient(135deg,#3355d1,#7a5af8); margin-right:8px; }}
  .shots {{ position:relative; height:420px; }}
  .shot {{ position:absolute; inset:0; opacity:0; background:#fff; border:1px solid #e4e6eb; border-radius:14px;
           overflow:hidden; display:flex; flex-direction:column; }}
  .shot img {{ width:100%; height:330px; object-fit:contain; background:#1f1f1f; }}
  .shot figcaption {{ padding:10px 14px; font-size:16px; }}
  .shot figcaption b {{ display:block; font-size:12.5px; color:#3355d1; margin-bottom:2px; }}
  .voice {{ background:#fff; border:1px solid #e4e6eb; border-radius:14px; padding:18px 20px; height:420px; opacity:0; }}
  .voice blockquote {{ margin-top:16px; font-size:22px; line-height:1.4; border-left:4px solid #3355d1; padding:8px 16px;
                       background:#e8edfc; border-radius:0 10px 10px 0; }}
  .voice .when {{ color:#626873; font-size:14px; margin-top:10px; }}
  .voice .then {{ margin-top:26px; font-size:17px; opacity:0; }}
  .voice .then b {{ color:#067647; }}
  {"".join(f'body[data-step="{n}"] .l{i}, ' for n in range(1, 12) for i in range(min(n, len(log))))} x {{ opacity:1; }}
  {"".join(f'body[data-step="{n}"] .s{min(n - 1, len(steps) - 1)}, ' for n in range(1, len(steps) + 1))} x {{ opacity:1; }}
  {"".join(f'body[data-step="{n}"] .s{len(steps) - 1}, body[data-step="{n}"] .voice, ' for n in range(len(steps) + 1, 12))} x {{ opacity:1; }}
  {"".join(f'body[data-step="{n}"] .then, ' for n in range(len(steps) + 2, 12))} x {{ opacity:1; }}
</style></head>
<body data-step="0">
  <h2>FlowSME server · log of this run</h2>
  <ul class="log">{lines}</ul>
  <div class="row">
    <div><h2><span class="tag">Muse Spark</span>reads each screen change</h2><div class="shots">{cards}</div></div>
    <div><h2><span class="tag">Muse Voice Transcribe</span>hears the narration</h2>
      <div class="voice"><div class="when">At {m}:{sec:02d} in the video</div>
        <blockquote>“{html.escape(rule["text"].strip())}”</blockquote>
        <p class="then">In the report, the top recommendation works out <b>due date = invoice date + 30 days</b>.
          </p></div></div>
  </div>
</body></html>""", encoding="utf-8")
print(out, len(steps), "steps,", len(log), "log lines")
