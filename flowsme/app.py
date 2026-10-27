"""FlowSME desktop app: Record, Pause, Stop and Report in one small window, for people who don't use a terminal.

Recordings and settings live in %LOCALAPPDATA%\\FlowSME, not in Documents, which OneDrive may upload.
The Meta API key is kept in the user's MODEL_API_KEY environment variable, as for the command line."""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import __version__
from .config import Config, load_config
from .models import fmt_minutes
from .session import list_sessions, load_meta, new_session_dir
from .toolplan import CATALOG

# First-run settings for the app: Muse Spark on the Meta Model API, with a spending cap per report.
DEFAULT_CONFIG = """# FlowSME app settings. Recordings are kept next to this file, in "sessions".
sessions_dir = "sessions"

[recorder]
# false = keystrokes are counted and shortcuts logged, but the text you type is never stored
capture_typed_text = false
excluded_title_keywords = ["password", "sign in", "log in", "login", "inprivate", "incognito",
                           "windows security", ".env"]

[llm]
# AI analysis with Muse Spark on the Meta Model API. The key is read from the MODEL_API_KEY environment
# variable (FlowSME > Settings saves it there). Without a key, reports use FlowSME's built-in rules.
provider = "openai_compatible"
base_url = "https://api.meta.ai/v1"
api_key_env = "MODEL_API_KEY"
vision_model = "muse-spark-1.3"
text_model = "muse-spark-1.3"
reasoning_effort = "medium"
vision_reasoning_effort = "low"
min_output_tokens = 4096
temperature = 1.0
grounded_search = true
search_context_size = "low"
search_reasoning_effort = "low"
# Stop paying for AI calls once one report's estimated cost reaches this (built-in rules take over).
max_cost_usd = 2.0
send_screenshots = true
concurrency = 8
screenshots_per_call = 4
vision_image_width = 1024
timeout = 180.0

[analysis]
# Used to turn minutes saved into hours and money per month
runs_per_month = 20
hourly_cost = 30
currency = "$"
"""

WELCOME = """FlowSME records how you work, so it can map the process and find what to automate.

While recording it saves:
•  where you click, with the button or field name, and screenshots
•  how many characters you type and the shortcuts you use, not the text
•  your voice, only when Narrate is ticked

It skips password managers, sign-in pages, private browsing and Windows security prompts. Pause (Ctrl+Alt+P) before anything private.

Recordings stay on this computer. When you make an AI report, its screenshots and narration are sent to Meta's AI service for the analysis. Nothing goes anywhere else unless you share a recording yourself.

If you record other people's work, ask them first."""

_STAMP = re.compile(r"^\d{8}-\d{6}-?")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FlowSME"


def app_config(path: str | None = None) -> tuple[Config, Path]:
    """The app's settings: the given config file, or the per-user one (created on first run)."""
    if path is None:
        cfg_path = home() / "config.toml"
        if not cfg_path.exists():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    else:
        cfg_path = Path(path)
    cfg = load_config(cfg_path)
    if not cfg.sessions_dir.is_absolute():
        cfg.sessions_dir = cfg_path.resolve().parent / cfg.sessions_dir
    return cfg, cfg_path


def needs_key(cfg: Config) -> bool:
    return cfg.llm.provider == "openai_compatible" and not any(h in cfg.llm.base_url for h in _LOCAL_HOSTS)


def saved_key(env: str) -> str:
    """The API key from this process, or from the user's environment variables (set by Settings or setx)."""
    if os.environ.get(env):
        return os.environ[env]
    with contextlib.suppress(OSError):
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value = str(winreg.QueryValueEx(key, env)[0] or "")
            if value:
                os.environ[env] = value
                return value
    return ""


def store_key(env: str, value: str) -> None:
    """Save (or remove, when empty) the key as a user environment variable, where the command line finds it too."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        if value:
            winreg.SetValueEx(key, env, 0, winreg.REG_SZ, value)
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, env)
    if value:
        os.environ[env] = value
    else:
        os.environ.pop(env, None)
    with contextlib.suppress(Exception):  # tell other programs (new terminals) that the environment changed
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000,
                                                 ctypes.byref(result))


def engine_text(cfg: Config) -> str:
    llm = cfg.llm
    if llm.provider == "offline":
        return "Reports: FlowSME's built-in rules (no AI)"
    if not needs_key(cfg):
        return f"Reports: local AI ({llm.text_model})"
    if saved_key(llm.api_key_env):
        cap = f", at most ${llm.max_cost_usd:g} per report" if llm.max_cost_usd else ""
        return f"Reports: Meta {llm.text_model}{cap}"
    return "No Meta API key yet: reports use built-in rules. Add one in Settings."


# The tool catalog's names, short enough for check boxes ("Microsoft 365", "Power BI"...).
TOOL_NAMES = [t.name.split(" (")[0] for t in CATALOG]


def apply_options(cfg: Config, options: dict) -> Config:
    """The report options chosen in the app, on top of the settings file."""
    if options["mode"] != "ai":
        cfg.llm.provider = "offline"
    cfg.llm.max_cost_usd = options["max_cost_usd"]
    cfg.llm.send_screenshots = options["screenshots"]
    cfg.analysis.web_search = options["web_search"]
    cfg.analysis.runs_per_month = options["runs_per_month"]
    cfg.analysis.hourly_cost = options["hourly_cost"]
    cfg.analysis.currency = options["currency"]
    cfg.customer.tools = options["tools"] + [t.strip() for t in options["other_tools"].split(";") if t.strip()]
    return cfg


def _title(path: Path) -> str:
    return _STAMP.sub("", path.name).replace("-", " ") or path.name


def _status(path: Path) -> str:
    if not (path / "analysis" / "report.html").exists():
        return ""
    built = path / "analysis" / "automations"
    return "ready, built" if built.exists() and any(built.glob("task*")) else "ready"


def _read_json(path: Path) -> dict | None:
    with contextlib.suppress(OSError, ValueError):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _desktop() -> Path:
    buf = ctypes.create_unicode_buffer(260)
    with contextlib.suppress(Exception):
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # CSIDL_DESKTOPDIRECTORY
            return Path(buf.value)
    return Path.home() / "Desktop"


def _icon() -> Path | None:
    for folder in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).parent / "assets"):
        if (folder / "flowsme.ico").exists():
            return folder / "flowsme.ico"
    return None


class _Pipe:
    """Sends print() output from the recorder and the analysis to the app's activity log."""

    def __init__(self, events: queue.Queue):
        self.events = events
        self.buf = ""
        self.lock = threading.Lock()

    def write(self, text: str) -> int:
        with self.lock:
            self.buf += text
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if line.strip():
                    self.events.put(("log", line.rstrip()))
        return len(text)

    def flush(self) -> None:
        pass


class App:
    def __init__(self, root: tk.Tk, config_path: str | None = None):
        self.root = root
        self.config_path = config_path
        self.cfg, self.cfg_path = app_config(config_path)
        self.state_path = self.cfg_path.resolve().parent / "app.json"
        self.state = self._load_state()
        self.events: queue.Queue = queue.Queue()
        sys.stdout = sys.stderr = _Pipe(self.events)
        root.report_callback_exception = lambda kind, exc, tb: print(f"Error: {exc}")
        self.recorder = None
        self.bar: tk.Toplevel | None = None
        self.busy = False
        self._build()
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(150, self._pump)
        if not self.state.get("accepted"):
            root.after(300, self._welcome)

    # ---- layout -----------------------------------------------------------------------------------

    def _build(self) -> None:
        r = self.root
        r.title("FlowSME")
        top = ttk.Frame(r, padding=(16, 14, 16, 2))
        top.pack(fill="x")
        ttk.Label(top, text="FlowSME", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="Record your work. Get the workflow, what to automate and the minutes saved.",
                  style="Muted.TLabel").pack(side="left", padx=12, pady=(4, 0))

        rec = ttk.LabelFrame(r, text="New recording", padding=12)
        rec.pack(fill="x", padx=16, pady=8)
        ttk.Label(rec, text="Process name").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar(value=self.state.get("last_name", ""))
        ttk.Entry(rec, textvariable=self.name).grid(row=0, column=1, sticky="we", padx=8)
        self.narrate = tk.BooleanVar(value=self.state.get("narrate", True))
        ttk.Checkbutton(rec, text="Narrate: also record my voice while I explain what I do and why",
                        variable=self.narrate).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.record_btn = ttk.Button(rec, text="●  Record", style="Accent.TButton", command=self.start)
        self.record_btn.grid(row=0, column=2, rowspan=2, sticky="ns", padx=(4, 0))
        ttk.Label(rec, text="While recording: Ctrl+Alt+P pauses, Ctrl+Alt+Q stops. Pause before anything private.",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        rec.columnconfigure(1, weight=1)

        box = ttk.LabelFrame(r, text="Recordings", padding=12)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        scale = r.winfo_fpixels("1i") / 96  # column widths are in pixels
        cols = (("name", "Process", 230), ("when", "Recorded", 140), ("length", "Length", 70), ("report", "Report", 110))
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in cols], show="headings", height=6,
                                 selectmode="browse")
        for col, label, width in cols:
            self.tree.heading(col, text=label, anchor="w")
            self.tree.column(col, width=int(width * scale), minwidth=int(width * scale * 0.8), anchor="w",
                             stretch=col == "name")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._update_buttons())
        self.tree.bind("<Double-1>", lambda _: self.open_report())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        self.report_btn = ttk.Button(row, text="Make report...", command=self.make_report)
        self.open_btn = ttk.Button(row, text="Open report", command=self.open_report)
        self.sop_btn = ttk.Button(row, text="Open SOP", command=self.open_sop)
        self.build_btn = ttk.Button(row, text="Build automation...", command=self.build)
        self.compare_btn = ttk.Button(row, text="Compare...", command=self.compare)
        self.more_btn = ttk.Menubutton(row, text="More")
        more = tk.Menu(self.more_btn, tearoff=False)
        more.add_command(label="Team view...", command=self.team)
        more.add_separator()
        more.add_command(label="Open folder", command=self.open_folder)
        more.add_command(label="Send to FlowSME team...", command=self.export)
        self.more_btn["menu"] = more
        for button in (self.report_btn, self.open_btn, self.sop_btn, self.build_btn, self.compare_btn, self.more_btn):
            button.pack(side="left", padx=(0, 6))
        self.progress = ttk.Progressbar(row, mode="indeterminate", length=90)

        logs = ttk.LabelFrame(r, text="Activity", padding=(10, 6))
        logs.pack(fill="both", padx=16, pady=(0, 8))
        self.log_box = tk.Text(logs, height=7, wrap="word", state="disabled", relief="flat",
                               font=("Consolas", 9), background="#f7f7f8")
        self.log_box.pack(fill="both", expand=True)

        foot = ttk.Frame(r, padding=(16, 0, 16, 12))
        foot.pack(fill="x")
        self.engine = ttk.Label(foot, style="Muted.TLabel")
        self.engine.pack(side="left")
        ttk.Button(foot, text="Settings", command=self.settings).pack(side="right")
        ttk.Label(foot, text=f"v{__version__}", style="Muted.TLabel").pack(side="right", padx=8)

    # ---- recordings list --------------------------------------------------------------------------

    def refresh(self, select: Path | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for path in reversed(list_sessions(self.cfg.sessions_dir)):
            meta = load_meta(path)
            secs = meta.get("duration_seconds")
            self.tree.insert("", "end", iid=str(path), values=(
                _title(path),
                (meta.get("started") or "")[:16].replace("T", " "),
                fmt_minutes(secs / 60) if secs else "?",
                _status(path)))
        items = self.tree.get_children()
        pick = str(select) if select and self.tree.exists(str(select)) else (items[0] if items else None)
        if pick:
            self.tree.selection_set(pick)
            self.tree.see(pick)
        self.engine.config(text=engine_text(self.cfg))
        self._update_buttons()

    def _selected(self) -> Path | None:
        chosen = self.tree.selection()
        return Path(chosen[0]) if chosen else None

    def _update_buttons(self) -> None:
        path = self._selected()
        idle = not self.busy and self.recorder is None
        has_report = bool(path and (path / "analysis" / "report.html").exists())
        others = has_report and any(p != path and (p / "analysis" / "analysis.json").exists()
                                    for p in list_sessions(self.cfg.sessions_dir))
        for button, ok in ((self.report_btn, path), (self.open_btn, has_report), (self.sop_btn, self._sop(path)),
                           (self.build_btn, has_report), (self.compare_btn, others), (self.more_btn, path),
                           (self.record_btn, True)):
            button.state(["!disabled"] if ok and idle else ["disabled"])
        if not self.busy:  # reading is fine while a recording runs
            for button, ok in ((self.open_btn, has_report), (self.sop_btn, self._sop(path))):
                button.state(["!disabled"] if ok else ["disabled"])

    # ---- recording --------------------------------------------------------------------------------

    def start(self) -> None:
        if self.recorder is not None or self.busy:
            return
        from .recorder import Recorder

        self.cfg, _ = app_config(self.config_path)  # pick up edits to config.toml
        self.cfg.recorder.narrate = self.narrate.get()
        name = self.name.get().strip()
        self._save_state(last_name=name, narrate=self.narrate.get())
        path = new_session_dir(self.cfg.sessions_dir, name or "recording")
        self.recorder = Recorder(self.cfg, path)
        threading.Thread(target=self._record, args=(self.recorder,), name="flowsme-record", daemon=True).start()
        self._update_buttons()
        self._show_bar()
        self.root.withdraw()

    def _record(self, recorder) -> None:
        try:
            recorder.run()
        except BaseException as exc:  # noqa: BLE001 - report anything, keep the app alive
            print(f"Recording stopped: {exc}")
        self.events.put(("recorded", recorder.dir))

    def _show_bar(self) -> None:
        """A small always-on-top bar with the timer, Pause and Stop, out of the way of the work."""
        bar = tk.Toplevel(self.root)
        bar.title("FlowSME")
        bar.resizable(False, False)
        bar.attributes("-topmost", True)
        bar.protocol("WM_DELETE_WINDOW", self.stop)
        frame = ttk.Frame(bar, padding=(10, 8))
        frame.pack()
        self.dot = tk.Label(frame, text="●", fg="#d93025", font=("Segoe UI", 13))
        self.dot.pack(side="left")
        self.clock = ttk.Label(frame, text="Recording 0:00", width=15)
        self.clock.pack(side="left", padx=(4, 8))
        self.pause_btn = ttk.Button(frame, text="Pause", width=8, command=self.pause)
        self.pause_btn.pack(side="left", padx=2)
        self.stop_btn = ttk.Button(frame, text="Stop", width=8, command=self.stop)
        self.stop_btn.pack(side="left", padx=2)
        bar.update_idletasks()
        x = bar.winfo_screenwidth() - bar.winfo_reqwidth() - 24
        y = bar.winfo_screenheight() - bar.winfo_reqheight() - int(110 * self.root.winfo_fpixels("1i") / 96)
        bar.geometry(f"+{x}+{y}")
        self.bar = bar
        self._tick()

    def _tick(self) -> None:
        rec = self.recorder
        if rec is None or self.bar is None:
            return
        if not rec.stop_event.is_set():
            seconds = int(time.time() - rec.t0) if rec.t0 else 0
            label = "Paused" if rec.paused else "Recording"
            self.clock.config(text=f"{label} {seconds // 60}:{seconds % 60:02d}")
            self.dot.config(fg="#9aa0a6" if rec.paused else "#d93025")
            self.pause_btn.config(text="Resume" if rec.paused else "Pause")
        self.root.after(400, self._tick)

    def pause(self) -> None:
        if self.recorder is not None:
            self.recorder.toggle_pause()

    def stop(self) -> None:
        if self.recorder is None:
            return
        self.recorder.stop()
        self.clock.config(text="Saving...")
        self.pause_btn.state(["disabled"])
        self.stop_btn.state(["disabled"])

    def _recorded(self, path: Path) -> None:
        if self.bar is not None:
            self.bar.destroy()
            self.bar = None
        self.recorder = None
        self.root.deiconify()
        self.root.lift()
        self.refresh(select=path)
        if (path / "events.jsonl").exists():
            print(f"Saved '{_title(path)}'. Select it and click Make report.")

    # ---- reports ----------------------------------------------------------------------------------

    def make_report(self) -> None:
        """Ask how to make the report (AI or rules, the business numbers, the customer's tools), then make it."""
        path = self._selected()
        if not path or self.busy or self.recorder is not None:
            return
        cfg, _ = app_config(self.config_path)
        saved = _read_json(path / "analysis" / "options.json") or self.state.get("report_options") or {}
        ai_ready = cfg.llm.provider != "offline" and (not needs_key(cfg) or bool(saved_key(cfg.llm.api_key_env)))
        win, f = self._dialog("Report options")
        ttk.Label(f, text=f"Report for '{_title(path)}'", style="Heading.TLabel").grid(row=0, column=0, sticky="w")

        box = ttk.LabelFrame(f, text="Analysis", padding=10)
        box.grid(row=1, column=0, sticky="we", pady=(10, 0))
        mode = tk.StringVar(value=saved.get("mode", "ai") if ai_ready else "rules")
        who = f"Meta {cfg.llm.text_model}" if needs_key(cfg) else f"{cfg.llm.text_model} on this computer"
        ai_radio = ttk.Radiobutton(box, text=f"AI analysis with {who}", value="ai", variable=mode)
        ai_radio.grid(row=0, column=0, columnspan=3, sticky="w")
        if not ai_ready:
            ai_radio.state(["disabled"])
            ttk.Label(box, style="Muted.TLabel", text="Add a Meta API key in Settings to use AI." if
                      cfg.llm.provider != "offline" else "AI is switched off in the settings file.").grid(
                row=1, column=0, columnspan=3, sticky="w", padx=(22, 0))
        ttk.Radiobutton(box, text="Built-in rules only: free, nothing is sent to an AI", value="rules",
                        variable=mode).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        cap = tk.StringVar(value=str(saved.get("max_cost_usd", cfg.llm.max_cost_usd or 2.0)))
        cap_row = ttk.Frame(box)
        cap_row.grid(row=3, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(8, 0))
        cap_label = ttk.Label(cap_row, text="Stop AI spending at  $")
        cap_label.pack(side="left")
        cap_box = ttk.Spinbox(cap_row, from_=0.5, to=50, increment=0.5, width=6, textvariable=cap)
        cap_box.pack(side="left", padx=4)
        ttk.Label(cap_row, text="per report").pack(side="left")
        shots = tk.BooleanVar(value=saved.get("screenshots", cfg.llm.send_screenshots))
        shots_box = ttk.Checkbutton(box, variable=shots, text="Send screenshots to the AI (off: steps are "
                                                              "described from button and field names only)")
        shots_box.grid(row=4, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(6, 0))
        web = tk.BooleanVar(value=saved.get("web_search", cfg.analysis.web_search))
        ttk.Checkbutton(box, variable=web, text="Search the web for tools and APIs that fit").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def follow_mode(*_) -> None:
            for widget in (cap_label, cap_box, shots_box):
                widget.state(["!disabled"] if mode.get() == "ai" else ["disabled"])
        mode.trace_add("write", follow_mode)
        follow_mode()

        biz = ttk.LabelFrame(f, text="Your business (for hours and money saved)", padding=10)
        biz.grid(row=2, column=0, sticky="we", pady=(10, 0))
        runs = tk.StringVar(value=str(saved.get("runs_per_month", cfg.analysis.runs_per_month)))
        hourly = tk.StringVar(value=str(saved.get("hourly_cost", cfg.analysis.hourly_cost)))
        currency = tk.StringVar(value=saved.get("currency", cfg.analysis.currency))
        ttk.Label(biz, text="This process runs").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(biz, from_=1, to=100000, width=7, textvariable=runs).grid(row=0, column=1, padx=6)
        ttk.Label(biz, text="times a month").grid(row=0, column=2, sticky="w")
        ttk.Label(biz, text="An hour of this person's time costs").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(biz, from_=0, to=10000, width=7, textvariable=hourly).grid(row=1, column=1, padx=6, pady=(6, 0))
        ttk.Entry(biz, width=4, textvariable=currency).grid(row=1, column=2, sticky="w", pady=(6, 0))

        kit = ttk.LabelFrame(f, text="Tools the business already has (the plan shows what they can do with them)",
                             padding=10)
        kit.grid(row=3, column=0, sticky="we", pady=(10, 0))
        given = cfg.customer.tools
        chosen = set(saved["tools"]) if "tools" in saved else {t for t in given if t in TOOL_NAMES}
        picks = {}
        for n, name in enumerate(TOOL_NAMES):
            picks[name] = tk.BooleanVar(value=name in chosen)
            ttk.Checkbutton(kit, text=name, variable=picks[name]).grid(row=n // 3, column=n % 3, sticky="w",
                                                                      padx=(0, 14), pady=1)
        other = tk.StringVar(value=saved["other_tools"] if "other_tools" in saved else
                             "; ".join(t for t in given if t not in TOOL_NAMES))
        rows = (len(TOOL_NAMES) + 2) // 3
        ttk.Label(kit, text="Other tools, with what they can do:").grid(row=rows, column=0, columnspan=3,
                                                                        sticky="w", pady=(8, 0))
        ttk.Entry(kit, textvariable=other).grid(row=rows + 1, column=0, columnspan=3, sticky="we", pady=(2, 0))
        ttk.Label(kit, style="Muted.TLabel", text="e.g.  MyERP: database, reporting; Odoo: api  (separate with ;)"
                  ).grid(row=rows + 2, column=0, columnspan=3, sticky="w")

        def go() -> None:
            try:
                options = {"mode": mode.get(), "max_cost_usd": float(cap.get()), "screenshots": shots.get(),
                           "web_search": web.get(), "runs_per_month": int(float(runs.get())),
                           "hourly_cost": float(hourly.get()), "currency": currency.get().strip() or "$",
                           "tools": [name for name, var in picks.items() if var.get()],
                           "other_tools": other.get().strip()}
                if options["runs_per_month"] < 1 or options["hourly_cost"] < 0 or options["max_cost_usd"] <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("FlowSME", "Use positive numbers for the runs, the hourly cost and the "
                                                "spending limit.", parent=win)
                return
            win.destroy()
            self._save_state(report_options=options)
            (path / "analysis").mkdir(exist_ok=True)
            (path / "analysis" / "options.json").write_text(json.dumps(options, indent=1), encoding="utf-8")
            self._start_report(path, apply_options(cfg, options))

        self._buttons(win, f, 4, "Make report", go)

    def _start_report(self, path: Path, cfg: Config) -> None:
        self._busy(True)
        how = "AI" if cfg.llm.provider != "offline" else "built-in rules"
        tools = f", tools: {', '.join(cfg.customer.tools)}" if cfg.customer.tools else ""
        print(f"Making the report for '{_title(path)}' with {how}{tools}. This takes a few minutes.")
        threading.Thread(target=self._work, args=("reported", path, self._analyze, cfg), daemon=True).start()

    @staticmethod
    def _analyze(path: Path, cfg: Config) -> None:
        from .analyze import analyze_session

        analyze_session(path, cfg, log=print)

    def _work(self, kind: str, path: Path, job, *args) -> None:
        """Run a slow job off the window's thread, then tell the window how it went."""
        error = None
        try:
            job(path, *args)
        except BaseException as exc:  # noqa: BLE001 - SystemExit included: show it, keep the app alive
            error = str(exc) or exc.__class__.__name__
            print(f"Stopped: {error}")
        self.events.put((kind, path, error))

    def _busy(self, on: bool) -> None:
        self.busy = on
        if on:
            self.progress.pack(side="left", padx=(6, 0))
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        self._update_buttons()

    def _finished(self, kind: str, path: Path, error: str | None) -> None:
        self._busy(False)
        self.refresh(select=path)
        if error:
            messagebox.showerror("FlowSME", error, parent=self.root)
        elif kind == "reported":
            print("Report ready.")
            self.open_report()
        elif kind == "compared":
            os.startfile(self._compared)
        elif kind == "team":
            os.startfile(self._team_out / "team.html")
        else:
            print("Automation files ready: start with BUILD.md.")
            os.startfile(self._built_dir(path))

    # ---- build ------------------------------------------------------------------------------------

    def build(self) -> None:
        """Turn one task of the report into an n8n workflow, Postman collection, SQL and Playwright files."""
        path = self._selected()
        if not path or self.busy or self.recorder is not None:
            return
        report = _read_json(path / "analysis" / "analysis.json") or {}
        tasks = sorted((t for t in report.get("tasks", []) if t.get("integration")), key=lambda t: -t["score"])
        if not tasks:
            messagebox.showinfo("FlowSME", "No task in this report has an automation design: none showed enough "
                                           "automation potential.", parent=self.root)
            return
        win, f = self._dialog("Build automation")
        ttk.Label(f, text="Which task should be automated?", style="Heading.TLabel").grid(row=0, column=0,
                                                                                         sticky="w")
        scale = self.root.winfo_fpixels("1i") / 96
        tree = ttk.Treeview(f, columns=("task", "potential", "saved"), show="headings", height=min(len(tasks), 5),
                            selectmode="browse")
        for col, label, width in (("task", "Task", 420), ("potential", "Potential", 110),
                                  ("saved", "Saves per run", 110)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=int(width * scale), anchor="w")
        for t in tasks:
            minutes = (t.get("estimate") or {}).get("minutes_saved_per_run") or 0
            tree.insert("", "end", iid=str(t["id"]), values=(f"{t['id']}. {t['name']}",
                                                            f"{t['level']} ({t['score']:.0f})", fmt_minutes(minutes)))
        tree.selection_set(str(tasks[0]["id"]))
        tree.grid(row=1, column=0, sticky="we", pady=(8, 0))
        ttk.Label(f, style="Muted.TLabel", wraplength=int(520 * scale), justify="left", text=(
            "Writes an n8n workflow to import, a Postman collection to test each connection, and SQL and "
            "Playwright starters, with BUILD.md listing what a person must fill in. Nothing runs until you "
            "review it.")).grid(row=2, column=0, sticky="w", pady=(8, 0))

        n8n = ttk.LabelFrame(f, text="n8n", padding=10)
        n8n.grid(row=3, column=0, sticky="we", pady=(10, 0))
        deploy = tk.BooleanVar(value=self.state.get("deploy_n8n", False))
        url = tk.StringVar(value=saved_key("N8N_URL") or "http://localhost:5678")
        key = tk.StringVar()
        ttk.Checkbutton(n8n, text="Also create the workflow in my n8n (switched off until I activate it)",
                        variable=deploy).grid(row=0, column=0, columnspan=2, sticky="w")
        fields = [ttk.Label(n8n, text="n8n address"), ttk.Entry(n8n, textvariable=url, width=36),
                  ttk.Label(n8n, text="n8n API key"), ttk.Entry(n8n, textvariable=key, show="•", width=36)]
        for n, widget in enumerate(fields):
            widget.grid(row=1 + n // 2, column=n % 2, sticky="w", pady=(6, 0), padx=(0, 8))
        ttk.Label(n8n, style="Muted.TLabel", text="A key is saved; leave the box empty to keep it." if
                  saved_key("N8N_API_KEY") else "In n8n: Settings > n8n API > Create an API key.").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        def follow(*_) -> None:
            for widget in fields:
                widget.state(["!disabled"] if deploy.get() else ["disabled"])
        deploy.trace_add("write", follow)
        follow()

        def go() -> None:
            if deploy.get():
                if key.get().strip():
                    store_key("N8N_API_KEY", key.get().strip())
                if not url.get().strip() or not saved_key("N8N_API_KEY"):
                    messagebox.showerror("FlowSME", "Enter the n8n address and an API key, or untick the n8n box.",
                                         parent=win)
                    return
                store_key("N8N_URL", url.get().strip())
            task_id = int(tree.selection()[0])
            win.destroy()
            self._save_state(deploy_n8n=deploy.get())
            self._busy(True)
            print(f"Building task {task_id}" + (" and creating it in n8n" if deploy.get() else "") + "...")
            threading.Thread(target=self._work, args=("built", path, self._build_job, task_id, deploy.get()),
                             daemon=True).start()

        self._buttons(win, f, 4, "Build", go)

    @staticmethod
    def _build_job(path: Path, task_id: int, deploy: bool) -> None:
        from .build import build_task

        build_task(path, task_id, deploy=deploy, log=print)

    @staticmethod
    def _built_dir(path: Path) -> Path:
        folder = path / "analysis" / "automations"
        builds = sorted(folder.glob("task*"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
        return builds[-1] if builds else path

    def _dialog(self, title: str) -> tuple[tk.Toplevel, ttk.Frame]:
        win = tk.Toplevel(self.root)
        win.title(title)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        return win, frame

    def _buttons(self, win: tk.Toplevel, frame: ttk.Frame, row: int, label: str, action) -> None:
        bar = ttk.Frame(frame)
        bar.grid(row=row, column=0, sticky="e", pady=(14, 0))
        ttk.Button(bar, text=label, style="Accent.TButton", command=action).pack(side="left")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left", padx=(8, 0))
        win.bind("<Escape>", lambda _: win.destroy())
        win.update_idletasks()  # centre on the main window
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_reqheight()) // 2)
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def open_report(self) -> None:
        path = self._selected()
        if path and (path / "analysis" / "report.html").exists():
            os.startfile(path / "analysis" / "report.html")

    @staticmethod
    def _sop(path: Path | None) -> Path | None:
        """The SOP of the recording's best opportunity, if the report made one."""
        report = _read_json(path / "analysis" / "analysis.json") if path else None
        tasks = [t for t in (report or {}).get("tasks", []) if (t.get("improvement") or {}).get("sop")]
        if not tasks:
            return None
        best = max(tasks, key=lambda t: t["score"])
        sop = path / "analysis" / best["improvement"]["sop"]
        return sop if sop.exists() else None

    def open_sop(self) -> None:
        sop = self._sop(self._selected())
        if sop:
            os.startfile(sop)

    def compare(self) -> None:
        """Before/after: pick the recording made before the change; the selected one is the after."""
        after = self._selected()
        if not after or self.busy or self.recorder is not None:
            return
        before_options = [p for p in reversed(list_sessions(self.cfg.sessions_dir))
                          if p != after and (p / "analysis" / "analysis.json").exists()]
        win, f = self._dialog("Compare before and after")
        scale = self.root.winfo_fpixels("1i") / 96
        ttk.Label(f, text="Which recording was made before the change?", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=int(520 * scale), justify="left", text=(
            f"'{_title(after)}' is the after. FlowSME compares the time per item, how much items vary, the steps "
            "and the values entered by hand, and tests whether the saving is bigger than the normal run-to-run "
            "variation. Record at least 2 or 3 items each time.")).grid(row=1, column=0, sticky="w", pady=(4, 8))
        tree = ttk.Treeview(f, columns=("name", "when"), show="headings", height=min(max(len(before_options), 1), 6),
                            selectmode="browse")
        for col, text, width in (("name", "Process", 300), ("when", "Recorded", 150)):
            tree.heading(col, text=text, anchor="w")
            tree.column(col, width=int(width * scale), anchor="w")
        for p in before_options:
            tree.insert("", "end", iid=str(p), values=(_title(p), (load_meta(p).get("started") or "")[:16]
                                                      .replace("T", " ")))
        tree.grid(row=2, column=0, sticky="we")
        counts = ttk.Frame(f)
        counts.grid(row=3, column=0, sticky="w", pady=(10, 0))
        before_items, after_items = tk.StringVar(), tk.StringVar(value=str(self._items(after)))
        ttk.Label(counts, text="Items done in the before recording").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(counts, from_=1, to=10000, width=6, textvariable=before_items).grid(row=0, column=1, padx=6)
        ttk.Label(counts, text="in the after recording").grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Spinbox(counts, from_=1, to=10000, width=6, textvariable=after_items).grid(row=0, column=3, padx=6)
        ttk.Label(counts, style="Muted.TLabel", text="FlowSME's count of repeated items; correct it if you did "
                  "more.").grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 0))
        tree.bind("<<TreeviewSelect>>", lambda _: before_items.set(str(self._items(Path(tree.selection()[0])))))
        if before_options:
            tree.selection_set(str(before_options[0]))
            before_items.set(str(self._items(before_options[0])))

        def go() -> None:
            if not tree.selection():
                return
            before = Path(tree.selection()[0])
            try:
                items = (int(before_items.get()), int(after_items.get()))
                if min(items) < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("FlowSME", "Enter how many items each recording covered (1 or more).",
                                     parent=win)
                return
            win.destroy()
            self._compared = after / "analysis" / f"compare-{before.name}" / "comparison.html"
            self._busy(True)
            print(f"Comparing '{_title(before)}' (before) with '{_title(after)}' (after)...")
            threading.Thread(target=self._work, args=("compared", after, self._compare_job, before, *items),
                             daemon=True).start()

        self._buttons(win, f, 4, "Compare", go)

    def team(self) -> None:
        """Several people's recordings of the same process: who is fastest, and what the others do differently."""
        if self.busy or self.recorder is not None:
            return
        options = [p for p in reversed(list_sessions(self.cfg.sessions_dir))
                   if (p / "analysis" / "analysis.json").exists()]
        if len(options) < 2:
            messagebox.showinfo("FlowSME", "Make reports for at least two recordings of the same process first, "
                                           "one per person.", parent=self.root)
            return
        win, f = self._dialog("Team view")
        scale = self.root.winfo_fpixels("1i") / 96
        ttk.Label(f, text="Which recordings show the same process done by different people?",
                  style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(f, style="Muted.TLabel", text="Select two or more (Ctrl+click). Give each the person's name and "
                  "the number of items they did.").grid(row=1, column=0, sticky="w", pady=(2, 8))
        tree = ttk.Treeview(f, columns=("person", "items", "name", "when"), show="headings",
                            height=min(len(options), 8), selectmode="extended")
        for col, text, width in (("person", "Person", 130), ("items", "Items", 60), ("name", "Process", 220),
                                 ("when", "Recorded", 140)):
            tree.heading(col, text=text, anchor="w")
            tree.column(col, width=int(width * scale), anchor="w")
        for n, p in enumerate(options, 1):
            tree.insert("", "end", iid=str(p), values=(f"Person {n}", self._items(p), _title(p),
                                                      (load_meta(p).get("started") or "")[:16].replace("T", " ")))
        tree.grid(row=2, column=0, sticky="we")
        edit = ttk.Frame(f)
        edit.grid(row=3, column=0, sticky="w", pady=(8, 0))
        person, items = tk.StringVar(), tk.StringVar()
        ttk.Label(edit, text="Selected: name").pack(side="left")
        name_box = ttk.Entry(edit, textvariable=person, width=20)
        name_box.pack(side="left", padx=6)
        ttk.Label(edit, text="items done").pack(side="left", padx=(8, 0))
        items_box = ttk.Spinbox(edit, from_=1, to=10000, width=6, textvariable=items)
        items_box.pack(side="left", padx=6)

        def show(_=None) -> None:
            chosen = tree.selection()
            person.set(tree.set(chosen[-1], "person") if chosen else "")
            items.set(tree.set(chosen[-1], "items") if chosen else "")

        def edit_one(*_) -> None:
            chosen = tree.selection()
            if not chosen:
                return
            if person.get().strip():
                tree.set(chosen[-1], "person", person.get().strip())
            if items.get().strip().isdigit() and int(items.get()) > 0:
                tree.set(chosen[-1], "items", items.get().strip())
        tree.bind("<<TreeviewSelect>>", show)
        name_box.bind("<KeyRelease>", edit_one)
        items_box.bind("<KeyRelease>", edit_one)
        items_box.configure(command=edit_one)

        def go() -> None:
            chosen = list(tree.selection())
            if len(chosen) < 2:
                messagebox.showerror("FlowSME", "Select two or more recordings.", parent=win)
                return
            names = [tree.set(i, "person") for i in chosen]
            counts = [int(tree.set(i, "items")) for i in chosen]
            win.destroy()
            self._team_out = self.cfg.sessions_dir / "team-views" / time.strftime("%Y%m%d-%H%M%S")
            self._busy(True)
            print(f"Team view of {len(chosen)} recordings...")
            threading.Thread(target=self._work, args=("team", Path(chosen[0]), self._team_job,
                                                     [Path(i) for i in chosen], names, counts, self._team_out),
                             daemon=True).start()

        self._buttons(win, f, 4, "Show team view", go)

    @staticmethod
    def _team_job(_first: Path, sessions: list[Path], names: list[str], counts: list[int], out: Path) -> None:
        from .team import team_view

        team_view(sessions, names, out, log=print, items=counts)

    @staticmethod
    def _items(path: Path) -> int:
        """Items FlowSME found in a recording's routine (its repeated runs)."""
        try:
            from .measure import summarize

            return summarize(path)["detected_runs"]
        except (SystemExit, OSError, ValueError, KeyError):
            return 1

    @staticmethod
    def _compare_job(after: Path, before: Path, before_items: int, after_items: int) -> None:
        from .measure import compare

        compare(before, after, log=print, before_items=before_items, after_items=after_items)

    def open_folder(self) -> None:
        path = self._selected()
        if path:
            os.startfile(path)

    def export(self) -> None:
        path = self._selected()
        if not path:
            return
        parts = ["the screenshots taken while you recorded"]
        if (path / "narration.wav").exists():
            parts.append("your narration")
        if (path / "analysis" / "report.html").exists():
            parts.append("the report")
        typed = ("" if self.cfg.recorder.capture_typed_text else " The text you typed was never stored, so it "
                 "isn't included.")
        if not messagebox.askokcancel("Save for the FlowSME team",
                                      f"This saves the recording as a zip file on your Desktop for you to send "
                                      f"to the FlowSME team. It contains {', '.join(parts)}.{typed}\n\n"
                                      "Save it?", parent=self.root):
            return
        dest = _desktop() / f"FlowSME-{path.name}.zip"
        shutil.make_archive(str(dest.with_suffix("")), "zip", root_dir=path.parent, base_dir=path.name)
        print(f"Saved {dest}")
        subprocess.Popen(["explorer", f"/select,{dest}"])

    # ---- settings and first run -------------------------------------------------------------------

    def settings(self) -> None:
        env = self.cfg.llm.api_key_env
        win = tk.Toplevel(self.root)
        win.title("FlowSME settings")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both")
        ttk.Label(frame, text="Meta API key", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=f"Used for AI reports with Muse Spark. Saved for your Windows user as {env}.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))
        status = ttk.Label(frame)
        status.grid(row=2, column=0, columnspan=3, sticky="w")
        value = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=value, show="•", width=46)
        entry.grid(row=3, column=0, sticky="we", pady=8)

        def show() -> None:
            status.config(text="A key is saved." if saved_key(env) else "No key saved yet.")
            self.engine.config(text=engine_text(self.cfg))

        def save() -> None:
            key = value.get().strip()
            if key:
                store_key(env, key)
                value.set("")
                print("API key saved.")
                show()

        def remove() -> None:
            if saved_key(env) and messagebox.askyesno("FlowSME", "Remove the saved API key?", parent=win):
                store_key(env, "")
                print("API key removed.")
                show()

        ttk.Button(frame, text="Save key", command=save).grid(row=3, column=1, padx=(8, 0))
        ttk.Button(frame, text="Remove", command=remove).grid(row=3, column=2, padx=(6, 0))
        ttk.Separator(frame).grid(row=4, column=0, columnspan=3, sticky="we", pady=12)
        links = ttk.Frame(frame)
        links.grid(row=5, column=0, columnspan=3, sticky="w")
        ttk.Button(links, text="Edit all settings", command=lambda: subprocess.Popen(
            ["notepad.exe", str(self.cfg_path.resolve())])).pack(side="left")
        ttk.Button(links, text="Open recordings folder", command=lambda: (
            self.cfg.sessions_dir.mkdir(parents=True, exist_ok=True), os.startfile(self.cfg.sessions_dir))
        ).pack(side="left", padx=6)
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=6, column=2, sticky="e", pady=(14, 0))
        show()
        entry.focus_set()

    def _welcome(self) -> None:
        if messagebox.askokcancel("Welcome to FlowSME", WELCOME, parent=self.root):
            self._save_state(accepted=True)
        else:
            self.root.destroy()

    def _load_state(self) -> dict:
        with contextlib.suppress(OSError, ValueError):
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    def _save_state(self, **values) -> None:
        self.state.update(values)
        with contextlib.suppress(OSError):
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    # ---- plumbing ---------------------------------------------------------------------------------

    def _pump(self) -> None:
        """Handle what the recording and report threads sent (Tk may only be touched from this thread)."""
        lines = []
        with contextlib.suppress(queue.Empty):
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    lines.append(event[1])
                elif event[0] == "recorded":
                    self._recorded(event[1])
                elif event[0] in ("reported", "built", "compared", "team"):
                    self._finished(*event)
        if lines:
            self.log_box.config(state="normal")
            self.log_box.insert("end", "\n".join(lines) + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.root.after(150, self._pump)

    def quit(self) -> None:
        if self.recorder is not None:
            messagebox.showinfo("FlowSME", "Stop the recording first.", parent=self.root)
            return
        if self.busy and not messagebox.askyesno("FlowSME", "A report is still being made. Quit anyway?",
                                                 parent=self.root):
            return
        self.root.destroy()


def _style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    scale = root.winfo_fpixels("1i") / 96
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):  # not option_add: it would
        tkfont.nametofont(name).configure(family="Segoe UI", size=10)          # override the styles below
    style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
    style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Muted.TLabel", foreground="#5f6368")
    style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 8))
    style.configure("Treeview", rowheight=int(24 * scale))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
    root.geometry(f"{int(720 * scale)}x{int(600 * scale)}")
    root.minsize(int(600 * scale), int(520 * scale))


def self_test() -> tuple[bool, str]:
    """Checks the parts an installed build can miss: input hooks, UI Automation, screen capture, microphone, the
    AI and search clients, and a short recording plus an offline report. Records nothing from the microphone."""
    import tempfile

    results = []

    def check(name: str, fn) -> None:
        try:
            results.append(f"ok    {name}: {fn()}")
        except BaseException as exc:  # noqa: BLE001 - report every failure, keep checking
            results.append(f"FAIL  {name}: {exc!r}")

    def hooks():
        from pynput import keyboard, mouse

        return f"{keyboard.Listener.__module__}, {mouse.Listener.__module__}"

    def automation():
        import uiautomation as auto

        return f"{len(auto.GetRootControl().GetChildren())} top-level windows"

    def screen():
        import mss

        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            return f"{len(sct.monitors) - 1} monitor(s)"

    def microphone():
        import sounddevice as sd

        return f"{sum(1 for d in sd.query_devices() if d['max_input_channels'] > 0)} input device(s)"

    def clients():
        import openai
        from ddgs import DDGS

        return f"openai {openai.__version__}, {DDGS.__name__}"

    def record_and_report():
        from .analyze import analyze_session
        from .demo import build_demo
        from .recorder import Recorder

        tmp = Path(tempfile.mkdtemp(prefix="flowsme-check-"))
        try:
            cfg = Config()
            cfg.llm.provider, cfg.analysis.web_search = "offline", False
            rec = Recorder(cfg, tmp / "recording")
            threading.Timer(2, rec.stop).start()
            rec.run()
            report = analyze_session(build_demo(tmp), cfg, log=lambda *_: None)["paths"]["html"]
            return f"2-second recording saved: {(tmp / 'recording' / 'meta.json').exists()}; " \
                   f"demo report written: {report.exists()}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    for name, fn in (("input hooks", hooks), ("UI Automation", automation), ("screen capture", screen),
                     ("microphone", microphone), ("AI and search clients", clients),
                     ("record and report", record_and_report)):
        check(name, fn)
    ok = not any(line.startswith("FAIL") for line in results)
    return ok, f"FlowSME {__version__} self-test: {'all good' if ok else 'problems found'}\n\n" + "\n".join(results)


def main(config_path: str | None = None, check: bool = False) -> None:
    if sys.platform != "win32":
        raise SystemExit("The FlowSME app runs on Windows (analysis works anywhere from the command line).")
    with contextlib.suppress(Exception):  # sharp text, and clicks that line up with screenshots
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    if check:
        ok, text = self_test()
        out = Path(os.environ.get("TEMP", ".")) / "FlowSME-self-test.txt"
        out.write_text(text, encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, f"{text}\n\nSaved to {out}", "FlowSME", 0x40 if ok else 0x10)
        return
    with contextlib.suppress(Exception):  # its own taskbar button and icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FlowSME.App")
    # One FlowSME at a time: two recorders would record everything twice.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FlowSME.App")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(None, "FlowSME is already open.", "FlowSME", 0x40)
        return
    root = tk.Tk()
    icon = _icon()
    if icon:
        with contextlib.suppress(tk.TclError):
            root.iconbitmap(default=str(icon))
    _style(root)
    App(root, config_path)
    root.mainloop()
    del mutex
