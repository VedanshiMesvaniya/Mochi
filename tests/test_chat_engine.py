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


def test_expiring_pending_action_never_logs_the_private_title(temp_db, caplog):
    """Regression test for the updated security review's remaining
    privacy finding: the "Expiring stale pending_action..." log line
    used to interpolate the pending_action dict itself with %r, which
    included the private calendar event title. Assert the title text
    never appears anywhere in the logs once the proposal expires."""
    import logging

    caplog.set_level(logging.INFO, logger="mochi.ai.chat_engine")

    proposal = handle_message("schedule a meeting with PRIVATE_TEST_EVENT tomorrow at 5pm")
    pending = proposal.pending_action
    assert pending is not None
    assert "PRIVATE_TEST_EVENT" in pending["title"]  # sanity check on the fixture itself

    handle_message("what reminders do i have", pending_action=pending)

    assert "PRIVATE_TEST_EVENT" not in caplog.text


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


def test_mark_task_done_with_tied_match_asks_instead_of_picking_first(temp_db):
    """Regression test for the security review's I2 finding: two tasks
    that score equally against the query ("call mom" and "call dad" both
    share only the word "call" with "mark my task call as done") used to
    silently complete whichever one was created first. Mochi must ask
    instead of guessing which one the user meant."""
    handle_message("remember that i need to call mom")
    handle_message("remember that i need to call dad")

    reaction = handle_message("mark my task call as done")

    assert "which one" in reaction.text.lower()
    assert "call mom" in reaction.text.lower()
    assert "call dad" in reaction.text.lower()
    # Neither task was touched.
    open_tasks = task_manager.list_tasks()
    assert len(open_tasks) == 2
    assert all(t.status == task_manager.TaskStatus.OPEN for t in open_tasks)


def test_cancel_task_with_tied_match_asks_instead_of_picking_first(temp_db):
    handle_message("remember that i need to call mom")
    handle_message("remember that i need to call dad")

    reaction = handle_message("cancel my task call")

    assert "which one" in reaction.text.lower()
    open_tasks = task_manager.list_tasks()
    assert len(open_tasks) == 2  # neither cancelled


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


def test_confirming_create_event_is_recorded_as_an_episodic_event(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.calendar_tools.create_event",
        lambda title, start_iso, confirmed=False: {"id": "abc", "title": title},
    )
    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    handle_message("yes", pending_action=proposal.pending_action)

    activity = handle_message("what have you done for me today")
    assert "meeting" in activity.text.lower()


def test_declining_create_event_never_calls_calendar_tools(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("declined action must never be executed")

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.create_event", _fail_if_called)

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    reaction = handle_message("no", pending_action=proposal.pending_action)

    assert reaction.pending_action is None
    assert "never mind" in reaction.text.lower()


def test_ambiguous_reply_expires_pending_action_instead_of_keeping_it_alive(temp_db, monkeypatch):
    """Regression test for the updated security review's finding: a
    reply that merely *sounds* like it's about the pending calendar
    proposal ("what time was that again?") isn't a real yes/no, and
    without deterministic entity/context tracking (the review's
    recommended ConversationState - a bigger, separate architecture
    change) Mochi cannot safely tell "clarifying the same proposal" apart
    from "unrelated new conversation". The safe default is to expire the
    proposal rather than let it sit around waiting for a bare "yes" that
    might really be about something else entirely."""
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("must not execute on an ambiguous, non-yes/no reply")

    monkeypatch.setattr("app.ai.chat_engine.calendar_tools.create_event", _fail_if_called)

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action

    reaction = handle_message("what time was that again?", pending_action=pending)

    assert reaction.pending_action is None  # expired, not carried forward

    # A later bare "yes" must not be able to resurrect/confirm it.
    follow_up = handle_message("yes", pending_action=reaction.pending_action)
    assert follow_up.pending_action is None


def test_small_talk_reply_expires_pending_action(temp_db):
    """Same scenario as above, but with plain small talk instead of
    something clarification-shaped - covers the review's "Test 2"
    (pending action after small talk)."""
    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action

    reaction = handle_message("haha okay", pending_action=pending)

    assert reaction.pending_action is None


def test_semantic_clarify_reply_expires_pending_action(temp_db, monkeypatch):
    """Covers the review's "Test 3": the intervening message being routed
    through the medium-confidence semantic-clarification path (rather
    than the keyword matcher's "unknown" bucket) must expire the pending
    proposal too, not just the deterministic list/action paths."""
    from app.ai import semantic_intent as si

    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action

    monkeypatch.setattr(
        "app.ai.chat_engine.semantic_intent.classify",
        lambda *_a, **_kw: si.SemanticGuess(intent="create_reminder", confidence=0.6),
    )

    reaction = handle_message("dont forget the thing", pending_action=pending)

    assert reaction.pending_action is None


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


def test_unrelated_query_while_pending_action_open_expires_it(temp_db, monkeypatch):
    """A pending calendar confirmation shouldn't block an unrelated
    message (e.g. checking reminders) from working normally - but it also
    should not survive it. Previously it did (see the security review's
    S3 finding): a later standalone "yes" could then confirm a calendar
    write the user had already moved on from. A concrete, unrelated
    database read is exactly the kind of "unrelated conversation" that
    should expire the old proposal, per spec section 23 / V4."""
    proposal = handle_message("schedule a meeting tomorrow at 5pm")
    pending = proposal.pending_action

    reaction = handle_message("do i have any reminders", pending_action=pending)

    assert reaction.pending_action is None  # stale proposal expired
    assert reaction.text  # the reminders query still answered normally

    # And a later bare "yes" must NOT be able to resurrect/confirm it,
    # since handle_message is only ever called with pending_action=None
    # from here on by the real chat window (it passes back exactly what
    # the previous reaction returned).
    follow_up = handle_message("yes", pending_action=reaction.pending_action)
    assert follow_up.pending_action is None


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


def test_relational_message_triggers_no_database_action(temp_db):
    """Conversational-issues report P1 ("Improve Relational/Emotional
    Conversation Understanding") acceptance criterion: "No database
    action is triggered by ordinary relational conversation.\""""
    reaction = handle_message("did you miss me")

    assert reaction.emotion == Emotion.HAPPY
    assert task_manager.list_tasks() == []
    assert (
        reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
        == []
    )
    assert timer_manager.list_active_timers() == []


def test_relational_message_flavored_by_familiarity(temp_db):
    """A newcomer and a familiar user should not get the exact same
    canned line - familiarity should be able to influence the response,
    same as it already does for plain greetings."""
    new_reaction = handle_message("I'm back")

    for _ in range(30):
        relationship.record_interaction()
    familiar_reaction = handle_message("I'm back")

    assert new_reaction.text != familiar_reaction.text


# ---------------------------------------------------------------------------
# Google Tasks (opt-in, shares Calendar's connect - see
# app/tasks/google_tasks.py)
# ---------------------------------------------------------------------------


def test_google_tasks_query_never_falls_through_to_llm(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("Google Tasks queries must never fall through to the LLM")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _fail_if_called)
    monkeypatch.setattr("app.ai.chat_engine.google_tasks.list_tasks", lambda: [])

    reaction = handle_message("what is on my google tasks")
    assert reaction.text


def test_google_tasks_list_reports_not_connected(temp_db, monkeypatch):
    from app.core.exceptions import GoogleTasksNotConnected

    def _raise():
        raise GoogleTasksNotConnected(
            'Google Tasks isn\'t connected yet. Say "connect my calendar" to set it up.'
        )

    monkeypatch.setattr("app.ai.chat_engine.google_tasks.list_tasks", _raise)

    reaction = handle_message("what is on my google tasks")
    assert "connect my calendar" in reaction.text.lower()
    assert reaction.animation == CharacterState.CONFUSED


def test_google_tasks_list_empty_is_happy(temp_db, monkeypatch):
    monkeypatch.setattr("app.ai.chat_engine.google_tasks.list_tasks", lambda: [])
    reaction = handle_message("what is on my google tasks")
    assert reaction.emotion == Emotion.HAPPY
    assert "empty" in reaction.text.lower()


def test_google_tasks_list_lists_tasks(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks.list_tasks",
        lambda: [{"id": "t1", "title": "Buy milk", "status": "needsAction", "completed": False}],
    )
    reaction = handle_message("show my google tasks")
    assert "buy milk" in reaction.text.lower()


def test_google_tasks_connect_uses_shared_calendar_connect(temp_db, monkeypatch):
    """'connect my calendar' is the one shared trigger - it should
    mention Tasks when Tasks is also enabled."""
    from app.core.config import settings

    monkeypatch.setattr("app.ai.chat_engine.google_calendar.connect", lambda: None)
    monkeypatch.setattr(settings, "google_tasks_enabled", True)

    reaction = handle_message("connect my calendar")
    assert "connected" in reaction.text.lower()
    assert "google tasks" in reaction.text.lower()


def test_create_google_task_proposes_and_waits_for_confirmation(temp_db):
    reaction = handle_message("add buy milk to my google tasks")
    assert reaction.pending_action is not None
    assert reaction.pending_action["kind"] == "google_task_create"
    assert reaction.pending_action["title"] == "buy milk"
    assert "add" in reaction.text.lower()
    assert "google tasks" in reaction.text.lower()


def test_confirming_create_google_task_calls_tools_with_confirmed_true(temp_db, monkeypatch):
    calls = []

    def _fake_create(title, confirmed=False):
        calls.append((title, confirmed))
        return {"id": "t1", "title": title}

    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks_tools.create_google_task", _fake_create
    )

    proposal = handle_message("add buy milk to my google tasks")
    pending = proposal.pending_action
    assert pending is not None

    reaction = handle_message("yes", pending_action=pending)

    assert len(calls) == 1
    assert calls[0][1] is True  # confirmed=True
    assert reaction.pending_action is None
    assert "done" in reaction.text.lower() or "added" in reaction.text.lower()
    assert reaction.emotion == Emotion.HAPPY


def test_declining_create_google_task_never_calls_tools(temp_db, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("declined action must never be executed")

    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks_tools.create_google_task", _fail_if_called
    )

    proposal = handle_message("add buy milk to my google tasks")
    reaction = handle_message("no", pending_action=proposal.pending_action)

    assert reaction.pending_action is None
    assert "never mind" in reaction.text.lower()


def test_delete_google_task_proposes_after_finding_a_match(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks.find_task",
        lambda query: [{"id": "t1", "title": "Buy milk"}],
    )
    reaction = handle_message("delete my google task buy milk")
    assert reaction.pending_action is not None
    assert reaction.pending_action["kind"] == "google_task_delete"
    assert reaction.pending_action["task_id"] == "t1"


def test_delete_google_task_no_match_asks_again(temp_db, monkeypatch):
    monkeypatch.setattr("app.ai.chat_engine.google_tasks.find_task", lambda query: [])
    reaction = handle_message("delete my google task nonexistent thing")
    assert reaction.pending_action is None
    assert "couldn't find" in reaction.text.lower()


def test_confirming_delete_google_task_calls_tools_with_confirmed_true(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks.find_task",
        lambda query: [{"id": "t1", "title": "Buy milk"}],
    )
    calls = []

    def _fake_delete(task_id, confirmed=False):
        calls.append((task_id, confirmed))

    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks_tools.delete_google_task", _fake_delete
    )

    proposal = handle_message("delete my google task buy milk")
    reaction = handle_message("yes", pending_action=proposal.pending_action)

    assert calls == [("t1", True)]
    assert reaction.pending_action is None
    assert "deleted" in reaction.text.lower()


def test_confirming_complete_google_task_calls_tools_with_confirmed_true(temp_db, monkeypatch):
    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks.find_task",
        lambda query: [{"id": "t1", "title": "Buy milk"}],
    )
    calls = []

    def _fake_complete(task_id, confirmed=False):
        calls.append((task_id, confirmed))

    monkeypatch.setattr(
        "app.ai.chat_engine.google_tasks_tools.complete_google_task", _fake_complete
    )

    proposal = handle_message("complete my google task buy milk")
    reaction = handle_message("yes", pending_action=proposal.pending_action)

    assert calls == [("t1", True)]
    assert reaction.pending_action is None
    assert "complete" in reaction.text.lower()


# ---------------------------------------------------------------------------
# Active-goal completion (Cognitive Upgrade spec sections 3-4/16) - the
# exact scenario the spec calls out: "Schedule Devika tomorrow" -> "what
# time?" -> "5" must complete the SAME event, not be treated as a fresh,
# unrelated message. See app/ai/goal_state.py.
# ---------------------------------------------------------------------------


def test_calendar_create_needs_time_then_bare_reply_completes_it(temp_db):
    asked = handle_message("schedule a meeting with Devika tomorrow")
    assert asked.pending_action is None
    assert "when" in asked.text.lower()
    assert asked.active_goal is not None
    assert asked.active_goal["kind"] == "calendar_create_event"

    answered = handle_message("5", active_goal=asked.active_goal)
    assert answered.pending_action is not None
    assert answered.pending_action["kind"] == "calendar_create"
    assert "devika" in answered.pending_action["title"].lower()
    # The goal is consumed once it resolves into a fresh proposal - not
    # left dangling to (mis)apply to some later, unrelated message.
    assert answered.active_goal is None


def test_reminder_needs_time_then_bare_reply_completes_it(temp_db):
    asked = handle_message("remind me to call mom")
    assert asked.active_goal is not None
    assert asked.active_goal["kind"] == "create_reminder"

    answered = handle_message("at 7pm", active_goal=asked.active_goal)
    assert answered.active_goal is None
    assert "call mom" in answered.text.lower()
    reminders = reminder_manager.list_reminders()
    assert any("call mom" in r.title.lower() for r in reminders)


def test_timer_needs_duration_then_bare_reply_completes_it(temp_db):
    asked = handle_message("start a timer")
    assert asked.active_goal is not None
    assert asked.active_goal["kind"] == "start_timer"

    answered = handle_message("10 minutes", active_goal=asked.active_goal)
    assert answered.active_goal is None
    assert "10 min" in answered.text.lower()


def test_active_goal_is_abandoned_by_an_unrelated_reply(temp_db):
    """An ambiguous reply must never guess a slot value - it should fall
    back to processing the message normally, exactly like an ambiguous
    pending_action reply is abandoned rather than carried forward."""
    asked = handle_message("remind me to call mom")
    assert asked.active_goal is not None

    reaction = handle_message("hows it going", active_goal=asked.active_goal)
    # Treated as an ordinary new (small-talk) message, not a broken/stuck
    # reminder flow.
    assert reaction.active_goal is None
    reminders = reminder_manager.list_reminders()
    assert not any("call mom" in r.title.lower() for r in reminders)


def test_active_goal_not_reissued_by_unrelated_intents(temp_db):
    """A normal, self-contained command must never pick up a leftover
    active_goal field - only the four "*_needs_*" intents do."""
    reaction = handle_message("what's the weather like")
    assert reaction.active_goal is None


# ---------------------------------------------------------------------------
# Semantic memory (Cognitive Upgrade phase 2, spec section 7) - explicit
# remember/recall/forget commands, passive extraction, and contradiction
# handling. See app/memory/semantic_memory.py and app/ai/fact_extraction.py.
# ---------------------------------------------------------------------------


def test_remember_that_stores_a_fact(temp_db):
    reaction = handle_message("remember that I live in Austin")
    assert "austin" in reaction.text.lower()

    recalled = handle_message("what do you know about me")
    assert "austin" in recalled.text.lower()


def test_remember_that_i_need_to_still_creates_a_task_not_a_fact(temp_db):
    """Backward compatibility: TASK_TRIGGER's existing "remember (that)
    i need to ..." phrasing must keep creating a task exactly as before -
    the new semantic-memory REMEMBER_TRIGGER must never intercept it."""
    handle_message("remember that i need to call aunt")
    tasks = task_manager.list_tasks()
    assert any("call aunt" in t.title.lower() for t in tasks)

    recalled = handle_message("what do you know about me")
    assert "call aunt" not in recalled.text.lower()


def test_recall_facts_when_nothing_stored_yet(temp_db):
    reaction = handle_message("what do you know about me")
    assert "don't have anything" in reaction.text.lower()


def test_passive_extraction_does_not_change_the_visible_reply(temp_db):
    """A plain, ordinary sentence like "I live in Austin" should be
    answered as normal chat (whatever that reply would have been
    anyway) - the fact is noticed silently in the background, never
    announced uninvited."""
    reaction = handle_message("I live in Austin")
    assert "remember" not in reaction.text.lower()
    assert "noted" not in reaction.text.lower()

    recalled = handle_message("what do you know about me")
    assert "austin" in recalled.text.lower()


def test_passive_extraction_ordinary_message_without_a_pattern_saves_nothing(temp_db):
    handle_message("hows it going today")
    handle_message("I finally finished the API")
    reaction = handle_message("what do you know about me")
    assert "don't have anything" in reaction.text.lower()


def test_passive_extraction_contradiction_handling(temp_db):
    """Spec section 9's exact example: a later statement about the same
    thing supersedes the earlier one rather than piling up as a second,
    conflicting fact."""
    handle_message("I use Windows")
    handle_message("I switched to Linux")

    recalled = handle_message("what do you know about me")
    assert "linux" in recalled.text.lower()
    assert "windows" not in recalled.text.lower()


def test_passive_extraction_hedged_statement_stored_as_considering(temp_db):
    handle_message("I might switch to Linux")
    recalled = handle_message("what do you know about me")
    assert "considering" in recalled.text.lower()
    # Never stored as a confident current-state claim.
    assert "user switched to linux" not in recalled.text.lower()
    assert "user uses linux" not in recalled.text.lower()


def test_forget_removes_a_previously_remembered_fact(temp_db):
    handle_message("remember that I live in Austin")
    reaction = handle_message("forget that I live in Austin")
    assert "forgotten" in reaction.text.lower() or "austin" in reaction.text.lower()

    recalled = handle_message("what do you know about me")
    assert "austin" not in recalled.text.lower()


def test_forget_with_no_match_says_so(temp_db):
    reaction = handle_message("forget about my imaginary pet dragon")
    assert "don't have anything" in reaction.text.lower()


def test_memory_disabled_degrades_gracefully_instead_of_crashing(temp_db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "memory_enabled", False)

    remember_reaction = handle_message("remember that I live in Austin")
    assert "turned off" in remember_reaction.text.lower()

    recall_reaction = handle_message("what do you know about me")
    assert "turned off" in recall_reaction.text.lower()

    # Passive extraction must also stay silent (never raise) when disabled.
    handle_message("I live in Austin")  # must not raise


# ---------------------------------------------------------------------------
# Episodic memory (Cognitive Upgrade phase 2, spec section 7) - a running
# record of things Mochi actually DID, distinct from semantic memory's
# facts about the user. See app/memory/episodic_memory.py.
# ---------------------------------------------------------------------------


def test_recent_activity_is_empty_before_anything_happens(temp_db):
    reaction = handle_message("what have you done for me today")
    assert "nothing" in reaction.text.lower()


def test_creating_a_reminder_is_recorded_as_an_episodic_event(temp_db):
    handle_message("remind me to call mom at 7pm")
    reaction = handle_message("what have you done for me today")
    assert "call mom" in reaction.text.lower()


def test_creating_a_timer_is_recorded_as_an_episodic_event(temp_db):
    handle_message("start a timer for 10 minutes")
    reaction = handle_message("what have we done recently")
    assert "timer" in reaction.text.lower()


def test_recent_activity_orders_newest_first(temp_db):
    handle_message("remind me to call mom at 7pm")
    handle_message("start a timer for 10 minutes")
    reaction = handle_message("what have you done for me today")
    lowered = reaction.text.lower()
    # The timer (created second) should be mentioned before the reminder.
    assert lowered.index("timer") < lowered.index("call mom")


def test_recent_activity_disabled_degrades_gracefully(temp_db, monkeypatch):
    from app.core.config import settings

    handle_message("remind me to call mom at 7pm")
    monkeypatch.setattr(settings, "memory_enabled", False)
    reaction = handle_message("what have you done for me today")
    assert "turned off" in reaction.text.lower()
