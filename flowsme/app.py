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
        cols = (("name", "Process", 230), ("when", "Recorded", 140), ("length", "Length", 70), ("report", "Report", 70))
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
        self.report_btn = ttk.Button(row, text="Make report", command=self.make_report)
        self.open_btn = ttk.Button(row, text="Open report", command=self.open_report)
        self.folder_btn = ttk.Button(row, text="Open folder", command=self.open_folder)
        self.send_btn = ttk.Button(row, text="Save for the FlowSME team", command=self.export)
        for button in (self.report_btn, self.open_btn, self.folder_btn, self.send_btn):
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
                _STAMP.sub("", path.name).replace("-", " ") or path.name,
                (meta.get("started") or "")[:16].replace("T", " "),
                fmt_minutes(secs / 60) if secs else "?",
                "ready" if (path / "analysis" / "report.html").exists() else ""))
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
        for button, ok in ((self.report_btn, path), (self.open_btn, has_report), (self.folder_btn, path),
                           (self.send_btn, path), (self.record_btn, True)):
            button.state(["!disabled"] if ok and idle else ["disabled"])
        if has_report and idle:
            self.open_btn.state(["!disabled"])  # reading a report is fine while another is being made

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
            print(f"Saved '{_STAMP.sub('', path.name) or path.name}'. Select it and click Make report.")

    # ---- reports ----------------------------------------------------------------------------------

    def make_report(self) -> None:
        path = self._selected()
        if not path or self.busy or self.recorder is not None:
            return
        cfg, _ = app_config(self.config_path)
        if needs_key(cfg) and not saved_key(cfg.llm.api_key_env):
            if not messagebox.askyesno("FlowSME", "No Meta API key is set, so this report will use FlowSME's "
                                       "built-in rules: no AI step descriptions, narration or recommendations.\n\n"
                                       "Add a key in Settings for the full analysis.\n\nMake the basic report "
                                       "now?", parent=self.root):
                return
            cfg.llm.provider = "offline"
        self.busy = True
        self.progress.pack(side="left", padx=(6, 0))
        self.progress.start(12)
        self._update_buttons()
        print(f"Making the report for '{_STAMP.sub('', path.name) or path.name}'. This takes a few minutes.")
        threading.Thread(target=self._analyze, args=(path, cfg), name="flowsme-report", daemon=True).start()

    def _analyze(self, path: Path, cfg: Config) -> None:
        ok = False
        try:
            from .analyze import analyze_session

            analyze_session(path, cfg, log=print)
            ok = True
        except BaseException as exc:  # noqa: BLE001 - SystemExit included: show it, keep the app alive
            print(f"The report could not be made: {exc}")
        self.events.put(("reported", path, ok))

    def _reported(self, path: Path, ok: bool) -> None:
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.refresh(select=path)
        if ok:
            print("Report ready.")
            self.open_report()

    def open_report(self) -> None:
        path = self._selected()
        if path and (path / "analysis" / "report.html").exists():
            os.startfile(path / "analysis" / "report.html")

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
                elif event[0] == "reported":
                    self._reported(event[1], event[2])
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
