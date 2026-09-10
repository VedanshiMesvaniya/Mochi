"""
Semantic memory (Cognitive Upgrade spec section 7, "Semantic Memory") -
stable user facts and preferences, e.g. "User prefers concise reminders."

Gated by settings.memory_enabled (MOCHI_MEMORY_ENABLED, default true) -
every write and read here checks it first and raises MemoryDisabled
rather than silently no-op'ing, so a caller can tell "nothing relevant"
apart from "memory is turned off" and say something accurate either way.

Two ways a fact gets stored:
  * Explicit ("remember that I'm vegetarian") - app/ai/intent.py's
    REMEMBER_TRIGGER, handled in app/ai/chat_engine.py, source="stated".
  * Passive (an ordinary sentence like "I live in Austin" matches a
    known pattern) - app/ai/fact_extraction.py, called as a best-effort
    side effect at the end of chat_engine.handle_message() so a failure
    here can never change the visible reply or break the chat pipeline
    (spec/project value: "best-effort, never-raise" - see that module's
    docstring for where the try/except actually lives).

Contradiction handling (spec section 9) is keyed by `subject`: passing a
`subject` that matches an existing ACTIVE fact supersedes it (the old
row's status flips to 'superseded', pointing at the new row via
`superseded_by`; a fresh row is inserted) rather than adding a second,
conflicting active fact about the same thing. A `subject` of None
(open-ended "remember that ..." notes with no obvious topic key) always
gets its own unique subject instead, so unrelated notes are never
accidentally superseded by each other.

Retrieval (`find_relevant`) is a lightweight keyword-overlap score
against each active fact's subject + text - the same no-embeddings-
dependency approach app/knowledge/knowledge_store.py already uses for
Web Knowledge Engine retrieval (spec section 6: rank by relevance
without needing an embeddings model), applied here to a much smaller,
locally-written table instead of crawled web documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.core.config import settings
from app.core.exceptions import MemoryDisabled
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.memory.semantic")

ISO_FORMAT = "%Y-%m-%d %H:%M:%S"

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"

SOURCE_STATED = "stated"
SOURCE_INFERRED = "inferred"


@dataclass
class Fact:
    id: int
    subject: str
    text: str
    confidence: float
    source: str
    status: str
    created_at: str
    updated_at: str
    last_confirmed: str

    @classmethod
    def from_row(cls, row) -> "Fact":
        return cls(
            id=row["id"],
            subject=row["subject"],
            text=row["fact"],
            confidence=row["confidence"],
            source=row["source"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_confirmed=row["last_confirmed"],
        )


def _require_enabled() -> None:
    if not settings.memory_enabled:
        raise MemoryDisabled(
            "Semantic memory is turned off. Set MOCHI_MEMORY_ENABLED=true "
            "in .env to enable it."
        )


def ensure_ready() -> None:
    initialize_schema()


def _now() -> str:
    return datetime.now().strftime(ISO_FORMAT)


def remember_fact(
    text: str,
    subject: Optional[str] = None,
    confidence: float = 0.8,
    source: str = SOURCE_STATED,
) -> Fact:
    """Store a new active fact, superseding any existing ACTIVE fact with
    the same `subject` (spec section 9's contradiction handling).
    `subject=None` gives the row a unique subject of its own
    (`note:<id>`, backfilled after insert) so an open-ended note never
    contradicts some other unrelated open-ended note.

    Raises MemoryDisabled if settings.memory_enabled is off, and
    MemoryError_ (via the underlying database call) on a genuine storage
    failure - callers doing PASSIVE/best-effort extraction
    (app/ai/fact_extraction.py) must catch both themselves; this
    function does not swallow errors on its own, since an explicit
    "remember that..." command should surface a real failure to the
    user rather than silently pretending it worked (spec section 27,
    rule 1: never claim an action succeeded without evidence)."""
    _require_enabled()
    ensure_ready()
    text = text.strip()
    if not text:
        raise ValueError("Cannot remember an empty fact.")
    now = _now()

    with get_connection() as conn:
        if subject:
            existing = conn.execute(
                "SELECT id FROM user_facts WHERE subject = ? AND status = ?;",
                (subject, STATUS_ACTIVE),
            ).fetchone()
        else:
            existing = None

        cursor = conn.execute(
            """
            INSERT INTO user_facts
                (subject, fact, confidence, source, status, created_at, updated_at, last_confirmed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (subject or "", text, confidence, source, STATUS_ACTIVE, now, now, now),
        )
        new_id = cursor.lastrowid

        if not subject:
            # No topic key given - assign one unique to this row so it
            # can never later collide with (and wrongly supersede, or be
            # superseded by) an unrelated open-ended note.
            subject = f"note:{new_id}"
            conn.execute(
                "UPDATE user_facts SET subject = ? WHERE id = ?;", (subject, new_id)
            )

        if existing is not None:
            conn.execute(
                "UPDATE user_facts SET status = ?, superseded_by = ? WHERE id = ?;",
                (STATUS_SUPERSEDED, new_id, existing["id"]),
            )
            logger.info(
                "Fact #%s superseded fact #%s (subject=%r)", new_id, existing["id"], subject
            )

        row = conn.execute("SELECT * FROM user_facts WHERE id = ?;", (new_id,)).fetchone()

    logger.info("Remembered fact #%s (subject=%r, source=%r)", new_id, subject, source)
    return Fact.from_row(row)


def list_facts(active_only: bool = True) -> list[Fact]:
    """All stored facts, most recently updated first."""
    _require_enabled()
    ensure_ready()
    query = "SELECT * FROM user_facts"
    params: tuple = ()
    if active_only:
        query += " WHERE status = ?"
        params = (STATUS_ACTIVE,)
    query += " ORDER BY updated_at DESC;"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Fact.from_row(r) for r in rows]


def forget_fact(fact_id: int) -> bool:
    """Delete one fact outright (not a supersede - the user explicitly
    asked to forget it, so no trace should remain; spec section 20's
    "data permission philosophy" - deletion should be easy). Returns
    True if a row was actually deleted."""
    _require_enabled()
    ensure_ready()
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM user_facts WHERE id = ?;", (fact_id,))
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info("Forgot fact #%s", fact_id)
    return deleted


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "what", "whats", "when",
    "where", "who", "how", "do", "does", "did", "in", "on", "of", "for",
    "to", "and", "or", "it", "this", "that", "with", "about", "my", "me",
    "i", "you", "your", "please", "remember", "know", "tell",
}


def _tokenize(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def find_relevant(query: str, limit: int = 3) -> list[Fact]:
    """Active facts ranked by keyword overlap with `query` (see module
    docstring - deliberately no embeddings dependency, and no stemming
    either, so "live" won't match a stored "lives" - a false negative is
    safe here, just means nothing gets surfaced, whereas a fuzzy stemmer
    matching the wrong fact would actively mislead). Facts with zero
    overlapping words are excluded entirely rather than padding the
    result with irrelevant matches - callers should treat an empty list
    as "nothing relevant", not "memory came up empty and needs a
    default"."""
    facts = list_facts(active_only=True)
    query_words = _tokenize(query)
    if not query_words:
        return []
    scored = []
    for fact in facts:
        fact_words = _tokenize(f"{fact.subject} {fact.text}")
        overlap = len(query_words & fact_words)
        if overlap > 0:
            scored.append((overlap, fact.confidence, fact))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [fact for _, _, fact in scored[:limit]]


def find_matching(query: str) -> list[Fact]:
    """Active facts whose text or subject substring-contains `query`
    (case-insensitive) - used for "forget ..." commands, where a precise
    match matters more than a ranked/fuzzy one (accidentally forgetting
    the wrong fact is worse than asking the user to be more specific)."""
    facts = list_facts(active_only=True)
    needle = query.strip().lower()
    if not needle:
        return []
    return [f for f in facts if needle in f.text.lower() or needle in f.subject.lower()]
