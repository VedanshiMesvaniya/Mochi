from __future__ import annotations

import pytest

from app.core.exceptions import ConfirmationRequiredError, GoogleTasksNotConnected, ToolValidationError
from app.tasks import google_tasks
from app.tools import google_tasks_tools


def test_get_google_tasks_returns_list(monkeypatch):
    monkeypatch.setattr(google_tasks, "list_tasks", lambda show_completed=False: [{"title": "A"}])
    assert google_tasks_tools.get_google_tasks() == [{"title": "A"}]


def test_get_google_tasks_wraps_task_sync_error(monkeypatch):
    def _raise(show_completed=False):
        raise GoogleTasksNotConnected("not connected")

    monkeypatch.setattr(google_tasks, "list_tasks", _raise)
    with pytest.raises(ToolValidationError):
        google_tasks_tools.get_google_tasks()


def test_find_google_task_rejects_empty_query():
    with pytest.raises(ToolValidationError):
        google_tasks_tools.find_google_task("   ")


def test_find_google_task_wraps_task_sync_error(monkeypatch):
    def _raise(query):
        raise GoogleTasksNotConnected("not connected")

    monkeypatch.setattr(google_tasks, "find_task", _raise)
    with pytest.raises(ToolValidationError):
        google_tasks_tools.find_google_task("milk")


def test_connect_google_tasks_success(monkeypatch):
    monkeypatch.setattr(google_tasks, "connect", lambda: None)
    assert google_tasks_tools.connect_google_tasks() == {"connected": True}


def test_connect_google_tasks_wraps_task_sync_error(monkeypatch):
    def _raise():
        raise GoogleTasksNotConnected("nope")

    monkeypatch.setattr(google_tasks, "connect", _raise)
    with pytest.raises(ToolValidationError):
        google_tasks_tools.connect_google_tasks()


def test_disconnect_google_tasks_reports_prior_state(monkeypatch):
    monkeypatch.setattr(google_tasks, "disconnect", lambda: True)
    assert google_tasks_tools.disconnect_google_tasks() == {
        "disconnected": True,
        "had_connection": True,
    }


def test_create_google_task_requires_confirmation():
    with pytest.raises(ConfirmationRequiredError):
        google_tasks_tools.create_google_task("Buy milk")


def test_create_google_task_rejects_empty_title():
    with pytest.raises(ToolValidationError):
        google_tasks_tools.create_google_task("   ", confirmed=True)


def test_create_google_task_success(monkeypatch):
    monkeypatch.setattr(
        google_tasks,
        "create_task",
        lambda title, notes=None, due_iso_date=None: {"id": "t1", "title": title},
    )
    task = google_tasks_tools.create_google_task("Buy milk", confirmed=True)
    assert task == {"id": "t1", "title": "Buy milk"}


def test_create_google_task_wraps_task_sync_error(monkeypatch):
    def _raise(title, notes=None, due_iso_date=None):
        raise GoogleTasksNotConnected("nope")

    monkeypatch.setattr(google_tasks, "create_task", _raise)
    with pytest.raises(ToolValidationError):
        google_tasks_tools.create_google_task("Buy milk", confirmed=True)


def test_complete_google_task_requires_confirmation():
    with pytest.raises(ConfirmationRequiredError):
        google_tasks_tools.complete_google_task("t1")


def test_complete_google_task_requires_task_id():
    with pytest.raises(ToolValidationError):
        google_tasks_tools.complete_google_task("", confirmed=True)


def test_complete_google_task_success(monkeypatch):
    monkeypatch.setattr(
        google_tasks, "complete_task", lambda task_id: {"id": task_id, "completed": True}
    )
    task = google_tasks_tools.complete_google_task("t1", confirmed=True)
    assert task == {"id": "t1", "completed": True}


def test_delete_google_task_requires_confirmation():
    with pytest.raises(ConfirmationRequiredError):
        google_tasks_tools.delete_google_task("t1")


def test_delete_google_task_requires_task_id():
    with pytest.raises(ToolValidationError):
        google_tasks_tools.delete_google_task("", confirmed=True)


def test_delete_google_task_success(monkeypatch):
    monkeypatch.setattr(google_tasks, "delete_task", lambda task_id: None)
    result = google_tasks_tools.delete_google_task("t1", confirmed=True)
    assert result == {"task_id": "t1", "deleted": True}


def test_delete_google_task_wraps_task_sync_error(monkeypatch):
    def _raise(task_id):
        raise GoogleTasksNotConnected("nope")

    monkeypatch.setattr(google_tasks, "delete_task", _raise)
    with pytest.raises(ToolValidationError):
        google_tasks_tools.delete_google_task("t1", confirmed=True)
