"""Tests for app/character/shake_detector.py."""

from __future__ import annotations

from app.character.shake_detector import ShakeDetector


def test_no_shake_from_smooth_single_direction_drag():
    detector = ShakeDetector()
    t = 0.0
    x = 0.0
    fired = False
    for _ in range(20):
        t += 0.05
        x += 15
        fired = fired or detector.feed(t, x)
    assert fired is False


def test_no_shake_from_tiny_jitter():
    detector = ShakeDetector()
    t = 0.0
    x = 100.0
    fired = False
    for i in range(30):
        t += 0.03
        x += 1 if i % 2 == 0 else -1  # well under min_travel_px
        fired = fired or detector.feed(t, x)
    assert fired is False


def test_rapid_back_and_forth_triggers_shake():
    detector = ShakeDetector(reversal_threshold=4, window_seconds=1.0, min_travel_px=12.0)
    t = 0.0
    x = 100.0
    fired = False
    # Oscillate well past the reversal threshold, all within the window.
    for i in range(10):
        t += 0.08
        x += 30 if i % 2 == 0 else -30
        if detector.feed(t, x):
            fired = True
            break
    assert fired is True


def test_shake_does_not_immediately_refire_during_cooldown():
    detector = ShakeDetector(reversal_threshold=4, window_seconds=1.0, min_travel_px=12.0, cooldown_seconds=4.0)
    t = 0.0
    x = 100.0
    fire_count = 0
    for i in range(10):
        t += 0.08
        x += 30 if i % 2 == 0 else -30
        if detector.feed(t, x):
            fire_count += 1
    assert fire_count == 1

    # Keep shaking immediately after - still within cooldown, must not refire.
    for i in range(10):
        t += 0.08
        x += 30 if i % 2 == 0 else -30
        assert detector.feed(t, x) is False


def test_shake_can_fire_again_after_cooldown_expires():
    detector = ShakeDetector(reversal_threshold=4, window_seconds=1.0, min_travel_px=12.0, cooldown_seconds=0.5)
    t = 0.0
    x = 100.0
    first_fire_t = None
    for i in range(10):
        t += 0.08
        x += 30 if i % 2 == 0 else -30
        if detector.feed(t, x) and first_fire_t is None:
            first_fire_t = t
    assert first_fire_t is not None

    t = first_fire_t + 1.0  # comfortably past cooldown_seconds
    x = 100.0
    fired_again = False
    for i in range(10):
        t += 0.08
        x += 30 if i % 2 == 0 else -30
        if detector.feed(t, x):
            fired_again = True
            break
    assert fired_again is True


def test_reset_clears_history_between_drags():
    detector = ShakeDetector(reversal_threshold=4, window_seconds=1.0, min_travel_px=12.0)
    t = 0.0
    x = 100.0
    for i in range(3):  # a few reversals, not enough to trigger yet
        t += 0.08
        x += 30 if i % 2 == 0 else -30
        detector.feed(t, x)

    detector.reset()

    # A single new sample after reset must not immediately count reversals
    # against the stale pre-reset history.
    assert detector.feed(t + 0.1, x + 5) is False
