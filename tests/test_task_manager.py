import pytest

from app.core.exceptions import TaskError
from app.tasks import manager


@pytest.fixture(autouse=True)
def _ready(temp_db):
    manager.ensure_ready()
    yield


def test_create_and_get_task():
    task = manager.create_task("Buy milk")
    assert task.status == "open"
    fetched = manager.get_task(task.id)
    assert fetched.title == "Buy milk"


def test_create_task_rejects_empty_title():
    with pytest.raises(TaskError):
        manager.create_task("   ")


def test_complete_and_reopen_task():
    task = manager.create_task("Submit assignment")
    completed = manager.complete_task(task.id)
    assert completed.status == "done"
    assert completed.completed_at is not None

    reopened = manager.reopen_task(task.id)
    assert reopened.status == "open"
    assert reopened.completed_at is None


def test_cancel_task():
    task = manager.create_task("Skip this")
    manager.cancel_task(task.id)
    assert manager.get_task(task.id).status == "cancelled"


def test_cancel_missing_task_raises():
    with pytest.raises(TaskError):
        manager.cancel_task(9999)


def test_delete_task():
    task = manager.create_task("Delete me")
    manager.delete_task(task.id)
    assert manager.get_task(task.id) is None


def test_delete_missing_task_raises():
    with pytest.raises(TaskError):
        manager.delete_task(9999)


def test_update_task_title():
    task = manager.create_task("Original")
    updated = manager.update_task(task.id, "Renamed")
    assert updated.title == "Renamed"


def test_update_missing_task_raises():
    with pytest.raises(TaskError):
        manager.update_task(9999, "New title")


def test_list_tasks_filters_by_status():
    t1 = manager.create_task("Open task")
    t2 = manager.create_task("Done task")
    manager.complete_task(t2.id)

    open_tasks = manager.list_tasks(status="open")
    done_tasks = manager.list_tasks(status="done")

    assert [t.id for t in open_tasks] == [t1.id]
    assert [t.id for t in done_tasks] == [t2.id]
