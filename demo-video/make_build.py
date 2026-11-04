"""Build the chapter 8 page from the real Build output of the run (the zip the server returns for a task): the files
it wrote, grouped, and the n8n workflow's nodes in order.

    python make_build.py <build.zip> <out.html>"""
import html
import json
import sys
import zipfile
from pathlib import Path

zf = zipfile.ZipFile(sys.argv[1])
out = Path(sys.argv[2])
names = zf.namelist()
n8n = json.loads(zf.read("n8n_workflow.json"))
deploy = json.loads(zf.read("deploy/n8n.json"))
nodes = [n for n in n8n["nodes"] if not n["type"].endswith("stickyNote")]
order, seen = [], set()
links = {src: [c["node"] for outs in conn.get("main", []) for c in outs] for src, conn in n8n.get("connections", {}).items()}
current = next((n["name"] for n in nodes if "Trigger" in n["type"] or "trigger" in n["type"]), nodes[0]["name"])
while current and current not in seen:
    seen.add(current)
    order.append(next(n for n in nodes if n["name"] == current))
    current = (links.get(current) or [None])[0]

TOOLS = {"n8n_workflow.json": "n8n", "nodered_flow.json": "Node-RED", "make_blueprint.json": "Make",
         "power_automate_flow.json": "Power Automate", "logic_app.json": "Azure Logic Apps",
         "windmill_flow.json": "Windmill"}
groups = [("Workflows for the customer's own tool", [n for n in names if n in TOOLS]),
          ("Acceptance tests", [n for n in names if n in ("user_stories.md", "acceptance.feature")]),
          ("Tracker and KPIs", [n for n in names if n.endswith(".sql")]),
          ("Guide", [n for n in names if n == "BUILD.md"])]
cols = "".join(
    f'<div class="group g{i}"><h3>{html.escape(title)}</h3><ul>' +
    "".join(f'<li><span class="file">{html.escape(n)}</span>'
            f'{f"<b>{TOOLS[n]}</b>" if n in TOOLS else ""}</li>' for n in files) + "</ul></div>"
    for i, (title, files) in enumerate(groups))
flow = '<span class="arrow">→</span>'.join(
    f'<div class="node"><small>{html.escape(n["type"].split(".")[-1])}</small>{html.escape(n["name"])}</div>'
    for n in order)
state = "switched off" if not deploy.get("active") else "active"

out.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>FlowSME build</title>
<style>
  * {{ box-sizing:border-box; margin:0; }}
  body {{ width:1280px; height:720px; overflow:hidden; background:#f4f5f8; color:#1b1d22; padding:26px 30px;
          font:15px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif; }}
  h2 {{ font-size:22px; margin-bottom:4px; }}
  .sub {{ color:#626873; margin-bottom:18px; }}
  .grid {{ display:grid; grid-template-columns:1.25fr 1fr; gap:18px; }}
  .group {{ background:#fff; border:1px solid #e4e6eb; border-radius:14px; padding:12px 16px; margin-bottom:12px; opacity:0; }}
  .group h3 {{ font-size:13px; color:#626873; text-transform:uppercase; letter-spacing:.6px; margin-bottom:6px; }}
  .group ul {{ list-style:none; padding:0; display:grid; gap:4px; }}
  .group li {{ display:flex; justify-content:space-between; font-size:15px; }}
  .file {{ font-family:"Cascadia Code",Consolas,monospace; font-size:14px; }}
  .group b {{ color:#3355d1; }}
  .n8n {{ background:#fff; border:1px solid #e4e6eb; border-radius:14px; padding:16px 18px; opacity:0; }}
  .n8n h3 {{ font-size:13px; color:#626873; text-transform:uppercase; letter-spacing:.6px; margin-bottom:12px; }}
  .flow {{ display:flex; flex-direction:column; align-items:stretch; gap:6px; }}
  .node {{ border:2px solid #3355d1; border-radius:12px; padding:10px 14px; font-weight:600; background:#e8edfc; }}
  .node small {{ display:block; font-weight:500; color:#626873; font-size:12px; }}
  .arrow {{ text-align:center; color:#3355d1; transform:rotate(90deg); font-size:18px; }}
  .state {{ margin-top:14px; display:inline-block; background:#fef0c7; color:#b54708; font-weight:700; border-radius:999px;
            padding:3px 12px; font-size:13px; }}
  body[data-step="1"] .g0, body[data-step="2"] .g0, body[data-step="3"] .g0, body[data-step="4"] .g0,
  body[data-step="2"] .n8n, body[data-step="3"] .n8n, body[data-step="4"] .n8n,
  body[data-step="3"] .g1, body[data-step="4"] .g1, body[data-step="3"] .g2, body[data-step="4"] .g2,
  body[data-step="3"] .g3, body[data-step="4"] .g3 {{ opacity:1; }}
</style></head>
<body data-step="0">
  <h2>Build · {html.escape(n8n.get("name", "").replace("FlowSME: ", ""))}</h2>
  <p class="sub">What FlowSME wrote for task 1 of this run ({len(names)} files)</p>
  <div class="grid">
    <div>{cols}</div>
    <div class="n8n"><h3>The n8n workflow, node by node</h3><div class="flow">{flow}</div>
      <span class="state">Goes to n8n {state}: nothing runs until someone turns it on</span></div>
  </div>
</body></html>""", encoding="utf-8")
print(out, [n["name"] for n in order], state)
