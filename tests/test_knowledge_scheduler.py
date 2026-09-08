from app.core.config import settings
from app.knowledge import scheduler, source_manager
from app.knowledge.models import RawFetch, Source, Document

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")


_SOURCE_A = Source(
    key="test:a", kind="rss", target="https://example.com/a",
    category_hint="knowledge", authority="high", frequency_hours=6,
)
_SOURCE_B = Source(
    key="test:b", kind="reddit", target="b",
    category_hint="temporal", authority="low", frequency_hours=3,
)


def test_run_ingestion_cycle_noop_when_disabled(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", False)
    assert scheduler.run_ingestion_cycle() == 0


def test_run_ingestion_cycle_stores_documents_from_enabled_sources(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    monkeypatch.setattr(source_manager, "DEFAULT_SOURCES", (_SOURCE_A,))
    monkeypatch.setattr(scheduler.source_manager, "DEFAULT_SOURCES", (_SOURCE_A,))

    fake_raw = RawFetch(
        source_key=_SOURCE_A.key,
        status="fetched",
        retrieved_at="2026-01-01T00:00:00+00:00",
        content_hash="abc123",
    )
    fake_docs = [
        Document(
            source_key=_SOURCE_A.key,
            url="https://example.com/1",
            title="Doc one",
            content="Content one",
            published_at=None,
            retrieved_at="2026-01-01T00:00:00+00:00",
        )
    ]
    monkeypatch.setattr(scheduler.fetcher, "fetch_source", lambda *a, **k: fake_raw)
    monkeypatch.setattr(scheduler.parser, "parse_fetch", lambda *a, **k: fake_docs)

    stored = scheduler.run_ingestion_cycle()
    assert stored == 1


def test_one_failing_source_does_not_block_others(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    monkeypatch.setattr(scheduler.source_manager, "DEFAULT_SOURCES", (_SOURCE_A, _SOURCE_B))

    good_doc = Document(
        source_key=_SOURCE_B.key,
        url="https://example.com/good",
        title="Good doc",
        content="fine",
        published_at=None,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )

    def _fetch_source(source, **kwargs):
        if source.key == _SOURCE_A.key:
            raise RuntimeError("simulated network explosion")
        return RawFetch(
            source_key=source.key,
            status="fetched",
            retrieved_at="2026-01-01T00:00:00+00:00",
            content_hash="hash-b",
        )

    monkeypatch.setattr(scheduler.fetcher, "fetch_source", _fetch_source)
    monkeypatch.setattr(
        scheduler.parser,
        "parse_fetch",
        lambda raw, source: [good_doc] if source.key == _SOURCE_B.key else [],
    )

    stored = scheduler.run_ingestion_cycle()
    assert stored == 1


def test_not_modified_result_updates_fetch_state_but_stores_nothing(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    monkeypatch.setattr(scheduler.source_manager, "DEFAULT_SOURCES", (_SOURCE_A,))

    not_modified = RawFetch(
        source_key=_SOURCE_A.key,
        status="not_modified",
        retrieved_at="2026-01-01T00:00:00+00:00",
        content_hash="unchanged",
    )
    monkeypatch.setattr(scheduler.fetcher, "fetch_source", lambda *a, **k: not_modified)

    stored = scheduler.run_ingestion_cycle()
    assert stored == 0

    state = scheduler.knowledge_store.get_fetch_state(_SOURCE_A.key)
    assert state is not None
    assert state["content_hash"] == "unchanged"


def test_knowledge_scheduler_interval_derived_from_settings(monkeypatch, qapp):
    monkeypatch.setattr(settings, "web_knowledge_fetch_interval_hours", 2)
    ks = scheduler.KnowledgeScheduler()
    assert ks._poll_interval_ms == 2 * 3600 * 1000
    ks.stop()


def test_knowledge_scheduler_falls_back_to_minimum_interval(monkeypatch, qapp):
    monkeypatch.setattr(settings, "web_knowledge_fetch_interval_hours", 0)
    ks = scheduler.KnowledgeScheduler()
    assert ks._poll_interval_ms == int(scheduler._MIN_POLL_INTERVAL_HOURS * 3600 * 1000)
    ks.stop()


def test_knowledge_scheduler_trigger_cycle_runs_worker_and_reports_stored(
    monkeypatch, temp_db, qapp
):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    monkeypatch.setattr(scheduler.source_manager, "DEFAULT_SOURCES", (_SOURCE_A,))

    fake_raw = RawFetch(
        source_key=_SOURCE_A.key,
        status="fetched",
        retrieved_at="2026-01-01T00:00:00+00:00",
        content_hash="abc123",
    )
    fake_docs = [
        Document(
            source_key=_SOURCE_A.key,
            url="https://example.com/scheduler-1",
            title="Doc one",
            content="Content one",
            published_at=None,
            retrieved_at="2026-01-01T00:00:00+00:00",
        )
    ]
    monkeypatch.setattr(scheduler.fetcher, "fetch_source", lambda *a, **k: fake_raw)
    monkeypatch.setattr(scheduler.parser, "parse_fetch", lambda *a, **k: fake_docs)

    ks = scheduler.KnowledgeScheduler()
    results = []
    ks.trigger_cycle()
    assert ks._worker is not None
    ks._worker.finished_cycle.connect(results.append)
    ks._worker.wait(5000)
    QtCore.QCoreApplication.processEvents()
    assert results == [1]
    ks.stop()


def test_knowledge_scheduler_does_not_start_second_worker_while_running(monkeypatch, qapp):
    monkeypatch.setattr(settings, "web_knowledge_enabled", True)
    ks = scheduler.KnowledgeScheduler()
    ks.trigger_cycle()
    first_worker = ks._worker
    ks.trigger_cycle()  # should be a no-op while first_worker is still running
    assert ks._worker is first_worker
    first_worker.wait(5000)
    ks.stop()
