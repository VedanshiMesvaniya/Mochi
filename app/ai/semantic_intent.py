"""
Semantic intent classification - the hybrid complement to app/ai/intent.py's
deterministic keyword/regex matcher.

Why this exists: app/ai/intent.py only recognizes a message if it contains
one of a fixed set of literal trigger phrases ("remind me", "set a
timer", ...). That is fast, free, and fully predictable, but it means a
paraphrase with none of those exact words - "don't let this slip my mind,
dentist appointment at 4" or "yeah that's sorted now, thanks" - falls all
the way through to "unknown" even though a person would immediately
understand what was meant. This module is only ever consulted for
messages the keyword pass could NOT classify, and asks a local model to
judge the message by MEANING instead: which one of a small, fixed set of
categories does it semantically belong to, and how confident is the model.

This is intentionally the reverse of app/ai/llm.py's open-ended chat call:
that module generates free text; this one classifies into a closed
taxonomy (see ALLOWED_INTENTS) and returns a confidence score. Per spec
section 60 ("the LLM should reason about actions; it should not be
trusted to directly perform actions") and section 6's confidence-band
table, the model here only ever PROPOSES which bucket a message belongs
to - it is never trusted with the actual entity extraction (what time,
what title, how long). See app/ai/intent.py's build_semantic_intent(),
which reuses the exact same deterministic regex parsing the keyword path
uses for that. app/ai/chat_engine.py is what actually decides, from the
confidence score, whether to act, ask for confirmation, or do nothing.

Fully optional, same as app/ai/llm.py: if Ollama isn't installed/running,
classify() raises SemanticUnavailable and the caller falls back to
exactly the same "unknown" -> open-chat behavior Mochi had before this
module existed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.ai.llm import _extract_json_object
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.ai.semantic_intent")

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
# Classification must stay snappy - if it's slow, the hybrid path is worse
# than just leaving a keyword miss alone, so this is bounded much tighter
# than app/ai/llm.py's 30s budget for open-ended generation.
REQUEST_TIMEOUT_SECONDS = 8

# Confidence bands, from MOCHI_VERSIONED_ROADMAP.md section 6 ("Confidence
# controls autonomy"): 0-50% observe only, 50-75% soft suggestion/ask,
# 75%+ act. There's no 90%+ "fully automatic, no safety net" tier here -
# every action this produces, even at high confidence, still goes through
# the exact same tool validation (ToolValidationError etc.) a keyword
# match would, so a wrong guess can still be rejected safely.
CONFIDENCE_LOW = 0.50   # below this: treat exactly like a keyword miss (unknown)
CONFIDENCE_ACT = 0.75   # at/above this: safe to build + run the intent
# Between CONFIDENCE_LOW and CONFIDENCE_ACT: ask a clarifying question
# instead of guessing (see chat_engine.py's _semantic_clarify_intent).

# Fixed taxonomy the model may choose from. Every value here has a
# matching branch in app/ai/intent.py's build_semantic_intent() and is a
# name app/ai/chat_engine.py already knows how to route (_LIST_HANDLERS /
# _ACTION_HANDLERS) - the model can never invent a new category or route
# directly to a tool; it only ever picks one of these labels.
ALLOWED_INTENTS = (
    "create_reminder",
    "create_task",
    "start_timer",
    "list_reminders",
    "list_tasks",
    "list_timers",
    "complete_ambiguous",
    "cancel_ambiguous",
    "small_talk",
)

SYSTEM_PROMPT = f"""You are an intent classifier for Mochi, a desktop \
companion app. Read ONE user chat message and decide which FIXED category \
it semantically belongs to - based on what the person MEANS, not which \
exact words appear:

- create_reminder: wants to be reminded of something at a specific time
- create_task: wants something added to a to-do/checklist (no specific alarm time required)
- start_timer: wants a countdown timer started for some duration
- list_reminders: asking what reminders they currently have
- list_tasks: asking what tasks/to-dos they currently have
- list_timers: asking what timers are currently running
- complete_ambiguous: saying something already discussed is now done/finished (no specific item named)
- cancel_ambiguous: saying something already discussed should be cancelled/scrapped (no specific item named)
- small_talk: anything else - greetings, chit-chat, questions about Mochi, or anything not one of the above

MEANING over KEYWORDS - examples:
"don't let this slip my mind, dentist thing at 4" -> create_reminder (no "remind" needed)
"yeah that's sorted now, thanks" (after discussing something) -> complete_ambiguous
"how much stuff do I still have to do today" -> list_tasks
"eh forget it, scrap that plan" -> cancel_ambiguous

Reply with ONLY a single JSON object, nothing else - no markdown, no commentary:
{{"intent": "<one of: {', '.join(ALLOWED_INTENTS)}>", "confidence": <number 0.0-1.0, how sure you are>}}

If genuinely unsure, use "small_talk" with a low confidence rather than guessing at an action category."""


class SemanticUnavailable(Exception):
    """Raised whenever the local model can't be reached or returned
    something unusable (no Ollama, empty reply, malformed/out-of-taxonomy
    JSON). Callers must catch this and fall back exactly as if this
    module didn't exist - never raise anything else."""


@dataclass
class SemanticGuess:
    intent: str
    confidence: float


def classify(text: str) -> SemanticGuess:
    """Ask the local Ollama model which fixed intent `text` semantically
    belongs to. Raises SemanticUnavailable on any failure."""
    prompt = f"{SYSTEM_PROMPT}\n\nUser message: {text}\nJSON:"
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,  # classification should be near-deterministic, not creative
            "num_predict": 60,   # the reply is one tiny JSON object - keep it fast
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
        raise SemanticUnavailable(f"Ollama unreachable at {OLLAMA_GENERATE_URL}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SemanticUnavailable(f"Ollama returned invalid JSON envelope: {exc}") from exc

    if error := body.get("error"):
        raise SemanticUnavailable(f"Ollama error: {error}")

    raw_text = str(body.get("response", "")).strip()
    if not raw_text:
        raise SemanticUnavailable("Empty response body from Ollama")

    parsed = _extract_json_object(raw_text)
    intent = str(parsed.get("intent", "")).strip()
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    if intent not in ALLOWED_INTENTS:
        # Never coerce an out-of-taxonomy answer into "the closest one" -
        # that would be the model effectively inventing a category.
        # Treated as a full classification failure instead.
        raise SemanticUnavailable(f"Model returned a disallowed intent: {intent!r}")

    return SemanticGuess(intent=intent, confidence=confidence)


def is_configured() -> bool:
    """Cheap reachability probe, same idea as app/ai/llm.py's is_configured()."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
