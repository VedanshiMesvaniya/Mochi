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


# --- Spelled-out ("word") numbers in relative time/duration -------------
# Bug report: "in one minute remind me to check output" fell through to
# "create_reminder_needs_time" ("but when?") even though a time WAS
# given, just spelled out rather than digits - see
# app/ai/intent.py's _normalize_word_numbers.


def test_reminder_with_word_number_relative_time():
    result = detect_intent("in one minute remind me to check output", now=NOW)
    assert result.name == "create_reminder"
    assert result.tool == "create_reminder"
    assert result.tool_args["datetime_iso"] == "2026-08-14T15:01:00"
    assert result.tool_args["title"] == "Check output"


def test_reminder_with_word_number_after_remind_me():
    result = detect_intent("remind me to stretch in twenty minutes", now=NOW)
    assert result.name == "create_reminder"
    assert result.tool_args["datetime_iso"] == "2026-08-14T15:20:00"


def test_reminder_with_indefinite_article_as_one():
    result = detect_intent("remind me to check the oven in a minute", now=NOW)
    assert result.name == "create_reminder"
    assert result.tool_args["datetime_iso"] == "2026-08-14T15:01:00"


def test_timer_with_word_number_duration():
    result = detect_intent("set a timer for five minutes", now=NOW)
    assert result.tool == "start_timer"
    assert result.tool_args["duration_seconds"] == 300


def test_timer_with_couple_as_two():
    result = detect_intent("timer for a couple minutes", now=NOW)
    assert result.tool == "start_timer"
    assert result.tool_args["duration_seconds"] == 120


def test_word_number_does_not_corrupt_unrelated_article():
    """"a break" must stay "a break" - only a number word DIRECTLY
    followed by a time unit word gets rewritten, never a bare "a"/"an"
    elsewhere in the sentence."""
    result = detect_intent("remind me to take a break in 10 minutes", now=NOW)
    assert result.name == "create_reminder"
    assert result.tool_args["title"] == "Take a break"
    assert result.tool_args["datetime_iso"] == "2026-08-14T15:10:00"


def test_timer_with_duration():
    result = detect_intent("set a timer for 10 minutes", now=NOW)
    assert result.tool == "start_timer"
    assert result.tool_args["duration_seconds"] == 600


def test_task_creation():
    result = detect_intent("remember that i need to buy milk", now=NOW)
    assert result.tool == "create_task"
    assert result.tool_args["title"] == "Buy milk"
    assert "due_at_iso" not in result.tool_args


def test_task_creation_with_deadline():
    result = detect_intent("add task submit report at 7pm", now=NOW)
    assert result.tool == "create_task"
    assert result.tool_args["title"] == "Submit report"
    assert result.tool_args["due_at_iso"] == "2026-08-14T19:00:00"


def test_reminder_trigger_variants():
    for phrase in [
        "set a reminder to water plants at 8am",
        "create a reminder to water plants at 8am",
        "reminder to water plants at 8am",
        "don't let me forget to water plants at 8am",
    ]:
        result = detect_intent(phrase, now=NOW)
        assert result.tool == "create_reminder", phrase
        assert result.tool_args["title"] == "Water plants"


def test_timer_trigger_variants():
    assert detect_intent("5 minute timer", now=NOW).tool_args["duration_seconds"] == 300
    assert (
        detect_intent("start a countdown for 2 minutes", now=NOW).tool_args["duration_seconds"]
        == 120
    )
    assert (
        detect_intent("countdown for 90 seconds", now=NOW).tool_args["duration_seconds"] == 90
    )


def test_timer_trigger_tolerates_the_common_timmer_typo():
    """Bug report: "set 10 second timmer" (doubled 'm') fell through to
    intent=unknown entirely, so no timer was ever created and Mochi never
    notified - not a notification bug, a trigger-matching gap. See
    _TIMER_WORD in app/ai/intent.py."""
    result = detect_intent("set 10 second timmer", now=NOW)
    assert result.tool == "start_timer", result.name
    assert result.tool_args["duration_seconds"] == 10

    result = detect_intent("set a timmer for 5 minutes", now=NOW)
    assert result.tool == "start_timer"
    assert result.tool_args["duration_seconds"] == 300

    assert detect_intent("cancel my timmer", now=NOW).name == "cancel_timer"
    assert detect_intent("what timmers do i have", now=NOW).name == "list_timers"


def test_task_trigger_variants():
    for phrase, expected_title in [
        ("new task clean my room", "Clean my room"),
        ("create a task call the bank", "Call the bank"),
        ("create task pay rent", "Pay rent"),
        ("task: clean my room", "Clean my room"),
    ]:
        result = detect_intent(phrase, now=NOW)
        assert result.tool == "create_task", phrase
        assert result.tool_args["title"] == expected_title


def test_check_on_extracts_query():
    result = detect_intent("check on messeging my aunt", now=NOW)
    assert result.name == "check_on"
    assert result.tool_args["query"] == "messeging my aunt"


def test_check_on_does_not_shadow_reminder_creation():
    """'remind me to check on X' must still create a reminder, not be
    misread as a status check - CHECK_ON_TRIGGER is checked after
    REMINDER_TRIGGER specifically to guarantee this."""
    result = detect_intent("remind me to check on my aunt at 7pm", now=NOW)
    assert result.tool == "create_reminder"
    assert result.tool_args["title"] == "Check on my aunt"


def test_ambiguous_done_trigger_variants():
    for phrase in ["mark it as done", "that's done", "i finished it", "complete it"]:
        result = detect_intent(phrase, now=NOW)
        assert result.name == "complete_ambiguous", phrase


def test_count_command():
    result = detect_intent("mochi count 1 to 10", now=NOW)
    assert result.name == "count"
    assert result.emotion == Emotion.EXCITED
    assert "1!" in result.response and "10!" in result.response


def test_count_to_defaults_start_to_one():
    result = detect_intent("count to 5", now=NOW)
    assert "1! 2! 3! 4! 5!" in result.response


def test_count_range_is_capped():
    result = detect_intent("count from 1 to 30", now=NOW)
    # Capped at start+30 in detect_intent - shouldn't silently balloon
    # into an enormous reply for a typo'd huge range.
    assert result.response.count("31!") == 0


def test_time_query_reads_injected_clock():
    result = detect_intent("what time is it", now=NOW)
    assert result.name == "time_query"
    assert result.response == "It's 03:00 PM right now."


def test_time_query_variants():
    for phrase in ("what's the time", "current time", "what time do you have"):
        assert detect_intent(phrase, now=NOW).name == "time_query"


def test_date_query_reads_injected_clock():
    result = detect_intent("what day is it today", now=NOW)
    assert result.name == "date_query"
    assert result.response == "It's Friday, August 14."


def test_date_query_variants():
    for phrase in ("what's the date", "what's today", "what day of the week is it"):
        assert detect_intent(phrase, now=NOW).name == "date_query"


def test_time_query_uses_real_system_clock_by_default():
    """Regression test for the actual bug report: 'what time is it' must
    reflect the live OS clock when `now` isn't injected, not a stale or
    hardcoded value - this is what makes the deterministic handler an
    actual fix for "Mochi can't read the real time" rather than just
    another canned line. Calls detect_intent with no `now=` at all (its
    real default: `datetime.now()`) and checks the reply matches the
    actual wall clock at call time, tolerating the minute ticking over
    mid-test by accepting either the before- or after-call minute."""
    before = datetime.now()
    result = detect_intent("what time is it")
    after = datetime.now()

    assert result.name == "time_query"
    acceptable = {
        f"It's {before:%I:%M %p} right now.",
        f"It's {after:%I:%M %p} right now.",
    }
    assert result.response in acceptable


def test_date_query_uses_real_system_clock_by_default():
    result = detect_intent("what day is it")
    today = datetime.now()
    assert result.name == "date_query"
    assert f"{today:%A, %B}" in result.response
    assert str(today.day) in result.response


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


# ---------------------------------------------------------------------------
# Google Calendar writes (spec section 23, V4)
# ---------------------------------------------------------------------------


def test_calendar_create_with_absolute_time():
    result = detect_intent("schedule a meeting with Devika tomorrow at 5pm", now=NOW)
    assert result.name == "calendar_create_event"
    assert result.tool == "calendar_create_event"
    assert "Devika" in result.tool_args["title"]
    assert result.tool_args["start_iso"] == "2026-08-15T17:00:00"


def test_calendar_create_add_a_meeting():
    result = detect_intent("add a meeting tomorrow at 5 PM", now=NOW)
    assert result.name == "calendar_create_event"
    assert result.tool_args["title"] == "Meeting"


def test_calendar_create_without_time_asks_for_one():
    result = detect_intent("schedule a meeting with Devika", now=NOW)
    assert result.name == "calendar_create_needs_time"
    assert result.tool is None


def test_calendar_create_book_an_appointment():
    result = detect_intent("book an appointment at 3pm", now=NOW)
    assert result.name == "calendar_create_event"


def test_calendar_delete_by_time():
    result = detect_intent("cancel my 5 PM meeting", now=NOW)
    assert result.name == "calendar_delete_event"
    assert result.tool == "calendar_delete_event"
    assert result.tool_args["time_of_day"] == "17:00"
    assert result.tool_args["query"] is None


def test_calendar_delete_by_title():
    result = detect_intent("cancel my meeting with Devika", now=NOW)
    assert result.name == "calendar_delete_event"
    assert result.tool_args["time_of_day"] is None
    assert "devika" in result.tool_args["query"].lower()


def test_calendar_delete_does_not_shadow_create():
    result = detect_intent("add a meeting tomorrow at 5pm", now=NOW)
    assert result.name == "calendar_create_event"


def test_calendar_create_does_not_shadow_task_creation():
    """'add task X' must still create a task, not a calendar event -
    CALENDAR_CREATE_TRIGGER requires an event-ish noun (meeting/event/
    appointment/call) that 'task' isn't."""
    result = detect_intent("add task buy milk", now=NOW)
    assert result.name == "create_task"


# --- Completing/cancelling an existing task/reminder/timer -------------
# Regression coverage for the exact reported bug: "mark my task to call
# aunt as done" had no matching intent at all and fell through to the
# open-ended LLM bucket, which had no task list to check against.


def test_mark_task_done_extracts_clean_query():
    result = detect_intent("mark my task to call aunt as done", now=NOW)
    assert result.name == "complete_task"
    assert result.tool_args["query"] == "call aunt"


def test_complete_task_alternate_phrasing():
    result = detect_intent("complete the task buy milk", now=NOW)
    assert result.name == "complete_task"
    assert "buy milk" in result.tool_args["query"]


def test_finished_task_phrasing():
    result = detect_intent("i finished the task submit assignment", now=NOW)
    assert result.name == "complete_task"


def test_cancel_task():
    result = detect_intent("cancel my task to buy milk", now=NOW)
    assert result.name == "cancel_task"
    assert "buy milk" in result.tool_args["query"]


def test_mark_reminder_done():
    result = detect_intent("mark my reminder to call mom as done", now=NOW)
    assert result.name == "complete_reminder"
    assert result.tool_args["query"] == "call mom"


def test_cancel_reminder():
    result = detect_intent("cancel my reminder to call mom", now=NOW)
    assert result.name == "cancel_reminder"


def test_cancel_timer():
    result = detect_intent("cancel the timer", now=NOW)
    assert result.name == "cancel_timer"


def test_stop_timer_phrasing():
    result = detect_intent("stop my timer", now=NOW)
    assert result.name == "cancel_timer"


def test_mark_task_done_does_not_shadow_reminder_creation():
    """'remind me' isn't in this message at all, but guard the inverse
    too: a genuine new-reminder request must never be misread as a
    completion just because it shares words like 'to'."""
    result = detect_intent("remind me to call aunt at 7pm", now=NOW)
    assert result.name == "create_reminder"


# --- Regression coverage for the reported "timer/reminder/task mix up" --
# bug: reminder/timer/task creation used to be three independent `if`
# blocks checked in a fixed order (reminder, then timer, then task), so a
# message containing a reminder-trigger phrase anywhere in it always won
# even when a timer/task trigger was what was actually being asked for.
# Fixed by resolving to whichever trigger phrase appears *earliest* in
# what was actually typed - these lock that behavior in.


def test_timer_request_is_not_shadowed_by_an_incidental_remind_me():
    """'start a 10 minute timer' is said before 'remind me' in this
    sentence, so the timer must win - previously ANY 'remind me'
    anywhere in the message always won regardless of position, so this
    used to ask 'but when?' for a reminder instead of starting the timer
    it was actually, unambiguously asked to start."""
    result = detect_intent(
        "start a 10 minute timer and remind me when it's done", now=NOW
    )
    assert result.name == "start_timer"
    assert result.tool == "start_timer"
    assert result.tool_args["duration_seconds"] == 600


def test_task_request_is_not_shadowed_by_a_later_remind_me():
    """'add task' is said before 'remind me' here, so the explicit task
    request must win over the later, incidental 'remind me' phrase."""
    result = detect_intent("add task buy milk, remind me later", now=NOW)
    assert result.name == "create_task"
    assert result.tool_args["title"].startswith("Buy milk")


def test_reminder_still_wins_when_it_is_said_first():
    """Inverse case: when the reminder phrase genuinely comes first, it
    must still win - this isn't a blanket 'timer/task always beats
    reminder' change, just earliest-mention-wins."""
    result = detect_intent("remind me to start a timer for the oven", now=NOW)
    assert result.name == "create_reminder_needs_time"
