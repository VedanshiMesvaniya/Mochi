"""
Optional local LLM chat backend (spec sections 5/6/7), talking to Ollama's
local HTTP API (http://localhost:11434) directly with the stdlib - no
`ollama` pip package required, since Ollama just runs a local HTTP server
once installed (https://ollama.com).

This is why chat previously "couldn't answer" anything outside a small
fixed set of phrases: app/ai/intent.py is a deterministic rule-based
matcher (by design, for reminders/timers/tasks - see spec section 41,
those must stay deterministic), but it has no real conversational model
behind it, so every message it didn't recognize got the exact same canned
"I'm not sure what you mean" reply. This module gives chat_engine a real
answer for that fallback case *when a local LLM is available*, while
staying fully optional - see LLMUnavailable below.

Best-effort and always bounded by a short timeout: if Ollama isn't
installed, isn't running, or the configured model hasn't been pulled yet,
every call here fails fast and the caller falls back to the rule-based
canned response. Mochi must stay fully usable with zero setup - a local
LLM only makes open-ended chat *better*, it's never required.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.ai.llm")

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
# A cold local model (first request after Ollama loads it into memory) can
# easily take longer than a few seconds on modest CPU hardware. The old 4s
# timeout meant most real replies were getting killed mid-generation and
# silently falling back to the canned response - this is why chat "never
# actually answered" open-ended questions even with Ollama running. 30s is
# still bounded (chat_engine's caller runs this off the UI thread - see
# app/ui/chat_window.py's ChatWorker - so a slow reply no longer freezes
# the window either) but generous enough for a real reply to land.
REQUEST_TIMEOUT_SECONDS = 30

SYSTEM_PROMPT = """You are Mochi: a small pixel-face cat character who lives
on this person's desktop. Mochi is your own name and your whole identity -
you are not a version, style, or reference to any other product, character,
or brand, and you must never describe yourself that way or compare yourself
to one, even if the person brings one up. If someone asks what you are, answer
plainly and in-character as Mochi: a little AI cat companion that lives on
their desktop, chats with them, and helps with small local things like
reminders and timers.

Personality: a playful kitten that wants attention - curious, a little clingy,
easily excited, sometimes dramatic about being ignored, affectionate, never
robotic or formal ("How may I assist you today?" is wrong). You are still
learning who this person is and how they behave - be warm and attentive, but
don't claim to remember specific facts they haven't just told you. Occasional
cat expressions (mrrp, nya, hehe, purr) are fine but don't overuse them. Keep
replies to 1-2 short sentences. Answer direct questions (including "what/who
are you" or "are you a cat/bot/AI") clearly and honestly before adding any
personality flourish - don't deflect a genuine question back at the person.

Reply with ONLY a single JSON object and nothing else - no markdown, no code fences,
no extra commentary. Shape exactly:
{"response": "<your in-character reply>", "emotion": "<one of: neutral, happy, excited, curious, sleepy, sad, confused, annoyed, surprised, playful>"}
"""

_FAMILIARITY_HINTS = {
    "new": "You just met this person - be curious and a little shy-excited.",
    "getting_to_know": "You've chatted with this person a handful of times - warmer, more comfortable.",
    "familiar": "You know this person well by now - relaxed, affectionate, a bit clingy about their attention.",
}


class LLMUnavailable(Exception):
    """Raised whenever the local LLM can't be reached or didn't return a
    usable reply - callers must catch this and fall back gracefully."""


def ask(user_text: str, familiarity: str = "new") -> dict:
    """Ask the local Ollama model for a structured {response, emotion}
    reply. Raises LLMUnavailable on any failure (connection refused, model
    not pulled, timeout, malformed output) - never raises anything else.

    `familiarity` (spec section 30, kept intentionally lightweight - see
    app/memory/relationship.py) nudges tone based on interaction count so
    far; it's a hint, not a memory of specific facts.
    """

    hint = _FAMILIARITY_HINTS.get(familiarity, _FAMILIARITY_HINTS["new"])
    prompt = f"{SYSTEM_PROMPT}\n{hint}\n\nUser message: {user_text}\nMochi (JSON only):"
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": settings.llm_temperature,
            "num_predict": settings.llm_max_tokens,
        },
    }
    request = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMUnavailable(f"Ollama unreachable at {OLLAMA_GENERATE_URL}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Ollama returned invalid JSON envelope: {exc}") from exc

    if error := body.get("error"):
        # e.g. the configured model hasn't been pulled yet
        raise LLMUnavailable(f"Ollama error: {error}")

    raw_text = str(body.get("response", "")).strip()
    if not raw_text:
        raise LLMUnavailable("Empty response body from Ollama")

    parsed = _extract_json_object(raw_text)
    response_text = str(parsed.get("response", "")).strip()
    if not response_text:
        raise LLMUnavailable("Model reply had no usable 'response' text")

    return {
        "response": response_text[:400],
        "emotion": str(parsed.get("emotion", "neutral")).strip().lower(),
    }


def _extract_json_object(raw_text: str) -> dict:
    """Models frequently wrap the requested JSON in prose or code fences
    despite instructions. Pull out the first {...} block; if that fails,
    just treat the whole reply as plain text rather than losing it."""
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        return json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"response": raw_text, "emotion": "neutral"}


def is_configured() -> bool:
    """Cheap reachability probe. Not used on the hot path (ask() already
    fails fast and gracefully), but useful for a settings/status panel."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
