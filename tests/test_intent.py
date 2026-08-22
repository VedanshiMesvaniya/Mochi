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


def test_expression_command_make_face():
    """spec: 'in chat if i say make <expression> for me then it show me
    cutely for me' - a direct on-demand expression request."""
    result = detect_intent("make happy for me", now=NOW)
    assert result.name == "expression_request"
    assert result.animation == CharacterState.HAPPY


def test_expression_command_variants():
    for text, expected in [
        ("make a wink face", CharacterState.WINK),
        ("show me your wink", CharacterState.WINK),
        ("do sad expression", CharacterState.SAD),
        ("can you make a heart face for me", CharacterState.HEART),
        ("make surprised", CharacterState.SURPRISED),
    ]:
        result = detect_intent(text, now=NOW)
        assert result.name == "expression_request", text
        assert result.animation == expected, text


def test_expression_command_ignores_non_expression_make_requests():
    """'make' alone shouldn't hijack unrelated messages that just happen
    to contain the word."""
    result = detect_intent("make me a sandwich", now=NOW)
    assert result.name != "expression_request"


def test_expression_command_does_not_shadow_reminders_or_tasks():
    """Reminder/task triggers are checked before the expression command,
    so a reminder that happens to contain the word 'make' still creates a
    reminder/task rather than being misread as a face request."""
    result = detect_intent("add task make dinner", now=NOW)
    assert result.name == "create_task"


def test_unknown_message_still_reacts():
    result = detect_intent("asdkjaslkdj", now=NOW)
    assert result.name == "unknown"
    assert result.animation == CharacterState.THINKING


def test_empty_message():
    result = detect_intent("   ", now=NOW)
    assert result.name == "empty"


# ---------------------------------------------------------------------------
# Google Calendar (spec sections 22-24, V3: read-only)
# ---------------------------------------------------------------------------


def test_calendar_today_query():
    result = detect_intent("what's on my calendar today?", now=NOW)
    assert result.name == "calendar_today"
    assert result.response == ""  # chat_engine fills this in from a live read


def test_calendar_today_alternate_phrasing():
    result = detect_intent("do i have any meetings today", now=NOW)
    assert result.name == "calendar_today"


def test_calendar_tomorrow_query():
    result = detect_intent("what's on my calendar tomorrow?", now=NOW)
    assert result.name == "calendar_tomorrow"


def test_calendar_upcoming_query():
    result = detect_intent("what's coming up?", now=NOW)
    assert result.name == "calendar_upcoming"


def test_calendar_next_meeting_query():
    result = detect_intent("when is my next meeting", now=NOW)
    assert result.name == "calendar_upcoming"


def test_calendar_connect_command():
    result = detect_intent("connect my calendar", now=NOW)
    assert result.name == "calendar_connect"


def test_calendar_connect_google_phrasing():
    result = detect_intent("connect google calendar", now=NOW)
    assert result.name == "calendar_connect"


def test_calendar_disconnect_command():
    result = detect_intent("disconnect my calendar", now=NOW)
    assert result.name == "calendar_disconnect"


def test_calendar_connect_is_not_shadowed_by_upcoming_trigger():
    """'connect my calendar' also loosely resembles the general 'what's on
    my calendar' phrasing family - connect/disconnect must win since
    they're checked first (see app/ai/intent.py)."""
    result = detect_intent("connect my calendar please", now=NOW)
    assert result.name == "calendar_connect"


def test_calendar_today_takes_priority_over_upcoming():
    result = detect_intent("what's on my calendar for today", now=NOW)
    assert result.name == "calendar_today"
