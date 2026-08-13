"""
Reminder tools (spec section 25).

This is the execution target for LLM-proposed `create_reminder` / etc.
actions, once Phase 2 (AI) lands (see PROJECT_ARCHITECTURE.md §5 for the
full "LLM proposes, Python disposes" validation flow). It's also directly
usable today - e.g. from a future CLI, or for testing - without any AI
involved.

Every function here:
  * takes plain, JSON-friendly arguments (strings/ints), not `datetime`
    objects, since this is the boundary layer structured LLM output will
    eventually cross
  * returns a plain dict, never a `Reminder` dataclass, for the same reason
  * raises `ToolValidationError` for malformed input - callers (the future
    AI orchestration layer) must catch this and turn it into a safe
    response rather than letting it propagate as a crash

Reminders never require user confirmation before executing (unlike
calendar writes - see spec section 23) because they're purely local and
reversible (cancel/delete).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.exceptions import ReminderError, ToolValidationError
from app.core.logger import get_logger
from app.reminders import manager
from app.reminders.manager import Reminder

logger = get_logger("mochi.tools.reminders")


# Describes the expected shape of each tool's arguments. A future
# `app/ai/structured_output.py` validator can check LLM-proposed actions
# against this before ever calling the function below.
TOOL_SCHEMAS = {
    "create_reminder": {
        "title": "str, required",
        "datetime_iso": "str, required (ISO 8601, e.g. '2026-08-12T19:00:00')",
        "repeat_rule": "str, optional ('DAILY' | 'WEEKLY' | 'WEEKLY:<DAY>' | 'MONTHLY' | null)",
    },
    "list_reminders": {
        "status": "str, optional ('pending' | 'completed' | 'cancelled' | null = all)",
    },
    "complete_reminder": {"reminder_id": "int, required"},
    "cancel_reminder": {"reminder_id": "int, required"},
    "snooze_reminder": {"reminder_id": "int, required", "minutes": "int, optional, default 10"},
    "delete_reminder": {"reminder_id": "int, required"},
}


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ToolValidationError(
            f"Invalid datetime_iso '{value}'. Expected ISO 8601, e.g. "
            "'2026-08-12T19:00:00'."
        ) from exc


def _serialize(reminder: Reminder) -> dict:
    return {
        "id": reminder.id,
        "title": reminder.title,
        "due_at": reminder.due_at.isoformat(),
        "repeat_rule": reminder.repeat_rule,
        "status": reminder.status,
        "created_at": reminder.created_at.isoformat(),
        "completed_at": reminder.completed_at.isoformat() if reminder.completed_at else None,
    }


def create_reminder(
    title: str, datetime_iso: str, repeat_rule: Optional[str] = None
) -> dict:
    due_at = _parse_datetime(datetime_iso)
    try:
        reminder = manager.create_reminder(title, due_at, repeat_rule)
    except ReminderError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(reminder)


def list_reminders(status: Optional[str] = None) -> list[dict]:
    return [_serialize(r) for r in manager.list_reminders(status=status)]


def complete_reminder(reminder_id: int) -> dict:
    try:
        reminder = manager.complete_reminder(reminder_id)
    except ReminderError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(reminder)


def cancel_reminder(reminder_id: int) -> dict:
    try:
        manager.cancel_reminder(reminder_id)
    except ReminderError as exc:
        raise ToolValidationError(str(exc)) from exc
    reminder = manager.get_reminder(reminder_id)
    return _serialize(reminder) if reminder else {"id": reminder_id, "status": "cancelled"}


def snooze_reminder(reminder_id: int, minutes: int = 10) -> dict:
    try:
        reminder = manager.snooze_reminder(reminder_id, minutes=minutes)
    except ReminderError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(reminder)


def delete_reminder(reminder_id: int) -> dict:
    try:
        manager.delete_reminder(reminder_id)
    except ReminderError as exc:
        raise ToolValidationError(str(exc)) from exc
    return {"id": reminder_id, "deleted": True}
