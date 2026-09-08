"""
Deduplication + revision detection (spec section 13) - the same
underlying content can arrive through multiple URLs, or unchanged across
successive ingestion cycles, or *changed* at the same URL (a living page
that gets edited - documentation, a news article, a GitHub release note).
knowledge_store.save_document uses this module to tell those three cases
apart before deciding whether to skip, update in place, or insert:

  1. Same URL, same content hash - genuinely unchanged. Not a new row;
     the existing one just gets `last_verified_at` bumped so callers can
     tell "still current as of X" from "we haven't checked since Y".
  2. Same URL, different content hash - the page changed. This must
     UPDATE the existing row (bumping `revision`), never silently skip
     it - `url` is UNIQUE, so an unconditional INSERT would fail here
     anyway, and a plain "already have this URL -> skip" check (the old
     behavior) would freeze that row's content at whatever it was the
     first time it was ever fetched, forever.
  3. Different URL, same content hash within the same source - the same
     item re-appearing at a slightly different URL (e.g. a reddit post's
     permalink changing case, a tracking-parameter variant of an RSS
     link). Treated as a duplicate and skipped, same as before.

True near-duplicate similarity (spec section 13's "similarity detection"
across genuinely different phrasings of the same story) is intentionally
out of scope for v1.1 - it needs a similarity metric heavier than this
package's stdlib-only, no-embeddings-dependency scope allows. Exact-hash
and exact-URL matching cover the common cases without that dependency.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def find_by_url(conn: sqlite3.Connection, url: str) -> Optional[sqlite3.Row]:
    """The existing knowledge_documents row for this exact URL, or None if
    this URL has never been stored before."""
    return conn.execute(
        "SELECT * FROM knowledge_documents WHERE url = ? LIMIT 1;", (url,)
    ).fetchone()


def is_content_duplicate(conn: sqlite3.Connection, source_key: str, content_hash_value: str) -> bool:
    """True if identical content from the same source is already stored
    under a *different* URL. Only meaningful once find_by_url() has
    already confirmed this exact URL isn't stored - this catches the
    cross-URL duplicate case (case 3 in the module docstring), not the
    same-URL update case (case 2), which save_document handles itself."""
    row = conn.execute(
        "SELECT 1 FROM knowledge_documents WHERE source = ? AND content_hash = ? LIMIT 1;",
        (source_key, content_hash_value),
    ).fetchone()
    return row is not None
