"""Regression coverage for app/memory/database.py's column-migration path.

Bug context: `due_at` was added to the `tasks` table after the table
already shipped. `CREATE TABLE IF NOT EXISTS` alone does nothing for a
database file that already has a `tasks` table without that column - this
module's `_run_migrations` is what backfills it. This test simulates
exactly that "existing DB from before the column existed" scenario rather
than only ever starting from a schema-less temp file (which would never
have caught the original bug).
"""

from __future__ import annotations

import sqlite3

from app.memory import database


def test_initialize_schema_adds_due_at_to_pre_existing_tasks_table(temp_db):
    db_path = database.get_database_path()

    # Simulate a database created before `due_at` existed: a `tasks` table
    # with the old, narrower column set.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tasks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'open',
            created_at    TEXT NOT NULL,
            completed_at  TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO tasks (title, status, created_at) VALUES (?, ?, ?)",
        ("Pre-existing task", "open", "2026-08-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    database.initialize_schema()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks);")}
    row = conn.execute("SELECT * FROM tasks WHERE title = 'Pre-existing task'").fetchone()
    conn.close()

    assert "due_at" in columns
    # The migration must be additive only - it must never touch existing
    # rows/data, just widen the schema.
    assert row["due_at"] is None
    assert row["title"] == "Pre-existing task"


def test_initialize_schema_migration_is_idempotent(temp_db):
    # Calling it repeatedly (as happens on every app/window startup via
    # ensure_ready()) must never raise "duplicate column" or similar.
    database.initialize_schema()
    database.initialize_schema()
    database.initialize_schema()
