"""
Scheduler (spec section 8) - decides which registered sources are due to
be checked, then runs each due source through the full pipeline: fetch ->
parse -> dedup/classify (inside knowledge_store.save_document) -> store.

`run_ingestion_cycle()` is the single entry point meant to be wired into
a periodic job or a manual "refresh" action - see
app/character/pet.py's _RefreshTrendsWorker, which calls this the same
opt-in, best-effort way it already calls
app/humor/trend_fetcher.fetch_trends() and
app/humor/meme_fetcher.fetch_memes(). Everything here degrades silently
on any single-source failure (spec section 10's Fetcher contract) so one
misbehaving source can never stop the others from updating, and this
function itself never raises.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.core.logger import get_logger
from app.knowledge import fetcher, knowledge_store, parser, source_manager
from app.knowledge.models import Source

logger = get_logger("mochi.knowledge.scheduler")


def _is_due(source: Source, fetch_state) -> bool:
    """A source with no prior fetch_state row is always due. Otherwise
    due once `source.frequency_hours` have elapsed since
    `last_checked_at` (spec section 8's per-source frequency policy)."""
    if fetch_state is None:
        return True
    try:
        last_checked = datetime.fromisoformat(fetch_state["last_checked_at"])
    except (TypeError, ValueError):
        return True
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=timezone.utc)
    elapsed_hours = (datetime.now(timezone.utc) - last_checked).total_seconds() / 3600.0
    return elapsed_hours >= source.frequency_hours


def _ingest_source(source: Source) -> int:
    """Runs one source through fetch -> parse -> store. Returns the
    number of newly-stored (non-duplicate) documents. Never raises - any
    exception is logged and treated as 0 documents ingested for this
    source, so scheduler.run_ingestion_cycle can safely move on to the
    next one."""
    try:
        fetch_state = knowledge_store.get_fetch_state(source.key)
        if not _is_due(source, fetch_state):
            return 0

        previous_etag = fetch_state["etag"] if fetch_state else None
        previous_last_modified = fetch_state["last_modified"] if fetch_state else None
        previous_hash = fetch_state["content_hash"] if fetch_state else None

        raw = fetcher.fetch_source(
            source,
            previous_etag=previous_etag,
            previous_last_modified=previous_last_modified,
            previous_hash=previous_hash,
        )

        if raw.status == "failed":
            logger.info("Ingestion skipped for %s: %s", source.key, raw.error)
            return 0

        # Record the fetch attempt regardless of whether anything new was
        # found, so a "not_modified" result still moves last_checked_at
        # forward and the scheduler doesn't re-check it again immediately.
        knowledge_store.update_fetch_state(
            source.key, raw.etag, raw.last_modified, raw.content_hash
        )

        if raw.status != "fetched":
            return 0

        documents = parser.parse_fetch(raw, source)
        stored = 0
        for document in documents:
            try:
                if knowledge_store.save_document(document, source):
                    stored += 1
            except Exception:  # noqa: BLE001 - one bad document must not lose the rest
                logger.exception("Failed to store a document from %s", source.key)
        logger.info(
            "Ingested %s: %d new document(s) (%d seen this fetch)",
            source.key, stored, len(documents),
        )
        return stored
    except Exception:  # noqa: BLE001 - a single source must never abort the cycle
        logger.exception("Ingestion failed unexpectedly for %s", source.key)
        return 0


def run_ingestion_cycle() -> int:
    """Runs every due, enabled source through the pipeline and purges
    expired temporal documents. Returns the total number of newly-stored
    documents. Safe to call even when settings.web_knowledge_enabled is
    False - it simply does nothing and returns 0, matching
    fetch_trends()/fetch_memes()'s own "no-op when disabled" contract, so
    callers (e.g. pet.py's manual refresh) don't need their own gate."""
    if not settings.web_knowledge_enabled:
        return 0

    total_stored = 0
    for source in source_manager.get_enabled_sources():
        total_stored += _ingest_source(source)

    try:
        purged = knowledge_store.purge_expired()
        if purged:
            logger.info("Purged %d expired temporal document(s)", purged)
    except Exception:  # noqa: BLE001 - purge failure must not hide successful ingestion
        logger.exception("Failed to purge expired knowledge documents (non-fatal)")

    return total_stored
