"""
Quick countdown timers — V2 (spec section 25's `timer.start()` tool).

Distinct from reminders: a timer is a short, ad-hoc countdown ("set a timer
for 10 minutes") rather than a scheduled event with a repeat rule. Timers
are still persisted to SQLite (not just in-memory) so a running timer
survives an app restart and still fires when its time comes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.core.exceptions import TimerError
from app.core.logger import get_logger
from app.memory.database import archive_row, get_connection, initialize_schema, list_done

logger = get_logger("mochi.timers")

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


class TimerStatus:
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class Timer:
    id: int
    label: str
    duration_seconds: int
    started_at: datetime
    due_at: datetime
    status: str
    notified_at: Optional[datetime]

    @classmethod
    def from_row(cls, row) -> "Timer":
        return cls(
            id=row["id"],
            label=row["label"],
            duration_seconds=row["duration_seconds"],
            started_at=datetime.strptime(row["started_at"], ISO_FORMAT),
            due_at=datetime.strptime(row["due_at"], ISO_FORMAT),
            status=row["status"],
            notified_at=(
                datetime.strptime(row["notified_at"], ISO_FORMAT)
                if row["notified_at"]
                else None
            ),
        )

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, (self.due_at - datetime.now()).total_seconds())


def ensure_ready() -> None:
    initialize_schema()


def start_timer(duration_seconds: int, label: str = "Timer") -> Timer:
    if duration_seconds <= 0:
        raise TimerError("Timer duration must be a positive number of seconds.")

    now = datetime.now()
    due_at = now + timedelta(seconds=duration_seconds)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO timers (label, duration_seconds, started_at, due_at, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (label or "Timer").strip() or "Timer",
                duration_seconds,
                now.strftime(ISO_FORMAT),
                due_at.strftime(ISO_FORMAT),
                TimerStatus.RUNNING,
            ),
        )
        timer_id = cursor.lastrowid

    logger.info("Started timer #%s '%s' for %ss", timer_id, label, duration_seconds)
    timer = get_timer(timer_id)
    if timer is None:  # pragma: no cover - defensive
        raise TimerError("Failed to read back newly created timer.")
    return timer


def get_timer(timer_id: int) -> Optional[Timer]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
    return Timer.from_row(row) if row else None


def list_active_timers() -> list[Timer]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM timers WHERE status = ? ORDER BY due_at ASC",
            (TimerStatus.RUNNING,),
        ).fetchall()
    return [Timer.from_row(row) for row in rows]


def list_due_timers(as_of: Optional[datetime] = None) -> list[Timer]:
    as_of = as_of or datetime.now()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM timers
            WHERE status = ? AND due_at <= ? AND notified_at IS NULL
            ORDER BY due_at ASC
            """,
            (TimerStatus.RUNNING, as_of.strftime(ISO_FORMAT)),
        ).fetchall()
    return [Timer.from_row(row) for row in rows]


def mark_notified(timer_id: int, when: Optional[datetime] = None) -> None:
    """Fired when a timer's due notification has gone out - marks it done
    and, per the product rule (main table = active only, see
    app/memory/database.py's archive_row()), immediately archives it into
    `timers_done` so it drops out of list_active_timers()."""
    when = when or datetime.now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE timers SET notified_at = ?, status = ? WHERE id = ?",
            (when.strftime(ISO_FORMAT), TimerStatus.DONE, timer_id),
        )
    archive_row("timers", timer_id)


def cancel_timer(timer_id: int) -> None:
    """Cancel a timer and archive it into `timers_done` (status
    'cancelled') - same "main table = active only" rule as
    mark_notified() above."""
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE timers SET status = ? WHERE id = ?", (TimerStatus.CANCELLED, timer_id)
        )
        if result.rowcount == 0:
            raise TimerError(f"Timer #{timer_id} not found.")
    archive_row("timers", timer_id)
    logger.info("Cancelled timer #%s", timer_id)


def list_archived_timers(limit: int = 20) -> list[Timer]:
    """Most-recently-finished (done or cancelled) timers, newest first -
    reads `timers_done`, never `timers`. Used for "what timers finished"
    chat queries - see app/ai/db_glossary.py."""
    return [Timer.from_row(row) for row in list_done("timers", limit=limit)]


def add_time(timer_id: int, extra_seconds: int) -> Timer:
    timer = get_timer(timer_id)
    if timer is None:
        raise TimerError(f"Timer #{timer_id} not found.")
    if timer.status != TimerStatus.RUNNING:
        raise TimerError(f"Timer #{timer_id} is not running.")

    new_due = timer.due_at + timedelta(seconds=extra_seconds)
    with get_connection() as conn:
        conn.execute("UPDATE timers SET due_at = ? WHERE id = ?", (new_due.strftime(ISO_FORMAT), timer_id))
    logger.info("Added %ss to timer #%s", extra_seconds, timer_id)
    return get_timer(timer_id)  # type: ignore[return-value]
