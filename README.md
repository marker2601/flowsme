# FlowSME

Record yourself doing a piece of office work. FlowSME turns the recording into a workflow map, shows which
applications you used and how data moved between them, scores each task for automation potential, and
recommends how to automate it, with links to existing tools and a draft automation to start from.

It works like a process consultant shadowing you. It sees what you click, type and copy, not just which apps
were open.

## Quick start (Windows)

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

See it work without recording anything. This builds a synthetic invoice-processing session and analyses it:

```bash
.venv/Scripts/python.exe -m flowsme demo --offline
```

Then open `sessions/demo-invoice-processing/analysis/report.html` in a browser.

Record your own work:

```bash
.venv/Scripts/python.exe -m flowsme record --name "invoice processing"
```

- **Ctrl+Alt+P** pauses and resumes (use it for anything private).
- **Ctrl+Alt+Q** stops. Ctrl+C in the terminal also stops.
- Add `--narrate` to also record the microphone, and explain what you do and why as you work ("the due date
  is the invoice date plus 30 days"). The analysis transcribes it with Muse Voice Transcribe (about $0.18 an
  hour of audio) and uses it to answer the questions the clicks alone can't: where a typed value comes from,
  and the rule behind it. Every answer must quote your words exactly, or it is rejected. A rule you say once
  applies to the same field in every task of the recording.

Analyse the most recent recording:

```bash
.venv/Scripts/python.exe -m flowsme analyze
```

Other commands: `sessions` lists recordings, and `analyze <session-id>` analyses a specific one. Useful flags:
`--offline` (no AI), `--no-search` (no web search), `--runs-per-month 20` and `--hourly-cost 30`.

## Using an AI model (Llama)

Without a model, FlowSME uses built-in heuristics. Steps are named from the UI element and window, and the
recommendations come from rules. With a model you get plain-English step descriptions read from the
screenshots, task names and summaries, and recommendations specific to the task, including a code draft.

Copy `config.example.toml` to `config.toml` and set `provider = "openai_compatible"`. Any OpenAI-style
endpoint works:

| Option | Settings | Privacy |
|---|---|---|
| **Meta Model API (Muse Spark)** | Ready-made: `config.meta.toml` (base URL `https://api.meta.ai/v1`, model `muse-spark-1.3`). Set your key with `setx MODEL_API_KEY "..."`, then run with `--config config.meta.toml` | Screenshots are sent to Meta. Use standard models, not "contributor" ones, for customer recordings |
| **Llama on your PC (Ollama)** | `base_url = "http://localhost:11434/v1"`; run `ollama pull llama3.2-vision` and `ollama pull llama3.1:8b` | Nothing leaves your computer |
| **Hosted Llama (Together AI, DeepInfra...)** | For example `base_url = "https://api.together.xyz/v1"`, a Llama 4 model from the host's list, and your key in the `LLM_API_KEY` environment variable | Screenshots are sent to that host |
| OpenRouter, vLLM, other hosts | Their OpenAI-compatible URL and model names | Sent to that provider |

Meta retired its own hosted Llama API in July 2026. Llama models are still available from third-party hosts
and for download.

**Two models, two endpoints.** Screenshots (many small requests) and text work (a few requests for task names
and recommendations) can use different models on different endpoints. A good mix is a cheap or local vision
model with a stronger text model:

```toml
base_url = "https://openrouter.ai/api/v1"      # text: task names, recommendations
text_model = "<a strong model>"
vision_base_url = "http://localhost:11434/v1"  # screenshots stay on your PC
vision_model = "qwen3.5:4b"
```

Or set `send_screenshots = false` to keep screenshots local entirely and send only text (window titles and
element names).

**Speed settings**

- `concurrency`: how many requests run at once. With a hosted API, 8-16 is fine. A single local GPU runs
  requests one at a time anyway, so a higher number doesn't help there.
- `screenshots_per_call`: consecutive screenshots sent together, plus the screen right after them, so the
  model can see what was typed or changed. This cuts requests by about two thirds. Use 1 for models that
  accept only one image, such as `llama3.2-vision`.
- `vision_image_width`: 1024 for hosted models. 768 keeps several screenshots inside a local model's 4k
  context.

On a 19-minute demo, against an API answering in 1 second per request: 89 one-by-one requests took 92 s; 31
grouped requests, 8 at a time, took 4 s. On a local 6 GB laptop GPU both modes take about 3 minutes, because
the GPU is the bottleneck.

**Troubleshooting**

- `unknown model architecture: 'mllama'`: recent Ollama builds can't run `llama3.2-vision`. Either set
  `send_screenshots = false` (Llama 3.1 still handles all the text work) or set `vision_model` to a vision
  model your Ollama supports.
- If a model fails three times in a row, only that model is switched off for the run. The others keep
  working, and the report header says which models were actually used.
- "Ran out of tokens before answering" means the model is a "thinking" model (Qwen, DeepSeek...). Set
  `reasoning_effort = "none"`.
- Small models (8B and under) can name tools or APIs that don't exist. Check every recommendation, or use a
  larger model for the text work.

**Cost control:** a repeated routine is described once, and later runs reuse that description.
Descriptions are cached in `analysis/descriptions.json`, so re-running an analysis is cheap.
`max_vision_steps` caps the number of screenshots described per run. Each grouped request also carries the
screen after the group, which adds about 30% more images (at `screenshots_per_call = 3`) but far fewer
requests. A 30-60 minute recording costs roughly $0.02-0.25 on Llama 4 or similar budget models.

## Your tools, minutes saved, and why

Tell FlowSME which tools the customer already has:

```bash
.venv/Scripts/python.exe -m flowsme analyze --tools "Microsoft 365, Power Automate, Power BI"
```

You can also set `tools = [...]` under `[customer]` in `config.toml`. Tools outside the catalog can be
described by what they do, separated with `;`: `--tools "n8n; MyERP: database, reporting"`.

For each promising task, the report adds a Lean Six Sigma section (Define, Measure, Analyze, Improve,
Control):

- **Define:** what must go right (critical-to-quality items) and a SIPOC outline (suppliers, inputs,
  process, outputs, customers).
- **Measure:** minutes per item split into value-adding, business-required and waste; process cycle
  efficiency; manual entries per item; and expected errors at published human error rates, as DPMO and
  sigma level.
- **Analyze:** the Lean wastes found, each with evidence from the recording, and the bottleneck (Theory of
  Constraints).
- **Improve:** Eliminate, Combine, Rearrange, Simplify first, then automate. A tool plan per stage compares
  three cases: now, with your tools, and with suggested tools. Suggested tools are added one at a time,
  biggest saving first, with minutes saved per item and per month.
- **Control:** a validation checklist with evidence for each check, and the measures (KPIs) for the final
  report.

No single product is assumed. n8n, Power Automate, Zapier, Python and UiPath are all "tools with
capabilities". When a website has no API, the plan suggests asking the vendor for an API key, or building
a small API in front of the page (`portal_api.py`) so any tool can call it.

## From recommendation to a working automation

For each promising task the report adds an **integration design**, built only from evidence:

- **Data flow, field to field:** every value copied or typed, from which file, page or field into which,
  with step numbers and how many runs it appeared in. Values reached through an intermediate copy are traced
  back to where they really come from.
- **Decide before automating:** values that were typed by hand have no recorded source, so they become
  questions for the business, not guesses. A typed field with the same name as a field read elsewhere is
  shown as "probably X, confirm".
- **How the systems can connect:** API, SQL, file, n8n, Postman or browser automation for each system, each
  with a status: *documented* (official documentation), *found online, confirm* (a web search hit), or
  *unknown* (ask the vendor or IT).
- **Automation sequence:** trigger, extract, write, finish and decide stages, in business language.

Once the business decides to go ahead:

```bash
.venv/Scripts/python.exe -m flowsme build demo-invoice-processing --task 2
```

This writes these files to `analysis/automations/task2/`:

- `n8n_workflow.json`: import into n8n. It calls Microsoft Graph and the portal, and puts every value that
  still has to be filled in behind a warning sign.
- `postman_collection.json`: the same calls, to prove each connection works before building.
- `tracker.sql`: a database table to replace the spreadsheet.
- `fill_web_form.py`: a Playwright script for web forms that have no API.
- `portal_api.py`: a small API (FastAPI + Playwright, protected by an API key) in front of a web form with no
  API, so n8n, Power Automate, Zapier or scripts can call it.
- `report_kpis.sql`: a run log (the Control step) and views for the final report in Power BI, Looker Studio
  or Excel.
- `BUILD.md`: the guide that ties these together.

Add `--deploy-n8n`, with `N8N_URL` and `N8N_API_KEY` set, to create the workflow in your n8n instance
directly. It is created inactive.

## What you get

`sessions/<id>/analysis/` contains:

- `report.html` and `report.md`. The report has these sections:
  - a summary with time saved per run and per month
  - an opportunity table
  - a workflow map, with repeated routines highlighted
  - an application map: window switches, plus values copied by hand from one app to another
  - for each task: a step-by-step list, key screens, recommendations, quick wins, risks, tools found
    online, and an automation draft
- `analysis.json`: everything in machine-readable form.
- `automations/`: the automation drafts as files.

## How it works

```
recorder  ->  events.jsonl + screenshots
segment   ->  steps (a click plus the typing/shortcuts that follow it) -> tasks
              (split at idle gaps; repeated routines found by matching step fingerprints,
               tolerant of extra clicks and retries)
describe  ->  one sentence per step (vision model, or a description built from the UI metadata)
workflow  ->  task names and summaries, application map, copy-paste hand-offs, Mermaid diagrams
score     ->  0-100 automation signal: repetition, cross-app copy-paste, typing, switching, time
recommend ->  web search (DuckDuckGo) + tool catalog + AI or rule-based recommendations
report    ->  HTML / Markdown / JSON
```

| File | Purpose |
|---|---|
| `flowsme/recorder.py` | Windows input hooks (pynput), UI Automation element lookup, screenshots (mss) |
| `flowsme/segment.py` | Events to steps to tasks; repeated-routine detection |
| `flowsme/describe.py` | Step descriptions (vision model or heuristics), with a cache |
| `flowsme/workflow.py` | Task naming, application map, Mermaid diagrams |
| `flowsme/score.py` | Automation score and savings estimate |
| `flowsme/search.py` | Web search and the built-in tool catalog |
| `flowsme/recommend.py` | Recommendations and automation drafts |
| `flowsme/report.py` | Report writers |
| `flowsme/demo.py` | Synthetic demo session |

## Privacy

- **Typed text is not stored by default.** Only the number of characters and the shortcuts are recorded.
  Password fields are never captured, even when `capture_typed_text = true`.
- **Values are fingerprinted, never stored.** When you copy something or finish typing in a field, the
  recorder stores a salted one-way fingerprint (HMAC-SHA256) of the value. Equal values get equal
  fingerprints, so the analysis can *prove* that "the Vendor typed into the portal is the Vendor copied from
  the PDF" instead of guessing. The salt is random, lives only in memory while recording and is never saved,
  so the fingerprints can't be reversed or checked against guesses afterwards. Excluded windows, password
  fields and clipboard content that password managers mark as private are never fingerprinted. Turn it off
  with `fingerprint_values = false`.
- **Copy-paste is detected however you do it:** Ctrl+C/V/X, Ctrl+Insert, Shift+Insert, Shift+Delete,
  right-click menus, ribbon buttons, and any other change to the clipboard.
- **Excluded windows.** When a password manager, a Windows security or UAC prompt, or a window whose title
  contains words like "password", "sign in" or "incognito" is in front, nothing about it is captured: no
  screenshot, no title, no text. Edit the list in `config.toml`.
- **The microphone is off unless you pass `--narrate`.** Pausing (Ctrl+Alt+P) also mutes it: the paused
  stretch is saved as silence. The audio (`narration.wav`) stays in the session folder; only the analysis
  sends it to the transcription endpoint.
- **Everything stays on your computer** unless you configure a hosted AI model. Web searches send only
  generic queries such as "automate copying data from Acrobat Reader to Excel".
- Screenshots capture the whole monitor, so close anything private before recording. `sessions/` is
  git-ignored.
- If you use this on other people's work, get their consent and check the local rules on employee
  monitoring (for example GDPR or works councils).

## Limits of this MVP

- Recording works on Windows only. Analysis runs anywhere.
- One recording shows one path through a process. Record it a few times to see exceptions.
- Browser windows are identified by page title, not URL.
- Savings are estimates. Automation drafts are starting points to review, never to run blindly.

## Next steps

- Merge several recordings of the same process into one map, with variants and exception paths.
- Read browser URLs through UI Automation for better web-app identification.
- Redact personal data (OCR + PII detection) from screenshots before they are sent to a hosted model.
- Generate ready-to-import Power Automate and n8n flows, not just drafts.
- Add a small tray app with record, pause and stop buttons.
