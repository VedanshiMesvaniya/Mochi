import random

from app.character.behavior import BORED_EXPRESSIONS, BehaviorEngine
from app.character.state_machine import CharacterState


def test_tick_does_nothing_when_disabled():
    engine = BehaviorEngine(enabled=False)
    calls = []
    engine.tick(lambda state: calls.append(state))
    assert calls == []


def test_stays_idle_before_interaction():
    """Before mark_interacted() is ever called, Mochi must stay calm/idle
    rather than cycling into happy/bored/sleepy/sleep states on its own."""
    engine = BehaviorEngine(enabled=True)
    engine._rng = random.Random(123)
    calls = []
    for _ in range(50):
        engine.tick(lambda state: calls.append(state))
    assert all(state == CharacterState.IDLE for state in calls)


def test_default_is_happy_right_after_interacting():
    """spec: 'make default happy' - right after any interaction, Mochi's
    resting face should be HAPPY, not a flat neutral IDLE."""
    engine = BehaviorEngine(enabled=True, happy_hold_seconds=15)
    engine.mark_interacted()
    seen = []
    engine.tick(lambda s: seen.append(s))
    assert CharacterState.HAPPY in seen
    assert engine.default_expression() == CharacterState.HAPPY


def test_settles_to_idle_after_happy_hold_expires():
    """spec: '...if not interact for a while then go idle'."""
    engine = BehaviorEngine(
        enabled=True,
        tick_interval_seconds=5,
        happy_hold_seconds=10,
        bored_after_seconds=10_000,
    )
    engine.mark_interacted()
    seen = []
    for _ in range(4):  # 5s, 10s, 15s, 20s idle
        engine.tick(lambda s: seen.append(s))
    assert CharacterState.IDLE in seen
    assert engine.default_expression() == CharacterState.IDLE


def test_mark_interacted_resets_idle_clock():
    engine = BehaviorEngine(
        enabled=True,
        tick_interval_seconds=5,
        happy_hold_seconds=0,
        bored_after_seconds=10,
        sleepy_after_seconds=1000,
        sleep_after_seconds=2000,
    )
    engine.mark_interacted()
    calls = []
    engine.tick(lambda s: calls.append(s))  # 5s idle
    engine.tick(lambda s: calls.append(s))  # 10s idle -> bored
    assert any(s in BORED_EXPRESSIONS for s in calls)

    engine.mark_interacted()  # resets the clock
    calls.clear()
    engine.tick(lambda s: calls.append(s))
    assert not any(s in BORED_EXPRESSIONS for s in calls)


def test_becomes_bored_then_sleepy_then_sleeps_after_enough_inactivity():
    """spec: '...if we don't interact around 5 to 10 minutes it starts to
    get bored and plays with its own faces' - and eventually winds down
    the same way the old sleepy/sleep tiers always did."""
    engine = BehaviorEngine(
        enabled=True,
        tick_interval_seconds=10,
        happy_hold_seconds=0,
        bored_after_seconds=20,
        sleepy_after_seconds=40,
        sleep_after_seconds=60,
    )
    engine.mark_interacted()
    seen = []
    for _ in range(8):
        engine.tick(lambda s: seen.append(s))
    assert any(s in BORED_EXPRESSIONS for s in seen)
    assert CharacterState.SLEEPY in seen
    assert CharacterState.SLEEP in seen

    # Once asleep, further ticks should keep reporting SLEEP, not bounce
    # back to something else on their own.
    seen.clear()
    for _ in range(3):
        engine.tick(lambda s: seen.append(s))
    assert seen and all(s == CharacterState.SLEEP for s in seen)


def test_bored_expressions_never_include_wink():
    """spec: wink is a 'quick expression' (a deliberate reaction / on-demand
    chat command), not something that should just sit there while Mochi is
    being ignored - regression guard on the self-play pool itself."""
    assert CharacterState.WINK not in BORED_EXPRESSIONS


def test_bored_cycles_through_multiple_expressions_without_flickering_every_tick():
    engine = BehaviorEngine(
        enabled=True,
        tick_interval_seconds=2,
        happy_hold_seconds=0,
        bored_after_seconds=0,
        sleepy_after_seconds=10_000,
        sleep_after_seconds=20_000,
        bored_hold_ticks_min=2,
        bored_hold_ticks_max=2,
    )
    engine._rng = random.Random(42)
    engine.mark_interacted()
    seen = []
    for _ in range(20):
        engine.tick(lambda s: seen.append(s))
    # Held for a few ticks each time, so far fewer state *changes* than
    # ticks - not a new random face every single 2s tick.
    assert 0 < len(seen) < 20
    assert all(s in BORED_EXPRESSIONS for s in seen)
    assert len(set(seen)) > 1  # actually varies, not stuck on one face


def test_next_interval_returns_tick_interval():
    engine = BehaviorEngine(tick_interval_seconds=3.0)
    assert engine.next_interval() == 3.0


def test_busy_suppresses_tick_driven_state_changes():
    """Regression test for the bug report: Mochi's THINKING face while a
    chat reply is pending got stomped by HAPPY within ~2s because
    mark_interacted() queues a happy-acknowledgment the very next tick
    would apply. enter_busy() must make tick() a complete no-op (not even
    HAPPY) until exit_busy() is called."""
    engine = BehaviorEngine(enabled=True, tick_interval_seconds=2)
    engine.mark_interacted()  # simulate having interacted already
    engine.enter_busy()

    calls = []
    for _ in range(20):  # would normally cross into bored/sleepy territory
        engine.tick(lambda s: calls.append(s))
    assert calls == []


def test_exit_busy_resumes_normal_ticking_and_flashes_happy():
    engine = BehaviorEngine(enabled=True, tick_interval_seconds=2, happy_hold_seconds=15)
    engine.enter_busy()
    engine.tick(lambda s: None)  # no-op while busy

    engine.exit_busy()
    seen = []
    engine.tick(lambda s: seen.append(s))
    assert CharacterState.HAPPY in seen
    assert engine.busy is False
