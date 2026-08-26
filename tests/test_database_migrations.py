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

import pytest

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


# --- Archive tables (spec follow-up: "after task/reminder/timer
# complete... move data to done table" / "remain ... should fetch from
# main table not done table") -----------------------------------------


def test_archive_row_moves_data_out_of_main_table(temp_db):
    database.initialize_schema()
    now = "2026-08-01T00:00:00"
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (1, 'Buy milk', 'done', ?)",
            (now,),
        )

    database.archive_row("tasks", 1)

    with database.get_connection() as conn:
        main_row = conn.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
        done_row = conn.execute("SELECT * FROM tasks_done WHERE id = 1").fetchone()

    assert main_row is None
    assert done_row is not None
    assert done_row["title"] == "Buy milk"
    assert done_row["archived_at"] is not None


def test_archive_row_is_a_noop_for_a_missing_row(temp_db):
    database.initialize_schema()
    # Must not raise - archiving something that isn't there (already
    # archived, or never existed) is a safe no-op.
    database.archive_row("tasks", 9999)


def test_restore_row_moves_data_back_to_main_table(temp_db):
    database.initialize_schema()
    now = "2026-08-01T00:00:00"
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (1, 'Buy milk', 'done', ?)",
            (now,),
        )
    database.archive_row("tasks", 1)

    restored = database.restore_row("tasks", 1)

    with database.get_connection() as conn:
        main_row = conn.execute("SELECT * FROM tasks WHERE id = 1").fetchone()
        done_row = conn.execute("SELECT * FROM tasks_done WHERE id = 1").fetchone()

    assert restored is True
    assert main_row is not None
    assert main_row["title"] == "Buy milk"
    assert done_row is None


def test_restore_row_returns_false_when_nothing_archived(temp_db):
    database.initialize_schema()
    assert database.restore_row("tasks", 9999) is False


def test_list_done_orders_newest_archived_first(temp_db):
    database.initialize_schema()
    now = "2026-08-01T00:00:00"
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (1, 'First', 'done', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (2, 'Second', 'done', ?)",
            (now,),
        )
    database.archive_row("tasks", 1)
    database.archive_row("tasks", 2)

    rows = database.list_done("tasks")

    assert [r["id"] for r in rows] == [2, 1]


def test_archive_row_rejects_unknown_table(temp_db):
    database.initialize_schema()
    with pytest.raises(ValueError):
        database.archive_row("not_a_real_table", 1)
