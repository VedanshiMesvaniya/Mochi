"""
SQLite database layer (spec section 18 - Local Memory).

Single local file at `data/mochi.db`. No server, no cloud. Tables are
created incrementally as each phase needs them - this file currently
defines reminders (V1), plus tasks and timers (V2). Later phases
(conversations, memories, mood_state, relationship_state, calendar_cache)
will extend `SCHEMA_STATEMENTS` rather than replacing this module.
"""

from __future__ import annotations

import re
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
        completed_at  TEXT,
        due_at        TEXT                 -- ISO 8601, local timezone, optional
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
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    );
    """,
    """
    -- Trend-awareness cache (opt-in, settings.trend_awareness_enabled) -
    -- see app/humor/trend_fetcher.py. topic_label is always a short label
    -- Mochi itself paraphrases from a fetched headline - never raw
    -- scraped text. A small rolling cache, replaced wholesale on each
    -- fetch rather than growing unbounded.
    CREATE TABLE IF NOT EXISTS trend_cache (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_label   TEXT NOT NULL,
        fetched_at    TEXT NOT NULL,
        expires_at    TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_trend_cache_expires ON trend_cache(expires_at);",
    """
    -- Meme-awareness cache (opt-in, shares settings.trend_awareness_enabled
    -- with trend_cache above) - see app/humor/meme_fetcher.py. premise is
    -- always a short paraphrase Mochi itself derives from a real meme post
    -- title - never the verbatim title/caption, and never an image.
    CREATE TABLE IF NOT EXISTS meme_cache (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        premise       TEXT NOT NULL,
        fetched_at    TEXT NOT NULL,
        expires_at    TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_meme_cache_expires ON meme_cache(expires_at);",
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


# Columns added to an existing table after it first shipped. CREATE TABLE
# IF NOT EXISTS (above) only helps brand-new databases - anyone with an
# existing data/mochi.db from before a column was added needs an explicit
# ALTER TABLE, guarded by a column-existence check since SQLite has no
# "ADD COLUMN IF NOT EXISTS". Each entry: (table, column, column_def).
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tasks", "due_at", "TEXT"),
]


# SQLite has no way to bind table/column names as query parameters ("?"
# placeholders only work for values), so PRAGMA/ALTER TABLE statements
# below build their SQL with an f-string. `_COLUMN_MIGRATIONS` is a
# hardcoded literal in this file today, so there's no live injection
# path - but an f-string built from an identifier is exactly the shape of
# a SQL-injection bug waiting to happen the moment anyone (or any future
# feature) makes one of these values configurable/derived. Validate every
# identifier against a strict allow-list pattern *before* it's ever
# interpolated, so this stays safe even if that assumption changes later.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, what: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        # Intentionally a hard failure, not a silent skip - a migration
        # entry that doesn't look like a plain identifier must never
        # reach string interpolation into SQL.
        raise ValueError(f"Refusing to use unsafe {what} identifier: {name!r}")
    return name


def _run_migrations(conn: sqlite3.Connection) -> None:
    for table, column, column_def in _COLUMN_MIGRATIONS:
        table = _validate_identifier(table, "table")
        column = _validate_identifier(column, "column")
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table});")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def};")
            logger.info("Migrated database: added %s.%s", table, column)


def initialize_schema() -> None:
    """Create any tables/indexes that don't already exist, then apply any
    pending column migrations. Safe to call on every application startup."""
    with get_connection() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        _run_migrations(conn)
    logger.info("Database schema ready at %s", get_database_path())
