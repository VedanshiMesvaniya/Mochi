"""
Markdown-link crawler (opt-in, manual/CLI use) - crawls every link listed in
a source markdown file (e.g. a curated subreddit/reference list) and stores
each page's fetched content in SQLite.

Design constraints, deliberately different from app/humor/trend_fetcher.py
and app/humor/meme_fetcher.py above:

  * Append-only, permanent storage. Those two caches above are small
    rolling windows that get wiped and replaced on every fetch. This
    table (`crawled_sources`, see app/memory/database.py) is the
    opposite: once a URL has been crawled successfully, its row is never
    deleted and never overwritten - there is intentionally no "refresh"
    or "delete" function here.
  * Never re-crawl a URL that already has a row. Before fetching
    anything, `crawl_markdown_file()` checks what's already stored and
    skips those URLs entirely - no network call is even attempted for
    them. This makes the whole operation idempotent/resumable: running
    it again after a partial run (or after adding new links to the
    source file) only fetches what's actually new.
  * Best-effort per link, same as the rest of app/humor/: a single link
    failing (network error, 404, timeout) is logged and skipped, never
    raised, and never written to the table - so a failed fetch can still
    be retried on a later run (only *successful* fetches count as
    "already stored").
  * Stdlib only (urllib), matching the rest of this package - no bs4/
    requests dependency for a simple text-extraction job.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.humor.crawler")

_REQUEST_TIMEOUT_SECONDS = 10
_MAX_CONTENT_CHARS = 20_000  # store a bounded amount of page text, not the whole raw HTML
_USER_AGENT = "Mochi-desktop-companion/1.0 (+https://github.com/)"

# Markdown link syntax: [display text](https://...)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


@dataclass
class CrawlResult:
    fetched: int      # newly crawled and stored this run
    skipped: int      # already had a row, no network call made
    failed: int        # attempted but the fetch/parse failed (not stored, retryable later)


def extract_links(markdown_text: str) -> list[tuple[str, str]]:
    """Pull every `[title](url)` markdown link out of `markdown_text`, in
    order, de-duplicated by URL (first occurrence wins)."""
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for title, url in _MD_LINK_RE.findall(markdown_text):
        if url in seen:
            continue
        seen.add(url)
        links.append((title.strip(), url.strip()))
    return links


def _already_crawled(urls: Iterable[str]) -> set[str]:
    urls = list(urls)
    if not urls:
        return set()
    initialize_schema()
    placeholders = ", ".join("?" for _ in urls)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT url FROM crawled_sources WHERE url IN ({placeholders});",
            urls,
        ).fetchall()
    return {row["url"] for row in rows}


# Very small, dependency-free HTML-to-text reduction: drop script/style
# blocks entirely, drop all remaining tags, collapse whitespace. This is
# intentionally not a full HTML parser - good enough to store readable
# page text without pulling in a new dependency (see module docstring).
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _html_to_text(html: str) -> tuple[str, str]:
    """Returns (title, body_text)."""
    title_match = _TITLE_TAG_RE.search(html)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""

    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    cleaned = _TAG_RE.sub("\n", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    cleaned = _BLANK_LINES_RE.sub("\n\n", cleaned)
    text = "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())
    return title, text[:_MAX_CONTENT_CHARS]


def _fetch_page(url: str) -> tuple[str, str]:
    """Raises urllib.error.URLError/OSError/UnicodeDecodeError on failure -
    callers must catch and treat as a skippable, retryable failure."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    html = raw.decode("utf-8", errors="replace")
    return _html_to_text(html)


def _store_page(url: str, source_list: str, title: str, content: str) -> None:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    crawled_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        # INSERT OR IGNORE, not INSERT OR REPLACE: `url` is UNIQUE (see
        # database.py) and this table is append-only - if a race ever put
        # a row there between the earlier `_already_crawled` check and
        # this write, silently keep the existing row rather than
        # overwriting stored data.
        conn.execute(
            "INSERT OR IGNORE INTO crawled_sources "
            "(url, source_list, title, content, content_hash, crawled_at) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (url, source_list, title or None, content, content_hash, crawled_at),
        )


def crawl_links(links: list[tuple[str, str]], source_list: str) -> CrawlResult:
    """Crawl a list of (display_title, url) pairs. Skips any URL already
    stored (no network call made for those). Never deletes or re-crawls
    an existing row."""
    initialize_schema()
    already = _already_crawled(url for _, url in links)

    fetched = skipped = failed = 0
    for display_title, url in links:
        if url in already:
            skipped += 1
            continue
        try:
            page_title, text = _fetch_page(url)
        except (urllib.error.URLError, OSError, UnicodeDecodeError, TimeoutError) as exc:
            logger.info("Crawl failed for %s (will retry on a future run): %s", url, exc)
            failed += 1
            continue
        _store_page(url, source_list, page_title or display_title, text)
        fetched += 1
        logger.info("Crawled and stored: %s", url)

    logger.info(
        "Crawl of '%s' complete: %d fetched, %d skipped (already stored), %d failed",
        source_list, fetched, skipped, failed,
    )
    return CrawlResult(fetched=fetched, skipped=skipped, failed=failed)


def crawl_markdown_file(path: str | Path, source_list: str | None = None) -> CrawlResult:
    """Parse every markdown link out of `path` and crawl/store each one
    (skipping anything already stored). `source_list` defaults to the
    file's own name so results from different source files stay
    distinguishable in the `crawled_sources` table."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    links = extract_links(text)
    return crawl_links(links, source_list or path.name)


def get_stored_page(url: str):
    """Read back one already-crawled page by URL, or None if it hasn't
    been crawled (successfully) yet."""
    initialize_schema()
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM crawled_sources WHERE url = ?;", (url,)
        ).fetchone()


def list_stored_pages(source_list: str | None = None, limit: int = 100):
    """Everything crawled so far, optionally filtered to one source list,
    most recently crawled first."""
    initialize_schema()
    with get_connection() as conn:
        if source_list is not None:
            return conn.execute(
                "SELECT * FROM crawled_sources WHERE source_list = ? "
                "ORDER BY crawled_at DESC LIMIT ?;",
                (source_list, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM crawled_sources ORDER BY crawled_at DESC LIMIT ?;",
            (limit,),
        ).fetchall()
