from datetime import datetime, timedelta, timezone

from app.knowledge import freshness


def test_age_hours_computes_elapsed_time():
    now = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    retrieved_at = (now - timedelta(hours=5)).isoformat()
    assert freshness.age_hours(retrieved_at, now=now) == 5.0


def test_age_hours_returns_none_for_unparsable_value():
    assert freshness.age_hours("not-a-date") is None
    assert freshness.age_hours(None) is None


def test_categorize_temporal_thresholds():
    assert freshness.categorize(1, "temporal") == freshness.FRESH
    assert freshness.categorize(10, "temporal") == freshness.RECENT
    assert freshness.categorize(48, "temporal") == freshness.AGING
    assert freshness.categorize(200, "temporal") == freshness.STALE


def test_categorize_knowledge_thresholds_are_much_longer_than_temporal():
    # A 3-day-old "knowledge" doc is still FRESH, unlike a 3-day-old "temporal" one.
    assert freshness.categorize(24 * 3, "knowledge") == freshness.FRESH
    assert freshness.categorize(24 * 3, "temporal") == freshness.AGING
    assert freshness.categorize(24 * 10, "temporal") == freshness.STALE


def test_categorize_expired_overrides_age():
    assert freshness.categorize(1, "temporal", expired=True) == freshness.EXPIRED


def test_categorize_unknown_when_age_missing():
    assert freshness.categorize(None, "knowledge") == freshness.UNKNOWN


def test_retrieval_score_rewards_fresh_high_authority_high_confidence():
    strong = freshness.retrieval_score(
        relevance=1.0, freshness_label=freshness.FRESH, authority="high", confidence=0.9
    )
    weak = freshness.retrieval_score(
        relevance=1.0, freshness_label=freshness.STALE, authority="low", confidence=0.3
    )
    assert strong > weak


def test_retrieval_score_clamps_out_of_range_inputs():
    score = freshness.retrieval_score(
        relevance=5.0, freshness_label=freshness.FRESH, authority="high", confidence=-2.0
    )
    assert 0.0 <= score <= 1.0


def test_retrieval_score_unknown_authority_falls_back_to_low_weight():
    known_low = freshness.retrieval_score(0.5, freshness.RECENT, "low", 0.5)
    unknown = freshness.retrieval_score(0.5, freshness.RECENT, "made-up-tier", 0.5)
    assert unknown == known_low
