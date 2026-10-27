"""Who is signed in: the server address and the seat key, kept encrypted for this Windows user (DPAPI), and the
settings the Build dialog remembers for the customer's workflow tools."""
from __future__ import annotations

import base64
import contextlib
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

SEAT_KIND = "flowsme-cloud-seat"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Encrypt (or decrypt) with the Windows user's own key: another user, or the file copied to another
    computer, can't read it."""
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    crypt = ctypes.windll.crypt32
    ok = (crypt.CryptProtectData(ctypes.byref(blob_in), "FlowSME", None, None, None, 0x1, ctypes.byref(blob_out))
          if protect else
          crypt.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)))
    if not ok:
        raise OSError("Windows couldn't encrypt or decrypt the seat key.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def read_seat_file(path: Path) -> dict:
    with contextlib.suppress(OSError, ValueError):
        seat = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(seat, dict) and seat.get("kind") == SEAT_KIND and seat.get("server") and seat.get("key"):
            return seat
    raise ValueError("This isn't a FlowSME seat file. Ask your manager (or FlowSME) to send it again.")


def save(home: Path, server: str, key: str, me: dict) -> None:
    data = {"server": server.rstrip("/"), "key": base64.b64encode(_dpapi(key.encode(), True)).decode(),
            "name": me.get("name"), "team": me.get("team"), "role": me.get("role")}
    home.mkdir(parents=True, exist_ok=True)
    (home / "account.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def load(home: Path) -> tuple[str, str] | None:
    """(server, key), or None when nobody is signed in on this computer."""
    with contextlib.suppress(OSError, ValueError, KeyError):
        data = json.loads((home / "account.json").read_text(encoding="utf-8"))
        return data["server"], _dpapi(base64.b64decode(data["key"]), False).decode()
    return None


def forget(home: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        (home / "account.json").unlink()


# ---- remembered tool settings (user environment variables, like the full app) -----------------------

def saved_setting(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    with contextlib.suppress(OSError):
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0] or "")
    return ""


def store_setting(name: str, value: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    os.environ[name] = value
