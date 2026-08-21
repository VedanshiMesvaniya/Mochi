"""
Meme-awareness layer (opt-in, shares settings.trend_awareness_enabled with
app/humor/trend_fetcher.py) - this is the "meme" half of the original
request ("crawl internet and fetch the current memes, trends, and context
so it be more of sense of humor"). trend_fetcher.py covers general
headlines/context; this module covers actual current memes specifically,
which is what makes the humor feel "meme level" rather than just
generically topical.

Source: Reddit's public per-subreddit JSON endpoint (e.g.
https://www.reddit.com/r/memes/top.json), which needs no login/API key for
read access to public subreddits. Pulled from a small rotation of
general-audience meme subreddits - deliberately not NSFW/political/edgy
communities, since Mochi's humor should stay all-ages regardless of
what's trending on the wider internet.

Same hard rule as trend_fetcher.py and the same reasoning as the
copyright-safety notes throughout this codebase: **we never store or
repeat a meme's actual title/caption verbatim, and never fetch or display
meme images.** `_paraphrase_post_title()` reduces a fetched post title
down to a short, generic "premise" - the loose subject/situation a meme is
about ("something about Monday morning energy", "something about a pet
doing something dramatic") - which is handed to the LLM purely as
inspiration to write its OWN original meme-style line, never as text to
quote or closely mirror. See _MEME_CONTEXT_TEMPLATE in app/ai/llm.py for
the exact instruction given to the model.

Off by default (same toggle as trend_fetcher.py): settings.trend_awareness_enabled.
Best-effort only - any failure here (network down, Reddit rate-limits us,
malformed JSON) is logged and swallowed; callers just get "no meme right
now", which is the normal, expected default.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.humor.memes")

# General-audience, high-traffic meme communities - rotated so repeated
# fetches don't always hit the exact same subreddit.
_MEME_SUBREDDITS = ("memes", "wholesomememes", "ProgrammerHumor")
_REQUEST_TIMEOUT_SECONDS = 5
_MAX_CACHED_PREMISES = 5
_MAX_PREMISE_CHARS = 90

# Reddit flair/tag prefixes like "[OC]", "(repost)" etc. that add nothing
# to the actual subject and would otherwise leak into the paraphrase.
_TAG_PREFIX_RE = re.compile(r"^\s*[\[\(][^\]\)]{1,20}[\]\)]\s*")


def _paraphrase_post_title(title: str) -> str:
    """Reduce a real meme post title to a short generic premise - never
    stored/used verbatim, and never presented as a quote. This is rough
    theme extraction, not a summary of the meme's actual joke/punchline."""
    cleaned = _TAG_PREFIX_RE.sub("", title).strip()
    words = cleaned.split()
    gist = " ".join(words[:6]).rstrip(".,;:! ")
    if not gist:
        return ""
    return f"something about {gist.lower()}"[:_MAX_PREMISE_CHARS]


def _fetch_raw_titles(limit: int = 8) -> list[str]:
    import random

    subreddit = random.choice(_MEME_SUBREDDITS)
    url = f"https://www.reddit.com/r/{subreddit}/top.json?limit={limit}&t=day"
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mochi-desktop-companion/1.0 (by /u/mochi-app)"}
    )
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    children = body.get("data", {}).get("children", [])
    titles = []
    for child in children:
        post = child.get("data", {})
        # Skip anything flagged NSFW/spoiler at the source - belt-and-
        # braces on top of only pulling general-audience subreddits.
        if post.get("over_18") or post.get("spoiler"):
            continue
        title = str(post.get("title", "")).strip()
        if title:
            titles.append(title)
    return titles[:limit]


def fetch_memes() -> int:
    """Fetch, paraphrase, and cache a fresh batch of meme premises.
    Returns the number cached. Never raises - any failure degrades to 0
    cached, which callers treat as the normal 'no meme available' case."""
    if not settings.trend_awareness_enabled:
        return 0

    try:
        titles = _fetch_raw_titles()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("Meme fetch skipped (unavailable): %s", exc)
        return 0

    initialize_schema()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=max(settings.trend_fetch_interval_hours, 1) * 4)

    premises = [p for p in (_paraphrase_post_title(t) for t in titles) if p]
    premises = premises[:_MAX_CACHED_PREMISES]
    if not premises:
        return 0

    with get_connection() as conn:
        conn.execute("DELETE FROM meme_cache;")
        conn.executemany(
            "INSERT INTO meme_cache (premise, fetched_at, expires_at) VALUES (?, ?, ?)",
            [(p, now.isoformat(), expires_at.isoformat()) for p in premises],
        )
    logger.info("Cached %d meme premise(s)", len(premises))
    return len(premises)


def get_recent_memes() -> list[str]:
    """Currently-unexpired cached meme premises, most recent first."""
    if not settings.trend_awareness_enabled:
        return []

    initialize_schema()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT premise FROM meme_cache WHERE expires_at > ? "
            "ORDER BY fetched_at DESC LIMIT ?",
            (now_iso, _MAX_CACHED_PREMISES),
        ).fetchall()
    return [row["premise"] for row in rows]


def pick_one_meme() -> Optional[str]:
    """Convenience for the chat layer: one cached meme premise, or None.
    chat_engine.py prefers this over trend_fetcher.pick_one_trend() when
    both are available, since a meme premise is more specifically 'funny'
    than a generic news headline."""
    memes = get_recent_memes()
    return memes[0] if memes else None


# --- Background scheduling note -------------------------------------------
# Same pattern as trend_fetcher.py: no timer/thread of its own.
# fetch_memes() should be wired into the same periodic job that calls
# trend_fetcher.fetch_trends(), gated on settings.trend_awareness_enabled.
