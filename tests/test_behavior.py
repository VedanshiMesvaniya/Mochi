import random

from app.character.behavior import BehaviorEngine
from app.character.state_machine import CharacterState


def test_choose_behavior_returns_valid_state():
    engine = BehaviorEngine()
    engine._rng = random.Random(42)
    result = engine.choose_behavior()
    assert isinstance(result, CharacterState)


def test_next_interval_within_configured_range():
    engine = BehaviorEngine(min_interval_seconds=2.0, max_interval_seconds=5.0)
    engine._rng = random.Random(1)
    for _ in range(20):
        interval = engine.next_interval()
        assert 2.0 <= interval <= 5.0


def test_tick_does_nothing_when_disabled():
    engine = BehaviorEngine(enabled=False)
    calls = []
    engine.tick(lambda state: calls.append(state))
    assert calls == []


def test_tick_applies_a_state_when_enabled():
    engine = BehaviorEngine(enabled=True)
    engine._rng = random.Random(7)
    calls = []
    engine.tick(lambda state: calls.append(state))
    assert len(calls) == 1
    assert isinstance(calls[0], CharacterState)
