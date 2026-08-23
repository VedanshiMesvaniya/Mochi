"""
Tiny local key-value settings store, SQLite-backed (same file as
everything else - see app/memory/database.py). This is for small persisted
*user preferences* that aren't worth their own table - not for app
configuration (that stays in .env, see app/core/config.py).

Note: this used to also hold the selected glow-color theme
(KEY_GLOW_THEME). That setting is gone - Mochi now has one unified
casing look, with the glow color itself changing per expression instead
of being a user-selectable palette (see app/character/theme.py).
"""

from __future__ import annotations

from app.memory.database import get_connection, initialize_schema


def ensure_ready() -> None:
    initialize_schema()


def get_setting(key: str, default: str | None = None) -> str | None:
    ensure_ready()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row is not None else default


def set_setting(key: str, value: str) -> None:
    ensure_ready()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
