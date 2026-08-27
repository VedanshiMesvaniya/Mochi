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
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

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
    """
    -- Archive table for finished reminders (spec follow-up: "after
    -- reminder complete... move data to done table" / "when ask remain
    -- ... it should fetch from main table not done table"). A completed
    -- or cancelled reminder is moved here wholesale (see
    -- app/memory/database.py's archive_row()) rather than just changing
    -- its `status` in-place, so the `reminders` table only ever holds
    -- reminders someone still cares about right now - list_reminders()
    -- with no filter, and every "what's left" question, never has to
    -- know to also exclude old finished rows. Same id as the original
    -- row (not autoincrement) since it's a straight move, not a new
    -- record; `archived_at` is when the move happened.
    CREATE TABLE IF NOT EXISTS reminders_done (
        id            INTEGER PRIMARY KEY,
        title         TEXT NOT NULL,
        due_at        TEXT NOT NULL,
        repeat_rule   TEXT,
        status        TEXT NOT NULL,       -- 'completed' or 'cancelled'
        created_at    TEXT NOT NULL,
        completed_at  TEXT,
        notified_at   TEXT,
        archived_at   TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_reminders_done_archived ON reminders_done(archived_at);",
    """
    -- Archive table for finished tasks - see reminders_done above for the
    -- reasoning. reopen_task() (app/tasks/manager.py) is the one path
    -- that moves a row back out of here into `tasks`.
    CREATE TABLE IF NOT EXISTS tasks_done (
        id            INTEGER PRIMARY KEY,
        title         TEXT NOT NULL,
        status        TEXT NOT NULL,       -- 'done' or 'cancelled'
        created_at    TEXT NOT NULL,
        completed_at  TEXT,
        due_at        TEXT,
        archived_at   TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_done_archived ON tasks_done(archived_at);",
    """
    -- Archive table for finished timers - see reminders_done above for
    -- the reasoning.
    CREATE TABLE IF NOT EXISTS timers_done (
        id                INTEGER PRIMARY KEY,
        label             TEXT NOT NULL,
        duration_seconds  INTEGER NOT NULL,
        started_at        TEXT NOT NULL,
        due_at            TEXT NOT NULL,
        status            TEXT NOT NULL,   -- 'done' or 'cancelled'
        notified_at       TEXT,
        archived_at       TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_timers_done_archived ON timers_done(archived_at);",
    """
    -- Crawled-link store (see app/humor/subreddit_crawler.py). Holds the
    -- result of fetching one URL taken from a source markdown file (e.g.
    -- a curated subreddit list). `url` is UNIQUE on purpose: this table is
    -- append-only by design (there is no delete/archive path for it, unlike
    -- reminders/tasks/timers above) - once a URL has been crawled
    -- successfully it is never crawled again and never removed, so the
    -- crawler only ever has to check "does this url already have a row?"
    -- before doing any network work.
    CREATE TABLE IF NOT EXISTS crawled_sources (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        url           TEXT NOT NULL UNIQUE,
        source_list   TEXT NOT NULL,   -- which source file/list this URL came from
        title         TEXT,
        content       TEXT NOT NULL,   -- raw extracted page content (ground truth)
        summary       TEXT,            -- optional model-cleaned read of `content` (see app/humor/subreddit_crawler.py); NULL if no local model was available at crawl time
        content_hash  TEXT NOT NULL,
        crawled_at    TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_crawled_sources_list ON crawled_sources(source_list);",
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
    # Model-cleaned version of a crawled page's raw extracted text (see
    # app/humor/subreddit_crawler.py) - added after crawled_sources first
    # shipped with only a raw `content` column, so existing rows/DBs need
    # this migrated in rather than recreated.
    ("crawled_sources", "summary", "TEXT"),
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


# Maps a "main" table to its archive ("done") table plus the exact column
# list to copy across - used by archive_row()/restore_row() below. Kept
# here (rather than duplicated in each of reminders/tasks/timers manager.py)
# so there's exactly one place that knows the move logic; each manager
# just calls archive_row("reminders", id) / restore_row("tasks", id).
# Every name on the right is a literal in this file, never user input, but
# still runs through _validate_identifier before interpolation - see that
# function's docstring for why that guard exists even so.
_ARCHIVE_MAP: dict[str, tuple[str, list[str]]] = {
    "reminders": (
        "reminders_done",
        ["id", "title", "due_at", "repeat_rule", "status", "created_at", "completed_at", "notified_at"],
    ),
    "tasks": (
        "tasks_done",
        ["id", "title", "status", "created_at", "completed_at", "due_at"],
    ),
    "timers": (
        "timers_done",
        ["id", "label", "duration_seconds", "started_at", "due_at", "status", "notified_at"],
    ),
}

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


def archive_row(table: str, row_id: int) -> None:
    """Move one row from `table` (reminders|tasks|timers) into its `_done`
    archive table, stamped with `archived_at`. No-op if the row isn't
    there (already archived, or never existed) - callers that already hold
    the row's data in memory (e.g. to build a return value) should read it
    *before* calling this, since it won't exist in `table` afterward.

    This is what keeps the "main" tables holding only active records, per
    the product rule: a "what's remaining" question only ever needs to
    query `table` itself, never filter out finished rows by hand.
    """
    if table not in _ARCHIVE_MAP:
        raise ValueError(f"Unknown archive source table: {table!r}")
    done_table, columns = _ARCHIVE_MAP[table]
    table = _validate_identifier(table, "table")
    done_table = _validate_identifier(done_table, "table")
    columns = [_validate_identifier(c, "column") for c in columns]

    with get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?;", (row_id,)).fetchone()
        if row is None:
            return
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        values = [row[c] for c in columns]
        conn.execute(
            f"INSERT OR REPLACE INTO {done_table} ({col_list}, archived_at) "
            f"VALUES ({placeholders}, ?);",
            (*values, datetime.now().strftime(ISO_FORMAT)),
        )
        conn.execute(f"DELETE FROM {table} WHERE id = ?;", (row_id,))
    logger.info("Archived %s #%s -> %s", table, row_id, done_table)


def restore_row(table: str, row_id: int) -> bool:
    """Move one row back from `table`'s `_done` archive into `table`
    itself (e.g. reopening a task that was already archived as done).
    Returns True if a row was actually moved, False if it wasn't in the
    archive (e.g. it's still active, or never existed)."""
    if table not in _ARCHIVE_MAP:
        raise ValueError(f"Unknown archive source table: {table!r}")
    done_table, columns = _ARCHIVE_MAP[table]
    table = _validate_identifier(table, "table")
    done_table = _validate_identifier(done_table, "table")
    columns = [_validate_identifier(c, "column") for c in columns]

    with get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {done_table} WHERE id = ?;", (row_id,)).fetchone()
        if row is None:
            return False
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        values = [row[c] for c in columns]
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders});",
            values,
        )
        conn.execute(f"DELETE FROM {done_table} WHERE id = ?;", (row_id,))
    logger.info("Restored %s #%s from %s", table, row_id, done_table)
    return True


def get_archived(table: str, row_id: int) -> Optional[sqlite3.Row]:
    """Look up one row by id directly in `table`'s `_done` archive,
    without moving it - used where UI/tools need to know about an
    already-archived record without restoring it (e.g. deleting an
    already-completed task, or showing it read-only)."""
    if table not in _ARCHIVE_MAP:
        raise ValueError(f"Unknown archive source table: {table!r}")
    done_table, _ = _ARCHIVE_MAP[table]
    done_table = _validate_identifier(done_table, "table")
    with get_connection() as conn:
        return conn.execute(f"SELECT * FROM {done_table} WHERE id = ?;", (row_id,)).fetchone()


def list_done(table: str, limit: int = 20) -> list[sqlite3.Row]:
    """Most-recently-archived rows from `table`'s `_done` archive, newest
    first - the read side for "what have I completed" style queries (see
    app/ai/db_glossary.py). Returns raw sqlite3.Row objects; callers map
    these through their own dataclass's from_row()."""
    if table not in _ARCHIVE_MAP:
        raise ValueError(f"Unknown archive source table: {table!r}")
    done_table, _ = _ARCHIVE_MAP[table]
    done_table = _validate_identifier(done_table, "table")
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM {done_table} ORDER BY archived_at DESC LIMIT ?;",
            (limit,),
        ).fetchall()
    return rows


def initialize_schema() -> None:
    """Create any tables/indexes that don't already exist, then apply any
    pending column migrations. Safe to call on every application startup."""
    with get_connection() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        _run_migrations(conn)
    logger.info("Database schema ready at %s", get_database_path())
