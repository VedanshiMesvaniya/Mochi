"""
Source Manager (spec section 5) - controls WHAT Mochi is allowed and
expected to collect. Mochi does not crawl the open internet; it only ever
checks the small, explicit registry below.

Two built-in sources ship by default, matching the acquisition priority
in spec section 6 (prefer official APIs/RSS over HTML scraping/browser
automation - this engine implements neither of the latter two):

  - Google News' public RSS feed (kind="rss") - the same feed
    app/humor/trend_fetcher.py already uses, registered here as a
    "knowledge" leaning, medium-authority source of current-events
    evidence for chat context (distinct from trend_fetcher's own
    always-paraphrased humor-flavor cache; this copy is stored as
    evidence with provenance, not reduced to a joke-seasoning label).
  - r/technology's public .json feed (kind="reddit") - a "temporal",
    low-authority source: useful as evidence of current discussion/
    sentiment (spec section 28), never as authoritative fact.

Both are off by default until settings.web_knowledge_enabled is true
(see scheduler.py); this module itself has no on/off switch of its own,
only "what would be fetched if the engine is enabled" - same separation
of concerns as app/core/config.py's crawl_sources_path.
"""

from __future__ import annotations

from app.knowledge.models import Source

DEFAULT_SOURCES: tuple[Source, ...] = (
    Source(
        key="rss:google_news_top",
        kind="rss",
        target="https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        category_hint="knowledge",
        authority="medium",
        frequency_hours=6,
    ),
    Source(
        key="reddit:technology",
        kind="reddit",
        target="technology",
        category_hint="temporal",
        authority="low",
        frequency_hours=3,
    ),
)


def get_enabled_sources() -> list[Source]:
    """Every currently-enabled registered source. A plain function (not a
    module-level constant re-export) so tests can monkeypatch
    DEFAULT_SOURCES and see it reflected without reimporting this
    module."""
    return [source for source in DEFAULT_SOURCES if source.enabled]
