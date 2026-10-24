"""Process-improvement knowledge the analysis and the AI prompts draw on: Lean, Six Sigma, Lean Six Sigma,
Theory of Constraints, ECRS, PDCA and automation-suitability criteria, with sources (ASQ, Lean Enterprise
Institute, iSixSigma, NIST, peer-reviewed papers). Definitions follow the sources; how each method maps to
signals in a desktop recording is FlowSME's own reading, not something the sources state."""
from __future__ import annotations

LEI = "https://www.lean.org/lexicon-terms"
ISS = "https://www.isixsigma.com/dictionary"
ASQ = "https://asq.org/quality-resources"

METHODS: dict[str, dict] = {
    # ---- Lean: the eight wastes (DOWNTIME) ----
    "transportation": {"name": "Transportation waste", "family": "lean",
                       "definition": "Moving material between steps without changing it. In office work the material "
                                     "is data: carrying it between applications, files or people unchanged.",
                       "signals": ["copy in one application, paste in another", "export then import of the same file"],
                       "sources": [f"{LEI}/seven-wastes/", f"{ASQ}/lean"]},
    "motion": {"name": "Motion waste", "family": "lean",
               "definition": "Unnecessary movement by the worker, such as searching for documents. On a desktop: "
                             "navigating, searching, scrolling and switching windows without changing the work.",
               "signals": ["window switches", "A-B-A back-and-forth between two apps", "scrolling", "clicks that only open or find"],
               "sources": [f"{LEI}/seven-wastes/", f"{ASQ}/lean"]},
    "waiting": {"name": "Waiting waste", "family": "lean",
                "definition": "Idle time waiting for machines, information, approvals or the previous step.",
                "signals": ["long pauses while a page loads", "work resumed only after a reply arrives"],
                "sources": [f"{LEI}/seven-wastes/", f"{ASQ}/lean"]},
    "over_processing": {"name": "Over-processing waste", "family": "lean",
                        "definition": "More work than the customer needs, such as entering the same data twice or "
                                      "checks beyond what the risk warrants.",
                        "signals": ["same value typed in two places", "value copied into a file only to be copied out"],
                        "sources": [f"{LEI}/seven-wastes/", f"{ASQ}/lean"]},
    "defects": {"name": "Defects and rework", "family": "lean",
                "definition": "Errors that need inspection, correction or rework: the work is paid for twice and "
                              "may reach the customer.",
                "signals": ["the same Submit clicked twice", "runs needing extra steps", "corrections (backspace, undo)"],
                "sources": [f"{LEI}/seven-wastes/", f"{ASQ}/lean"]},
    "inventory": {"name": "Inventory waste", "family": "lean",
                  "definition": "More work-in-process than smooth flow needs: in offices, queues of unprocessed "
                                "emails, requests and documents. More waiting work means longer lead time.",
                  "signals": ["filtering a queue for unread items", "items opened, parked and finished later"],
                  "sources": [f"{LEI}/seven-wastes/", f"{ISS}/process-cycle-efficiency-pce/"]},
    "overproduction": {"name": "Overproduction", "family": "lean",
                       "definition": "Producing more, or earlier, than the next step needs, such as reports nobody uses.",
                       "signals": ["outputs no later step uses (confirm with the user)"],
                       "sources": [f"{LEI}/overproduction/"]},
    "skills": {"name": "Unused talent", "family": "lean",
               "definition": "Not using people's skills and judgement; added to Ohno's original seven wastes as "
                             "the eighth.",
               "signals": ["most of a skilled person's time spent copying and typing"],
               "sources": [f"{ASQ}/lean", f"{ISS}/8-wastes-of-lean/"]},
    # ---- Lean: value and flow ----
    "va_nva": {"name": "Value-added analysis (VA / BVA / NVA)", "family": "lean",
               "definition": "Value-added steps change the output in a way the customer would pay for. "
                             "Business-value-added steps are required (law, control, audit) but add nothing for "
                             "the customer. Non-value-added steps are waste: eliminate them, and minimise BVA.",
               "signals": ["every recorded step is classified"],
               "sources": [f"{ISS}/value-added/", f"{ISS}/business-value-added/", f"{ISS}/non-value-added/"]},
    "pce": {"name": "Process cycle efficiency", "family": "lean",
            "definition": "Value-added time divided by total lead time. A low figure means most time is waiting "
                          "or non-value-added work. Here it is measured on hands-on time only, so the true "
                          "figure (with waiting between items) is lower.",
            "signals": ["time per step by value class"],
            "sources": [f"{ISS}/process-cycle-efficiency-pce/"]},
    "vsm": {"name": "Value stream mapping", "family": "lean",
            "definition": "Mapping every step needed to deliver a service, with process time, waits and value "
                          "class, to expose waste and design the future state.",
            "signals": ["the step sequence, hand-offs and timings of each run"],
            "sources": [f"{LEI}/value-stream-mapping/", f"{ASQ}/value-stream-mapping"]},
    "takt_time": {"name": "Takt time vs cycle time", "family": "lean",
                  "definition": "Takt time = available working time / customer demand: the pace work must keep. "
                                "When the cycle time per item is above takt, a backlog grows.",
                  "signals": ["items completed per day", "cycle time per item", "available hours"],
                  "sources": [f"{LEI}/takt-time/", f"{LEI}/cycle-time/"]},
    "five_s": {"name": "5S (digital workplace)", "family": "lean",
               "definition": "Sort, set in order, shine, standardize, sustain: organising the workplace so nothing "
                             "has to be searched for. On a desktop: folders, file names, templates, bookmarks.",
               "signals": ["time in file dialogs and folder navigation", "searching for files or emails",
                           "many open windows"],
               "sources": [f"{LEI}/five-s/", f"{ASQ}/five-s-tutorial"]},
    "gemba": {"name": "Gemba (go and see)", "family": "lean",
              "definition": "Observe the real work where it happens instead of relying on reports or the written "
                            "procedure. A recording is a digital gemba walk: it shows the actual process.",
              "signals": ["recorded steps that differ from the documented procedure"],
              "sources": [f"{LEI}/gemba/"]},
    "standard_work": {"name": "Standardized work", "family": "lean",
                      "definition": "One precise, agreed way to do the work (sequence, timing, materials). It is the "
                                    "baseline improvement is measured against, and the automation script follows it.",
                      "signals": ["runs of the same task that take different paths"],
                      "sources": [f"{LEI}/standardized-work/"]},
    "poka_yoke": {"name": "Poka-yoke (error-proofing)", "family": "lean",
                  "definition": "Simple means that stop a mistake from being made, or make it obvious at once. "
                                "Prevention (it can't be done wrong) is preferred to detection.",
                  "signals": ["values re-keyed between apps", "free typing of IDs, amounts, dates"],
                  "sources": [f"{LEI}/poka-yoke/", f"{ASQ}/mistake-proofing"]},
    "jidoka": {"name": "Jidoka (stop on abnormality)", "family": "lean",
               "definition": "Detect an abnormal condition and stop, instead of passing errors downstream. For "
                             "automation: hand exceptions to a person rather than forcing them through.",
               "signals": ["runs that needed extra steps", "error dialogs that need judgement"],
               "sources": [f"{LEI}/jidoka/"]},
    "kaizen": {"name": "Kaizen", "family": "lean",
               "definition": "Small, frequent improvements by the people who do the work, usually run as "
                             "plan-do-check-act cycles.",
               "signals": ["before/after recordings of the same task"],
               "sources": [f"{LEI}/kaizen/", f"{ASQ}/continuous-improvement"]},
    # ---- Six Sigma ----
    "lean_six_sigma": {"name": "Lean Six Sigma", "family": "lss",
                       "definition": "Lean removes waste and improves flow; Six Sigma reduces variation and defects. "
                                     "Used together, usually through DMAIC.",
                       "signals": ["waste and defect signals in the same task"],
                       "sources": [f"{ASQ}/six-sigma"]},
    "dmaic": {"name": "DMAIC", "family": "six_sigma",
              "definition": "Define, Measure, Analyze, Improve, Control: the structured cycle for improving an "
                            "existing process with data.",
              "signals": ["the structure of this analysis"],
              "sources": [f"{ASQ}/dmaic", f"{ISS}/dmaic/"]},
    "sipoc": {"name": "SIPOC", "family": "six_sigma",
              "definition": "A one-page scope of a process: Suppliers, Inputs, Process (5-7 steps), Outputs, Customers.",
              "signals": ["sources read", "stages", "records written", "who receives them"],
              "sources": ["https://www.isixsigma.com/sipoc-copis/sipoc-diagram/", f"{ASQ}/sipoc"]},
    "ctq": {"name": "Critical to quality (CTQ)", "family": "six_sigma",
            "definition": "Measurable requirements from the voice of the customer (e.g. 100% field accuracy). "
                          "They define what counts as a defect.",
            "signals": ["amounts, IDs and dates moved between systems"],
            "sources": [f"{ISS}/critical-to-quality-ctq/"]},
    "dpmo": {"name": "DPMO and sigma level", "family": "six_sigma",
             "definition": "DPMO = defects / (units x opportunities) x 1,000,000; each field entered is an "
                           "opportunity. Six sigma is 3.4 DPMO (long term, with the conventional 1.5 sigma shift).",
             "signals": ["manual entries per item"],
             "sources": [f"{ISS}/defects-per-million-opportunities-dpmo/", f"{ISS}/sigma-level/"]},
    "fpy": {"name": "First-pass yield", "family": "six_sigma",
            "definition": "Share of items that pass a step correctly the first time. A high final yield with a "
                          "low first-pass yield reveals hidden rework.",
            "signals": ["runs with and without rework"],
            "sources": [f"{ISS}/first-time-yield-fty/", f"{ISS}/rolled-throughput-yield-rty/"]},
    "fmea": {"name": "FMEA", "family": "six_sigma",
             "definition": "Lists how each step can fail and rates severity, occurrence and detection (1-10); "
                           "their product (RPN) ranks which risks to control first.",
             "signals": ["each manual hand-off is a failure mode"],
             "sources": [f"{ASQ}/fmea"]},
    "control_plan": {"name": "Control plan", "family": "six_sigma",
                     "definition": "How the improved process is held: what is measured, how often, by whom, and "
                                   "what happens when a check fails.",
                     "signals": ["run log", "reconciliation", "exception review"],
                     "sources": [f"{ISS}/control-plan/", f"{ASQ}/dmaic"]},
    "pareto": {"name": "Pareto analysis", "family": "six_sigma",
               "definition": "Rank causes by frequency or impact; a vital few usually explain most of the effect.",
               "signals": ["which checks fail most after go-live"],
               "sources": [f"{ASQ}/pareto"]},
    "five_whys": {"name": "5 Whys", "family": "six_sigma",
                  "definition": "Ask 'why' repeatedly to get from a symptom to its root cause.",
                  "signals": ["open questions about where typed values come from"],
                  "sources": [f"{LEI}/5-whys/", f"{ASQ}/five-whys"]},
    "fishbone": {"name": "Fishbone (Ishikawa) diagram", "family": "six_sigma",
                 "definition": "Groups possible causes of a problem by category (methods, systems, input data, "
                               "people, environment), so automation isn't assumed to be the only fix.",
                 "signals": ["slow systems", "corrections after paste", "different methods between runs",
                             "interruptions mid-task"],
                 "sources": [f"{ASQ}/fishbone"]},
    "cost_of_quality": {"name": "Cost of poor quality", "family": "six_sigma",
                        "definition": "Quality costs split into prevention, appraisal (checking), internal failure "
                                      "(rework) and external failure. Turning rework and checking time into money "
                                      "justifies error-proofing on more than speed.",
                        "signals": ["time spent correcting", "time spent checking by hand"],
                        "sources": [f"{ASQ}/cost-of-quality"]},
    # ---- Other ----
    "klm": {"name": "Keystroke-Level Model", "family": "other",
            "definition": "Predicts expert task time by adding operator times: keystroke about 0.2 s, pointing "
                          "1.1 s, moving a hand between keyboard and mouse 0.4 s, a mental step 1.35 s (Card, "
                          "Moran and Newell 1980). Used to sanity-check claimed time savings.",
            "signals": ["keystroke counts", "clicks", "keyboard-mouse switches", "decisions"],
            "sources": ["https://facsrv.cdm.depaul.edu/~cmiller/hci460/materials/goms.html",
                        "https://doi.org/10.1145/358886.358895"]},
    "toc": {"name": "Theory of Constraints", "family": "toc",
            "definition": "Throughput is limited by the constraint. Identify it, exploit it, subordinate the rest "
                          "to it, elevate it, repeat. Speeding up a non-constraint step doesn't shorten the whole.",
            "signals": ["the stage with the most minutes per item"],
            "sources": ["https://www.tocinstitute.org/five-focusing-steps.html"]},
    "ecrs": {"name": "ECRS", "family": "other",
             "definition": "Improve in order: Eliminate unneeded steps, Combine, Rearrange, then Simplify. Only "
                           "then automate, so you don't automate waste.",
             "signals": ["duplicate entry", "intermediate copies", "one-at-a-time processing", "free typing"],
             "sources": ["https://www.lean.org/the-lean-post/articles/the-art-of-lean-use-these-tools-to-help-you-improve-a-process-part-2/"]},
    "pdca": {"name": "PDCA", "family": "other",
             "definition": "Plan, Do, Check, Act: try a change small, measure it, then standardise or adjust.",
             "signals": ["pilot with a few items, then re-record"],
             "sources": [f"{ASQ}/pdca-cycle", "https://deming.org/explore/pdsa/"]},
    "rpa_suitability": {"name": "Automation suitability", "family": "other",
                        "definition": "Good candidates are standardised, stable, rule-based, frequent and long enough "
                                      "to matter, with structured digital inputs, few exceptions and error-prone "
                                      "manual work (Wellmann et al., BPM 2020: 13 criteria in 5 groups).",
                        "signals": ["repetition", "variation between runs", "copy-paste", "judgement steps"],
                        "sources": ["https://arxiv.org/abs/2007.10900", "https://doi.org/10.1007/s12599-018-0542-4"]},
    "robotic_process_mining": {"name": "Robotic process mining", "family": "other",
                               "definition": "Finding automatable routines in user-interaction logs (clicks, keys, "
                                             "copy-paste) and generating automation from them: the research field "
                                             "closest to what FlowSME does.",
                               "signals": ["repeated routines", "a value copied from one field appears in another"],
                               "sources": ["https://doi.org/10.1007/s12599-020-00641-4"]},
}

# Long-term DPMO for each sigma level, with the conventional 1.5 sigma shift: DPMO = 1e6 * (1 - Phi(sigma - 1.5)).
# Source: https://www.isixsigma.com/sigma-level/sigma-to-dpmo-to-yield-to-cpk-table/
SIGMA_TABLE = {6.0: 3.4, 5.5: 32, 5.0: 233, 4.5: 1_350, 4.0: 6_210, 3.5: 22_750, 3.0: 66_807,
               2.5: 158_655, 2.0: 308_538, 1.5: 500_000, 1.0: 691_462}

# Published human error rates for manual entry, low to high. Rates differ with field type and setting, so the
# report shows the range instead of one number.
ERROR_RATES: list[dict] = [
    {"per_field": 0.00143, "context": "Nahm et al. 2008: source to database, clinical trials",
     "source": "https://doi.org/10.1371/journal.pone.0003049"},
    {"per_field": 0.0029, "context": "Garza et al. 2025: meta-analysis of 93 studies, single data entry",
     "source": "https://doi.org/10.1016/j.ijmedinf.2024.105749"},
    {"per_field": 0.037, "context": "Mays & Mathias 2019: manual transcription of numeric results",
     "source": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6351970/"},
]

# Why the checklist puts prevention (no re-keying, drop-downs) before detection (reviews, rules).
EVIDENCE: list[dict] = [
    {"finding": "When values flowed electronically instead of being transcribed, transcription errors fell from "
                "6.1% to zero.", "source": "https://pubmed.ncbi.nlm.nih.gov/20445181/", "who": "Poon et al. 2010"},
    {"finding": "Checking entries by eye was not significantly better than no checking; double entry was far better.",
     "source": "https://doi.org/10.1016/j.chb.2011.04.004", "who": "Barchard & Pace 2011"},
    {"finding": "Built-in validation alarms prevented only a small fraction of errors.",
     "source": "https://pmc.ncbi.nlm.nih.gov/articles/PMC2656002/", "who": "Goldberg et al. 2008"},
    {"finding": "A second, independent entry of the same data caught 88% of errors.",
     "source": "https://pubmed.ncbi.nlm.nih.gov/14500053/", "who": "Kawado et al. 2003"},
]


def sigma_level(dpmo: float) -> float:
    """Highest sigma level whose DPMO is at or above the given defect rate."""
    for level, limit in sorted(SIGMA_TABLE.items(), reverse=True):
        if dpmo <= limit:
            return level
    return 1.0
