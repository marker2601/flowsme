# FlowSME website

A one-page static site: plain HTML, CSS and a little JavaScript, no framework and no build step. Open
`index.html` in a browser to see it.

| File | What it is |
|---|---|
| `index.html` | The page: content, schema.org data (Organization, WebSite, SoftwareApplication, FAQPage), Open Graph and Twitter tags. The FAQ answers in the schema must match the FAQ on the page. |
| `assets/site.css` | Styles. Colours are tokens at the top, light and dark, echoing the report pages (`flowsme/page.py`). |
| `assets/site.js` | Enhancements only; the page reads fine without it. Mobile menu, the navigation that follows the reader, the hero animation, the sample explorer's tabs, filters and sorting, the savings calculator, the manager/employee switch, light/dark. |
| `sample-report.html` | The demo example's report (synthetic data) on FlowSME's current report page, screenshots included, with a link back to the site. `noindex`. |
| `og-image.png`, `favicon.svg`, `apple-touch-icon.png`, `icon-512.png` | Link preview image and icons. |
| `robots.txt`, `sitemap.xml`, `_headers`, `404.html` | Crawling, security and cache headers, and the not-found page. |

The sample explorer on the home page shows the same demo data as `sample-report.html` (tasks, the 23 steps of one
run, the data flow and the proposed automation). If the example is regenerated, update both.

## Before going live

The domain and the contact address are placeholders. Replace them everywhere in this folder:

- `https://flowsme.example` with the real address (in `index.html`, `robots.txt`, `sitemap.xml`)
- `hello@flowsme.example` with the real contact address (in `index.html`)

When `assets/site.css` or `assets/site.js` change, bump `?v=2` where `index.html` and `404.html` link them.

## Deploy

- **Cloudflare Pages:** create a project, connect the repository, leave the build command empty and set the
  output directory to `site`. Or upload the folder with `npx wrangler pages deploy site`.
- **Netlify:** set the publish directory to `site` with no build command, or drag the folder onto
  app.netlify.com/drop.

## Rebuilding the sample report

`sample-report.html` is `examples/demo-invoice-processing/analysis/report.md` rendered by `flowsme.page.render`,
with the same summary cards `report.py` makes, its screenshots inside the page (as in every report), `noindex`
and a link back to the site. From the repository root:

```python
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from flowsme.models import fmt_minutes
from flowsme.page import render
from flowsme.report import _inline_shots

example = Path("examples/demo-invoice-processing")
data = json.loads((example / "analysis" / "analysis.json").read_text(encoding="utf-8"))
md = _inline_shots(example, (example / "analysis" / "report.md").read_text(encoding="utf-8"))
main = [t for t in data["tasks"] if not t["misc"]]
processes = [t for t in main if not t["same_as"]]
total = sum(s["duration"] for s in data["steps"]) / 60
saved = sum(t["estimate"].get("minutes_saved_per_run", 0) for t in main)
top = max(processes, key=lambda t: t["score"])
kpis = [{"label": "Recorded work", "value": fmt_minutes(total)},
        {"label": "Could be saved per recording", "value": f"{fmt_minutes(saved)} ({saved / total * 100:.0f}%)"},
        {"label": "Hours saved a month (done 20× a month)", "value": f"{sum(t['estimate'].get('hours_saved_per_month', 0) for t in main):.1f} h"},
        {"label": "Worth a month", "value": f"${sum(t['estimate'].get('cost_saved_per_month', 0) for t in main):,}"},
        {"label": "Processes found", "value": str(len(processes))},
        {"label": f"Biggest opportunity · {top['level'].lower()} potential", "value": top["name"], "wide": True}]
page = render(md.splitlines()[0].lstrip("# ").strip(), md, "report", {"kpis": kpis})
page = page.replace('initial-scale=1">', 'initial-scale=1">\n<meta name="robots" content="noindex">', 1)
page = page.replace('<div class="brand"><span class="logo" aria-hidden="true">F</span><span class="wide">FlowSME</span></div>',
                    '<a class="brand" href="index.html" title="Back to the FlowSME website" style="color:inherit;'
                    'text-decoration:none"><span class="logo" aria-hidden="true">F</span><span class="wide">FlowSME</span></a>')
page = page.replace('<button class="btn" id="toc-btn"', '<a class="btn hide-small" href="index.html" '
                    'style="text-decoration:none">&larr; Website</a>\n  <button class="btn" id="toc-btn"')
Path("site/sample-report.html").write_text(page, encoding="utf-8")
```
