from datetime import timedelta

import pytest

from app.core.exceptions import TimerError
from app.timers import manager


@pytest.fixture(autouse=True)
def _ready(temp_db):
    manager.ensure_ready()
    yield


def test_start_timer_sets_due_at_correctly():
    timer = manager.start_timer(600, label="Tea")
    assert timer.label == "Tea"
    delta = (timer.due_at - timer.started_at).total_seconds()
    assert 599 <= delta <= 601


def test_start_timer_rejects_non_positive_duration():
    with pytest.raises(TimerError):
        manager.start_timer(0)
    with pytest.raises(TimerError):
        manager.start_timer(-5)


def test_start_timer_defaults_label():
    timer = manager.start_timer(60, label="")
    assert timer.label == "Timer"


def test_list_active_timers_only_running():
    t1 = manager.start_timer(60, "A")
    t2 = manager.start_timer(60, "B")
    manager.cancel_timer(t2.id)

    active = manager.list_active_timers()
    assert [t.id for t in active] == [t1.id]


def test_list_due_timers():
    # Force an already-due timer by starting with 0-ish and immediately
    # backdating via add_time (negative).
    timer = manager.start_timer(5, "Quick")
    manager.add_time(timer.id, -10)  # push due_at into the past

    due = manager.list_due_timers()
    assert [t.id for t in due] == [timer.id]


def test_mark_notified_moves_status_to_done():
    timer = manager.start_timer(5, "Quick")
    manager.add_time(timer.id, -10)
    manager.mark_notified(timer.id)

    # A notified timer is archived out of `timers` immediately (see
    # mark_notified()'s archive_row() call) - get_timer() (main table
    # only) correctly finds nothing; the finished record lives in
    # timers_done instead.
    assert manager.get_timer(timer.id) is None
    fetched = manager.list_archived_timers()[0]
    assert fetched.status == "done"
    assert fetched.notified_at is not None
    assert manager.list_due_timers() == []


def test_cancel_timer():
    timer = manager.start_timer(60, "Cancel me")
    manager.cancel_timer(timer.id)
    # Cancelled timers are archived out of `timers` the same way - see
    # cancel_timer()'s archive_row() call.
    assert manager.get_timer(timer.id) is None
    assert manager.list_archived_timers()[0].status == "cancelled"


def test_cancel_missing_timer_raises():
    with pytest.raises(TimerError):
        manager.cancel_timer(9999)


def test_add_time_extends_due_at():
    timer = manager.start_timer(60, "Extend me")
    original_due = timer.due_at
    updated = manager.add_time(timer.id, 30)
    assert updated.due_at == original_due + timedelta(seconds=30)


def test_add_time_on_missing_timer_raises():
    with pytest.raises(TimerError):
        manager.add_time(9999, 30)


def test_add_time_on_cancelled_timer_raises():
    timer = manager.start_timer(60, "Stopped")
    manager.cancel_timer(timer.id)
    with pytest.raises(TimerError):
        manager.add_time(timer.id, 30)


def test_seconds_remaining_never_negative():
    timer = manager.start_timer(5, "Quick")
    manager.add_time(timer.id, -1000)
    fetched = manager.get_timer(timer.id)
    assert fetched.seconds_remaining == 0.0
