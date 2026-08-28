"""
Synonym glossary + schema reader for natural-language database questions
(spec follow-up: "connect llm with database - first it should read db
schema and make one synonym glossary for our db... then when user ask it
fetch glosary, db schema, make query, run on db, get answer, go to llm").

Design note - why this ISN'T "let the LLM write SQL":
Mochi's own architecture rule (see MOCHI_VERSIONED_ROADMAP.md section 1)
is "the LLM should reason about actions; it should not be trusted to
directly perform actions" - and section 7's hallucination strategy is
explicit: a question about real application state must ASK DATABASE, not
ASK LLM. A small local model (the whole point of Mochi staying
lightweight) writing live SQL against someone's own database is exactly
the kind of untrusted "LLM performs the action" step that rule exists to
prevent - it's a real SQL-injection/hallucination surface, and a wrong
query would silently misreport someone's own tasks/reminders/timers.

So the actual query here is built deterministically: this module maps the
words someone used (any synonym in ENTITY_SYNONYMS/STATUS_SYNONYMS below)
onto a plain (entity, status_bucket) pair, then app/ai/chat_engine.py runs
that through the existing, already-safe manager functions (the same ones
list_tasks()/list_reminders()/etc. already use) - never a raw string of
SQL. Only the *final wording* of the answer is handed to the LLM (see
app/ai/llm.py's phrase_data_answer()), grounded in the real rows this
module fetched, with an explicit instruction not to add facts beyond
them - so the net effect the person asked for (ask in plain English, get
a nicely-phrased answer) still happens, just without trusting the model
to touch the database directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.memory.database import get_connection

# --- Entity glossary -----------------------------------------------------
# Every phrase a person might use for "task"/"reminder"/"timer" ->
# Mochi's actual table name. Deliberately broad/"universal" per the spec
# ask - includes casual synonyms, not just the literal table name.
ENTITY_SYNONYMS: dict[str, str] = {
    # tasks
    "task": "tasks", "tasks": "tasks", "todo": "tasks", "to-do": "tasks",
    "to do": "tasks", "todos": "tasks", "to-dos": "tasks",
    "checklist": "tasks", "checklist item": "tasks", "assignment": "tasks",
    "assignments": "tasks", "chore": "tasks", "chores": "tasks",
    # reminders
    "reminder": "reminders", "reminders": "reminders", "alert": "reminders",
    "alerts": "reminders", "notification": "reminders",
    "notifications": "reminders", "ping": "reminders",
    # timers
    "timer": "timers", "timers": "timers", "timmer": "timers",
    "timmers": "timers", "countdown": "timers", "countdowns": "timers",
    "stopwatch": "timers",
}

# Every phrase for "still open" vs "already finished" vs "called off" ->
# a canonical status bucket. "all" means no status filter at all.
STATUS_SYNONYMS: dict[str, str] = {
    # active / still outstanding
    "remaining": "active", "remain": "active", "left": "active",
    "pending": "active", "open": "active", "outstanding": "active",
    "unfinished": "active", "not done": "active", "still need": "active",
    "still have": "active", "active": "active", "running": "active",
    "upcoming": "active", "current": "active", "todo": "active",
    # finished / done
    "done": "done", "completed": "done", "complete": "done",
    "finished": "done", "finish": "done", "closed": "done",
    "checked off": "done", "wrapped up": "done", "history": "done",
    "past": "done", "archive": "done", "archived": "done",
    "have i done": "done", "have i completed": "done",
    "have i finished": "done",
    # cancelled
    "cancelled": "cancelled", "canceled": "cancelled",
    "called off": "cancelled", "scrapped": "cancelled",
    # everything, regardless of state
    "all": "all", "everything": "all", "every": "all", "total": "all",
}

# Longest-phrase-first so e.g. "have i completed" matches before the
# shorter "have" would ever get a chance to (it doesn't appear alone, but
# this ordering rule is what keeps multi-word entries reliable in
# general - see match_status()/match_entity() below).
_ENTITY_KEYS = sorted(ENTITY_SYNONYMS, key=len, reverse=True)
_STATUS_KEYS = sorted(STATUS_SYNONYMS, key=len, reverse=True)

# Tables this module is willing to describe/query - kept in one place so
# a future table doesn't silently become queryable without a deliberate
# glossary entry for it too.
_QUERYABLE_TABLES = ("tasks", "reminders", "timers")


@dataclass
class QueryPlan:
    entity: str  # "tasks" | "reminders" | "timers"
    status: str  # "active" | "done" | "cancelled" | "all"


def match_entity(lowered_text: str) -> Optional[str]:
    """First entity (tasks/reminders/timers) whose synonym appears in the
    text, or None if none of the glossary's words are present."""
    for key in _ENTITY_KEYS:
        if key in lowered_text:
            return ENTITY_SYNONYMS[key]
    return None


def match_status(lowered_text: str) -> str:
    """First status bucket whose synonym appears in the text. Defaults to
    "active" (the most common thing "what's my ___" implicitly means) if
    nothing in the glossary matches."""
    for key in _STATUS_KEYS:
        if key in lowered_text:
            return STATUS_SYNONYMS[key]
    return "active"


def build_plan(text: str) -> Optional[QueryPlan]:
    """Turn free-form text into a QueryPlan, or None if it doesn't
    mention any recognized entity at all (caller should fall through to
    normal chat handling in that case)."""
    lowered = text.lower()
    entity = match_entity(lowered)
    if entity is None:
        return None
    return QueryPlan(entity=entity, status=match_status(lowered))


def describe_schema() -> str:
    """Read the *live* database schema (spec: "it should read db
    schema") via PRAGMA table_info and render it as short, plain text -
    used as grounding context for the LLM phrasing pass (see
    app/ai/llm.py's phrase_data_answer()), and handy for debugging/docs.
    Deliberately only describes the tables this module actually queries,
    not every internal table (e.g. trend_cache) - those aren't part of
    any user-facing glossary entry above.
    """
    lines: list[str] = []
    with get_connection() as conn:
        for table in _QUERYABLE_TABLES:
            columns = _table_columns(conn, table)
            lines.append(f"{table}({', '.join(columns)})")
            done_table = f"{table}_done"
            done_columns = _table_columns(conn, done_table)
            if done_columns:
                lines.append(f"{done_table}({', '.join(done_columns)})  -- archive of finished {table}")
    return "\n".join(lines)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [row["name"] for row in conn.execute(f"PRAGMA table_info({table});")]
    except sqlite3.DatabaseError:
        return []
