import http.server
import json
import threading
import urllib.error

import pytest

from app.ai import semantic_intent
from app.ai.semantic_intent import SemanticGuess, SemanticUnavailable, classify


@pytest.fixture()
def fake_ollama(monkeypatch):
    """Same pattern as tests/test_llm.py's fixture of the same name - a
    tiny local HTTP server standing in for Ollama on an ephemeral port,
    so tests never depend on (or collide with) a real Ollama instance."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            prompt = body.get("prompt", "")
            if "trigger_reminder" in prompt.lower():
                reply = '{"intent": "create_reminder", "confidence": 0.92}'
            elif "trigger_low" in prompt.lower():
                reply = '{"intent": "create_task", "confidence": 0.3}'
            elif "trigger_mid" in prompt.lower():
                reply = '{"intent": "start_timer", "confidence": 0.6}'
            elif "trigger_bad_intent" in prompt.lower():
                reply = '{"intent": "delete_everything", "confidence": 0.99}'
            elif "trigger_bad_json" in prompt.lower():
                reply = "not json at all"
            else:
                reply = '{"intent": "small_talk", "confidence": 0.8}'
            payload = json.dumps({"response": reply, "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)
    port = server.server_address[1]
    monkeypatch.setattr(
        semantic_intent, "OLLAMA_GENERATE_URL", f"http://localhost:{port}/api/generate"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_classify_returns_high_confidence_guess(fake_ollama):
    guess = classify("trigger_reminder: dentist thing at 4, don't let me forget")
    assert guess == SemanticGuess(intent="create_reminder", confidence=0.92)


def test_classify_clamps_confidence_into_0_1_range(fake_ollama):
    guess = classify("trigger_low: whatever")
    assert 0.0 <= guess.confidence <= 1.0


def test_classify_rejects_out_of_taxonomy_intent(fake_ollama):
    with pytest.raises(SemanticUnavailable):
        classify("trigger_bad_intent: do something wild")


def test_classify_raises_on_malformed_json(fake_ollama):
    with pytest.raises(SemanticUnavailable):
        classify("trigger_bad_json: gibberish")


def test_classify_raises_when_unreachable(monkeypatch):
    def _always_unreachable(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused (simulated)")

    monkeypatch.setattr(semantic_intent.urllib.request, "urlopen", _always_unreachable)
    with pytest.raises(SemanticUnavailable):
        classify("anything")


def test_confidence_band_ordering_is_sane():
    """CONFIDENCE_LOW must be strictly below CONFIDENCE_ACT - chat_engine's
    routing logic assumes a real 'ask' band exists between them."""
    assert 0.0 < semantic_intent.CONFIDENCE_LOW < semantic_intent.CONFIDENCE_ACT <= 1.0
