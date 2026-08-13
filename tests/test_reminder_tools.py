from datetime import datetime, timedelta

import pytest

from app.core.exceptions import ToolValidationError
from app.reminders import manager
from app.tools import reminder_tools


@pytest.fixture(autouse=True)
def _ready(temp_db):
    manager.ensure_ready()
    yield


def test_create_reminder_tool_returns_plain_dict():
    due = (datetime.now() + timedelta(hours=1)).isoformat()
    result = reminder_tools.create_reminder("Call Mom", due, "DAILY")

    assert isinstance(result, dict)
    assert result["title"] == "Call Mom"
    assert result["repeat_rule"] == "DAILY"
    assert result["status"] == "pending"


def test_create_reminder_tool_rejects_bad_datetime():
    with pytest.raises(ToolValidationError):
        reminder_tools.create_reminder("Call Mom", "not-a-date")


def test_create_reminder_tool_rejects_bad_repeat_rule():
    due = datetime.now().isoformat()
    with pytest.raises(ToolValidationError):
        reminder_tools.create_reminder("Call Mom", due, "YEARLY")


def test_list_reminders_tool_round_trips():
    due = datetime.now().isoformat()
    reminder_tools.create_reminder("A", due)
    reminder_tools.create_reminder("B", due)

    results = reminder_tools.list_reminders()
    assert len(results) == 2
    assert {r["title"] for r in results} == {"A", "B"}


def test_complete_cancel_snooze_delete_round_trip():
    due = datetime.now().isoformat()
    created = reminder_tools.create_reminder("Task", due)
    reminder_id = created["id"]

    snoozed = reminder_tools.snooze_reminder(reminder_id, minutes=5)
    assert snoozed["id"] == reminder_id

    completed = reminder_tools.complete_reminder(reminder_id)
    assert completed["status"] == "completed"

    created2 = reminder_tools.create_reminder("Task2", due)
    cancelled = reminder_tools.cancel_reminder(created2["id"])
    assert cancelled["status"] == "cancelled"

    deleted = reminder_tools.delete_reminder(created2["id"])
    assert deleted["deleted"] is True


def test_operations_on_missing_reminder_raise_tool_validation_error():
    with pytest.raises(ToolValidationError):
        reminder_tools.complete_reminder(99999)
    with pytest.raises(ToolValidationError):
        reminder_tools.cancel_reminder(99999)
    with pytest.raises(ToolValidationError):
        reminder_tools.delete_reminder(99999)
