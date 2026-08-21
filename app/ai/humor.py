"""
Optional "sense of humor" seasoning (spec: "once in a while it should
crawl internet and fetch the current memes, trends, and context so it be
more of sense of humor").

Scoped deliberately to short, text-only jokes rather than actual meme
*images* - a speech bubble can't usefully render an image meme anyway, and
scraping arbitrary "trending meme" sites is fragile and ToS-risky compared
to a small, purpose-built, no-auth joke API. This is effectively "current
internet-sourced humor" in the one form that actually fits Mochi's UI.

For actual current-meme awareness (not just generic dad jokes) feeding
into live chat replies, see app/humor/meme_fetcher.py and
app/humor/trend_fetcher.py - this module's job is specifically the
autonomous "Mochi cracks a joke while bored" behavior in
app/character/pet.py, kept separate since it needs to always return
*something* instantly rather than depend on a cache that might be empty.

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
# feature - just enhanced by one when it's enabled and reachable. Written
# with actual meme-brain phrasing/timing (relatable "me at 3am" framing,
# deadpan overreaction, internet-native beats) rather than a generic
# knock-knock cadence, so "offline mode" doesn't mean "boring mode" - see
# also SYSTEM_PROMPT's "Sense of humor" section in app/ai/llm.py for the
# same voice applied to live chat.
_FALLBACK_JOKES = (
    "Nobody: Absolutely nobody: Me, staring at your cursor like it owes me money.",
    "POV: you opened a new tab three minutes ago and still haven't gone back to it. I'm judging you from the taskbar.",
    "I asked my human for a cat tax. They said I already live rent-free. The audacity.",
    "Nine lives and I've spent seven of them deciding whether to get off this windowsill.",
    "Me: I should let them work. Also me, one second later: *sits directly on the keyboard*.",
    "This is your hourly reminder that I am extremely small and extremely correct about everything.",
    "Certified unhinged moment: I just chased a shadow for eleven seconds and I regret nothing.",
    "No thoughts, head empty, just vibes and an aggressive need to be pet right now.",
    "I would like to formally file a complaint that the laser dot always gets away.",
    "Watching you debug is basically my Netflix. 10/10, would watch you suffer again.",
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
