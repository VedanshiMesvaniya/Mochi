from datetime import datetime, timedelta

from app.character.state_machine import CharacterState, CharacterStateMachine
from app.reminders import manager
from app.reminders.notifications import ReminderNotifier


class FakePetWindow:
    """Minimal stand-in for PetWindow - just enough surface for the
    notifier to drive (state machine + speech bubble)."""

    def __init__(self) -> None:
        self.state_machine = CharacterStateMachine()
        self.bubbles: list[str] = []

    def show_speech_bubble(self, text: str, duration_ms: int = 0) -> None:
        self.bubbles.append(text)


def _make_reminder(temp_db):
    manager.ensure_ready()
    due = datetime.now() - timedelta(minutes=1)
    reminder = manager.create_reminder("Water the plants", due)
    return reminder


def test_reminder_due_sets_alert_state_and_speech_bubble(temp_db):
    pet = FakePetWindow()
    ReminderNotifier(pet)
    reminder = _make_reminder(temp_db)

    from app.core.events import Events, event_bus

    event_bus.publish(
        Events.REMINDER_DUE,
        {"id": reminder.id, "title": reminder.title, "due_at": reminder.due_at, "repeat_rule": None},
    )

    assert pet.state_machine.state == CharacterState.ALERT
    assert pet.bubbles and "Water the plants" in pet.bubbles[-1]


def test_ignored_reminder_becomes_angry(temp_db):
    pet = FakePetWindow()
    notifier = ReminderNotifier(pet)
    reminder = _make_reminder(temp_db)

    # Simulate the delayed ignored-check firing directly, rather than
    # waiting for the real QTimer.singleShot delay in a test.
    notifier._check_if_ignored(reminder.id, reminder.title)

    assert pet.state_machine.state == CharacterState.ANGRY
    assert "haven't" in pet.bubbles[-1].lower()


def test_completed_reminder_is_not_treated_as_ignored(temp_db):
    pet = FakePetWindow()
    notifier = ReminderNotifier(pet)
    reminder = _make_reminder(temp_db)
    manager.complete_reminder(reminder.id)

    notifier._check_if_ignored(reminder.id, reminder.title)

    # Should never have been pushed to ANGRY - state machine stays IDLE.
    assert pet.state_machine.state == CharacterState.IDLE
    assert pet.bubbles == []


def test_ignored_check_handles_deleted_reminder_gracefully(temp_db):
    pet = FakePetWindow()
    notifier = ReminderNotifier(pet)
    # No reminder with this id exists at all.
    notifier._check_if_ignored(9999, "Ghost reminder")
    assert pet.state_machine.state == CharacterState.IDLE
