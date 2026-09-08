"""
Deduplication (spec section 13) - the same underlying content can arrive
through multiple URLs, or unchanged across successive ingestion cycles.
Detect that before it becomes a second knowledge_documents row.

Two checks, cheapest first:
  1. Exact URL match - knowledge_documents.url is UNIQUE, so this is a
     plain lookup.
  2. Exact content-hash match within the same source - catches the same
     item re-appearing at a slightly different URL (e.g. a reddit post's
     permalink changing case, a tracking-parameter variant of an RSS
     link).

True near-duplicate similarity (spec section 13's "similarity detection"
across genuinely different phrasings of the same story) is intentionally
out of scope for v1.1 - it needs a similarity metric heavier than this
package's stdlib-only, no-embeddings-dependency scope allows. Exact-hash
and exact-URL matching cover the common cases (unchanged content on a
periodic re-fetch) without that dependency.
"""

from __future__ import annotations

import hashlib
import sqlite3


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def is_duplicate(conn: sqlite3.Connection, url: str, content_hash_value: str, source_key: str) -> bool:
    """True if this exact URL is already stored, or if identical content
    from the same source is already stored under a different URL."""
    row = conn.execute(
        "SELECT 1 FROM knowledge_documents WHERE url = ? LIMIT 1;", (url,)
    ).fetchone()
    if row is not None:
        return True
    row = conn.execute(
        "SELECT 1 FROM knowledge_documents WHERE source = ? AND content_hash = ? LIMIT 1;",
        (source_key, content_hash_value),
    ).fetchone()
    return row is not None
