"""
Lightweight local "relationship" tracking (spec section 30).

Deliberately minimal per the "for now no heavy thing" scope: this is just
an interaction counter in SQLite, not any kind of learned model. It exists
so Mochi's personality can shift a little over time - a newly-met Mochi
greets differently than one you've talked to fifty times - without adding
any real complexity or heaviness. This is the whole "over time understand
user behavior" story for now; a real behavioral model would be a much
later phase (spec section 32).
"""

from __future__ import annotations

from datetime import datetime

from app.memory.database import get_connection, initialize_schema

ISO_FORMAT = "%Y-%m-%d %H:%M:%S"


def ensure_ready() -> None:
    initialize_schema()


def record_interaction() -> int:
    """Call once per meaningful interaction (currently: every chat
    message). Returns the updated total interaction count."""
    ensure_ready()
    now = datetime.now().strftime(ISO_FORMAT)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO relationship (id, interaction_count, first_seen, last_seen)
            VALUES (1, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                interaction_count = interaction_count + 1,
                last_seen = excluded.last_seen
            """,
            (now, now),
        )
        row = conn.execute(
            "SELECT interaction_count FROM relationship WHERE id = 1"
        ).fetchone()
    return int(row["interaction_count"]) if row else 1


def get_interaction_count() -> int:
    ensure_ready()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT interaction_count FROM relationship WHERE id = 1"
        ).fetchone()
    return int(row["interaction_count"]) if row else 0


# Coarse familiarity tiers - just enough to flavor a greeting/prompt,
# nothing more granular is needed for this scope.
NEW = "new"
GETTING_TO_KNOW = "getting_to_know"
FAMILIAR = "familiar"


def level_for_count(count: int) -> str:
    if count < 5:
        return NEW
    if count < 25:
        return GETTING_TO_KNOW
    return FAMILIAR
