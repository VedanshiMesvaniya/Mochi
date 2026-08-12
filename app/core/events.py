"""
A tiny synchronous, in-process event bus.

This decouples subsystems (character, reminders, calendar, chat UI) so, for
example, the reminder scheduler can tell the character to "wake up and speak"
without importing PySide6 widgets directly, and without routing everything
through the LLM (spec section 12: the LLM must not be called for every
behavior/movement decision).

Usage:
    from app.core.events import event_bus

    def on_reminder_due(payload):
        ...

    event_bus.subscribe("reminder.due", on_reminder_due)
    event_bus.publish("reminder.due", {"title": "Call Mom"})
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, DefaultDict, List

from app.core.logger import get_logger

logger = get_logger("mochi.events")

EventHandler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self._subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_name)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def publish(self, event_name: str, payload: Any = None) -> None:
        for handler in list(self._subscribers.get(event_name, [])):
            try:
                handler(payload)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break others
                logger.exception("Error in handler for event '%s'", event_name)


# Shared singleton used across the whole app.
event_bus = EventBus()


# --- Well-known event name constants (avoids typos across modules) ---
class Events:
    REMINDER_DUE = "reminder.due"
    REMINDER_CREATED = "reminder.created"

    CHAT_MESSAGE_RECEIVED = "chat.message_received"
    CHAT_RESPONSE_READY = "chat.response_ready"

    EMOTION_CHANGED = "character.emotion_changed"
    ANIMATION_REQUESTED = "character.animation_requested"
    SOUND_REQUESTED = "character.sound_requested"

    CALENDAR_CONFIRMATION_REQUIRED = "calendar.confirmation_required"

    APP_STARTUP = "app.startup"
    APP_SHUTDOWN = "app.shutdown"
