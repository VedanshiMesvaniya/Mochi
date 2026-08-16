import random

from app.character.behavior import BehaviorEngine
from app.character.state_machine import CharacterState


def test_tick_does_nothing_when_disabled():
    engine = BehaviorEngine(enabled=False)
    calls = []
    engine.tick(lambda state: calls.append(state))
    assert calls == []


def test_stays_idle_before_interaction():
    """Before mark_interacted() is ever called, Mochi must stay calm/idle
    rather than cycling into alert/sleepy/sleep states on its own."""
    engine = BehaviorEngine(enabled=True)
    engine._rng = random.Random(123)
    calls = []
    for _ in range(50):
        engine.tick(lambda state: calls.append(state))
    assert all(state == CharacterState.IDLE for state in calls)


def test_mark_interacted_resets_idle_clock():
    engine = BehaviorEngine(enabled=True, sleepy_after_seconds=10, tick_interval_seconds=5)
    engine.mark_interacted()
    calls = []
    engine.tick(lambda s: calls.append(s))  # 5s idle
    engine.tick(lambda s: calls.append(s))  # 10s idle -> sleepy
    assert CharacterState.SLEEPY in calls

    engine.mark_interacted()  # resets the clock
    calls.clear()
    engine.tick(lambda s: calls.append(s))
    assert CharacterState.SLEEPY not in calls


def test_becomes_sleepy_then_sleeps_after_enough_inactivity():
    engine = BehaviorEngine(
        enabled=True,
        tick_interval_seconds=10,
        attention_after_seconds=1000,  # disable attention pings for this test
        sleepy_after_seconds=20,
        sleep_after_seconds=40,
    )
    engine.mark_interacted()
    seen = []
    for _ in range(6):
        engine.tick(lambda s: seen.append(s))
    assert CharacterState.SLEEPY in seen
    assert CharacterState.SLEEP in seen
    # Once asleep, further ticks should keep reporting SLEEP, not bounce
    # back to something else on their own.
    seen.clear()
    for _ in range(3):
        engine.tick(lambda s: seen.append(s))
    assert seen and all(s == CharacterState.SLEEP for s in seen)


def test_attention_ping_eventually_fires_with_high_probability():
    """Kitten personality: left alone past attention_after_seconds, Mochi
    should occasionally perk up (ALERT) rather than sitting perfectly
    static forever."""
    engine = BehaviorEngine(
        enabled=True,
        tick_interval_seconds=1,
        attention_after_seconds=2,
        sleepy_after_seconds=10_000,
        sleep_after_seconds=20_000,
        attention_ping_chance=0.5,
    )
    engine._rng = random.Random(7)
    engine.mark_interacted()
    seen = []
    for _ in range(30):
        engine.tick(lambda s: seen.append(s))
    assert CharacterState.ALERT in seen


def test_next_interval_returns_tick_interval():
    engine = BehaviorEngine(tick_interval_seconds=3.0)
    assert engine.next_interval() == 3.0
