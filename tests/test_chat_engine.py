from app.ai.chat_engine import handle_message
from app.character.state_machine import CharacterState, Emotion
from app.memory import relationship
from app.reminders import manager as reminder_manager


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
