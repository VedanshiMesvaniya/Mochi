"""
Tests for app/tasks/google_tasks.py.

Same fake-library pattern as tests/test_google_calendar.py - every test
monkeypatches `_import_google_libraries` with small fakes rather than
requiring the real optional google-auth-oauthlib/google-api-python-client
packages, so the suite still runs with just requirements-dev.txt.
"""

from __future__ import annotations

import json

import pytest

from app.calendar import google_calendar
from app.core.config import settings
from app.core.exceptions import (
    GoogleTasksNotConfigured,
    GoogleTasksNotConnected,
    TaskSyncError,
)
from app.tasks import google_tasks


class _FakeRefreshError(Exception):
    pass


class _FakeHttpError(Exception):
    pass


class _FakeCredentials:
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
        self.scopes = scopes if scopes is not None else [google_tasks.SCOPE_READONLY]
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


class _FakeTasksResource:
    def __init__(self, response, raises=None):
        self._response = response
        self._raises = raises
        self.last_call = None

    def _result(self):
        if self._raises is not None:
            raise self._raises
        return self._response

    def list(self, **kwargs):
        self.last_call = ("list", kwargs)
        return self

    def insert(self, **kwargs):
        self.last_call = ("insert", kwargs)
        return self

    def patch(self, **kwargs):
        self.last_call = ("patch", kwargs)
        return self

    def delete(self, **kwargs):
        self.last_call = ("delete", kwargs)
        return self

    def execute(self):
        return self._result()


class _FakeService:
    def __init__(self, response=None, raises=None):
        self._tasks = _FakeTasksResource(response if response is not None else {"items": []}, raises)

    def tasks(self):
        return self._tasks


def _patch_libraries(monkeypatch, build_fn=None, import_error=False):
    def _fake_import():
        if import_error:
            raise GoogleTasksNotConfigured("Google Tasks support isn't installed.")
        return (
            _FakeCredentials,
            _FakeRequest,
            _FakeRefreshError,
            build_fn or (lambda *a, **k: _FakeService()),
            _FakeHttpError,
        )

    monkeypatch.setattr(google_tasks, "_import_google_libraries", _fake_import)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, temp_config_dir):
    google_tasks._service_cache = None
    _FakeCredentials.next_instance = None
    yield
    google_tasks._service_cache = None


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "google_tasks_enabled", True)


# ---------------------------------------------------------------------------
# required_scopes / is_configured / is_connected
# ---------------------------------------------------------------------------


def test_required_scopes_readonly_by_default(enabled, monkeypatch):
    monkeypatch.setattr(settings, "google_tasks_write_enabled", False)
    assert google_tasks.required_scopes() == [google_tasks.SCOPE_READONLY]


def test_required_scopes_full_when_write_enabled(enabled, monkeypatch):
    monkeypatch.setattr(settings, "google_tasks_write_enabled", True)
    assert google_tasks.required_scopes() == [google_tasks.SCOPE_TASKS]


def test_calendar_required_scopes_includes_tasks_when_enabled(monkeypatch):
    """The whole point: one combined sign-in - Calendar's own
    _required_scopes() should widen to include Tasks once Tasks is
    turned on, so a single connect() flow covers both."""
    monkeypatch.setattr(settings, "google_calendar_write_enabled", False)
    monkeypatch.setattr(settings, "google_tasks_enabled", True)
    monkeypatch.setattr(settings, "google_tasks_write_enabled", False)

    scopes = google_calendar._required_scopes()

    assert google_calendar.SCOPE_READONLY in scopes
    assert google_tasks.SCOPE_READONLY in scopes


def test_calendar_required_scopes_unaffected_when_tasks_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_write_enabled", False)
    monkeypatch.setattr(settings, "google_tasks_enabled", False)

    assert google_calendar._required_scopes() == [google_calendar.SCOPE_READONLY]


def test_is_configured_false_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_tasks_enabled", False)
    assert google_tasks.is_configured() is False


def test_is_configured_true_when_enabled_and_secret_present(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    assert google_tasks.is_configured() is True


def test_is_connected_true_with_sufficient_token(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(valid=True)
    assert google_tasks.is_connected() is True


# ---------------------------------------------------------------------------
# _load_credentials / capability checks
# ---------------------------------------------------------------------------


def test_list_tasks_raises_not_configured_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_tasks_enabled", False)
    with pytest.raises(GoogleTasksNotConfigured):
        google_tasks.list_tasks()


def test_list_tasks_raises_not_connected_without_token(enabled, monkeypatch):
    _patch_libraries(monkeypatch)
    with pytest.raises(GoogleTasksNotConnected):
        google_tasks.list_tasks()


def test_calendar_only_token_insufficient_for_tasks(enabled, monkeypatch):
    """A token that only ever granted Calendar scope (Tasks was turned
    on later) should be recognized as not covering Tasks yet."""
    _patch_libraries(monkeypatch)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=True, scopes=[google_calendar.SCOPE_READONLY]
    )
    with pytest.raises(GoogleTasksNotConnected):
        google_tasks.list_tasks()


def test_readonly_token_insufficient_when_write_required(enabled, monkeypatch):
    monkeypatch.setattr(settings, "google_tasks_write_enabled", True)
    _patch_libraries(monkeypatch)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=True, scopes=[google_tasks.SCOPE_READONLY]
    )
    with pytest.raises(GoogleTasksNotConnected, match="edit permission"):
        google_tasks.list_tasks()


def test_expired_token_refresh_success_saves_new_token(enabled, monkeypatch):
    _patch_libraries(monkeypatch, build_fn=lambda *a, **k: _FakeService(response={"items": []}))
    settings.google_token_path.write_text(json.dumps({"old": True}), encoding="utf-8")
    fake_creds = _FakeCredentials(valid=False, expired=True, refresh_token="rt")
    _FakeCredentials.next_instance = fake_creds

    tasks = google_tasks.list_tasks()

    assert tasks == []
    assert fake_creds.refreshed is True
    assert json.loads(settings.google_token_path.read_text(encoding="utf-8")) == {"fake": True}


# ---------------------------------------------------------------------------
# connect() / disconnect() delegate to google_calendar
# ---------------------------------------------------------------------------


def test_connect_delegates_to_calendar(monkeypatch):
    called = []
    monkeypatch.setattr(google_calendar, "connect", lambda: called.append(True))
    google_tasks.connect()
    assert called == [True]


def test_disconnect_delegates_to_calendar_and_clears_cache(monkeypatch):
    monkeypatch.setattr(google_calendar, "disconnect", lambda: True)
    google_tasks._service_cache = object()
    assert google_tasks.disconnect() is True
    assert google_tasks._service_cache is None


# ---------------------------------------------------------------------------
# list_tasks / find_task / create_task / complete_task / delete_task
# ---------------------------------------------------------------------------


def _connect_valid_token(monkeypatch, build_fn, scopes=None):
    _patch_libraries(monkeypatch, build_fn=build_fn)
    settings.google_token_path.write_text("{}", encoding="utf-8")
    _FakeCredentials.next_instance = _FakeCredentials(
        valid=True, scopes=scopes or [google_tasks.SCOPE_READONLY]
    )


def test_list_tasks_returns_serialized_items(enabled, monkeypatch):
    response = {
        "items": [
            {"id": "t1", "title": "Buy milk", "status": "needsAction"},
            {"id": "t2", "title": "Submit report", "status": "completed", "notes": "for work"},
        ]
    }
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response=response))

    tasks = google_tasks.list_tasks()

    assert tasks[0] == {
        "id": "t1",
        "title": "Buy milk",
        "notes": None,
        "due": None,
        "status": "needsAction",
        "completed": False,
    }
    assert tasks[1]["completed"] is True
    assert tasks[1]["notes"] == "for work"


def test_list_tasks_http_error_wrapped(enabled, monkeypatch):
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(raises=_FakeHttpError("boom")))
    with pytest.raises(TaskSyncError):
        google_tasks.list_tasks()


def test_find_task_rejects_empty_query(enabled, monkeypatch):
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService())
    with pytest.raises(TaskSyncError):
        google_tasks.find_task("   ")


def test_find_task_filters_by_title(enabled, monkeypatch):
    response = {"items": [{"id": "t1", "title": "Buy milk"}, {"id": "t2", "title": "Submit report"}]}
    _connect_valid_token(monkeypatch, lambda *a, **k: _FakeService(response=response))

    matches = google_tasks.find_task("milk")

    assert [m["id"] for m in matches] == ["t1"]


def _connect_write_token(monkeypatch, build_fn):
    monkeypatch.setattr(settings, "google_tasks_write_enabled", True)
    _connect_valid_token(monkeypatch, build_fn, scopes=[google_tasks.SCOPE_TASKS])


def test_create_task_sends_expected_body(enabled, monkeypatch):
    service = _FakeService(response={"id": "t1", "title": "Buy milk", "status": "needsAction"})
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    task = google_tasks.create_task("Buy milk", notes="2%", due_iso_date="2026-08-20")

    assert task["id"] == "t1"
    method, kwargs = service._tasks.last_call
    assert method == "insert"
    assert kwargs["body"]["title"] == "Buy milk"
    assert kwargs["body"]["notes"] == "2%"
    assert kwargs["body"]["due"] == "2026-08-20T00:00:00.000Z"


def test_create_task_rejects_empty_title(enabled, monkeypatch):
    _connect_write_token(monkeypatch, lambda *a, **k: _FakeService())
    with pytest.raises(TaskSyncError):
        google_tasks.create_task("   ")


def test_create_task_http_error_wrapped(enabled, monkeypatch):
    _connect_write_token(monkeypatch, lambda *a, **k: _FakeService(raises=_FakeHttpError("nope")))
    with pytest.raises(TaskSyncError):
        google_tasks.create_task("Buy milk")


def test_complete_task_calls_patch_with_completed_status(enabled, monkeypatch):
    service = _FakeService(response={"id": "t1", "title": "Buy milk", "status": "completed"})
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    task = google_tasks.complete_task("t1")

    method, kwargs = service._tasks.last_call
    assert method == "patch"
    assert kwargs["task"] == "t1"
    assert kwargs["body"] == {"status": "completed"}
    assert task["completed"] is True


def test_delete_task_calls_delete_with_task_id(enabled, monkeypatch):
    service = _FakeService(response={})
    _connect_write_token(monkeypatch, lambda *a, **k: service)

    google_tasks.delete_task("t1")

    method, kwargs = service._tasks.last_call
    assert method == "delete"
    assert kwargs["task"] == "t1"


def test_delete_task_http_error_wrapped(enabled, monkeypatch):
    _connect_write_token(monkeypatch, lambda *a, **k: _FakeService(raises=_FakeHttpError("nope")))
    with pytest.raises(TaskSyncError):
        google_tasks.delete_task("t1")
