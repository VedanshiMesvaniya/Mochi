import http.server
import json
import threading

import pytest

from app.ai import humor
from app.ai.humor import _FALLBACK_JOKES, fetch_joke, get_joke


def test_fetch_joke_returns_none_when_unreachable(monkeypatch):
    """No real joke API reachable in the test environment - must fail
    fast and quietly (return None), never raise."""
    monkeypatch.setattr(humor, "JOKE_API_URL", "http://localhost:1/nope")
    assert fetch_joke() is None


def test_get_joke_always_returns_something(monkeypatch):
    """Whether or not the network call succeeds, get_joke() must always
    return usable text - humor is a nice-to-have, never something that can
    leave a caller with nothing."""
    monkeypatch.setattr(humor.settings, "humor_enabled", False)
    joke = get_joke()
    assert joke
    assert joke in _FALLBACK_JOKES


@pytest.fixture()
def fake_joke_server(monkeypatch):
    """A tiny local HTTP server standing in for icanhazdadjoke.com, so the
    happy path is tested without a real network dependency."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({"joke": "Why did the cat sit on the keyboard? To keep an eye on the mouse."}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence test output
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)
    port = server.server_address[1]
    monkeypatch.setattr(humor, "JOKE_API_URL", f"http://localhost:{port}/")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fetch_joke_returns_real_text_on_success(fake_joke_server):
    joke = fetch_joke()
    assert joke == "Why did the cat sit on the keyboard? To keep an eye on the mouse."


def test_get_joke_prefers_network_when_enabled_and_reachable(fake_joke_server, monkeypatch):
    monkeypatch.setattr(humor.settings, "humor_enabled", True)
    assert get_joke() == "Why did the cat sit on the keyboard? To keep an eye on the mouse."


def test_get_joke_falls_back_when_enabled_but_unreachable(monkeypatch):
    monkeypatch.setattr(humor.settings, "humor_enabled", True)
    monkeypatch.setattr(humor, "JOKE_API_URL", "http://localhost:1/nope")
    assert get_joke() in _FALLBACK_JOKES
