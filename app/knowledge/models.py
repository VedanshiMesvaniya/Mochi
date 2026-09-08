"""
Shared dataclasses passed between the Web Knowledge Engine's pipeline
stages (source_manager -> fetcher -> parser -> dedup -> classifier ->
knowledge_store -> context_engine). Kept in one module, same reasoning as
app/ai/chat_engine.py's ChatReaction/ActionResult - one shape, reused by
every stage, instead of each stage inventing its own tuple/dict shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Source:
    """One registered external source (spec section 5 - Source Manager).

    `key` is a stable identifier used both as the `source` column in
    `knowledge_documents` and as the primary key of `knowledge_fetch_state`
    (incremental-fetch bookkeeping) - never reused across different
    targets.

    `kind` selects which fetcher/parser function handles this source -
    currently "reddit" (a subreddit's public .json feed, same approach as
    app/humor/subreddit_crawler.py) or "rss" (a plain RSS/Atom feed, same
    approach as app/humor/trend_fetcher.py).

    `category_hint` is the classifier's starting point (spec section 15 -
    "Temporal Feed" vs "Knowledge"), not the final word: classifier.py can
    still adjust per-document if a signal disagrees.

    `authority` is a coarse, source-level trust tier (spec section 28)
    used by freshness.retrieval_score - "high" for official
    docs/announcements, "medium" for established publications, "low" for
    open community discussion (e.g. Reddit) that's useful as *evidence of
    current sentiment/trends*, not as authoritative fact.
    """

    key: str
    kind: str  # "reddit" | "rss"
    target: str  # subreddit name (no "r/") for reddit, feed URL for rss
    category_hint: str  # "temporal" | "knowledge"
    authority: str  # "high" | "medium" | "low"
    frequency_hours: float
    enabled: bool = True


@dataclass
class RawFetch:
    """Output of fetcher.py - obtaining source material only, no parsing/
    reasoning (spec section 10). `status` distinguishes a genuinely new
    fetch from "server/local hash says nothing changed" (spec section 9 -
    Incremental Fetching), so callers can skip re-processing unchanged
    content without treating that as a failure.
    """

    source_key: str
    status: str  # "fetched" | "not_modified" | "failed"
    retrieved_at: str
    payload: Optional[bytes] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    error: Optional[str] = None


@dataclass
class Document:
    """Output of parser.py - one normalized, provenance-tagged unit of
    content (spec section 12 - Normalization). A single RawFetch can
    produce many Documents (e.g. one per Reddit post, one per RSS item).
    """

    source_key: str
    url: str
    title: str
    content: str
    published_at: Optional[str]  # ISO 8601, or None if unknown
    retrieved_at: str


@dataclass
class EvidenceItem:
    """One ranked piece of evidence handed to the LLM by context_engine.py
    (spec section 30 - Answer Generation: a compact evidence package, not
    the whole scraped database)."""

    claim: str
    source: str
    freshness: str  # FRESH | RECENT | AGING | STALE | EXPIRED | UNKNOWN
    confidence: float
    score: float
