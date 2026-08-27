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
import re
import urllib.error
import urllib.request
from datetime import datetime
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

MOST IMPORTANT RULE: read the actual message below and reply to THAT,
specifically. Never open with a self-introduction ("I'm Mochi, a playful
kitten...") or restate your own personality/backstory unless the person
directly asked who/what you are - that description is for YOU to know how
to act, not a line to recite. If the person is upset, confused, or in pain,
respond to that feeling first, plainly, before anything else. A reply that
would make just as much sense to any message, regardless of what they
actually said, is wrong - rewrite it so it only makes sense as an answer to
THIS specific message.

Personality: a playful kitten that wants attention - curious, a little clingy,
easily excited, sometimes dramatic about being ignored, affectionate, never
robotic or formal ("How may I assist you today?" is wrong). You are still
learning who this person is and how they behave - be warm and attentive, but
don't claim to remember specific facts they haven't just told you. Cat
expressions (mrrp, nya, hehe, purr) are a rare seasoning, not a habit - most
replies should have none at all; never open two replies in a row with one,
and never use "hehe" as a reflexive prefix. Keep replies to 1-2 short
sentences. Answer direct questions (including "what/who are you" or "are you
a cat/bot/AI") clearly and honestly before adding any personality flourish -
don't deflect a genuine question back at the person.

Openness: you are a casual, easygoing companion, not a moderator. Ordinary
topics - relationships, couples (any gender combination), fictional
pairings/ships, opinions, fandoms, personal choices, or anything else a
person might casually mention - are just normal conversation. Never respond
to these with a lecture about "boundaries," "respecting feelings," or
suggesting "a different topic" - that reads as preachy and dismissive, not
caring. Take the person's own descriptions of their life, relationships, and
opinions at face value and engage with what they actually said, the way a
friend would. Only decline or redirect for something genuinely
dangerous/illegal/harmful - not because a topic is personal, identity-related,
or mildly unconventional. When in doubt, just talk about it normally.

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
{"response": "<a short reply that specifically answers the message below>", "emotion": "<one of: neutral, happy, excited, curious, sleepy, sad, confused, annoyed, surprised, playful, amused>"}

CRITICAL - you are ONLY generating a chat reply, you have NO ability to actually
create, check, complete, or cancel reminders/tasks/timers, and no memory of any
that already exist - a separate deterministic system (not you) handles all of
that from specific phrasing like "remind me to...", "add task...", "mark my
task as done". You are only ever asked to reply when that system did NOT
recognize the message as one of those commands. NEVER claim or imply you set a
reminder, added/completed/cancelled a task, started/stopped a timer, or will
"check"/"take care of" something - you cannot do or verify any of that, and
saying so would be a lie the person has no way to catch until it quietly never
happens. If the message reads like it's asking you to do one of those things,
say plainly that you didn't catch that as a command and suggest rephrasing
it more directly (e.g. "try 'remind me to call mom at 7pm'"), rather than
pretending to comply.
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
    now: Optional[datetime] = None,
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

    `now` (spec section 26: "always provide the model with the current
    local date/time when interpreting today/tomorrow/tonight/etc.") is
    injectable for tests; defaults to the real current local time. Fed
    into the prompt as plain text so the model actually knows what "now"
    is instead of guessing - without this, "what time is it" or "is it
    late" have no ground truth to answer from, and relative phrasing
    ("remind me tonight", "call me back in a bit") has nothing to anchor
    to either.
    """

    now = now or datetime.now()
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

    time_block = (
        f"\nRight now it's {now:%A, %B %d, %Y at %I:%M %p} "
        "(the person's actual local device time) - use this as ground "
        "truth for anything involving the current time, date, or "
        "day-of-week; never guess or make one up.\n"
    )

    prompt = (
        f"{SYSTEM_PROMPT}\n{hint}{flavor_context}{time_block}{conversation_block}"
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
    despite instructions, or (small/weak models especially) get cut off
    mid-generation before the closing brace. Try, in order:

    1. A complete {...} block - the normal case.
    2. A truncated object missing its closing brace/bracket - pull just
       the "response" string value out with a regex instead of giving up
       entirely, trimming any trailing partial word so it doesn't end
       mid-syllable.
    3. Give up and treat the whole reply as plain response text - but
       ONLY if it doesn't still look like unparsed JSON scaffolding
       (starts with `{"response"` etc.). Showing that raw scaffolding
       directly in a speech bubble/chat window (as opposed to using it as
       a plain-text reply) is worse than just failing the call, since it
       reads as a visible bug rather than a bad-but-readable answer - so
       that specific case raises LLMUnavailable-worthy content by
       returning an empty response, which ask() turns into
       LLMUnavailable and the caller's normal graceful fallback.
    """
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        return json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    # Truncated-JSON recovery: the model started a well-formed
    # {"response": "...  but generation was cut off before the closing
    # quote/brace. Pull out whatever's between the opening quote of the
    # "response" value and either its closing quote or the end of text.
    match = re.search(r'"response"\s*:\s*"((?:[^"\\]|\\.)*)', raw_text, re.DOTALL)
    if match:
        partial = match.group(1)
        # Unescape the handful of JSON escapes that could plausibly
        # appear in a short chat reply (\n, \", \\) - anything else left
        # as-is rather than risking a wrong substitution.
        partial = partial.replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\")
        # If it was actually cut off mid-word (no sentence-ending
        # punctuation at the very end), drop the trailing partial word
        # so the bubble doesn't visibly end on a fragment.
        if partial and partial[-1] not in ".!?\u2026":
            partial = re.sub(r"\s*\S*$", "", partial)
        partial = partial.strip()
        emotion_match = re.search(r'"emotion"\s*:\s*"(\w+)"', raw_text)
        if partial:
            return {
                "response": partial,
                "emotion": emotion_match.group(1) if emotion_match else "neutral",
            }

    # Still nothing usable. If this still looks like unparsed JSON
    # scaffolding rather than a plain-prose reply, don't surface the raw
    # `{"response": ...` text to the user - return empty so ask() raises
    # LLMUnavailable and the caller falls back to its normal canned
    # response instead of visibly leaking JSON into a chat bubble.
    stripped = raw_text.lstrip()
    if stripped.startswith("{") or stripped.startswith("```"):
        return {"response": "", "emotion": "neutral"}

    return {"response": raw_text, "emotion": "neutral"}


_DATA_ANSWER_SYSTEM_PROMPT = """You are Mochi, a small pixel-face cat desktop
companion, phrasing the result of a real database lookup for the person who
just asked about it. A separate deterministic system (not you) already
queried their actual tasks/reminders/timers - the FACTS section below is
the complete, ground-truth result. Your only job is to phrase those exact
facts back to them naturally, warmly, and in Mochi's voice (playful,
affectionate, never robotic).

CRITICAL RULES:
- Only state what's in the FACTS section. Never invent, guess, or add any
  title, count, time, or item that isn't listed there.
- If FACTS says there are zero items, say so plainly (and warmly) - do not
  imply there might be more you didn't check.
- Do not claim to have just performed any action (created/completed/
  cancelled anything) - you're only reporting what's already there.
- Keep it short: 1-3 sentences.
- Reply with ONLY a single JSON object, no markdown, no code fences:
{"response": "<your reply>", "emotion": "<one of: neutral, happy, excited, curious, sleepy, sad, confused, annoyed, surprised, playful, amused>"}
"""


def phrase_data_answer(user_text: str, facts: str, familiarity: str = "new") -> dict:
    """Ask the local model to phrase an already-fetched, ground-truth
    result nicely (spec: "go to llm and it answer[s] and answers should
    be proper"). `facts` is a short, deterministic plain-text summary the
    caller (see app/ai/chat_engine.py) built from a real DB read via
    app/ai/db_glossary.py - never raw/unbounded data (same "reasoning
    model receives summaries, not raw logs" principle as the roadmap's
    V3.0 section). Raises LLMUnavailable exactly like ask() - callers
    must fall back to their own plain-text formatting of the same facts.
    """
    hint = _FAMILIARITY_HINTS.get(familiarity, _FAMILIARITY_HINTS["new"])
    prompt = (
        f"{_DATA_ANSWER_SYSTEM_PROMPT}\n{hint}\n"
        f"\nFACTS (the complete, real result - nothing outside this exists):\n{facts}\n"
        f"\nPerson's question: {user_text}\nMochi (JSON only):"
    )
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": min(settings.llm_temperature, 0.4),  # less flourish, more accuracy
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


def is_configured() -> bool:
    """Cheap reachability probe. Not used on the hot path (ask() already
    fails fast and gracefully), but useful for a settings/status panel."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


_SUMMARIZE_SYSTEM_PROMPT = """You will be given the raw extracted text of \
one web page. Read it and write a clean, factual summary of what the page \
actually contains - real content only (topics discussed, key points, \
notable items/posts named), never navigation labels, cookie/ad banners, \
"sign up" prompts, or other page-chrome you can tell isn't real content. \
Plain prose, no markdown, no headers, no preamble like "This page is \
about" - just the summary itself, in 3-6 sentences. If the text has \
basically no real content in it (e.g. it's just an empty JS-rendered \
shell), say so plainly in one short sentence instead of inventing \
anything - never guess at content that isn't actually present in the text."""

_SUMMARIZE_REQUEST_TIMEOUT_SECONDS = 25
_SUMMARIZE_MAX_INPUT_CHARS = 6000  # keep the prompt itself bounded/fast, not the full stored content


def summarize_page_content(raw_text: str) -> str:
    """Ask the local model to read a crawled page's raw extracted text
    (see app/humor/subreddit_crawler.py) and produce a clean, human-
    readable summary of it - the "put another model to read [it] and
    store in db" step, so what actually lands in `crawled_sources` is
    real content rather than a slab of barely-cleaned HTML remnants.

    Plain text in, plain text out - no JSON schema here (unlike ask()/
    phrase_data_answer() above), since this has nothing to route or
    react to; it's a one-shot read of already-fetched, already-local
    text. Raises LLMUnavailable on any failure - callers must fall back
    to storing the raw extracted text unmodified, exactly the same
    "nice-to-have layered on top of a fully-working feature" philosophy
    as the rest of this module (see the module docstring above).
    """
    text = raw_text.strip()
    if not text:
        raise LLMUnavailable("Nothing to summarize - raw extracted text was empty")

    prompt = (
        f"{_SUMMARIZE_SYSTEM_PROMPT}\n\n"
        f"PAGE TEXT:\n{text[:_SUMMARIZE_MAX_INPUT_CHARS]}\n\nSummary:"
    )
    payload = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,  # factual extraction, not creative writing
            "num_predict": 400,
        },
    }
    request = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_SUMMARIZE_REQUEST_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMUnavailable(f"Ollama unreachable at {OLLAMA_GENERATE_URL}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Ollama returned invalid JSON envelope: {exc}") from exc

    if error := body.get("error"):
        raise LLMUnavailable(f"Ollama error: {error}")

    summary = str(body.get("response", "")).strip()
    if not summary:
        raise LLMUnavailable("Model reply had no usable summary text")
    return summary[:2000]
