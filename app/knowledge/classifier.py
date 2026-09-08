"""
Classifier (spec section 15) - not everything Mochi collects should
become permanent knowledge. Splits incoming documents into:

  - "temporal": short-lived, currently-happening information (trending
    posts, current discussion) - gets a short TTL (spec section 16).
  - "knowledge": information that stays useful over time (spec section
    17) - no hard TTL, though freshness.py still ages it for ranking
    purposes.

v1.1 classifies at the *source* level (Source.category_hint) rather than
per-document content analysis - a lighter-weight starting point the spec
explicitly allows ("initially use rules", mirrored from the V2 Human
Activity State section's own guidance). Per-document overrides can be
layered on top later without changing this function's signature.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.knowledge.models import Source

# Default time-to-live for temporal documents (spec section 16 example:
# "TTL: 24 hours" for a meme/trend-style post). Persistent knowledge
# (category == "knowledge") has no hard TTL - see module docstring.
_DEFAULT_TEMPORAL_TTL_HOURS = 24


def classify(source: Source, retrieved_at: str) -> tuple[str, Optional[str]]:
    """Returns (category, expires_at_iso_or_None) for a document that
    just came from `source`, retrieved at `retrieved_at` (ISO 8601)."""
    category = source.category_hint if source.category_hint in {"temporal", "knowledge"} else "knowledge"
    if category != "temporal":
        return category, None

    try:
        retrieved_dt = datetime.fromisoformat(retrieved_at)
    except ValueError:
        retrieved_dt = datetime.now(timezone.utc)
    expires_at = retrieved_dt + timedelta(hours=_DEFAULT_TEMPORAL_TTL_HOURS)
    return category, expires_at.isoformat()
