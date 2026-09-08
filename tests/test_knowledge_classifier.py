from datetime import datetime, timezone

from app.knowledge.classifier import classify
from app.knowledge.models import Source

_TEMPORAL_SOURCE = Source(
    key="reddit:test",
    kind="reddit",
    target="test",
    category_hint="temporal",
    authority="low",
    frequency_hours=3,
)
_KNOWLEDGE_SOURCE = Source(
    key="rss:test",
    kind="rss",
    target="https://example.com/feed",
    category_hint="knowledge",
    authority="medium",
    frequency_hours=6,
)


def test_temporal_source_gets_a_ttl():
    retrieved_at = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    category, expires_at = classify(_TEMPORAL_SOURCE, retrieved_at)
    assert category == "temporal"
    assert expires_at is not None
    expires_dt = datetime.fromisoformat(expires_at)
    assert expires_dt > datetime.fromisoformat(retrieved_at)


def test_knowledge_source_has_no_hard_ttl():
    retrieved_at = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    category, expires_at = classify(_KNOWLEDGE_SOURCE, retrieved_at)
    assert category == "knowledge"
    assert expires_at is None


def test_unrecognized_category_hint_defaults_to_knowledge():
    weird_source = Source(
        key="weird:test",
        kind="rss",
        target="https://example.com",
        category_hint="something_else",
        authority="low",
        frequency_hours=1,
    )
    category, expires_at = classify(weird_source, datetime.now(timezone.utc).isoformat())
    assert category == "knowledge"
    assert expires_at is None


def test_classify_survives_unparsable_retrieved_at():
    category, expires_at = classify(_TEMPORAL_SOURCE, "not-a-real-timestamp")
    assert category == "temporal"
    assert expires_at is not None
