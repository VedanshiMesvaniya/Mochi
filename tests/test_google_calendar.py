"""
Tests for app/calendar/google_calendar.py.

Deliberately does NOT require the optional google-auth-oauthlib /
google-api-python-client packages to be installed (see that module's
`_import_google_libraries()`) - every test monkeypatches that one seam
with small fakes, the same way test_llm.py stands up a fake local HTTP
server instead of needing a real Ollama running. This keeps the test
suite runnable with just requirements-dev.txt, matching the project's
"nothing else is required" policy, while still exercising the real
control flow (token loading, refresh, error mapping, event listing).
"""

from __future__ import annotations

import json

import pytest

from app.calendar import google_calendar
from app.core.config import settings
from app.core.exceptions import (
    CalendarError,
    GoogleCalendarNotConfigured,
    GoogleCalendarNotConnected,
)


class _FakeRefreshError(Exception):
    pass


class _FakeHttpError(Exception):
    pass


class _FakeCredentials:
    """Stands in for google.oauth2.credentials.Credentials. Tests set
    `_FakeCredentials.next_instance` before calling into google_calendar
    so `from_authorized_user_file` (a classmethod on the real thing) has
    something to return."""

    next_instance = None

    def __init__(
        self,
        valid=True,
        expired=False,
        refresh_token="rt",
        raise_on_refresh=False,
        scopes=None,
    ):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._raise_on_refresh = raise_on_refresh
        # Defaults to read-only - matches what V3's tests (which never
        # touch settings.google_calendar_write_enabled) expect a
        # connected token to have. V4 tests override this explicitly to
        # exercise the read/write capability check.
        self.scopes = scopes if scopes is not None else [google_calendar.SCOPE_READONLY]
        self.refreshed = False

    @classmethod
    def from_authorized_user_file(cls, path, scopes=None):
        if cls.next_instance is None:
            raise ValueError("no fake token configured")
        return cls.next_instance

    def refresh(self, request):
        self.refreshed = True
        if self._raise_on_refresh:
            raise _FakeRefreshError("refresh failed")
        self.valid = True
        self.expired = False

    def to_json(self):
        return json.dumps({"fake": True})


class _FakeRequest:
    pass


class _FakeFlow:
    """Stands in for InstalledAppFlow."""

    last_secrets_path = None
    last_scopes = None
    run_local_server_result = None
    run_local_server_raises = None

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        cls.last_secrets_path = path
        cls.last_scopes = scopes
        return cls()

    def run_local_server(self, port=0, timeout_seconds=None):
        if _FakeFlow.run_local_server_raises is not None:
            raise _FakeFlow.run_local_server_raises
        return _FakeFlow.run_local_server_result or _FakeCredentials()


class _FakeEventsList:
    def __init__(self, response, raises=None):
        self._response = response
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._response


class _FakeEventsResource:
    def __init__(self, response, raises=None):
        self._response = response
        self._raises = raises
        self.last_kwargs = None
        self.last_call = None  # (method_name, kwargs) for insert/patch/delete assertions

    def list(self, **kwargs):
        self.last_kwargs = kwargs
        self.last_call = ("list", kwargs)
        return _FakeEventsList(self._response, self._raises)

    def insert(self, **kwargs):
        self.last_call = ("insert", kwargs)
        return _FakeEventsList(self._response, self._raises)

    def patch(self, **kwargs):
        self.last_call = ("patch", kwargs)
        return _FakeEventsList(self._response, self._raises)

    def delete(self, **kwargs):
        self.last_call = ("delete", kwargs)
        return _FakeEventsList(self._response, self._raises)


class _FakeService:
    def __init__(self, response=None, raises=None):
        self._events = _FakeEventsResource(response or {"items": []}, raises)

    def events(self):
        return self._events


def _patch_libraries(monkeypatch, build_fn=None, import_error=False):
    def _fake_import():
        if import_error:
            raise GoogleCalendarNotConfigured("Google Calendar support isn't installed.")
        return (
            _FakeCredentials,
            _FakeFlow,
            _FakeRequest,
            _FakeRefreshError,
            build_fn or (lambda *a, **k: _FakeService()),
            _FakeHttpError,
        )

    monkeypatch.setattr(google_calendar, "_import_google_libraries", _fake_import)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, temp_config_dir):
    # Every test gets its own config dir (no real config/*.json touched)
    # and a clean service cache/fake-credentials slate.
    google_calendar._service_cache = None
    _FakeCredentials.next_instance = None
    _FakeFlow.last_secrets_path = None
    _FakeFlow.last_scopes = None
    _FakeFlow.run_local_server_result = None
    _FakeFlow.run_local_server_raises = None
    yield
    google_calendar._service_cache = None


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_enabled", True)


# ---------------------------------------------------------------------------
# is_configured / is_connected
# ---------------------------------------------------------------------------


def test_is_configured_false_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_enabled", False)
    assert google_calendar.is_configured() is False


def test_is_configured_false_without_client_secret_file(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    assert google_calendar.is_configured() is False


def test_is_configured_false_without_libraries_installed(enabled, monkeypatch):
    _patch_libraries(monkeypatch, import_error=True)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    assert google_calendar.is_configured() is False


def test_is_configured_true_when_enabled_and_secret_present(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    assert google_calendar.is_configured() is True


def test_is_connected_false_without_token_file(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    assert google_calendar.is_connected() is False


def test_is_connected_true_with_valid_token(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(valid=True)
    assert google_calendar.is_connected() is True


# ---------------------------------------------------------------------------
# _load_credentials / error mapping
# ---------------------------------------------------------------------------


def test_get_today_events_raises_not_configured_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_enabled", False)
    with pytest.raises(GoogleCalendarNotConfigured):
        google_calendar.get_today_events()


def test_get_today_events_raises_not_connected_without_token(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    with pytest.raises(GoogleCalendarNotConnected):
        google_calendar.get_today_events()


def test_expired_token_without_refresh_token_raises_not_connected(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=False, expired=True, refresh_token=None
    )
    with pytest.raises(GoogleCalendarNotConnected):
        google_calendar.get_today_events()


def test_expired_token_refresh_failure_raises_not_connected(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=False, expired=True, refresh_token="rt", raise_on_refresh=True
    )
    with pytest.raises(GoogleCalendarNotConnected):
        google_calendar.get_today_events()


def test_expired_token_refresh_success_saves_new_token(enabled, monkeypatch):
    def build_fn(*a, **k):
        return _FakeService(response={"items": []})

    _patch_libraries(monkeypatch, build_fn=build_fn)
    settings.google_token_path.write_text(json.dumps({"old": True}), encoding="utf-8")
    fake_creds = _FakeCredentials(valid=False, expired=True, refresh_token="rt")
    _FakeCredentials.next_instance = fake_creds

    events = google_calendar.get_today_events()

    assert events == []
    assert fake_creds.refreshed is True
    assert json.loads(settings.google_token_path.read_text(encoding="utf-8")) == {"fake": True}


# ---------------------------------------------------------------------------
# Event listing / serialization
# ---------------------------------------------------------------------------


def _connect_valid_token(monkeypatch, build_fn):
    _patch_libraries(monkeypatch, build_fn=build_fn)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(valid=True)


def test_get_today_events_returns_serialized_events(enabled, monkeypatch):
    response = {
        "items": [
            {
                "id": "abc123",
                "summary": "Team meeting",
                "start": {"dateTime": "2026-08-13T10:00:00-07:00"},
                "end": {"dateTime": "2026-08-13T10:30:00-07:00"},
                "location": "Zoom",
                "htmlLink": "https://calendar.google.com/event?eid=abc123",
            },
            {
                "id": "allday1",
                "summary": "Company holiday",
                "start": {"date": "2026-08-13"},
                "end": {"date": "2026-08-14"},
            },
        ]
    }
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response=response))

    events = google_calendar.get_today_events()

    assert events[0] == {
        "id": "abc123",
        "title": "Team meeting",
        "start": "2026-08-13T10:00:00-07:00",
        "end": "2026-08-13T10:30:00-07:00",
        "all_day": False,
        "location": "Zoom",
        "html_link": "https://calendar.google.com/event?eid=abc123",
    }
    assert events[1]["all_day"] is True
    assert events[1]["title"] == "Company holiday"


def test_get_today_events_empty_when_no_events(enabled, monkeypatch):
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response={"items": []}))
    assert google_calendar.get_today_events() == []


def test_http_error_wrapped_as_calendar_error(enabled, monkeypatch):
    _connect_valid_token(
        monkeypatch,
        lambda *a, **k: _FakeService(raises=_FakeHttpError("boom")),
    )
    with pytest.raises(CalendarError):
        google_calendar.get_today_events()


def test_search_events_rejects_empty_query(enabled, monkeypatch):
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService())
    with pytest.raises(CalendarError):
        google_calendar.search_events("   ")


def test_search_events_passes_query_through(enabled, monkeypatch):
    service = _FakeService(response={"items": []})
    _connect_valid_token(monkeypatch, lambda *a, **k: service)

    google_calendar.search_events("standup")

    assert service._events.last_kwargs["q"] == "standup"


def test_service_is_cached_across_calls(enabled, monkeypatch):
    build_calls = []

    def build_fn(*a, **k):
        build_calls.append(1)
        return _FakeService(response={"items": []})

    _connect_valid_token(monkeypatch, build_fn)

    google_calendar.get_today_events()
    google_calendar.get_today_events()

    assert len(build_calls) == 1


# ---------------------------------------------------------------------------
# connect() / disconnect()
# ---------------------------------------------------------------------------


def test_connect_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_enabled", False)
    with pytest.raises(GoogleCalendarNotConfigured):
        google_calendar.connect()


def test_connect_raises_without_client_secret_file(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    with pytest.raises(GoogleCalendarNotConfigured):
        google_calendar.connect()


def test_connect_runs_flow_and_saves_token(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    _FakeFlow.run_local_server_result = _FakeCredentials(valid=True)

    google_calendar.connect()

    assert settings.google_token_path.exists()
    assert json.loads(settings.google_token_path.read_text(encoding="utf-8")) == {"fake": True}
    assert _FakeFlow.last_secrets_path == str(settings.google_client_secret_path)


def test_connect_wraps_flow_failure_as_calendar_error(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    _FakeFlow.run_local_server_raises = RuntimeError("user closed the browser")

    with pytest.raises(CalendarError):
        google_calendar.connect()

    assert not settings.google_token_path.exists()


def test_disconnect_removes_token_and_reports_whether_one_existed(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    assert google_calendar.disconnect() is False

    settings.google_token_path.write_text("{}", encoding="utf-8")
    assert google_calendar.disconnect() is True
    assert not settings.google_token_path.exists()


def test_disconnect_clears_cached_service(enabled, monkeypatch):
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response={"items": []}))
    google_calendar.get_today_events()
    assert google_calendar._service_cache is not None

    google_calendar.disconnect()

    assert google_calendar._service_cache is None


# ---------------------------------------------------------------------------
# V4: capability levels (read-only vs read+write scope)
# ---------------------------------------------------------------------------


def test_connect_requests_readonly_scope_by_default(enabled, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_write_enabled", False)
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    _FakeFlow.run_local_server_result = _FakeCredentials(valid=True)

    google_calendar.connect()

    assert _FakeFlow.last_scopes == [google_calendar.SCOPE_READONLY]


def test_connect_requests_events_scope_when_write_enabled(enabled, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_write_enabled", True)
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    _FakeFlow.run_local_server_result = _FakeCredentials(valid=True)

    google_calendar.connect()

    assert _FakeFlow.last_scopes == [google_calendar.SCOPE_EVENTS]


def test_readonly_token_insufficient_when_write_required(enabled, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(settings, "google_calendar_write_enabled", True)
    _patch_libraries(monkeypatch)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=True, scopes=[google_calendar.SCOPE_READONLY]
    )

    with pytest.raises(GoogleCalendarNotConnected, match="edit permission"):
        google_calendar.create_event("Sync", dt.datetime(2026, 8, 15, 17, 0))


def test_events_token_sufficient_when_write_required(enabled, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(settings, "google_calendar_write_enabled", True)
    service = _FakeService(response={"id": "abc", "summary": "Sync", "start": {"dateTime": "2026-08-15T17:00:00"}, "end": {"dateTime": "2026-08-15T18:00:00"}})
    _patch_libraries(monkeypatch, build_fn=lambda *a, **k: service)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=True, scopes=[google_calendar.SCOPE_EVENTS]
    )

    event = google_calendar.create_event("Sync", dt.datetime(2026, 8, 15, 17, 0))

    assert event["title"] == "Sync"


def test_events_token_also_covers_reading_when_write_not_required(enabled, monkeypatch):
    """A token connected with write scope should still work fine for plain
    reads once write access is turned back off (calendar.events covers
    viewing too - capability level 2 satisfies a required level of 1)."""
    monkeypatch.setattr(settings, "google_calendar_write_enabled", False)
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response={"items": []}))
    _FakeCredentials.next_instance.scopes = [google_calendar.SCOPE_EVENTS]

    assert google_calendar.get_today_events() == []


# ---------------------------------------------------------------------------
# V4: create_event / update_event / delete_event / find_event
# ---------------------------------------------------------------------------


def _connect_write_token(monkeypatch, build_fn):
    monkeypatch.setattr(settings, "google_calendar_write_enabled", True)
    _patch_libraries(monkeypatch, build_fn=build_fn)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=True, scopes=[google_calendar.SCOPE_EVENTS]
    )


def test_create_event_sends_expected_body(enabled, monkeypatch):
    import datetime as dt

    service = _FakeService(
        response={
            "id": "abc",
            "summary": "Sync",
            "start": {"dateTime": "2026-08-15T17:00:00-07:00"},
            "end": {"dateTime": "2026-08-15T18:00:00-07:00"},
        }
    )
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    event = google_calendar.create_event(
        "Sync", dt.datetime(2026, 8, 15, 17, 0), location="Zoom"
    )

    assert event["id"] == "abc"
    assert event["title"] == "Sync"
    method, kwargs = service._events.last_call
    assert method == "insert"
    assert kwargs["body"]["summary"] == "Sync"
    assert kwargs["body"]["location"] == "Zoom"
    assert kwargs["body"]["start"]["dateTime"].startswith("2026-08-15T17:00:00")
    # end defaults to start + 1 hour when not given
    assert kwargs["body"]["end"]["dateTime"].startswith("2026-08-15T18:00:00")


def test_create_event_all_day_uses_date_not_datetime(enabled, monkeypatch):
    import datetime as dt

    service = _FakeService(response={"id": "abc", "summary": "Holiday", "start": {"date": "2026-08-15"}, "end": {"date": "2026-08-16"}})
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    google_calendar.create_event(
        "Holiday", dt.datetime(2026, 8, 15), all_day=True
    )

    method, kwargs = service._events.last_call
    assert kwargs["body"]["start"] == {"date": "2026-08-15"}


def test_create_event_http_error_wrapped(enabled, monkeypatch):
    import datetime as dt

    service = _FakeService(raises=_FakeHttpError("nope"))
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    with pytest.raises(CalendarError):
        google_calendar.create_event("Sync", dt.datetime(2026, 8, 15, 17, 0))


def test_update_event_only_sends_given_fields(enabled, monkeypatch):
    service = _FakeService(response={"id": "evt1", "summary": "New title", "start": {}, "end": {}})
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    google_calendar.update_event("evt1", title="New title")

    method, kwargs = service._events.last_call
    assert method == "patch"
    assert kwargs["eventId"] == "evt1"
    assert kwargs["body"] == {"summary": "New title"}


def test_update_event_with_nothing_to_update_raises(enabled, monkeypatch):
    _connect_write_token(monkeypatch, lambda *a, **k: _FakeService())
    with pytest.raises(CalendarError):
        google_calendar.update_event("evt1")


def test_delete_event_calls_delete_with_event_id(enabled, monkeypatch):
    service = _FakeService(response={})
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    google_calendar.delete_event("evt1")

    method, kwargs = service._events.last_call
    assert method == "delete"
    assert kwargs["eventId"] == "evt1"


def test_delete_event_http_error_wrapped(enabled, monkeypatch):
    service = _FakeService(raises=_FakeHttpError("nope"))
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    with pytest.raises(CalendarError):
        google_calendar.delete_event("evt1")


def test_find_event_filters_by_time_of_day(enabled, monkeypatch):
    import datetime as dt

    response = {
        "items": [
            {"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-08-15T09:00:00-07:00"}, "end": {"dateTime": "2026-08-15T09:15:00-07:00"}},
            {"id": "e2", "summary": "1:1", "start": {"dateTime": "2026-08-15T17:10:00-07:00"}, "end": {"dateTime": "2026-08-15T17:40:00-07:00"}},
            {"id": "e3", "summary": "Lunch", "start": {"dateTime": "2026-08-15T12:00:00-07:00"}, "end": {"dateTime": "2026-08-15T13:00:00-07:00"}},
        ]
    }
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response=response))

    matches = google_calendar.find_event(around=dt.datetime(2026, 1, 1, 17, 0))

    assert [m["id"] for m in matches] == ["e2"]


def test_find_event_without_around_returns_everything_in_window(enabled, monkeypatch):
    response = {"items": [{"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-08-15T09:00:00-07:00"}, "end": {}}]}
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response=response))

    matches = google_calendar.find_event()

    assert len(matches) == 1


def test_find_event_ignores_all_day_events_when_matching_time(enabled, monkeypatch):
    import datetime as dt

    response = {"items": [{"id": "e1", "summary": "Holiday", "start": {"date": "2026-08-15"}, "end": {"date": "2026-08-16"}}]}
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response=response))

    matches = google_calendar.find_event(around=dt.datetime(2026, 1, 1, 17, 0))

    assert matches == []
