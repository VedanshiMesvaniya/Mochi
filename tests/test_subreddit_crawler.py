import json
import urllib.error

from app.humor import subreddit_crawler as crawler
from app.memory.database import get_connection, initialize_schema


def test_extract_links_parses_and_dedupes():
    md = (
        "See [r/funny](https://www.reddit.com/r/funny/) for jokes.\n"
        "Also [r/funny](https://www.reddit.com/r/funny/) again (dup).\n"
        "And [r/memes](https://www.reddit.com/r/memes/).\n"
    )
    links = crawler.extract_links(md)
    assert links == [
        ("r/funny", "https://www.reddit.com/r/funny/"),
        ("r/memes", "https://www.reddit.com/r/memes/"),
    ]


def test_html_to_text_strips_tags_scripts_and_styles():
    html = (
        "<html><head><title> My Page </title>"
        "<style>body{color:red}</style></head>"
        "<body><script>alert(1)</script><h1>Hello</h1><p>World</p></body></html>"
    )
    title, text = crawler._html_to_text(html)
    assert title == "My Page"
    assert "alert(1)" not in text
    assert "color:red" not in text
    assert "Hello" in text
    assert "World" in text


def test_crawl_links_stores_new_and_skips_already_stored(monkeypatch, temp_db):
    initialize_schema()
    calls = []

    def _fake_fetch(url):
        calls.append(url)
        return ("Fake Title", "some page text")

    monkeypatch.setattr(crawler, "_fetch_page", _fake_fetch)

    links = [("A", "https://example.com/a"), ("B", "https://example.com/b")]
    result = crawler.crawl_links(links, source_list="test-list")

    assert result.fetched == 2
    assert result.skipped == 0
    assert result.failed == 0
    assert set(calls) == {"https://example.com/a", "https://example.com/b"}

    # Second run over the same links must NOT hit the network again -
    # both URLs already have a stored row.
    calls.clear()
    result2 = crawler.crawl_links(links, source_list="test-list")
    assert result2.fetched == 0
    assert result2.skipped == 2
    assert calls == []


def test_crawl_links_never_deletes_or_overwrites_existing_row(monkeypatch, temp_db):
    initialize_schema()

    def _first_fetch(url):
        return ("Original Title", "original content")

    monkeypatch.setattr(crawler, "_fetch_page", _first_fetch)
    crawler.crawl_links([("A", "https://example.com/a")], source_list="list-1")

    stored_before = crawler.get_stored_page("https://example.com/a")
    assert stored_before["title"] == "Original Title"

    # Simulate the row's URL being re-submitted (e.g. present in another
    # source list too) with different fetched content - must be skipped,
    # not overwritten, since it's already stored.
    def _second_fetch(url):
        raise AssertionError("must not be called - URL already stored")

    monkeypatch.setattr(crawler, "_fetch_page", _second_fetch)
    result = crawler.crawl_links([("A", "https://example.com/a")], source_list="list-2")

    assert result.skipped == 1
    stored_after = crawler.get_stored_page("https://example.com/a")
    assert stored_after["title"] == "Original Title"
    assert stored_after["content"] == "original content"


def test_crawl_links_handles_fetch_failure_without_storing(monkeypatch, temp_db):
    initialize_schema()

    def _boom(url):
        raise urllib.error.URLError("simulated: unreachable")

    monkeypatch.setattr(crawler, "_fetch_page", _boom)
    result = crawler.crawl_links([("A", "https://example.com/a")], source_list="test-list")

    assert result.fetched == 0
    assert result.failed == 1
    assert crawler.get_stored_page("https://example.com/a") is None


def test_crawl_markdown_file_reads_links_from_disk(tmp_path, monkeypatch, temp_db):
    initialize_schema()
    md_file = tmp_path / "links.md"
    md_file.write_text("[r/funny](https://www.reddit.com/r/funny/)\n")

    monkeypatch.setattr(
        crawler, "_fetch_page", lambda url: ("Funny", "lots of jokes here")
    )
    result = crawler.crawl_markdown_file(md_file)

    assert result.fetched == 1
    rows = crawler.list_stored_pages(source_list=md_file.name)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://www.reddit.com/r/funny/"


def test_crawled_sources_table_has_unique_url_constraint(temp_db):
    initialize_schema()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO crawled_sources (url, source_list, title, content, "
            "content_hash, crawled_at) VALUES (?, ?, ?, ?, ?, ?);",
            ("https://example.com/x", "list", "T", "c", "hash", "2026-01-01T00:00:00"),
        )
    with get_connection() as conn:
        # INSERT OR IGNORE (what the crawler itself uses) must silently
        # keep the existing row rather than raising or duplicating.
        conn.execute(
            "INSERT OR IGNORE INTO crawled_sources (url, source_list, title, "
            "content, content_hash, crawled_at) VALUES (?, ?, ?, ?, ?, ?);",
            ("https://example.com/x", "list", "T2", "c2", "hash2", "2026-01-02T00:00:00"),
        )
        rows = conn.execute(
            "SELECT * FROM crawled_sources WHERE url = ?;", ("https://example.com/x",)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "T"


def _fake_urlopen_json(payload: dict):
    """Build a fake urllib.request.urlopen() replacement that returns a
    fixed JSON payload, for testing Reddit's .json endpoint handling
    without any real network access."""

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return lambda *args, **kwargs: _FakeResponse()


def test_reddit_url_is_detected_and_uses_json_endpoint(monkeypatch, temp_db):
    """A subreddit link must fetch REAL post content via Reddit's public
    .json endpoint, not the near-empty HTML shell a plain GET of the page
    itself would return."""
    reddit_payload = {
        "data": {
            "children": [
                {"data": {"title": "Funniest thing today", "score": 4200, "selftext": ""}},
                {"data": {"title": "A relatable meme", "score": 1500, "selftext": "so real"}},
            ]
        }
    }
    seen_urls = []

    def fake_urlopen(request, timeout=None):
        seen_urls.append(request.full_url)
        return _fake_urlopen_json(reddit_payload)()

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)

    title, content = crawler._fetch_page("https://www.reddit.com/r/funny/")

    assert title == "r/funny"
    assert "Funniest thing today" in content
    assert "A relatable meme" in content
    assert "so real" in content
    assert seen_urls[0].startswith("https://www.reddit.com/r/funny/.json")


def test_reddit_url_with_no_posts_is_still_a_success_not_a_failure(monkeypatch, temp_db):
    empty_payload = {"data": {"children": []}}
    monkeypatch.setattr(
        crawler.urllib.request, "urlopen", lambda *a, **k: _fake_urlopen_json(empty_payload)()
    )
    title, content = crawler._fetch_page("https://www.reddit.com/r/emptysub/")
    assert title == "r/emptysub"
    assert "no accessible posts" in content.lower()


def test_non_reddit_url_uses_html_fallback_not_json_endpoint(monkeypatch, temp_db):
    calls = []

    class _FakeHtmlResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return b"<html><title>Hi</title><body>Hello world</body></html>"

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeHtmlResponse()

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    title, content = crawler._fetch_page("https://example.com/some-article")

    assert title == "Hi"
    assert "Hello world" in content
    assert ".json" not in calls[0]


def test_store_page_saves_model_summary_when_available(monkeypatch, temp_db):
    initialize_schema()
    monkeypatch.setattr(crawler, "summarize_page_content", lambda text: "A clean summary.")
    crawler._store_page("https://example.com/a", "list", "Title", "raw extracted content")

    row = crawler.get_stored_page("https://example.com/a")
    assert row["content"] == "raw extracted content"
    assert row["summary"] == "A clean summary."


def test_store_page_leaves_summary_null_when_model_unavailable(monkeypatch, temp_db):
    initialize_schema()

    def _unavailable(text):
        raise crawler.LLMUnavailable("no ollama")

    monkeypatch.setattr(crawler, "summarize_page_content", _unavailable)
    crawler._store_page("https://example.com/b", "list", "Title", "raw extracted content")

    row = crawler.get_stored_page("https://example.com/b")
    assert row["content"] == "raw extracted content"
    assert row["summary"] is None
