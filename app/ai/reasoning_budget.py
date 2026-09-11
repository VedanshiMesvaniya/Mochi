"""
Reasoning budget (Cognitive Upgrade spec section 18: "Do not spend
expensive reasoning on simple messages").

Mochi already gets most of the spec's LEVEL 0-4 idea for free from the
intent router itself (app/ai/chat_engine.handle_message):

  LEVEL 0 (no model at all) - a deterministic keyword/regex trigger
    matched (app/ai/intent.py). The vast majority of messages never
    reach a model call in the first place - this is spec section 60's
    own rule ("can a normal function do it? -> tool"), not something
    this module needs to add.
  LEVEL 1 (one tiny, bounded call) - the keyword pass found nothing, so
    app/ai/semantic_intent.py asks the local model to classify which
    FIXED category the message belongs to (a few words back, capped at
    60 tokens) - already far cheaper than open-ended generation.
  LEVEL 2+ (open-ended generation) - both of the above came up empty,
    so app/ai/chat_engine.py falls back to a real conversational reply
    via app/ai/llm.ask().

The one place level wasn't actually distinguished is inside that LEVEL
2+ fallback itself: every such message got the exact same
context-gathering work before the model call - scanning stored facts
for overlap (app/memory/semantic_memory.find_relevant) and pulling the
cached web-knowledge context (app/knowledge/context_engine.get_web_context)
- even for a bare "lol" or "thanks" that plainly has nothing for either
to find. This module closes that one gap: a short, low-information
acknowledgment/reaction needs a reply, not a memory/context search.

Deliberately narrow, matching how every other Cognitive Upgrade phase
was scoped: this is NOT the spec's fuller vision of switching between a
smaller and a larger reasoning MODEL, or toggling a model's own
thinking-mode switch. Mochi's current local model (qwen2.5:1.5b via
Ollama) exposes neither, and there is no second, larger reasoning model
installed to route into yet (MOCHI_VERSIONED_ROADMAP.md section 19,
"Model Strategy" - still unactioned, tracked separately). Building a
model-tier router with only one real model to route to would be
speculative and untestable - see docs/ROADMAP_COGNITIVE_UPGRADE.md for
where this is tracked as still-deferred. What's implemented here is the
one concrete, testable slice available today: skip needless retrieval
work for messages that plainly don't need it.
"""

from __future__ import annotations

import re

# Fixed, deliberately small list of acknowledgments/reactions/filler
# that carry no information worth retrieving memory or web context for
# (same philosophy as app/ai/intent.py's GREETINGS tuple - a fixed set
# of known phrases, not a length-only heuristic). A short message CAN
# still be substantive ("i'm sad", "it broke again" are both two or
# three words and very much worth full context) - this only ever
# returns True for a message that, once punctuation/emoji are stripped,
# normalizes to exactly one of these known-empty-content phrases.
_TRIVIAL_PHRASES = frozenset({
    "ok", "okay", "k", "kk", "okie", "alright", "aight", "sure",
    "yeah", "yep", "yup", "mhm", "uh huh",
    "nah", "nope", "no",
    "cool", "nice", "sweet", "neat", "great",
    "thanks", "thank you", "thanks mochi", "ty", "tysm",
    "lol", "lmao", "haha", "hehe", "hahaha", "lolol",
    "got it", "gotcha", "sounds good", "ok thanks", "okay thanks",
    "np", "no problem", "you too",
})

_WORD_RE = re.compile(r"[a-z']+")


def is_trivial_chat(text: str) -> bool:
    """True for a short acknowledgment/reaction/filler message with
    nothing for memory or web-context retrieval to find - LEVEL 0
    conversation, in the spec's terms. False for anything not on the
    fixed list above, including every unfamiliar phrase - this only
    ever narrows what gets skipped, never guesses."""
    normalized = " ".join(_WORD_RE.findall(text.lower()))
    return normalized in _TRIVIAL_PHRASES
