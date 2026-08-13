from datetime import datetime, timedelta

import pytest

from app.core.exceptions import ReminderError
from app.reminders import manager


@pytest.fixture(autouse=True)
def _ready(temp_db):
    manager.ensure_ready()
    yield


def test_create_and_get_reminder():
    due = datetime.now() + timedelta(hours=1)
    reminder = manager.create_reminder("Call Mom", due)
    assert reminder.id is not None
    assert reminder.title == "Call Mom"
    assert reminder.status == "pending"

    fetched = manager.get_reminder(reminder.id)
    assert fetched is not None
    assert fetched.title == "Call Mom"


def test_create_reminder_rejects_empty_title():
    with pytest.raises(ReminderError):
        manager.create_reminder("   ", datetime.now())


def test_create_reminder_rejects_invalid_repeat_rule():
    with pytest.raises(ReminderError):
        manager.create_reminder("Test", datetime.now(), repeat_rule="YEARLY")


def test_list_due_reminders_only_returns_past_pending_unnotified():
    past = datetime.now() - timedelta(minutes=1)
    future = datetime.now() + timedelta(hours=1)

    due_reminder = manager.create_reminder("Due now", past)
    manager.create_reminder("Not due yet", future)

    due = manager.list_due_reminders()
    assert len(due) == 1
    assert due[0].id == due_reminder.id


def test_mark_notified_excludes_from_due_list():
    past = datetime.now() - timedelta(minutes=1)
    reminder = manager.create_reminder("Due now", past)

    manager.mark_notified(reminder.id)

    due = manager.list_due_reminders()
    assert due == []


def test_complete_reminder_without_repeat_marks_completed():
    reminder = manager.create_reminder("One-off", datetime.now())
    completed = manager.complete_reminder(reminder.id)
    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_complete_reminder_with_repeat_creates_next_occurrence():
    due = datetime(2026, 1, 1, 9, 0, 0)
    reminder = manager.create_reminder("Daily standup", due, repeat_rule="DAILY")

    next_reminder = manager.complete_reminder(reminder.id)

    original = manager.get_reminder(reminder.id)
    assert original.status == "completed"

    assert next_reminder.id != reminder.id
    assert next_reminder.status == "pending"
    assert next_reminder.due_at == due + timedelta(days=1)
    assert next_reminder.repeat_rule == "DAILY"


def test_cancel_reminder_sets_status():
    reminder = manager.create_reminder("Cancel me", datetime.now())
    manager.cancel_reminder(reminder.id)
    fetched = manager.get_reminder(reminder.id)
    assert fetched.status == "cancelled"


def test_cancel_missing_reminder_raises():
    with pytest.raises(ReminderError):
        manager.cancel_reminder(9999)


def test_snooze_reminder_pushes_due_time_forward():
    past = datetime.now() - timedelta(minutes=5)
    reminder = manager.create_reminder("Snooze me", past)
    manager.mark_notified(reminder.id)

    snoozed = manager.snooze_reminder(reminder.id, minutes=10)

    assert snoozed.due_at > datetime.now()
    assert snoozed.notified_at is None  # so the scheduler will re-notify


def test_delete_reminder_removes_it():
    reminder = manager.create_reminder("Delete me", datetime.now())
    manager.delete_reminder(reminder.id)
    assert manager.get_reminder(reminder.id) is None


def test_delete_missing_reminder_raises():
    with pytest.raises(ReminderError):
        manager.delete_reminder(9999)


def test_update_reminder_changes_fields():
    reminder = manager.create_reminder("Original", datetime.now())
    new_due = datetime.now() + timedelta(days=2)

    updated = manager.update_reminder(reminder.id, title="Updated", due_at=new_due)

    assert updated.title == "Updated"
    # Stored/read back at second precision (ISO_FORMAT has no microseconds).
    assert updated.due_at == new_due.replace(microsecond=0)


def test_update_reminder_can_clear_repeat_rule():
    reminder = manager.create_reminder("Recurs", datetime.now(), repeat_rule="WEEKLY")
    updated = manager.update_reminder(reminder.id, repeat_rule=None)
    assert updated.repeat_rule is None


def test_list_reminders_filters_by_status():
    r1 = manager.create_reminder("Pending one", datetime.now())
    r2 = manager.create_reminder("Will cancel", datetime.now())
    manager.cancel_reminder(r2.id)

    pending = manager.list_reminders(status="pending")
    cancelled = manager.list_reminders(status="cancelled")

    assert [r.id for r in pending] == [r1.id]
    assert [r.id for r in cancelled] == [r2.id]


def test_compute_next_due_variants():
    base = datetime(2026, 3, 1, 8, 0, 0)
    assert manager.compute_next_due(base, "DAILY") == base + timedelta(days=1)
    assert manager.compute_next_due(base, "WEEKLY") == base + timedelta(days=7)
    assert manager.compute_next_due(base, "WEEKLY:MON") == base + timedelta(days=7)
