import pytest

from app.core.exceptions import ToolValidationError
from app.timers import manager
from app.tools import timer_tools


@pytest.fixture(autouse=True)
def _ready(temp_db):
    manager.ensure_ready()
    yield


def test_start_timer_tool_returns_plain_dict():
    result = timer_tools.start_timer(600, "Tea")
    assert isinstance(result, dict)
    assert result["label"] == "Tea"
    assert result["status"] == "running"
    assert "seconds_remaining" in result


def test_start_timer_tool_rejects_bad_duration():
    with pytest.raises(ToolValidationError):
        timer_tools.start_timer(0)
    with pytest.raises(ToolValidationError):
        timer_tools.start_timer(-10)


def test_list_active_timers_tool():
    timer_tools.start_timer(60, "A")
    timer_tools.start_timer(60, "B")
    results = timer_tools.list_active_timers()
    assert {t["label"] for t in results} == {"A", "B"}


def test_cancel_and_add_time_round_trip():
    created = timer_tools.start_timer(60, "Test")
    timer_id = created["id"]

    extended = timer_tools.add_time(timer_id, 30)
    assert extended["duration_seconds"] == 60  # duration field unchanged, due_at shifted

    cancelled = timer_tools.cancel_timer(timer_id)
    assert cancelled["status"] == "cancelled"


def test_operations_on_missing_timer_raise_tool_validation_error():
    with pytest.raises(ToolValidationError):
        timer_tools.cancel_timer(9999)
    with pytest.raises(ToolValidationError):
        timer_tools.add_time(9999, 30)
