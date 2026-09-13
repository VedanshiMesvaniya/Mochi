from app.ai.initiative import InitiativeSignal, score, should_initiate


def test_high_importance_timely_useful_signal_initiates():
    signal = InitiativeSignal(importance=0.9, timeliness=0.9, user_benefit=0.9)
    assert should_initiate(signal)
    assert score(signal) > 0.6


def test_low_signal_across_the_board_stays_quiet():
    signal = InitiativeSignal(importance=0.1, timeliness=0.1, user_benefit=0.1)
    assert not should_initiate(signal)


def test_recent_interruptions_reduce_the_score():
    calm = InitiativeSignal(importance=0.8, timeliness=0.8, user_benefit=0.8)
    fatigued = InitiativeSignal(
        importance=0.8, timeliness=0.8, user_benefit=0.8, recent_interruptions=3,
    )
    assert score(fatigued) < score(calm)


def test_enough_recent_interruptions_can_flip_the_decision():
    """A signal that would otherwise clear the bar should be suppressed
    once "notification fatigue" (spec section 23) is high enough."""
    signal = InitiativeSignal(
        importance=0.7, timeliness=0.7, user_benefit=0.7, recent_interruptions=5,
    )
    assert not should_initiate(signal)


def test_focus_mode_suppresses_even_a_strong_signal():
    signal = InitiativeSignal(
        importance=0.9, timeliness=0.9, user_benefit=0.9, focus_mode=True,
    )
    assert not should_initiate(signal)


def test_score_never_goes_below_zero_or_above_one():
    heavily_penalized = InitiativeSignal(
        importance=0.0, timeliness=0.0, user_benefit=0.0,
        recent_interruptions=20, focus_mode=True,
    )
    assert score(heavily_penalized) == 0.0

    maxed_out = InitiativeSignal(importance=1.0, timeliness=1.0, user_benefit=1.0)
    assert score(maxed_out) == 1.0


def test_borderline_score_stays_quiet():
    """Ties at the threshold favor speaking up (the caller's inputs are
    already an honest read, not padded to clear a bar) - but anything
    genuinely below it must stay quiet."""
    signal = InitiativeSignal(importance=0.3, timeliness=0.3, user_benefit=0.3)
    assert score(signal) < 0.6
    assert not should_initiate(signal)
