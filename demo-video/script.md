# FlowSME hackathon demo video: release contract and script

## Release contract

- **For:** Meta Global AI Developer Hackathon judges (submission due Wed Nov 4, 9:00 PM ET). Judged on impact (30%),
  use of Meta AI tooling (25%), technical depth (20%) and the demo (25%).
- **Length and format:** at most 3:00. 1920×1080, 30 fps, H.264 + AAC stereo 48 kHz, faststart.
- **Rule that shapes it:** the video may only highlight what was built during the hackathon (README, "What was
  built during the Meta Global AI Developer Hackathon"). Each chapter carries a small "Built during the
  hackathon" label. Anything older that is on screen (the workflow map, data flow, value fingerprints, web
  search) isn't named or highlighted.
- **Real product only:** every product shot is a real run on this PC: FlowSME Recorder importing the 5.6-minute
  invoice screen video, the FlowSME server analysing it with Muse Spark and Muse Voice Transcribe, the report,
  SOP and Build. Title and closing cards are the only made-up visuals.
- **Claims that must appear:** Muse Spark reads the screen changes and writes the task names and
  recommendations as schema-checked JSON; Muse Voice Transcribe transcribes the narration; spoken rules are
  kept only when they quote the transcript word for word; only the active window is sent; spending cap per
  report; employees see hours, never pay rates.
- **Claims that must not appear:** customer results, testimonials or user counts; "built entirely at the
  hackathon"; anything the run on camera didn't do.
- **Voice:** one synthetic voice, approved from a 15-second sample. The final mix must be listened to by a person
  before it is submitted.
- **Captions:** burned in from the locked narration below, at most two lines of about 42 characters, never over
  the product's controls.
- **Spend:** at most $5 of Meta Model API credit for the runs on camera.
- **Publishing:** not by Claude. The user uploads the video and attaches the link to the submission.

## Narration (locked text, about 340 words)

| # | Time | On screen | Narration |
|---|---|---|---|
| 1 | 0:00 | Title over the finished report | Every small business runs on routines nobody ever wrote down. FlowSME watches one round of the work, and hands back the process, the SOP, and the automation. |
| 2 | 0:10 | Report summary cards, slow push in | Hiring a consultant to shadow your staff is slow and expensive, so most small firms never do it. The same invoice gets retyped by hand, every day. This demo shows what we built during the hackathon, on Meta's models. |
| 3 | 0:24 | The source video, then FlowSME Recorder: More, Import a video | Here's a five-and-a-half-minute screen video of invoice entry: Notepad into an Excel tracker, explained out loud while working. In FlowSME Recorder, I import it. The Recorder only records and sends. The analysis runs on our server. |
| 4 | 0:46 | Recorder showing the job; server log lines | On the server, Muse Spark reads each screen change: which app, which field, and whether a value was typed, copied or pasted. Muse Voice Transcribe turns the soundtrack into text, lined up with the steps. Then Muse Spark names the tasks and writes the recommendations, as schema-checked JSON. |
| 5 | 1:13 | Report: summary, what to automate first, roadmap | A couple of minutes later, the report is back. It found the invoice routine repeating, how long each round takes, and the hours it could save each month. This chart says what to automate first, and the roadmap shows the payback. |
| 6 | 1:38 | Task details: what was said, the due-date rule | Values typed by hand are the hard part. Here, the person said the due date is the invoice date plus thirty days. FlowSME turned that into a business rule, and it keeps a rule only when it quotes the transcript word for word. |
| 7 | 2:00 | SOP page, step cards, approval block; user stories | For the team, there's an SOP: numbered steps with a screenshot of each, the reason behind each check, and an approval block. For whoever builds the automation, user stories with acceptance tests. |
| 8 | 2:22 | Build: workflow sent to the customer's tool | Build sends a ready-made workflow, switched off, to the customer's own tool: n8n, Node-RED, Make, Power Automate, Logic Apps, or Windmill. |
| 9 | 2:40 | Closing card | Only the active window goes to Meta, every report has a spending cap, and employees see hours, never pay rates. FlowSME: show it the work once, and get the process and the automation. |
