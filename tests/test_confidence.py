from app.ai.confidence import CONFIDENCE_ACT, CONFIDENCE_LOW, Confidence, band


def test_thresholds_are_ordered():
    """CONFIDENCE_LOW must be strictly below CONFIDENCE_ACT, or the
    HIGH/MEDIUM/LOW bands below would overlap or leave a gap."""
    assert 0.0 < CONFIDENCE_LOW < CONFIDENCE_ACT <= 1.0


def test_high_confidence_at_and_above_the_act_threshold():
    assert band(CONFIDENCE_ACT) is Confidence.HIGH
    assert band(0.95) is Confidence.HIGH
    assert band(1.0) is Confidence.HIGH


def test_medium_confidence_between_the_two_thresholds():
    assert band(CONFIDENCE_LOW) is Confidence.MEDIUM
    assert band((CONFIDENCE_LOW + CONFIDENCE_ACT) / 2) is Confidence.MEDIUM
    # Just below the ACT threshold - still MEDIUM, not HIGH.
    assert band(CONFIDENCE_ACT - 0.01) is Confidence.MEDIUM


def test_low_confidence_below_the_low_threshold():
    assert band(0.0) is Confidence.LOW
    assert band(CONFIDENCE_LOW - 0.01) is Confidence.LOW


def test_out_of_range_scores_are_clamped_not_raised():
    """A model that hands back something outside 0.0-1.0 should degrade
    to the nearest valid band, never crash chat."""
    assert band(1.5) is Confidence.HIGH
    assert band(-0.3) is Confidence.LOW
