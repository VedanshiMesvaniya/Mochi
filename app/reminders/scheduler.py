"""
Local reminder scheduler (spec section 20/21).

Polls the reminders table on a QTimer (default every 15s) for pending
reminders whose due_at has passed and that haven't been notified yet, and
publishes `Events.REMINDER_DUE` for each one. This module does NOT touch
Qt widgets directly - `app/reminders/notifications.py` and the character
window subscribe to the event instead, keeping the scheduler UI-agnostic
and unit-testable.

Startup catch-up: if the app was closed when a reminder became due, the
first `poll()` call after startup will still find it (due_at <= now) and
surface it then, per spec section 20's "one limitation" note.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer

from app.core.events import Events, event_bus
from app.core.logger import get_logger
from app.reminders import manager

logger = get_logger("mochi.scheduler")

DEFAULT_POLL_INTERVAL_MS = 15_000


class ReminderScheduler(QObject):
    def __init__(self, poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._poll_interval_ms = poll_interval_ms

    def start(self) -> None:
        manager.ensure_ready()
        self.poll()  # catch up on anything missed while the app was closed
        self._timer.start(self._poll_interval_ms)
        logger.info(
            "Reminder scheduler started (poll every %sms)", self._poll_interval_ms
        )

    def stop(self) -> None:
        self._timer.stop()

    def poll(self) -> None:
        try:
            due = manager.list_due_reminders()
        except Exception:  # noqa: BLE001 - scheduler must never crash the app
            logger.exception("Failed to poll for due reminders")
            return

        for reminder in due:
            logger.info("Reminder due: #%s '%s'", reminder.id, reminder.title)
            manager.mark_notified(reminder.id)
            event_bus.publish(
                Events.REMINDER_DUE,
                {
                    "id": reminder.id,
                    "title": reminder.title,
                    "due_at": reminder.due_at,
                    "repeat_rule": reminder.repeat_rule,
                },
            )
