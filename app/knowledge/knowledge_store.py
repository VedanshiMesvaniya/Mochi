"""
Knowledge Store (spec section 19) - the persistence layer for everything
the Web Knowledge Engine ingests, plus the freshness-aware retrieval used
by context_engine.py (spec section 21).

v1.1 implements Layer A (Raw Documents, spec section 19) as
`knowledge_documents` - provenance-tagged raw content, one row per
Document. Layer B (semantic/vector index) and Layer C (structured
claims table) are intentionally deferred: v1.1's retrieval instead uses a
lightweight keyword-overlap relevance score combined with
freshness.retrieval_score, which needs no embeddings dependency and keeps
this package stdlib-only, consistent with the rest of Mochi's opt-in
network features. Claim-level extraction/verification (spec sections
25-27) is future work - `confidence` is stored per-document today as a
fixed per-source-kind estimate, not yet a calibrated per-claim value.

Writes are additive-only in the sense that no *other* table is touched -
`knowledge_documents` itself does get updated in place when a document
already stored at a given URL is re-fetched with different content (see
save_document and app/knowledge/dedup.py's module docstring). Rows also
carry a `last_verified_at` timestamp, separate from `retrieved_at`: the
former is "when Mochi last confirmed this URL's content", the latter is
"when this exact stored content was first (or most recently updated to
be) retrieved" - see the migration in app/memory/database.py for how an
existing database picks these columns up.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from app.core.logger import get_logger
from app.knowledge import dedup, freshness
from app.knowledge.classifier import classify
from app.knowledge.models import Document, EvidenceItem, Source
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.knowledge.store")

# Fixed per-source-kind confidence estimate (see module docstring - not
# yet a calibrated per-claim value). Reddit discussion is evidence of
# sentiment/discussion, not verified fact, so it starts noticeably lower
# than an RSS feed pulled from an established publication.
_DEFAULT_CONFIDENCE = {"reddit": 0.55, "rss": 0.75}

# How much of a document's body to hand the LLM as grounding evidence
# (spec section 30) - enough to actually answer from, still far short of
# the full stored content (bounded by parser._MAX_CONTENT_CHARS already).
_EXCERPT_CHARS = 400

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "what", "whats", "when",
    "where", "who", "how", "do", "does", "did", "in", "on", "of", "for",
    "to", "and", "or", "it", "this", "that", "with", "about", "right",
    "now", "today", "current", "currently", "latest", "recent",
}


def _tokenize(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def save_document(document: Document, source: Source) -> bool:
    """Persist one Document, handling three cases (see dedup.py's module
    docstring for the full reasoning):

      1. New URL, and no identical content already stored under a
         different URL -> INSERT a new row.
      2. Same URL, content unchanged -> not a new/updated row; just bump
         `last_verified_at` so callers can tell "still current as of X"
         apart from "never re-checked". Returns False, same as a plain
         duplicate skip, since nothing new was stored.
      3. Same URL, content changed -> UPDATE the existing row in place
         (new content/hash/timestamps, `revision` incremented) rather
         than silently skipping it - a living page (docs, a news
         article, a release page) must not get frozen at whatever it
         said the first time Mochi ever fetched it. Returns True since
         the stored knowledge did change.

    Never raises for a single bad document - a malformed one is logged
    and skipped so it can never take down an ingestion cycle."""
    initialize_schema()
    content_hash_value = dedup.content_hash(document.content)
    category, expires_at = classify(source, document.retrieved_at)
    confidence = _DEFAULT_CONFIDENCE.get(source.kind, 0.6)
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        existing = dedup.find_by_url(conn, document.url)

        if existing is None:
            if dedup.is_content_duplicate(conn, source.key, content_hash_value):
                return False
            conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_documents
                    (source, url, title, content, content_hash, category,
                     source_authority, confidence, published_at, retrieved_at,
                     last_verified_at, expires_at, revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
                """,
                (
                    source.key,
                    document.url,
                    document.title,
                    document.content,
                    content_hash_value,
                    category,
                    source.authority,
                    confidence,
                    document.published_at,
                    document.retrieved_at,
                    now_iso,
                    expires_at,
                ),
            )
            return True

        if existing["content_hash"] == content_hash_value:
            # Unchanged - re-verified, not re-discovered. Still worth
            # recording that Mochi checked and the content held up.
            conn.execute(
                "UPDATE knowledge_documents SET last_verified_at = ? WHERE id = ?;",
                (now_iso, existing["id"]),
            )
            return False

        # Same URL, different content - the page changed since we last
        # fetched it. Update in place rather than leaving the old
        # content stored forever under this URL.
        conn.execute(
            """
            UPDATE knowledge_documents
            SET title = ?, content = ?, content_hash = ?, category = ?,
                source_authority = ?, confidence = ?, published_at = ?,
                retrieved_at = ?, last_verified_at = ?, expires_at = ?,
                revision = revision + 1
            WHERE id = ?;
            """,
            (
                document.title,
                document.content,
                content_hash_value,
                category,
                source.authority,
                confidence,
                document.published_at,
                document.retrieved_at,
                now_iso,
                expires_at,
                existing["id"],
            ),
        )
        logger.info(
            "Knowledge document updated in place (revision bump): %s", document.url
        )
        return True


def get_fetch_state(source_key: str):
    """Row (etag, last_modified, content_hash, last_checked_at) for a
    source, or None if it has never been fetched before."""
    initialize_schema()
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM knowledge_fetch_state WHERE source_key = ?;", (source_key,)
        ).fetchone()


def update_fetch_state(
    source_key: str,
    etag: Optional[str],
    last_modified: Optional[str],
    content_hash: Optional[str],
) -> None:
    initialize_schema()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_fetch_state
                (source_key, etag, last_modified, content_hash, last_checked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                content_hash = excluded.content_hash,
                last_checked_at = excluded.last_checked_at;
            """,
            (source_key, etag, last_modified, content_hash, now_iso),
        )


def purge_expired() -> int:
    """Delete temporal documents past their expires_at (spec section 16 -
    an expired trend/meme should no longer be treated as current). Rows
    with expires_at IS NULL (persistent knowledge) are never touched."""
    initialize_schema()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM knowledge_documents WHERE expires_at IS NOT NULL AND expires_at <= ?;",
            (now_iso,),
        )
        return cursor.rowcount if cursor.rowcount is not None else 0


def query_relevant(query_text: str, limit: int = 3) -> list[EvidenceItem]:
    """Freshness-and-relevance-ranked evidence for `query_text` (spec
    section 21's Retrieval Score, applied over a lightweight keyword
    overlap relevance measure rather than a vector index - see module
    docstring). Only non-expired rows are ever considered - purge_expired
    is also invoked here defensively so a query never surfaces something
    that should already be gone even if the last scheduled purge was
    skipped."""
    initialize_schema()
    purge_expired()
    query_tokens = _tokenize(query_text)
    if not query_tokens:
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_documents "
            "WHERE expires_at IS NULL OR expires_at > ? "
            "ORDER BY retrieved_at DESC LIMIT 200;",
            (now_iso,),
        ).fetchall()

    scored: list[EvidenceItem] = []
    for row in rows:
        doc_tokens = _tokenize(f"{row['title'] or ''} {row['content'] or ''}")
        if not doc_tokens:
            continue
        overlap = len(query_tokens & doc_tokens)
        if overlap == 0:
            continue
        relevance = overlap / len(query_tokens)
        # Content age (for the freshness *label*, which is what
        # determines ranking) is based on published_at when we know it -
        # that's when the information itself became true/current. When
        # published_at is unknown, retrieved_at is the best available
        # proxy. Either way, retrieved_at/last_verified_at are still kept
        # on the EvidenceItem itself so a caller can separately see "when
        # Mochi last checked this" versus "how old is the information".
        age_source = row["published_at"] or row["retrieved_at"]
        hours = freshness.age_hours(age_source)
        label = freshness.categorize(hours, row["category"])
        score = freshness.retrieval_score(
            relevance, label, row["source_authority"], row["confidence"]
        )
        content = row["content"] or ""
        scored.append(
            EvidenceItem(
                claim=row["title"] or content[:120],
                excerpt=content[:_EXCERPT_CHARS],
                url=row["url"],
                source=row["source"],
                category=row["category"],
                authority=row["source_authority"],
                published_at=row["published_at"],
                retrieved_at=row["retrieved_at"],
                last_verified_at=row["last_verified_at"] if "last_verified_at" in row.keys() else None,
                freshness=label,
                confidence=row["confidence"],
                score=score,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]
