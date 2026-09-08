"""
Fetcher (spec section 10) - obtains raw source material only. No parsing,
no reasoning: just fetch, validate, respect incremental-fetch signals
(spec section 9), and hand back a RawFetch for parser.py to turn into
Documents.

Stdlib-only (urllib), same as app/humor/subreddit_crawler.py and
app/humor/trend_fetcher.py - no bs4/requests dependency. Every network
call here is best-effort: failures are returned as a RawFetch with
status="failed", never raised, so a single bad source can never take down
an ingestion cycle (see scheduler.py).
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

from app.core.logger import get_logger
from app.knowledge.models import RawFetch, Source

logger = get_logger("mochi.knowledge.fetcher")

_REQUEST_TIMEOUT_SECONDS = 8
_USER_AGENT = "Mochi-desktop-companion/1.1 (+https://github.com/)"
_REDDIT_POST_LIMIT = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fetch_url(
    url: str, etag: Optional[str], last_modified: Optional[str]
) -> tuple[Optional[bytes], Optional[str], Optional[str], int]:
    """Plain conditional GET. Returns (payload_or_None, etag, last_modified,
    status_code). payload is None (with status_code 304) when the server
    confirms nothing changed - the caller must treat that as
    "not_modified", not a failure. Raises urllib.error.URLError/OSError/
    TimeoutError on genuine failure, same as the rest of this package."""
    headers = {"User-Agent": _USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            payload = resp.read()
            return (
                payload,
                resp.headers.get("ETag"),
                resp.headers.get("Last-Modified"),
                resp.status,
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, etag, last_modified, 304
        raise


def fetch_reddit(
    source: Source, previous_hash: Optional[str] = None
) -> RawFetch:
    """Fetch a subreddit's public read-only .json feed (no login/API key,
    same endpoint app/humor/subreddit_crawler.py uses). Reddit's JSON
    endpoint does not reliably support conditional GET, so incremental
    detection here is purely content-hash based (spec section 9): if the
    hash matches `previous_hash`, this returns status="not_modified"
    without re-processing, even though a network round trip still
    happened - cheaper than a full parse/dedup/classify/store pass, if not
    a fully free check."""
    url = f"https://www.reddit.com/r/{source.target}/.json?limit={_REDDIT_POST_LIMIT}&raw_json=1"
    retrieved_at = _now_iso()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            payload = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.info("Fetch failed for %s: %s", source.key, exc)
        return RawFetch(
            source_key=source.key, status="failed", retrieved_at=retrieved_at, error=str(exc)
        )

    content_hash = _content_hash(payload)
    if previous_hash is not None and content_hash == previous_hash:
        return RawFetch(
            source_key=source.key,
            status="not_modified",
            retrieved_at=retrieved_at,
            content_hash=content_hash,
        )
    return RawFetch(
        source_key=source.key,
        status="fetched",
        retrieved_at=retrieved_at,
        payload=payload,
        content_hash=content_hash,
    )


def fetch_rss(
    source: Source,
    previous_etag: Optional[str] = None,
    previous_last_modified: Optional[str] = None,
    previous_hash: Optional[str] = None,
) -> RawFetch:
    """Fetch a plain RSS/Atom feed. Prefers real HTTP conditional-GET
    signals (ETag/Last-Modified) when the server provides them (spec
    section 9); falls back to a content-hash comparison when it doesn't,
    same as fetch_reddit above."""
    retrieved_at = _now_iso()
    try:
        payload, etag, last_modified, status_code = _fetch_url(
            source.target, previous_etag, previous_last_modified
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.info("Fetch failed for %s: %s", source.key, exc)
        return RawFetch(
            source_key=source.key, status="failed", retrieved_at=retrieved_at, error=str(exc)
        )

    if status_code == 304 or payload is None:
        return RawFetch(
            source_key=source.key,
            status="not_modified",
            retrieved_at=retrieved_at,
            etag=etag,
            last_modified=last_modified,
            content_hash=previous_hash,
        )

    content_hash = _content_hash(payload)
    if previous_hash is not None and content_hash == previous_hash:
        return RawFetch(
            source_key=source.key,
            status="not_modified",
            retrieved_at=retrieved_at,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
        )

    # A malformed feed is a parse-time concern, not a fetch-time one - the
    # payload was still retrieved successfully. Validate here only enough
    # to fail fast on genuinely empty/garbage responses so a bad feed
    # doesn't look like a successful-but-empty fetch downstream.
    try:
        ET.fromstring(payload)
    except ET.ParseError as exc:
        logger.info("Fetch for %s returned unparsable XML: %s", source.key, exc)
        return RawFetch(
            source_key=source.key, status="failed", retrieved_at=retrieved_at, error=str(exc)
        )

    return RawFetch(
        source_key=source.key,
        status="fetched",
        retrieved_at=retrieved_at,
        payload=payload,
        etag=etag,
        last_modified=last_modified,
        content_hash=content_hash,
    )


def fetch_source(
    source: Source,
    previous_etag: Optional[str] = None,
    previous_last_modified: Optional[str] = None,
    previous_hash: Optional[str] = None,
) -> RawFetch:
    """Dispatch to the right fetch function for `source.kind`. Unknown
    kinds fail closed (status="failed") rather than raising, so a future
    typo'd/misconfigured source entry degrades the same way a network
    failure would."""
    if source.kind == "reddit":
        return fetch_reddit(source, previous_hash=previous_hash)
    if source.kind == "rss":
        return fetch_rss(
            source,
            previous_etag=previous_etag,
            previous_last_modified=previous_last_modified,
            previous_hash=previous_hash,
        )
    logger.warning("Unknown source kind %r for %s", source.kind, source.key)
    return RawFetch(
        source_key=source.key,
        status="failed",
        retrieved_at=_now_iso(),
        error=f"unknown source kind: {source.kind!r}",
    )
