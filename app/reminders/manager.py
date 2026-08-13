"""
Local reminder manager (spec section 20).

Fully local, SQLite-backed CRUD for reminders, plus repeat-rule handling.
This module has no dependency on the AI layer - it can be exercised
directly by the reminder UI (Phase 1.5/V1) and, later, by
`app/tools/reminder_tools.py` once natural-language parsing lands (Phase 2).

Repeat rules (kept intentionally simple for V1):
    None          -> one-off reminder
    "DAILY"       -> recurs every day at the same time
    "WEEKLY"      -> recurs every 7 days at the same time
    "WEEKLY:MON"  -> same as WEEKLY, day suffix is informational/validation only
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.core.exceptions import ReminderError
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.reminders")

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"

VALID_REPEAT_PREFIXES = ("DAILY", "WEEKLY", "MONTHLY")
VALID_WEEKDAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}


class ReminderStatus:
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Reminder:
    id: int
    title: str
    due_at: datetime
    repeat_rule: Optional[str]
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    notified_at: Optional[datetime]

    @classmethod
    def from_row(cls, row) -> "Reminder":
        return cls(
            id=row["id"],
            title=row["title"],
            due_at=datetime.strptime(row["due_at"], ISO_FORMAT),
            repeat_rule=row["repeat_rule"],
            status=row["status"],
            created_at=datetime.strptime(row["created_at"], ISO_FORMAT),
            completed_at=(
                datetime.strptime(row["completed_at"], ISO_FORMAT)
                if row["completed_at"]
                else None
            ),
            notified_at=(
                datetime.strptime(row["notified_at"], ISO_FORMAT)
                if row["notified_at"]
                else None
            ),
        )


def _validate_repeat_rule(repeat_rule: Optional[str]) -> None:
    if repeat_rule is None:
        return
    prefix = repeat_rule.split(":", 1)[0]
    if prefix not in VALID_REPEAT_PREFIXES:
        raise ReminderError(
            f"Invalid repeat_rule '{repeat_rule}'. Must start with one of "
            f"{VALID_REPEAT_PREFIXES}."
        )
    if ":" in repeat_rule:
        _, day = repeat_rule.split(":", 1)
        if day.upper() not in VALID_WEEKDAYS:
            raise ReminderError(f"Invalid weekday suffix '{day}' in repeat_rule.")


def compute_next_due(current_due: datetime, repeat_rule: str) -> datetime:
    """Given the just-fired due time and a repeat rule, compute the next
    occurrence. Assumes `repeat_rule` has already been validated."""
    prefix = repeat_rule.split(":", 1)[0]
    if prefix == "DAILY":
        return current_due + timedelta(days=1)
    if prefix == "WEEKLY":
        return current_due + timedelta(days=7)
    if prefix == "MONTHLY":
        # Simple approximation good enough for V1: add ~30 days.
        # A calendar-accurate month rollover can replace this later without
        # touching callers.
        return current_due + timedelta(days=30)
    raise ReminderError(f"Cannot compute next occurrence for '{repeat_rule}'.")


def ensure_ready() -> None:
    initialize_schema()


def create_reminder(
    title: str, due_at: datetime, repeat_rule: Optional[str] = None
) -> Reminder:
    if not title or not title.strip():
        raise ReminderError("Reminder title cannot be empty.")
    _validate_repeat_rule(repeat_rule)

    now = datetime.now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reminders (title, due_at, repeat_rule, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                due_at.strftime(ISO_FORMAT),
                repeat_rule,
                ReminderStatus.PENDING,
                now.strftime(ISO_FORMAT),
            ),
        )
        reminder_id = cursor.lastrowid

    logger.info("Created reminder #%s: '%s' due %s", reminder_id, title, due_at)
    reminder = get_reminder(reminder_id)
    if reminder is None:  # pragma: no cover - defensive, should not happen
        raise ReminderError("Failed to read back newly created reminder.")
    return reminder


def get_reminder(reminder_id: int) -> Optional[Reminder]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
    return Reminder.from_row(row) if row else None


def list_reminders(
    status: Optional[str] = None, upcoming_only: bool = False
) -> list[Reminder]:
    query = "SELECT * FROM reminders"
    clauses = []
    params: list = []

    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if upcoming_only:
        clauses.append("due_at >= ?")
        params.append(datetime.now().strftime(ISO_FORMAT))

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY due_at ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Reminder.from_row(row) for row in rows]


def list_due_reminders(as_of: Optional[datetime] = None) -> list[Reminder]:
    """Reminders that are pending, due, and haven't been notified yet."""
    as_of = as_of or datetime.now()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM reminders
            WHERE status = ? AND due_at <= ? AND notified_at IS NULL
            ORDER BY due_at ASC
            """,
            (ReminderStatus.PENDING, as_of.strftime(ISO_FORMAT)),
        ).fetchall()
    return [Reminder.from_row(row) for row in rows]


def mark_notified(reminder_id: int, when: Optional[datetime] = None) -> None:
    when = when or datetime.now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE reminders SET notified_at = ? WHERE id = ?",
            (when.strftime(ISO_FORMAT), reminder_id),
        )


def complete_reminder(reminder_id: int) -> Reminder:
    """Mark a reminder completed. If it has a repeat_rule, schedule the next
    occurrence as a fresh pending reminder."""
    reminder = get_reminder(reminder_id)
    if reminder is None:
        raise ReminderError(f"Reminder #{reminder_id} not found.")

    now = datetime.now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE reminders SET status = ?, completed_at = ? WHERE id = ?",
            (ReminderStatus.COMPLETED, now.strftime(ISO_FORMAT), reminder_id),
        )

    logger.info("Completed reminder #%s: '%s'", reminder_id, reminder.title)

    if reminder.repeat_rule:
        next_due = compute_next_due(reminder.due_at, reminder.repeat_rule)
        return create_reminder(reminder.title, next_due, reminder.repeat_rule)

    return get_reminder(reminder_id)  # type: ignore[return-value]


def cancel_reminder(reminder_id: int) -> None:
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE reminders SET status = ? WHERE id = ?",
            (ReminderStatus.CANCELLED, reminder_id),
        )
        if result.rowcount == 0:
            raise ReminderError(f"Reminder #{reminder_id} not found.")
    logger.info("Cancelled reminder #%s", reminder_id)


def snooze_reminder(reminder_id: int, minutes: int = 10) -> Reminder:
    reminder = get_reminder(reminder_id)
    if reminder is None:
        raise ReminderError(f"Reminder #{reminder_id} not found.")

    new_due = datetime.now() + timedelta(minutes=minutes)
    with get_connection() as conn:
        conn.execute(
            "UPDATE reminders SET due_at = ?, notified_at = NULL WHERE id = ?",
            (new_due.strftime(ISO_FORMAT), reminder_id),
        )
    logger.info("Snoozed reminder #%s by %s minutes", reminder_id, minutes)
    return get_reminder(reminder_id)  # type: ignore[return-value]


def delete_reminder(reminder_id: int) -> None:
    """Hard delete - distinct from cancel (which keeps history)."""
    with get_connection() as conn:
        result = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        if result.rowcount == 0:
            raise ReminderError(f"Reminder #{reminder_id} not found.")
    logger.info("Deleted reminder #%s", reminder_id)


def update_reminder(
    reminder_id: int,
    title: Optional[str] = None,
    due_at: Optional[datetime] = None,
    repeat_rule: Optional[str] = "__unset__",  # sentinel: distinguish "not passed" from "set to None"
) -> Reminder:
    reminder = get_reminder(reminder_id)
    if reminder is None:
        raise ReminderError(f"Reminder #{reminder_id} not found.")

    new_title = title if title is not None else reminder.title
    new_due_at = due_at if due_at is not None else reminder.due_at
    new_repeat_rule = (
        reminder.repeat_rule if repeat_rule == "__unset__" else repeat_rule
    )
    _validate_repeat_rule(new_repeat_rule)

    with get_connection() as conn:
        conn.execute(
            "UPDATE reminders SET title = ?, due_at = ?, repeat_rule = ? WHERE id = ?",
            (new_title.strip(), new_due_at.strftime(ISO_FORMAT), new_repeat_rule, reminder_id),
        )
    logger.info("Updated reminder #%s", reminder_id)
    return get_reminder(reminder_id)  # type: ignore[return-value]
