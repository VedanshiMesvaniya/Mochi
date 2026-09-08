from app.core.config import settings
from app.knowledge import context_engine, knowledge_store
from app.knowledge.models import Document, Source

_SOURCE = Source(
    key="rss:test",
    kind="rss",
    target="https://example.com/feed",
    category_hint="knowledge",
    authority="high",
    frequency_hours=6,
)


def test_classify_query_detects_current_signal_words():
    assert context_engine.classify_query("what's trending today?") == "current"
    assert context_engine.classify_query("what's the latest python version") == "current"
    assert context_engine.classify_query("what is happening right now") == "current"


def test_classify_query_defaults_to_stable():
    assert context_engine.classify_query("what is a transformer") == "stable"
    assert context_engine.classify_query("remind me to call mom") == "stable"


def test_classify_query_detects_broadened_temporal_signals():
    assert context_engine.classify_query("who won yesterday's match") == "current"
    assert context_engine.classify_query("what changed in Python 3.15") == "current"
    assert context_engine.classify_query("is version 3.14 released") == "current"
    assert context_engine.classify_query("what happened with OpenAI this week") == "current"
    assert context_engine.classify_query("what's new in 2026") == "current"


def test_format_evidence_includes_provenance(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    doc = Document(
        source_key=_SOURCE.key,
        url="https://example.com/latest-python",
        title="Latest Python release announced",
        content="Latest Python release announced with new features and details",
        published_at="2026-09-01T00:00:00+00:00",
        retrieved_at="2026-09-08T00:00:00+00:00",
    )
    knowledge_store.save_document(doc, _SOURCE)

    context = context_engine.get_web_context("what's the latest python release")
    assert context is not None
    assert "https://example.com/latest-python" in context
    assert "Published: 2026-09-01T00:00:00+00:00" in context
    assert "Retrieved: 2026-09-08T00:00:00+00:00" in context
    assert "Excerpt:" in context


def test_get_web_context_returns_none_when_disabled(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", False)
    assert context_engine.get_web_context("what's trending today") is None


def test_get_web_context_returns_none_for_stable_questions_even_when_enabled(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    assert context_engine.get_web_context("what is a transformer") is None


def test_get_web_context_returns_none_when_nothing_relevant_ingested(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    assert context_engine.get_web_context("what's trending today") is None


def test_get_web_context_returns_formatted_evidence_when_available(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    doc = Document(
        source_key=_SOURCE.key,
        url="https://example.com/latest-python",
        title="Latest Python release announced",
        content="Latest Python release announced with new features",
        published_at=None,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )
    knowledge_store.save_document(doc, _SOURCE)

    context = context_engine.get_web_context("what's the latest python release")
    assert context is not None
    assert "Latest Python release announced" in context
    assert "rss:test" in context


def test_get_web_context_never_raises_on_store_failure(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated store failure")

    monkeypatch.setattr(context_engine, "query_relevant", _boom)
    assert context_engine.get_web_context("what's trending today") is None
