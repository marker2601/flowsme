"""Windows desktop recorder.

Writes sessions/<id>/events.jsonl (one JSON event per line) and shots/*.jpg:
  click    - position, the UI element under the cursor (via UI Automation) and a screenshot
  type     - a burst of typing: character count, a value fingerprint (and the text only if
             capture_typed_text = true)
  clipboard - the clipboard changed: a fingerprint of its text, never the text
  shortcut - ctrl/alt/win combos such as ctrl+c, plus shift+insert / shift+delete
  key      - enter, tab, esc, delete, function keys
  scroll   - a burst of scrolling
  focus    - the active window changed (app + title) with a screenshot
  pause / resume

Input hooks only enqueue raw events; one worker thread does the slow work (screenshots, UI Automation)
so the system never feels laggy while recording.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import hmac
import json
import queue
import secrets
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
from PIL import Image, ImageChops, ImageStat

from .config import Config

MOD_NAMES = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "altgr",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win",
}
MOD_ORDER = ("ctrl", "alt", "shift", "win")
IGNORED_KEYS = {
    "up", "down", "left", "right", "home", "end", "page_up", "page_down", "insert", "caps_lock",
    "num_lock", "scroll_lock", "print_screen", "menu", "pause",
}
# Shortcuts that move data: log which element had focus, i.e. where the data really came from or went.
DATA_COMBOS = {"ctrl+c", "ctrl+x", "ctrl+v", "ctrl+shift+v", "ctrl+insert"}
STUCK_MODIFIER = 5.0  # seconds; held keys auto-repeat, so an older press means we missed its release
TYPING_IDLE = 3.0
SCROLL_IDLE = 1.5

user32 = kernel32 = None  # set up by _setup_win32()


def normalize_combo(combo: str) -> str:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    return "+".join([m for m in MOD_ORDER if m in parts] + [p for p in parts if p not in MOD_ORDER])


def _setup_win32() -> None:
    global user32, kernel32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, so clicks line up with screenshots
    except (AttributeError, OSError):
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]


def _clipboard_seq() -> int:
    return user32.GetClipboardSequenceNumber()


def _clipboard_text() -> str | None:
    """Clipboard text, or None if it is unavailable, not text, or marked private by the app that set it
    (password managers set ExcludeClipboardContentFromMonitorProcessing)."""
    private = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    if not user32.OpenClipboard(None):
        raise BlockingIOError("clipboard busy")  # another app is writing: try again on the next event
    try:
        if private and user32.IsClipboardFormatAvailable(private):
            return None
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _window_info(hwnd, pid_names: dict) -> tuple:
    """(hwnd, exe name, window title) for a top-level window."""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = pid_names.get(pid.value)
    if exe is None:
        try:
            exe = psutil.Process(pid.value).name()
        except psutil.Error:
            exe = "unknown"
        pid_names[pid.value] = exe
    return hwnd, exe, buf.value


def _root_window_at(x: int, y: int):
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    return user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT


def _window_center(hwnd) -> tuple[int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def _key_name(key) -> tuple[str, str | None]:
    """(normalized key name, printable character or None)."""
    from pynput.keyboard import Key

    if isinstance(key, Key):
        return key.name, None
    vk = getattr(key, "vk", None)
    char = getattr(key, "char", None)
    printable = char if char and len(char) == 1 and char.isprintable() else None
    # With ctrl held Windows reports control characters, so name letters/digits by virtual-key code.
    if vk is not None and (65 <= vk <= 90 or 48 <= vk <= 57):
        return chr(vk).lower(), printable
    if printable:
        return printable.lower(), printable
    return (f"vk{vk}" if vk is not None else "unknown"), None


class Recorder:
    def __init__(self, cfg: Config, session_dir: Path):
        self.cfg = cfg.recorder
        self.dir = Path(session_dir)
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_combo = normalize_combo(self.cfg.pause_hotkey)
        self.stop_combo = normalize_combo(self.cfg.stop_hotkey)
        self.excluded_apps = {a.lower() for a in self.cfg.excluded_apps}
        self.excluded_words = [w.lower() for w in self.cfg.excluded_title_keywords]
        self.t0 = 0.0
        self.fp = None
        self.n_events = 0
        self.n_shots = 0
        # Worker-thread state (only touched by the worker).
        self.paused = False
        self.mods: dict[str, float] = {}
        self.window: tuple | None = None
        self.typing: dict | None = None
        self.scroll: dict | None = None
        self.last_click: tuple = (None, False)  # (hwnd, clicked a password field)
        self.pid_names: dict[int, str] = {}
        self.last_shot: tuple = (0.0, None, None)  # (time, path, thumbnail)
        self.sct = None
        self.auto = None
        self.salt = secrets.token_bytes(32)  # in memory only: never written to disk
        self.clip_seq: int | None = None
        self.last_t = 0.0

    # ---- main thread -------------------------------------------------------------------------

    def run(self, duration: float | None = None) -> Path:
        if sys.platform != "win32":
            raise SystemExit("Recording currently works on Windows only (analysis works anywhere).")
        from pynput import keyboard, mouse

        _setup_win32()
        (self.dir / "shots").mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        self.t0 = self.last_t = time.time()
        self.fp = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self._write_meta(started, ended=None)

        worker = threading.Thread(target=self._worker, name="flowsme-worker", daemon=True)
        worker.start()
        listeners = [
            mouse.Listener(on_click=self._hook_click, on_scroll=self._hook_scroll),
            keyboard.Listener(on_press=self._hook_press, on_release=self._hook_release),
        ]
        for listener in listeners:
            listener.start()
        print(f"Recording to {self.dir}", flush=True)
        print(f"  {self.pause_combo} = pause/resume   {self.stop_combo} (or Ctrl+C here) = stop", flush=True)
        if not self.cfg.capture_typed_text:
            print("  Typed text is counted, not stored.", flush=True)

        deadline = time.monotonic() + duration if duration else None
        try:
            while not self.stop_event.wait(0.5):
                if deadline and time.monotonic() >= deadline:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            for listener in listeners:
                listener.stop()
            self.q.put(None)
            worker.join(timeout=15)
            self.fp.close()
            self._write_meta(started, ended=datetime.now())
        print(f"Saved {self.n_events} events and {self.n_shots} screenshots.")
        return self.dir

    # Hooks run inside the OS input hook: keep them tiny or Windows drops the hook.
    def _hook_click(self, x, y, button, pressed):
        if pressed:
            self.q.put(("click", time.time(), x, y, button.name))

    def _hook_scroll(self, x, y, dx, dy):
        self.q.put(("scroll", time.time(), x, y))

    def _hook_press(self, key):
        self.q.put(("press", time.time(), key))

    def _hook_release(self, key):
        self.q.put(("release", time.time(), key))

    # ---- worker thread -----------------------------------------------------------------------

    def _worker(self) -> None:
        import mss

        try:
            import uiautomation as auto
        except ImportError:
            auto = None
        self.auto = auto
        com = auto.UIAutomationInitializerInThread() if auto else contextlib.nullcontext()
        grabber = getattr(mss, "MSS", None) or mss.mss  # mss 10 renamed the class
        with com, grabber() as sct:
            self.sct = sct
            self._poll_focus(time.time())
            self.clip_seq = _clipboard_seq()  # what was already on the clipboard is not part of the recording
            while True:
                try:
                    item = self.q.get(timeout=0.5)
                except queue.Empty:
                    item = ("tick", time.time())
                if item is None:
                    break
                try:
                    self._handle(item)
                except Exception as exc:  # never let one bad event stop the recording
                    print(f"  recorder warning: {exc!r}", file=sys.stderr)
            self._flush_typing()
            self._flush_scroll()

    def _handle(self, item: tuple) -> None:
        kind, t = item[0], item[1]
        self._poll_clipboard()  # a copy shows up once the app has updated the clipboard
        if kind != "tick":
            self.last_t = t
        if kind == "tick":
            self._poll_focus(t)
            if self.typing and t - self.typing["t_end"] > TYPING_IDLE:
                self._flush_typing()
            if self.scroll and t - self.scroll["t_end"] > SCROLL_IDLE:
                self._flush_scroll()
        elif kind == "release":
            name, _ = _key_name(item[2])
            if name in MOD_NAMES:
                self.mods.pop(MOD_NAMES[name], None)
        elif kind == "press":
            self._key(t, item[2])
        elif self.paused:
            return
        elif kind == "click":
            self._click(t, *item[2:])
        elif kind == "scroll":
            self._scroll(t, item[2], item[3])

    def _key(self, t: float, key) -> None:
        name, char = _key_name(key)
        if name in MOD_NAMES:
            self.mods[MOD_NAMES[name]] = t
            return
        self.mods = {m: since for m, since in self.mods.items() if t - since < STUCK_MODIFIER}
        held = set(self.mods)
        if held == {"shift"} and name in ("insert", "delete"):  # Shift+Insert pastes, Shift+Delete cuts
            if not self.paused:
                self._poll_focus(t)
                self._flush_typing()
                self._flush_scroll()
                self._emit(t, "shortcut", keys=f"shift+{name}", **self._focus_fields())
            return
        # AltGr (reported as ctrl+alt) types characters such as @ on many keyboards: treat it as typing.
        is_combo = "altgr" not in held and bool(held & {"ctrl", "alt", "win"})
        if is_combo:
            combo = "+".join([m for m in MOD_ORDER if m in held] + [name])
            if combo == self.pause_combo:
                self._toggle_pause(t)
                return
            if combo == self.stop_combo:
                self.stop_event.set()
                return
            if self.paused:
                return
            self._poll_focus(t)
            self._flush_typing()
            self._flush_scroll()
            self._emit(t, "shortcut", keys=combo, **(self._focus_fields() if combo in DATA_COMBOS else {}))
            return
        if self.paused:
            return
        self._poll_focus(t)
        if name == "space":
            char = " "
        if char is not None or name == "backspace":
            self._type(t, char)
        elif name not in IGNORED_KEYS and not name.startswith(("media", "volume")):
            self._flush_typing()
            self._flush_scroll()
            self._emit(t, "key", keys=name)

    def _type(self, t: float, char: str | None) -> None:
        if self.typing and self.typing["window"] != self.window:
            self._flush_typing()
        if self.typing is None:
            self._flush_scroll()
            hwnd, exe, title = self.window or (None, "unknown", "")
            private = self._is_excluded(exe, title) or (self.last_click[0] == hwnd and self.last_click[1])
            # "secret" holds the typed characters in memory only, to fingerprint them; it is never written.
            self.typing = {"t": t, "t_end": t, "chars": 0, "edits": 0, "text": [], "secret": [],
                           "window": self.window, "private": private,
                           "focus": None if private else self._focus_fields().get("focus")}
        tb = self.typing
        tb["t_end"] = t
        keep_text = self.cfg.capture_typed_text and not tb["private"]
        if char is None:
            tb["edits"] += 1
            if keep_text and tb["text"]:
                tb["text"].pop()
            if tb["secret"]:
                tb["secret"].pop()
        else:
            tb["chars"] += 1
            if keep_text:
                tb["text"].append(char)
            if not tb["private"]:
                tb["secret"].append(char)

    def _flush_typing(self) -> None:
        tb, self.typing = self.typing, None
        if not tb or (tb["chars"] == 0 and tb["edits"] == 0):
            return
        fields = {"t_end": self._rel(tb["t_end"]), "chars": tb["chars"], "edits": tb["edits"]}
        if tb["text"]:
            fields["text"] = "".join(tb["text"])
        if tb["secret"] and self.cfg.fingerprint_values:
            fields["value_hash"] = self._fingerprint("".join(tb["secret"]))
        if tb["focus"]:
            fields["focus"] = tb["focus"]
        tb["secret"].clear()
        self._emit(tb["t"], "type", window=tb["window"], **fields)

    def _fingerprint(self, text: str) -> str:
        """Salted one-way fingerprint: equal values match within this recording, but nothing can be read back.
        The salt lives only in memory while recording, so fingerprints can't be checked against guesses later."""
        normalized = " ".join(text.split()).casefold().encode("utf-8")
        return hmac.new(self.salt, normalized, hashlib.sha256).hexdigest()[:16]

    def _poll_clipboard(self) -> None:
        if not self.cfg.fingerprint_values or self.clip_seq is None:
            return
        seq = _clipboard_seq()
        if seq == self.clip_seq:
            return
        try:
            text = _clipboard_text()
        except BlockingIOError:
            return  # another app holds the clipboard: try again on the next event
        self.clip_seq = seq
        _, exe, title = self.window or (None, "unknown", "")
        if self.paused or not text or not text.strip() or self._is_excluded(exe, title):
            return
        self._emit(self.last_t + 0.001, "clipboard", hash=self._fingerprint(text), chars=len(text.strip()))

    def _click(self, t: float, x: int, y: int, button: str) -> None:
        self._flush_typing()
        self._flush_scroll()
        root = _root_window_at(x, y)
        window = _window_info(root, self.pid_names) if root else self.window
        fields: dict = {"x": x, "y": y, "button": button}
        if window and not self._is_excluded(window[1], window[2]):
            element = self._element_at(x, y, root)
            if element:
                fields["element"] = element
            path, xy = self._screenshot(x, y)
            if path:
                fields.update(shot=path, shot_xy=xy)
        self.last_click = (window[0] if window else None, bool(fields.get("element", {}).get("password")))
        self._emit(t, "click", window=window, **fields)
        self._poll_focus(t)

    def _scroll(self, t: float, x: int, y: int) -> None:
        self._flush_typing()
        sc = self.scroll
        if sc and t - sc["t_end"] <= SCROLL_IDLE and sc["window"] == self.window:
            sc["count"] += 1
            sc["t_end"] = t
            sc["xy"] = (x, y)
            return
        self._flush_scroll()
        self.scroll = {"t": t, "t_end": t, "count": 1, "window": self.window, "xy": (x, y)}

    def _flush_scroll(self) -> None:
        sc, self.scroll = self.scroll, None
        if not sc:
            return
        fields: dict = {"t_end": self._rel(sc["t_end"]), "count": sc["count"]}
        window = sc["window"]
        if window and not self._is_excluded(window[1], window[2]):
            path, _ = self._screenshot(*sc["xy"])
            if path:
                fields["shot"] = path
        self._emit(sc["t"], "scroll", window=window, **fields)

    def _poll_focus(self, t: float) -> None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return
        info = _window_info(hwnd, self.pid_names)
        if info == self.window:
            return
        self._flush_typing()
        self._flush_scroll()
        self.window = info
        if self.paused:
            return
        fields = {}
        if not self._is_excluded(info[1], info[2]):
            path, _ = self._screenshot(*_window_center(hwnd))
            if path:
                fields["shot"] = path
        self._emit(t, "focus", **fields)

    def _toggle_pause(self, t: float) -> None:
        self._flush_typing()
        self._flush_scroll()
        if not self.paused:
            self._emit(t, "pause")
            self.paused = True
            print("  Paused. Press the pause hotkey again to resume.", flush=True)
        else:
            self.paused = False
            self._emit(t, "resume")
            self.window = None
            self._poll_focus(t)
            print("  Recording again.", flush=True)

    def _focus_fields(self) -> dict:
        """The element that has keyboard focus: where a paste or typing really lands, even when the user
        moved there with Tab or Enter, or a click didn't select what it aimed at."""
        if not self.auto:
            return {}
        try:
            ctrl = self.auto.GetFocusedControl()
            if ctrl and ctrl.Name:
                return {"focus": {"name": ctrl.Name.strip()[:120], "role": ctrl.ControlTypeName}}
        except Exception:
            pass
        return {}

    def _element_at(self, x: int, y: int, root=None) -> dict | None:
        if not self.auto:
            return None
        try:
            ctrl = self.auto.ControlFromPoint(x, y)
            pid = wintypes.DWORD()
            if root:
                user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
            if ctrl and pid.value and ctrl.ProcessId != pid.value:
                # A click-through overlay from another app sits on top: look inside the clicked window instead.
                ctrl = self._descend(self.auto.ControlFromHandle(root), x, y, depth=12)
            if ctrl and ctrl.ControlTypeName in ("PaneControl", "WindowControl", "CustomControl", "MenuControl"):
                # Some menus (e.g. Notepad's) answer with their host window: find the item actually clicked.
                ctrl = self._descend(ctrl, x, y, depth=6) or ctrl
            if not ctrl:
                return None
            name, probe = ctrl.Name or "", ctrl
            for _ in range(2):  # unnamed element: borrow the name of a close parent
                if name:
                    break
                probe = probe.GetParentControl()
                if not probe:
                    break
                name = probe.Name or ""
            info = {"name": name.strip()[:120], "role": ctrl.ControlTypeName,
                    "automation_id": (ctrl.AutomationId or "")[:80]}
            with contextlib.suppress(Exception):
                if ctrl.IsPassword:
                    info["password"] = True
            return info
        except Exception:
            return None

    @staticmethod
    def _descend(ctrl, x: int, y: int, depth: int = 8):
        """Walk down to the smallest element whose rectangle contains the point."""
        for _ in range(depth):
            if not ctrl:
                break
            best, best_area = None, None
            for child in ctrl.GetChildren():
                r = child.BoundingRectangle
                if r.left <= x < r.right and r.top <= y < r.bottom:
                    area = (r.right - r.left) * (r.bottom - r.top)
                    if best_area is None or area < best_area:
                        best, best_area = child, area
            if best is None:
                break
            ctrl = best
        return ctrl

    def _screenshot(self, x: int, y: int) -> tuple[str | None, list[float] | None]:
        now = time.time()
        last_time, last_path, last_thumb = self.last_shot
        if now - last_time < self.cfg.min_screenshot_interval:
            return None, None
        monitors = self.sct.monitors[1:] or self.sct.monitors
        mon = next((m for m in monitors
                    if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]),
                   monitors[0])
        raw = self.sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        scale = min(1.0, self.cfg.screenshot_max_width / img.width)
        if scale < 1:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
        xy = [round((x - mon["left"]) * scale, 1), round((y - mon["top"]) * scale, 1)]
        thumb = img.convert("L").resize((48, 27))
        if last_thumb is not None and ImageStat.Stat(ImageChops.difference(thumb, last_thumb)).mean[0] < 1.0:
            self.last_shot = (now, last_path, last_thumb)  # screen unchanged: reuse the previous file
            return last_path, xy
        self.n_shots += 1
        rel = f"shots/{self.n_shots:06d}.jpg"
        img.save(self.dir / rel, "JPEG", quality=self.cfg.screenshot_quality)
        self.last_shot = (now, rel, thumb)
        return rel, xy

    def _is_excluded(self, exe: str, title: str) -> bool:
        lowered = (title or "").lower()
        return (exe or "").lower() in self.excluded_apps or any(w in lowered for w in self.excluded_words)

    def _emit(self, t: float, kind: str, window: tuple | None = None, **fields) -> None:
        _, exe, title = window or self.window or (None, "unknown", "")
        event = {"t": self._rel(t), "type": kind, "app": exe, "title": title}
        if self._is_excluded(exe, title):
            event.update(title="[excluded]", excluded=True)
            fields = {k: v for k, v in fields.items() if k not in ("text", "element", "focus", "shot", "shot_xy")}
        event.update(fields)
        self.fp.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.fp.flush()
        self.n_events += 1

    def _rel(self, t: float) -> float:
        return round(t - self.t0, 3)

    def _write_meta(self, started: datetime, ended: datetime | None) -> None:
        meta = {
            "session": self.dir.name,
            "started": started.isoformat(timespec="seconds"),
            "ended": ended.isoformat(timespec="seconds") if ended else None,
            "duration_seconds": round(time.time() - self.t0, 1) if ended else None,
            "events": self.n_events,
            "screenshots": self.n_shots,
            "privacy": {
                "capture_typed_text": self.cfg.capture_typed_text,
                "excluded_apps": self.cfg.excluded_apps,
                "excluded_title_keywords": self.cfg.excluded_title_keywords,
            },
        }
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
