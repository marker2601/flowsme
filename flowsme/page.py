"""The look of every page FlowSME writes (report, SOP, before/after comparison, team view).

The content is Markdown (so report.md and sop-*.md stay readable on their own); in the browser it is rendered
and laid out: a title bar with buttons, summary cards, a contents list that follows the reader, each task as a
card with tabs instead of one long page, coloured potential and roadmap labels, sortable tables, screenshots
that open full size, and for SOPs, numbered step cards to tick off with a progress bar. Printing shows
everything, without the buttons, always in the light colours.

Each page is one file that works wherever it is sent: the Markdown renderer (marked, MIT licence) is inside it,
screenshots are embedded by the writers, and only the diagrams need the internet (they fall back to their
text). Recorded window titles and typed field names end up in the Markdown, so the rendered HTML is cleaned
of scripts, event handlers and script links before it is shown."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from functools import lru_cache
from pathlib import Path

from . import __version__

KINDS = {"report": "Workflow report", "sop": "Standard operating procedure", "compare": "Before and after",
         "team": "Team view", "page": "FlowSME"}
MERMAID = ('<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js" '
           'integrity="sha384-WmdflGW9aGfoBdHc4rRyWzYuAjEmDwMdGdiPNacbwfGKxBW/SO6guzuQ76qjnSlr" '
           'crossorigin="anonymous"></script>')
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E"
           "%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%233355d1'/%3E"
           "%3Cstop offset='1' stop-color='%237a5af8'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='32' "
           "height='32' rx='8' fill='url(%23g)'/%3E%3Cpath d='M11 8h11v4h-7v3h6v4h-6v6h-4z' fill='%23fff'/%3E"
           "%3C/svg%3E")


@lru_cache(maxsize=1)
def _marked() -> str:
    for folder in (Path(__file__).parent / "assets", Path(getattr(__import__("sys"), "_MEIPASS", "")) / "flowsme" / "assets"):
        path = folder / "marked.min.js"
        if path.is_file():
            # Inside a <script>, "<!--" and "</script" would end or confuse it; in marked they sit in regexes.
            return path.read_text(encoding="utf-8").replace("<!--", "\\x3C!--").replace("</script", "\\x3C/script")
    return ""



def _sha(script: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode() + "'"


@lru_cache(maxsize=1)
def _csp() -> str:
    """Only these scripts run: an injected <script> or onerror=... in recorded text is refused by the browser, and
    the page can't fetch or send anything, so even a missed hole can't leak the report."""
    return ("default-src 'none'; "
            f"script-src {_sha(_marked())} {_sha(SCRIPT)} https://cdn.jsdelivr.net/npm/mermaid@10.9.1/; "
            "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; "
            "base-uri 'none'; form-action 'none'")


def render(title: str, markdown: str, kind: str = "page", data: dict | None = None) -> str:
    data = data or {}
    page_title = title if "FlowSME" in title else f"{title} · FlowSME"
    description = data.get("description") or f"{KINDS.get(kind, 'Page')} made with FlowSME: {title}"
    values = {
        "TITLE": html.escape(title), "PAGE_TITLE": html.escape(page_title),
        "DESCRIPTION": html.escape(description[:300]), "KIND": kind, "KIND_LABEL": KINDS.get(kind, "FlowSME"),
        "VERSION": __version__, "FAVICON": FAVICON, "MARKED": _marked(),
        "MERMAID": MERMAID if "```mermaid" in markdown else "",
        "DATA": json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
        "MARKDOWN": re.sub(r"</(script)", r"<\\/\1", markdown, flags=re.IGNORECASE),
        "SCRIPT": SCRIPT, "CSP": _csp(),
    }
    # One pass, so text inside the Markdown that looks like a placeholder is left alone.
    return re.sub(r"__([A-Z_]+)__", lambda m: values.get(m.group(1), m.group(0)), PAGE)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<meta name="description" content="__DESCRIPTION__">
<meta name="generator" content="FlowSME __VERSION__">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="light dark">
<meta http-equiv="Content-Security-Policy" content="__CSP__">
<link rel="icon" href="__FAVICON__">
<style>
  :root {
    color-scheme:light;
    --bg:#f4f5f8; --card:#ffffff; --fg:#1b1d22; --muted:#626873; --line:#e4e6eb; --soft:#f7f8fa;
    --accent:#3355d1; --accent-soft:#e8edfc; --on-accent:#fff; --shadow:0 1px 2px rgba(16,24,40,.06), 0 1px 8px rgba(16,24,40,.04);
    --high:#b42318; --high-bg:#fee4e2; --med:#b54708; --med-bg:#fef0c7; --low:#067647; --low-bg:#dcfae6;
    --next:#175cd3; --next-bg:#d1e9ff; --hold:#475467; --hold-bg:#eaecf0;
  }
  :root[data-theme="dark"] {
    color-scheme:dark;
    --bg:#111317; --card:#1a1d23; --fg:#e7e9ee; --muted:#9aa1ad; --line:#2a2e36; --soft:#20242b;
    --accent:#8ea8ff; --accent-soft:#232c48; --on-accent:#111317; --shadow:none;
    --high:#fda29b; --high-bg:#4a1f1c; --med:#fec84b; --med-bg:#4a3510; --low:#75e0a7; --low-bg:#123b27;
    --next:#84caff; --next-bg:#14304f; --hold:#cfd4dc; --hold-bg:#2a2f37;
  }
  * { box-sizing:border-box; }
  html { --bar-h:64px; scroll-behavior:smooth; scroll-padding-top:calc(var(--bar-h) + 18px); }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:15.5px/1.6 "Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif; }
  a { color:var(--accent); }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }
  .bar { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:14px; padding:10px 22px;
         background:var(--card); border-bottom:1px solid var(--line); box-shadow:var(--shadow); }
  .brand { display:flex; align-items:center; gap:9px; font-weight:700; letter-spacing:.2px; white-space:nowrap; }
  .logo { width:26px; height:26px; border-radius:7px; background:linear-gradient(135deg,#3355d1,#7a5af8);
          display:grid; place-items:center; color:#fff; font-size:14px; flex:none; }
  .bar .title { flex:1; min-width:0; }
  .bar .title b { display:block; font-size:15px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .bar .title span { display:block; font-size:12px; color:var(--muted); overflow:hidden; text-overflow:ellipsis;
                     white-space:nowrap; }
  .btn { border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:8px;
         padding:6px 12px; font:inherit; font-size:13.5px; cursor:pointer; white-space:nowrap; }
  .btn:hover { border-color:var(--accent); color:var(--accent); }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); }
  .btn.primary:hover { color:var(--on-accent); filter:brightness(1.08); }
  .layout { display:grid; grid-template-columns:250px minmax(0,1fr); gap:24px; max-width:1320px;
            margin:0 auto; padding:24px 22px 40px; }
  body.no-toc .layout { grid-template-columns:minmax(0,1fr); max-width:1040px; }
  body.no-toc nav.toc, body.no-toc #toc-btn { display:none; }
  nav.toc { position:sticky; top:calc(var(--bar-h) + 18px); align-self:start;
            max-height:calc(100vh - var(--bar-h) - 38px); overflow:auto;
            font-size:13.5px; padding-right:6px; }
  nav.toc .label { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.8px;
                   margin:4px 10px 8px; }
  nav.toc a { display:block; padding:5px 10px; border-radius:7px; color:var(--fg); text-decoration:none;
              border-left:3px solid transparent; }
  nav.toc a.sub { padding-left:22px; color:var(--muted); font-size:12.8px; }
  nav.toc a:hover { background:var(--soft); }
  nav.toc a.on { background:var(--accent-soft); color:var(--accent); border-left-color:var(--accent); }
  main { min-width:0; }
  main p, main li, main h1, main h2, main h3, main h4, main blockquote, main sub { overflow-wrap:anywhere; }
  main td { overflow-wrap:break-word; }
  .hero { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px;
          box-shadow:var(--shadow); margin-bottom:18px; }
  .hero h1 { margin:0 0 4px; font-size:1.65rem; line-height:1.25; }
  .hero .meta { color:var(--muted); font-size:13.5px; }
  .hero blockquote { margin:14px 0 0; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-top:18px; }
  .kpi { background:var(--soft); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
  .kpi small { color:var(--muted); font-size:12px; display:block; }
  .kpi b { font-size:1.45rem; line-height:1.3; }
  .kpi.top b { font-size:1rem; font-weight:600; display:block; margin-top:3px; }
  section.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:6px 24px 18px;
                 box-shadow:var(--shadow); margin-bottom:18px; }
  section.card > h2 { font-size:1.2rem; margin:16px 0 8px; }
  h3 { font-size:1.05rem; } h4 { font-size:.98rem; }
  .table-wrap { overflow-x:auto; margin:10px 0 14px; border:1px solid var(--line); border-radius:10px; }
  table { border-collapse:collapse; width:100%; font-size:13.8px; }
  th, td { text-align:left; padding:8px 11px; border-bottom:1px solid var(--line); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  th { background:var(--soft); color:var(--muted); font-weight:600; font-size:12.5px; position:relative; }
  th.sortable { cursor:pointer; user-select:none; }
  th.sortable::after { content:"↕"; opacity:.35; margin-left:6px; font-size:11px; }
  th.asc::after { content:"↑"; opacity:.9; } th.desc::after { content:"↓"; opacity:.9; }
  tbody tr:hover td { background:var(--soft); }
  tr.go { cursor:pointer; }
  .badge { display:inline-block; padding:1px 9px; border-radius:999px; font-size:12px; font-weight:600;
           white-space:nowrap; }
  .b-high { color:var(--high); background:var(--high-bg); } .b-medium { color:var(--med); background:var(--med-bg); }
  .b-low { color:var(--low); background:var(--low-bg); } .b-now { color:var(--low); background:var(--low-bg); }
  .b-next { color:var(--next); background:var(--next-bg); } .b-later { color:var(--med); background:var(--med-bg); }
  .b-hold, .b-same { color:var(--hold); background:var(--hold-bg); }
  code, pre { font-family:"Cascadia Code",ui-monospace,Consolas,monospace; font-size:.84rem; }
  pre { background:var(--soft); border:1px solid var(--line); padding:12px 14px; overflow-x:auto; border-radius:10px; }
  .mermaid { background:#fff; color:#1b1d22; border:1px solid var(--line); border-radius:12px; padding:14px;
             overflow-x:auto; margin:12px 0; text-align:center; }
  .mermaid.off { text-align:left; background:var(--soft); color:var(--fg); }
  .mermaid.off p { margin:0 0 6px; color:var(--muted); }
  svg.chart { background:#fff; border-radius:10px; max-width:100%; }
  img { max-width:100%; border:1px solid var(--line); border-radius:8px; cursor:zoom-in; }
  blockquote { margin:12px 0; padding:10px 14px; border-left:4px solid var(--accent); background:var(--accent-soft);
               border-radius:0 10px 10px 0; color:var(--fg); }
  blockquote p { margin:0; }
  details.task { border:1px solid var(--line); border-radius:12px; margin:14px 0; background:var(--card); }
  details.task > summary { list-style:none; cursor:pointer; padding:14px 18px; display:flex; align-items:center;
                           gap:12px; flex-wrap:wrap; }
  details.task > summary::-webkit-details-marker { display:none; }
  details.task > summary::before { content:"▸"; color:var(--muted); transition:transform .15s; }
  details.task[open] > summary::before { transform:rotate(90deg); }
  details.task > summary h3 { margin:0; flex:1; min-width:200px; }
  details.task > summary .facts { color:var(--muted); font-size:13px; }
  .task-body { padding:0 18px 14px; border-top:1px solid var(--line); }
  details.task.same { background:var(--soft); }
  .tabs { display:flex; gap:4px; flex-wrap:wrap; border-bottom:1px solid var(--line); margin:12px 0 4px;
          position:sticky; top:var(--bar-h); background:var(--card); z-index:5; padding-top:6px; }
  .tab { border:none; background:none; color:var(--muted); padding:8px 12px; font:inherit; font-size:13.5px;
         border-bottom:2px solid transparent; cursor:pointer; margin-bottom:-1px; white-space:nowrap; }
  .tab:hover { color:var(--fg); }
  .tab.on { color:var(--accent); border-bottom-color:var(--accent); font-weight:600; }
  .pane[hidden] { display:none; }
  .progress { position:sticky; top:calc(var(--bar-h) + 8px); z-index:6; background:var(--card); border:1px solid var(--line);
              border-radius:12px; padding:10px 16px; margin-bottom:14px; display:flex; align-items:center; gap:14px;
              box-shadow:var(--shadow); }
  .progress .track { flex:1; height:8px; background:var(--soft); border-radius:99px; overflow:hidden; }
  .progress .fill { height:100%; width:0; background:linear-gradient(90deg,#3355d1,#12b76a); transition:width .2s; }
  .progress span { font-size:13px; color:var(--muted); white-space:nowrap; }
  article.step { display:grid; grid-template-columns:44px minmax(0,1fr) auto; gap:4px 14px; padding:16px 0;
                 border-bottom:1px solid var(--line); }
  article.step:last-of-type { border-bottom:none; }
  article.step .num { width:34px; height:34px; border-radius:50%; background:var(--accent-soft); color:var(--accent);
                      display:grid; place-items:center; font-weight:700; }
  article.step.done .num { background:var(--low-bg); color:var(--low); }
  article.step.done h3 { text-decoration:line-through; color:var(--muted); }
  article.step h3 { margin:4px 0 2px; }
  article.step img { max-width:min(560px,100%); margin-top:8px; }
  article.step label { font-size:13px; color:var(--muted); display:flex; gap:6px; align-items:center;
                       cursor:pointer; height:34px; }
  body.no-shots article.step img { display:none; }
  .lightbox { position:fixed; inset:0; background:rgba(10,12,16,.82); display:none; place-items:center; z-index:50;
              cursor:zoom-out; padding:24px; }
  .lightbox.on { display:grid; }
  .lightbox img { max-width:96vw; max-height:92vh; cursor:zoom-out; border:none; }
  sub { color:var(--muted); font-size:12.5px; }
  .foot { text-align:center; color:var(--muted); font-size:12px; padding:0 16px 28px; }
  @media (max-width:1000px) {
    .layout { grid-template-columns:minmax(0,1fr); padding:16px 12px 40px; }
    nav.toc { position:fixed; top:var(--bar-h); left:0; bottom:0; width:270px; max-height:none; background:var(--card);
              border-right:1px solid var(--line); padding:14px; transform:translateX(-105%); transition:transform .2s;
              z-index:30; }
    body.toc-open nav.toc { transform:none; }
    .bar .hide-small { display:none; }
  }
  @media (max-width:600px) {
    /* a phone has room for the logo, the title and short buttons, not the full labels */
    .bar { padding:10px 12px; gap:10px; }
    .bar .wide { display:none; }
    .tabs { flex-wrap:nowrap; overflow-x:auto; overflow-y:hidden; scrollbar-width:none; }  /* one row, not three */
    .tab { flex:none; margin-bottom:0; }
    .layout { padding:12px 10px 32px; }
    section.card { padding:4px 14px 14px; } .hero { padding:16px; } .hero h1 { font-size:1.35rem; }
    article.step { grid-template-columns:36px minmax(0,1fr); }
    article.step label { grid-column:2; height:auto; }
    .task-body { padding:0 12px 12px; } details.task > summary { padding:12px; }
  }
  @media print {
    :root, :root[data-theme="dark"] {
      color-scheme:light;
      --bg:#fff; --card:#fff; --fg:#1b1d22; --muted:#555b66; --line:#d9dce1; --soft:#f5f6f8;
      --accent:#3355d1; --accent-soft:#eef1fb; --on-accent:#fff; --shadow:none;
      --high:#b42318; --high-bg:#fee4e2; --med:#b54708; --med-bg:#fef0c7; --low:#067647; --low-bg:#dcfae6;
      --next:#175cd3; --next-bg:#d1e9ff; --hold:#475467; --hold-bg:#eaecf0;
    }
    .bar, nav.toc, .tabs, .progress, .lightbox, article.step label { display:none !important; }
    .layout { display:block; padding:0; } body { background:#fff; }
    section.card, .hero, details.task { box-shadow:none; break-inside:auto; }
    details.task > summary::before { display:none; }
    .pane[hidden] { display:block !important; }
    .pane::before { content:attr(data-title); display:block; font-weight:700; margin:12px 0 4px; }
  }
</style>
</head>
<body data-kind="__KIND__">
<header class="bar">
  <div class="brand"><span class="logo" aria-hidden="true">F</span><span class="wide">FlowSME</span></div>
  <div class="title"><b id="bar-title">__TITLE__</b><span>__KIND_LABEL__</span></div>
  <button class="btn" id="toc-btn" type="button" title="Contents" aria-label="Contents">☰<span class="wide"> Contents</span></button>
  <button class="btn hide-small" id="expand" type="button" hidden>Expand all</button>
  <button class="btn hide-small" id="collapse" type="button" hidden>Collapse all</button>
  <button class="btn hide-small" id="shots" type="button" hidden>Hide screenshots</button>
  <button class="btn" id="theme" type="button" title="Light or dark" aria-label="Switch light or dark">◐</button>
  <button class="btn primary" id="print" type="button">Print<span class="wide"> / Save PDF</span></button>
</header>
<div class="layout">
  <nav class="toc" id="toc" aria-label="Contents"><div class="label">Contents</div></nav>
  <main id="content">Loading...</main>
</div>
<footer class="foot">Made with FlowSME __VERSION__</footer>
<div class="lightbox" id="lightbox" role="dialog" aria-modal="true" aria-label="Screenshot, full size"><img alt=""></div>
<script type="application/json" id="data">__DATA__</script>
<script type="text/markdown" id="md">__MARKDOWN__</script>
<script>__MARKED__</script>
__MERMAID__
<script>__SCRIPT__</script>
</body>
</html>
"""

SCRIPT = r"""
(async () => {
  const kind = document.body.dataset.kind;
  const data = JSON.parse(document.getElementById("data").textContent || "{}");
  const main = document.getElementById("content");
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls;
                                   if (text !== undefined) e.textContent = text; return e; };
  const slug = (t, n) => "s-" + (t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || n);
  const theme = (() => { try { return localStorage.getItem("flowsme-theme"); } catch (e) { return null; } })();
  if (theme) document.documentElement.dataset.theme = theme;
  else if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark";
  // the contents, the task tabs and the progress bar stick just below the title bar, whatever its height
  const topBar = document.querySelector(".bar");
  new ResizeObserver(() => document.documentElement.style.setProperty("--bar-h", topBar.offsetHeight + "px"))
    .observe(topBar);

  // ---- the Markdown, rendered and cleaned: recorded titles must never run as code ------------------
  const URL_ATTR = /^(href|src|xlink:href|action|formaction|poster|background|data|srcset)$/;
  const SAFE_DATA = /^data:image\/(png|jpe?g|gif|webp);/;
  const clean = (root) => {
    root.querySelectorAll("script,iframe,frame,frameset,object,embed,link,meta,base,form,input,button,textarea," +
                          "select,applet,portal").forEach((n) => n.remove());
    root.querySelectorAll("style").forEach((n) => { if (!n.closest("svg")) n.remove(); });
    root.querySelectorAll("*").forEach((n) => {
      for (const a of Array.from(n.attributes)) {
        const name = a.name.toLowerCase(), v = a.value.replace(/[\u0000-\u0020]+/g, "").toLowerCase();
        if (name.startsWith("on") || name === "srcdoc" || (name === "style" && /url\(|expression/.test(v)) ||
            (URL_ATTR.test(name) && (/^(javascript|vbscript):/.test(v) || (v.startsWith("data:") && !SAFE_DATA.test(v)))))
          n.removeAttribute(a.name);
      }
    });
  };
  const source = document.getElementById("md").textContent;
  const tpl = document.createElement("template");  // inert: nothing in it loads or runs until it is cleaned
  try { tpl.innerHTML = marked.parse(source); }
  catch (e) {
    console.warn(e);
    tpl.content.append(el("p", "", "This page could not be laid out. Here is its text."), el("pre", "", source));
  }
  clean(tpl.content);
  main.replaceChildren(tpl.content);

  main.querySelectorAll("code.language-mermaid").forEach((code) => {
    const div = el("div", "mermaid", code.textContent); code.parentElement.replaceWith(div);
  });
  main.querySelectorAll("svg").forEach((svg) => { if (!svg.closest(".mermaid")) svg.classList.add("chart"); });
  const diagrams = Array.from(main.querySelectorAll("div.mermaid"));
  if (diagrams.length) {
    try {  // draw the diagrams while everything is visible, before tabs hide some of it
      if (!window.mermaid) throw new Error("The diagram library did not load (no internet?)");
      mermaid.initialize({ startOnLoad:false, theme:"default", securityLevel:"strict", flowchart:{ htmlLabels:true } });
      await mermaid.run({ nodes: diagrams });
    } catch (e) { console.warn(e); }
    diagrams.forEach((d) => {
      if (d.querySelector("svg")) return;
      const text = d.textContent, more = el("details");
      more.append(el("summary", "", "Show the diagram as text"), el("pre", "", text));
      d.className = "mermaid off";
      d.replaceChildren(el("p", "", "This diagram is drawn when the page is opened with an internet connection."), more);
    });
  }

  // ---- header: the title, the line under it, and the summary cards -----------------------------
  const kids = Array.from(main.children);
  const hero = el("div", "hero");
  const h1 = kids.find((k) => k.tagName === "H1");
  if (h1) {
    document.getElementById("bar-title").textContent = h1.textContent;
    hero.appendChild(h1);
    for (const k of Array.from(main.children)) {
      if (k.tagName === "H2") break;
      if (k === hero) continue;
      if (k.tagName === "P" && !hero.querySelector(".meta")) { k.classList.add("meta"); hero.appendChild(k); }
      else if (k.tagName === "BLOCKQUOTE" || k.tagName === "SUB" || k.tagName === "P") hero.appendChild(k);
      else break;
    }
  }
  if (kind === "report" && Array.isArray(data.kpis)) {
    const box = el("div", "kpis");
    for (const k of data.kpis) {
      const card = el("div", "kpi" + (k.wide ? " top" : ""));
      card.append(el("small", "", k.label), el("b", "", k.value));
      box.appendChild(card);
    }
    hero.appendChild(box);
  }
  main.prepend(hero);

  // ---- one card per section ------------------------------------------------------------------------
  const sections = [];
  let current = null;
  for (const k of Array.from(main.children)) {
    if (k === hero) continue;
    if (k.tagName === "H2") {
      current = el("section", "card"); current.id = slug(k.textContent, sections.length);
      k.before(current); current.appendChild(k); sections.push(current);
    } else if (current) current.appendChild(k);
    else hero.appendChild(k);
  }

  // ---- tables: scroll on small screens, coloured labels, sorting --------------------------------
  const BADGE = /^(High|Medium|Low|Now|Next|Later|Hold)\b(?!\s+(steps?|to)\b)/;
  const UNIT = { h:3600, hr:3600, hrs:3600, hour:3600, hours:3600, min:60, mins:60, minute:60, minutes:60,
                 s:1, sec:1, secs:1, second:1, seconds:1 };
  const sortValue = (cell) => {
    const t = (cell ? cell.textContent : "").split("/")[0].trim();
    const score = t.match(/^(High|Medium|Low)\b.*?(\d+)\s*\/?\s*100/) || (cell && cell.textContent.match(/\((\d+)\/100\)/));
    if (score) return parseFloat(score[score.length - 1]);
    let secs = 0, found = false;
    for (const m of t.matchAll(/(-?\d+(?:[.,]\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|min|seconds?|secs?|s)\b/gi)) {
      secs += parseFloat(m[1].replace(",", ".")) * UNIT[m[2].toLowerCase()]; found = true;
    }
    if (found) return secs;
    const n = t.match(/^[^a-z0-9-]*(-?\d[\d,]*(?:\.\d+)?)/i);
    return n ? parseFloat(n[1].replace(/,/g, "")) : t.toLowerCase();
  };
  main.querySelectorAll("table").forEach((table) => {
    const wrap = el("div", "table-wrap"); table.before(wrap); wrap.appendChild(table);
    table.querySelectorAll("td").forEach((td) => {
      const strong = td.querySelector("strong");
      const text = (strong || td).textContent.trim();
      const m = text.match(BADGE);
      if (m && text.length < 40) {
        const host = strong || td, b = el("span", "badge b-" + m[1].toLowerCase());
        b.append(...host.childNodes); host.appendChild(b);
      } else if (/^same process as task/.test(td.textContent.trim())) {
        const b = el("span", "badge b-same"); b.append(...td.childNodes); td.appendChild(b);
      }
    });
    const head = Array.from(table.querySelectorAll("thead th"));
    const body = table.tBodies[0];
    if (!body || body.rows.length < 3 || !head.length || !head[0].textContent.trim()) return;  // key/value tables stay put
    head.forEach((th, col) => {
      if (!th.textContent.trim()) return;
      th.classList.add("sortable"); th.tabIndex = 0; th.setAttribute("aria-sort", "none");
      const sort = () => {
        const asc = !th.classList.contains("asc");
        head.forEach((h) => { h.classList.remove("asc", "desc"); if (h.classList.contains("sortable")) h.setAttribute("aria-sort", "none"); });
        th.classList.add(asc ? "asc" : "desc"); th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        const rows = Array.from(body.rows).map((r) => [sortValue(r.cells[col]), r]);
        rows.sort(([x], [y]) => {
          if (typeof x !== typeof y) return typeof x === "number" ? -1 : 1;  // numbers first, either way
          return (x > y ? 1 : x < y ? -1 : 0) * (asc ? 1 : -1);
        });
        rows.forEach(([, r]) => body.appendChild(r));
      };
      th.addEventListener("click", sort);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    });
  });

  // ---- tasks: a card each, with tabs for the long parts ---------------------------------------------
  const tasksSection = sections.find((s) => /^Task details/i.test(s.querySelector("h2").textContent));
  const taskLinks = [];
  if (tasksSection) {
    let card = null;
    for (const k of Array.from(tasksSection.children)) {
      if (k.tagName === "H2") continue;
      if (k.tagName === "H3") {
        card = el("details", "task"); card.open = true;
        card.id = slug(k.textContent, taskLinks.length) + "-t";
        const summary = el("summary"); summary.appendChild(k);
        card.appendChild(summary); card.appendChild(el("div", "task-body"));
        tasksSection.appendChild(card);
        taskLinks.push({ id: card.id, text: k.textContent });
      } else if (card) card.querySelector(".task-body").appendChild(k);
    }
    tasksSection.querySelectorAll("details.task").forEach((card, n) => {
      const body = card.querySelector(".task-body");
      const first = body.firstElementChild;
      const facts = first && first.textContent.match(/Automation potential: (High|Medium|Low)[^·]*/);
      if (facts) {
        const sum = card.querySelector("summary");
        sum.appendChild(el("span", "badge b-" + facts[1].toLowerCase(), facts[0].replace("Automation potential: ", "")));
        const rest = first.textContent.split("·").slice(1).join(" · ").trim();
        if (rest) sum.appendChild(el("span", "facts", rest));
        first.remove();
      }
      if (/^\s*The same process as task/.test(body.textContent)) {
        card.classList.add("same"); card.open = false;
        card.querySelector("summary").appendChild(el("span", "badge b-same", "same process"));
      }
      const parts = []; let pane = el("div", "pane"); pane.dataset.title = "Overview"; parts.push(pane);
      for (const k of Array.from(body.children)) {
        if (k.tagName === "H4") {
          pane = el("div", "pane"); pane.dataset.title = k.textContent.replace(/:$/, ""); parts.push(pane);
          k.remove();
        } else pane.appendChild(k);
      }
      if (parts.length < 2) { body.append(...parts); return; }
      const bar = el("div", "tabs"); bar.setAttribute("role", "tablist");
      const SHORT = [[/^Data flow/i, "Data flow"], [/^How the systems/i, "Connections"],
                     [/automation sequence/i, "Automation"], [/^To-be/i, "To-be"], [/^Business rules/i, "Rules"],
                     [/^User stories/i, "User stories"], [/Lean Six Sigma/i, "Lean Six Sigma"],
                     [/^Recommend/i, "Recommendations"], [/^Tool plan|^Tools/i, "Tools"]];
      const tabs = parts.map((p, i) => {
        const label = (SHORT.find(([re]) => re.test(p.dataset.title)) || [null, p.dataset.title])[1];
        const b = el("button", "tab" + (i ? "" : " on"), label); b.type = "button";
        b.title = p.dataset.title; b.id = `t${n}-${i}`; p.id = `p${n}-${i}`;
        b.setAttribute("role", "tab"); b.setAttribute("aria-controls", p.id);
        b.setAttribute("aria-selected", i ? "false" : "true"); b.tabIndex = i ? -1 : 0;
        p.setAttribute("role", "tabpanel"); p.setAttribute("aria-labelledby", b.id);
        bar.appendChild(b); p.hidden = i > 0;
        return b;
      });
      const show = (i) => {
        tabs.forEach((t, j) => { t.classList.toggle("on", i === j); t.setAttribute("aria-selected", i === j);
                                 t.tabIndex = i === j ? 0 : -1; });
        parts.forEach((q, j) => { q.hidden = j !== i; });
      };
      tabs.forEach((b, i) => {
        b.addEventListener("click", () => show(i));
        b.addEventListener("keydown", (e) => {
          const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
          if (step) { const j = (i + step + tabs.length) % tabs.length; show(j); tabs[j].focus(); }
        });
      });
      body.append(bar, ...parts);
    });
    // a row of the opportunities table opens its task
    main.querySelectorAll("table").forEach((table) => {
      const head = Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent.trim());
      if (head[0] !== "#") return;
      Array.from(table.tBodies[0] ? table.tBodies[0].rows : []).forEach((row) => {
        const id = row.cells[0].textContent.trim();
        const target = taskLinks.find((t) => t.text.startsWith(id + "."));
        if (!target) return;
        row.classList.add("go"); row.title = "Open this task"; row.tabIndex = 0;
        const go = () => { const card = document.getElementById(target.id); card.open = true; card.scrollIntoView(); };
        row.addEventListener("click", go);
        row.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      });
    });
    const ex = document.getElementById("expand"), col = document.getElementById("collapse");
    ex.hidden = col.hidden = false;
    ex.onclick = () => main.querySelectorAll("details.task").forEach((d) => { d.open = true; });
    col.onclick = () => main.querySelectorAll("details.task").forEach((d) => { d.open = false; });
  }

  // ---- SOP: numbered step cards to tick off -----------------------------------------------------
  const procedure = sections.find((s) => /^Procedure/i.test(s.querySelector("h2").textContent));
  if (kind === "sop" && procedure) {
    const key = "flowsme-sop:" + (data.key || location.pathname + document.title);
    let ticked = {}; try { ticked = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) {}
    const steps = []; let step = null;
    for (const k of Array.from(procedure.children)) {
      if (k.tagName === "H2") continue;
      if (k.tagName === "H3") {
        const m = k.textContent.match(/^(\d+)\.\s*(.*)$/);
        const n = m ? m[1] : String(steps.length + 1);
        step = el("article", "step");
        step.appendChild(el("div", "num", n));
        const body = el("div"); if (m) k.textContent = m[2]; body.appendChild(k); step.appendChild(body);
        const label = el("label"); const box = el("input"); box.type = "checkbox";
        box.setAttribute("aria-label", "Step " + n + " done");
        label.append(box, document.createTextNode("Done")); step.appendChild(label);
        procedure.appendChild(step); steps.push({ step, box, n });
      } else if (step) step.children[1].appendChild(k);
    }
    const bar = el("div", "progress");
    bar.innerHTML = '<b>Progress</b><div class="track"><div class="fill"></div></div><span></span>' +
                    '<button class="btn" type="button" title="Clear the ticks for the next item">Start again</button>';
    procedure.querySelector("h2").after(bar);
    const update = () => {
      const done = steps.filter((s) => s.box.checked).length;
      bar.querySelector(".fill").style.width = (steps.length ? 100 * done / steps.length : 0) + "%";
      bar.querySelector("span").textContent = done + " of " + steps.length + " steps done";
      try { localStorage.setItem(key, JSON.stringify(Object.fromEntries(steps.map((s) => [s.n, s.box.checked])))); }
      catch (e) {}
    };
    steps.forEach((s) => { s.box.checked = !!ticked[s.n]; s.step.classList.toggle("done", s.box.checked);
      s.box.addEventListener("change", () => { s.step.classList.toggle("done", s.box.checked); update(); }); });
    bar.querySelector("button").onclick = () => { steps.forEach((s) => { s.box.checked = false;
      s.step.classList.remove("done"); }); update(); };
    update();
    const shots = document.getElementById("shots"); shots.hidden = false;
    shots.onclick = () => { document.body.classList.toggle("no-shots");
      shots.textContent = document.body.classList.contains("no-shots") ? "Show screenshots" : "Hide screenshots"; };
  }

  // ---- contents list that follows the reader ---------------------------------------------------
  const toc = document.getElementById("toc");
  const links = [];
  sections.forEach((s) => {
    const a = el("a", "", s.querySelector("h2").textContent); a.href = "#" + s.id; toc.appendChild(a);
    links.push([s, a]);
    if (s === tasksSection) taskLinks.forEach((t) => { const b = el("a", "sub", t.text); b.href = "#" + t.id;
      b.onclick = () => { document.getElementById(t.id).open = true; }; toc.appendChild(b); });
  });
  if (!sections.length) document.body.classList.add("no-toc");
  const seen = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) links.forEach(([s, a]) => a.classList.toggle("on", s === e.target)); });
  }, { rootMargin: "-80px 0px -70% 0px" });
  sections.forEach((s) => seen.observe(s));
  document.getElementById("toc-btn").onclick = () => document.body.classList.toggle("toc-open");
  toc.addEventListener("click", (e) => { if (e.target.tagName === "A") document.body.classList.remove("toc-open"); });

  // ---- screenshots full size, theme, print ---------------------------------------------------------
  const box = document.getElementById("lightbox");
  const zoom = (img) => { box.querySelector("img").src = img.src; box.querySelector("img").alt = img.alt;
                          box.classList.add("on"); };
  main.querySelectorAll("img").forEach((img) => { if (!img.closest(".mermaid")) img.tabIndex = 0; });
  main.addEventListener("click", (e) => { if (e.target.tagName === "IMG" && !e.target.closest(".mermaid")) zoom(e.target); });
  main.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName === "IMG") zoom(e.target); });
  box.onclick = () => box.classList.remove("on");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") box.classList.remove("on"); });
  document.getElementById("theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("flowsme-theme", next); } catch (e) {}
  };
  const openAll = () => main.querySelectorAll("details.task").forEach((d) => { d.open = true; });
  window.addEventListener("beforeprint", openAll);  // Ctrl+P prints every task too
  document.getElementById("print").onclick = () => { openAll(); window.print(); };
})();
"""
