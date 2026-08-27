import http.server
import json
import threading

import pytest

from app.ai import llm
from app.ai.llm import LLMUnavailable, summarize_page_content


@pytest.fixture()
def fake_summarize_ollama(monkeypatch):
    """Same pattern as tests/test_llm.py's fake_ollama fixture, but the
    reply here is a plain-text summary (no JSON envelope) - matching what
    summarize_page_content() actually expects back, unlike ask()'s
    {"response": ..., "emotion": ...} schema."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            prompt = body.get("prompt", "")
            if "trigger_empty" in prompt.lower():
                reply = ""
            else:
                reply = "This page covers a subreddit full of relatable memes and jokes."
            payload = json.dumps({"response": reply, "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)
    port = server.server_address[1]
    monkeypatch.setattr(llm, "OLLAMA_GENERATE_URL", f"http://localhost:{port}/api/generate")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_summarize_page_content_returns_plain_text(fake_summarize_ollama):
    summary = summarize_page_content("some raw scraped page text about memes")
    assert summary == "This page covers a subreddit full of relatable memes and jokes."


def test_summarize_page_content_raises_on_empty_input():
    with pytest.raises(LLMUnavailable):
        summarize_page_content("   ")


def test_summarize_page_content_raises_when_model_returns_nothing(fake_summarize_ollama):
    with pytest.raises(LLMUnavailable):
        summarize_page_content("trigger_empty: whatever")


def test_summarize_page_content_raises_when_unreachable(monkeypatch):
    def _boom(*args, **kwargs):
        import urllib.error

        raise urllib.error.URLError("connection refused (simulated)")

    monkeypatch.setattr(llm.urllib.request, "urlopen", _boom)
    with pytest.raises(LLMUnavailable):
        summarize_page_content("some text")
