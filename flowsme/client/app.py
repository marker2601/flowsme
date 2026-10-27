"""FlowSME for customers: Record, Pause and Stop, send the recording to the FlowSME server, then read the report
and SOP, build the automation and, for managers, run the team's seats and rates. Nothing in this app analyses
anything: the results come from the server.

Recordings and settings live in %LOCALAPPDATA%\\FlowSME Client. The seat key is kept encrypted for this
Windows user."""
from __future__ import annotations

import contextlib
import ctypes
import io
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from .. import __version__
from ..config import Config, load_config, save_config
from ..session import list_sessions, load_meta, new_session_dir
from . import account
from .cloud import Cloud, CloudError, check_address

CLIENT_CONFIG = """# FlowSME recorder settings. Recordings are kept next to this file, in "sessions".
sessions_dir = "sessions"

[recorder]
# false = keystrokes are counted and shortcuts logged, but the text you type is never stored
capture_typed_text = false
excluded_title_keywords = ["password", "sign in", "log in", "login", "inprivate", "incognito",
                           "windows security", ".env"]
"""

WELCOME = """FlowSME records how you work, so it can map the process and find what to automate.

While recording it saves:
•  where you click, with the button or field name, and screenshots
•  how many characters you type and the shortcuts you use, not the text
•  your voice, only when Narrate is ticked

It skips password managers, sign-in pages, private browsing and Windows security prompts. Pause (Ctrl+Alt+P) before anything private.

Recordings stay on this computer until you send one for a report. Then it goes, encrypted, to the FlowSME server, which analyses it and keeps it for your team. Nothing goes anywhere else.

If you record other people's work, ask them first."""

# Tools the business may already have: labels only; what each can do is known on the server.
TOOL_NAMES = ["Microsoft 365", "Google Workspace", "Power Automate", "Power Automate Desktop", "n8n", "Zapier",
              "Make", "Python scripts", "Playwright", "UiPath", "Azure Document Intelligence", "PostgreSQL",
              "SQL Server / Azure SQL", "Power BI", "Looker Studio", "Excel", "Postman",
              "Power Apps / Microsoft Forms"]
STATUS = {"queued": "waiting", "analysing": "analysing...", "ready": "ready", "failed": "failed"}
HOTKEY = re.compile(r"^(ctrl|alt|shift|win)(\+(ctrl|alt|shift|win))*\+[a-z0-9]$", re.I)
_STAMP = re.compile(r"^\d{8}-\d{6}-?")


def home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FlowSME Client"


def fmt_minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{round(minutes * 60)} s"
    if minutes < 60:
        return f"{round(minutes)} min"
    return f"{minutes / 60:.1f} h"


def _title(path: Path) -> str:
    return _STAMP.sub("", path.name).replace("-", " ") or path.name


def _desktop() -> Path:
    buf = ctypes.create_unicode_buffer(260)
    with contextlib.suppress(Exception):
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
            return Path(buf.value)
    return Path.home() / "Desktop"


def _recycle(path: Path) -> bool:
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]

    op = SHFILEOPSTRUCTW(None, 3, str(path.resolve()) + "\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, 0, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not path.exists()


def _icon() -> Path | None:
    for folder in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).parent.parent / "assets"):
        if (folder / "flowsme.ico").exists():
            return folder / "flowsme.ico"
    return None


class _Pipe:
    def __init__(self, events: queue.Queue):
        self.events, self.buf, self.lock = events, "", threading.Lock()

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


class ClientApp:
    def __init__(self, root: tk.Tk, folder: Path | None = None):
        self.root, self.home = root, Path(folder or home())
        self.home.mkdir(parents=True, exist_ok=True)
        self.cfg_path = self.home / "config.toml"
        if not self.cfg_path.exists():
            self.cfg_path.write_text(CLIENT_CONFIG, encoding="utf-8")
        self.cfg = self._config()
        self.state_path = self.home / "app.json"
        self.state = self._load_state()
        self.events: queue.Queue = queue.Queue()
        sys.stdout = sys.stderr = _Pipe(self.events)
        root.report_callback_exception = lambda kind, exc, tb: print(f"Error: {exc}")
        self.cloud: Cloud | None = None
        self.me: dict = {}
        self.server_recs: dict[str, dict] = {}
        self.summaries: dict[str, dict] = {}
        self.recorder = None
        self.bar = None
        self.busy = False
        self.revoked = False
        self._pick: str | None = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(150, self._pump)
        root.after(300, self._start)

    def _config(self) -> Config:
        cfg = load_config(self.cfg_path)
        cfg.llm.provider = "offline"  # this app never analyses anything
        if not cfg.sessions_dir.is_absolute():
            cfg.sessions_dir = self.home / cfg.sessions_dir
        return cfg

    # ---- layout -----------------------------------------------------------------------------------

    def _build(self) -> None:
        r = self.root
        r.title("FlowSME")
        scale = r.winfo_fpixels("1i") / 96
        top = ttk.Frame(r, padding=(16, 14, 16, 2))
        top.pack(fill="x")
        ttk.Label(top, text="FlowSME", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="Record your work. Get the workflow, what to automate and the minutes saved.",
                  style="Muted.TLabel").pack(side="left", padx=12, pady=(4, 0))
        team = ttk.Frame(r, padding=(16, 2, 16, 0))
        team.pack(fill="x")
        self.who = ttk.Label(team, style="Muted.TLabel")
        self.who.pack(side="left")
        self.account_btn = ttk.Button(team, text="Sign out", command=self.sign_out)
        self.account_btn.pack(side="right")
        self.admin_btn = ttk.Button(team, text="Admin panel", command=self.admin)

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
        cols = (("name", "Process", 230), ("by", "By", 110), ("when", "Recorded", 140), ("length", "Length", 70),
                ("status", "Report", 110))
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in cols], show="headings", height=6, selectmode="browse")
        for col, label, width in cols:
            self.tree.heading(col, text=label, anchor="w")
            self.tree.column(col, width=int(width * scale), minwidth=int(width * scale * 0.8), anchor="w",
                             stretch=col == "name")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._selected_changed())
        self.tree.bind("<Double-1>", lambda _: self.open_report())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        self.send_btn = ttk.Button(row, text="Get report...", command=self.get_report)
        self.open_btn = ttk.Button(row, text="Open report", command=self.open_report)
        self.sop_btn = ttk.Button(row, text="Open SOP", command=self.open_sop)
        self.build_btn = ttk.Button(row, text="Build automation...", command=self.build)
        self.compare_btn = ttk.Button(row, text="Compare...", command=self.compare)
        self.more_btn = ttk.Menubutton(row, text="More")
        self.more = tk.Menu(self.more_btn, tearoff=False)
        self.more_btn["menu"] = self.more
        for button in (self.send_btn, self.open_btn, self.sop_btn, self.build_btn, self.compare_btn, self.more_btn):
            button.pack(side="left", padx=(0, 6))
        self.progress = ttk.Progressbar(row, mode="indeterminate", length=90)

        self.tabs = ttk.Notebook(r)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        results = ttk.Frame(self.tabs, padding=(10, 8))
        self.tabs.add(results, text="Results")
        self.summary = ttk.Label(results, wraplength=int(660 * scale), justify="left")
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
                                     font=("Segoe UI", 9), background="#f7f7f8")
        self.narration_box.pack(fill="both", expand=True)
        logs = ttk.Frame(self.tabs, padding=(10, 8))
        self.tabs.add(logs, text="Activity")
        self.log_box = tk.Text(logs, height=6, wrap="word", state="disabled", relief="flat", font=("Consolas", 9),
                               background="#f7f7f8")
        self.log_box.pack(fill="both", expand=True)

        foot = ttk.Frame(r, padding=(16, 0, 16, 12))
        foot.pack(fill="x")
        self.server_label = ttk.Label(foot, style="Muted.TLabel")
        self.server_label.pack(side="left")
        ttk.Button(foot, text="Settings", command=self.settings).pack(side="right")
        ttk.Label(foot, text=f"v{__version__}", style="Muted.TLabel").pack(side="right", padx=8)

    # ---- signing in -------------------------------------------------------------------------------

    def _start(self) -> None:
        if not self.state.get("accepted"):
            if not messagebox.askokcancel("Welcome to FlowSME", WELCOME, parent=self.root):
                self.root.destroy()
                return
            self._save_state(accepted=True)
        signed = account.load(self.home)
        if signed:
            self.cloud = Cloud(*signed)
            self._bg(self.cloud.me, self._signed_in)
        else:
            self.sign_in()
        self.refresh_local()

    def sign_in(self) -> None:
        win, f = self._dialog("Sign in to FlowSME")
        ttk.Label(f, text="Sign in with your seat", style="Heading.TLabel").grid(row=0, column=0, columnspan=2,
                                                                                 sticky="w")
        ttk.Label(f, style="Muted.TLabel", text="Open the seat file your manager (or FlowSME) sent you, or paste "
                  "the server address and seat key.").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        server, key = tk.StringVar(value=self.state.get("server", "")), tk.StringVar()

        def open_file() -> None:
            file = filedialog.askopenfilename(parent=win, title="Open your FlowSME seat file",
                                              filetypes=[("FlowSME seat", "*.flowseat"), ("All files", "*.*")])
            if not file:
                return
            try:
                seat = account.read_seat_file(Path(file))
            except ValueError as exc:
                messagebox.showerror("FlowSME", str(exc), parent=win)
                return
            server.set(seat["server"])
            key.set(seat["key"])
            go()
        ttk.Button(f, text="Open seat file...", command=open_file).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Server address").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(f, textvariable=server, width=40).grid(row=3, column=1, sticky="w", padx=8, pady=(10, 0))
        ttk.Label(f, text="Seat key").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(f, textvariable=key, width=40, show="•").grid(row=4, column=1, sticky="w", padx=8, pady=(6, 0))

        def go() -> None:
            try:
                cloud = Cloud(check_address(server.get()), key.get().strip())
                me = cloud.me()
            except CloudError as exc:
                messagebox.showerror("FlowSME", str(exc), parent=win)
                return
            account.save(self.home, cloud.server, cloud.key, me)
            self._save_state(server=cloud.server)
            self.cloud = cloud
            win.destroy()
            self._signed_in(me, None)
        self._buttons(win, f, 5, "Sign in", go)

    def _signed_in(self, me: dict | None, error: str | None) -> None:
        if error:
            self.who.config(text=f"Not connected: {error}", foreground="#d93025")
            self.server_label.config(text="Reports need the FlowSME server.")
            if "revoked" in error or "Sign in" in error:
                self.cloud = None
                self.revoked = "revoked" in error
            self._update_buttons()
            return
        self.me = me
        manager = me["role"] == "manager"
        who = f"{me['name']} · {me['position']} · {me['team']}"
        if manager:
            who = f"Manager · {me['team']} · {me['used']} of {me['seats']} seats in use"
        self.who.config(text=who, foreground="")
        if manager:
            self.admin_btn.pack(side="right", padx=(0, 6))
        else:
            self.admin_btn.pack_forget()
        self.server_label.config(text=f"Reports are made on the FlowSME server ({self.cloud.server})")
        self.refresh()

    def sign_out(self) -> None:
        if self.cloud and not messagebox.askyesno("FlowSME", "Sign out? Your recordings stay on this computer.",
                                                  parent=self.root):
            return
        account.forget(self.home)
        self.cloud, self.me, self.server_recs, self.summaries = None, {}, {}, {}
        self.who.config(text="Not signed in", foreground="")
        self.admin_btn.pack_forget()
        self.refresh_local()
        self.sign_in()

    # ---- recordings list --------------------------------------------------------------------------

    def refresh(self) -> None:
        """The recordings on this computer, joined with what the server has (and, for managers, the team's)."""
        if self.cloud:
            self._bg(self.cloud.recordings, self._listed)
        self.refresh_local()

    def _listed(self, recs: list | None, error: str | None) -> None:
        if error:
            print(f"Couldn't list the server's recordings: {error}")
            return
        self.server_recs = {r["id"]: r for r in recs}
        self.refresh_local()
        if any(r["status"] in ("queued", "analysing") for r in recs):
            self.root.after(4000, self.refresh)

    @staticmethod
    def _link(path: Path) -> str | None:
        with contextlib.suppress(OSError, ValueError, KeyError):
            return json.loads((path / "cloud.json").read_text(encoding="utf-8"))["id"]
        return None

    def refresh_local(self) -> None:
        keep = self._selected_iid()
        if self._pick:
            keep, self._pick = self._pick, None
        self.tree.delete(*self.tree.get_children())
        linked = set()
        rows = []
        for path in list_sessions(self.cfg.sessions_dir):
            meta, rec_id = load_meta(path), self._link(path)
            rec = self.server_recs.get(rec_id) if rec_id else None
            linked.add(rec_id)
            secs = meta.get("duration_seconds")
            status = STATUS.get(rec["status"], rec["status"]) if rec else ("sent" if rec_id else "not sent")
            rows.append((meta.get("started") or "", str(path), (_title(path), "me", (meta.get("started") or "")[:16]
                         .replace("T", " "), fmt_minutes(secs / 60) if secs else "?", status)))
        for rec in self.server_recs.values():
            if rec["id"] not in linked:
                rows.append((rec.get("started") or "", f"srv:{rec['id']}", (
                    rec["title"], rec["by"], (rec.get("started") or "")[:16].replace("T", " "),
                    fmt_minutes(rec["seconds"] / 60) if rec.get("seconds") else "?",
                    STATUS.get(rec["status"], rec["status"]))))
        for _, iid, values in sorted(rows, reverse=True):
            self.tree.insert("", "end", iid=iid, values=values)
        manager = self.me.get("role") == "manager"
        self.tree.configure(displaycolumns=[c for c in ("name", "by", "when", "length", "status")
                                            if c != "by" or manager])
        items = self.tree.get_children()
        pick = keep if keep and self.tree.exists(keep) else (items[0] if items else None)
        if pick:
            self.tree.selection_set(pick)
        keys = self.cfg.recorder
        self.hint.config(text=f"While recording: {keys.pause_hotkey.title()} pauses, {keys.stop_hotkey.title()} "
                              "stops. Pause before anything private.")
        self._menu()
        self._selected_changed()

    def _menu(self) -> None:
        m = self.more
        m.delete(0, "end")
        if self.me.get("role") == "manager":
            m.add_command(label="Team view...", command=self.team)
            m.add_separator()
        m.add_command(label="Import a video...", command=self.import_video)
        m.add_separator()
        m.add_command(label="Delete...", command=self.delete)
        m.add_command(label="Open folder", command=self.open_folder)
        m.add_command(label="Refresh", command=self.refresh)

    def _selected_iid(self) -> str | None:
        chosen = self.tree.selection()
        return chosen[0] if chosen else None

    def _selection(self) -> tuple[Path | None, dict | None]:
        """The selected recording's folder on this computer (if any) and its server record (if any)."""
        iid = self._selected_iid()
        if not iid:
            return None, None
        if iid.startswith("srv:"):
            return None, self.server_recs.get(iid[4:])
        path = Path(iid)
        rec_id = self._link(path)
        return path, self.server_recs.get(rec_id) if rec_id else None

    def _selected_changed(self) -> None:
        _, rec = self._selection()
        if rec and rec["status"] == "ready" and rec["id"] not in self.summaries and self.cloud:
            rec_id = rec["id"]
            self._bg(lambda: self.cloud.recording(rec_id), lambda s, e: self._summary(rec_id, s, e))
        self._show_results()
        self._update_buttons()

    def _summary(self, rec_id: str, data: dict | None, error: str | None) -> None:
        if data:
            self.summaries[rec_id] = data
            self._show_results()
            self._update_buttons()

    def _update_buttons(self) -> None:
        path, rec = self._selection()
        idle = not self.busy and self.recorder is None
        online = self.cloud is not None
        ready = bool(rec and rec["status"] == "ready")
        info = self.summaries.get(rec["id"]) if rec else None
        manager = self.me.get("role") == "manager"
        for button, ok in ((self.send_btn, online and (path or rec) and not (rec and rec["status"] in
                                                                              ("queued", "analysing"))),
                           (self.open_btn, online and ready), (self.build_btn, online and ready and info and
                                                               any(t["can_build"] for t in info["tasks"])),
                           (self.sop_btn, online and ready and info and any(t["has_sop"] for t in info["tasks"])),
                           (self.compare_btn, online and ready and manager), (self.more_btn, True),
                           (self.record_btn, True)):
            button.state(["!disabled"] if ok and idle else ["disabled"])
        self.compare_btn.pack_forget() if not manager else None
        if manager and not self.compare_btn.winfo_ismapped():
            self.compare_btn.pack(side="left", padx=(0, 6), before=self.more_btn)

    def _show_results(self) -> None:
        self.results.delete(*self.results.get_children())
        path, rec = self._selection()
        info = self.summaries.get(rec["id"]) if rec else None
        if not path and not rec:
            text = "Record something to see results here."
        elif not rec and path and self._link(path):
            text = "Sent to the FlowSME server. Its report shows here while you're signed in."
        elif not rec:
            text = "Not sent yet. Click Get report... to send it to the FlowSME server for a report."
        elif rec["status"] in ("queued", "analysing"):
            text = "The FlowSME server is making the report. This takes a few minutes; the list updates itself."
        elif rec["status"] == "failed":
            text = f"The report failed: {rec.get('error') or 'unknown error'}. Click Get report... to try again."
        elif not info:
            text = "Loading the results..."
        else:
            tasks, cur = info["tasks"], info.get("currency")
            money = (lambda v: f" ({cur}{v:,})") if cur else (lambda v: "")
            saved = sum(t["minutes_saved_per_run"] for t in tasks)
            hours = sum(t["hours_saved_per_month"] for t in tasks)
            total = sum(t["money_saved_per_month"] or 0 for t in tasks)
            secs = rec.get("seconds") or 0
            text = (f"{fmt_minutes(secs / 60)} recorded, {len(tasks)} task{'s' if len(tasks) != 1 else ''}. About "
                    f"{fmt_minutes(saved)} could be saved per run: {hours:.1f} hours{money(total)} a month."
                    + (f" Done by: {rec['position']}." if rec.get("position") else "")
                    + " Double-click a task to open the report.")
            for t in tasks:
                self.results.insert("", "end", values=(
                    t["id"], t["name"], f"{t['level']} ({t['score']})", fmt_minutes(t["minutes_saved_per_run"]),
                    f"{t['hours_saved_per_month']} h{money(t['money_saved_per_month'] or 0)}", t["phase"]))
        self.summary.config(text=text)
        turns = (info or {}).get("narration") or []
        if turns:
            said = "\n".join(f"{int(t['start'] // 60)}:{int(t['start'] % 60):02d}   {t['text']}" for t in turns)
        elif path and (path / "narration.wav").exists():
            said = "Narration was recorded. It is transcribed on the server when the report is made."
        else:
            said = "No narration in this recording. Tick Narrate before recording to explain what you do and why."
        self.narration_box.config(state="normal")
        self.narration_box.delete("1.0", "end")
        self.narration_box.insert("1.0", said)
        self.narration_box.config(state="disabled")

    # ---- recording --------------------------------------------------------------------------------

    def start(self) -> None:
        if self.recorder is not None or self.busy:
            return
        if self.revoked:
            messagebox.showerror("FlowSME", "Your seat was revoked, so FlowSME can't record for your team. Ask your "
                                            "manager for a new seat file.", parent=self.root)
            return
        from ..recorder import Recorder

        self.cfg = self._config()
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
        self.refresh_local()
        if self.tree.exists(str(path)):
            self.tree.selection_set(str(path))
        if (path / "events.jsonl").exists():
            print(f"Saved '{_title(path)}'. Click Get report... to send it to the FlowSME server.")

    # ---- reports ----------------------------------------------------------------------------------

    def import_video(self) -> None:
        """A screen video (Teams, Loom, Snipping Tool...) instead of a FlowSME recording."""
        if not self.cloud or self.busy:
            return
        file = filedialog.askopenfilename(parent=self.root, title="Choose a screen video of the work",
                                          filetypes=[("Videos", "*.mp4 *.mov *.webm *.mkv *.avi *.wmv *.m4v"),
                                                     ("All files", "*.*")])
        if file:
            self.get_report(Path(file))

    def get_report(self, video: Path | None = None) -> None:
        """Send the recording or a video (or, if it is already on the server, ask for the report again)."""
        path, rec = (None, None) if video else self._selection()
        if not self.cloud or self.busy or (not path and not rec and not video):
            return
        saved = self.state.get("report_options") or {}
        manager = self.me.get("role") == "manager"
        win, f = self._dialog("Report options")
        title = video.stem.replace("-", " ").replace("_", " ") if video else _title(path) if path else rec["title"]
        ttk.Label(f, text=f"Report for '{title}'", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        biz = ttk.LabelFrame(f, text="Your business (for hours and money saved)" if manager else
                             "Your business (for hours saved)", padding=10)
        biz.grid(row=1, column=0, sticky="we", pady=(10, 0))
        runs = tk.StringVar(value=str(saved.get("runs_per_month", 20)))
        ttk.Label(biz, text="This process runs").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(biz, from_=1, to=100000, width=7, textvariable=runs).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(biz, text="times a month").grid(row=0, column=2, sticky="w")
        position = tk.StringVar(value=(rec or {}).get("position") or self.me.get("position", ""))
        ttk.Label(biz, text="Done by").grid(row=1, column=0, sticky="w", pady=(6, 0))
        if manager:
            ttk.Combobox(biz, textvariable=position, values=self.me.get("positions", []), state="readonly",
                         width=26).grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
        else:
            ttk.Label(biz, text=f"{self.me.get('position', '')} (your position)").grid(
                row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
        kit = ttk.LabelFrame(f, text="Tools the business already has (the plan shows what they can do with them)",
                             padding=10)
        kit.grid(row=2, column=0, sticky="we", pady=(10, 0))
        chosen = set(saved.get("tools", []))
        picks = {}
        for n, name in enumerate(TOOL_NAMES):
            picks[name] = tk.BooleanVar(value=name in chosen)
            ttk.Checkbutton(kit, text=name, variable=picks[name]).grid(row=n // 3, column=n % 3, sticky="w",
                                                                      padx=(0, 14), pady=1)
        other = tk.StringVar(value=saved.get("other_tools", ""))
        rows = (len(TOOL_NAMES) + 2) // 3
        ttk.Label(kit, text="Other tools, with what they can do:").grid(row=rows, column=0, columnspan=3,
                                                                        sticky="w", pady=(8, 0))
        ttk.Entry(kit, textvariable=other).grid(row=rows + 1, column=0, columnspan=3, sticky="we", pady=(2, 0))
        ttk.Label(kit, style="Muted.TLabel", text="e.g.  MyERP: database, reporting; Odoo: api  (separate with ;)"
                  ).grid(row=rows + 2, column=0, columnspan=3, sticky="w")
        note = ("The video goes to the FlowSME server, encrypted. Its AI model reads the steps from the picture, "
                "so the times in the report are approximate. Ready in a few minutes." if video else
                "The recording (with its screenshots and narration) goes to the FlowSME server, encrypted. The "
                "report is ready in a few minutes.")
        ttk.Label(f, style="Muted.TLabel", text=note, wraplength=int(620 * self.root.winfo_fpixels("1i") / 96),
                  justify="left").grid(row=3, column=0, sticky="w", pady=(8, 0))

        def go() -> None:
            try:
                options = {"runs_per_month": int(float(runs.get())), "position": position.get(),
                           "tools": [n for n, v in picks.items() if v.get()], "other_tools": other.get().strip()}
                if options["runs_per_month"] < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("FlowSME", "Use a positive number for the runs.", parent=win)
                return
            win.destroy()
            self._save_state(report_options={k: options[k] for k in ("runs_per_month", "tools", "other_tools")})
            cloud = self.cloud
            if video:
                self._busy(True)
                print(f"Sending the video '{video.name}' to the FlowSME server...")
                self._bg(lambda: cloud.upload_video(video, dict(options, title=title)), self._sent)
                return
            if rec:
                self._busy(True)
                print(f"Asking the server for a new report of '{title}'...")
                self._bg(lambda: cloud.reanalyse(rec["id"], options), self._sent)
                return
            self._busy(True)
            print(f"Sending '{title}' to the FlowSME server...")

            def send() -> dict:
                sent = cloud.upload(path, dict(options, title=title))
                (path / "cloud.json").write_text(json.dumps({"id": sent["id"], "server": cloud.server}),
                                                 encoding="utf-8")
                return sent
            self._bg(send, self._sent)
        self._buttons(win, f, 4, "Get report", go)

    def _sent(self, rec: dict | None, error: str | None) -> None:
        self._busy(False)
        if error:
            messagebox.showerror("FlowSME", error, parent=self.root)
            return
        print("Sent. The server is making the report; the list updates itself.")
        self.summaries.pop(rec["id"], None)
        self._pick = f"srv:{rec['id']}"  # a video has no folder here: select its row once it is listed
        self.refresh()

    def _fetch_page(self, what: str, fetch) -> None:
        _, rec = self._selection()
        if not rec or not self.cloud:
            return
        rec_id = rec["id"]
        dest = self.home / "cache" / rec_id / f"{what}.html"

        def get() -> Path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(fetch(rec_id), encoding="utf-8")
            return dest
        self._bg(get, lambda p, e: messagebox.showerror("FlowSME", e, parent=self.root) if e else os.startfile(p))

    def open_report(self) -> None:
        _, rec = self._selection()
        if rec and rec["status"] == "ready":
            self._fetch_page("report", self.cloud.report)

    def open_sop(self) -> None:
        self._fetch_page("sop", self.cloud.sop)

    # ---- build ------------------------------------------------------------------------------------

    def build(self) -> None:
        from ..connect import CONNECTORS

        _, rec = self._selection()
        info = self.summaries.get(rec["id"]) if rec else None
        if not info or self.busy:
            return
        tasks = [t for t in info["tasks"] if t["can_build"]]
        win, f = self._dialog("Build automation")
        scale = self.root.winfo_fpixels("1i") / 96
        ttk.Label(f, text="Which task should be automated?", style="Heading.TLabel").grid(row=0, column=0,
                                                                                         sticky="w")
        tree = ttk.Treeview(f, columns=("task", "potential", "saved"), show="headings", height=min(len(tasks), 5),
                            selectmode="browse")
        for col, label, width in (("task", "Task", 420), ("potential", "Potential", 110),
                                  ("saved", "Saves per run", 110)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=int(width * scale), anchor="w")
        for t in tasks:
            tree.insert("", "end", iid=str(t["id"]), values=(f"{t['id']}. {t['name']}", f"{t['level']} ({t['score']})",
                                                            fmt_minutes(t["minutes_saved_per_run"])))
        tree.selection_set(str(tasks[0]["id"]))
        tree.grid(row=1, column=0, sticky="we", pady=(8, 0))
        ttk.Label(f, style="Muted.TLabel", wraplength=int(560 * scale), justify="left", text=(
            "The FlowSME server writes the automation for n8n, Node-RED, Windmill, Make, Power Automate and Azure "
            "Logic Apps, with test calls, SQL and browser starters and BUILD.md listing what a person must fill in. "
            "Nothing runs until you review it.")).grid(row=2, column=0, sticky="w", pady=(8, 0))
        box = ttk.LabelFrame(f, text="Also create it in my workflow tool (switched off until I turn it on)",
                             padding=10)
        box.grid(row=3, column=0, sticky="we", pady=(10, 0))
        ids = [None, *CONNECTORS]
        names = ["No: just save the files"] + [c.name + ("" if c.tested else "  (beta)") for c in CONNECTORS.values()]
        last = self.state.get("deploy_target")
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
            return CONNECTORS.get(ids[names.index(choice.get())])

        def show(*_) -> None:
            for widget in fields.winfo_children():
                widget.destroy()
            entries.clear()
            c = target()
            test_btn.state(["!disabled"] if c else ["disabled"])
            if not c:
                about.config(text="Import the file for your tool by hand: BUILD.md says how for each one.")
                return
            for n, s in enumerate(c.settings):
                entries[s.env] = tk.StringVar(value="" if s.secret else account.saved_setting(s.env) or s.default)
                ttk.Label(fields, text=s.label).grid(row=n, column=0, sticky="w", pady=(6, 0))
                ttk.Entry(fields, textvariable=entries[s.env], width=34, show="•" if s.secret else "").grid(
                    row=n, column=1, sticky="w", padx=8, pady=(6, 0))
                hint = "saved; leave empty to keep it" if s.secret and s.keep and account.saved_setting(s.env) \
                    else s.hint
                ttk.Label(fields, text=hint, style="Muted.TLabel").grid(row=n, column=2, sticky="w", pady=(6, 0))
            about.config(text=c.help + ("" if c.tested else " Beta: built from the vendor's API documentation and "
                                                             "not yet tried on a live account."))
        choice.trace_add("write", show)
        show()

        def settings_for(c) -> dict | None:
            v = {s.env: entries[s.env].get().strip() or (account.saved_setting(s.env) if s.keep else "") or s.default
                 for s in c.settings}
            missing = [s.label for s in c.settings if not s.optional and not v[s.env]]
            if missing:
                messagebox.showerror("FlowSME", f"Enter the {', '.join(missing).lower()} for {c.name}, or choose "
                                                "'No: just save the files'.", parent=win)
                return None
            return v

        def test() -> None:
            c = target()
            v = settings_for(c) if c else None
            if not v:
                return
            win.config(cursor="watch")
            win.update()
            try:
                messagebox.showinfo("FlowSME", c.check(v), parent=win)
            except SystemExit as exc:
                messagebox.showerror("FlowSME", str(exc), parent=win)
            finally:
                win.config(cursor="")
        test_btn.configure(command=test)

        def go() -> None:
            c = target()
            v = settings_for(c) if c else None
            if c and v is None:
                return
            for s in (c.settings if c else []):
                typed = entries[s.env].get().strip()
                if typed and s.keep:
                    account.store_setting(s.env, typed)
            task_id = int(tree.selection()[0])
            win.destroy()
            self._save_state(deploy_target=c.id if c else None)
            self._busy(True)
            print(f"Building task {task_id}" + (f" and creating it in {c.name}" if c else "") + "...")
            self._bg(lambda: self._build_job(rec, task_id, c and c.id, v), self._built)
        self._buttons(win, f, 4, "Build", go)

    def _build_job(self, rec: dict, task_id: int, target: str | None, settings: dict | None) -> Path:
        from ..connect import create

        data = self.cloud.build(rec["id"], task_id)
        slug = re.sub(r"[^a-z0-9]+", "-", rec["title"].lower()).strip("-")[:40] or "automation"
        out = self.home / "automations" / f"{slug}-task{task_id}"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                if member.startswith("deploy/") or ".." in member or member.startswith(("/", "\\")):
                    continue
                (out / Path(member).name).write_bytes(archive.read(member))
            if target:
                doc = json.loads(archive.read(f"deploy/{target}.json"))
                print(f"  {create(target, doc, settings, archive.read('deploy/slug.txt').decode())}")
        return out

    def _built(self, out: Path | None, error: str | None) -> None:
        self._busy(False)
        if error:
            messagebox.showerror("FlowSME", error, parent=self.root)
            return
        print(f"Automation files saved to {out}: start with BUILD.md.")
        os.startfile(out)

    # ---- the manager's views ----------------------------------------------------------------------

    def _ready(self) -> list[dict]:
        return [r for r in self.server_recs.values() if r["status"] == "ready"]

    def compare(self) -> None:
        _, after = self._selection()
        if not after or after["status"] != "ready":
            return
        options = [r for r in self._ready() if r["id"] != after["id"]]
        if not options:
            messagebox.showinfo("FlowSME", "Another recording with a ready report is needed.", parent=self.root)
            return
        win, f = self._dialog("Compare before and after")
        ttk.Label(f, text="Which recording was made before the change?", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(f, style="Muted.TLabel", text=f"'{after['title']}' is the after.").grid(row=1, column=0, sticky="w")
        tree = ttk.Treeview(f, columns=("name", "by", "when"), show="headings", height=min(len(options), 6),
                            selectmode="browse")
        for col, text in (("name", "Process"), ("by", "By"), ("when", "Recorded")):
            tree.heading(col, text=text, anchor="w")
        for r in options:
            tree.insert("", "end", iid=r["id"], values=(r["title"], r["by"], (r.get("started") or "")[:16]))
        tree.selection_set(options[0]["id"])
        tree.grid(row=2, column=0, sticky="we", pady=(8, 0))
        counts = ttk.Frame(f)
        counts.grid(row=3, column=0, sticky="w", pady=(10, 0))
        before_items, after_items = tk.StringVar(value="3"), tk.StringVar(value="3")
        ttk.Label(counts, text="Items done in the before recording").pack(side="left")
        ttk.Spinbox(counts, from_=1, to=10000, width=6, textvariable=before_items).pack(side="left", padx=6)
        ttk.Label(counts, text="in the after recording").pack(side="left", padx=(8, 0))
        ttk.Spinbox(counts, from_=1, to=10000, width=6, textvariable=after_items).pack(side="left", padx=6)

        def go() -> None:
            try:
                items = (int(before_items.get()), int(after_items.get()))
            except ValueError:
                return
            before = tree.selection()[0]
            win.destroy()
            self._page_from(lambda: self.cloud.compare(before, after["id"], *items), f"compare-{before}")
        self._buttons(win, f, 4, "Compare", go)

    def team(self) -> None:
        options = self._ready()
        if len(options) < 2:
            messagebox.showinfo("FlowSME", "Two or more recordings with ready reports are needed.", parent=self.root)
            return
        win, f = self._dialog("Team view")
        ttk.Label(f, text="Which recordings show the same process done by different people?",
                  style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        tree = ttk.Treeview(f, columns=("by", "items", "name"), show="headings", height=min(len(options), 8),
                            selectmode="extended")
        for col, text in (("by", "Person"), ("items", "Items"), ("name", "Process")):
            tree.heading(col, text=text, anchor="w")
        for r in options:
            tree.insert("", "end", iid=r["id"], values=(r["by"], 3, r["title"]))
        tree.grid(row=1, column=0, sticky="we", pady=(8, 0))
        items = tk.StringVar()
        edit = ttk.Frame(f)
        edit.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(edit, text="Items the selected person did").pack(side="left")
        box = ttk.Spinbox(edit, from_=1, to=10000, width=6, textvariable=items)
        box.pack(side="left", padx=6)

        def set_items(*_) -> None:
            if tree.selection() and items.get().isdigit():
                tree.set(tree.selection()[-1], "items", items.get())
        box.configure(command=set_items)
        box.bind("<KeyRelease>", set_items)
        tree.bind("<<TreeviewSelect>>", lambda _: items.set(tree.set(tree.selection()[-1], "items"))
                  if tree.selection() else None)

        def go() -> None:
            chosen = list(tree.selection())
            if len(chosen) < 2:
                messagebox.showerror("FlowSME", "Select two or more recordings (Ctrl+click).", parent=win)
                return
            counts = [int(tree.set(i, "items")) for i in chosen]
            win.destroy()
            self._page_from(lambda: self.cloud.team(chosen, counts), "team-" + time.strftime("%Y%m%d-%H%M%S"))
        self._buttons(win, f, 3, "Show team view", go)

    def _page_from(self, fetch, name: str) -> None:
        dest = self.home / "cache" / f"{name}.html"
        self._busy(True)

        def get() -> Path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(fetch(), encoding="utf-8")
            return dest

        def done(page: Path | None, error: str | None) -> None:
            self._busy(False)
            if error:
                messagebox.showerror("FlowSME", error, parent=self.root)
            else:
                os.startfile(page)
        self._bg(get, done)

    def admin(self) -> None:
        if self.me.get("role") != "manager":
            return
        from .admin import open_admin

        open_admin(self)

    # ---- managing recordings ----------------------------------------------------------------------

    def delete(self) -> None:
        path, rec = self._selection()
        if (not path and not rec) or self.busy or self.recorder is not None:
            return
        title = _title(path) if path else rec["title"]
        where = " and from the FlowSME server" if rec else ""
        if not messagebox.askyesno("FlowSME", f"Delete '{title}' from this computer (to the Recycle Bin){where}?"
                                   if path else f"Delete '{title}' from the FlowSME server?", parent=self.root):
            return
        if path and not _recycle(path):
            messagebox.showerror("FlowSME", "Couldn't move it to the Recycle Bin: close its folder and try again.",
                                 parent=self.root)
            return
        if rec and self.cloud:
            self._bg(lambda: self.cloud.delete(rec["id"]), lambda _, e: (print(e) if e else None, self.refresh()))
        else:
            self.refresh_local()

    def open_folder(self) -> None:
        path, _ = self._selection()
        os.startfile(path or self.cfg.sessions_dir)

    # ---- settings ---------------------------------------------------------------------------------

    def settings(self) -> None:
        cfg = load_config(self.cfg_path)
        win, f = self._dialog("FlowSME settings")
        rec = cfg.recorder
        try:
            import sounddevice as sd

            mics = list(dict.fromkeys(d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0))
        except Exception:  # noqa: BLE001 - no audio system: only the default
            mics = []
        mic = tk.StringVar(value=rec.mic_device or "Windows default")
        pause_key, stop_key = tk.StringVar(value=rec.pause_hotkey), tk.StringVar(value=rec.stop_hotkey)
        typed = tk.BooleanVar(value=rec.capture_typed_text)
        ttk.Label(f, text="Microphone for narration").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=mic, values=["Windows default", *mics], state="readonly", width=40).grid(
            row=0, column=1, sticky="w", padx=8)
        for n, (label, var) in enumerate((("Pause / resume shortcut", pause_key), ("Stop shortcut", stop_key)), 1):
            ttk.Label(f, text=label).grid(row=n, column=0, sticky="w", pady=3)
            ttk.Entry(f, textvariable=var, width=16).grid(row=n, column=1, sticky="w", padx=8)
        ttk.Checkbutton(f, variable=typed, text="Store the text I type (off: only counted)").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(f, style="Muted.TLabel", text=f"Signed in to {self.cloud.server if self.cloud else 'no server'}"
                  ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def save() -> None:
            keys = (pause_key.get().strip().lower(), stop_key.get().strip().lower())
            if not all(HOTKEY.match(k) for k in keys) or keys[0] == keys[1]:
                messagebox.showerror("FlowSME", "Use two different shortcuts of the form ctrl+alt+p.", parent=win)
                return
            if typed.get() and not rec.capture_typed_text and not messagebox.askyesno(
                    "FlowSME", "Storing typed text keeps everything people type, including personal data. Store it "
                               "anyway?", parent=win):
                return
            rec.mic_device = "" if mic.get() == "Windows default" else mic.get()
            rec.pause_hotkey, rec.stop_hotkey = keys
            rec.capture_typed_text = typed.get()
            save_config(cfg, self.cfg_path)
            self.cfg = self._config()
            win.destroy()
            self.refresh_local()
            print("Settings saved.")
        self._buttons(win, f, 5, "Save", save)

    # ---- plumbing ---------------------------------------------------------------------------------

    def _bg(self, job, done) -> None:
        """Run a slow call off the window's thread; done(result, error) runs on it afterwards."""
        def run() -> None:
            try:
                self.events.put(("call", done, job(), None))
            except (CloudError, SystemExit, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                self.events.put(("call", done, None, str(exc) or exc.__class__.__name__))
        threading.Thread(target=run, daemon=True).start()

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
        bar.grid(row=row, column=0, columnspan=4, sticky="e", pady=(14, 0))
        ttk.Button(bar, text=label, style="Accent.TButton", command=action).pack(side="left")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left", padx=(8, 0))
        win.bind("<Escape>", lambda _: win.destroy())
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_reqheight()) // 2)
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _load_state(self) -> dict:
        with contextlib.suppress(OSError, ValueError):
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    def _save_state(self, **values) -> None:
        self.state.update(values)
        with contextlib.suppress(OSError):
            self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def _pump(self) -> None:
        lines = []
        with contextlib.suppress(queue.Empty):
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    lines.append(event[1])
                elif event[0] == "recorded":
                    self._recorded(event[1])
                elif event[0] == "call":
                    event[1](event[2], event[3])
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
        if self.busy and not messagebox.askyesno("FlowSME", "Something is still being sent. Quit anyway?",
                                                 parent=self.root):
            return
        self.root.destroy()


def style(root: tk.Tk) -> None:
    s = ttk.Style(root)
    if "vista" in s.theme_names():
        s.theme_use("vista")
    scale = root.winfo_fpixels("1i") / 96
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family="Segoe UI", size=10)
    s.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
    s.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
    s.configure("Muted.TLabel", foreground="#5f6368")
    s.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 8))
    s.configure("Treeview", rowheight=int(24 * scale))
    s.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
    root.geometry(f"{int(760 * scale)}x{int(740 * scale)}")
    root.minsize(int(600 * scale), int(520 * scale))


def self_test() -> tuple[bool, str]:
    """What an installed recorder needs: input hooks, UI Automation, screen capture, the microphone, a short
    recording, and the FlowSME server if someone is signed in. Records nothing from the microphone."""
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

    def recording():
        from ..recorder import Recorder

        tmp = Path(tempfile.mkdtemp(prefix="flowsme-check-"))
        try:
            rec = Recorder(Config(), tmp / "recording")
            threading.Timer(2, rec.stop).start()
            rec.run()
            return f"2-second recording saved: {(tmp / 'recording' / 'meta.json').exists()}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def server():
        signed = account.load(home())
        if not signed:
            return "nobody signed in yet"
        me = Cloud(*signed).me()
        return f"signed in to {signed[0]} as {me['name']} ({me['team']})"

    for name, fn in (("input hooks", hooks), ("UI Automation", automation), ("screen capture", screen),
                     ("microphone", microphone), ("recording", recording), ("FlowSME server", server)):
        check(name, fn)
    ok = not any(line.startswith("FAIL") for line in results)
    return ok, f"FlowSME Recorder {__version__} self-test: {'all good' if ok else 'problems found'}\n\n" + \
        "\n".join(results)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("FlowSME records on Windows.")
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    if "--self-test" in sys.argv:
        ok, text = self_test()
        out = Path(os.environ.get("TEMP", ".")) / "FlowSME-Recorder-self-test.txt"
        out.write_text(text, encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, f"{text}\n\nSaved to {out}", "FlowSME", 0x40 if ok else 0x10)
        return
    with contextlib.suppress(Exception):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FlowSME.Client")
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FlowSME.Client")
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(None, "FlowSME is already open.", "FlowSME", 0x40)
        return
    root = tk.Tk()
    icon = _icon()
    if icon:
        with contextlib.suppress(tk.TclError):
            root.iconbitmap(default=str(icon))
    style(root)
    ClientApp(root)
    root.mainloop()
    del mutex
