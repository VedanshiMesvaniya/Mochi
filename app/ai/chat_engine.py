"""
Chat orchestration layer (spec sections 6/7/25).

This is intentionally the *only* place that:
  1. turns free-form chat text into a structured intent (app/ai/intent.py)
  2. validates + executes any resulting local tool call (app/tools/)
  3. reports back a single `ChatReaction` for the UI/character to render

Nothing here ever executes an LLM-proposed action without going through the
existing tool-layer validation (`ToolValidationError`) - see spec section 41
(Security). Reminders/timers/tasks are always handled by the deterministic
rule-based detector in app/ai/intent.py, never by the LLM - only open-ended
small talk that the detector doesn't recognize gets routed to a local LLM
(app/ai/llm.py), with a graceful canned-response fallback if one isn't
available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.ai.intent import DetectedIntent, detect_intent
from app.ai.llm import LLMUnavailable, ask as ask_llm
from app.calendar import google_calendar
from app.character.state_machine import EMOTION_PROFILE, CharacterState, Emotion
from app.core.config import settings
from app.core.exceptions import (
    CalendarError,
    GoogleCalendarNotConfigured,
    GoogleCalendarNotConnected,
    MochiError,
)
from app.core.logger import get_logger
from app.humor.meme_fetcher import pick_one_meme
from app.humor.trend_fetcher import pick_one_trend
from app.memory import relationship
from app.reminders import manager as reminder_manager
from app.tasks import manager as task_manager
from app.timers import manager as timer_manager
from app.tools import calendar_tools, reminder_tools, task_tools, timer_tools

logger = get_logger("mochi.ai.chat_engine")

_TOOL_MODULES = {
    "create_reminder": reminder_tools.create_reminder,
    "start_timer": timer_tools.start_timer,
    "create_task": task_tools.create_task,
}

# Reminders/timers get their tables created by their background schedulers
# at app startup (see app/main.py), but tasks have no scheduler - nothing
# guarantees `ensure_ready()` ran before the first chat message tries to
# create one. Calling it here too (it's idempotent - `CREATE TABLE IF NOT
# EXISTS`) means chat works correctly regardless of what's been opened yet.
_ENSURE_READY = {
    "create_reminder": reminder_manager.ensure_ready,
    "start_timer": timer_manager.ensure_ready,
    "create_task": task_manager.ensure_ready,
}

# Spec section 30 - lightweight familiarity flavor. NEW isn't listed here
# because the rule-based greeting in app/ai/intent.py already reads right
# for a Mochi that's just meeting you.
_FAMILIAR_GREETINGS = {
    relationship.GETTING_TO_KNOW: "Hey, good to see you again!",
    relationship.FAMILIAR: "You're back! I missed you~",
}


@dataclass
class ChatReaction:
    text: str
    emotion: Emotion
    animation: CharacterState
    sound: str | None = None
    # Spec section 23 (V4): a calendar write proposed but not yet
    # confirmed - e.g. {"kind": "calendar_create", "title": ..., "start_iso": ...}
    # or {"kind": "calendar_delete", "event_id": ..., "title": ...}. The
    # calling chat window (app/ui/chat_window.py) is responsible for
    # holding onto this between messages and passing it back into the
    # next handle_message() call as `pending_action` - chat_engine itself
    # is otherwise stateless between calls, same as everything else here.
    # None means "nothing awaiting confirmation."
    pending_action: Optional[dict] = None


def _emotion_and_animation(name: str) -> tuple[Emotion, CharacterState]:
    try:
        emotion = Emotion(name)
    except ValueError:
        emotion = Emotion.NEUTRAL
    profile = EMOTION_PROFILE.get(emotion, {})
    try:
        animation = CharacterState(profile.get("animation", "talking"))
    except ValueError:
        animation = CharacterState.TALKING
    return emotion, animation


def _list_tasks_reaction() -> "ChatReaction":
    task_manager.ensure_ready()
    tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    if not tasks:
        return ChatReaction(
            text="Nope, your task list is empty! Nothing hanging over you right now.",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
        )
    def _label(t) -> str:
        return f"{t.title} (due {t.due_at:%m-%d %I:%M %p})" if t.due_at else t.title

    shown = "; ".join(_label(t) for t in tasks[:5])
    more = f" (+{len(tasks) - 5} more)" if len(tasks) > 5 else ""
    plural = "task" if len(tasks) == 1 else "tasks"
    return ChatReaction(
        text=f"You've got {len(tasks)} open {plural}: {shown}{more}.",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
    )


def _list_reminders_reaction() -> "ChatReaction":
    reminder_manager.ensure_ready()
    reminders = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    if not reminders:
        return ChatReaction(
            text="You're all clear - no reminders waiting!",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
        )
    shown = "; ".join(f"{r.title} at {r.due_at:%I:%M %p}" for r in reminders[:5])
    more = f" (+{len(reminders) - 5} more)" if len(reminders) > 5 else ""
    plural = "reminder" if len(reminders) == 1 else "reminders"
    return ChatReaction(
        text=f"You've got {len(reminders)} {plural}: {shown}{more}.",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
    )


def _fuzzy_find(query: str, items: list, title_attr: str = "title"):
    """Best-matching item for `query` among `items` by word overlap, or
    None if nothing shares a word with it. Simple, deterministic scoring
    - good enough for resolving "mark X as done" against someone's own
    short task/reminder list; not meant to be a real search engine, and
    deliberately never guesses when the overlap is zero rather than
    silently completing the wrong thing."""
    if not items or not query:
        return None
    query_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not query_words:
        return None
    query_lower = query.lower()
    best, best_score = None, 0
    for item in items:
        title_lower = getattr(item, title_attr).lower()
        title_words = set(re.findall(r"[a-z0-9]+", title_lower))
        score = len(query_words & title_words)
        if query_lower in title_lower or title_lower in query_lower:
            score += 1
        if score > best_score:
            best, best_score = item, score
    return best


def _complete_task_reaction(tool_args: dict) -> "ChatReaction":
    task_manager.ensure_ready()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    if not open_tasks:
        return ChatReaction(
            text="You don't have any open tasks to mark done!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    match = _fuzzy_find(query, open_tasks)
    if match is None and not query and len(open_tasks) == 1:
        match = open_tasks[0]
    if match is None:
        shown = "; ".join(t.title for t in open_tasks[:5])
        return ChatReaction(
            text=f"Not sure which task you mean - your open ones: {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    task_manager.complete_task(match.id)
    return ChatReaction(
        text=f'Done! Marked "{match.title}" as complete.',
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        sound="chirp",
    )


def _cancel_task_reaction(tool_args: dict) -> "ChatReaction":
    task_manager.ensure_ready()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    if not open_tasks:
        return ChatReaction(
            text="There's nothing on your task list to cancel!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    match = _fuzzy_find(query, open_tasks)
    if match is None and not query and len(open_tasks) == 1:
        match = open_tasks[0]
    if match is None:
        shown = "; ".join(t.title for t in open_tasks[:5])
        return ChatReaction(
            text=f"Not sure which task you mean - your open ones: {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    task_manager.cancel_task(match.id)
    return ChatReaction(
        text=f'Okay, cancelled "{match.title}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
    )


def _complete_reminder_reaction(tool_args: dict) -> "ChatReaction":
    reminder_manager.ensure_ready()
    pending = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    if not pending:
        return ChatReaction(
            text="You don't have any pending reminders to mark done!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    match = _fuzzy_find(query, pending)
    if match is None and not query and len(pending) == 1:
        match = pending[0]
    if match is None:
        shown = "; ".join(r.title for r in pending[:5])
        return ChatReaction(
            text=f"Not sure which reminder you mean - your pending ones: {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    reminder_manager.complete_reminder(match.id)
    return ChatReaction(
        text=f'Done! Marked "{match.title}" as complete.',
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        sound="chirp",
    )


def _cancel_reminder_reaction(tool_args: dict) -> "ChatReaction":
    reminder_manager.ensure_ready()
    pending = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    if not pending:
        return ChatReaction(
            text="You don't have any pending reminders to cancel!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    match = _fuzzy_find(query, pending)
    if match is None and not query and len(pending) == 1:
        match = pending[0]
    if match is None:
        shown = "; ".join(r.title for r in pending[:5])
        return ChatReaction(
            text=f"Not sure which reminder you mean - your pending ones: {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    reminder_manager.cancel_reminder(match.id)
    return ChatReaction(
        text=f'Okay, cancelled "{match.title}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
    )


def _cancel_timer_reaction(tool_args: dict) -> "ChatReaction":
    timer_manager.ensure_ready()
    active = timer_manager.list_active_timers()
    if not active:
        return ChatReaction(
            text="You don't have any timers running!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    match = _fuzzy_find(query, active, title_attr="label")
    if match is None and not query and len(active) == 1:
        match = active[0]
    if match is None:
        shown = "; ".join(t.label for t in active[:5])
        return ChatReaction(
            text=f"Not sure which timer you mean - running ones: {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    timer_manager.cancel_timer(match.id)
    return ChatReaction(
        text=f'Okay, stopped "{match.label}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
    )


def _format_event(event: dict) -> str:
    if event["all_day"]:
        return event["title"]
    start = event["start"] or ""
    # start is an RFC3339 datetime like '2026-08-13T17:00:00-07:00' for
    # timed events (all-day events use the plain-date branch above) -
    # HH:MM is everything a spoken/chat reply needs, so avoid pulling in
    # a full datetime-parsing dependency just to reformat it.
    time_part = start[11:16] if len(start) >= 16 else start
    return f"{event['title']} at {time_part}" if time_part else event["title"]


def _events_reaction(events: list[dict], empty_text: str, label: str) -> "ChatReaction":
    if not events:
        return ChatReaction(
            text=empty_text,
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
        )
    shown = "; ".join(_format_event(e) for e in events[:5])
    more = f" (+{len(events) - 5} more)" if len(events) > 5 else ""
    plural = "thing" if len(events) == 1 else "things"
    return ChatReaction(
        text=f"You've got {len(events)} {plural} {label}: {shown}{more}.",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
    )


# Friendly, specific messages per failure mode (spec section 36: degrade
# gracefully, section 23: never silently pretend a calendar action
# happened). GoogleCalendarNotConnected/NotConfigured are both already
# actionable ("say 'connect my calendar'" / "enable X in .env") - their
# own message is used verbatim; only a genuinely unexpected CalendarError
# (e.g. a live API/network failure) gets a generic wrapper here.
def _calendar_error_reaction(exc: CalendarError) -> "ChatReaction":
    if isinstance(exc, (GoogleCalendarNotConfigured, GoogleCalendarNotConnected)):
        text = str(exc)
    else:
        text = f"Hmm, I couldn't check your calendar: {exc}"
    return ChatReaction(text=text, emotion=Emotion.CONFUSED, animation=CharacterState.CONFUSED)


def _calendar_today_reaction() -> "ChatReaction":
    try:
        events = google_calendar.get_today_events()
    except CalendarError as exc:
        return _calendar_error_reaction(exc)
    return _events_reaction(events, "You're all clear today - nothing on your calendar!", "today")


def _calendar_tomorrow_reaction() -> "ChatReaction":
    try:
        events = google_calendar.get_tomorrow_events()
    except CalendarError as exc:
        return _calendar_error_reaction(exc)
    return _events_reaction(
        events, "Nothing on your calendar tomorrow - a free day!", "tomorrow"
    )


def _calendar_upcoming_reaction() -> "ChatReaction":
    try:
        events = google_calendar.get_upcoming_events(days=7)
    except CalendarError as exc:
        return _calendar_error_reaction(exc)
    return _events_reaction(
        events, "Nothing coming up in the next week!", "coming up"
    )


def _calendar_connect_reaction() -> "ChatReaction":
    try:
        google_calendar.connect()
    except CalendarError as exc:
        return _calendar_error_reaction(exc)
    return ChatReaction(
        text="Connected! I can check your Google Calendar now.",
        emotion=Emotion.EXCITED,
        animation=CharacterState.EXCITED,
        sound="chirp",
    )


def _calendar_disconnect_reaction() -> "ChatReaction":
    had_connection = google_calendar.disconnect()
    text = (
        "Okay, I've forgotten your Google Calendar sign-in."
        if had_connection
        else "Your calendar wasn't connected, so there's nothing to undo."
    )
    return ChatReaction(text=text, emotion=Emotion.NEUTRAL, animation=CharacterState.IDLE)


# ---------------------------------------------------------------------------
# Google Calendar writes (spec section 23, V4) - propose, then confirm.
#
# Nothing here ever calls app/tools/calendar_tools.py's create_event/
# update_event/delete_event with confirmed=True except _resolve_pending_action,
# and that only runs once the user's *next* message is recognized as an
# explicit "yes" (see _classify_confirmation) in response to a proposal
# this module itself generated. A stray/hallucinated "create an event"
# tool call from anywhere else in the app has no path to actually writing
# anything - the confirmation gate lives here, not in the LLM's judgement.
# ---------------------------------------------------------------------------

# Deliberately short, exact-phrase matching (not substring/regex) - a
# confirmation is a yes/no decision about something specific and
# consequential (spec section 23), so it should require an unambiguous
# reply rather than accidentally firing because "yes" appears inside a
# longer, unrelated sentence.
_CONFIRM_PHRASES = {
    "yes", "yeah", "yep", "yup", "confirm", "sure", "ok", "okay",
    "do it", "go ahead", "add it", "please do", "please", "confirmed",
}
_CANCEL_PHRASES = {
    "no", "nope", "nah", "cancel", "never mind", "nevermind",
    "don't", "dont", "stop", "no thanks",
}


def _classify_confirmation(text: str) -> Optional[bool]:
    cleaned = text.strip().lower().strip(" .!?")
    if cleaned in _CONFIRM_PHRASES:
        return True
    if cleaned in _CANCEL_PHRASES:
        return False
    return None


def _describe_when(start_iso: str) -> str:
    try:
        start_dt = datetime.fromisoformat(start_iso)
    except ValueError:
        return start_iso
    return start_dt.strftime("%a %b %d at %I:%M %p")


def _calendar_create_proposal(tool_args: dict) -> "ChatReaction":
    title = tool_args["title"]
    start_iso = tool_args["start_iso"]
    when = _describe_when(start_iso)
    return ChatReaction(
        text=(
            f"I found this:\n\n{title}\n{when}\n\n"
            "Add it to your Google Calendar? (yes/no)"
        ),
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
        pending_action={"kind": "calendar_create", "title": title, "start_iso": start_iso},
    )


def _calendar_delete_proposal(tool_args: dict) -> "ChatReaction":
    query = tool_args.get("query")
    time_of_day = tool_args.get("time_of_day")
    around = None
    if time_of_day:
        try:
            around = datetime.strptime(time_of_day, "%H:%M")
        except ValueError:
            around = None

    try:
        matches = google_calendar.find_event(query=query, around=around)
    except CalendarError as exc:
        return _calendar_error_reaction(exc)

    if not matches:
        return ChatReaction(
            text="I couldn't find a matching event on your calendar in the next couple of days.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    target = matches[0]
    when = _format_event(target)
    extra = (
        f" (+{len(matches) - 1} other possible match"
        f"{'es' if len(matches) > 2 else ''} - tell me if this isn't the right one)"
        if len(matches) > 1
        else ""
    )
    return ChatReaction(
        text=f"I found: {when}.{extra}\n\nCancel this event? (yes/no)",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
        pending_action={
            "kind": "calendar_delete",
            "event_id": target["id"],
            "title": target["title"],
        },
    )


# Same shape/reasoning as _LIST_HANDLERS below, but for intents that
# *propose* a write instead of reading data - each of these returns a
# ChatReaction carrying a fresh `pending_action` for the confirmation
# flow above, rather than executing anything immediately.
def _check_on_reaction(tool_args: dict) -> "ChatReaction":
    """Handles "check on X" / "did you remind me about X" / "status of X" -
    bug report: this phrasing fell through to the open-ended LLM, which has
    no actual access to the task/reminder database and would just
    improvise a plausible-sounding "I'll remind you..." reply - a real
    hallucination, since nothing was actually checked or created. This
    answers from the real database only, and says so plainly when nothing
    matches rather than inventing a status."""
    task_manager.ensure_ready()
    reminder_manager.ensure_ready()
    query = (tool_args.get("query") or "").strip()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    pending_reminders = reminder_manager.list_reminders(
        status=reminder_manager.ReminderStatus.PENDING
    )

    if not query:
        # No specific title given (e.g. a bare "you forgot to remind me!"
        # complaint rather than "did you forget to remind me about X") -
        # answer from the real pending list instead of just demanding a
        # title, since "I don't have anything on file" is itself a real,
        # useful answer here.
        if not pending_reminders and not open_tasks:
            return ChatReaction(
                text="I don't actually have any reminders or tasks on file for you right now - want me to set one?",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        shown = "; ".join(
            f'"{r.title}" at {r.due_at:%I:%M %p}' for r in pending_reminders[:3]
        )
        if shown:
            return ChatReaction(
                text=f"I've got: {shown}. Is one of those what you meant?",
                emotion=Emotion.CURIOUS,
                animation=CharacterState.THINKING,
            )
        return ChatReaction(
            text="Check on what, exactly? Give me a bit of the title.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    task_match = _fuzzy_find(query, open_tasks)
    reminder_match = _fuzzy_find(query, pending_reminders)

    if reminder_match is not None and task_match is not None:
        return ChatReaction(
            text=(
                f'I have both a reminder ("{reminder_match.title}" at '
                f"{reminder_match.due_at:%I:%M %p}) and a task "
                f'("{task_match.title}") that match that - which one?'
            ),
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
        )
    if reminder_match is not None:
        return ChatReaction(
            text=f'Yep - "{reminder_match.title}" is set for {reminder_match.due_at:%I:%M %p}.',
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
        )
    if task_match is not None:
        due_note = (
            f" (due {task_match.due_at:%m-%d %I:%M %p})" if task_match.due_at else ""
        )
        return ChatReaction(
            text=f'Yep - "{task_match.title}" is still open on your task list{due_note}.',
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
        )
    return ChatReaction(
        text=f"I don't have anything like \"{query}\" saved as a task or reminder.",
        emotion=Emotion.CONFUSED,
        animation=CharacterState.CONFUSED,
    )


def _complete_ambiguous_reaction(_tool_args: dict) -> "ChatReaction":
    """Handles "mark it as done" / "that's done" / "I finished it" - i.e.
    the same completion request as complete_task/complete_reminder, but
    phrased without the literal word "task"/"reminder" so TASK_DONE_TRIGGER/
    REMINDER_DONE_TRIGGER never match it (bug report: this used to fall to
    the open-ended LLM, which would say something like "Okay, I'll take
    care of it" without actually marking anything done anywhere - another
    hallucination). Resolves "it" against whatever's actually open across
    both stores; only auto-completes when that's unambiguous."""
    task_manager.ensure_ready()
    reminder_manager.ensure_ready()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    pending_reminders = reminder_manager.list_reminders(
        status=reminder_manager.ReminderStatus.PENDING
    )
    combined = [("task", t) for t in open_tasks] + [("reminder", r) for r in pending_reminders]

    if not combined:
        return ChatReaction(
            text="I don't have anything open to mark done!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    if len(combined) > 1:
        shown = "; ".join(f"{kind}: {item.title}" for kind, item in combined[:5])
        return ChatReaction(
            text=f"Which one do you mean? {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    kind, item = combined[0]
    if kind == "task":
        task_manager.complete_task(item.id)
    else:
        reminder_manager.complete_reminder(item.id)
    return ChatReaction(
        text=f'Done! Marked "{item.title}" as complete.',
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        sound="chirp",
    )


def _cancel_ambiguous_reaction(_tool_args: dict) -> "ChatReaction":
    """Handles "cancel it" / "delete it" / "scratch that" - the
    cancellation counterpart to _complete_ambiguous_reaction above (see
    AMBIGUOUS_CANCEL_TRIGGER in app/ai/intent.py for the full bug report).
    Resolves "it" against whatever's actually open/pending/running across
    all three stores (tasks, reminders, AND running timers - unlike
    completion, an active timer is a perfectly normal thing to want to
    cancel); only auto-cancels when that's unambiguous, and never
    silently claims success without a real write."""
    task_manager.ensure_ready()
    reminder_manager.ensure_ready()
    timer_manager.ensure_ready()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    pending_reminders = reminder_manager.list_reminders(
        status=reminder_manager.ReminderStatus.PENDING
    )
    active_timers = timer_manager.list_active_timers()
    combined = (
        [("task", t) for t in open_tasks]
        + [("reminder", r) for r in pending_reminders]
        + [("timer", t) for t in active_timers]
    )

    if not combined:
        return ChatReaction(
            text="I don't have anything open to cancel!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    if len(combined) > 1:
        def _label(kind: str, item) -> str:
            title = item.label if kind == "timer" else item.title
            return f"{kind}: {title}"

        shown = "; ".join(_label(kind, item) for kind, item in combined[:5])
        return ChatReaction(
            text=f"Which one do you mean? {shown}.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    kind, item = combined[0]
    if kind == "task":
        task_manager.cancel_task(item.id)
        label = item.title
    elif kind == "reminder":
        reminder_manager.cancel_reminder(item.id)
        label = item.title
    else:
        timer_manager.cancel_timer(item.id)
        label = item.label
    return ChatReaction(
        text=f'Okay, cancelled "{label}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
    )


_PROPOSAL_HANDLERS = {
    "calendar_create_event": _calendar_create_proposal,
    "calendar_delete_event": _calendar_delete_proposal,
}


def _resolve_pending_action(pending_action: dict) -> "ChatReaction":
    """Called only after the user's message was classified as an explicit
    confirmation (see _classify_confirmation) for a proposal this module
    itself generated last turn. Always calls the calendar_tools write
    function with confirmed=True - the one and only place in the whole
    app that ever does."""
    kind = pending_action.get("kind")

    if kind == "calendar_create":
        try:
            calendar_tools.create_event(
                pending_action["title"], pending_action["start_iso"], confirmed=True
            )
        except MochiError as exc:
            return ChatReaction(
                text=f"Hmm, I couldn't add that: {exc}",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        return ChatReaction(
            text=f"Done! Added \"{pending_action['title']}\" to your calendar.",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
        )

    if kind == "calendar_delete":
        try:
            calendar_tools.delete_event(pending_action["event_id"], confirmed=True)
        except MochiError as exc:
            return ChatReaction(
                text=f"Hmm, I couldn't cancel that: {exc}",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        return ChatReaction(
            text=f"Done! Cancelled \"{pending_action['title']}\".",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
        )

    # Should be unreachable (every place that sets pending_action uses a
    # known `kind`) - but per spec section 41/36, never silently no-op on
    # something we don't recognize.
    logger.warning("Unknown pending_action kind: %r", kind)
    return ChatReaction(
        text="Hmm, I lost track of what we were confirming. Could you try again?",
        emotion=Emotion.CONFUSED,
        animation=CharacterState.CONFUSED,
    )


# Read-only DB queries (spec: "it can not read db, make it read db so it
# can answer") plus the Google Calendar read/connect/disconnect actions -
# handled entirely separately from _TOOL_MODULES below. Those are
# fire-and-forget writes whose response text is authored ahead of time in
# intent.py; everything in this dict needs to read a DB/live API *first*
# and build the reply from whatever's actually there, so a small local
# LLM never gets a chance to hallucinate an answer to a factual "what's
# on my calendar" question (see the list_tasks/list_reminders/calendar_*
# DetectedIntents, all of which carry an empty `response`).
_LIST_HANDLERS = {
    "list_tasks": _list_tasks_reaction,
    "list_reminders": _list_reminders_reaction,
    "calendar_today": _calendar_today_reaction,
    "calendar_tomorrow": _calendar_tomorrow_reaction,
    "calendar_upcoming": _calendar_upcoming_reaction,
    "calendar_connect": _calendar_connect_reaction,
    "calendar_disconnect": _calendar_disconnect_reaction,
}

# "Act on an existing item" handlers (spec bug fix: "mark my task ... as
# done" / cancel-a-timer / etc were completely unreachable from chat -
# only *creation* commands were wired up before this). Unlike
# _LIST_HANDLERS these take the intent's tool_args (the free-text query
# to fuzzy-match against real titles - see _fuzzy_find above), same
# calling convention as _PROPOSAL_HANDLERS below.
_ACTION_HANDLERS = {
    "complete_task": _complete_task_reaction,
    "cancel_task": _cancel_task_reaction,
    "complete_reminder": _complete_reminder_reaction,
    "cancel_reminder": _cancel_reminder_reaction,
    "cancel_timer": _cancel_timer_reaction,
    "check_on": _check_on_reaction,
    "complete_ambiguous": _complete_ambiguous_reaction,
    "cancel_ambiguous": _cancel_ambiguous_reaction,
}


def handle_message(
    text: str,
    history: Optional[list[tuple[str, str]]] = None,
    pending_action: Optional[dict] = None,
) -> ChatReaction:
    """Process one chat message end-to-end and return how Mochi should react.

    `history` (spec: "for chat it should store the current chat memory...
    remember whole chat [until closed]") is the calling chat window's own
    session-so-far as (role, text) pairs, oldest first - see
    app/ui/chat_window.py, which owns and clears it. It's only actually
    used for the open-ended LLM fallback below; deterministic intents
    (reminders/tasks/etc.) don't need conversational context to act
    correctly on a single, self-contained command.

    `pending_action` (spec section 23, V4) is a calendar write this
    module proposed on a *previous* call and is still awaiting a yes/no
    answer for - also owned by the calling chat window, which is expected
    to pass back whatever the previous ChatReaction.pending_action was.
    If the message is an unambiguous confirmation/cancellation, it's
    resolved here before anything else runs; otherwise it's carried
    forward unchanged in the returned ChatReaction so an unrelated
    message in between doesn't silently drop it.
    """
    if pending_action is not None:
        confirmation = _classify_confirmation(text)
        if confirmation is True:
            return _resolve_pending_action(pending_action)
        if confirmation is False:
            return ChatReaction(
                text="Okay, never mind!",
                emotion=Emotion.NEUTRAL,
                animation=CharacterState.IDLE,
            )
        # Anything else: not a clear yes/no, so fall through to normal
        # handling below and keep waiting - the pending_action is carried
        # forward at every return point past this one.

    intent: DetectedIntent = detect_intent(text)
    # Observability (bug report: reminders/timers/tasks "not getting set"
    # with nothing in the logs to say why): log what every message was
    # actually classified as *before* any handler runs, so a message that
    # silently fails to match create_reminder/start_timer/create_task -
    # e.g. because the phrasing didn't match app/ai/intent.py's regex
    # triggers - is visible in the log as "unknown"/some other intent
    # instead of leaving no trace at all.
    logger.info("Message %r -> intent=%s tool=%s args=%s", text, intent.name, intent.tool, intent.tool_args)

    if intent.name in _LIST_HANDLERS:
        try:
            reaction = _LIST_HANDLERS[intent.name]()
        except Exception:  # noqa: BLE001 - never let a bad DB read crash chat
            logger.exception("Failed to read DB for intent '%s'", intent.name)
            reaction = ChatReaction(
                text="Hmm, I couldn't check that just now.",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        reaction.pending_action = pending_action
        return reaction

    if intent.name in _ACTION_HANDLERS:
        try:
            reaction = _ACTION_HANDLERS[intent.name](intent.tool_args)
        except MochiError as exc:
            logger.info("Action '%s' rejected: %s", intent.name, exc)
            reaction = ChatReaction(
                text=f"Hmm, I couldn't do that: {exc}",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        except Exception:  # noqa: BLE001 - never let a bad DB write crash chat
            logger.exception("Failed to run action for intent '%s'", intent.name)
            reaction = ChatReaction(
                text="Oops, something went wrong on my end.",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        reaction.pending_action = pending_action
        return reaction

    if intent.name in _PROPOSAL_HANDLERS:
        try:
            # Proposal handlers always set their own fresh pending_action
            # on the returned reaction - this deliberately replaces
            # whatever was passed in, since starting a new write request
            # supersedes an old unconfirmed one rather than stacking them.
            return _PROPOSAL_HANDLERS[intent.name](intent.tool_args)
        except Exception:  # noqa: BLE001 - never let a bad proposal crash chat
            logger.exception("Failed to build proposal for intent '%s'", intent.name)
            return ChatReaction(
                text="Oops, something went wrong setting that up.",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                pending_action=pending_action,
            )

    try:
        interaction_count = relationship.record_interaction()
    except Exception:  # noqa: BLE001 - familiarity tracking must never break chat
        logger.exception("Failed to record interaction (non-fatal)")
        interaction_count = 0
    familiarity = relationship.level_for_count(interaction_count)

    response = intent.response
    emotion = intent.emotion
    animation = intent.animation
    sound = intent.sound

    if intent.name == "greeting" and familiarity in _FAMILIAR_GREETINGS:
        response = _FAMILIAR_GREETINGS[familiarity]

    # The rule-based detector above is intentionally deterministic for
    # actionable things (reminders/timers/tasks - spec section 41, these
    # must never be left to an LLM's judgement). But its catch-all for
    # everything else it doesn't recognize is a single canned line, which
    # is the actual "chat can't answer anything" gap. For that bucket
    # only, try a real local LLM reply and fall back to the canned line
    # if one isn't available (see app/ai/llm.py for why this is safe).
    if intent.name == "unknown":
        try:
            # pick_one_meme()/pick_one_trend() are cheap cache reads (near-
            # instant no-op unless settings.trend_awareness_enabled is on
            # and something's already cached) - never fetch over the
            # network here, only read whatever the background job already
            # cached. See app/humor/meme_fetcher.py, app/humor/trend_fetcher.py.
            llm_reply = ask_llm(
                text,
                familiarity=familiarity,
                history=history,
                trend_topic=pick_one_trend(),
                meme_premise=pick_one_meme(),
            )
            response = llm_reply["response"]
            emotion, animation = _emotion_and_animation(llm_reply["emotion"])
            sound = EMOTION_PROFILE.get(emotion, {}).get("sound")
        except LLMUnavailable as exc:
            # Previously this fell back to the exact same generic "I'm not
            # sure what you mean yet" line used for a truly-unrecognized
            # message - which made a *working-as-designed* "Ollama isn't
            # running" situation look identical to a broken/confused
            # Mochi. Surface the real reason so the person can actually
            # fix it (install Ollama / pull the model / start it) instead
            # of assuming chat itself is broken.
            logger.info("Local LLM unavailable, using setup-hint fallback: %s", exc)
            response = (
                "Mrrp... my brain's offline right now! Install Ollama, run "
                f"'ollama pull {settings.llm_model}', and make sure Ollama's "
                "running - then I can actually chat about anything."
            )
            emotion = Emotion.SLEEPY
            animation = CharacterState.SLEEPY
            sound = None

    if intent.tool:
        tool_fn = _TOOL_MODULES.get(intent.tool)
        if tool_fn is None:
            logger.warning("Unknown tool requested by intent: %s", intent.tool)
        else:
            try:
                ensure_ready = _ENSURE_READY.get(intent.tool)
                if ensure_ready is not None:
                    ensure_ready()
                result = tool_fn(**intent.tool_args)
                logger.info("Tool '%s' succeeded: %s", intent.tool, result)
            except MochiError as exc:
                logger.info("Tool '%s' rejected: %s", intent.tool, exc)
                return ChatReaction(
                    text=f"Hmm, I couldn't do that: {exc}",
                    emotion=Emotion.CONFUSED,
                    animation=CharacterState.CONFUSED,
                    pending_action=pending_action,
                )
            except Exception:  # noqa: BLE001 - never let a bad tool crash chat
                logger.exception("Unexpected error running tool '%s'", intent.tool)
                return ChatReaction(
                    text="Oops, something went wrong on my end.",
                    emotion=Emotion.CONFUSED,
                    animation=CharacterState.CONFUSED,
                    pending_action=pending_action,
                )

    return ChatReaction(
        text=response,
        emotion=emotion,
        animation=animation,
        sound=sound,
        pending_action=pending_action,
    )
