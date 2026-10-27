"""The server's records: customer organisations, their people and seat keys, positions and rates, recordings.

Plain JSON files in the data folder, one folder per organisation, so one customer's data never sits beside
another's. Seat keys are stored only as SHA-256 hashes: the key itself is shown once, when it is made."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from ..org import DEFAULT_POSITIONS

LOCK = threading.RLock()
MAX_UNPACKED = 4 * 1024 ** 3  # a recording zip may unpack to at most 4 GB...
MAX_FILES = 20_000           # ...in at most this many files (screenshots), so a zip bomb can't fill the disk
_CODE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "fsk_" + secrets.token_urlsafe(32)


def _retry(action):
    """Windows refuses to replace a file while another request is reading it: wait a moment and try again."""
    for attempt in range(40):
        try:
            return action()
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def _read(path: Path) -> dict:
    return _retry(lambda: json.loads(path.read_text(encoding="utf-8")))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    _retry(lambda: tmp.replace(path))


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "orgs").mkdir(parents=True, exist_ok=True)
        self._keys: dict[str, tuple[str, str]] = {}  # key hash -> (org id, seat)
        self._index()

    def _index(self) -> None:
        with LOCK:
            self._keys = {m["key_hash"]: (org["id"], m["seat"])
                          for org in self.orgs() for m in org["members"] if m.get("key_hash")}
            self._indexed = time.monotonic()

    # ---- organisations ------------------------------------------------------------------------------

    def _org_path(self, org_id: str) -> Path:
        if not re.fullmatch(r"O-[A-F0-9]{12}", org_id):
            raise KeyError(org_id)
        return self.root / "orgs" / org_id / "org.json"

    def orgs(self) -> list[dict]:
        return [_read(p) for p in sorted((self.root / "orgs").glob("O-*/org.json"))]

    def org(self, org_id: str) -> dict:
        return _read(self._org_path(org_id))

    def save_org(self, org: dict) -> None:
        with LOCK:
            _write(self._org_path(org["id"]), org)
            self._index()

    def create_org(self, name: str, seats: int, currency: str = "$") -> dict:
        if not name.strip() or seats < 1:
            raise ValueError("An organisation needs a name and at least one seat.")
        org = {"id": "O-" + secrets.token_hex(6).upper(), "name": name.strip(), "seats": int(seats),
               "currency": currency, "created": now(), "positions": [dict(p) for p in DEFAULT_POSITIONS],
               "default_position": DEFAULT_POSITIONS[0]["name"], "members": []}
        self.save_org(org)
        return org

    def by_name(self, name_or_id: str) -> dict:
        for org in self.orgs():
            if name_or_id in (org["id"], org["name"]):
                return org
        raise KeyError(f"No organisation called {name_or_id}")

    # ---- people and seat keys -----------------------------------------------------------------------

    @staticmethod
    def employees(org: dict) -> list[dict]:
        return [m for m in org["members"] if m["status"] == "active" and m["role"] == "employee"]

    @staticmethod
    def member(org: dict, seat: str) -> dict:
        found = next((m for m in org["members"] if m["seat"] == seat), None)
        if found is None:
            raise KeyError(seat)
        return found

    def add_member(self, org: dict, name: str, position: str, email: str = "", role: str = "employee") -> tuple[dict, str]:
        """A new person and their seat key. Managers don't take one of the plan's seats."""
        if not name.strip():
            raise ValueError("Enter the person's name.")
        if position not in [p["name"] for p in org["positions"]]:
            raise ValueError("Choose one of the positions.")
        if role == "employee" and len(self.employees(org)) >= org["seats"]:
            raise ValueError(f"All {org['seats']} seats are in use. Revoke one, or ask FlowSME for more seats.")
        used = {m["seat"] for m in org["members"]}
        seat = None
        while seat is None or seat in used:
            seat = "FS-" + "".join(secrets.choice(_CODE) for _ in range(4)) + "-" + \
                   "".join(secrets.choice(_CODE) for _ in range(4))
        key = new_key()
        member = {"seat": seat, "name": name.strip(), "email": email.strip(), "position": position, "role": role,
                  "status": "active", "issued": now(), "revoked": None, "key_hash": key_hash(key)}
        org["members"].append(member)
        self.save_org(org)
        return member, key

    def new_key(self, org: dict, seat: str) -> str:
        member = self.member(org, seat)
        key = new_key()
        member["key_hash"] = key_hash(key)  # the old key stops working at once
        self.save_org(org)
        return key

    def set_status(self, org: dict, seat: str, active: bool) -> None:
        member = self.member(org, seat)
        if active and member["status"] != "active":
            if member["role"] == "employee" and len(self.employees(org)) >= org["seats"]:
                raise ValueError(f"All {org['seats']} seats are in use. Revoke another seat first.")
            member.update(status="active", revoked=None)
        elif not active and member["status"] == "active":
            member.update(status="revoked", revoked=now())
        self.save_org(org)

    def who(self, key: str) -> tuple[dict, dict] | None:
        """The organisation and person a seat key belongs to, or None."""
        found = self._keys.get(key_hash(key))
        if not found and time.monotonic() - self._indexed > 2:  # made by the admin command since we last looked
            self._index()
            found = self._keys.get(key_hash(key))
        if not found:
            return None
        org = self.org(found[0])
        return org, self.member(org, found[1])

    # ---- positions and rates ------------------------------------------------------------------------

    @staticmethod
    def rate(org: dict, position: str) -> float | None:
        return next((float(p["hourly_cost"]) for p in org["positions"] if p["name"] == position), None)

    def set_positions(self, org: dict, positions: list[dict], currency: str, default: str) -> None:
        """Replace the positions. An entry with "was" renames that position, and its people keep it."""
        names = [str(p.get("name", "")).strip() for p in positions]
        renames = {str(p["was"]): n for p, n in zip(positions, names) if p.get("was") and p["was"] != n}
        if not positions or any(not n for n in names) or len(set(names)) != len(names):
            raise ValueError("Each position needs a different name, and keep at least one.")
        try:
            rates = [float(p["hourly_cost"]) for p in positions]
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each position needs a cost of an hour.") from None
        if any(r < 0 for r in rates):
            raise ValueError("A cost of an hour can't be negative.")
        in_use = {m["position"] for m in org["members"] if m["status"] == "active"} - set(names) - set(renames)
        if in_use:
            raise ValueError(f"Still in use: {', '.join(sorted(in_use))}. Move those people first.")
        for m in org["members"]:
            m["position"] = renames.get(m["position"], m["position"])
        default = renames.get(default, default)
        org["positions"] = [{"name": n, "hourly_cost": r} for n, r in zip(names, rates)]
        org["currency"] = (currency or "$").strip()[:5] or "$"
        org["default_position"] = default if default in names else names[0]
        self.save_org(org)

    # ---- recordings ---------------------------------------------------------------------------------

    def recordings_dir(self, org_id: str) -> Path:
        return self._org_path(org_id).parent / "recordings"

    def _record_path(self, org_id: str, rec_id: str) -> Path:
        if not re.fullmatch(r"R-[a-f0-9]{16}", rec_id):
            raise KeyError(rec_id)
        return self.recordings_dir(org_id) / rec_id / "record.json"

    def record(self, org_id: str, rec_id: str) -> dict:
        path = self._record_path(org_id, rec_id)
        if not path.exists():
            raise KeyError(rec_id)
        return _read(path)

    def save_record(self, record: dict) -> None:
        with LOCK:
            _write(self._record_path(record["org"], record["id"]), record)

    def records(self, org_id: str) -> list[dict]:
        folder = self.recordings_dir(org_id)
        found = [_read(p) for p in folder.glob("R-*/record.json")] if folder.exists() else []
        return sorted(found, key=lambda r: r["uploaded"], reverse=True)

    def session_dir(self, record: dict) -> Path:
        return self.recordings_dir(record["org"]) / record["id"] / record["session"]

    def add_recording(self, org: dict, member: dict, zip_path: Path, options: dict) -> dict:
        """Unpack an uploaded recording into the organisation's folder, checking it can't escape or explode."""
        from ..session import import_recording, load_meta

        try:
            with zipfile.ZipFile(zip_path) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
                    raise ValueError("This recording is too large.")
        except zipfile.BadZipFile:
            raise ValueError("The upload isn't a zip file.") from None
        rec_id = "R-" + secrets.token_hex(8)
        folder = self.recordings_dir(org["id"]) / rec_id
        folder.mkdir(parents=True)
        try:
            session = import_recording(zip_path, folder)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        for stale in ("analysis",):  # results come only from this server's own analysis
            shutil.rmtree(session / stale, ignore_errors=True)
        with contextlib.suppress(FileNotFoundError):
            (session / "seat.json").unlink()
        meta = load_meta(session)
        record = {"id": rec_id, "org": org["id"], "seat": member["seat"], "session": session.name,
                  "title": str(options.get("title") or meta.get("name") or session.name)[:120],
                  "started": meta.get("started"), "seconds": meta.get("duration_seconds"),
                  "uploaded": now(), "status": "queued", "error": None, "options": options, "log": []}
        self.save_record(record)
        return record

    def add_video(self, org: dict, member: dict, video: Path, filename: str, options: dict) -> dict:
        """Keep an uploaded screen video until the analysis job has turned it into a recording."""
        from ..video import VIDEO_TYPES

        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_TYPES:
            raise ValueError(f"FlowSME reads {', '.join(VIDEO_TYPES)} videos.")
        rec_id = "R-" + secrets.token_hex(8)
        folder = self.recordings_dir(org["id"]) / rec_id
        (folder / "source").mkdir(parents=True)
        shutil.move(str(video), folder / "source" / f"video{suffix}")
        title = str(options.get("title") or Path(filename).stem)[:120]
        record = {"id": rec_id, "org": org["id"], "seat": member["seat"], "session": None, "title": title,
                  "video": f"video{suffix}", "started": now(), "seconds": None, "uploaded": now(),
                  "status": "queued", "error": None, "options": options, "log": []}
        self.save_record(record)
        return record

    def delete_recording(self, record: dict) -> None:
        shutil.rmtree(self.recordings_dir(record["org"]) / record["id"], ignore_errors=True)
