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
