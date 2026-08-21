"""
Trend-awareness layer (opt-in) - complements app/ai/humor.py's joke-list
humor with actual current general-interest context, so Mochi's jokes/asides
aren't purely static forever.

Off by default: settings.trend_awareness_enabled (MOCHI_TREND_AWARENESS_ENABLED).
Along with Google Calendar and app/ai/humor.py's joke API, this is one of
the only things in Mochi that reaches the open internet without the user
explicitly connecting an account - so it stays opt-in and degrades to
"no trend context" (not an error) on any failure.

Design constraints (matches app/ai/humor.py's philosophy):
  - We NEVER store or hand the LLM raw scraped headline/article text.
    `_paraphrase_headline()` reduces a fetched headline down to a short,
    generic topic label before it's cached or used - not a quote, not a
    close paraphrase of specific facts, just a rough theme ("something
    about a new phone launch", "something about a viral dance trend").
  - Fetching happens on a slow background timer (default: every 6h, see
    settings.trend_fetch_interval_hours), never synchronously inside a
    chat reply - a slow/failed network call must never add latency or
    break chat. Wire fetch_trends() into the same kind of background
    scheduling app/reminders/scheduler.py already uses elsewhere in
    Mochi, gated on settings.trend_awareness_enabled.
  - No API key required - uses Google News' public RSS feed.
  - Best-effort only: any failure here is silently swallowed (logged, not
    raised); get_recent_trends()/pick_one_trend() just return nothing, and
    chat_engine.py's caller treats "no trend" as the normal default case.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.humor.trends")

# Google News' "top stories" RSS feed - public, no API key, general-interest.
_TRENDS_FEED_URL = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
_REQUEST_TIMEOUT_SECONDS = 5
_MAX_CACHED_TOPICS = 5
_MAX_TOPIC_LABEL_CHARS = 80

# Trailing " - Source Name" that Google News appends to every headline;
# stripped before paraphrasing so the source name never ends up looking
# like an attributed quote.
_SOURCE_SUFFIX_RE = re.compile(r"\s*-\s*[^-]{2,40}$")


def _paraphrase_headline(headline: str) -> str:
    """Reduce a fetched headline to a short, generic topic label - never
    stored/used verbatim. Intentionally a rough theme extraction, not a
    summary of the specific facts in the article."""
    cleaned = _SOURCE_SUFFIX_RE.sub("", headline).strip()
    words = cleaned.split()
    gist = " ".join(words[:6]).rstrip(".,;: ")
    if not gist:
        return ""
    return f"something about {gist.lower()}"[:_MAX_TOPIC_LABEL_CHARS]


def _fetch_raw_headlines(limit: int = 8) -> list[str]:
    request = urllib.request.Request(
        _TRENDS_FEED_URL, headers={"User-Agent": "Mochi-desktop-companion/1.0"}
    )
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
        body = resp.read()
    root = ET.fromstring(body)
    titles = [item.findtext("title", "").strip() for item in root.iter("item")]
    return [t for t in titles if t][:limit]


def fetch_trends() -> int:
    """Fetch, paraphrase, and cache a fresh batch of trend topic labels.
    Returns the number cached. Never raises - any failure is logged and
    treated as "no trends available right now" (offline, feed down,
    disabled, etc. are all normal, expected outcomes here)."""
    if not settings.trend_awareness_enabled:
        return 0

    try:
        headlines = _fetch_raw_headlines()
    except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
        logger.info("Trend fetch skipped (unavailable): %s", exc)
        return 0

    initialize_schema()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=max(settings.trend_fetch_interval_hours, 1) * 4)

    labels = [label for label in (_paraphrase_headline(h) for h in headlines) if label]
    labels = labels[:_MAX_CACHED_TOPICS]
    if not labels:
        return 0

    with get_connection() as conn:
        # Replace the cache wholesale - a small rolling window, not a
        # growing archive.
        conn.execute("DELETE FROM trend_cache;")
        conn.executemany(
            "INSERT INTO trend_cache (topic_label, fetched_at, expires_at) VALUES (?, ?, ?)",
            [(label, now.isoformat(), expires_at.isoformat()) for label in labels],
        )
    logger.info("Cached %d trend topic(s)", len(labels))
    return len(labels)


def get_recent_trends() -> list[str]:
    """Currently-unexpired cached topic labels, most recent first. Empty
    if disabled, nothing's been fetched yet, or everything expired -
    callers must treat that as the normal default, not an error."""
    if not settings.trend_awareness_enabled:
        return []

    initialize_schema()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT topic_label FROM trend_cache WHERE expires_at > ? "
            "ORDER BY fetched_at DESC LIMIT ?",
            (now_iso, _MAX_CACHED_TOPICS),
        ).fetchall()
    return [row["topic_label"] for row in rows]


def pick_one_trend() -> Optional[str]:
    """Convenience for the chat layer: one cached topic label, or None."""
    trends = get_recent_trends()
    return trends[0] if trends else None


# --- Background scheduling note -------------------------------------------
# This module intentionally does not start its own timer/thread -
# fetch_trends() should be wired into whatever periodic scheduler
# app/main.py already runs for reminders/timers, gated on
# settings.trend_awareness_enabled, running roughly every
# settings.trend_fetch_interval_hours. Keeping scheduling at the
# application-composition layer (rather than this module spawning its own
# thread) matches how the rest of Mochi's background jobs are wired, and
# keeps this module trivially unit-testable in isolation.
