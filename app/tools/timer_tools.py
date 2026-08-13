"""
Timer tools (spec section 25) - the future AI execution target for
requests like "set a timer for 10 minutes". See app/tools/reminder_tools.py
for the pattern this follows.
"""

from __future__ import annotations

from app.core.exceptions import TimerError, ToolValidationError
from app.core.logger import get_logger
from app.timers import manager
from app.timers.manager import Timer

logger = get_logger("mochi.tools.timers")

TOOL_SCHEMAS = {
    "start_timer": {
        "duration_seconds": "int, required, must be > 0",
        "label": "str, optional, default 'Timer'",
    },
    "list_active_timers": {},
    "cancel_timer": {"timer_id": "int, required"},
    "add_time": {"timer_id": "int, required", "extra_seconds": "int, required"},
}


def _serialize(timer: Timer) -> dict:
    return {
        "id": timer.id,
        "label": timer.label,
        "duration_seconds": timer.duration_seconds,
        "started_at": timer.started_at.isoformat(),
        "due_at": timer.due_at.isoformat(),
        "status": timer.status,
        "seconds_remaining": timer.seconds_remaining,
    }


def start_timer(duration_seconds: int, label: str = "Timer") -> dict:
    try:
        timer = manager.start_timer(duration_seconds, label)
    except TimerError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(timer)


def list_active_timers() -> list[dict]:
    return [_serialize(t) for t in manager.list_active_timers()]


def cancel_timer(timer_id: int) -> dict:
    try:
        manager.cancel_timer(timer_id)
    except TimerError as exc:
        raise ToolValidationError(str(exc)) from exc
    return {"id": timer_id, "status": "cancelled"}


def add_time(timer_id: int, extra_seconds: int) -> dict:
    try:
        timer = manager.add_time(timer_id, extra_seconds)
    except TimerError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(timer)
