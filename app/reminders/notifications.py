"""
Bridges `Events.REMINDER_DUE` to observable behavior (spec section 21):

    1. Mochi wakes up
    2. Plays a notification sound
    3. Performs a wake animation
    4. Shows a speech bubble with the reminder text
    5. Shows a desktop notification (via the system tray icon)

This module intentionally does the wiring, not the rendering - the pet
window and tray icon are passed in and just get told what to do.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QSystemTrayIcon

from app.character.state_machine import CharacterState
from app.core.events import Events, event_bus
from app.core.logger import get_logger

logger = get_logger("mochi.notifications")


class ReminderNotifier:
    def __init__(self, pet_window, tray_icon: Optional[QSystemTrayIcon] = None) -> None:
        self.pet_window = pet_window
        self.tray_icon = tray_icon
        event_bus.subscribe(Events.REMINDER_DUE, self._on_reminder_due)

    def _on_reminder_due(self, payload: dict) -> None:
        title = payload.get("title", "Reminder")
        logger.info("Notifying user about due reminder: %s", title)

        # 1 & 3: wake the character + animation
        self.pet_window.state_machine.set_state(CharacterState.WAKE)

        # 2: sound (via event bus - the (future) sound player subscribes to this)
        event_bus.publish(Events.SOUND_REQUESTED, {"sound": "chirp"})

        # 4: speech bubble
        message = f'Hey! You asked me to remind you: "{title}"'
        if hasattr(self.pet_window, "show_speech_bubble"):
            self.pet_window.show_speech_bubble(message)
        else:
            logger.debug("Pet window has no speech bubble yet; message was: %s", message)

        # 5: OS-level desktop notification
        if self.tray_icon is not None:
            self.tray_icon.showMessage(
                "Mochi",
                message,
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )
