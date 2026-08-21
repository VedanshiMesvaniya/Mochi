"""
Optional "sense of humor" seasoning (spec: "once in a while it should
crawl internet and fetch the current memes, trends, and context so it be
more of sense of humor").

Scoped deliberately to short, text-only jokes rather than actual meme
*images* - a speech bubble can't usefully render an image meme anyway, and
scraping arbitrary "trending meme" sites is fragile and ToS-risky compared
to a small, purpose-built, no-auth joke API. This is effectively "current
internet-sourced humor" in the one form that actually fits Mochi's UI.

This is one of the only places in Mochi that reaches out to the open
internet for something that isn't an explicit integration the person
turned on (spec section 28: "clearly show when a feature requires
internet") - see Settings.humor_enabled. Every call here degrades to a
small built-in offline joke list on ANY failure (no network, blocked,
slow, whatever) - humor should make Mochi more fun, never something that
can break, slow down, or be required for chat to work.
"""

from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from typing import Optional

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.ai.humor")

# A small, free, no-API-key, purpose-built joke API - no scraping, no ToS
# ambiguity, just short text jokes, which is exactly the shape of content
# a speech bubble can actually show.
JOKE_API_URL = "https://icanhazdadjoke.com/"
# Short and no-retry, deliberately: a slow/hanging joke fetch must never be
# allowed to delay or block real chat/behavior - see how this is called
# from a background QThread in app/character/pet.py, never the UI thread.
REQUEST_TIMEOUT_SECONDS = 4

# Always available offline, so "sense of humor" isn't purely a network
# feature - just enhanced by one when it's enabled and reachable.
_FALLBACK_JOKES = (
    "Why do cats make terrible storytellers? They only have one tail.",
    "I asked my human for a cat tax. They said I already live rent-free.",
    "Mrrp. I'd tell you a yarn joke but it might unravel.",
    "Why was the cat sitting on the computer? To keep an eye on the mouse.",
    "I'm not lazy, I'm just running in low-power mode. Like a real cat.",
    "What do you call a pile of kittens? A meowntain.",
    "I've got 9 lives and I'm still too tired to use most of them.",
    "Why don't cats play poker in the jungle? Too many cheetahs.",
)


def fetch_joke() -> Optional[str]:
    """One best-effort network call for a fresh joke. Returns None on
    literally any failure - never raises, never retries."""
    request = urllib.request.Request(
        JOKE_API_URL,
        headers={"Accept": "application/json", "User-Agent": "Mochi-desktop-companion (+local)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.info("Joke fetch failed, falling back to offline jokes: %s", exc)
        return None

    joke = str(body.get("joke", "")).strip()
    return joke or None


def get_joke() -> str:
    """The function callers should actually use - always returns
    *something*, trying the network first only if the person has opted
    into it (Settings.humor_enabled), falling back to the offline list
    otherwise (disabled, or the network call failed)."""
    if settings.humor_enabled:
        fetched = fetch_joke()
        if fetched:
            return fetched
    return random.choice(_FALLBACK_JOKES)
