from app.ai.chat_engine import handle_message
from app.character.state_machine import CharacterState, Emotion
from app.reminders import manager as reminder_manager


def test_greeting_reaction_has_no_side_effects():
    reaction = handle_message("hi mochi")
    assert reaction.emotion == Emotion.HAPPY
    assert reaction.animation == CharacterState.HAPPY
    assert reaction.text


def test_reminder_message_actually_creates_a_reminder(temp_db):
    reaction = handle_message("remind me to test the chat engine in 5 minutes")
    assert reaction.text

    after = reminder_manager.list_reminders()
    assert len(after) == 1
    assert after[-1].title == "Test the chat engine"


def test_malformed_input_never_crashes():
    reaction = handle_message("")
    assert reaction.text
