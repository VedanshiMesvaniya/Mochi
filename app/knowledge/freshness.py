"""
Freshness Engine (spec sections 21/22) - freshness is a first-class part
of retrieval, not an afterthought. "The most semantically similar document
is not necessarily the correct document" (spec section 21) - an old,
superseded claim can still look like a strong text match.

Two responsibilities:
  1. `categorize(age_hours, category)` - label an age as
     FRESH/RECENT/AGING/STALE/EXPIRED. Thresholds are source-category
     dependent (spec section 22: "A meme might expire after hours. A
     software specification might remain valid for months.").
  2. `retrieval_score(...)` - combine relevance + freshness + source
     authority + confidence into one ranking score (spec section 21's
     formula), used by knowledge_store.query_relevant.

The exact weights below are a starting point, explicitly callable-out as
tunable (spec section 21: "the exact weighting can evolve during
development") - kept as module-level constants, not hardcoded inline, so
future tuning is a one-line change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

FRESH, RECENT, AGING, STALE, EXPIRED, UNKNOWN = (
    "FRESH",
    "RECENT",
    "AGING",
    "STALE",
    "EXPIRED",
    "UNKNOWN",
)

# (max_age_hours, label) thresholds, checked in order. Temporal content
# (trends/memes/current discussion) ages out much faster than persistent
# knowledge (docs/specs/announcements) - see spec section 22.
_TEMPORAL_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (6, FRESH),
    (24, RECENT),
    (72, AGING),
)
_KNOWLEDGE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (24 * 7, FRESH),
    (24 * 30, RECENT),
    (24 * 180, AGING),
)

_FRESHNESS_WEIGHT = {FRESH: 1.0, RECENT: 0.8, AGING: 0.5, STALE: 0.2, EXPIRED: 0.0, UNKNOWN: 0.4}
_AUTHORITY_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}

# Retrieval score weighting (spec section 21/29): relevance dominates
# (the evidence has to actually be about the question), freshness is the
# second-biggest factor (the whole point of this engine), authority and
# extraction confidence round it out.
_RELEVANCE_WEIGHT = 0.4
_FRESHNESS_SCORE_WEIGHT = 0.3
_AUTHORITY_SCORE_WEIGHT = 0.2
_CONFIDENCE_WEIGHT = 0.1


def age_hours(retrieved_at: str, now: Optional[datetime] = None) -> Optional[float]:
    """Hours since `retrieved_at` (ISO 8601), or None if unparsable."""
    try:
        retrieved_dt = datetime.fromisoformat(retrieved_at)
    except (TypeError, ValueError):
        return None
    if retrieved_dt.tzinfo is None:
        retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    delta = now - retrieved_dt
    return max(delta.total_seconds() / 3600.0, 0.0)


def categorize(hours: Optional[float], category: str, expired: bool = False) -> str:
    """Freshness label for something `hours` old, from a document of
    `category` ("temporal" or "knowledge"). `expired` is passed
    separately (rather than inferred here) since expiry is a hard TTL
    check knowledge_store.py already does against `expires_at`, not
    something this function should re-derive."""
    if expired:
        return EXPIRED
    if hours is None:
        return UNKNOWN
    thresholds = _TEMPORAL_THRESHOLDS if category == "temporal" else _KNOWLEDGE_THRESHOLDS
    for max_age, label in thresholds:
        if hours <= max_age:
            return label
    return STALE


def retrieval_score(relevance: float, freshness_label: str, authority: str, confidence: float) -> float:
    """Combined ranking score in roughly [0, 1] - see module docstring
    for the weighting rationale. Unknown authority values fall back to
    the "low" weight rather than raising, since a misconfigured/future
    source should be trusted less by default, not crash retrieval."""
    freshness_weight = _FRESHNESS_WEIGHT.get(freshness_label, _FRESHNESS_WEIGHT[UNKNOWN])
    authority_weight = _AUTHORITY_WEIGHT.get(authority, _AUTHORITY_WEIGHT["low"])
    relevance = max(0.0, min(relevance, 1.0))
    confidence = max(0.0, min(confidence, 1.0))
    return (
        relevance * _RELEVANCE_WEIGHT
        + freshness_weight * _FRESHNESS_SCORE_WEIGHT
        + authority_weight * _AUTHORITY_SCORE_WEIGHT
        + confidence * _CONFIDENCE_WEIGHT
    )
