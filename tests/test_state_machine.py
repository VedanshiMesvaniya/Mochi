from app.character.state_machine import CharacterState, CharacterStateMachine, Emotion
from app.core.events import Events, event_bus


def test_set_state_changes_state_and_tracks_history():
    sm = CharacterStateMachine()
    assert sm.state == CharacterState.IDLE
    sm.set_state(CharacterState.WALK_LEFT)
    assert sm.state == CharacterState.WALK_LEFT
    assert sm.previous_state() == CharacterState.IDLE


def test_set_state_noop_when_same_state():
    sm = CharacterStateMachine()
    sm.set_state(CharacterState.IDLE)
    assert sm.previous_state() is None


def test_set_emotion_publishes_event():
    sm = CharacterStateMachine()
    received = []

    def handler(payload):
        received.append(payload)

    event_bus.subscribe(Events.EMOTION_CHANGED, handler)
    try:
        sm.set_emotion(Emotion.HAPPY)
        assert len(received) == 1
        assert received[0]["emotion"] == "happy"
        assert received[0]["animation"] == "happy"
    finally:
        event_bus.unsubscribe(Events.EMOTION_CHANGED, handler)


def test_set_emotion_with_sound_publishes_sound_event():
    sm = CharacterStateMachine()
    received = []

    def handler(payload):
        received.append(payload)

    event_bus.subscribe(Events.SOUND_REQUESTED, handler)
    try:
        sm.set_emotion(Emotion.SURPRISED)
        assert len(received) == 1
        assert received[0]["sound"] == "surprised"
    finally:
        event_bus.unsubscribe(Events.SOUND_REQUESTED, handler)
