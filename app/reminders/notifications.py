"""
Bridges `Events.REMINDER_DUE` to observable behavior (spec section 21):

    1. Mochi perks up (ALERT)
    2. Plays a notification sound
    3. Shows a speech bubble with the reminder text
    4. Shows a desktop notification (via the system tray icon)

Also implements "become annoyed if you ignore reminders": if a reminder is
still pending a while after being surfaced, Mochi reacts with ANGRY once.
This module intentionally does the wiring, not the rendering - the pet
window and tray icon are passed in and just get told what to do.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QSystemTrayIcon

from app.character.state_machine import CharacterState
from app.core.events import Events, event_bus
from app.core.logger import get_logger
from app.reminders import manager

logger = get_logger("mochi.notifications")

# How long to wait after surfacing a reminder before checking whether it's
# still pending and reacting annoyed - spec: "become annoyed if you ignore
# reminders". Kept modest by default; not user-configurable yet.
IGNORED_CHECK_DELAY_MS = 5 * 60 * 1000


class ReminderNotifier:
    def __init__(self, pet_window, tray_icon: Optional[QSystemTrayIcon] = None) -> None:
        self.pet_window = pet_window
        self.tray_icon = tray_icon
        event_bus.subscribe(Events.REMINDER_DUE, self._on_reminder_due)

    def _on_reminder_due(self, payload: dict) -> None:
        title = payload.get("title", "Reminder")
        reminder_id = payload.get("id")
        logger.info("Notifying user about due reminder: %s", title)

        # 1: perk up
        self.pet_window.state_machine.set_state(CharacterState.ALERT)

        # 2: sound (via event bus - the (future) sound player subscribes to this)
        event_bus.publish(Events.SOUND_REQUESTED, {"sound": "chirp"})

        # 3: speech bubble
        message = f'Hey! You asked me to remind you: "{title}"'
        if hasattr(self.pet_window, "show_speech_bubble"):
            self.pet_window.show_speech_bubble(message)
        else:
            logger.debug("Pet window has no speech bubble yet; message was: %s", message)

        # 4: OS-level desktop notification
        if self.tray_icon is not None:
            self.tray_icon.showMessage(
                "Mochi",
                message,
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )

        # "Become annoyed if you ignore reminders": check back later, and
        # only react if it's genuinely still pending (not completed,
        # snoozed to a new due time, or cancelled in the meantime).
        if reminder_id is not None:
            QTimer.singleShot(
                IGNORED_CHECK_DELAY_MS,
                lambda: self._check_if_ignored(reminder_id, title),
            )

    def _check_if_ignored(self, reminder_id: int, title: str) -> None:
        try:
            reminder = manager.get_reminder(reminder_id)
        except Exception:  # noqa: BLE001 - notifier must never crash the app
            logger.exception("Failed to check ignored-reminder status for #%s", reminder_id)
            return

        if reminder is None or reminder.status != manager.ReminderStatus.PENDING:
            return  # completed/snoozed/cancelled - nothing to be annoyed about

        logger.info("Reminder #%s ('%s') still ignored - reacting annoyed", reminder_id, title)
        self.pet_window.state_machine.set_state(CharacterState.ANGRY)
        event_bus.publish(Events.REMINDER_IGNORED, {"id": reminder_id, "title": title})
        if hasattr(self.pet_window, "show_speech_bubble"):
            self.pet_window.show_speech_bubble(f'Hmph. You still haven\'t "{title}"...')
