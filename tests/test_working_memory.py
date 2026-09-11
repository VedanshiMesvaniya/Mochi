from __future__ import annotations

from app.ai.chat_engine import ChatReaction
from app.ai.working_memory import WorkingMemory
from app.character.state_machine import CharacterState, Emotion


def test_default_working_memory_is_empty():
    wm = WorkingMemory()
    assert wm.is_empty()


def test_working_memory_with_any_field_set_is_not_empty():
    assert not WorkingMemory(pending_action={"kind": "calendar_create"}).is_empty()
    assert not WorkingMemory(reference={"entity_type": "task"}).is_empty()
    assert not WorkingMemory(goal={"kind": "create_reminder"}).is_empty()


def test_from_reaction_bundles_all_three_fields():
    reaction = ChatReaction(
        text="ok",
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        pending_action={"kind": "calendar_create"},
        conversation_state={"entity_type": "task", "entity_id": 1},
        active_goal={"kind": "create_reminder", "awaiting": "time"},
    )
    wm = WorkingMemory.from_reaction(reaction)
    assert wm.pending_action == {"kind": "calendar_create"}
    assert wm.reference == {"entity_type": "task", "entity_id": 1}
    assert wm.goal == {"kind": "create_reminder", "awaiting": "time"}


def test_from_reaction_with_nothing_set_is_empty():
    reaction = ChatReaction(text="hi", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY)
    assert WorkingMemory.from_reaction(reaction).is_empty()


def test_as_kwargs_matches_handle_message_parameter_names():
    wm = WorkingMemory(
        pending_action={"kind": "calendar_create"},
        reference={"entity_type": "task"},
        goal={"kind": "create_reminder"},
    )
    kwargs = wm.as_kwargs()
    assert kwargs == {
        "pending_action": {"kind": "calendar_create"},
        "conversation_state": {"entity_type": "task"},
        "active_goal": {"kind": "create_reminder"},
    }


def test_round_trip_from_reaction_to_kwargs():
    reaction = ChatReaction(
        text="ok",
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        pending_action={"kind": "calendar_create"},
        conversation_state={"entity_type": "task"},
        active_goal={"kind": "create_reminder"},
    )
    kwargs = WorkingMemory.from_reaction(reaction).as_kwargs()
    assert kwargs["pending_action"] == reaction.pending_action
    assert kwargs["conversation_state"] == reaction.conversation_state
    assert kwargs["active_goal"] == reaction.active_goal


def test_working_memory_is_immutable():
    wm = WorkingMemory()
    try:
        wm.pending_action = {"kind": "x"}  # type: ignore[misc]
        assert False, "expected a frozen dataclass to reject attribute assignment"
    except AttributeError:
        pass
