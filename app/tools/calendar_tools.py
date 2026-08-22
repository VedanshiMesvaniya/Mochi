"""
Calendar tools (spec section 25) - Google Calendar, read + confirmed writes.

Same shape as reminder_tools.py/task_tools.py/timer_tools.py: plain
JSON-friendly arguments in, plain dicts out, `ToolValidationError` on bad
input. Unlike those, every function here can also raise the more
specific `GoogleCalendarNotConfigured` / `GoogleCalendarNotConnected`
(subclasses of `CalendarError`) - callers that want to show a different
message for "not set up" vs "not signed in" can catch those distinctly;
anything that only wants "did this work" can catch `CalendarError`.

Per spec section 23 ("Never let the small LLM directly modify the
calendar without application-level validation and user confirmation"),
`create_event`/`update_event`/`delete_event` all require an explicit
`confirmed=True` keyword and raise `ConfirmationRequiredError` (see
app/core/exceptions.py) otherwise. This is enforced *here*, at the tool
boundary - not just as a matter of app/ai/chat_engine.py's calling
convention - so a write can never happen without confirmation regardless
of what calls this module. `app/ai/chat_engine.py`'s two-step
propose-then-confirm chat flow is the only current caller that ever
passes `confirmed=True`, and only after the user has explicitly agreed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.calendar import google_calendar
from app.core.exceptions import CalendarError, ConfirmationRequiredError, ToolValidationError
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
    "find_calendar_event": {
        "query": "str, optional",
        "time_of_day": "str, optional ('HH:MM', 24h)",
        "days_ahead": "int, optional, default 2",
    },
    "create_event": {
        "title": "str, required",
        "start_iso": "str, required (ISO 8601)",
        "end_iso": "str, optional (ISO 8601, default: start + 1 hour)",
        "all_day": "bool, optional, default false",
        "location": "str, optional",
        "confirmed": "bool, required - must be true (spec §23: user confirmation)",
    },
    "update_event": {
        "event_id": "str, required",
        "title": "str, optional",
        "start_iso": "str, optional (ISO 8601)",
        "end_iso": "str, optional (ISO 8601)",
        "confirmed": "bool, required - must be true (spec §23: user confirmation)",
    },
    "delete_event": {
        "event_id": "str, required",
        "confirmed": "bool, required - must be true (spec §23: user confirmation)",
    },
}


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ToolValidationError(
            f"Invalid {field} '{value}'. Expected ISO 8601, e.g. "
            "'2026-08-13T17:00:00'."
        ) from exc


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


def find_calendar_event(
    query: Optional[str] = None, time_of_day: Optional[str] = None, days_ahead: int = 2
) -> list[dict]:
    around = None
    if time_of_day:
        try:
            around = datetime.strptime(time_of_day, "%H:%M")
        except ValueError as exc:
            raise ToolValidationError(
                f"Invalid time_of_day '{time_of_day}'. Expected 24h 'HH:MM'."
            ) from exc
    try:
        return google_calendar.find_event(query=query, around=around, days_ahead=days_ahead)
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc


def create_event(
    title: str,
    start_iso: str,
    end_iso: Optional[str] = None,
    all_day: bool = False,
    location: Optional[str] = None,
    confirmed: bool = False,
) -> dict:
    if not confirmed:
        raise ConfirmationRequiredError(
            "Creating a calendar event requires explicit user confirmation."
        )
    if not title or not title.strip():
        raise ToolValidationError("Event title cannot be empty.")
    start = _parse_datetime(start_iso, "start_iso")
    end = _parse_datetime(end_iso, "end_iso") if end_iso is not None else None
    try:
        event = google_calendar.create_event(
            title.strip(), start, end=end, all_day=all_day, location=location
        )
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc
    logger.info("Chat-confirmed calendar event created: '%s'", title)
    return event


def update_event(
    event_id: str,
    title: Optional[str] = None,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    confirmed: bool = False,
) -> dict:
    if not confirmed:
        raise ConfirmationRequiredError(
            "Updating a calendar event requires explicit user confirmation."
        )
    if not event_id:
        raise ToolValidationError("event_id is required.")
    start = _parse_datetime(start_iso, "start_iso") if start_iso is not None else None
    end = _parse_datetime(end_iso, "end_iso") if end_iso is not None else None
    try:
        event = google_calendar.update_event(event_id, title=title, start=start, end=end)
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc
    logger.info("Chat-confirmed calendar event updated: %s", event_id)
    return event


def delete_event(event_id: str, confirmed: bool = False) -> dict:
    if not confirmed:
        raise ConfirmationRequiredError(
            "Deleting a calendar event requires explicit user confirmation."
        )
    if not event_id:
        raise ToolValidationError("event_id is required.")
    try:
        google_calendar.delete_event(event_id)
    except CalendarError as exc:
        raise ToolValidationError(str(exc)) from exc
    logger.info("Chat-confirmed calendar event deleted: %s", event_id)
    return {"event_id": event_id, "deleted": True}
