"""
Project Mochi - application entry point.

Phase 1 scope (see README "Development Phases"): transparent desktop
character with idle/walk/drag behavior, system tray, and exit menu. No AI
yet - that lands in Phase 2 (app/ai/).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.character.pet import PetWindow
from app.core.config import settings
from app.core.events import Events, event_bus
from app.core.logger import get_logger
from app.reminders.notifications import ReminderNotifier
from app.reminders.scheduler import ReminderScheduler
from app.timers.notifications import TimerNotifier
from app.timers.scheduler import TimerScheduler
from app.ui.tray import build_tray_icon

logger = get_logger("mochi.main")


def main() -> int:
    settings.ensure_directories()
    logger.info("Starting Mochi (LLM model configured: %s)", settings.llm_model)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep running via tray even if window closes

    pet_window = PetWindow()
    pet_window.show()

    tray = build_tray_icon(app, pet_window)  # noqa: F841 - must stay referenced to avoid GC

    # Local reminders (V1): scheduler polls SQLite for due reminders and
    # publishes an event; the notifier turns that into character behavior
    # + a desktop notification. Fully local, no AI/network involved.
    reminder_notifier = ReminderNotifier(pet_window, tray)  # noqa: F841
    reminder_scheduler = ReminderScheduler()
    reminder_scheduler.start()

    # Quick countdown timers (V2): same pattern, faster poll interval.
    timer_notifier = TimerNotifier(pet_window, tray)  # noqa: F841
    timer_scheduler = TimerScheduler()
    timer_scheduler.start()

    event_bus.publish(Events.APP_STARTUP)

    exit_code = app.exec()

    reminder_scheduler.stop()
    timer_scheduler.stop()
    event_bus.publish(Events.APP_SHUTDOWN)
    logger.info("Mochi shut down (exit code %s)", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
