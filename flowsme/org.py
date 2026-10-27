"""Positions, hourly rates and seats: who may record for a team, and what an hour of each position costs.

Three ways to use the app, decided by the files in the app's folder:
  solo      neither file: one person, who sets the positions and rates themselves
  manager   team.json with a "team" section: positions and rates, the seats handed out, an admin PIN
  employee  seat.json: joined a team with a seat file from the manager

team.json holds the rates and the team's secret and never leaves the manager's computer. A seat file (.flowseat)
holds the person's name and position title, never a rate. It is signed with the team secret, so the manager's
app can tell its own seats from made-up or revoked ones when recordings come back.

The optional team folder (a OneDrive, SharePoint or network folder both can reach) is the shared "back end":
the manager publishes seats.json there (seat codes and whether each is active, no rates) and employees drop
recordings into its inbox folder."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import secrets
import zipfile
from datetime import datetime
from pathlib import Path

TEAM_FILE, SEAT_FILE = "team.json", "seat.json"
SEAT_KIND = "flowsme-seat"
PIN_ROUNDS = 200_000
# Starting points only: the manager replaces them with the team's real positions and rates.
DEFAULT_POSITIONS = [{"name": "Clerk / administrator", "hourly_cost": 25.0},
                     {"name": "Specialist / analyst", "hourly_cost": 40.0},
                     {"name": "Team lead / supervisor", "hourly_cost": 55.0},
                     {"name": "Manager", "hourly_cost": 70.0}]
_CODE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread
_SIGNED = ("team_id", "team_name", "seat", "name", "position", "issued")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read(path: Path) -> dict | None:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def mode(home: Path) -> str:
    if (home / SEAT_FILE).exists():
        return "employee"
    team = _read(home / TEAM_FILE)
    return "manager" if team and team.get("team") else "solo"


# ---- the manager's (or solo user's) side ------------------------------------------------------------

class Org:
    """team.json: positions and rates for everyone; a team, its seats and the admin PIN for a manager."""

    def __init__(self, home: Path):
        self.path = home / TEAM_FILE
        self.data = _read(self.path) or {"positions": [dict(p) for p in DEFAULT_POSITIONS], "currency": "$",
                                         "default_position": DEFAULT_POSITIONS[0]["name"]}

    def save(self) -> None:
        _write(self.path, self.data)
        self.publish()

    # positions and rates

    @property
    def currency(self) -> str:
        return self.data.get("currency") or "$"

    @property
    def positions(self) -> list[dict]:
        return self.data.setdefault("positions", [])

    def position_names(self) -> list[str]:
        return [p["name"] for p in self.positions]

    def rate(self, position: str) -> float | None:
        return next((float(p["hourly_cost"]) for p in self.positions if p["name"] == position), None)

    @property
    def default_position(self) -> str:
        names = self.position_names()
        chosen = self.data.get("default_position")
        return chosen if chosen in names else (names[0] if names else "")

    def set_position(self, name: str, hourly_cost: float, old: str | None = None) -> None:
        name = name.strip()
        if not name or hourly_cost < 0:
            raise ValueError("A position needs a name and a cost of 0 or more.")
        if name != old and name in self.position_names():
            raise ValueError(f"There is already a position called '{name}'.")
        if old is None:
            self.positions.append({"name": name, "hourly_cost": float(hourly_cost)})
        else:
            entry = next(p for p in self.positions if p["name"] == old)
            entry.update(name=name, hourly_cost=float(hourly_cost))
            for m in self.members:  # people keep their position when it is renamed
                if m["position"] == old:
                    m["position"] = name
            if self.data.get("default_position") == old:
                self.data["default_position"] = name

    def remove_position(self, name: str) -> None:
        users = [m["name"] for m in self.members if m["position"] == name and m["status"] == "active"]
        if users:
            raise ValueError(f"'{name}' is still the position of {', '.join(users)}. Move them first.")
        if len(self.positions) == 1:
            raise ValueError("Keep at least one position.")
        self.data["positions"] = [p for p in self.positions if p["name"] != name]

    # the team

    @property
    def team(self) -> dict | None:
        return self.data.get("team")

    def create_team(self, name: str, pin: str, seats: int, folder: str = "") -> None:
        if not name.strip():
            raise ValueError("Give the team a name.")
        if len(pin) < 4 or not pin.isdigit():
            raise ValueError("The admin PIN needs at least 4 digits.")
        if seats < 1:
            raise ValueError("The plan needs at least one seat.")
        self.data["team"] = {"id": "T-" + secrets.token_hex(6).upper(), "name": name.strip(),
                             "secret": secrets.token_hex(32), "created": _now(), "seats": int(seats),
                             "folder": folder.strip(), "employees_make_reports": True}
        self.data.setdefault("members", [])
        self.set_pin(pin)

    def set_pin(self, pin: str) -> None:
        salt = secrets.token_hex(16)
        self.data["pin"] = {"salt": salt, "rounds": PIN_ROUNDS,
                            "hash": hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt),
                                                        PIN_ROUNDS).hex()}

    def check_pin(self, pin: str) -> bool:
        p = self.data.get("pin")
        if not p:
            return True
        got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(p["salt"]), int(p["rounds"])).hex()
        return hmac.compare_digest(got, p["hash"])

    # seats

    @property
    def members(self) -> list[dict]:
        return self.data.setdefault("members", [])

    def active(self) -> list[dict]:
        return [m for m in self.members if m["status"] == "active"]

    def member(self, seat: str) -> dict | None:
        return next((m for m in self.members if m["seat"] == seat), None)

    def _new_code(self) -> str:
        used = {m["seat"] for m in self.members}
        while True:
            code = "FS-" + "".join(secrets.choice(_CODE) for _ in range(4)) + "-" + \
                   "".join(secrets.choice(_CODE) for _ in range(4))
            if code not in used:
                return code

    def add_member(self, name: str, position: str, email: str = "") -> dict:
        team = self.team
        if not team:
            raise ValueError("Set up the team first.")
        if not name.strip():
            raise ValueError("Enter the person's name.")
        if position not in self.position_names():
            raise ValueError("Choose one of the positions.")
        if len(self.active()) >= team["seats"]:
            raise ValueError(f"All {team['seats']} seats are in use. Revoke a seat or add seats to the plan.")
        member = {"seat": self._new_code(), "name": name.strip(), "email": email.strip(), "position": position,
                  "status": "active", "issued": _now(), "revoked": None}
        self.members.append(member)
        return member

    def revoke(self, seat: str) -> None:
        m = self.member(seat)
        if m and m["status"] == "active":
            m.update(status="revoked", revoked=_now())

    def restore(self, seat: str) -> None:
        m = self.member(seat)
        if not m or m["status"] == "active":
            return
        if len(self.active()) >= self.team["seats"]:
            raise ValueError(f"All {self.team['seats']} seats are in use. Revoke another seat first.")
        m.update(status="active", revoked=None)

    def _sign(self, fields: dict) -> str:
        message = "|".join(str(fields.get(k, "")) for k in _SIGNED).encode()
        return hmac.new(bytes.fromhex(self.team["secret"]), message, hashlib.sha256).hexdigest()

    def seat_file(self, member: dict) -> dict:
        """What the employee's app keeps: who they are and which team, no rates, signed."""
        team = self.team
        seat = {"kind": SEAT_KIND, "version": 1, "team_id": team["id"], "team_name": team["name"],
                "seat": member["seat"], "name": member["name"], "position": member["position"],
                "issued": member["issued"], "folder": team.get("folder", ""),
                "employees_make_reports": team.get("employees_make_reports", True)}
        if member.get("email"):
            seat["email"] = member["email"]
        seat["signature"] = self._sign(seat)
        return seat

    def check_seat(self, seat: dict | None) -> tuple[str, dict | None]:
        """("ok" | "none" | "other team" | "forged" | "revoked" | "unknown", the member) for a recording's seat."""
        if not seat:
            return "none", None
        if not self.team or seat.get("team_id") != self.team["id"]:
            return "other team", None
        if not hmac.compare_digest(self._sign(seat), str(seat.get("signature", ""))):
            return "forged", None
        member = self.member(seat["seat"])
        if member is None:
            return "unknown", None
        return ("ok" if member["status"] == "active" else "revoked"), member

    # the shared team folder

    @property
    def folder(self) -> Path | None:
        folder = (self.team or {}).get("folder")
        return Path(folder) if folder else None

    def publish(self) -> bool:
        """Seat codes and whether each is active (no names, rates or secret) for employees' apps to check."""
        folder = self.folder
        if not folder or not folder.exists():
            return False
        with contextlib.suppress(OSError):
            (folder / "inbox").mkdir(exist_ok=True)
            _write(folder / "seats.json", {"team_id": self.team["id"], "team_name": self.team["name"],
                                           "updated": _now(),
                                           "employees_make_reports": self.team.get("employees_make_reports", True),
                                           "seats": {m["seat"]: m["status"] for m in self.members}})
            return True
        return False

    def inbox(self) -> list[Path]:
        folder = self.folder
        if not folder or not (folder / "inbox").exists():
            return []
        return sorted((folder / "inbox").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)


# ---- the employee's side ----------------------------------------------------------------------------

def read_seat_file(path: Path) -> dict:
    seat = _read(path)
    if not seat or seat.get("kind") != SEAT_KIND or not all(seat.get(k) for k in (*_SIGNED, "signature")):
        raise ValueError("This isn't a FlowSME seat file. Ask your manager to send it again.")
    return seat


def join(home: Path, seat: dict, folder: str = "") -> None:
    if (home / TEAM_FILE).exists() and (_read(home / TEAM_FILE) or {}).get("team"):
        raise ValueError("This app manages a team. A manager can't also join a team on the same computer.")
    seat = dict(seat)
    if folder:
        seat["folder"] = folder
    _write(home / SEAT_FILE, seat)


def leave(home: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        (home / SEAT_FILE).unlink()


def my_seat(home: Path) -> dict | None:
    return _read(home / SEAT_FILE)


def published(seat: dict) -> dict | None:
    """What the manager last published to the team folder, or None if it can't be reached (then the app carries
    on with the seat file's settings: the manager still checks every recording that comes back)."""
    folder = seat.get("folder")
    data = _read(Path(folder) / "seats.json") if folder else None
    return data if data and data.get("team_id") == seat.get("team_id") else None


def seat_status(seat: dict) -> str | None:
    """"active", "revoked" or "unknown" as last published, or None if the team folder can't be reached."""
    data = published(seat)
    return data.get("seats", {}).get(seat["seat"], "unknown") if data else None


def can_make_reports(seat: dict) -> bool:
    data = published(seat)
    return bool((data or seat).get("employees_make_reports", True))


def stamp(session_dir: Path, seat: dict) -> None:
    """Mark a recording as made on this seat, so the manager's app knows whose it is."""
    _write(session_dir / SEAT_FILE, {k: v for k, v in seat.items() if k != "folder"})


def recording_seat(session_dir: Path) -> dict | None:
    return _read(session_dir / SEAT_FILE)


def seat_in_zip(zip_path: Path) -> dict | None:
    """The seat a zipped recording was made on, read without unpacking it."""
    with contextlib.suppress(OSError, ValueError, KeyError, zipfile.BadZipFile):
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted((n for n in archive.namelist() if n.endswith("/" + SEAT_FILE) or n == SEAT_FILE),
                           key=len)
            if names:
                data = json.loads(archive.read(names[0]).decode("utf-8"))
                return data if isinstance(data, dict) else None
    return None
