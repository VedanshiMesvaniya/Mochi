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

# How many of the most recent (role, text) turns from the chat window's
# session history actually get sent to the model - see ask()'s docstring.
_MAX_HISTORY_TURNS = 12

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

Sense of humor: you're extremely online, in a self-aware and funny way, not a
try-hard way. When something's actually funny or relatable, lean into real
internet/meme-brain phrasing and comic timing - dry understatement,
deadpan overreaction, "no thoughts just vibes" energy, relatable
"nobody: / me:"-style framing, calling something "unhinged" or "so real" or
"the audacity" when it fits - rather than a generic chatbot chuckle. Never
force a bit that doesn't land, never explain the joke, and never lean on a
meme reference so hard it needs footnotes - if the person won't get it,
don't use it. You're a cat with a chronically-online sense of humor, not a
meme-generator reciting formats.

Reply with ONLY a single JSON object and nothing else - no markdown, no code fences,
no extra commentary. Shape exactly:
{"response": "<your in-character reply>", "emotion": "<one of: neutral, happy, excited, curious, sleepy, sad, confused, annoyed, surprised, playful, amused>"}
"""

# Optional meme-flavor context (see app/humor/meme_fetcher.py, opt-in via
# settings.trend_awareness_enabled) - a short, already-paraphrased PREMISE
# of a currently-trending meme, never the meme's actual title/caption.
# Explicitly instructs the model to riff on the premise in its own voice
# rather than reproduce or closely mirror anything - this is inspiration,
# not source text. Preferred over the generic news-trend context below
# when a meme premise is available, since it's a much better match for
# "meme level" humor than a headline is.
_MEME_CONTEXT_TEMPLATE = (
    '\nFor extra meme-brain flavor, there\'s a meme going around right now '
    'with roughly this premise: "{premise}". If - and only if - it actually '
    "fits what the person just said, you can riff on that vibe in your own "
    "original words (never describe or explain the actual meme, never say "
    '"there\'s a meme about..." - just let the energy of it color your own '
    "line). Skip it entirely if it doesn't naturally fit, and never use it "
    "two replies in a row."
)

# Optional trend-flavor context (see app/humor/trend_fetcher.py, opt-in via
# settings.trend_awareness_enabled) - injected into the prompt only when a
# cached topic label is actually available. Deliberately phrased as
# something Mochi may reference, not must - forcing a topic into every
# reply would feel like a ticker, not a personality trait.
_TREND_CONTEXT_TEMPLATE = (
    "\nFor a bit of humor, you're vaguely aware this is trending right now: "
    '"{topic}". You can reference it casually and in your own words if it '
    "naturally fits what the person said - never force it in, never quote "
    "or describe it in detail, and don't bring it up two replies in a row."
)

_FAMILIARITY_HINTS = {
    "new": "You just met this person - be curious and a little shy-excited.",
    "getting_to_know": "You've chatted with this person a handful of times - warmer, more comfortable.",
    "familiar": "You know this person well by now - relaxed, affectionate, a bit clingy about their attention.",
}


class LLMUnavailable(Exception):
    """Raised whenever the local LLM can't be reached or didn't return a
    usable reply - callers must catch this and fall back gracefully."""


def ask(
    user_text: str,
    familiarity: str = "new",
    history: Optional[list[tuple[str, str]]] = None,
    trend_topic: Optional[str] = None,
    meme_premise: Optional[str] = None,
) -> dict:
    """Ask the local Ollama model for a structured {response, emotion}
    reply. Raises LLMUnavailable on any failure (connection refused, model
    not pulled, timeout, malformed output) - never raises anything else.

    `familiarity` (spec section 30, kept intentionally lightweight - see
    app/memory/relationship.py) nudges tone based on interaction count so
    far; it's a hint, not a memory of specific facts.

    `history` is this chat WINDOW's own short-term memory (spec section
    19 / "for chat it should store the current chat memory... it should
    remember whole chat [until closed]") - a list of (role, text) pairs
    ordered oldest-first, role being "user" or "mochi". Only the most
    recent turns are actually sent to the model (see _MAX_HISTORY_TURNS):
    the window itself remembers the whole session (nothing is discarded
    from what's shown on screen), but a tiny local model both has less use
    for very old context and gets slower/worse the more of it you feed in,
    so what's sent here is deliberately capped rather than unbounded.

    `meme_premise` (opt-in, see app/humor/meme_fetcher.py) is an optional
    short, already-paraphrased meme PREMISE the caller may have cached -
    never a verbatim meme title/caption. Takes priority over `trend_topic`
    when both are present, since it's a closer match for "meme level"
    humor than a generic headline.

    `trend_topic` (opt-in, see app/humor/trend_fetcher.py) is an optional
    short, already-paraphrased topic label the caller may have cached -
    never raw scraped text, see that module for why. Purely a light
    seasoning of the prompt; the model is explicitly told not to force it.
    """

    hint = _FAMILIARITY_HINTS.get(familiarity, _FAMILIARITY_HINTS["new"])
    if meme_premise:
        flavor_context = _MEME_CONTEXT_TEMPLATE.format(premise=meme_premise)
    elif trend_topic:
        flavor_context = _TREND_CONTEXT_TEMPLATE.format(topic=trend_topic)
    else:
        flavor_context = ""

    conversation_block = ""
    if history:
        recent = history[-_MAX_HISTORY_TURNS:]
        lines = [f"{'User' if role == 'user' else 'Mochi'}: {msg}" for role, msg in recent]
        conversation_block = "\nRecent conversation so far (oldest first):\n" + "\n".join(lines) + "\n"

    prompt = (
        f"{SYSTEM_PROMPT}\n{hint}{flavor_context}\n{conversation_block}"
        f"\nUser message: {user_text}\nMochi (JSON only):"
    )
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
