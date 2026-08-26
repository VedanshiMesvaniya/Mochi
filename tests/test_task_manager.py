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
    # Cancelled tasks are archived out of `tasks` (see cancel_task()'s
    # archive_row() call) - get_task() (main table only) now finds
    # nothing; get_task_any() also checks the archive.
    assert manager.get_task(task.id) is None
    assert manager.get_task_any(task.id).status == "cancelled"


def test_create_task_with_due_date():
    from datetime import datetime

    due = datetime(2026, 9, 1, 17, 0)
    task = manager.create_task("Submit report", due_at=due)
    assert task.due_at == due
    fetched = manager.get_task(task.id)
    assert fetched.due_at == due


def test_create_task_without_due_date_has_none():
    task = manager.create_task("Just a task")
    assert task.due_at is None


def test_set_and_clear_due_date():
    from datetime import datetime

    task = manager.create_task("Buy milk")
    assert task.due_at is None

    due = datetime(2026, 9, 1, 9, 0)
    updated = manager.set_due_date(task.id, due)
    assert updated.due_at == due

    cleared = manager.set_due_date(task.id, None)
    assert cleared.due_at is None


def test_set_due_date_missing_task_raises():
    with pytest.raises(TaskError):
        manager.set_due_date(9999, None)


def test_list_tasks_orders_dated_tasks_first():
    from datetime import datetime

    manager.create_task("No deadline")
    manager.create_task("Later deadline", due_at=datetime(2026, 9, 5, 10, 0))
    manager.create_task("Sooner deadline", due_at=datetime(2026, 9, 1, 10, 0))

    titles = [t.title for t in manager.list_tasks()]
    assert titles == ["Sooner deadline", "Later deadline", "No deadline"]


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
    # Completed tasks are archived out of `tasks` entirely now, so
    # list_tasks(status="done") - which only ever queries the active
    # table - correctly finds none; the done one is read back via
    # list_archived_tasks() instead.
    done_in_main = manager.list_tasks(status="done")
    done_tasks = manager.list_archived_tasks()

    assert [t.id for t in open_tasks] == [t1.id]
    assert done_in_main == []
    assert [t.id for t in done_tasks] == [t2.id]
