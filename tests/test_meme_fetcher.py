from app.core.config import settings
from app.humor import meme_fetcher


def test_paraphrase_strips_tag_prefix_and_shortens():
    premise = meme_fetcher._paraphrase_post_title("[OC] when the deploy finally works on Friday")
    assert not premise.startswith("[OC]")
    assert premise.startswith("something about")


def test_paraphrase_empty_title_returns_empty_string():
    assert meme_fetcher._paraphrase_post_title("   ") == ""


def test_fetch_memes_noop_when_disabled(monkeypatch, temp_db):
    monkeypatch.setattr(settings, "trend_awareness_enabled", False)
    assert meme_fetcher.fetch_memes() == 0
    assert meme_fetcher.get_recent_memes() == []
    assert meme_fetcher.pick_one_meme() is None


def test_fetch_memes_handles_network_failure_gracefully(monkeypatch, temp_db):
    """Feature enabled but the network call fails - must degrade to 'no
    meme' rather than raising.

    Regression note: same fix as test_trend_fetcher.py's identically-named
    test - this used to rely on the ambient test environment actually
    having no network access, which broke on any CI runner with real
    internet access (it would successfully fetch real Reddit posts
    instead of hitting the 'unreachable' path this test means to cover).
    Directly raising the exception fetch_memes() is documented to catch
    makes this deterministic everywhere."""
    monkeypatch.setattr(settings, "trend_awareness_enabled", True)

    def _unreachable(limit: int = 8):
        raise meme_fetcher.urllib.error.URLError("simulated: network unreachable")

    monkeypatch.setattr(meme_fetcher, "_fetch_raw_titles", _unreachable)

    assert meme_fetcher.fetch_memes() == 0
    assert meme_fetcher.get_recent_memes() == []


def test_get_recent_memes_reads_cache_directly(monkeypatch, temp_db):
    from datetime import datetime, timedelta, timezone

    from app.memory.database import get_connection, initialize_schema

    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    initialize_schema()
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO meme_cache (premise, fetched_at, expires_at) VALUES (?, ?, ?)",
            (
                "something about Monday morning energy",
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
            ),
        )

    memes = meme_fetcher.get_recent_memes()
    assert memes == ["something about Monday morning energy"]
    assert meme_fetcher.pick_one_meme() == "something about Monday morning energy"


def test_expired_memes_are_excluded(monkeypatch, temp_db):
    from datetime import datetime, timedelta, timezone

    from app.memory.database import get_connection, initialize_schema

    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    initialize_schema()
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO meme_cache (premise, fetched_at, expires_at) VALUES (?, ?, ?)",
            (
                "something stale",
                (now - timedelta(hours=10)).isoformat(),
                (now - timedelta(hours=1)).isoformat(),
            ),
        )

    assert meme_fetcher.get_recent_memes() == []
