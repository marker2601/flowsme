"""Generate a realistic synthetic recording (accounts-payable invoice processing) so the pipeline can be
tried without recording anything. Events follow exactly the format the recorder writes."""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VENDORS = ["Northwind Traders", "Contoso Supplies", "Fabrikam Ltd", "Adventure Works", "Litware Inc",
           "Tailspin Toys", "Proseware", "Wingtip Toys"]
OUTLOOK = ("OUTLOOK.EXE", "Inbox - Outlook")
EXCEL = ("EXCEL.EXE", "AP Tracker.xlsx - Excel")
PORTAL_HOME = ("chrome.exe", "Dashboard - Vendor Portal - Google Chrome")
PORTAL_FORM = ("chrome.exe", "New Payment Request - Vendor Portal - Google Chrome")
def _fingerprint(value: str) -> str:
    """Same scheme as the recorder (salted HMAC, normalised text), with a fixed salt for the demo."""
    normalized = " ".join(value.split()).casefold().encode("utf-8")
    return hmac.new(b"flowsme-demo", normalized, hashlib.sha256).hexdigest()[:16]


# What a real screen would show under the title bar: column headers, form fields, recipients.
SCREEN_LABELS = [
    ("AP Tracker", "A Invoice #  |  B Vendor  |  C Amount  |  D Due date  |  E GL code"),
    ("New Payment Request", "Vendor  |  Amount  |  Invoice number  |  Due date  |  Attach file  |  Submit"),
    ("Message (HTML)", "To: AP Manager  |  Subject: Weekly AP summary  |  Message  |  Attach File  |  Send"),
    (".pdf - Adobe", "INVOICE    Invoice number  |  Vendor  |  Total due  |  Payment terms"),
    ("Inbox", "Inbox  |  From  |  Subject  |  Attachments  |  Move  |  Processed"),
]
APP_COLORS = {"OUTLOOK.EXE": (0, 114, 198), "EXCEL.EXE": (33, 115, 70), "AcroRd32.exe": (180, 30, 30),
              "chrome.exe": (66, 133, 244), "explorer.exe": (200, 150, 0)}


class _Recording:
    def __init__(self, root: Path, seed: int = 7):
        self.root = root
        self.rng = random.Random(seed)
        self.t = 0.0
        self.events: list[dict] = []
        self.window = OUTLOOK
        self.excluded = False
        self.n_shots = 0
        self.font = ImageFont.load_default(size=15)
        (root / "shots").mkdir(parents=True, exist_ok=True)

    def wait(self, lo: float, hi: float) -> None:
        self.t += self.rng.uniform(lo, hi)

    def _shot(self, label: str | None = None, xy: tuple[int, int] | None = None) -> str:
        """A mock screen: coloured title bar, content rows, and the clicked element highlighted."""
        self.n_shots += 1
        exe, title = self.window
        img = Image.new("RGB", (800, 450), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 800, 34], fill=APP_COLORS.get(exe, (90, 90, 90)))
        draw.text((12, 9), title, fill="white", font=self.font)
        labels = next((text for key, text in SCREEN_LABELS if key in title), None)
        if labels:
            draw.text((24, 40), labels, fill=(60, 60, 60), font=self.font)
        for row in range(6):
            y = 60 + row * 62
            draw.rectangle([24, y, 776, y + 40], outline=(210, 210, 210), fill="white")
        if label and xy:
            x, y = xy
            draw.rectangle([x - 95, y - 16, x + 95, y + 16], fill=(255, 248, 200), outline=(200, 160, 0))
            draw.text((x - 88, y - 8), label[:24], fill=(30, 30, 30), font=self.font)
        rel = f"shots/{self.n_shots:06d}.jpg"
        img.save(self.root / rel, "JPEG", quality=60)
        return rel

    def _emit(self, kind: str, t: float | None = None, **fields) -> None:
        exe, title = self.window
        event = {"t": round(self.t if t is None else t, 3), "type": kind, "app": exe,
                 "title": "[excluded]" if self.excluded else title}
        if self.excluded:
            event["excluded"] = True
        event.update(fields)
        self.events.append(event)

    def focus(self, window: tuple[str, str], excluded: bool = False) -> None:
        self.wait(0.4, 1.5)
        self.window, self.excluded = window, excluded
        self._emit("focus", **({} if excluded else {"shot": self._shot()}))

    def click(self, name: str, role: str, *, wait=(2.0, 6.0), shortcuts=(), typed: int = 0,
              typed_value: str | None = None, copied: str | None = None, key: str | None = None,
              button: str = "left") -> None:
        self.wait(*wait)
        xy = (self.rng.randint(130, 670), self.rng.randint(80, 400))
        fields: dict = {"x": xy[0], "y": xy[1], "button": button}
        if not self.excluded:
            fields.update(element={"name": name, "role": role}, shot=self._shot(name, xy), shot_xy=list(xy))
        self._emit("click", **fields)
        for combo in shortcuts:
            self.wait(0.4, 1.0)
            self._emit("shortcut", keys=combo)
        if copied is not None:
            self._clipboard(copied)
        if typed or typed_value:
            typed = len(typed_value) if typed_value else typed
            self.wait(0.3, 0.8)
            start = self.t
            self.t += typed * self.rng.uniform(0.18, 0.3)
            extra = {"value_hash": _fingerprint(typed_value)} if typed_value and not self.excluded else {}
            self._emit("type", t=start, t_end=round(self.t, 3), chars=typed, edits=self.rng.randint(0, 2), **extra)
        if key:
            self.wait(0.2, 0.6)
            self._emit("key", keys=key)

    def menu(self, name: str, role: str = "MenuItemControl", copied: str | None = None) -> None:
        """A click on a context-menu or ribbon command such as Copy or Paste."""
        self.wait(0.5, 1.5)
        xy = (self.rng.randint(130, 670), self.rng.randint(80, 400))
        self._emit("click", x=xy[0], y=xy[1], button="left", element={"name": name, "role": role},
                   shot=self._shot(name, xy), shot_xy=list(xy))
        if copied is not None:
            self._clipboard(copied)

    def _clipboard(self, value: str) -> None:
        """What the recorder writes when the clipboard changes: a fingerprint, never the value."""
        self._emit("clipboard", t=self.t + 0.001, hash=_fingerprint(value), chars=len(value))

    def scroll(self, count: int = 3) -> None:
        self.wait(1.0, 3.0)
        start = self.t
        self.t += count * 0.25
        self._emit("scroll", t=start, t_end=round(self.t, 3), count=count, shot=self._shot())


def build_demo(sessions_dir: Path) -> Path:
    root = Path(sessions_dir) / "demo-invoice-processing"
    # On Windows a folder that is open in a shell or explorer can't be removed; clearing its files is enough.
    shutil.rmtree(root, ignore_errors=True)
    rec = _Recording(root)

    # 1. Getting set up: unread invoices, the tracker, the vendor portal (sign-in page is excluded).
    rec.focus(OUTLOOK)
    rec.click("Inbox", "TreeItemControl")
    rec.click("Filter", "ButtonControl")
    rec.click("Unread", "MenuItemControl")
    rec.scroll(4)
    rec.focus(("explorer.exe", "AP - File Explorer"))
    rec.click("AP Tracker.xlsx", "ListItemControl")
    rec.click("AP Tracker.xlsx", "ListItemControl", wait=(0.15, 0.3))
    rec.focus(EXCEL)
    rec.click("October", "TabItemControl")
    rec.click("A1", "DataItemControl", shortcuts=["ctrl+end"])
    rec.focus(("chrome.exe", "Sign in - Vendor Portal - Google Chrome"), excluded=True)
    rec.click("", "", typed=22, key="enter")
    rec.focus(PORTAL_HOME)
    rec.click("Payments", "HyperlinkControl")

    # 2. The routine: one invoice email -> PDF -> tracker -> portal payment request, eight times.
    for i, vendor in enumerate(VENDORS):
        inv, row = f"INV-{10431 + i * 7}", 42 + i
        total, due, gl = f"{rec.rng.uniform(150, 9500):,.2f}", f"2026-11-{10 + i:02d}", "6100" if i % 2 else "6200"
        pdf = ("AcroRd32.exe", f"{inv}.pdf - Adobe Acrobat Reader (64-bit)")
        rec.focus(OUTLOOK)
        rec.click(f"Invoice {inv} from {vendor}", "ListItemControl", wait=(3, 9))
        rec.click(f"{inv}.pdf", "ButtonControl")
        rec.focus(pdf)
        if i == 3:  # a blurry scan: the user zooms in first
            rec.scroll(2)
            rec.click("Zoom in", "ButtonControl")
        rec.click("Invoice number", "TextControl", shortcuts=["ctrl+c"], copied=inv, wait=(3, 8))
        rec.focus(EXCEL)
        rec.click(f"A{row}", "DataItemControl", shortcuts=["ctrl+v"])
        rec.focus(pdf)
        if i == 1:  # this time the user copies with the right-click menu
            rec.click("Vendor", "TextControl", button="right")
            rec.menu("Copy", copied=vendor)
        else:
            rec.click("Vendor", "TextControl", shortcuts=["ctrl+c"], copied=vendor)
        rec.focus(EXCEL)
        if i == 6:  # ...and here pastes with the ribbon's Paste button
            rec.click(f"B{row}", "DataItemControl")
            rec.menu("Paste", role="SplitButtonControl")
        else:
            rec.click(f"B{row}", "DataItemControl", shortcuts=["ctrl+v"])
        rec.focus(pdf)
        rec.scroll(3)
        rec.click("Total due", "TextControl", shortcuts=["ctrl+c"], copied=total)
        rec.focus(EXCEL)
        rec.click(f"C{row}", "DataItemControl", shortcuts=["ctrl+v"])
        rec.click(f"D{row}", "DataItemControl", typed_value=due, key="enter")
        rec.click(f"E{row}", "DataItemControl", typed_value=gl, key="enter")
        rec.click(f"C{row}", "DataItemControl", shortcuts=["ctrl+c"], copied=total)
        rec.focus(PORTAL_HOME)
        rec.click("New payment request", "ButtonControl")
        rec.focus(PORTAL_FORM)
        rec.click("Vendor", "EditControl", typed_value=vendor)
        rec.click("Amount", "EditControl", shortcuts=["ctrl+v"])
        rec.click("Invoice number", "EditControl", typed_value=inv)
        rec.click("Due date", "EditControl", typed_value=due)
        rec.click("Attach file", "ButtonControl")
        rec.focus(("chrome.exe", "Open"))
        rec.click(f"{inv}.pdf", "ListItemControl")
        rec.click("Open", "ButtonControl", wait=(0.8, 1.5))
        rec.focus(PORTAL_FORM)
        rec.click("Submit", "ButtonControl", wait=(3, 6))
        if i == 5:  # the portal timed out and the user submitted again
            rec.click("Submit", "ButtonControl", wait=(5, 9))
        rec.focus(PORTAL_HOME)
        rec.focus(OUTLOOK)
        rec.click("Move", "ButtonControl")
        rec.click("Processed", "MenuItemControl")

    # 3. Weekly summary to the manager.
    rec.focus(EXCEL)
    rec.click("Summary", "TabItemControl", wait=(3, 6))
    rec.click("Refresh All", "ButtonControl")
    rec.click("A1", "DataItemControl", shortcuts=["ctrl+a", "ctrl+c"])
    rec.focus(OUTLOOK)
    rec.click("New Email", "ButtonControl")
    rec.focus(("OUTLOOK.EXE", "Untitled - Message (HTML)"))
    rec.click("To", "EditControl", typed=24)
    rec.click("Subject", "EditControl", typed=38)
    rec.click("Message body", "DocumentControl", shortcuts=["ctrl+v"], typed=180)
    rec.click("Attach File", "ButtonControl")
    rec.click("AP Tracker.xlsx", "MenuItemControl")
    rec.click("Send", "ButtonControl", wait=(4, 9))
    rec.focus(OUTLOOK)

    with open(root / "events.jsonl", "w", encoding="utf-8") as fp:
        for event in rec.events:
            fp.write(json.dumps(event) + "\n")
    meta = {"session": root.name, "name": "Demo: invoice processing (synthetic)",
            "started": "2026-10-21T09:12:00", "duration_seconds": round(rec.t, 1),
            "events": len(rec.events), "screenshots": rec.n_shots,
            "privacy": {"capture_typed_text": False}}
    (root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return root
