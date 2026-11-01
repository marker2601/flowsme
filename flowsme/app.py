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
import zipfile
from pathlib import Path
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__, org
from .config import Config, load_config
from .models import fmt_minutes
from .session import import_recording, list_sessions, load_meta, new_session_dir
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
    cfg.analysis.hourly_cost = options.get("hourly_cost", cfg.analysis.hourly_cost)
    cfg.analysis.currency = options.get("currency", cfg.analysis.currency)
    cfg.analysis.position = options.get("position", "")
    cfg.analysis.show_money = options.get("show_money", True)
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


def _recycle(path: Path) -> bool:
    """Move a folder to the Recycle Bin, where it can be restored."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]

    # FO_DELETE with undo (Recycle Bin), no confirmation (we asked), silent, no error dialogs
    op = SHFILEOPSTRUCTW(None, 3, str(path.resolve()) + "\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, 0, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted and \
        not path.exists()


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
        self.home = self.cfg_path.resolve().parent  # team.json / seat.json live beside the settings
        self.state_path = self.home / "app.json"
        self.state = self._load_state()
        self.unlocked = False  # the admin PIN was entered in this session
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
        tk.Label(top, text="F", bg="#3355d1", fg="#ffffff", font=("Segoe UI", 13, "bold"), width=2).pack(
            side="left", padx=(0, 10))
        ttk.Label(top, text="FlowSME", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="Record your work. Get the workflow, what to automate and the minutes saved.",
                  style="Muted.TLabel").pack(side="left", padx=12, pady=(4, 0))
        team = ttk.Frame(r, padding=(16, 2, 16, 0))  # who is using it: on their own, a manager or an employee
        team.pack(fill="x")
        self.who = ttk.Label(team, style="Muted.TLabel")
        self.who.pack(side="left")
        self.admin_btn = ttk.Button(team, command=self.admin)
        self.admin_btn.pack(side="right")

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
        self.hint = ttk.Label(rec, style="Muted.TLabel")
        self.hint.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        rec.columnconfigure(1, weight=1)

        box = ttk.LabelFrame(r, text="Recordings", padding=12)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        scale = r.winfo_fpixels("1i") / 96  # column widths are in pixels
        cols = (("name", "Process", 200), ("by", "By", 100), ("when", "Recorded", 125), ("length", "Length", 65),
                ("report", "Report", 110))
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in cols], show="headings", height=6,
                                 selectmode="browse")
        for col, label, width in cols:
            self.tree.heading(col, text=label, anchor="w")
            self.tree.column(col, width=int(width * scale), minwidth=int(width * scale * 0.8), anchor="w",
                             stretch=True)  # all share the window, so the last column never falls off
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
        self.more = tk.Menu(self.more_btn, tearoff=False)
        self.more_btn["menu"] = self.more
        for button in (self.report_btn, self.open_btn, self.sop_btn, self.build_btn, self.compare_btn, self.more_btn):
            button.pack(side="left", padx=(0, 6))
        self.progress = ttk.Progressbar(row, mode="indeterminate", length=90)

        self.tabs = ttk.Notebook(r)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        results = ttk.Frame(self.tabs, padding=(10, 8))
        self.tabs.add(results, text="Results")
        self.summary = ttk.Label(results, wraplength=int(840 * scale), justify="left")
        self.summary.pack(fill="x")
        cols = (("id", "#", 34), ("task", "Task", 260), ("potential", "Potential", 100), ("saves", "Saves per item", 100),
                ("month", "A month", 110), ("phase", "Roadmap", 80))
        self.results = ttk.Treeview(results, columns=[c for c, _, _ in cols], show="headings", height=4,
                                    selectmode="browse")
        for col, label, width in cols:
            self.results.heading(col, text=label, anchor="w")
            self.results.column(col, width=int(width * scale), anchor="w", stretch=col == "task")
        self.results.pack(fill="both", expand=True, pady=(6, 0))
        self.results.bind("<Double-1>", lambda _: self.open_report())
        said = ttk.Frame(self.tabs, padding=(10, 8))
        self.tabs.add(said, text="Narration")
        self.narration_box = tk.Text(said, height=6, wrap="word", state="disabled", relief="flat",
                                     font=("Segoe UI", 9), background="#ffffff")
        self.narration_box.pack(fill="both", expand=True)
        logs = ttk.Frame(self.tabs, padding=(10, 8))
        self.tabs.add(logs, text="Activity")
        self.log_box = tk.Text(logs, height=6, wrap="word", state="disabled", relief="flat",
                               font=("Consolas", 9), background="#ffffff")
        self.log_box.pack(fill="both", expand=True)

        foot = ttk.Frame(r, padding=(16, 0, 16, 12))
        foot.pack(fill="x")
        self.engine = ttk.Label(foot, style="Muted.TLabel")
        self.engine.pack(side="left")
        ttk.Button(foot, text="Settings", command=self.settings).pack(side="right")
        ttk.Label(foot, text=f"v{__version__}", style="Muted.TLabel").pack(side="right", padx=8)

    # ---- recordings list --------------------------------------------------------------------------

    def refresh(self, select: Path | None = None) -> None:
        self._read_team()
        self.tree.delete(*self.tree.get_children())
        for path in reversed(list_sessions(self.cfg.sessions_dir)):
            meta = load_meta(path)
            secs = meta.get("duration_seconds")
            self.tree.insert("", "end", iid=str(path), values=(
                _title(path),
                self._by(path),
                (meta.get("started") or "")[:16].replace("T", " "),
                fmt_minutes(secs / 60) if secs else "?",
                _status(path)))
        items = self.tree.get_children()
        pick = str(select) if select and self.tree.exists(str(select)) else (items[0] if items else None)
        if pick:
            self.tree.selection_set(pick)
            self.tree.see(pick)
        self.engine.config(text=engine_text(self.cfg))
        keys = self.cfg.recorder
        self.hint.config(text=f"While recording: {keys.pause_hotkey.title()} pauses, {keys.stop_hotkey.title()} stops. "
                              "Pause before anything private.")
        self._update_buttons()

    def _selected(self) -> Path | None:
        chosen = self.tree.selection()
        return Path(chosen[0]) if chosen else None

    # ---- team: solo, manager or employee ------------------------------------------------------------

    def _read_team(self) -> None:
        """Which of the three ways this app is used, and what the header, list and menu show for it."""
        self.mode = org.mode(self.home)
        self.org = org.Org(self.home) if self.mode != "employee" else None
        self.seat = org.my_seat(self.home) if self.mode == "employee" else None
        self.seat_state = org.seat_status(self.seat) if self.seat else None
        if self.mode == "manager":
            self.who.config(text=f"Manager · {self.org.team['name']}", foreground="")
            self.admin_btn.config(text="Admin panel")
            self.admin_btn.pack(side="right")
        elif self.mode == "employee":
            revoked = self.seat_state == "revoked"
            self.who.config(text=f"{self.seat['name']} · {self.seat['position']} · {self.seat['team_name']}"
                                 + ("  ·  seat revoked: ask your manager" if revoked else ""),
                            foreground="#d93025" if revoked else "")
            self.admin_btn.pack_forget()
        else:
            self.who.config(text="Using FlowSME on your own", foreground="")
            self.admin_btn.config(text="Team and rates...")
            self.admin_btn.pack(side="right")
        self.tree.configure(displaycolumns=[c for c in ("name", "by", "when", "length", "report")
                                            if c != "by" or self.mode == "manager"])
        m = self.more
        m.delete(0, "end")
        if self.mode != "employee":
            m.add_command(label="Team view...", command=self.team)
            m.add_separator()
        m.add_command(label="Rename...", command=self.rename)
        m.add_command(label="Delete...", command=self.delete)
        m.add_command(label="Open folder", command=self.open_folder)
        if self.mode == "employee":
            m.add_command(label="Send to my manager...", command=self.export)
        else:
            m.add_command(label="Send to FlowSME team...", command=self.export)
        m.add_separator()
        m.add_command(label="Import a recording (.zip)...", command=self.import_zip)
        m.add_command(label="Import a screen video...", command=self.import_video)
        m.add_command(label="Create demo recording", command=self.demo)
        m.add_separator()
        if self.mode == "employee":
            m.add_command(label="Leave the team...", command=self.leave_team)
        elif self.mode == "solo":
            m.add_command(label="Set up a team...", command=lambda: self.admin("team"))
            m.add_command(label="Join a team (seat file)...", command=self.join_team)
        else:
            m.add_command(label="Admin panel...", command=self.admin)

    def _by(self, path: Path) -> str:
        """Who recorded it, for the manager: the person on the seat it was made on."""
        if self.mode != "manager":
            return ""
        seat = org.recording_seat(path)
        if not seat:
            return "me"
        status, member = self.org.check_seat(seat)
        name = member["name"] if member else seat.get("name", "?")
        return name if status == "ok" else f"{name} ({status})"

    def admin(self, tab: str | None = None) -> None:
        from .admin_ui import open_admin

        open_admin(self, tab)

    def join_team(self) -> None:
        if self.mode == "manager":
            messagebox.showinfo("FlowSME", "This app manages a team, so it can't join one.", parent=self.root)
            return
        file = filedialog.askopenfilename(parent=self.root, title="Open the seat file your manager sent",
                                          filetypes=[("FlowSME seat", "*.flowseat"), ("All files", "*.*")])
        if not file:
            return
        try:
            seat = org.read_seat_file(Path(file))
        except ValueError as exc:
            messagebox.showerror("FlowSME", str(exc), parent=self.root)
            return
        folder = seat.get("folder", "")
        if folder and not Path(folder).exists():
            if messagebox.askyesno("FlowSME", f"Your team shares recordings through the folder\n{folder}\n\nIt isn't "
                                              "at that place on this computer. Choose where it is here (for example "
                                              "in your OneDrive)? Choose No to send recordings by email instead.",
                                   parent=self.root):
                folder = filedialog.askdirectory(parent=self.root, title="The team folder on this computer") or ""
            else:
                folder = ""
        try:
            org.join(self.home, seat, folder)
        except ValueError as exc:
            messagebox.showerror("FlowSME", str(exc), parent=self.root)
            return
        self.refresh()
        messagebox.showinfo("FlowSME", f"You joined {seat['team_name']} as {seat['name']}, {seat['position']}.\n\n"
                                       "Record as usual, then use More > Send to my manager.", parent=self.root)

    def leave_team(self) -> None:
        if self.seat and messagebox.askyesno("FlowSME", f"Leave {self.seat['team_name']}? Your recordings stay on "
                                                        "this computer.", parent=self.root):
            org.leave(self.home)
            self.refresh()

    def _update_buttons(self) -> None:
        path = self._selected()
        idle = not self.busy and self.recorder is None
        has_report = bool(path and (path / "analysis" / "report.html").exists())
        others = has_report and any(p != path and (p / "analysis" / "analysis.json").exists()
                                    for p in list_sessions(self.cfg.sessions_dir))
        self._show_results(path)
        for button, ok in ((self.report_btn, path), (self.open_btn, has_report), (self.sop_btn, self._sop(path)),
                           (self.build_btn, has_report), (self.compare_btn, others), (self.more_btn, path),
                           (self.record_btn, True)):
            button.state(["!disabled"] if ok and idle else ["disabled"])
        self.more_btn.state(["!disabled"] if idle else ["disabled"])  # Import and the demo need no selection
        if not self.busy:  # reading is fine while a recording runs
            for button, ok in ((self.open_btn, has_report), (self.sop_btn, self._sop(path))):
                button.state(["!disabled"] if ok else ["disabled"])

    # ---- recording --------------------------------------------------------------------------------

    def start(self) -> None:
        if self.recorder is not None or self.busy:
            return
        if self.mode == "employee":
            self.seat_state = org.seat_status(self.seat)
            if self.seat_state in ("revoked", "unknown"):
                messagebox.showerror("FlowSME", "Your manager has revoked your seat, so FlowSME can't record for the "
                                                "team. Ask them for a new seat file.", parent=self.root)
                return
        from .recorder import Recorder

        self.cfg, _ = app_config(self.config_path)  # pick up edits to config.toml
        self.cfg.recorder.narrate = self.narrate.get()
        name = self.name.get().strip()
        self._save_state(last_name=name, narrate=self.narrate.get())
        path = new_session_dir(self.cfg.sessions_dir, name or "recording")
        if self.seat:
            org.stamp(path, self.seat)
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
        if self.mode == "employee" and not org.can_make_reports(self.seat):
            messagebox.showinfo("FlowSME", "Your manager makes the reports for your team. Use More > Send to my "
                                           "manager to send them this recording.", parent=self.root)
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

        employee = self.mode == "employee"
        biz = ttk.LabelFrame(f, text="Your business (for hours saved)" if employee else
                             "Your business (for hours and money saved)", padding=10)
        biz.grid(row=2, column=0, sticky="we", pady=(10, 0))
        runs = tk.StringVar(value=str(saved.get("runs_per_month", cfg.analysis.runs_per_month)))
        ttk.Label(biz, text="This process runs").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(biz, from_=1, to=100000, width=7, textvariable=runs).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(biz, text="times a month").grid(row=0, column=2, sticky="w")
        ttk.Label(biz, text="Done by").grid(row=1, column=0, sticky="w", pady=(6, 0))
        if employee:
            position = tk.StringVar(value=self.seat["position"])
            ttk.Label(biz, text=f"{self.seat['position']} (your position)").grid(row=1, column=1, columnspan=2,
                                                                               sticky="w", padx=6, pady=(6, 0))
            ttk.Label(biz, style="Muted.TLabel", text="Reports here show hours. Your manager's copy adds what the "
                      "time is worth.").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        else:
            o = self.org
            member = o.check_seat(org.recording_seat(path))[1]
            start = member["position"] if member else saved.get("position")
            position = tk.StringVar(value=start if start in o.position_names() else o.default_position)
            ttk.Combobox(biz, textvariable=position, values=o.position_names(), state="readonly", width=26).grid(
                row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
            rate = ttk.Label(biz, style="Muted.TLabel")
            rate.grid(row=1, column=3, sticky="w", pady=(6, 0))
            show = lambda *_: rate.config(text=f"{o.currency}{o.rate(position.get()) or 0:g} an hour")
            position.trace_add("write", show)
            show()
            ttk.Label(biz, style="Muted.TLabel", text=(f"Recorded by {member['name']}. " if member else "") +
                      "Positions and rates: " + ("Admin panel." if o.team else "Team and rates.")).grid(
                row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

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
                           "position": position.get(),
                           "tools": [name for name, var in picks.items() if var.get()],
                           "other_tools": other.get().strip()}
                if options["runs_per_month"] < 1 or options["max_cost_usd"] <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("FlowSME", "Use positive numbers for the runs and the spending limit.",
                                     parent=win)
                return
            if employee:  # no rate on this computer: hours only
                options.update(hourly_cost=0, show_money=False)
            else:
                options.update(hourly_cost=self.org.rate(position.get()) or 0, currency=self.org.currency,
                               show_money=True)
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
            self.tabs.select(2)
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
            self.tabs.select(0)
            self.open_report()
        elif kind == "compared":
            os.startfile(self._compared)
        elif kind == "team":
            os.startfile(self._team_out / "team.html")
        elif kind == "video":
            self.refresh(select=self._imported)
            print(f"The video is now a recording: '{_title(self._imported)}'. Choose the report options.")
            self.make_report()
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
        tasks = sorted((t for t in report.get("tasks", []) if t.get("integration") and not t.get("same_as")),
                       key=lambda t: -t["score"])
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
                                  ("saved", "Saves each time", 110)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=int(width * scale), anchor="w")
        for t in tasks:
            minutes = (t.get("estimate") or {}).get("minutes_saved_per_run") or 0
            tree.insert("", "end", iid=str(t["id"]), values=(f"{t['id']}. {t['name']}",
                                                            f"{t['level']} ({t['score']:.0f})", fmt_minutes(minutes)))
        tree.selection_set(str(tasks[0]["id"]))
        tree.grid(row=1, column=0, sticky="we", pady=(8, 0))
        ttk.Label(f, style="Muted.TLabel", wraplength=int(560 * scale), justify="left", text=(
            "Writes the automation for n8n, Node-RED, Windmill, Make, Power Automate and Azure Logic Apps, a "
            "Postman collection to test each connection, and SQL and Playwright starters, with BUILD.md listing "
            "what a person must fill in. Nothing runs until you review it.")).grid(row=2, column=0, sticky="w",
                                                                                   pady=(8, 0))
        from .deploy import TARGETS

        box = ttk.LabelFrame(f, text="Also create it in my workflow tool (switched off until I turn it on)",
                             padding=10)
        box.grid(row=3, column=0, sticky="we", pady=(10, 0))
        ids = [None, *TARGETS]
        names = ["No: just write the files"] + [t.name + ("" if t.tested else "  (beta)") for t in TARGETS.values()]
        last = self.state.get("deploy_target", "n8n" if self.state.get("deploy_n8n") else None)
        choice = tk.StringVar(value=names[ids.index(last)] if last in ids else names[0])
        ttk.Combobox(box, textvariable=choice, values=names, state="readonly", width=32).grid(row=0, column=0,
                                                                                            sticky="w")
        test_btn = ttk.Button(box, text="Test connection")
        test_btn.grid(row=0, column=1, sticky="w", padx=8)
        fields = ttk.Frame(box)
        fields.grid(row=1, column=0, columnspan=2, sticky="we")
        about = ttk.Label(box, style="Muted.TLabel", wraplength=int(540 * scale), justify="left")
        about.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        entries: dict[str, tk.StringVar] = {}

        def target():
            return TARGETS.get(ids[names.index(choice.get())])

        def show(*_) -> None:
            for widget in fields.winfo_children():
                widget.destroy()
            entries.clear()
            t = target()
            test_btn.state(["!disabled"] if t else ["disabled"])
            if not t:
                about.config(text="Import the file for your tool by hand: BUILD.md says how for each one.")
                return
            for n, s in enumerate(t.settings):
                entries[s.env] = tk.StringVar(value="" if s.secret else saved_key(s.env) or s.default)
                ttk.Label(fields, text=s.label).grid(row=n, column=0, sticky="w", pady=(6, 0))
                ttk.Entry(fields, textvariable=entries[s.env], width=34, show="•" if s.secret else "").grid(
                    row=n, column=1, sticky="w", padx=8, pady=(6, 0))
                hint = "saved; leave empty to keep it" if s.secret and s.keep and saved_key(s.env) else s.hint
                ttk.Label(fields, text=hint, style="Muted.TLabel").grid(row=n, column=2, sticky="w", pady=(6, 0))
            about.config(text=t.help + ("" if t.tested else " Beta: built from the vendor's API documentation and "
                                                             "not yet tried on a live account."))
        choice.trace_add("write", show)
        show()

        def settings_for(t) -> dict | None:
            v = {s.env: entries[s.env].get().strip() or (saved_key(s.env) if s.keep else "") or s.default
                 for s in t.settings}
            missing = [s.label for s in t.settings if not s.optional and not v[s.env]]
            if missing:
                messagebox.showerror("FlowSME", f"Enter the {', '.join(missing).lower()} for {t.name}, or choose "
                                                "'No: just write the files'.", parent=win)
                return None
            return v

        def test() -> None:
            t = target()
            v = settings_for(t) if t else None
            if not v:
                return
            win.config(cursor="watch")
            win.update()
            try:
                messagebox.showinfo("FlowSME", t.check(v), parent=win)
            except SystemExit as exc:
                messagebox.showerror("FlowSME", str(exc), parent=win)
            finally:
                win.config(cursor="")
        test_btn.configure(command=test)

        def go() -> None:
            t = target()
            v = settings_for(t) if t else None
            if t and v is None:
                return
            for s in (t.settings if t else []):
                typed = entries[s.env].get().strip()
                if typed and s.keep:
                    store_key(s.env, typed)
            task_id = int(tree.selection()[0])
            win.destroy()
            self._save_state(deploy_target=t.id if t else None)
            self._busy(True)
            print(f"Building task {task_id}" + (f" and creating it in {t.name}" if t else "") + "...")
            threading.Thread(target=self._work, args=("built", path, self._build_job, task_id, t and t.id, v),
                             daemon=True).start()

        self._buttons(win, f, 4, "Build", go)

    @staticmethod
    def _build_job(path: Path, task_id: int, target: str | None, settings: dict | None) -> None:
        from .build import build_task

        build_task(path, task_id, deploy=target, log=print, settings=settings)

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
            by = self._by(p)
            person = by if by and by != "me" and "(" not in by else f"Person {n}"
            tree.insert("", "end", iid=str(p), values=(person, self._items(p), _title(p),
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

    # ---- results ----------------------------------------------------------------------------------

    def _show_results(self, path: Path | None) -> None:
        """The selected recording's headline numbers and tasks, and what was said, without opening the report."""
        self.results.delete(*self.results.get_children())
        report = _read_json(path / "analysis" / "analysis.json") if path else None
        meta = load_meta(path) if path else {}
        if not path:
            self.summary.config(text="Record something, or import a recording, to see results here.")
        elif not report:
            self.summary.config(text="No report yet. Click Make report... to analyse this recording.")
        else:
            tasks = sorted((t for t in report["tasks"] if not t.get("misc")), key=lambda t: -t["score"])
            phases = {i["id"]: i["phase"] for i in ((report["meta"].get("priorities") or {}).get("items") or [])}
            options = _read_json(path / "analysis" / "options.json") or {}
            money_shown = self.mode != "employee" and options.get("show_money", True)
            cur = options.get("currency") or (self.org.currency if self.org else "$")
            money = lambda value: f" ({cur}{value:,})" if money_shown else ""
            saved = sum((t.get("estimate") or {}).get("minutes_saved_per_run", 0) for t in tasks)
            hours = sum((t.get("estimate") or {}).get("hours_saved_per_month", 0) for t in tasks)
            total = sum((t.get("estimate") or {}).get("cost_saved_per_month", 0) for t in tasks)
            cost = (report["meta"].get("ai_usage") or {}).get("cost_usd")
            secs = meta.get("duration_seconds") or 0
            self.summary.config(text=(
                f"{fmt_minutes(secs / 60)} recorded, {len(tasks)} task{'s' if len(tasks) != 1 else ''}. About "
                f"{fmt_minutes(saved)} could be saved each time this work is done: {hours:.1f} hours{money(total)} a month."
                + (f" AI cost of the report: about ${cost:.2f}." if cost else " Made with built-in rules.")
                + " Double-click a task to open the report."))
            for t in tasks:
                est = t.get("estimate") or {}
                self.results.insert("", "end", values=(
                    t["id"], t["name"], f"{t['level']} ({t['score']:.0f})",
                    fmt_minutes(est.get("minutes_saved_per_run", 0)),
                    f"{est.get('hours_saved_per_month', 0)} h{money(est.get('cost_saved_per_month', 0))}",
                    f"with task {t['same_as']}" if t.get("same_as") else phases.get(t["id"], "-")))
        turns = (_read_json(path / "analysis" / "narration.json") or {}).get("turns") if path else None
        if turns:
            text = "\n".join(f"{int(t['start'] // 60)}:{int(t['start'] % 60):02d}   {t['text']}" for t in turns)
        elif path and (path / "narration.wav").exists():
            text = "Narration was recorded. It is transcribed when you make an AI report."
        else:
            text = "No narration in this recording. Tick Narrate before recording to explain what you do and why."
        self.narration_box.config(state="normal")
        self.narration_box.delete("1.0", "end")
        self.narration_box.insert("1.0", text)
        self.narration_box.config(state="disabled")

    # ---- managing recordings ----------------------------------------------------------------------

    def import_video(self) -> None:
        """A screen video (Teams, Loom, Snipping Tool, OBS...) instead of a FlowSME recording: the AI model reads
        the steps from its frames, then the report options open as usual."""
        if self.busy or self.recorder is not None:
            return
        cfg, _ = app_config(self.config_path)
        if cfg.llm.provider == "offline" or (needs_key(cfg) and not saved_key(cfg.llm.api_key_env)):
            messagebox.showinfo("FlowSME", "Reading a video needs the AI model (it reads each frame). Add the "
                                           "Meta API key in Settings first.", parent=self.root)
            return
        file = filedialog.askopenfilename(parent=self.root, title="Choose a screen video of the work",
                                          filetypes=[("Videos", "*.mp4 *.mov *.webm *.mkv *.avi *.wmv *.m4v"),
                                                     ("All files", "*.*")])
        if not file:
            return
        name = simpledialog.askstring("Import a screen video", "Process name:", parent=self.root,
                                      initialvalue=Path(file).stem.replace("-", " ").replace("_", " "))
        if name is None:
            return
        self._busy(True)
        print(f"Reading '{Path(file).name}' with {cfg.llm.vision_model}: it looks at every change on screen. "
              "This takes a few minutes.")
        threading.Thread(target=self._work, args=("video", Path(file), self._video_job, cfg, name.strip()),
                         daemon=True).start()

    def _video_job(self, video: Path, cfg: Config, name: str) -> None:
        from .video import video_to_session

        self._imported = video_to_session(video, self.cfg.sessions_dir, name, cfg, log=print)

    def import_zip(self) -> None:
        file = filedialog.askopenfilename(parent=self.root, title="Import a FlowSME recording",
                                          filetypes=[("FlowSME recording", "*.zip"), ("All files", "*.*")])
        if not file:
            return
        try:
            if self.mode == "manager":
                from .admin_ui import import_checked

                path = import_checked(self.org, Path(file), self.cfg.sessions_dir)
            else:
                path = import_recording(Path(file), self.cfg.sessions_dir)
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            messagebox.showerror("FlowSME", f"Couldn't import it: {exc}", parent=self.root)
            return
        print(f"Imported '{_title(path)}'. Click Make report... to analyse it.")
        self.refresh(select=path)

    def rename(self) -> None:
        path = self._selected()
        if not path or self.busy or self.recorder is not None:
            return
        new = simpledialog.askstring("Rename", "Process name:", initialvalue=_title(path), parent=self.root)
        slug = re.sub(r"[^a-z0-9]+", "-", (new or "").lower()).strip("-")
        if not slug:
            return
        stamp = _STAMP.match(path.name)
        target = path.parent / (f"{stamp.group(0).rstrip('-')}-{slug}" if stamp else slug)
        if target == path:
            return
        if target.exists():
            messagebox.showerror("FlowSME", "A recording with that name already exists.", parent=self.root)
            return
        try:
            path.rename(target)
        except OSError as exc:
            messagebox.showerror("FlowSME", f"Couldn't rename it (close its report and folder first): {exc}",
                                 parent=self.root)
            return
        meta = load_meta(target)
        if meta:
            meta["session"] = target.name
            (target / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.refresh(select=target)

    def delete(self) -> None:
        path = self._selected()
        if not path or self.busy or self.recorder is not None:
            return
        if not messagebox.askyesno("FlowSME", f"Move '{_title(path)}' (recording, screenshots and report) to the "
                                              "Recycle Bin?", parent=self.root):
            return
        if _recycle(path):
            print(f"Moved '{_title(path)}' to the Recycle Bin.")
        else:
            messagebox.showerror("FlowSME", "Couldn't move it to the Recycle Bin: close its report and folder, then "
                                            "try again.", parent=self.root)
        self.refresh()

    def demo(self) -> None:
        if self.busy or self.recorder is not None:
            return
        from .demo import build_demo

        path = build_demo(self.cfg.sessions_dir)
        print("Demo recording created: a synthetic invoice process (8 invoices from email into a tracker and a "
              "web portal). Click Make report... to see a full report.")
        self.refresh(select=path)

    def open_folder(self) -> None:
        path = self._selected()
        if path:
            os.startfile(path)

    def export(self) -> None:
        path = self._selected()
        if not path:
            return
        if self.mode == "employee":
            self._send_to_manager(path)
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

    def _send_to_manager(self, path: Path) -> None:
        seat = self.seat
        if org.recording_seat(path) is None:
            org.stamp(path, seat)  # recorded before joining: it is still this person's work
        parts = ["the screenshots taken while you recorded"]
        if (path / "narration.wav").exists():
            parts.append("your narration")
        if (path / "analysis" / "report.html").exists():
            parts.append("your report")
        inbox = Path(seat["folder"]) / "inbox" if seat.get("folder") else None
        where = "the team folder" if inbox and inbox.parent.exists() else "a zip file on your Desktop to email"
        if not messagebox.askokcancel("Send to my manager", f"This sends '{_title(path)}' to {where}. It contains "
                                      f"{', '.join(parts)}.\n\nSend it?", parent=self.root):
            return
        slug = re.sub(r"[^a-z0-9]+", "-", seat["name"].lower()).strip("-") or "me"
        if inbox and inbox.parent.exists():
            inbox.mkdir(exist_ok=True)
            dest = inbox / f"{slug}-{path.name}.zip"
            shutil.make_archive(str(dest.with_suffix("")), "zip", root_dir=path.parent, base_dir=path.name)
            print(f"Sent to your manager: {dest}")
            messagebox.showinfo("FlowSME", "Sent. Your manager will find it in the Admin panel's Inbox.",
                                parent=self.root)
        else:
            dest = _desktop() / f"FlowSME-{slug}-{path.name}.zip"
            shutil.make_archive(str(dest.with_suffix("")), "zip", root_dir=path.parent, base_dir=path.name)
            print(f"Saved {dest}: email it to your manager.")
            subprocess.Popen(["explorer", f"/select,{dest}"])

    # ---- settings and first run -------------------------------------------------------------------

    def settings(self) -> None:
        from .settings_ui import open_settings

        open_settings(self)

    def _welcome(self) -> None:
        if messagebox.askokcancel("Welcome to FlowSME", WELCOME, parent=self.root):
            self._save_state(accepted=True)
            if self.mode == "solo":
                self._choose_use()
        else:
            self.root.destroy()

    def _choose_use(self) -> None:
        """Just me, managing a team, or joining one with a seat file."""
        win, f = self._dialog("How will you use FlowSME?")
        ttk.Label(f, text="How will you use FlowSME?", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        use = tk.StringVar(value="solo")
        for n, (value, text, note) in enumerate((
                ("solo", "Just me", "Record and analyse my own work. I set the positions and rates myself."),
                ("manager", "I manage a team", "Give people seats, set positions and what an hour of each costs, "
                                               "and make the reports with money in them."),
                ("employee", "My manager sent me a seat file", "Join my team: I record, my manager sees the "
                                                               "results.")), 1):
            ttk.Radiobutton(f, text=text, value=value, variable=use).grid(row=2 * n - 1, column=0, sticky="w",
                                                                          pady=(8, 0))
            ttk.Label(f, text=note, style="Muted.TLabel").grid(row=2 * n, column=0, sticky="w", padx=(22, 0))

        def go() -> None:
            win.destroy()
            if use.get() == "manager":
                self.admin("team")
            elif use.get() == "employee":
                self.join_team()
        self._buttons(win, f, 7, "Continue", go)

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
                elif event[0] in ("reported", "built", "compared", "team", "video"):
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
    try:  # a Windows 11 look (the Sun Valley theme); the plain Windows theme if it isn't installed
        import sv_ttk

        sv_ttk.set_theme("light")
        # The theme sizes its fonts for 96 DPI; on scaled screens they come out tiny. Use real point sizes.
        for name, size, weight in (("Caption", 9, "normal"), ("Body", 10, "normal"), ("BodyStrong", 10, "bold"),
                                   ("BodyLarge", 11, "normal"), ("Subtitle", 13, "bold"), ("Title", 16, "bold"),
                                   ("TitleLarge", 20, "bold"), ("Display", 26, "bold")):
            tkfont.nametofont(f"SunValley{name}Font").configure(family="Segoe UI", size=size, weight=weight)
    except Exception:  # noqa: BLE001 - any problem with the theme: fall back, the app still works
        if "vista" in style.theme_names():
            style.theme_use("vista")
    scale = root.winfo_fpixels("1i") / 96
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):  # not option_add: it would
        tkfont.nametofont(name).configure(family="Segoe UI", size=10)          # override the styles below
    style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
    style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Muted.TLabel", foreground="#5f6368")
    style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 8))
    style.configure("Treeview", rowheight=int(28 * scale))
    style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
    root.geometry(f"{int(920 * scale)}x{int(800 * scale)}")
    root.minsize(int(720 * scale), int(560 * scale))


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
            if "marked v" not in report.read_text(encoding="utf-8"):
                raise RuntimeError("report pages are missing their built-in Markdown renderer (assets/marked.min.js)")
            return f"2-second recording saved: {(tmp / 'recording' / 'meta.json').exists()}; " \
                   f"demo report written: {report.exists()}, works offline"
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
        if "--quiet" not in sys.argv:  # --quiet: no message box, for scripts; the exit code says it all
            ctypes.windll.user32.MessageBoxW(None, f"{text}\n\nSaved to {out}", "FlowSME", 0x40 if ok else 0x10)
        raise SystemExit(0 if ok else 1)
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
