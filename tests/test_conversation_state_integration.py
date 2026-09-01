"""End-to-end tests for deterministic conversational reference resolution
(security review I1/I3) - i.e. that `conversation_state` is actually
threaded correctly through handle_message(), not just that the module
in isolation works (see test_conversation_state.py for that).
"""

from __future__ import annotations

from app.ai.chat_engine import handle_message
from app.reminders import manager as reminder_manager
from app.tasks import manager as task_manager
from app.timers import manager as timer_manager


def test_delete_it_resolves_to_just_created_task_even_with_others_open(temp_db):
    """The review's headline example: create a task, then say "actually
    delete it" - with OTHER open tasks around, this used to always ask
    "which one?" since the fuzzy matcher had no memory of what was just
    made. It must now resolve to the just-created task specifically."""
    handle_message("add task walk the dog")
    handle_message("add task pay rent")
    created = handle_message("add task buy milk")

    reaction = handle_message("actually delete it", conversation_state=created.conversation_state)

    assert "buy milk" in reaction.text.lower()
    remaining_titles = {t.title for t in task_manager.list_tasks()}
    assert remaining_titles == {"Walk the dog", "Pay rent"}


def test_mark_it_done_resolves_to_just_created_reminder(temp_db):
    handle_message("remind me to call mom at 7pm")
    handle_message("remind me to call dad at 8pm")
    created = handle_message("remind me to water the plants at 9pm")

    reaction = handle_message("mark it as done", conversation_state=created.conversation_state)

    assert "water the plants" in reaction.text.lower()
    pending_titles = {r.title for r in reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)}
    assert pending_titles == {"Call mom", "Call dad"}


def test_make_it_time_reschedules_just_created_reminder(temp_db):
    """"remind me to call mom at 7" -> "make it 8" - the review's other
    canonical example. Uses "make it 7" (rather than an exact literal "8")
    so the existing before-8-means-PM heuristic (hours 1-7 only) applies
    unambiguously regardless of what the real wall-clock time happens to
    be when this test runs."""
    created = handle_message("remind me to call mom at 6pm")

    reaction = handle_message("make it 7", conversation_state=created.conversation_state)

    assert "7:00 pm" in reaction.text.lower()
    reminders = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    assert len(reminders) == 1
    assert reminders[0].due_at.hour == 19


def test_reschedule_reference_without_prior_context_asks_instead_of_guessing():
    reaction = handle_message("make it 8", conversation_state=None)
    assert "don't have a specific" in reaction.text.lower()


def test_the_second_one_resolves_against_a_prior_list_query(temp_db):
    handle_message("add task walk the dog")
    handle_message("add task pay rent")
    handle_message("add task buy milk")
    listed = handle_message("what tasks do i have")

    reaction = handle_message("cancel the second one", conversation_state=listed.conversation_state)

    assert reaction.text  # some response either way
    open_titles = {t.title for t in task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)}
    # Exactly one of the three should now be missing - whichever was
    # actually second in the order Mochi displayed.
    assert len(open_titles) == 2


def test_stale_reference_does_not_silently_act_on_a_different_entity(temp_db):
    """If the remembered entity has since been completed/deleted through a
    different path, "it" must not resolve to some other item instead -
    it should fail closed and ask, never silently guess. Uses two
    still-open tasks afterwards so the (separate, pre-existing) "only one
    thing open" convenience fallback can't paper over the check this test
    actually cares about."""
    created = handle_message("add task buy milk")
    state = created.conversation_state
    # Completed through an unrelated, explicit command - the remembered
    # id no longer refers to anything open.
    handle_message("mark my task buy milk as done")
    handle_message("add task walk the dog")
    handle_message("add task pay rent")

    reaction = handle_message("actually cancel it", conversation_state=state)

    assert "which one" in reaction.text.lower()
    open_titles = {t.title for t in task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)}
    assert open_titles == {"Walk the dog", "Pay rent"}


def test_conversation_state_carries_forward_across_an_unrelated_list_query(temp_db):
    """Unlike pending_action, conversation_state is harmless to carry
    forward - listing reminders in between shouldn't erase memory of the
    task that was just created, since nothing about that list query
    conflicts with it."""
    created = handle_message("add task buy milk")
    unrelated = handle_message("do i have any reminders", conversation_state=created.conversation_state)

    reaction = handle_message("actually delete it", conversation_state=unrelated.conversation_state)

    assert "buy milk" in reaction.text.lower()
    assert task_manager.list_tasks(status=task_manager.TaskStatus.OPEN) == []
