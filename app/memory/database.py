"""
SQLite database layer (spec section 18 - Local Memory).

Single local file at `data/mochi.db`. No server, no cloud. Tables are
created incrementally as each phase needs them - this file currently
defines reminders (V1), plus tasks and timers (V2). Later phases
(conversations, memories, mood_state, relationship_state, calendar_cache)
will extend `SCHEMA_STATEMENTS` rather than replacing this module.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.database")

# Each statement is idempotent (CREATE TABLE IF NOT EXISTS) so calling
# initialize_schema() repeatedly on startup is always safe.
SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS reminders (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT NOT NULL,
        due_at        TEXT NOT NULL,       -- ISO 8601, local timezone
        repeat_rule   TEXT,                -- e.g. 'DAILY', 'WEEKLY:MON', NULL = one-off
        status        TEXT NOT NULL DEFAULT 'pending',  -- pending|completed|cancelled
        created_at    TEXT NOT NULL,
        completed_at  TEXT,
        notified_at   TEXT                 -- set once the due notification has fired
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders(status, due_at);",
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open',   -- open|done|cancelled
        created_at    TEXT NOT NULL,
        completed_at  TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);",
    """
    CREATE TABLE IF NOT EXISTS timers (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        label         TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,
        started_at    TEXT NOT NULL,
        due_at        TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'running',  -- running|done|cancelled
        notified_at   TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_timers_status_due ON timers(status, due_at);",
    """
    CREATE TABLE IF NOT EXISTS relationship (
        id                 INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
        interaction_count  INTEGER NOT NULL DEFAULT 0,
        first_seen         TEXT,
        last_seen          TEXT
    );
    """,
]


def get_database_path() -> Path:
    settings.ensure_directories()
    return settings.database_path


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection with sane defaults.

    Usage:
        with get_connection() as conn:
            conn.execute(...)
    """
    db_path = get_database_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_schema() -> None:
    """Create any tables/indexes that don't already exist. Safe to call on
    every application startup."""
    with get_connection() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
    logger.info("Database schema ready at %s", get_database_path())
