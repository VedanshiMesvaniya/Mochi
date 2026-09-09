from __future__ import annotations

import pytest

from app.calendar import google_calendar
from app.core.exceptions import GoogleCalendarNotConnected, ToolValidationError
from app.tools import calendar_tools


def test_get_today_events_returns_list(monkeypatch):
    monkeypatch.setattr(google_calendar, "get_today_events", lambda: [{"title": "A"}])
    assert calendar_tools.get_today_events() == [{"title": "A"}]


def test_get_today_events_wraps_calendar_error(monkeypatch):
    def _raise():
        raise GoogleCalendarNotConnected("not connected")

    monkeypatch.setattr(google_calendar, "get_today_events", _raise)
    with pytest.raises(ToolValidationError):
        calendar_tools.get_today_events()


def test_get_tomorrow_events_wraps_calendar_error(monkeypatch):
    def _raise():
        raise GoogleCalendarNotConnected("not connected")

    monkeypatch.setattr(google_calendar, "get_tomorrow_events", _raise)
    with pytest.raises(ToolValidationError):
        calendar_tools.get_tomorrow_events()


def test_get_upcoming_events_rejects_non_positive_days():
    with pytest.raises(ToolValidationError):
        calendar_tools.get_upcoming_events(days=0)
    with pytest.raises(ToolValidationError):
        calendar_tools.get_upcoming_events(days=-3)


def test_get_upcoming_events_passes_days_through(monkeypatch):
    seen = {}

    def _fake(days=7):
        seen["days"] = days
        return []

    monkeypatch.setattr(google_calendar, "get_upcoming_events", _fake)
    calendar_tools.get_upcoming_events(days=14)
    assert seen["days"] == 14


def test_search_calendar_events_wraps_calendar_error(monkeypatch):
    def _raise(query, days_ahead=30):
        raise GoogleCalendarNotConnected("not connected")

    monkeypatch.setattr(google_calendar, "search_events", _raise)
    with pytest.raises(ToolValidationError):
        calendar_tools.search_calendar_events("standup")


def test_connect_google_calendar_success(monkeypatch):
    monkeypatch.setattr(google_calendar, "connect", lambda: None)
    assert calendar_tools.connect_google_calendar() == {"connected": True}


def test_connect_google_calendar_wraps_calendar_error(monkeypatch):
    def _raise():
        raise GoogleCalendarNotConnected("nope")

    monkeypatch.setattr(google_calendar, "connect", _raise)
    with pytest.raises(ToolValidationError):
        calendar_tools.connect_google_calendar()


def test_disconnect_google_calendar_reports_prior_state(monkeypatch):
    monkeypatch.setattr(google_calendar, "disconnect", lambda: True)
    assert calendar_tools.disconnect_google_calendar() == {
        "disconnected": True,
        "had_connection": True,
    }

    monkeypatch.setattr(google_calendar, "disconnect", lambda: False)
    assert calendar_tools.disconnect_google_calendar() == {
        "disconnected": True,
        "had_connection": False,
    }


# ---------------------------------------------------------------------------
# Tool verification (Cognitive Upgrade spec section 12): create_event/
# update_event/delete_event must re-check with google_calendar.get_event()
# before reporting success - a successful API call alone isn't proof the
# change actually took effect.
# ---------------------------------------------------------------------------


def test_create_event_succeeds_when_verification_confirms_it(monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "create_event",
        lambda *a, **kw: {"id": "evt1", "title": "Sync"},
    )
    monkeypatch.setattr(google_calendar, "get_event", lambda event_id: {"id": event_id})

    result = calendar_tools.create_event("Sync", "2026-08-14T17:00:00", confirmed=True)
    assert result["id"] == "evt1"


def test_create_event_fails_when_verification_finds_nothing(monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "create_event",
        lambda *a, **kw: {"id": "evt1", "title": "Sync"},
    )
    monkeypatch.setattr(google_calendar, "get_event", lambda event_id: None)

    with pytest.raises(ToolValidationError):
        calendar_tools.create_event("Sync", "2026-08-14T17:00:00", confirmed=True)


def test_create_event_does_not_punish_success_for_a_flaky_verification_read(monkeypatch):
    """A verification READ failing (rate limit, transient network blip)
    is a separate problem from whether the write itself worked - it
    must not turn a real success into a false failure."""
    monkeypatch.setattr(
        google_calendar,
        "create_event",
        lambda *a, **kw: {"id": "evt1", "title": "Sync"},
    )

    def _raise(event_id):
        raise GoogleCalendarNotConnected("rate limited")

    monkeypatch.setattr(google_calendar, "get_event", _raise)

    result = calendar_tools.create_event("Sync", "2026-08-14T17:00:00", confirmed=True)
    assert result["id"] == "evt1"


def test_delete_event_succeeds_when_verification_confirms_its_gone(monkeypatch):
    monkeypatch.setattr(google_calendar, "delete_event", lambda event_id: None)
    monkeypatch.setattr(google_calendar, "get_event", lambda event_id: None)

    result = calendar_tools.delete_event("evt1", confirmed=True)
    assert result == {"event_id": "evt1", "deleted": True}


def test_delete_event_fails_when_verification_finds_it_still_there(monkeypatch):
    monkeypatch.setattr(google_calendar, "delete_event", lambda event_id: None)
    monkeypatch.setattr(google_calendar, "get_event", lambda event_id: {"id": event_id})

    with pytest.raises(ToolValidationError):
        calendar_tools.delete_event("evt1", confirmed=True)


def test_delete_event_fails_open_on_a_flaky_verification_read(monkeypatch):
    monkeypatch.setattr(google_calendar, "delete_event", lambda event_id: None)

    def _raise(event_id):
        raise GoogleCalendarNotConnected("rate limited")

    monkeypatch.setattr(google_calendar, "get_event", _raise)

    result = calendar_tools.delete_event("evt1", confirmed=True)
    assert result == {"event_id": "evt1", "deleted": True}


def test_update_event_fails_when_verification_finds_nothing(monkeypatch):
    monkeypatch.setattr(
        google_calendar, "update_event", lambda *a, **kw: {"id": "evt1", "title": "New title"}
    )
    monkeypatch.setattr(google_calendar, "get_event", lambda event_id: None)

    with pytest.raises(ToolValidationError):
        calendar_tools.update_event("evt1", title="New title", confirmed=True)


def test_update_event_succeeds_when_verification_confirms_it(monkeypatch):
    monkeypatch.setattr(
        google_calendar, "update_event", lambda *a, **kw: {"id": "evt1", "title": "New title"}
    )
    monkeypatch.setattr(google_calendar, "get_event", lambda event_id: {"id": event_id})

    result = calendar_tools.update_event("evt1", title="New title", confirmed=True)
    assert result["title"] == "New title"
