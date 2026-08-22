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
