from app.knowledge import dedup, knowledge_store
from app.knowledge.models import Document, Source
from app.memory.database import get_connection, initialize_schema

_SOURCE = Source(
    key="rss:test",
    kind="rss",
    target="https://example.com/feed",
    category_hint="knowledge",
    authority="high",
    frequency_hours=6,
)


def test_content_hash_is_deterministic():
    assert dedup.content_hash("hello world") == dedup.content_hash("hello world")
    assert dedup.content_hash("hello world") != dedup.content_hash("goodbye world")


def test_is_duplicate_true_for_matching_url(temp_db):
    initialize_schema()
    doc = Document(
        source_key=_SOURCE.key,
        url="https://example.com/a",
        title="T",
        content="C",
        published_at=None,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )
    knowledge_store.save_document(doc, _SOURCE)
    with get_connection() as conn:
        assert dedup.is_duplicate(conn, "https://example.com/a", "irrelevant-hash", _SOURCE.key)


def test_is_duplicate_true_for_matching_content_hash_same_source(temp_db):
    initialize_schema()
    doc = Document(
        source_key=_SOURCE.key,
        url="https://example.com/a",
        title="T",
        content="Same content",
        published_at=None,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )
    knowledge_store.save_document(doc, _SOURCE)
    content_hash = dedup.content_hash("Same content")
    with get_connection() as conn:
        assert dedup.is_duplicate(conn, "https://example.com/different-url", content_hash, _SOURCE.key)


def test_is_duplicate_false_for_new_url_and_content(temp_db):
    initialize_schema()
    with get_connection() as conn:
        assert not dedup.is_duplicate(conn, "https://example.com/new", "some-hash", _SOURCE.key)
