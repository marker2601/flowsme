"""The server's records: customer organisations, their people and seat keys, positions and rates, recordings.

Plain JSON files in the data folder, one folder per organisation, so one customer's data never sits beside
another's. Seat keys are stored only as SHA-256 hashes: the key itself is shown once, when it is made."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import sys
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
DAILY_REPORTS = 40           # analyses a day per organisation, unless its org.json sets "daily_reports"
REPORT_RESERVE_USD = 5.0     # the most one report may cost: a screen video has two $2 caps (reading it, then the
                             # analysis), plus calls already under way when a cap is reached
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
    with open(tmp, "w", encoding="utf-8") as out:
        out.write(json.dumps(data, indent=1, ensure_ascii=False))
        out.flush()
        os.fsync(out.fileno())  # on disk before it replaces the old file: a power cut leaves one or the other
    _retry(lambda: tmp.replace(path))


def _readable(paths) -> list[dict]:
    """Every file that reads as JSON; a damaged one is reported and skipped, so it can't stop the server."""
    found = []
    for path in paths:
        try:
            data = _read(path)
        except (OSError, ValueError) as exc:
            print(f"FlowSME: skipping unreadable {path}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            found.append(data)
    return found


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
        return _readable(sorted((self.root / "orgs").glob("O-*/org.json")))

    def org(self, org_id: str) -> dict:
        return _read(self._org_path(org_id))

    def save_org(self, org: dict) -> None:
        with LOCK:
            _write(self._org_path(org["id"]), org)
            self._index()

    @contextlib.contextmanager
    def _editing(self, org: dict):
        """The organisation as it is on disk now, saved afterwards; the caller's copy is updated to match. Two
        admin requests at once can't overwrite each other's change."""
        with LOCK:
            fresh = self.org(org["id"])
            yield fresh
            self.save_org(fresh)
            org.clear()
            org.update(fresh)

    def create_org(self, name: str, seats: int, currency: str = "$") -> dict:
        if not name.strip() or seats < 1:
            raise ValueError("An organisation needs a name and at least one seat.")
        if any(o["name"].lower() == name.strip().lower() for o in self.orgs()):
            raise ValueError(f"There is already an organisation called {name.strip()}.")
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
        with self._editing(org) as org:
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
            member = {"seat": seat, "name": name.strip()[:80], "email": email.strip()[:120], "position": position,
                      "role": role, "status": "active", "issued": now(), "revoked": None, "key_hash": key_hash(key)}
            org["members"].append(member)
        return member, key

    def new_key(self, org: dict, seat: str) -> str:
        with self._editing(org) as org:
            member = self.member(org, seat)
            key = new_key()
            member["key_hash"] = key_hash(key)  # the old key stops working at once
        return key

    def set_status(self, org: dict, seat: str, active: bool) -> None:
        with self._editing(org) as org:
            member = self.member(org, seat)
            if active and member["status"] != "active":
                if member["role"] == "employee" and len(self.employees(org)) >= org["seats"]:
                    raise ValueError(f"All {org['seats']} seats are in use. Revoke another seat first.")
                member.update(status="active", revoked=None)
            elif not active and member["status"] == "active":
                member.update(status="revoked", revoked=now())

    def set_member_position(self, org: dict, seat: str, position: str) -> None:
        with self._editing(org) as org:
            if position not in [p["name"] for p in org["positions"]]:
                raise ValueError("Choose one of the positions.")
            self.member(org, seat)["position"] = position

    def set_seats(self, org: dict, seats: int) -> None:
        with self._editing(org) as org:
            if seats < 1:
                raise ValueError("A plan needs at least one seat.")
            if seats < len(self.employees(org)):
                raise ValueError(f"{len(self.employees(org))} seats are in use: revoke some first.")
            org["seats"] = int(seats)

    def use_report(self, org: dict, refund: bool = False) -> None:
        """Count one analysis against the organisation's daily allowance (each costs AI money)."""
        with self._editing(org) as org:
            today = now()[:10]
            usage = org.get("usage") if (org.get("usage") or {}).get("day") == today else {"day": today, "reports": 0}
            if refund:
                usage["reports"] = max(0, usage["reports"] - 1)
                org["usage"] = usage
                return
            limit = int(org.get("daily_reports", DAILY_REPORTS))
            if usage["reports"] >= limit:
                raise ValueError(f"Your team has used today's {limit} reports. More tomorrow, or ask FlowSME "
                                 "for a bigger allowance.")
            usage["reports"] += 1
            org["usage"] = usage

    def set_budget(self, org: dict, usd: float | None) -> None:
        """A total AI budget for the organisation in dollars, or None for no budget."""
        with self._editing(org) as org:
            if usd is None:
                org.pop("budget_usd", None)
            elif not math.isfinite(usd) or usd < 0:
                raise ValueError("A budget is a number of dollars, 0 or more.")
            else:
                org["budget_usd"] = float(usd)

    def spend(self, org_id: str, usd: float) -> None:
        """Add what an analysis cost to the organisation's AI spending."""
        if usd > 0:
            with self._editing(self.org(org_id)) as org:
                org["spent_usd"] = round(float(org.get("spent_usd", 0)) + usd, 4)

    @staticmethod
    def check_budget(org: dict, in_flight: int) -> None:
        """Refuse a new report unless the organisation's AI budget, if it has one, covers it and the reports
        already being made, each counted at the most it may cost."""
        budget = org.get("budget_usd")
        if budget is None:
            return
        spent = float(org.get("spent_usd", 0))
        if spent + REPORT_RESERVE_USD > budget:
            raise ValueError("This team's AI budget is used up, so no new reports can be made.")
        if spent + (in_flight + 1) * REPORT_RESERVE_USD > budget:
            raise ValueError("The reports being made now use the rest of this team's AI budget. "
                             "Try again when they're ready.")

    def who(self, key: str) -> tuple[dict, dict] | None:
        """The organisation and person a seat key belongs to, or None."""
        found = self._keys.get(key_hash(key))
        if not found and time.monotonic() - self._indexed > 2:  # made by the admin command since we last looked
            self._index()
            found = self._keys.get(key_hash(key))
        if not found:
            return None
        try:
            org = self.org(found[0])
            member = self.member(org, found[1])
        except (KeyError, OSError, ValueError):
            return None
        if member.get("key_hash") != key_hash(key):  # replaced since this process last indexed
            self._index()
            return None
        return org, member

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
        if any(not math.isfinite(r) or r < 0 or r > 100_000 for r in rates):
            raise ValueError("A cost of an hour must be a number from 0 to 100,000.")
        with self._editing(org) as org:
            in_use = {m["position"] for m in org["members"] if m["status"] == "active"} - set(names) - set(renames)
            if in_use:
                raise ValueError(f"Still in use: {', '.join(sorted(in_use))}. Move those people first.")
            for m in org["members"]:
                m["position"] = renames.get(m["position"], m["position"])
            default = renames.get(default, default)
            org["positions"] = [{"name": n[:60], "hourly_cost": r} for n, r in zip(names, rates)]
            org["currency"] = (currency or "$").strip()[:5] or "$"
            org["default_position"] = default if default in names else names[0]
        if renames:  # recordings keep their position by name: rename it there too, so re-runs keep the rate
            for record in self.records(org["id"]):
                if record.get("position") in renames:
                    record["position"] = renames[record["position"]]
                    self.save_record(record)

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
        found = _readable(folder.glob("R-*/record.json")) if folder.exists() else []
        return sorted(found, key=lambda r: str(r.get("uploaded")), reverse=True)

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
            for stale in ("analysis",):  # results come only from this server's own analysis
                shutil.rmtree(session / stale, ignore_errors=True)
            with contextlib.suppress(FileNotFoundError):
                (session / "seat.json").unlink()
            meta = load_meta(session)
            if not isinstance(meta, dict):
                raise ValueError("meta.json is not a recording's details.")
        except (ValueError, OSError, zipfile.BadZipFile, KeyError) as exc:
            shutil.rmtree(folder, ignore_errors=True)
            detail = str(exc) if isinstance(exc, ValueError) and "zip" in str(exc).lower() else ""
            raise ValueError(detail or "This isn't a complete FlowSME recording. Record it again, or send the "
                             "file the Recorder made.") from None
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
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
        """Gone from the list at once (renamed away), then removed; refused if Windows is holding a file."""
        folder = self.recordings_dir(record["org"]) / record["id"]
        gone = folder.with_name(f"deleted-{record['id']}-{secrets.token_hex(3)}")
        try:
            _retry(lambda: folder.rename(gone))
        except OSError:
            raise ValueError("A file of this recording is in use on the server. Try again in a minute.") from None
        shutil.rmtree(gone, ignore_errors=True)
        if gone.exists():
            print(f"FlowSME: {gone} is still being removed (a file was in use)", file=sys.stderr)

    def sweep(self) -> None:
        """Finish deletions that a busy file interrupted."""
        for gone in (self.root / "orgs").glob("O-*/recordings/deleted-*"):
            shutil.rmtree(gone, ignore_errors=True)
