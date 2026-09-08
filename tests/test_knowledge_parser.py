import json

from app.knowledge import parser
from app.knowledge.models import RawFetch, Source

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


def _reddit_payload():
    return json.dumps(
        {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Something interesting happened",
                            "permalink": "/r/test/comments/abc123/something/",
                            "selftext": "More detail here",
                            "score": 42,
                            "created_utc": 1700000000,
                        }
                    },
                    {"data": {"title": "", "permalink": "/r/test/comments/skip/"}},
                ]
            }
        }
    ).encode("utf-8")


def test_parse_reddit_builds_one_document_per_post_and_skips_blank_titles():
    raw = RawFetch(
        source_key=_REDDIT_SOURCE.key,
        status="fetched",
        retrieved_at="2026-01-01T00:00:00+00:00",
        payload=_reddit_payload(),
    )
    docs = parser.parse_reddit(raw, _REDDIT_SOURCE)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == "Something interesting happened"
    assert "More detail here" in doc.content
    assert doc.url.startswith("https://www.reddit.com/r/test/comments/abc123")
    assert doc.published_at is not None


def test_parse_reddit_handles_malformed_json_gracefully():
    raw = RawFetch(
        source_key=_REDDIT_SOURCE.key,
        status="fetched",
        retrieved_at="2026-01-01T00:00:00+00:00",
        payload=b"not json at all",
    )
    assert parser.parse_reddit(raw, _REDDIT_SOURCE) == []


_SAMPLE_RSS = (
    b'<?xml version="1.0"?><rss><channel>'
    b"<item><title>Headline One - Example News</title>"
    b"<link>https://example.com/1</link>"
    b"<description>Body one</description>"
    b"<pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate></item>"
    b"<item><title>No link item</title><description>skip me</description></item>"
    b"</channel></rss>"
)


def test_parse_rss_strips_source_suffix_and_skips_items_without_links():
    raw = RawFetch(
        source_key=_RSS_SOURCE.key,
        status="fetched",
        retrieved_at="2026-01-01T00:00:00+00:00",
        payload=_SAMPLE_RSS,
    )
    docs = parser.parse_rss(raw, _RSS_SOURCE)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == "Headline One"
    assert "Example News" not in doc.title
    assert doc.published_at is not None


def test_parse_fetch_returns_empty_for_non_fetched_status():
    raw = RawFetch(source_key="x", status="not_modified", retrieved_at="2026-01-01T00:00:00+00:00")
    assert parser.parse_fetch(raw, _RSS_SOURCE) == []

    raw_failed = RawFetch(source_key="x", status="failed", retrieved_at="2026-01-01T00:00:00+00:00")
    assert parser.parse_fetch(raw_failed, _RSS_SOURCE) == []
