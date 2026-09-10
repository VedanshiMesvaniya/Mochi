from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.exceptions import MemoryDisabled
from app.memory import episodic_memory as em


def test_record_event_stores_and_returns_it(temp_db):
    event = em.record_event("Created reminder: call mom", importance=0.5, entities=["call mom"])
    assert event is not None
    assert event.event == "Created reminder: call mom"
    assert event.entities == ["call mom"]


def test_record_event_defaults_context_and_entities(temp_db):
    event = em.record_event("Did a thing")
    assert event.context is None
    assert event.entities == []


def test_recent_events_orders_newest_first(temp_db):
    em.record_event("First thing")
    em.record_event("Second thing")
    em.record_event("Third thing")

    events = em.recent_events(limit=10)
    assert [e.event for e in events] == ["Third thing", "Second thing", "First thing"]


def test_recent_events_respects_limit(temp_db):
    for i in range(5):
        em.record_event(f"Event {i}")
    assert len(em.recent_events(limit=2)) == 2


def test_important_events_filters_by_threshold(temp_db):
    em.record_event("Minor thing", importance=0.3)
    em.record_event("Major thing", importance=0.8)

    important = em.important_events(threshold=0.7)
    assert [e.event for e in important] == ["Major thing"]


def test_record_event_returns_none_when_memory_disabled(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    assert em.record_event("Should not be stored") is None


def test_record_event_never_raises_even_on_bad_input(temp_db, monkeypatch):
    """Best-effort by design (see module docstring) - a failure while
    recording must never propagate, since every real call site is a
    side effect alongside an action that already succeeded."""

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(em, "get_connection", _boom)
    assert em.record_event("Should not raise") is None


def test_recent_events_raises_when_memory_disabled(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    with pytest.raises(MemoryDisabled):
        em.recent_events()


def test_important_events_raises_when_memory_disabled(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    with pytest.raises(MemoryDisabled):
        em.important_events()
