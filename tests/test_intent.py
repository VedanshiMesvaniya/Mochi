from datetime import datetime

from app.character.state_machine import CharacterState, Emotion
from app.ai.intent import detect_intent

NOW = datetime(2026, 8, 14, 15, 0, 0)  # 3:00 PM, for deterministic time math


def test_greeting():
    result = detect_intent("hey mochi", now=NOW)
    assert result.name == "greeting"
    assert result.emotion == Emotion.HAPPY


def test_farewell():
    result = detect_intent("bye mochi", now=NOW)
    assert result.name == "farewell"


def test_reminder_with_absolute_time():
    result = detect_intent("remind me to call mom at 7pm", now=NOW)
    assert result.name == "create_reminder"
    assert result.tool == "create_reminder"
    assert result.tool_args["title"] == "Call mom"
    assert result.tool_args["datetime_iso"] == "2026-08-14T19:00:00"


def test_reminder_with_relative_time():
    result = detect_intent("remind me to stretch in 30 minutes", now=NOW)
    assert result.name == "create_reminder"
    assert result.tool_args["datetime_iso"] == "2026-08-14T15:30:00"


def test_reminder_without_time_asks_for_one():
    result = detect_intent("remind me to push code", now=NOW)
    assert result.name == "create_reminder_needs_time"
    assert result.tool is None


def test_timer_with_duration():
    result = detect_intent("set a timer for 10 minutes", now=NOW)
    assert result.tool == "start_timer"
    assert result.tool_args["duration_seconds"] == 600


def test_task_creation():
    result = detect_intent("remember that i need to buy milk", now=NOW)
    assert result.tool == "create_task"
    assert result.tool_args["title"] == "Buy milk"


def test_insult_is_not_confused_with_greeting():
    """Regression test: the greeting keyword 'yo' used to match as a
    substring inside 'you', e.g. 'you're so stupid' -> false greeting."""
    result = detect_intent("you're so stupid", now=NOW)
    assert result.name == "insult"
    assert result.emotion == Emotion.ANNOYED


def test_how_are_you_is_not_confused_with_greeting():
    result = detect_intent("how are you", now=NOW)
    assert result.name == "how_are_you"


def test_what_are_you_doing_is_not_confused_with_greeting():
    result = detect_intent("what are you doing", now=NOW)
    assert result.name == "what_doing"


def test_identity_question_gets_a_clear_direct_answer():
    result = detect_intent("are you cat?", now=NOW)
    assert result.name == "identity"
    assert "mochi" in result.response.lower()
    assert "emo" not in result.response.lower()


def test_what_are_you_doing_is_not_swallowed_by_identity_check():
    """Regression: 'what are you' is a substring of 'what are you doing',
    so identity detection must run after WHAT_DOING, not before it."""
    result = detect_intent("what are you doing", now=NOW)
    assert result.name == "what_doing"


def test_unknown_message_still_reacts():
    result = detect_intent("asdkjaslkdj", now=NOW)
    assert result.name == "unknown"
    assert result.animation == CharacterState.THINKING


def test_empty_message():
    result = detect_intent("   ", now=NOW)
    assert result.name == "empty"
