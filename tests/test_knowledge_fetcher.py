import urllib.error

from app.knowledge import fetcher
from app.knowledge.models import Source

_REDDIT_SOURCE = Source(
    key="reddit:test",
    kind="reddit",
    target="test",
    category_hint="temporal",
    authority="low",
    frequency_hours=3,
)
_RSS_SOURCE = Source(
    key="rss:test",
    kind="rss",
    target="https://example.com/feed",
    category_hint="knowledge",
    authority="medium",
    frequency_hours=6,
)

_SAMPLE_RSS = (
    b'<?xml version="1.0"?><rss><channel>'
    b"<item><title>Headline One - Example News</title>"
    b"<link>https://example.com/1</link>"
    b"<description>Body one</description>"
    b"<pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate></item>"
    b"</channel></rss>"
)


def test_fetch_reddit_returns_fetched_on_success(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"data": {"children": []}}'

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    raw = fetcher.fetch_reddit(_REDDIT_SOURCE)
    assert raw.status == "fetched"
    assert raw.payload is not None
    assert raw.content_hash is not None


def test_fetch_reddit_not_modified_when_hash_unchanged(monkeypatch):
    payload = b'{"data": {"children": []}}'

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    previous_hash = fetcher._content_hash(payload)
    raw = fetcher.fetch_reddit(_REDDIT_SOURCE, previous_hash=previous_hash)
    assert raw.status == "not_modified"
    assert raw.payload is None


def test_fetch_reddit_handles_network_failure_gracefully(monkeypatch):
    def _unreachable(*args, **kwargs):
        raise urllib.error.URLError("simulated: network unreachable")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", _unreachable)

    raw = fetcher.fetch_reddit(_REDDIT_SOURCE)
    assert raw.status == "failed"
    assert raw.error is not None


def test_fetch_rss_parses_successfully(monkeypatch):
    monkeypatch.setattr(
        fetcher, "_fetch_url", lambda *a, **k: (_SAMPLE_RSS, "etag123", "lastmod", 200)
    )
    raw = fetcher.fetch_rss(_RSS_SOURCE)
    assert raw.status == "fetched"
    assert raw.etag == "etag123"


def test_fetch_rss_not_modified_on_304(monkeypatch):
    monkeypatch.setattr(fetcher, "_fetch_url", lambda *a, **k: (None, "etag123", "lastmod", 304))
    raw = fetcher.fetch_rss(_RSS_SOURCE, previous_etag="etag123")
    assert raw.status == "not_modified"


def test_fetch_rss_malformed_xml_fails_closed(monkeypatch):
    monkeypatch.setattr(fetcher, "_fetch_url", lambda *a, **k: (b"not xml", None, None, 200))
    raw = fetcher.fetch_rss(_RSS_SOURCE)
    assert raw.status == "failed"


def test_fetch_source_dispatches_by_kind(monkeypatch):
    monkeypatch.setattr(fetcher, "fetch_reddit", lambda *a, **k: "reddit-called")
    monkeypatch.setattr(fetcher, "fetch_rss", lambda *a, **k: "rss-called")
    assert fetcher.fetch_source(_REDDIT_SOURCE) == "reddit-called"
    assert fetcher.fetch_source(_RSS_SOURCE) == "rss-called"


def test_fetch_source_unknown_kind_fails_closed():
    weird = Source(
        key="weird:test", kind="carrier_pigeon", target="x",
        category_hint="knowledge", authority="low", frequency_hours=1,
    )
    raw = fetcher.fetch_source(weird)
    assert raw.status == "failed"
