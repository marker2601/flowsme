"""The FlowSME server's API, as the customer's app calls it."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path



class CloudError(Exception):
    pass



class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header to wherever it points: refuse it instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"Redirected to {newurl}; not followed", headers, fp)


_open = urllib.request.build_opener(_NoRedirect).open


def check_address(server: str) -> str:
    """Only https, so seat keys and recordings are encrypted on the way; plain http only to this computer."""
    server = server.strip().rstrip("/")
    parts = urllib.parse.urlsplit(server)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")):
        return server
    raise CloudError("The server address must start with https://")


class Cloud:
    def __init__(self, server: str, key: str):
        self.server, self.key = check_address(server), key

    def _send(self, method: str, path: str, data=None, headers: dict | None = None, timeout: int = 120) -> bytes:
        request = urllib.request.Request(f"{self.server}/api/v1{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", "User-Agent": "FlowSME-Client", **(headers or {})})
        try:
            with _open(request, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise CloudError(detail if isinstance(detail, str) else f"The server said {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CloudError(f"Can't reach the FlowSME server ({getattr(exc, 'reason', exc)}).") from None

    def _json(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        raw = self._send(method, path, data, {"Content-Type": "application/json"} if data else None)
        return json.loads(raw) if raw else {}

    def me(self) -> dict:
        return self._json("GET", "/me")

    def recordings(self) -> list[dict]:
        return self._json("GET", "/recordings")

    def recording(self, rec_id: str) -> dict:
        return self._json("GET", f"/recordings/{rec_id}")

    def report(self, rec_id: str) -> str:
        return self._send("GET", f"/recordings/{rec_id}/report").decode("utf-8")

    def sop(self, rec_id: str) -> str:
        return self._send("GET", f"/recordings/{rec_id}/sop").decode("utf-8")

    def build(self, rec_id: str, task: int) -> bytes:
        return self._send("POST", f"/recordings/{rec_id}/build", json.dumps({"task": task}).encode(),
                          {"Content-Type": "application/json"}, timeout=300)

    def reanalyse(self, rec_id: str, options: dict) -> dict:
        return self._json("POST", f"/recordings/{rec_id}/reanalyse", options)

    def delete(self, rec_id: str) -> None:
        self._json("DELETE", f"/recordings/{rec_id}")

    def upload(self, session_dir: Path, options: dict) -> dict:
        """Zip the recording and send it."""
        work = Path(tempfile.mkdtemp(prefix="flowsme-upload-"))
        try:
            archive = Path(shutil.make_archive(str(work / session_dir.name), "zip", root_dir=session_dir.parent,
                                               base_dir=session_dir.name))
            return self._post_file("/recordings", archive, "recording.zip", "application/zip", options, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def upload_video(self, video: Path, options: dict) -> dict:
        """A screen video from Teams, Loom, Snipping Tool...: the server's AI model reads the steps from it."""
        work = Path(tempfile.mkdtemp(prefix="flowsme-upload-"))
        try:
            return self._post_file("/videos", Path(video), f"video{Path(video).suffix.lower()}",
                                   "application/octet-stream", options, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _post_file(self, path: str, file: Path, name: str, kind: str, options: dict, work: Path) -> dict:
        """A multipart form, written to a temporary file first (recordings and videos can be big)."""
        boundary = "flowsme" + secrets.token_hex(12)
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"options\"\r\n\r\n"
                f"{json.dumps(options)}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{name}\"\r\nContent-Type: {kind}\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = work / "body.bin"
        with open(body, "wb") as out, open(file, "rb") as src:
            out.write(head)
            shutil.copyfileobj(src, out)
            out.write(tail)
        with open(body, "rb") as data:
            raw = self._send("POST", path, data, {"Content-Type": f"multipart/form-data; boundary={boundary}",
                                                  "Content-Length": str(os.path.getsize(body))}, timeout=1800)
        return json.loads(raw)

    # the manager's

    def admin(self) -> dict:
        return self._json("GET", "/admin")

    def add_member(self, name: str, position: str, email: str = "") -> dict:
        return self._json("POST", "/admin/members", {"name": name, "position": position, "email": email})

    def change_member(self, seat: str, action: str, position: str | None = None) -> dict:
        return self._json("POST", f"/admin/members/{seat}", {"action": action, "position": position})

    def positions(self, positions: list[dict], currency: str, default: str) -> dict:
        return self._json("PUT", "/admin/positions", {"positions": positions, "currency": currency,
                                                      "default_position": default})

    def compare(self, before: str, after: str, before_items: int, after_items: int) -> str:
        return self._send("POST", "/compare", json.dumps({"before": before, "after": after, "before_items": before_items,
                                                         "after_items": after_items}).encode(),
                          {"Content-Type": "application/json"}, timeout=300).decode("utf-8")

    def team(self, recordings: list[str], items: list[int]) -> str:
        return self._send("POST", "/team", json.dumps({"recordings": recordings, "items": items}).encode(),
                          {"Content-Type": "application/json"}, timeout=300).decode("utf-8")
