import pytest

from app.core.exceptions import ToolValidationError
from app.tasks import manager
from app.tools import task_tools


@pytest.fixture(autouse=True)
def _ready(temp_db):
    manager.ensure_ready()
    yield


def test_create_task_tool_returns_plain_dict():
    result = task_tools.create_task("Buy milk")
    assert isinstance(result, dict)
    assert result["title"] == "Buy milk"
    assert result["status"] == "open"


def test_create_task_tool_rejects_empty_title():
    with pytest.raises(ToolValidationError):
        task_tools.create_task("   ")


def test_list_tasks_tool_round_trip():
    task_tools.create_task("A")
    task_tools.create_task("B")
    results = task_tools.list_tasks()
    assert {t["title"] for t in results} == {"A", "B"}


def test_complete_reopen_cancel_delete_round_trip():
    created = task_tools.create_task("Task")
    task_id = created["id"]

    completed = task_tools.complete_task(task_id)
    assert completed["status"] == "done"

    reopened = task_tools.reopen_task(task_id)
    assert reopened["status"] == "open"

    updated = task_tools.update_task(task_id, "Renamed")
    assert updated["title"] == "Renamed"

    created2 = task_tools.create_task("Task2")
    cancelled = task_tools.cancel_task(created2["id"])
    assert cancelled["status"] == "cancelled"

    deleted = task_tools.delete_task(created2["id"])
    assert deleted["deleted"] is True


def test_operations_on_missing_task_raise_tool_validation_error():
    with pytest.raises(ToolValidationError):
        task_tools.complete_task(9999)
    with pytest.raises(ToolValidationError):
        task_tools.reopen_task(9999)
    with pytest.raises(ToolValidationError):
        task_tools.cancel_task(9999)
    with pytest.raises(ToolValidationError):
        task_tools.delete_task(9999)
    with pytest.raises(ToolValidationError):
        task_tools.update_task(9999, "New")
