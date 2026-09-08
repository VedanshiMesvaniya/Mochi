"""
Parser + Normalization (spec sections 11/12) - converts a RawFetch's raw
payload into one or more normalized Document objects, preserving
provenance (source, url, published time) so later stages never lose track
of where a claim came from (spec section 31).

Kept dependency-free (stdlib json/xml only), same as the rest of this
package. A parse failure for one item never aborts the whole batch -
each item is parsed independently and a bad one is just skipped.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Optional

from app.core.logger import get_logger
from app.knowledge.models import Document, RawFetch, Source

logger = get_logger("mochi.knowledge.parser")

_MAX_CONTENT_CHARS = 2_000  # bounded per-document content, not a full page dump
_SOURCE_SUFFIX_RE = re.compile(r"\s*-\s*[^-]{2,40}$")  # trailing " - Source Name" (RSS titles)


def _clean_title(title: str) -> str:
    return _SOURCE_SUFFIX_RE.sub("", title).strip()


def parse_reddit(raw: RawFetch, source: Source) -> list[Document]:
    """One Document per current post in a subreddit's .json feed. Post
    score/selftext become the document body - real content, same
    reasoning as app/humor/subreddit_crawler.py's
    _fetch_reddit_subreddit_content, but per-post here (rather than one
    combined blob per subreddit) so each post can be freshness-scored and
    retrieved independently."""
    if raw.payload is None:
        return []
    try:
        data = json.loads(raw.payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        logger.info("Failed to parse reddit payload for %s: %s", source.key, exc)
        return []

    documents: list[Document] = []
    for entry in (data or {}).get("data", {}).get("children", []):
        post = entry.get("data", {})
        title = str(post.get("title", "")).strip()
        permalink = str(post.get("permalink", "")).strip()
        if not title or not permalink:
            continue
        url = f"https://www.reddit.com{permalink}"
        selftext = str(post.get("selftext", "")).strip()
        score = post.get("score", 0)
        content = f"{title} (score: {score})"
        if selftext:
            content += f"\n{selftext[:_MAX_CONTENT_CHARS]}"
        created_utc = post.get("created_utc")
        published_at = _epoch_to_iso(created_utc) if created_utc else None
        documents.append(
            Document(
                source_key=source.key,
                url=url,
                title=title,
                content=content[:_MAX_CONTENT_CHARS],
                published_at=published_at,
                retrieved_at=raw.retrieved_at,
            )
        )
    return documents


def parse_rss(raw: RawFetch, source: Source) -> list[Document]:
    """One Document per <item> in an RSS feed."""
    if raw.payload is None:
        return []
    try:
        root = ET.fromstring(raw.payload)
    except ET.ParseError as exc:
        logger.info("Failed to parse rss payload for %s: %s", source.key, exc)
        return []

    documents: list[Document] = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not raw_title or not url:
            continue
        title = _clean_title(raw_title)
        description = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        content = title if not description else f"{title}\n{description}"
        documents.append(
            Document(
                source_key=source.key,
                url=url,
                title=title,
                content=content[:_MAX_CONTENT_CHARS],
                published_at=_rfc822_to_iso(pub_date) if pub_date else None,
                retrieved_at=raw.retrieved_at,
            )
        )
    return documents


def parse_fetch(raw: RawFetch, source: Source) -> list[Document]:
    """Dispatch to the right parser for `source.kind`. Anything that isn't
    a successful "fetched" result parses to no documents - "not_modified"
    means there's nothing new to process, and "failed" has no payload."""
    if raw.status != "fetched":
        return []
    if source.kind == "reddit":
        return parse_reddit(raw, source)
    if source.kind == "rss":
        return parse_rss(raw, source)
    return []


def _epoch_to_iso(epoch_seconds: float) -> Optional[str]:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _rfc822_to_iso(value: str) -> Optional[str]:
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.isoformat()
