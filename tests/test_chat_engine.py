from app.ai.chat_engine import handle_message
from app.character.state_machine import CharacterState, Emotion
from app.memory import relationship
from app.reminders import manager as reminder_manager
from app.tasks import manager as task_manager
from app.timers import manager as timer_manager


def test_greeting_reaction_has_no_side_effects(temp_db):
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


def test_malformed_input_never_crashes(temp_db):
    reaction = handle_message("")
    assert reaction.text


# --- Completing/cancelling an existing task/reminder/timer -------------
# Regression coverage for the exact reported bug: "mark my task to call
# aunt as done" had no matching intent at all and fell through to the
# open-ended LLM bucket instead of actually completing anything.


def test_mark_task_done_actually_completes_it(temp_db):
    handle_message("remember that i need to call aunt")
    reaction = handle_message("mark my task to call aunt as done")
    assert "call aunt" in reaction.text.lower()
    assert reaction.emotion == Emotion.HAPPY

    # Completed tasks are archived out of the main `tasks` table (see
    # app/tasks/manager.py's complete_task()) so a "what's left" query
    # never has to filter finished rows out by hand - the finished record
    # now lives in tasks_done instead.
    assert task_manager.list_tasks() == []
    archived = task_manager.list_archived_tasks()
    assert archived[0].status == task_manager.TaskStatus.DONE
    assert archived[0].title == "Call aunt"


def test_mark_task_done_with_only_one_task_and_no_specific_title(temp_db):
    """"mark my task as done" with nothing else - falls back to the one
    open task rather than asking, since there's nothing else it could
    mean. This must not kick in when a *specific* title was given that
    just doesn't match anything (see the "asks instead of guessing" test
    above) - only when the query itself came out empty."""
    handle_message("remember that i need to buy milk")
    reaction = handle_message("mark my task as done")
    assert "buy milk" in reaction.text.lower()


def test_mark_task_done_with_no_matching_task_asks_instead_of_guessing(temp_db):
    handle_message("remember that i need to buy milk")
    reaction = handle_message("mark my task to launch a rocket as done")
    assert "not sure" in reaction.text.lower()
    tasks = task_manager.list_tasks()
    assert tasks[-1].status == task_manager.TaskStatus.OPEN  # untouched


def test_mark_task_done_with_no_tasks_at_all(temp_db):
    reaction = handle_message("mark my task as done")
    assert "don't have any open tasks" in reaction.text.lower()


def test_cancel_task_actually_cancels_it(temp_db):
    handle_message("remember that i need to buy milk")
    reaction = handle_message("cancel my task to buy milk")
    assert "buy milk" in reaction.text.lower()
    # Cancelled tasks are archived out of `tasks` the same way completed
    # ones are - see app/tasks/manager.py's cancel_task().
    assert task_manager.list_tasks() == []
    archived = task_manager.list_archived_tasks()
    assert archived[0].status == task_manager.TaskStatus.CANCELLED


def test_mark_reminder_done_actually_completes_it(temp_db):
    handle_message("remind me to call mom at 7pm")
    reaction = handle_message("mark my reminder to call mom as done")
    assert "call mom" in reaction.text.lower()
    # Completed reminders are archived out of `reminders` - see
    # app/reminders/manager.py's complete_reminder().
    assert reminder_manager.list_reminders() == []
    archived = reminder_manager.list_archived_reminders()
    assert archived[0].status == reminder_manager.ReminderStatus.COMPLETED


def test_cancel_reminder_actually_cancels_it(temp_db):
    handle_message("remind me to call mom at 7pm")
    reaction = handle_message("cancel my reminder to call mom")
    # Cancelled reminders are archived out of `reminders` - see
    # app/reminders/manager.py's cancel_reminder().
    assert reminder_manager.list_reminders() == []
    archived = reminder_manager.list_archived_reminders()
    assert archived[0].status == reminder_manager.ReminderStatus.CANCELLED


def test_cancel_timer_actually_cancels_it(temp_db):
    handle_message("set a timer for 10 minutes")
    reaction = handle_message("cancel the timer")
    assert "stopped" in reaction.text.lower()
    assert timer_manager.list_active_timers() == []


def test_start_timer_end_to_end_with_the_common_timmer_typo(temp_db):
    """Bug report: "set 10 second timmer" produced intent=unknown and no
    timer was ever created (and so, correctly, no notification ever
    fired for it) - reproduces the exact reported message end-to-end
    through handle_message(), not just detect_intent()."""
    handle_message("set 10 second timmer")
    active = timer_manager.list_active_timers()
    assert len(active) == 1
    assert active[0].duration_seconds == 10


def test_cancel_timer_with_none_running(temp_db):
    reaction = handle_message("cancel the timer")
    assert "don't have any timers running" in reaction.text.lower()


def test_every_message_records_an_interaction(temp_db):
    handle_message("hi")
    handle_message("bye")
    assert relationship.get_interaction_count() == 2


def test_greeting_changes_once_familiar(temp_db):
    first_reaction = handle_message("hi")
    # Rack up enough interactions to cross into "familiar" territory.
    for _ in range(30):
        handle_message("hi")
    familiar_reaction = handle_message("hi")
    assert familiar_reaction.text != first_reaction.text


def test_list_tasks_reads_real_db_not_the_llm(temp_db, monkeypatch):
    """Regression: 'do i have any task to do' was falling through to the
    LLM and getting a hallucinated, unrelated answer instead of an actual
    answer about what's in the task table."""

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("list_tasks must never fall through to the LLM")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _fail_if_called)

    empty_reaction = handle_message("do i have any task to do")
    assert "empty" in empty_reaction.text.lower() or "no" in empty_reaction.text.lower()

    handle_message("add task buy milk")
    handle_message("add task walk the dog")

    reaction = handle_message("what tasks do i have")
    assert "buy milk" in reaction.text.lower()
    assert "walk the dog" in reaction.text.lower()
    assert "2" in reaction.text


def test_list_reminders_reads_real_db_not_the_llm(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("list_reminders must never fall through to the LLM")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _fail_if_called)

    handle_message("remind me to call mom in 10 minutes")
    reaction = handle_message("do i have any reminders")
    assert "call mom" in reaction.text.lower()
    assert "1" in reaction.text


def test_listing_never_creates_a_new_reminder_or_task(temp_db):
    """A pure query must be read-only - it must not also insert anything."""
    handle_message("do i have any task to do")
    handle_message("do i have any reminders")
    assert reminder_manager.list_reminders() == []


def test_unknown_message_with_llm_unavailable_gives_a_setup_hint(temp_db, monkeypatch):
    """Regression test: previously an LLM-unavailable fallback used the
    exact same generic line as a genuinely-unrecognized message, so a
    working-as-designed 'Ollama isn't running' situation looked
    indistinguishable from a broken/confused Mochi. It must now say
    something actionable instead."""
    from app.ai.llm import LLMUnavailable

    def _boom(*_args, **_kwargs):
        raise LLMUnavailable("connection refused")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _boom)

    reaction = handle_message("what is the meaning of life")
    assert "ollama" in reaction.text.lower()
    assert reaction.animation == CharacterState.SLEEPY


# ---------------------------------------------------------------------------
# Google Calendar (spec sections 22-24, V3: read-only)
# ---------------------------------------------------------------------------


def test_calendar_query_never_falls_through_to_llm(temp_db, monkeypatch):
    """Same reasoning as the list_tasks regression above: a calendar
    question is a factual DB/API read, never something the LLM should be
    guessing at."""

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("calendar queries must never fall through to the LLM")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _fail_if_called)
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.get_today_events", lambda: []
    )

    reaction = handle_message("what's on my calendar today?")
    assert reaction.text


def test_calendar_today_reports_not_connected(temp_db, monkeypatch):
    from app.core.exceptions import GoogleCalendarNotConnected

    def _raise():
        raise GoogleCalendarNotConnected(
            'Google Calendar isn\'t connected yet. Say "connect my calendar" to set it up.'
        )

    monkeypatch.setattr("app.ai.chat_engine.google_calendar.get_today_events", _raise)

    reaction = handle_message("what's on my calendar today?")
    assert "connect my calendar" in reaction.text.lower()
    assert reaction.animation == CharacterState.CONFUSED


def test_calendar_today_reports_not_configured(temp_db, monkeypatch):
    from app.core.exceptions import GoogleCalendarNotConfigured

    def _raise():
        raise GoogleCalendarNotConfigured(
            "Google Calendar isn't turned on. Set MOCHI_GOOGLE_CALENDAR_ENABLED=true in .env to enable it."
        )

    monkeypatch.setattr("app.ai.chat_engine.google_calendar.get_today_events", _raise)

    reaction = handle_message("what's on my calendar today?")
    assert "mochi_google_calendar_enabled" in reaction.text.lower()


def test_calendar_today_empty_is_happy(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.get_today_events", lambda: []
    )
    reaction = handle_message("what's on my calendar today?")
    assert reaction.emotion == Emotion.HAPPY
    assert "clear" in reaction.text.lower() or "nothing" in reaction.text.lower()


def test_calendar_today_lists_events(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.get_today_events",
        lambda: [
            {
                "title": "Standup",
                "start": "2026-08-14T09:00:00-07:00",
                "all_day": False,
            }
        ],
    )
    reaction = handle_message("what's on my calendar today?")
    assert "standup" in reaction.text.lower()
    assert "09:00" in reaction.text


def test_calendar_connect_success(temp_db, monkeypatch):
    monkeypatch.setattr("app.ai.chat_engine.google_calendar.connect", lambda: None)
    reaction = handle_message("connect my calendar")
    assert "connected" in reaction.text.lower()
    assert reaction.emotion == Emotion.EXCITED


def test_calendar_connect_failure_surfaces_message(temp_db, monkeypatch):
    from app.core.exceptions import GoogleCalendarNotConfigured

    def _raise():
        raise GoogleCalendarNotConfigured("No client secret found.")

    monkeypatch.setattr("app.ai.chat_engine.google_calendar.connect", _raise)

    reaction = handle_message("connect my calendar")
    assert "client secret" in reaction.text.lower()


def test_calendar_disconnect_reports_when_nothing_was_connected(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.disconnect", lambda: False
    )
    reaction = handle_message("disconnect my calendar")
    assert "wasn't connected" in reaction.text.lower() or "wasnt connected" in reaction.text.lower()


def test_calendar_disconnect_reports_success(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.disconnect", lambda: True
    )
    reaction = handle_message("disconnect my calendar")
    assert "forgotten" in reaction.text.lower()


# ---------------------------------------------------------------------------
# Google Calendar writes (spec section 23, V4: create/cancel, both requiring
# explicit user confirmation)
# ---------------------------------------------------------------------------


def test_create_event_proposes_and_waits_for_confirmation(temp_db):
    """The very first response to 'schedule a meeting...' must NOT create
    anything yet - only propose it and wait."""
    reaction = handle_message("schedule a meeting with Devika tomorrow at 5pm")
    assert reaction.pending_action is not None
    assert reaction.pending_action["kind"] == "calendar_create"
    assert "devika" in reaction.pending_action["title"].lower()
    assert "add it to your google calendar" in reaction.text.lower()


def test_create_event_needs_time_asks_for_one(temp_db):
    reaction = handle_message("schedule a meeting with Devika")
    assert reaction.pending_action is None
    assert "when" in reaction.text.lower()


def test_create_event_never_falls_through_to_llm(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("calendar create proposals must never reach the LLM")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _fail_if_called)
    reaction = handle_message("add a meeting tomorrow at 5pm")
    assert reaction.pending_action is not None


def test_confirming_create_event_calls_calendar_tools_with_confirmed_true(
    temp_db, monkeypatch
):
    calls = []

    def _fake_create(title, start_iso, confirmed=False):
        calls.append((title, start_iso, confirmed))
        return {"id": "abc", "title": title}

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.create_event", _fake_create)

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action
    assert pending is not None

    reaction = handle_message("yes", pending_action=pending)

    assert len(calls) == 1
    assert calls[0][2] is True  # confirmed=True
    assert reaction.pending_action is None
    assert "added" in reaction.text.lower() or "done" in reaction.text.lower()
    assert reaction.emotion == Emotion.HAPPY


def test_declining_create_event_never_calls_calendar_tools(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("declined action must never be executed")

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.create_event", _fail_if_called)

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    reaction = handle_message("no", pending_action=proposal.pending_action)

    assert reaction.pending_action is None
    assert "never mind" in reaction.text.lower()


def test_ambiguous_reply_keeps_pending_action_alive(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("must not execute on an ambiguous reply")

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.create_event", _fail_if_called)

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action

    reaction = handle_message("what time was that again?", pending_action=pending)

    assert reaction.pending_action == pending  # still waiting


def test_create_event_failure_after_confirmation_reports_error(temp_db, monkeypatch):
    from app.core.exceptions import GoogleCalendarNotConnected

    def _raise(title, start_iso, confirmed=False):
        raise GoogleCalendarNotConnected("not connected")

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.create_event", _raise)

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    reaction = handle_message("yes", pending_action=proposal.pending_action)

    assert "couldn't add" in reaction.text.lower()
    assert reaction.emotion == Emotion.CONFUSED


def test_delete_event_finds_match_and_proposes_cancellation(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.find_event",
        lambda query=None, around=None, days_ahead=2: [
            {
                "id": "evt1",
                "title": "Standup",
                "start": "2026-08-14T17:00:00-07:00",
                "all_day": False,
            }
        ],
    )
    reaction = handle_message("cancel my 5 PM meeting")
    assert reaction.pending_action == {
        "kind": "calendar_delete",
        "event_id": "evt1",
        "title": "Standup",
    }
    assert "cancel this event" in reaction.text.lower()


def test_delete_event_no_match_found(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.find_event",
        lambda query=None, around=None, days_ahead=2: [],
    )
    reaction = handle_message("cancel my 5 PM meeting")
    assert reaction.pending_action is None
    assert "couldn't find" in reaction.text.lower()


def test_confirming_delete_event_calls_calendar_tools_with_confirmed_true(
    temp_db, monkeypatch
):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_calendar.find_event",
        lambda query=None, around=None, days_ahead=2: [
            {"id": "evt1", "title": "Standup", "start": "2026-08-14T17:00:00-07:00", "all_day": False}
        ],
    )
    calls = []

    def _fake_delete(event_id, confirmed=False):
        calls.append((event_id, confirmed))
        return {"event_id": event_id, "deleted": True}

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.delete_event", _fake_delete)

    proposal = handle_message("cancel my 5 PM meeting")
    reaction = handle_message("yes", pending_action=proposal.pending_action)

    assert calls == [("evt1", True)]
    assert reaction.pending_action is None
    assert "cancelled" in reaction.text.lower()


def test_unrelated_query_while_pending_action_open_keeps_it_alive(temp_db, monkeypatch):
    """A pending calendar confirmation shouldn't block an unrelated
    message (e.g. checking reminders) from working normally, and
    shouldn't be silently dropped either."""
    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action

    reaction = handle_message("do i have any reminders", pending_action=pending)

    assert reaction.pending_action == pending
    assert reaction.text  # the reminders query still answered normally


# --- "check on X" / ambiguous "mark it as done" ------------------------
# Regression coverage for the exact reported bug: both phrasings fell
# through to the open-ended LLM fallback, which has no real database
# access and would hallucinate a plausible-sounding reply ("I'll remind
# you..." / "Okay, I'll take care of it") without actually checking or
# completing anything.


def test_check_on_reports_real_reminder_status(temp_db):
    handle_message("remind me to message my aunt at 7pm")
    reaction = handle_message("check on messeging my aunt")
    assert "message my aunt" in reaction.text.lower()
    assert "07:00 PM" in reaction.text


def test_check_on_reports_real_task_status(temp_db):
    handle_message("add task buy milk")
    reaction = handle_message("check on buy milk")
    assert "buy milk" in reaction.text.lower()
    assert "open" in reaction.text.lower()


def test_check_on_nothing_found_says_so_plainly(temp_db):
    reaction = handle_message("check on the moon landing")
    assert "don't have anything" in reaction.text.lower()


def test_check_on_without_query_asks_for_one(temp_db):
    reaction = handle_message("check on")
    assert reaction.emotion == Emotion.CONFUSED


def test_ambiguous_done_completes_the_only_open_item(temp_db):
    handle_message("remind me to message my aunt at 7pm")
    reaction = handle_message("mark it as done")
    assert "message my aunt" in reaction.text.lower()
    # Completed reminders are archived out of `reminders` now - see
    # app/reminders/manager.py's complete_reminder().
    assert reminder_manager.list_reminders() == []
    assert reminder_manager.list_archived_reminders()[0].status == "completed"


def test_ambiguous_done_asks_which_when_multiple_open_items(temp_db):
    handle_message("remind me to message my aunt at 7pm")
    handle_message("add task buy milk")
    reaction = handle_message("mark it as done")
    assert reaction.emotion == Emotion.CONFUSED
    assert "which one" in reaction.text.lower()


def test_ambiguous_done_with_nothing_open_says_so(temp_db):
    reaction = handle_message("mark it as done")
    assert reaction.emotion == Emotion.CONFUSED


# --- Regression coverage for the "chat loses context" bug report: -------
# "cancel it" / "delete it" with no literal task/reminder/timer word used
# to fall through to the open-ended LLM fallback, which has no DB access
# and would just claim success without cancelling anything real.


def test_ambiguous_cancel_cancels_the_only_open_item(temp_db):
    handle_message("remind me to message my aunt at 7pm")
    reaction = handle_message("cancel it")
    assert "message my aunt" in reaction.text.lower()
    # Cancelled reminders are archived out of `reminders` now - see
    # app/reminders/manager.py's cancel_reminder().
    assert reminder_manager.list_reminders() == []
    assert reminder_manager.list_archived_reminders()[0].status == "cancelled"


def test_ambiguous_cancel_covers_running_timers_too(temp_db):
    handle_message("set a timer for 10 minutes")
    reaction = handle_message("scratch that")
    assert "timer" in reaction.text.lower()
    assert timer_manager.list_active_timers() == []


def test_ambiguous_cancel_asks_which_when_multiple_open_items(temp_db):
    handle_message("remind me to message my aunt at 7pm")
    handle_message("add task buy milk")
    reaction = handle_message("cancel it")
    assert reaction.emotion == Emotion.CONFUSED
    assert "which one" in reaction.text.lower()


def test_ambiguous_cancel_with_nothing_open_says_so(temp_db):
    reaction = handle_message("cancel it")
    assert reaction.emotion == Emotion.CONFUSED
    assert "anything" in reaction.text.lower()


def test_ambiguous_cancel_never_falsely_claims_success_via_llm(temp_db):
    """The actual bug: without a deterministic handler this message has
    no literal 'task'/'reminder'/'timer' word, so it used to reach the
    open-ended LLM fallback (which has no real DB access) instead of a
    real cancel. Confirm it never silently no-ops by checking the
    reminder is genuinely untouched when it plainly doesn't match "it"."""
    handle_message("remind me to message my aunt at 7pm")
    handle_message("add task buy milk")
    handle_message("cancel it")  # ambiguous - asks which, doesn't guess
    assert reminder_manager.list_reminders()[0].status == "pending"
    assert task_manager.list_tasks()[0].status == "open"


def test_bare_never_mind_is_not_treated_as_a_cancel_command(temp_db):
    """Deliberately NOT covered by AMBIGUOUS_CANCEL_TRIGGER - 'never
    mind' is extremely common as a plain conversational dismissal
    unrelated to any reminder/task/timer, and must not surface 'I don't
    have anything open to cancel!' in the middle of ordinary chat."""
    reaction = handle_message("never mind")
    assert "cancel" not in reaction.text.lower()


def test_count_command_end_to_end(temp_db):
    reaction = handle_message("mochi count 1 to 5")
    assert reaction.emotion == Emotion.EXCITED
    assert "1!" in reaction.text and "5!" in reaction.text


# --- Timer listing (spec follow-up: there was previously no way to ask
# chat "what timers do I have" at all - see LIST_TIMERS_TRIGGER) --------


def test_list_timers_reaction_reports_running_timers(temp_db):
    handle_message("set a timer for 10 minutes")
    reaction = handle_message("what timers do i have")
    assert "timer" in reaction.text.lower()
    assert "1" in reaction.text


def test_list_timers_reaction_with_none_running(temp_db):
    reaction = handle_message("any timers running")
    assert "no timers" in reaction.text.lower()


# --- "What have I finished" query (spec follow-up: glossary-driven
# lookup against the *_done archive tables, not the main active tables -
# see app/ai/db_glossary.py and _query_done_reaction in chat_engine.py) -


def test_query_done_reports_completed_tasks(temp_db):
    handle_message("remember that i need to call aunt")
    handle_message("mark my task to call aunt as done")

    reaction = handle_message("what tasks are done")
    assert "call aunt" in reaction.text.lower()


def test_query_done_reports_nothing_when_archive_is_empty(temp_db):
    handle_message("remember that i need to buy milk")  # still open
    reaction = handle_message("show completed tasks")
    assert "call aunt" not in reaction.text.lower()
    assert "no" in reaction.text.lower() or "nothing" in reaction.text.lower()


def test_query_done_reports_cancelled_reminders(temp_db):
    handle_message("remind me to call mom at 7pm")
    handle_message("cancel my reminder to call mom")

    reaction = handle_message("show me cancelled reminders")
    assert "call mom" in reaction.text.lower()


def test_query_done_never_shows_still_open_items(temp_db):
    """The core product rule this whole feature exists for: a 'done'
    question must read from the archive table, never the active one - an
    open task must never appear in a 'what's done' answer."""
    handle_message("remember that i need to buy milk")  # left open
    handle_message("remember that i need to call aunt")
    handle_message("mark my task to call aunt as done")

    reaction = handle_message("which tasks are done")
    assert "call aunt" in reaction.text.lower()
    assert "buy milk" not in reaction.text.lower()
