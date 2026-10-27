"""Data structures shared by the analysis pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field

APP_NAMES = {
    "excel.exe": "Excel", "winword.exe": "Word", "powerpnt.exe": "PowerPoint", "onenote.exe": "OneNote",
    "outlook.exe": "Outlook", "olk.exe": "Outlook", "ms-teams.exe": "Teams", "teams.exe": "Teams",
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave",
    "opera.exe": "Opera", "explorer.exe": "File Explorer", "acrord32.exe": "Acrobat Reader",
    "acrobat.exe": "Acrobat", "notepad.exe": "Notepad", "code.exe": "VS Code", "slack.exe": "Slack",
    "saplogon.exe": "SAP GUI", "sapgui.exe": "SAP GUI", "applicationframehost.exe": "Windows app",
}
BROWSER_EXES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
BROWSER_LABELS = {APP_NAMES[exe] for exe in BROWSER_EXES}
_BROWSER_TITLES = {"google chrome", "microsoft edge", "mozilla firefox", "brave", "opera"}
_PROFILE_PREFIXES = ("personal", "work", "profile")


def app_label(exe: str) -> str:
    if not exe:
        return "Unknown app"
    return APP_NAMES.get(exe.lower(), exe.rsplit(".", 1)[0])


def context_label(exe: str, title: str) -> str:
    """Application name, plus the website for browser windows (e.g. 'Chrome · Vendor Portal')."""
    label = app_label(exe)
    if (exe or "").lower() not in BROWSER_EXES or not title or title.startswith("["):
        return label
    clean = title.replace("​", "").replace(" — ", " - ")
    parts = [p.strip() for p in clean.split(" - ") if p.strip()]
    parts = [p for p in parts if p.lower() not in _BROWSER_TITLES]
    while len(parts) > 1 and parts[-1].lower().startswith(_PROFILE_PREFIXES):
        parts.pop()
    if len(parts) < 2:
        return label
    return f"{label} · {parts[-1][:40]}"


def base_app(app: str) -> str:
    """'Chrome · Vendor Portal' -> 'Chrome'."""
    return app.split(" · ")[0]


@dataclass
class Step:
    """One user action: a click plus the typing/shortcuts that follow it in the same window."""
    index: int
    start: float
    end: float
    app: str
    exe: str
    title: str
    action: str  # click | type | shortcut | key | scroll
    target: str = ""
    role: str = ""
    double: bool = False
    chars_typed: int = 0
    shortcuts: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    scrolls: int = 0
    text: str = ""
    excluded: bool = False
    screenshot: str | None = None
    shot_xy: list[float] | None = None
    shot_rect: list[int] | None = None  # the window being worked in, on the screenshot (left, top, right, bottom)
    duration: float = 0.0
    description: str = ""
    signature: str = ""
    right_click: bool = False
    copy_hash: str | None = None  # salted fingerprint of what was copied (never the value)
    typed_hashes: list[str] = field(default_factory=list)  # fingerprints of what was typed
    narration: str = ""  # what the person said while doing this step

    @property
    def copies(self) -> bool:
        return any(s in COPY_KEYS for s in self.shortcuts)

    @property
    def pastes(self) -> bool:
        return any(s in PASTE_KEYS for s in self.shortcuts)


# Keyboard shortcuts, menu/ribbon commands ("menu:...") and clipboard changes the recorder saw.
COPY_KEYS = {"ctrl+c", "ctrl+x", "ctrl+insert", "shift+delete", "menu:copy", "menu:cut", "clipboard"}
PASTE_KEYS = {"ctrl+v", "ctrl+shift+v", "shift+insert", "menu:paste"}


@dataclass
class Task:
    """A chunk of related steps. Repeated tasks keep each run (iteration) separately."""
    id: int
    steps: list[Step]
    iterations: list[list[Step]] = field(default_factory=list)
    misc: bool = False
    name: str = ""
    summary: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    decision_points: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    score: float = 0.0
    level: str = ""
    reasons: list[str] = field(default_factory=list)
    recommendation: dict = field(default_factory=dict)
    estimate: dict = field(default_factory=dict)
    integration: dict = field(default_factory=dict)
    improvement: dict = field(default_factory=dict)  # tool plan, validation checklist, Lean Six Sigma analysis
    same_as: int | None = None  # the same process as this task (its design, SOP and roadmap entry cover both)

    @property
    def runs(self) -> int:
        return len(self.iterations) or 1

    @property
    def repeated(self) -> bool:
        return len(self.iterations) >= 3

    rep: int = 0  # which run is the most typical one (set when runs are found)

    @property
    def representative(self) -> list[Step]:
        return self.iterations[self.rep] if self.iterations else self.steps

    @property
    def start(self) -> float:
        return self.steps[0].start

    @property
    def end(self) -> float:
        return self.steps[-1].end

    @property
    def minutes(self) -> float:
        return sum(s.duration for s in self.steps) / 60

    @property
    def apps(self) -> list[str]:
        return list(dict.fromkeys(s.app for s in self.steps))


def fmt_minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{minutes * 60:.0f} s"
    if minutes < 10:
        return f"{minutes:.1f} min"
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"
