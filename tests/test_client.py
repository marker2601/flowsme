"""FlowSME Recorder's own safety: where it may send keys, and how it keeps them."""
from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from flowsme.client import cloud
from flowsme.connect import _call, _local


@pytest.mark.parametrize("address", ["http://example.com", "ftp://x", "http://localhost.evil.com",
                                     "http://localhost@evil.com"])
def test_server_address_must_be_https(address):
    with pytest.raises(cloud.CloudError):
        cloud.check_address(address)


def test_local_server_address_allowed():
    assert cloud.check_address("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"


def test_tool_addresses():
    assert all(_local(h) for h in ("localhost", "192.168.1.5", "10.0.0.2", "nas.local"))
    assert not any(_local(h) for h in ("n8n.example.com", "8.8.8.8", None))
    with pytest.raises(SystemExit, match="https"):
        _call("n8n", "GET", "http://n8n.example.com/api/v1/workflows")


def test_redirects_never_carry_the_key():
    """A redirect would send the Authorization header on to wherever it points."""
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/api/v1/me"):
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(cloud.CloudError):
            cloud.Cloud(f"http://127.0.0.1:{server.server_port}", "fsk_secret").me()
    finally:
        server.shutdown()
    assert [p for p, _ in hits] == ["/api/v1/me"]


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_tool_keys_are_kept_encrypted(tmp_path, monkeypatch):
    from flowsme.client import account

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("FLOWSME_TEST_KEY", raising=False)
    account.store_setting("FLOWSME_TEST_KEY", "n8n-secret-123")
    stored = (tmp_path / "FlowSME Client" / "tools.dat").read_bytes()
    assert b"n8n-secret-123" not in stored
    assert account.saved_setting("FLOWSME_TEST_KEY") == "n8n-secret-123"
