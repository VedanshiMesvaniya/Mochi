"""End-to-end tests for multi-target conversational selection
(conversational-issues report P0: "Add Multi-Target Conversational Entity
Resolution") - i.e. that "three of them"/"all of them"/"the first two"
actually reach and act on the right database rows through
handle_message(), not just that conversation_state's parser works in
isolation.
"""

from __future__ import annotations

from app.ai.chat_engine import handle_message
from app.reminders import manager as reminder_manager
from app.tasks import manager as task_manager


def _make_three_tasks():
    handle_message("add task walk the dog")
    handle_message("add task pay rent")
    handle_message("add task buy milk")
    return handle_message("list my tasks")


def test_all_of_them_completes_every_listed_task(temp_db):
    listed = _make_three_tasks()

    reaction = handle_message(
        "mark all of them as done", conversation_state=listed.conversation_state
    )

    assert "3" in reaction.text
    assert task_manager.list_tasks() == []


def test_first_two_completes_only_the_first_two_in_listed_order(temp_db):
    listed = _make_three_tasks()

    reaction = handle_message(
        "complete the first two", conversation_state=listed.conversation_state
    )

    assert "2" in reaction.text
    remaining = {t.title for t in task_manager.list_tasks()}
    assert remaining == {"Buy milk"}


def test_last_two_completes_only_the_last_two_in_listed_order(temp_db):
    listed = _make_three_tasks()

    reaction = handle_message(
        "cancel the last two", conversation_state=listed.conversation_state
    )

    assert "2" in reaction.text
    remaining = {t.title for t in task_manager.list_tasks()}
    assert remaining == {"Walk the dog"}


def test_n_of_them_phrasing_resolves_to_first_n(temp_db):
    """"three of them" should behave the same as "the first three" - both
    describe the first N entities in the order they were last shown."""
    listed = _make_three_tasks()

    reaction = handle_message(
        "three of them check as done", conversation_state=listed.conversation_state
    )

    assert "3" in reaction.text
    assert task_manager.list_tasks() == []


def test_out_of_range_quantity_never_guesses(temp_db):
    """Asking for more than were ever shown must not silently act on a
    smaller subset or on unrelated open items - it should fall back to
    asking, exactly like an unresolvable single reference does."""
    handle_message("add task walk the dog")
    handle_message("add task pay rent")
    listed = handle_message("list my tasks")

    reaction = handle_message(
        "complete the first five", conversation_state=listed.conversation_state
    )

    assert reaction.emotion.name == "CONFUSED"
    remaining = {t.title for t in task_manager.list_tasks()}
    assert remaining == {"Walk the dog", "Pay rent"}


def test_both_requires_exactly_two_remembered_candidates(temp_db):
    """"both" only makes sense against exactly two remembered candidates
    - with three, it must not guess which two are meant."""
    listed = _make_three_tasks()

    reaction = handle_message("cancel both", conversation_state=listed.conversation_state)

    assert reaction.emotion.name == "CONFUSED"
    assert len(task_manager.list_tasks()) == 3


def test_both_resolves_when_exactly_two_were_shown(temp_db):
    handle_message("add task walk the dog")
    listed = handle_message("add task pay rent")
    listed = handle_message("list my tasks")

    reaction = handle_message("cancel both", conversation_state=listed.conversation_state)

    assert "2" in reaction.text
    assert task_manager.list_tasks() == []


def test_multi_target_selection_does_not_disturb_completed_items(temp_db):
    """A candidate that's already been acted on by the time the
    multi-target reference is resolved (completed/cancelled through some
    other path) must simply be skipped, never re-processed or reported as
    freshly changed."""
    handle_message("add task walk the dog")
    handle_message("add task pay rent")
    listed = handle_message("list my tasks")
    task_manager.complete_task(task_manager.list_tasks()[0].id)

    reaction = handle_message(
        "mark all of them as done", conversation_state=listed.conversation_state
    )

    assert "pay rent" in reaction.text.lower()
    assert task_manager.list_tasks() == []


def test_singular_ordinal_reference_still_resolves_to_one_entity(temp_db):
    """Regression guard: multi-target parsing must never hijack the
    existing singular ordinal path ("the first one") - that must keep
    resolving to exactly one entity with its original response wording."""
    listed = _make_three_tasks()

    reaction = handle_message(
        "complete the first one", conversation_state=listed.conversation_state
    )

    assert "walk the dog" in reaction.text.lower()
    remaining = {t.title for t in task_manager.list_tasks()}
    assert remaining == {"Pay rent", "Buy milk"}


def test_all_of_them_works_across_reminders_too(temp_db):
    handle_message("remind me to call mom at 7pm")
    handle_message("remind me to water plants at 8pm")
    listed = handle_message("list my reminders")

    reaction = handle_message(
        "cancel all of them", conversation_state=listed.conversation_state
    )

    assert "2" in reaction.text
    assert reminder_manager.list_reminders(
        status=reminder_manager.ReminderStatus.PENDING
    ) == []
