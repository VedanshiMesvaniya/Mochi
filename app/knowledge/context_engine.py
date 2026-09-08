"""
Context Engine integration point (spec sections 23/24/30) - the only part
of this package chat_engine.py should ever call directly, and only a
cheap local SQLite read (see get_web_context's docstring): no network
call happens here, matching app/humor/trend_fetcher.pick_one_trend()'s
"never fetch synchronously inside a chat reply" rule.

Two responsibilities:
  1. `classify_query` - the Freshness Router (spec section 23): does this
     question look like it needs current/fresh information at all, or is
     it the kind of stable question local/LLM knowledge already answers
     fine? Personal-context and live-conversation-memory questions (spec
     section 24's other two categories) are already handled elsewhere in
     Mochi (app/ai/conversation_state.py, chat history) and are out of
     scope here - this router only decides "would fresh web evidence
     help", not the full four-way split from the spec.
  2. `get_web_context` - builds the compact evidence package (spec
     section 30) hostname passed into app/ai/llm.ask's new `web_context`
     parameter, or None when nothing applies - which chat_engine.py must
     treat as the normal default case, not an error.
"""

from __future__ import annotations

import re
from typing import Optional

from app.core.config import settings
from app.core.logger import get_logger
from app.knowledge.knowledge_store import query_relevant

logger = get_logger("mochi.knowledge.context_engine")

# Signals that a question is asking about the current/live state of the
# world rather than a stable, timeless concept (spec section 24's
# "Current Knowledge" / "Live Information" categories). Deliberately a
# small, high-precision keyword list rather than an LLM classification
# call - this router itself must stay a cheap, synchronous, no-network
# check (see module docstring).
_CURRENT_SIGNAL_WORDS = {
    "latest", "current", "currently", "today", "trending", "trend",
    "recent", "recently", "now", "update", "updated", "news",
    "happening", "right now",
}
_WORD_RE = re.compile(r"[a-z']+")

_MAX_EVIDENCE_ITEMS = 3
_MAX_CLAIM_CHARS = 160


def classify_query(text: str) -> str:
    """Returns "current" if `text` looks like it wants fresh/live
    information, else "stable". A "stable" classification is the safe
    default - it means "answer from existing knowledge as normal", not
    "this question is unanswerable"."""
    lowered = text.lower()
    if "right now" in lowered:
        return "current"
    words = set(_WORD_RE.findall(lowered))
    return "current" if words & _CURRENT_SIGNAL_WORDS else "stable"


def _format_evidence(items) -> str:
    lines = ["Fresh web evidence you may reference if relevant (never invent beyond this):"]
    for item in items:
        claim = item.claim[:_MAX_CLAIM_CHARS]
        lines.append(
            f"- {claim} (source: {item.source}, freshness: {item.freshness})"
        )
    return "\n".join(lines)


def get_web_context(text: str) -> Optional[str]:
    """Compact evidence package for `text`, or None. None whenever:
    the engine is disabled, the question doesn't look like it needs fresh
    information, or nothing relevant has been ingested yet - all three
    are the normal default case, matching pick_one_trend()'s contract
    that callers never need to distinguish "off" from "nothing found".
    This only ever reads from the local knowledge_store cache (populated
    by scheduler.run_ingestion_cycle on its own schedule) - never a live
    fetch, so this call is always fast and offline-safe.
    """
    if not settings.web_knowledge_enabled:
        return None
    if classify_query(text) != "current":
        return None

    try:
        items = query_relevant(text, limit=_MAX_EVIDENCE_ITEMS)
    except Exception:  # noqa: BLE001 - a knowledge-store hiccup must never break chat
        logger.exception("Failed to query knowledge store (non-fatal)")
        return None

    if not items:
        return None
    return _format_evidence(items)
