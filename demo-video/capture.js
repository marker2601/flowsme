// Records a real FlowSME page (report, SOP, Node-RED...) frame by frame in headless Chrome at 1920x1080, so scrolls
// and clicks are smooth whatever the machine's speed. The clicks really happen in the page; the arrow drawn on top
// only shows where.
//
//   node capture.js scene.json
//
// scene.json: {"url", "out" (folder for PNG frames), "seconds", "fps"?, "zoom"?, "setup"? (JS run once after load),
//              "actions": [{"at": s, "do": "scroll", "to": px | "selector", "dur": s, "offset"?: px},
//                          {"at": s, "do": "move", "to": [x, y] | "selector", "dur": s},
//                          {"at": s, "do": "click", "to": "selector"}]}
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const ARROW = "data:image/svg+xml," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">' +
  '<path d="M3 2 L3 22 L8.5 17 L12.5 26 L16 24.5 L12 15.8 L19.5 15.8 Z" fill="#111" stroke="#fff" stroke-width="1.6" ' +
  'stroke-linejoin="round"/></svg>');

(async () => {
  const scene = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const fps = scene.fps || 30;
  const total = Math.round(scene.seconds * fps);
  fs.mkdirSync(scene.out, { recursive: true });
  for (const f of fs.readdirSync(scene.out)) fs.unlinkSync(path.join(scene.out, f));

  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true,
    args: ["--hide-scrollbars", "--force-color-profile=srgb", "--font-render-hinting=none"] });
  const page = await browser.newPage();
  await page.setBypassCSP(true);
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scene.dark ? "dark" : "light" }]);
  const zoom = scene.zoom || 1;
  await page.setViewport({ width: Math.round(1920 / zoom), height: Math.round(1080 / zoom), deviceScaleFactor: zoom });
  await page.goto(scene.url, { waitUntil: "networkidle0", timeout: 120000 });
  await new Promise((r) => setTimeout(r, 1500)); // diagrams finish drawing
  await page.evaluate((arrow) => {
    document.documentElement.style.scrollBehavior = "auto";
    const s = document.createElement("style");
    s.textContent = "html,body{scroll-behavior:auto !important} *{transition-duration:0s !important;animation-duration:0s !important}";
    document.head.appendChild(s);
    const c = document.createElement("img");
    c.id = "__cursor"; c.src = arrow;
    c.style.cssText = "position:fixed;left:0;top:0;width:28px;height:28px;z-index:2147483647;pointer-events:none;" +
                      "border:none;border-radius:0;cursor:default;max-width:none;display:none";
    document.body.appendChild(c);
  }, ARROW);
  if (scene.setup) await page.evaluate(scene.setup);

  // Where a selector is, in page (scroll) or viewport (cursor) terms, measured when the action starts.
  const target = (sel, kind, offset) => page.evaluate((sel, kind, offset) => {
    const el = document.querySelector(sel);
    if (!el) throw new Error("not found: " + sel);
    const r = el.getBoundingClientRect();
    if (kind === "scroll") return Math.max(0, r.top + window.scrollY - (offset || 0));
    return [r.left + Math.min(r.width / 2, 60), r.top + r.height / 2];
  }, sel, kind, offset);

  const acts = (scene.actions || []).map((a) => ({ ...a, f0: Math.round(a.at * fps), f1: Math.round((a.at + (a.dur || 0)) * fps) }));
  let cursor = scene.cursor || null; // [x, y] in viewport pixels, or hidden
  for (let f = 0; f < total; f++) {
    for (const a of acts) {
      if (f < a.f0) continue;
      if (a.do === "scroll") {
        if (f === a.f0) { a.from = await page.evaluate(() => window.scrollY);
                          a.dest = typeof a.to === "number" ? a.to : await target(a.to, "scroll", a.offset ?? 90); }
        if (f <= a.f1) { const t = a.f1 === a.f0 ? 1 : (f - a.f0) / (a.f1 - a.f0);
                         await page.evaluate((y) => window.scrollTo(0, y), a.from + (a.dest - a.from) * ease(t)); }
      } else if (a.do === "move") {
        if (f === a.f0) { a.from = cursor || [960, 1150]; a.dest = Array.isArray(a.to) ? a.to : await target(a.to, "cursor"); }
        if (f <= a.f1) { const t = a.f1 === a.f0 ? 1 : (f - a.f0) / (a.f1 - a.f0), e = ease(t);
                         cursor = [a.from[0] + (a.dest[0] - a.from[0]) * e, a.from[1] + (a.dest[1] - a.from[1]) * e]; }
      } else if (a.do === "click" && f === a.f0) {
        await page.evaluate((sel) => document.querySelector(sel).click(), a.to);
      } else if (a.do === "hide" && f === a.f0) {
        cursor = null;
      } else if (a.do === "eval" && f === a.f0) {
        await page.evaluate(a.js);
      }
    }
    await page.evaluate((c) => { const el = document.getElementById("__cursor");
      if (!c) { el.style.display = "none"; return; }
      el.style.display = "block"; el.style.transform = `translate(${c[0] - 3}px, ${c[1] - 2}px)`; }, cursor);
    await page.screenshot({ path: path.join(scene.out, String(f).padStart(5, "0") + ".png") });
  }
  await browser.close();
  console.log(`${total} frames -> ${scene.out}`);
})().catch((e) => { console.error(e); process.exit(1); });
