import http.server
import json
import threading

import pytest

from app.ai.chat_engine import handle_message
from app.ai import llm
from app.ai.llm import LLMUnavailable, ask
from app.character.state_machine import Emotion


def test_ask_raises_llmunavailable_when_unreachable():
    """No Ollama running in the test environment - must fail fast and
    with the specific exception callers know to catch, never hang or
    raise something generic."""
    with pytest.raises(LLMUnavailable):
        ask("anything")


def test_chat_engine_falls_back_gracefully_without_llm():
    reaction = handle_message("what's the meaning of life")
    assert reaction.text  # never empty, even with no LLM available


@pytest.fixture()
def fake_ollama(monkeypatch):
    """A tiny local HTTP server standing in for Ollama, so we can test the
    happy path (and malformed-output handling) without a real model.

    Binds an OS-assigned ephemeral port (not the real Ollama port 11434)
    and monkeypatches the client to point at it, so tests never collide
    with each other or with a real Ollama instance that might be running.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            prompt = body.get("prompt", "")
            if "trigger_explode" in prompt.lower():
                reply = "not json at all, just prose"
            elif "trigger_fence" in prompt.lower():
                reply = '```json\n{"response": "wrapped reply", "emotion": "happy"}\n```'
            else:
                reply = '{"response": "a real answer", "emotion": "curious"}'
            payload = json.dumps({"response": reply, "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence test output
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


def test_ask_returns_structured_reply(fake_ollama):
    result = ask("give me a real answer")
    assert result == {"response": "a real answer", "emotion": "curious"}


def test_ask_extracts_json_from_code_fence(fake_ollama):
    result = ask("trigger_fence please")
    assert result["response"] == "wrapped reply"
    assert result["emotion"] == "happy"


def test_ask_falls_back_to_raw_text_when_not_json(fake_ollama):
    result = ask("please trigger_explode")
    assert result["response"] == "not json at all, just prose"
    assert result["emotion"] == "neutral"


def test_chat_engine_uses_llm_for_unrecognized_messages(fake_ollama):
    reaction = handle_message("give me a real answer to this")
    assert reaction.text == "a real answer"
    assert reaction.emotion == Emotion.CURIOUS


def test_chat_engine_never_routes_reminders_through_llm(fake_ollama):
    """Actionable intents must stay fully deterministic even when an LLM
    is available - spec section 41."""
    reaction = handle_message("remind me to water the plants at 6pm")
    assert reaction.text.startswith("Okay! I'll remind you")
