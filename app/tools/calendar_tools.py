"""
Calendar tools (spec section 25) - Google Calendar, read-only (V3).

Same shape as reminder_tools.py/task_tools.py/timer_tools.py: plain
JSON-friendly arguments in, plain dicts out, `ToolValidationError` on bad
input. Unlike those, every function here can also raise the more specific
`GoogleCalendarNotConfigured` / `GoogleCalendarNotConnected` (subclasses
of `CalendarError`) - callers that want to show a different message for
"not set up" vs "not signed in" can catch those distinctly; anything that
only wants "did this work" can catch `CalendarError`.

There is deliberately no `create_event`/`update_event`/`delete_event`
here yet - per spec section 23, writes require explicit per-action user
confirmation and land in V4, not this phase.
"""

from __future__ import annotations

from app.calendar import google_calendar
from app.core.exceptions import CalendarError, ToolValidationError
from app.core.logger import get_logger

logger = get_logger("mochi.tools.calendar")

TOOL_SCHEMAS = {
    "connect_google_calendar": {},
    "disconnect_google_calendar": {},
    "get_today_events": {},
    "get_tomorrow_events": {},
    "get_upcoming_events": {"days": "int, optional, default 7"},
    "search_calendar_events": {
        "query": "str, required",
        "days_ahead": "int, optional, default 30",
    },
}


def connect_google_calendar() -> dict:
    """Runs the one-time OAuth browser flow. Blocking (see
    google_calendar.connect()'s docstring for the timeout) - callers must
    invoke this off the UI thread."""
    try:
        google_calendar.connect()
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc
    return {"connected": True}


def disconnect_google_calendar() -> dict:
    removed = google_calendar.disconnect()
    return {"disconnected": True, "had_connection": removed}


def get_today_events() -> list[dict]:
    try:
        return google_calendar.get_today_events()
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc


def get_tomorrow_events() -> list[dict]:
    try:
        return google_calendar.get_tomorrow_events()
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc


def get_upcoming_events(days: int = 7) -> list[dict]:
    if days <= 0:
        raise ToolValidationError("'days' must be a positive integer.")
    try:
        return google_calendar.get_upcoming_events(days=days)
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc


def search_calendar_events(query: str, days_ahead: int = 30) -> list[dict]:
    try:
        return google_calendar.search_events(query, days_ahead=days_ahead)
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc
