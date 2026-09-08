"""
Scheduler (spec section 8) - decides which registered sources are due to
be checked, then runs each due source through the full pipeline: fetch ->
parse -> dedup/classify (inside knowledge_store.save_document) -> store.

`run_ingestion_cycle()` is the pipeline entry point, called from two
places:

  1. Manually, via app/character/pet.py's _RefreshTrendsWorker (the
     "Refresh trends & memes" menu action) - the same opt-in, best-effort
     way it already calls app/humor/trend_fetcher.fetch_trends() and
     app/humor/meme_fetcher.fetch_memes().
  2. Automatically, via `KnowledgeScheduler` below, wired into
     app/main.py's application lifecycle the same way
     app/reminders/scheduler.py and app/timers/scheduler.py are. Without
     this, ingestion only ever ran when a person happened to click
     "Refresh trends & memes" - `run_ingestion_cycle` existed but nothing
     in the app actually called it on its own.

Everything here degrades silently on any single-source failure (spec
section 10's Fetcher contract) so one misbehaving source can never stop
the others from updating, and `run_ingestion_cycle` itself never raises.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from app.core.config import settings
from app.core.logger import get_logger
from app.knowledge import fetcher, knowledge_store, parser, source_manager
from app.knowledge.models import Source

logger = get_logger("mochi.knowledge.scheduler")

# Fallback poll interval if settings.web_knowledge_fetch_interval_hours is
# ever misconfigured to <= 0 - avoids a runaway QTimer firing constantly.
_MIN_POLL_INTERVAL_HOURS = 0.25


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


class _IngestionWorker(QThread):
    """Runs run_ingestion_cycle() off the Qt GUI thread - a network call,
    even a fast/failed one, has no business running on the same thread
    that's driving the character's animation timer, same reasoning as
    app/character/pet.py's _RefreshTrendsWorker/_HumorWorker."""

    finished_cycle = Signal(int)

    def run(self) -> None:  # noqa: D102 - QThread override
        try:
            stored = run_ingestion_cycle()
        except Exception:  # noqa: BLE001 - a background cycle must never crash the app
            logger.exception("Automatic knowledge ingestion failed unexpectedly")
            stored = 0
        self.finished_cycle.emit(stored)


class KnowledgeScheduler(QObject):
    """Periodic, opt-in background trigger for run_ingestion_cycle(),
    wired into app/main.py the same way ReminderScheduler/TimerScheduler
    are (see their docstrings). The QTimer itself just decides *when* to
    check; the actual fetch/parse/store work always runs on a background
    _IngestionWorker thread, never inline on the GUI thread that owns
    this QTimer.

    Safe to start() even when settings.web_knowledge_enabled is False -
    the QTimer still ticks, but each tick's run_ingestion_cycle() call is
    already a no-op when disabled (see that function's docstring), and
    individual sources are only actually fetched when they're due per
    their own frequency_hours. This mirrors ReminderScheduler/
    TimerScheduler always running regardless of whether there happen to
    be any reminders/timers yet.
    """

    def __init__(self, poll_interval_ms: "int | None" = None) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.trigger_cycle)
        self._poll_interval_ms = poll_interval_ms or self._interval_ms_from_settings()
        self._worker: "_IngestionWorker | None" = None

    @staticmethod
    def _interval_ms_from_settings() -> int:
        hours = max(settings.web_knowledge_fetch_interval_hours, _MIN_POLL_INTERVAL_HOURS)
        return int(hours * 3600 * 1000)

    def start(self) -> None:
        self._timer.start(self._poll_interval_ms)
        logger.info(
            "Knowledge scheduler started (poll every %.2fh, web_knowledge_enabled=%s)",
            self._poll_interval_ms / 3_600_000,
            settings.web_knowledge_enabled,
        )

    def stop(self) -> None:
        self._timer.stop()
        if self._worker is not None:
            self._worker.wait(100)

    def trigger_cycle(self) -> None:
        """Starts one ingestion cycle on a background thread, unless one
        is already running - a slow/stuck cycle must never pile up
        overlapping worker threads on the next tick."""
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _IngestionWorker(self)
        self._worker.finished_cycle.connect(self._on_cycle_finished)
        self._worker.start()

    def _on_cycle_finished(self, stored: int) -> None:
        if stored:
            logger.info("Automatic knowledge ingestion stored %d new/updated document(s)", stored)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
