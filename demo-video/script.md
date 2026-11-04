# FlowSME hackathon demo video: release contract and script

## Release contract

- **For:** Meta Global AI Developer Hackathon judges (submission due Wed Nov 4, 9:00 PM ET). Judged on impact (30%),
  use of Meta AI tooling (25%), technical depth (20%) and the demo (25%).
- **Length and format:** at most 3:00 (this cut: 2:05). 1920×1080, 30 fps, H.264 + AAC stereo 48 kHz, faststart.
- **Rule that shapes it:** the video may only highlight what was built during the hackathon (README, "What was
  built during the Meta Global AI Developer Hackathon"). Each chapter about new work carries a "Built during the
  hackathon" label. Older parts that are on screen (the report's summary, the workflow map) aren't named.
- **Real product only:** the run on screen is a real one on this PC (run 3 below): the invoice screen video sent
  to the FlowSME server with a manager seat of a demo organisation ("Acme Finance"), read by Muse Spark,
  transcribed by Muse Voice Transcribe, then the report, SOP and Build output it produced. The report and SOP are
  the server's own pages, recorded in headless Chrome; the server-run and Build pages are drawn only from that
  run's log, frames, model sentences, transcript and build zip. Title and closing cards are the only made-up
  visuals.
- **Claims that appear:** Muse Spark reads the screen changes and names tasks and writes recommendations as
  schema-checked JSON; Muse Voice Transcribe transcribes the narration; the narrated net-30 rule reaches the top
  recommendation; Build writes workflows for six tools and the Recorder can send them switched off; Recorder
  recordings send only the active window; spending cap per report; employees see hours, never pay rates.
- **Claims that must not appear:** customer results, testimonials or user counts; "built entirely at the
  hackathon"; anything the run on screen didn't do (for example the business-rules table's word-for-word quote
  check, which this run didn't use).
- **Shown with a label:** the input is a test video made for this demo with synthetic invoices, cropped to Notepad
  and Excel and played at 4× speed.
- **Not in this cut:** FlowSME Recorder's own window. Screen control was declined, so the video was sent with the
  same client code the Recorder uses, and the narration only says customers send videos from the Recorder.
- **Voice:** en-US-AvaMultilingualNeural, approved from a 25-second audition of segments 1 and 2 (unchanged since).
  The final mix must be listened to by a person before it is submitted.
- **Captions:** burned in from the locked narration below, split at sentences and commas, at most two lines of 42
  characters, in the band under the product frame.
- **Spend:** at most $5 of Meta Model API credit. Used: three runs, about $1.10 (video reading $0.21 + $0.18 +
  $0.17 from the server log; the rest estimated from earlier runs of the same video).
- **Publishing:** not by Claude. The user uploads the video and attaches the link to the submission.

## The runs

| Run | Input | Result | Used |
|---|---|---|---|
| 1 | Original test video | 37 steps in 3 apps (the Claude window in the video counted as an app); 2 tasks, no routine; report titled "Vendor field" | No |
| 2 | Video cropped to Notepad and Excel | 35 steps, 2 apps; no routine; roadmap: Hold (20 runs a month) | No |
| 3 | Same cropped video, title fix (8233e89), 100 recordings a month | 34 steps, 2 apps, same 5-step routine 4 times, 5.9-6.0 h a month, Now (quick win), pays back in 6.8 months | Yes |

The model reads a video a little differently each time, so whether the routine is found varies between runs.

## Narration (locked text)

| # | Starts | On screen | Narration |
|---|---|---|---|
| 1 | 0:00 | Title over the finished report | Every small business runs on routines nobody ever wrote down. FlowSME watches one round of the work, and hands back the process, the SOP, and the automation. |
| 2 | 0:11 | The input video | Hiring a consultant to shadow your staff is slow and expensive, so most small firms never do it. The same invoice gets retyped by hand, every day. This demo shows what we built during the hackathon, on Meta's models. |
| 3 | 0:26 | The input video, "Screen video import" | Here's a five-and-a-half-minute screen video of invoice entry: Notepad into an Excel tracker, explained out loud while working. Customers send a video like this from FlowSME Recorder, our Windows app, and the analysis runs on our server. |
| 4 | 0:42 | Server log, frames with Muse Spark's sentences, the transcript | On the server, Muse Spark reads each screen change: which app, which field, and whether a value was typed, copied or pasted. Muse Voice Transcribe turns the soundtrack into text, lined up with the steps. Then Muse Spark names the tasks and writes the recommendations, as schema-checked JSON. |
| 5 | 1:01 | Report summary, what to automate first, roadmap row | A couple of minutes later, the report is back. It found the same five-step routine four times, and six hours a month to save. The chart and the roadmap say what to automate first: a quick win that pays back in under seven months. |
| 6 | 1:16 | What was said; Recommendations tab | It also listens. The person said all their suppliers are on net 30, so the top recommendation works out the due date as the invoice date plus thirty days, instead of someone doing the sum. |
| 7 | 1:27 | SOP steps and approval; User stories tab | For the team, there's a draft SOP: numbered steps with a screenshot of each, the checks, and an approval block. For whoever builds it, user stories with acceptance tests. |
| 8 | 1:39 | Build output: six tools, the n8n workflow | Build writes the workflow for the customer's own tool: n8n, Node-RED, Make, Power Automate, Logic Apps, or Windmill. The Recorder can send it there, switched off. |
| 9 | 1:50 | Closing card | With the Recorder, only the active window goes to Meta. Every report has a spending cap, and employees see hours, never pay rates. FlowSME: show it the work once, and get the process and the automation. |

## How to rebuild it

From `demo-video/`, with ffmpeg, node and `puppeteer-core` on `NODE_PATH`, and edge-tts for the voice:

1. `python narrate.py` writes `media/narration/` and `timeline.json` from `delivery.json`.
2. Put the run's `report.html` and `sop.html` in `media/pages/`; `make_panel.py` and `make_build.py` write
   `panel.html` and `build.html` from the run's session folder, record and build zip.
3. `python scenes.py` records every chapter's frames with `capture.js`.
4. `python compose.py <cropped input video>` writes `out/final.mp4`; `still.js` renders the cards from
   `cards.html`.

## Checks on the master

- 125.07 s, 3,752 frames, H.264 high yuv420p 1920×1080 30 fps, AAC 48 kHz stereo, moov before mdat; full decode
  clean; random-seek frames identical to sequential ones at 30, 60, 90 and 120 s.
- -16.0 LUFS integrated, -1.8 dBTP; no silence over 1.2 s.
- Frames checked at every chapter's start, middle and end and at caption midpoints.

# Version 2 (out/final-v2.mp4)

Version 1 (`out/final.mp4`, 2:05) stays as the fallback. Version 2 adds what the first cut could not show: FlowSME
Recorder on screen, each stage of the analysis with the real call to Meta's Model API, and the real-work problem with
a before-and-after.

## What changes in the contract

- **Length:** 2:44.6 (at most 3:00). Same format, voice profile (`delivery-v2.json`: segments 1 and 2 are the approved
  audition, unchanged), caption rules and loudness target.
- **Real product only, one run:** run 5 below, sent from FlowSME Recorder 0.5.1 with the demo manager seat (the same
  test video, 100 runs a month). The Recorder footage is a screen capture of the app window only; where another window
  covered the right edge of the first captures, they are cropped to the clean area. The first-run Welcome dialog was
  shown again (its "accepted" flag reset) to film what a recording keeps, before sign-in, so no later recording is in
  the list behind it.
- **The Meta API calls:** `make_api.py` builds the page from run 5's server files. Every response is verbatim from the
  run's caches (video reading, transcript, task naming, recommendation, web search) or its log (frames, calls, cost);
  requests show the fields `flowsme/llm.py` and `flowsme/video.py` send, trimmed and labelled as such. The endpoint
  and cap come from the server's `server.toml` (`https://api.meta.ai/v1`, `max_cost_usd = 2.0`).
- **Before and after:** the 36 s to 9 s per invoice (74% less time) result is the internal test made with FlowSME's
  before-and-after comparison on our own recordings, when that feature was built (`158049b`). It is labelled on screen and in the narration as an
  internal test, not a customer result.
- **Hackathon labels:** Recorder app and video import, Muse Spark on video frames, Muse Voice Transcribe, the AI cost
  meter and cap, Recorder results tabs, what to automate first and the roadmap, SOP, user stories and Build carry the
  "Built during the hackathon" pill. Task naming, web search and recommendations (the Meta connection of `f528dda`,
  51 minutes before kickoff) are shown because the brief asks for every stage, with a note on screen that they were
  built before the hackathon.
- **Claims that must not appear (added):** that the report's sources come from Meta's web search (in runs 3-5 its
  answer had no citation marks, so FlowSME fell back to DuckDuckGo; fixed in `58862bb` for future reports); a routine
  found four times (run 5 found none); "a hundred invoices" (the input is a hundred runs of the recorded work).

## The runs (version 2)

| Run | Sent from | Result | Used |
|---|---|---|---|
| 4 | FlowSME Recorder, More > Import a video | 37 steps, 2 tasks, no routine; Next (major project), 6.2 h a month, pays back in 8.4 months | No |
| 5 | FlowSME Recorder, same video | 33 steps, 2 tasks (task 2 the same process as task 1); Now (quick win), 5.1-5.2 h a month ($127), pays back in 7.1 months; net-30 rule in the task summary and the recommendation | Yes |

Video reading cost $0.19 (run 4) and $0.17 (run 5) from the server log; the transcript, task naming, recommendation
and web search add an estimated $0.15-0.20 per run. Total for both versions: about $1.85 of the $5 allowed.

## Narration, version 2 (locked text)

| # | Starts | On screen | Narration |
|---|---|---|---|
| 1 | 0:00 | Title over run 5's report | Every small business runs on routines nobody ever wrote down. FlowSME watches one round of the work, and hands back the process, the SOP, and the automation. |
| 2 | 0:11 | The input video | Hiring a consultant to shadow your staff is slow and expensive, so most small firms never do it. The same invoice gets retyped by hand, every day. This demo shows what we built during the hackathon, on Meta's models. |
| 3 | 0:26 | Recorder: Welcome dialog and Record button; More > Import a video; report options; Get report; Activity | This is FlowSME Recorder, our Windows app. Press Record and work as usual: it keeps each click, the field, whether a value was typed, copied or pasted, a picture of the active window only, and your voice. Here, a manager imports a screen video instead, and Get report sends it to our server. |
| 4 | 0:46 | API page: frames log, POST /chat/completions (vision), request and response, B2 paste highlighted | On the server, FlowSME finds the forty-two moments the screen changed, and sends them to Muse Spark on Meta's API, five frames at a time. Back comes schema-checked JSON: Excel, cell B2, pasted, the vendor name. |
| 5 | 1:02 | API page: POST /asr/transcribe, the net-30 turn, then the task summary and recommendation that use it | Muse Voice Transcribe turns the soundtrack into text, with timings. The clerk said all their suppliers are on net 30, and that sentence carries through: the task summary and the top recommendation both set the due date to the invoice date plus thirty days. |
| 6 | 1:18 | API page: task naming, web search, recommendation; cost meter and cap | Muse Spark then names the tasks, runs a web search for tools that fit, and writes the recommendations as checked JSON. Every call is metered, and each report has a spending cap: reading this whole video cost seventeen cents. |
| 7 | 1:32 | Recorder: the row turns ready; Results; Narration tab | A few minutes later, the report is back in the Recorder: the task, the minutes saved each time, the hours a month, and the narration, time-stamped. |
| 8 | 1:42 | Report: summary, roadmap row | The full report says what to automate first: about five hours a month if this work is done a hundred times, a quick win that pays back in about seven months. |
| 9 | 1:51 | SOP; User stories tab | For the team, there's a draft SOP: a screenshot of each step, the checks, and an approval block. For whoever builds it, user stories with acceptance tests. |
| 10 | 2:02 | Recorder: Build automation dialog, n8n chosen; Build output | Build, right in the Recorder, writes the workflow for the customer's own tool: n8n, Node-RED, Make, Power Automate, Logic Apps, or Windmill, and can create it there, switched off. |
| 11 | 2:15 | Before-and-after card (internal test), what the owner gets | Does it pay off? In our own internal test, not a customer result, an invoice took thirty-six seconds by hand, and nine once the clerk only checked it: seventy-four percent less time. The owner gets the process on paper, what it costs, and the fix. |
| 12 | 2:31 | Closing card | With the Recorder, only the active window goes to Meta, and employees see hours, never pay rates. FlowSME: show it the work once, and get the process and the automation. |

## How to rebuild version 2

1. `python narrate.py v2` writes `media/narration-v2/` and `timeline-v2.json` from `delivery-v2.json`.
2. Put run 5's `report.html` and `sop.html` in `media/v2/pages/`; `make_build.py <build.zip> media/v2/pages/build.html`
   and `make_api.py <recording folder R-...> media/v2/pages/api.html`.
3. `python scenes.py v2` records the page scenes into `media/v2/frames/`.
4. Put the Recorder screen captures in `media/v2/recorder/` (`welcome.mkv`, `import-menu.mkv`, `send.mkv`,
   `results.mkv`; the cut points are in `compose.py`), then `python compose.py v2 <cropped input video>` writes
   `out/final-v2.mp4`.

## Checks on the version 2 master

- 164.57 s, 4,937 frames, H.264 high yuv420p 1920×1080 30 fps, AAC 48 kHz stereo, ftyp/moov/mdat; `verify_master.py`
  (full decode) passes; random-seek frames identical to sequential ones at frames 600, 900, 1500, 2400, 2700, 3300,
  4200 and 4500. SHA-256 1DBFB008D8B52BDB07765444212E59BA18B0FB483F40B1B5B34A613B3AE703E8.
- -16.0 LUFS integrated, -1.5 dBTP; no silence over 1.2 s at -45 dB.
- 47 captions, none overlapping, longest line 42 characters; frames checked at every chapter's start, middle and end,
  every caption midpoint, and the label bands (the chapter 6 note was shortened after it overlapped its label).
- Product on screen about 83% of the time (the before/after and closing cards are the rest).
- Not checked by a person yet: the full mix must be listened to before it is submitted.
