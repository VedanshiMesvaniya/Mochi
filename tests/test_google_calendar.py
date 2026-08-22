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

    def __init__(self, valid=True, expired=False, refresh_token="rt", raise_on_refresh=False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._raise_on_refresh = raise_on_refresh
        self.refreshed = False

    @classmethod
    def from_authorized_user_file(cls, path, scopes):
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
    run_local_server_result = None
    run_local_server_raises = None

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        cls.last_secrets_path = path
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

    def list(self, **kwargs):
        self.last_kwargs = kwargs
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
