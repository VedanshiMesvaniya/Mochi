from datetime import datetime, timedelta, timezone

from app.knowledge import knowledge_store
from app.knowledge.models import Document, Source

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
    authority="high",
    frequency_hours=6,
)


def _doc(source: Source, url: str, title: str, content: str, retrieved_at=None) -> Document:
    return Document(
        source_key=source.key,
        url=url,
        title=title,
        content=content,
        published_at=None,
        retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat(),
    )


def test_save_document_inserts_new_document(temp_db):
    doc = _doc(_KNOWLEDGE_SOURCE, "https://example.com/a", "Python 4 release", "Python 4 is out")
    assert knowledge_store.save_document(doc, _KNOWLEDGE_SOURCE) is True


def test_save_document_skips_exact_url_duplicate(temp_db):
    doc = _doc(_KNOWLEDGE_SOURCE, "https://example.com/a", "Title", "Content")
    assert knowledge_store.save_document(doc, _KNOWLEDGE_SOURCE) is True
    assert knowledge_store.save_document(doc, _KNOWLEDGE_SOURCE) is False


def test_fetch_state_round_trip(temp_db):
    assert knowledge_store.get_fetch_state("reddit:test") is None
    knowledge_store.update_fetch_state("reddit:test", "etag1", "lastmod1", "hash1")
    row = knowledge_store.get_fetch_state("reddit:test")
    assert row["etag"] == "etag1"
    assert row["content_hash"] == "hash1"

    knowledge_store.update_fetch_state("reddit:test", "etag2", "lastmod2", "hash2")
    row = knowledge_store.get_fetch_state("reddit:test")
    assert row["etag"] == "etag2"


def test_purge_expired_removes_only_expired_temporal_documents(temp_db):
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    doc = _doc(
        _TEMPORAL_SOURCE, "https://reddit.com/x", "Old trend", "content", retrieved_at=old_time
    )
    knowledge_store.save_document(doc, _TEMPORAL_SOURCE)

    fresh_doc = _doc(
        _KNOWLEDGE_SOURCE, "https://example.com/fresh", "Fresh doc", "content"
    )
    knowledge_store.save_document(fresh_doc, _KNOWLEDGE_SOURCE)

    purged = knowledge_store.purge_expired()
    assert purged == 1

    results = knowledge_store.query_relevant("fresh doc", limit=5)
    assert any(item.claim == "Fresh doc" for item in results)


def test_query_relevant_ranks_fresher_and_higher_authority_first(temp_db):
    old_time = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    stale_doc = _doc(
        _KNOWLEDGE_SOURCE,
        "https://example.com/stale",
        "Python performance improvements",
        "old details about Python performance",
        retrieved_at=old_time,
    )
    fresh_doc = _doc(
        _KNOWLEDGE_SOURCE,
        "https://example.com/fresh",
        "Python performance improvements today",
        "new details about Python performance",
    )
    knowledge_store.save_document(stale_doc, _KNOWLEDGE_SOURCE)
    knowledge_store.save_document(fresh_doc, _KNOWLEDGE_SOURCE)

    results = knowledge_store.query_relevant("python performance", limit=5)
    assert len(results) == 2
    assert results[0].claim == "Python performance improvements today"


def test_query_relevant_returns_empty_for_no_matching_tokens(temp_db):
    doc = _doc(_KNOWLEDGE_SOURCE, "https://example.com/a", "Unrelated topic", "nothing matches")
    knowledge_store.save_document(doc, _KNOWLEDGE_SOURCE)
    assert knowledge_store.query_relevant("completely different query", limit=5) == []


def test_query_relevant_returns_empty_for_blank_query(temp_db):
    assert knowledge_store.query_relevant("   ", limit=5) == []
