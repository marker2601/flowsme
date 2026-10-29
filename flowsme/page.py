"""The look of every page FlowSME writes (report, SOP, before/after comparison, team view).

The content is Markdown (so report.md and sop-*.md stay readable on their own); in the browser it is rendered
and laid out: a title bar with buttons, summary cards, a contents list that follows the reader, each task as a
card with tabs instead of one long page, coloured potential and roadmap labels, sortable tables, screenshots
that open full size, and for SOPs, numbered step cards to tick off with a progress bar. Printing shows
everything, without the buttons."""
from __future__ import annotations

import html
import json

KINDS = {"report": "Workflow report", "sop": "Standard operating procedure", "compare": "Before and after",
         "team": "Team view", "page": "FlowSME"}


def render(title: str, markdown: str, kind: str = "page", data: dict | None = None) -> str:
    safe = markdown.replace("</script", "<\\/script")
    payload = json.dumps(data or {}, ensure_ascii=False).replace("</", "<\\/")
    return (PAGE.replace("__TITLE__", html.escape(title)).replace("__KIND__", kind)
            .replace("__KIND_LABEL__", KINDS.get(kind, "FlowSME"))
            .replace("__DATA__", payload).replace("__MARKDOWN__", safe))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg:#f4f5f8; --card:#ffffff; --fg:#1b1d22; --muted:#626873; --line:#e4e6eb; --soft:#f7f8fa;
    --accent:#3355d1; --accent-soft:#e8edfc; --on-accent:#fff; --shadow:0 1px 2px rgba(16,24,40,.06), 0 1px 8px rgba(16,24,40,.04);
    --high:#b42318; --high-bg:#fee4e2; --med:#b54708; --med-bg:#fef0c7; --low:#067647; --low-bg:#dcfae6;
    --next:#175cd3; --next-bg:#d1e9ff; --hold:#475467; --hold-bg:#eaecf0;
  }
  :root[data-theme="dark"] {
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
  .bar { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:14px; padding:10px 22px;
         background:var(--card); border-bottom:1px solid var(--line); box-shadow:var(--shadow); }
  .brand { display:flex; align-items:center; gap:9px; font-weight:700; letter-spacing:.2px; white-space:nowrap; }
  .logo { width:26px; height:26px; border-radius:7px; background:linear-gradient(135deg,#3355d1,#7a5af8);
          display:grid; place-items:center; color:#fff; font-size:14px; }
  .bar .title { flex:1; min-width:0; }
  .bar .title b { display:block; font-size:15px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .bar .title span { display:block; font-size:12px; color:var(--muted); overflow:hidden; text-overflow:ellipsis;
                     white-space:nowrap; }
  .btn { border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:8px;
         padding:6px 12px; font:inherit; font-size:13.5px; cursor:pointer; white-space:nowrap; }
  .btn:hover { border-color:var(--accent); color:var(--accent); }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); }
  .layout { display:grid; grid-template-columns:250px minmax(0,1fr); gap:24px; max-width:1320px;
            margin:0 auto; padding:24px 22px 80px; }
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
  .mermaid { background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px; overflow-x:auto;
             margin:12px 0; text-align:center; }
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
  details.task > summary h3 { margin:0; flex:1; min-width:220px; }
  details.task > summary .facts { color:var(--muted); font-size:13px; }
  .task-body { padding:0 18px 14px; border-top:1px solid var(--line); }
  details.task.same { background:var(--soft); }
  .tabs { display:flex; gap:4px; flex-wrap:wrap; border-bottom:1px solid var(--line); margin:12px 0 4px;
          position:sticky; top:var(--bar-h); background:var(--card); z-index:5; padding-top:6px; }
  .tab { border:none; background:none; color:var(--muted); padding:8px 12px; font:inherit; font-size:13.5px;
         border-bottom:2px solid transparent; cursor:pointer; margin-bottom:-1px; }
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
  @media (max-width:1000px) {
    .layout { grid-template-columns:minmax(0,1fr); padding:16px 12px 60px; }
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
  }
  @media print {
    .bar, nav.toc, .tabs, .progress, .lightbox, article.step label { display:none !important; }
    .layout { display:block; padding:0; } body { background:#fff; }
    section.card, .hero, details.task { box-shadow:none; break-inside:auto; }
    .pane[hidden] { display:block !important; }
    .pane::before { content:attr(data-title); display:block; font-weight:700; margin:12px 0 4px; }
  }
</style>
</head>
<body data-kind="__KIND__">
<header class="bar">
  <div class="brand"><span class="logo">F</span><span class="wide">FlowSME</span></div>
  <div class="title"><b id="bar-title">__TITLE__</b><span>__KIND_LABEL__</span></div>
  <button class="btn" id="toc-btn" title="Contents" aria-label="Contents">☰<span class="wide"> Contents</span></button>
  <button class="btn hide-small" id="expand" hidden>Expand all</button>
  <button class="btn hide-small" id="collapse" hidden>Collapse all</button>
  <button class="btn hide-small" id="shots" hidden>Hide screenshots</button>
  <button class="btn" id="theme" title="Light or dark">◐</button>
  <button class="btn primary" id="print">Print<span class="wide"> / Save PDF</span></button>
</header>
<div class="layout">
  <nav class="toc" id="toc"><div class="label">Contents</div></nav>
  <main id="content">Loading...</main>
</div>
<div class="lightbox" id="lightbox"><img alt=""></div>
<script type="application/json" id="data">__DATA__</script>
<script type="text/markdown" id="md">__MARKDOWN__</script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
(async () => {
  const kind = document.body.dataset.kind;
  const data = JSON.parse(document.getElementById("data").textContent || "{}");
  const main = document.getElementById("content");
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls;
                                   if (html !== undefined) e.innerHTML = html; return e; };
  const slug = (t, n) => "s-" + (t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || n);
  const theme = (() => { try { return localStorage.getItem("flowsme-theme"); } catch (e) { return null; } })();
  if (theme) document.documentElement.dataset.theme = theme;
  else if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark";
  // the contents, the task tabs and the progress bar stick just below the title bar, whatever its height
  const topBar = document.querySelector(".bar");
  new ResizeObserver(() => document.documentElement.style.setProperty("--bar-h", topBar.offsetHeight + "px"))
    .observe(topBar);

  main.innerHTML = marked.parse(document.getElementById("md").textContent);
  main.querySelectorAll("code.language-mermaid").forEach((code) => {
    const div = el("div", "mermaid"); div.textContent = code.textContent; code.parentElement.replaceWith(div);
  });
  try {  // draw the diagrams while everything is visible, before tabs hide some of it
    mermaid.initialize({ startOnLoad:false, theme:"default", flowchart:{ htmlLabels:true } });
    await mermaid.run();
  } catch (e) { console.warn(e); }

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
  if (kind === "report" && data.kpis) {
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
  const BADGE = /^(High|Medium|Low|Now|Next|Later|Hold)\\b/;
  main.querySelectorAll("table").forEach((table) => {
    const wrap = el("div", "table-wrap"); table.before(wrap); wrap.appendChild(table);
    table.querySelectorAll("td").forEach((td) => {
      const strong = td.querySelector("strong");
      const text = (strong || td).textContent.trim();
      const m = text.match(BADGE);
      if (m && text.length < 40) {
        const b = el("span", "badge b-" + m[1].toLowerCase(), (strong || td).innerHTML);
        (strong || td).innerHTML = ""; (strong || td).appendChild(b);
      } else if (/^same process as task/.test(td.textContent.trim())) {
        td.innerHTML = '<span class="badge b-same">' + td.innerHTML + "</span>";
      }
    });
    const head = table.querySelectorAll("thead th");
    const body = table.tBodies[0];
    if (!body || body.rows.length < 3) return;
    head.forEach((th, col) => {
      if (!th.textContent.trim()) return;
      th.classList.add("sortable");
      th.addEventListener("click", () => {
        const asc = !th.classList.contains("asc");
        head.forEach((h) => h.classList.remove("asc", "desc"));
        th.classList.add(asc ? "asc" : "desc");
        const value = (r) => { const t = (r.cells[col] || {}).textContent || "";
                               const n = parseFloat(t.replace(/[^0-9.\\-]/g, "")); return isNaN(n) ? t.toLowerCase() : n; };
        const rows = Array.from(body.rows).sort((a, b) => { const x = value(a), y = value(b);
                               return (x > y ? 1 : x < y ? -1 : 0) * (asc ? 1 : -1); });
        rows.forEach((r) => body.appendChild(r));
      });
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
    tasksSection.querySelectorAll("details.task").forEach((card) => {
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
      if (/^\\s*The same process as task/.test(body.textContent)) {
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
                     [/^Recommend/i, "Recommendation"], [/^Tool plan|^Tools/i, "Tools"]];
      parts.forEach((p, i) => {
        const label = (SHORT.find(([re]) => re.test(p.dataset.title)) || [null, p.dataset.title])[1];
        const b = el("button", "tab" + (i ? "" : " on"), label); b.type = "button";
        b.title = p.dataset.title;
        b.setAttribute("role", "tab");
        b.addEventListener("click", () => { bar.querySelectorAll(".tab").forEach((t) => t.classList.remove("on"));
          b.classList.add("on"); parts.forEach((q, j) => { q.hidden = j !== i; }); });
        bar.appendChild(b); p.hidden = i > 0;
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
        row.classList.add("go"); row.title = "Open this task";
        row.addEventListener("click", () => { const card = document.getElementById(target.id); card.open = true;
                                              card.scrollIntoView(); });
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
    const key = "flowsme-sop:" + document.title;
    let ticked = {}; try { ticked = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) {}
    const steps = []; let step = null;
    for (const k of Array.from(procedure.children)) {
      if (k.tagName === "H2") continue;
      if (k.tagName === "H3") {
        const m = k.textContent.match(/^(\\d+)\\.\\s*(.*)$/);
        step = el("article", "step");
        step.appendChild(el("div", "num", m ? m[1] : steps.length + 1));
        const body = el("div"); if (m) k.textContent = m[2]; body.appendChild(k); step.appendChild(body);
        const label = el("label"); const box = el("input"); box.type = "checkbox";
        label.append(box, document.createTextNode("Done")); step.appendChild(label);
        procedure.appendChild(step); steps.push({ step, box, n: m ? m[1] : steps.length + 1 });
      } else if (step) step.children[1].appendChild(k);
    }
    const bar = el("div", "progress", '<b>Progress</b><div class="track"><div class="fill"></div></div>' +
                   '<span></span><button class="btn" type="button">Reset</button>');
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
  if (!sections.length) document.getElementById("toc-btn").hidden = true;
  const seen = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) links.forEach(([s, a]) => a.classList.toggle("on", s === e.target)); });
  }, { rootMargin: "-80px 0px -70% 0px" });
  sections.forEach((s) => seen.observe(s));
  document.getElementById("toc-btn").onclick = () => document.body.classList.toggle("toc-open");
  toc.addEventListener("click", (e) => { if (e.target.tagName === "A") document.body.classList.remove("toc-open"); });

  // ---- screenshots full size, theme, print ---------------------------------------------------------
  const box = document.getElementById("lightbox");
  main.addEventListener("click", (e) => { if (e.target.tagName === "IMG" && !e.target.closest(".mermaid")) {
    box.querySelector("img").src = e.target.src; box.classList.add("on"); } });
  box.onclick = () => box.classList.remove("on");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") box.classList.remove("on"); });
  document.getElementById("theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("flowsme-theme", next); } catch (e) {}
  };
  document.getElementById("print").onclick = () => {
    main.querySelectorAll("details.task").forEach((d) => { d.open = true; });
    window.print();
  };
})();
</script>
</body>
</html>
"""
