"""
Quick timer scheduler (V2).

Same pattern as `app/reminders/scheduler.py`, but polls much more often
(default every 1s) since timers are short, immediate countdowns and users
expect near-instant notification when one finishes - unlike reminders,
which are fine being noticed within ~15s of becoming due.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer

from app.core.events import Events, event_bus
from app.core.logger import get_logger
from app.timers import manager

logger = get_logger("mochi.timers.scheduler")

DEFAULT_POLL_INTERVAL_MS = 1_000


class TimerScheduler(QObject):
    def __init__(self, poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._poll_interval_ms = poll_interval_ms

    def start(self) -> None:
        manager.ensure_ready()
        self.poll()  # catch up on anything that finished while the app was closed
        self._timer.start(self._poll_interval_ms)
        logger.info("Timer scheduler started (poll every %sms)", self._poll_interval_ms)

    def stop(self) -> None:
        self._timer.stop()

    def poll(self) -> None:
        try:
            due = manager.list_due_timers()
        except Exception:  # noqa: BLE001 - scheduler must never crash the app
            logger.exception("Failed to poll for due timers")
            return

        for timer in due:
            logger.info("Timer done: #%s '%s'", timer.id, timer.label)
            manager.mark_notified(timer.id)
            event_bus.publish(
                Events.TIMER_DONE,
                {"id": timer.id, "label": timer.label, "duration_seconds": timer.duration_seconds},
            )
