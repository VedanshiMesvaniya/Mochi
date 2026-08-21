from app.core.config import settings
from app.humor import trend_fetcher


def test_paraphrase_strips_source_suffix_and_shortens():
    label = trend_fetcher._paraphrase_headline(
        "Local team wins championship in dramatic finish - Some News Site"
    )
    assert "Some News Site" not in label
    assert label.startswith("something about")


def test_paraphrase_empty_headline_returns_empty_string():
    assert trend_fetcher._paraphrase_headline("   ") == ""


def test_fetch_trends_noop_when_disabled(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "trend_awareness_enabled", False)
    assert trend_fetcher.fetch_trends() == 0
    assert trend_fetcher.get_recent_trends() == []
    assert trend_fetcher.pick_one_trend() is None


def test_fetch_trends_handles_network_failure_gracefully(monkeypatch, temp_db):
    """Feature enabled but network unreachable in the test environment -
    must degrade to 'no trends' rather than raising."""
    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    assert trend_fetcher.fetch_trends() == 0
    assert trend_fetcher.get_recent_trends() == []


def test_get_recent_trends_reads_cache_directly(monkeypatch, temp_db):
    """Bypass the real network fetch and verify the cache read/write path
    (used by chat_engine.py) works once entries exist."""
    from datetime import datetime, timedelta, timezone

    from app.memory.database import get_connection, initialize_schema

    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    initialize_schema()
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO trend_cache (topic_label, fetched_at, expires_at) VALUES (?, ?, ?)",
            (
                "something about a viral dance trend",
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
            ),
        )

    trends = trend_fetcher.get_recent_trends()
    assert trends == ["something about a viral dance trend"]
    assert trend_fetcher.pick_one_trend() == "something about a viral dance trend"


def test_expired_trends_are_excluded(monkeypatch, temp_db):
    from datetime import datetime, timedelta, timezone

    from app.memory.database import get_connection, initialize_schema

    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    initialize_schema()
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO trend_cache (topic_label, fetched_at, expires_at) VALUES (?, ?, ?)",
            (
                "something stale",
                (now - timedelta(hours=10)).isoformat(),
                (now - timedelta(hours=1)).isoformat(),
            ),
        )

    assert trend_fetcher.get_recent_trends() == []
