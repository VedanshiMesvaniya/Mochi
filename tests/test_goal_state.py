from __future__ import annotations

import pytest

from app.ai import goal_state as gs


def test_create_goal_builds_expected_shape():
    goal = gs.create_goal("create_reminder", "time", {"title": "Call mom"})
    assert goal == {
        "kind": "create_reminder",
        "awaiting": "time",
        "known_slots": {"title": "Call mom"},
    }


def test_create_goal_defaults_known_slots_to_empty_dict():
    goal = gs.create_goal("start_timer", "duration")
    assert goal["known_slots"] == {}


def test_create_goal_rejects_unknown_awaiting_slot():
    with pytest.raises(ValueError):
        gs.create_goal("create_reminder", "location", {})


def test_create_goal_copies_known_slots_defensively():
    slots = {"title": "Call mom"}
    goal = gs.create_goal("create_reminder", "time", slots)
    slots["title"] = "mutated"
    assert goal["known_slots"]["title"] == "Call mom"


@pytest.mark.parametrize(
    "intent_name,expected_kind,expected_awaiting",
    [
        ("calendar_create_needs_time", "calendar_create_event", "time"),
        ("create_reminder_needs_time", "create_reminder", "time"),
        ("reschedule_reference_needs_time", "reschedule_reference", "time"),
        ("create_timer_needs_duration", "start_timer", "duration"),
    ],
)
def test_from_intent_name_maps_every_needs_intent(intent_name, expected_kind, expected_awaiting):
    goal = gs.from_intent_name(intent_name, {"title": "Meeting with Devika"})
    assert goal["kind"] == expected_kind
    assert goal["awaiting"] == expected_awaiting
    assert goal["known_slots"] == {"title": "Meeting with Devika"}


def test_from_intent_name_returns_none_for_unrelated_intent():
    assert gs.from_intent_name("create_reminder", {"title": "x"}) is None
    assert gs.from_intent_name("greeting", {}) is None


def test_is_goal_true_only_for_well_formed_goals():
    assert gs.is_goal(gs.create_goal("start_timer", "duration"))
    assert not gs.is_goal(None)
    assert not gs.is_goal({})
    assert not gs.is_goal({"kind": "start_timer"})  # missing "awaiting"
