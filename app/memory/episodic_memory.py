"""
Episodic memory (Cognitive Upgrade spec section 7, "Episodic Memory") -
a running, queryable record of notable things Mochi actually DID
(created a reminder, added a calendar event, ...), distinct from
semantic memory's stable facts ABOUT the user
(app/memory/semantic_memory.py, spec section 7's "Semantic Memory").

Scope (deliberate - see docs/ROADMAP_COGNITIVE_UPGRADE.md's "Deferred"
section for the full reasoning): only Mochi's own confirmed actions are
recorded here, not a free-text summary of what the conversation was
ABOUT. The spec's own example - "User worked on Google Calendar
integration and encountered OAuth problems" - is a conversational
summary, which would need either an LLM call or much richer NLP than a
deterministic keyword match can safely produce without risking a wrong,
confidently-stated summary. Recording real actions Mochi took is the
deterministic slice of the same underlying idea: a true, verifiable
event Mochi can point to (see recent_events()/important_events() below)
rather than one it's guessing at from conversational context.

Gated by settings.memory_enabled (MOCHI_MEMORY_ENABLED) - the same flag
semantic memory uses, since to the user both are simply "does Mochi
remember things about this session at all", even though they're
different tables/modules under the hood.

record_event() is deliberately best-effort and NEVER raises (unlike
semantic_memory.remember_fact, which raises on a genuine storage
failure so an explicit "remember that..." command can report a real
failure back to the user): every call site here is a side effect
alongside an action that has already succeeded (a reminder really was
created, a calendar event really was added) - a logging failure must
never be able to turn that real success into a visible chat error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.core.config import settings
from app.core.exceptions import MemoryDisabled
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.memory.episodic")

ISO_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Event:
    id: int
    event: str
    context: Optional[str]
    importance: float
    entities: list[str]
    occurred_at: str

    @classmethod
    def from_row(cls, row) -> "Event":
        raw_entities = row["entities"]
        try:
            entities = json.loads(raw_entities) if raw_entities else []
        except (TypeError, ValueError):
            entities = []
        return cls(
            id=row["id"],
            event=row["event"],
            context=row["context"],
            importance=row["importance"],
            entities=entities,
            occurred_at=row["occurred_at"],
        )


def _require_enabled() -> None:
    if not settings.memory_enabled:
        raise MemoryDisabled(
            "Episodic memory is turned off. Set MOCHI_MEMORY_ENABLED=true "
            "in .env to enable it."
        )


def ensure_ready() -> None:
    initialize_schema()


def record_event(
    event: str,
    context: Optional[str] = None,
    importance: float = 0.5,
    entities: Optional[list[str]] = None,
) -> Optional[Event]:
    """Record one notable thing Mochi just did. Returns None (rather
    than raising) when memory is disabled or the write itself fails for
    any reason - see the module docstring for why this specifically
    must never raise, unlike most of the rest of this codebase's
    fail-loudly-on-a-real-error convention."""
    if not settings.memory_enabled:
        return None
    try:
        ensure_ready()
        now = datetime.now().strftime(ISO_FORMAT)
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO episodic_events (event, context, importance, entities, occurred_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (event, context, importance, json.dumps(entities or []), now),
            )
            row = conn.execute(
                "SELECT * FROM episodic_events WHERE id = ?;", (cursor.lastrowid,)
            ).fetchone()
        return Event.from_row(row)
    except Exception as exc:  # noqa: BLE001 - see module docstring: never raise from here
        logger.debug("Episodic event not recorded: %s", exc)
        return None


def recent_events(limit: int = 10) -> list[Event]:
    """Most recent events, newest first. Raises MemoryDisabled if memory
    is off - unlike record_event, a caller explicitly asking to READ
    history should be told why it came back empty rather than getting a
    silent []. Matches semantic_memory.list_facts's same choice."""
    _require_enabled()
    ensure_ready()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM episodic_events ORDER BY occurred_at DESC LIMIT ?;", (limit,)
        ).fetchall()
    return [Event.from_row(r) for r in rows]


def important_events(threshold: float = 0.7, limit: int = 10) -> list[Event]:
    """Events at or above `threshold` importance, most recent first -
    for a shorter "what actually mattered" view rather than everything."""
    _require_enabled()
    ensure_ready()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM episodic_events
            WHERE importance >= ?
            ORDER BY occurred_at DESC
            LIMIT ?;
            """,
            (threshold, limit),
        ).fetchall()
    return [Event.from_row(r) for r in rows]
