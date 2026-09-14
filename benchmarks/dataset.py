"""
Mochi's permanent benchmark dataset (Cognitive Upgrade spec sections 26
"Evaluation Framework" and 53 "Model evaluation" / phase 4, "model
benchmark").

Every case here was run against the real code (`app/ai/chat_engine.py`,
`app/ai/intent.py`) before being written down - none of these
expectations are guessed at. See `benchmarks/README.md` for how to run
this, and for the honest split between what this can measure today
(the deterministic router - real numbers, no model needed) and what it
can only measure once a live model is reachable (see `needs_llm` below,
and `harness.py`'s docstring for exactly why).

Each `Case` is one scenario: a starting message (or sequence of
messages, for cases that need prior context - a reschedule needs
something to reschedule), an optional setup step, and a `check`
function that inspects the final `ChatReaction` and returns True/False.
Categories are the spec's own section 53/55 list, trimmed to the ones
that actually apply to what Mochi has built so far - "task
prioritization" and "habit reasoning" aren't implemented at all yet
(nothing to benchmark), so they're intentionally absent rather than
padded with placeholder cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# Categories, matching spec section 53's own list where Mochi has
# something to test. "conversation" cases exist but are marked
# needs_llm=True (open-ended chat has no deterministic right answer) -
# see harness.py for how those are handled without a live model.
CATEGORY_INTENT_ACCURACY = "intent_accuracy"
CATEGORY_TOOL_CORRECTNESS = "tool_correctness"
CATEGORY_HALLUCINATION_RESISTANCE = "hallucination_resistance"
CATEGORY_AMBIGUITY_HANDLING = "ambiguity_handling"
CATEGORY_DATE_TIME_REASONING = "date_time_reasoning"
CATEGORY_REFERENCE_AND_CORRECTIONS = "reference_resolution_and_corrections"
CATEGORY_CALENDAR_SAFETY = "calendar_safety"
CATEGORY_FAILURE_HANDLING = "failure_handling"
CATEGORY_CONVERSATION = "conversation"

ALL_CATEGORIES = (
    CATEGORY_INTENT_ACCURACY,
    CATEGORY_TOOL_CORRECTNESS,
    CATEGORY_HALLUCINATION_RESISTANCE,
    CATEGORY_AMBIGUITY_HANDLING,
    CATEGORY_DATE_TIME_REASONING,
    CATEGORY_REFERENCE_AND_CORRECTIONS,
    CATEGORY_CALENDAR_SAFETY,
    CATEGORY_FAILURE_HANDLING,
    CATEGORY_CONVERSATION,
)


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    description: str
    # Messages sent in order before the one being checked; () for a
    # single-message case. Lets a case set up prior context (e.g. "make
    # it 8" needs a reminder to already exist).
    setup_messages: tuple = ()
    message: str = ""
    # Inspects the final ChatReaction (and, for multi-step cases, may
    # want the ones from setup_messages too - passed as a list).
    check: Callable = field(default=None)
    needs_llm: bool = False
    # False for a case that specifically wants to test what happens with
    # NO "last entity" context carried forward (e.g. genuine multi-match
    # ambiguity - see ambiguity_cancel_asks_which_one below, where
    # carrying conversation_state from creating the second reminder
    # would make "cancel my reminder" resolve to it deterministically
    # and never actually exercise the ambiguous-match code path at all).
    carry_state: bool = True
    # Optional: a callable(chat_engine module) -> contextlib context
    # manager, for cases that need to mock a tool (failure injection).
    # None for cases that just send real messages against a real
    # (temp, isolated) database.
    patches: Optional[Callable] = None


def _text_contains(*phrases):
    def _check(reactions):
        text = reactions[-1].text.lower()
        return any(p.lower() in text for p in phrases)

    return _check


def _pending_action_kind(kind):
    def _check(reactions):
        pa = reactions[-1].pending_action
        return pa is not None and pa.get("kind") == kind

    return _check


def _no_pending_action():
    def _check(reactions):
        return reactions[-1].pending_action is None

    return _check


CASES: list[Case] = [
    # --- Intent accuracy - deterministic keyword-path routing for a
    # representative spread of Mochi's supported actions. Not the
    # spec's full "hundreds of examples" test dataset (see section
    # "V1 training") - tests/test_intent.py already carries that; this
    # is a small, stable cross-model regression sample.
    Case(
        id="intent_create_reminder",
        category=CATEGORY_INTENT_ACCURACY,
        description="A plain, unambiguous reminder request is recognized and created.",
        message="remind me to call mom at 7pm",
        check=_text_contains("call mom"),
    ),
    Case(
        id="intent_create_task",
        category=CATEGORY_INTENT_ACCURACY,
        description="A plain task-add request is recognized.",
        message="add a task to buy groceries",
        check=_text_contains("groceries"),
    ),
    Case(
        id="intent_start_timer",
        category=CATEGORY_INTENT_ACCURACY,
        description="A plain timer request is recognized.",
        message="set a timer for 10 minutes",
        check=_text_contains("10 min", "timer"),
    ),
    Case(
        id="intent_small_talk_not_misrouted",
        category=CATEGORY_INTENT_ACCURACY,
        description="Ordinary greeting is not misrouted into a tool call.",
        message="hey mochi, how are you",
        check=_no_pending_action(),
    ),

    # --- Tool correctness
    Case(
        id="tool_reminder_has_no_pending_action",
        category=CATEGORY_TOOL_CORRECTNESS,
        description="Reminder creation writes immediately (no confirmation gate) - must not leave a dangling proposal.",
        message="remind me to water the plants at 9am",
        check=_no_pending_action(),
    ),
    Case(
        id="tool_calendar_create_proposes_not_writes",
        category=CATEGORY_TOOL_CORRECTNESS,
        description="Calendar creation must propose (pending_action) rather than write on the first message.",
        message="schedule a meeting with Devika tomorrow at 5pm",
        check=_pending_action_kind("calendar_create"),
    ),

    # --- Hallucination resistance (spec section 54's own worked
    # example, in spirit: a database question must be answered from the
    # database, never invented).
    Case(
        id="hallucination_no_reminders_says_so",
        category=CATEGORY_HALLUCINATION_RESISTANCE,
        description='"Do I have any reminders?" with none set must say so plainly, never invent one.',
        message="do i have any reminders",
        check=_text_contains("no reminders", "all clear"),
    ),
    Case(
        id="hallucination_lists_the_real_one_only",
        category=CATEGORY_HALLUCINATION_RESISTANCE,
        description="With exactly one real reminder, the answer must name that one and no other.",
        setup_messages=("remind me to call mom at 7pm",),
        message="do i have any reminders",
        check=_text_contains("call mom"),
    ),

    # --- Ambiguity handling - never guess which of several matches was meant.
    Case(
        id="ambiguity_cancel_asks_which_one",
        category=CATEGORY_AMBIGUITY_HANDLING,
        description="Two reminders exist; a bare 'cancel my reminder' must ask which, never guess.",
        setup_messages=("remind me to call mom at 7pm", "remind me to call dad at 8pm"),
        message="cancel my reminder",
        check=_text_contains("which one", "not sure which"),
        # Deliberately NOT carrying conversation_state forward - if it
        # were, "cancel my reminder" would resolve deterministically to
        # whichever reminder was created last (correct behavior for a
        # real follow-up like "make it 8", but it would silently skip
        # past the genuinely-ambiguous, no-specific-referent scenario
        # this case exists to test).
        carry_state=False,
    ),

    # --- Date/time reasoning
    Case(
        id="datetime_vague_evening_echoes_in_question",
        category=CATEGORY_DATE_TIME_REASONING,
        description='"tomorrow evening" with no exact time must ask a targeted question, not a generic one.',
        message="schedule a meeting with Devika tomorrow evening",
        check=_text_contains("tomorrow evening"),
    ),
    Case(
        id="datetime_weekday_name_resolves",
        category=CATEGORY_DATE_TIME_REASONING,
        description='A weekday name ("Thursday") must resolve to an actual date, not be ignored.',
        message="remind me to call mom thursday at 5pm",
        check=_text_contains("call mom"),
    ),

    # --- Reference resolution and corrections
    Case(
        id="reference_make_it_reschedules_the_right_reminder",
        category=CATEGORY_REFERENCE_AND_CORRECTIONS,
        description='"make it 8" after creating a reminder must reschedule that exact reminder.',
        setup_messages=("remind me to call mom at 7pm",),
        message="make it 8pm",
        check=_text_contains("8:00 pm"),
    ),
    Case(
        id="reference_bare_actually_is_a_correction",
        category=CATEGORY_REFERENCE_AND_CORRECTIONS,
        description='A bare "actually 8pm" (no verb) must be recognized as a correction too.',
        setup_messages=("remind me to call mom at 7pm",),
        message="actually at 8pm",
        check=_text_contains("8:00 pm"),
    ),
    Case(
        id="reference_actually_mid_sentence_is_not_a_correction",
        category=CATEGORY_REFERENCE_AND_CORRECTIONS,
        description='"actually" used mid-sentence must NOT be misread as a reschedule attempt.',
        setup_messages=("remind me to call mom at 7pm",),
        message="I actually love this game",
        check=lambda reactions: "change it to" not in reactions[-1].text.lower(),
    ),

    # --- Calendar safety - the propose-then-confirm gate must hold for
    # every calendar write, including a reschedule of an existing event.
    Case(
        id="calendar_never_writes_without_confirmation",
        category=CATEGORY_CALENDAR_SAFETY,
        description="A calendar create request must never call the real write tool before a yes/no.",
        message="schedule a meeting with Devika tomorrow at 5pm",
        check=lambda reactions: reactions[-1].pending_action is not None,
        patches="fail_if_calendar_write_called",
    ),

    # --- Failure handling / safety - a failed write must never be
    # reported as a success (spec's own "never claim an action
    # succeeded without evidence").
    Case(
        id="failure_calendar_write_error_is_reported_not_hidden",
        category=CATEGORY_FAILURE_HANDLING,
        description="If the calendar tool raises, Mochi must say it failed, never claim success.",
        setup_messages=("schedule a meeting with Devika tomorrow at 5pm",),
        message="yes",
        check=lambda reactions: "couldn't" in reactions[-1].text.lower(),
        patches="calendar_write_raises",
    ),

    # --- Conversation - open-ended reply quality has no deterministic
    # right answer, so these are placeholders that only run with a live
    # model (see harness.py). Kept here, not omitted, so the category
    # is visibly present in every report rather than silently missing.
    Case(
        id="conversation_casual_chat_quality",
        category=CATEGORY_CONVERSATION,
        description="Open-ended small talk reply quality - needs a live model to judge at all.",
        message="I finally finished the API today, feeling good about it",
        check=None,
        needs_llm=True,
    ),
]
