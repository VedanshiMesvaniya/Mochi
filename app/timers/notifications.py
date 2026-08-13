"""
Bridges `Events.TIMER_DONE` to observable behavior - same idea as
`app/reminders/notifications.py`, kept as a separate class since timers and
reminders are conceptually distinct (spec section 25's `timer.start()` vs
reminder tools), even though the notification shape is nearly identical.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QSystemTrayIcon

from app.character.state_machine import CharacterState
from app.core.events import Events, event_bus
from app.core.logger import get_logger

logger = get_logger("mochi.timers.notifications")


class TimerNotifier:
    def __init__(self, pet_window, tray_icon: Optional[QSystemTrayIcon] = None) -> None:
        self.pet_window = pet_window
        self.tray_icon = tray_icon
        event_bus.subscribe(Events.TIMER_DONE, self._on_timer_done)

    def _on_timer_done(self, payload: dict) -> None:
        label = payload.get("label", "Timer")
        logger.info("Notifying user that timer finished: %s", label)

        self.pet_window.state_machine.set_state(CharacterState.JUMP)
        event_bus.publish(Events.SOUND_REQUESTED, {"sound": "notification"})

        message = f'"{label}" is done!'
        if hasattr(self.pet_window, "show_speech_bubble"):
            self.pet_window.show_speech_bubble(message)
        else:
            logger.debug("Pet window has no speech bubble yet; message was: %s", message)

        if self.tray_icon is not None:
            self.tray_icon.showMessage(
                "Mochi", message, QSystemTrayIcon.MessageIcon.Information, 6000
            )
