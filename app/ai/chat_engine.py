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

from app.ai import semantic_intent
from app.ai import conversation_state as convo
from app.ai.db_glossary import QueryPlan, build_plan
from app.ai.intent import DetectedIntent, build_semantic_intent, detect_intent
from app.ai.llm import LLMUnavailable, ask as ask_llm, phrase_data_answer
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

# Which conversation_state "entity_type" a successful _TOOL_MODULES create
# call should be remembered as (security review I1/I3) - lets an immediate
# follow-up like "actually delete it" or "make it 8" resolve without
# repeating the title. Only creation tools produce a new entity worth
# remembering this way.
_CREATE_TOOL_ENTITY_KINDS = {
    "create_reminder": "reminder",
    "start_timer": "timer",
    "create_task": "task",
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

# Same idea, for relational/social messages ("did you miss me", "I'm
# back") - conversational-issues report P1 ("Improve Relational/
# Emotional Conversation Understanding"). Deliberately stays general
# ("glad you're back"/"missed having you around") rather than claiming
# any specific remembered event or duration - Mochi's only actual signal
# here is the coarse interaction-count tier, not a real memory of the
# absence itself (acceptance criterion: "Mochi does not claim memories/
# events that do not exist"). NEW isn't listed here for the same reason
# as _FAMILIAR_GREETINGS above - intent.py's own default response
# already reads right for someone Mochi barely knows yet.
_FAMILIAR_RELATIONAL_RESPONSES = {
    relationship.GETTING_TO_KNOW: "Of course! Glad to have you back.",
    relationship.FAMILIAR: "Always~ it's just not the same around here without you!",
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
    # Deterministic conversational-reference memory (security review I1/I3
    # - see app/ai/conversation_state.py) - what "it"/"that"/"the second
    # one" should resolve to on the *next* handle_message() call. Owned
    # and threaded by the caller exactly like `pending_action` above
    # (app/ui/chat_window.py's `_conversation_state`). None means "nothing
    # in particular to remember right now."
    conversation_state: Optional[dict] = None


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

    plural = "task" if len(tasks) == 1 else "tasks"
    return ChatReaction(
        text=f"You've got {len(tasks)} open {plural}:\n{_format_bullet_list([_label(t) for t in tasks])}",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
        # Remembered in the exact order shown, so a follow-up "the second
        # one" means the second bullet actually displayed, not database
        # insertion order (see app/ai/conversation_state.py).
        conversation_state=convo.remember_candidates("task", [(t.id, t.title) for t in tasks]),
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
    shown_labels = [f"{r.title} at {r.due_at:%I:%M %p}" for r in reminders]
    plural = "reminder" if len(reminders) == 1 else "reminders"
    return ChatReaction(
        text=f"You've got {len(reminders)} {plural}:\n{_format_bullet_list(shown_labels)}",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
        conversation_state=convo.remember_candidates(
            "reminder", [(r.id, r.title) for r in reminders]
        ),
    )


_COMPLETE_FNS = {
    "task": task_manager.complete_task,
    "reminder": reminder_manager.complete_reminder,
}
_CANCEL_FNS = {
    "task": task_manager.cancel_task,
    "reminder": reminder_manager.cancel_reminder,
    "timer": timer_manager.cancel_timer,
}
_LABEL_ATTR = {"task": "title", "reminder": "title", "timer": "label"}


@dataclass
class ActionResult:
    """Structured record of what an action against one or more entities
    actually did (conversational-issues report P2: "Add Structured
    Action Execution Results") - `_multi_action_reaction()` below builds
    one of these and generates its response text FROM it, rather than
    text generation ever being free to assume every requested operation
    succeeded. `requested` is how many entities were targeted;
    `completed`/`failed` are the (kind, entity) pairs that actually
    succeeded/raised - always `completed + failed == requested` in
    count, and `success` is only ever True when nothing failed."""

    requested: int
    completed: list[tuple[str, object]]
    failed: list[tuple[str, object]]
    verb: str  # "complete" or "cancel"

    @property
    def success(self) -> bool:
        return not self.failed and bool(self.completed)


def _run_multi_action(items: list[tuple[str, object]], verb: str) -> ActionResult:
    """Executes `verb` ("complete"/"cancel") against every (kind, item)
    pair in `items` - already-resolved, real, currently-valid entities
    (see app/ai/conversation_state.py's resolve_selection()/
    resolve_selection_typed()) - and returns exactly what happened as an
    ActionResult, never assuming success. Each item's own `kind` picks
    the right manager function, so a single call can span mixed types
    (e.g. completing a task and a reminder chosen from the same
    cross-type "which one?" list)."""
    action_fns = _COMPLETE_FNS if verb == "complete" else _CANCEL_FNS
    completed: list[tuple[str, object]] = []
    failed: list[tuple[str, object]] = []
    for kind, item in items:
        try:
            action_fns[kind](item.id)
            completed.append((kind, item))
        except MochiError:
            failed.append((kind, item))
    return ActionResult(requested=len(items), completed=completed, failed=failed, verb=verb)


def _multi_action_reaction(items: list[tuple[str, object]], verb: str) -> "ChatReaction":
    """Runs `_run_multi_action()` and turns the resulting ActionResult
    into a ChatReaction - the response text is always generated from
    that result, so a partial failure is reported as a partial failure,
    never claimed as a full success (report P0/P2)."""
    result = _run_multi_action(items, verb)

    def _label(kind: str, item) -> str:
        title = getattr(item, _LABEL_ATTR[kind])
        return f"{kind}: {title}" if mixed else title

    if not result.completed:
        return ChatReaction(
            text="Something went wrong - I couldn't update any of those.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    mixed = len({kind for kind, _ in items}) > 1
    past = "Completed" if verb == "complete" else "Cancelled"
    lines = _format_bullet_list([_label(kind, item) for kind, item in result.completed])
    if result.failed:
        text = (
            f"{past} {len(result.completed)} of {result.requested}:\n{lines}\n"
            "Couldn't update the rest - you may want to try those again."
        )
    else:
        noun = "item" if len(result.completed) == 1 else "items"
        text = f"Done! {past} {len(result.completed)} {noun}:\n{lines}"

    last_kind, last_item = result.completed[-1]
    return ChatReaction(
        text=text,
        emotion=Emotion.HAPPY if result.success and verb == "complete" else Emotion.NEUTRAL,
        animation=CharacterState.HAPPY if result.success and verb == "complete" else CharacterState.IDLE,
        sound="chirp" if result.success and verb == "complete" else None,
        conversation_state=convo.remember_entity(
            last_kind, last_item.id, getattr(last_item, _LABEL_ATTR[last_kind])
        ),
    )


def _format_bullet_list(labels: list[str], max_shown: int = 6) -> str:
    """Render a handful of items as a short, line-broken bullet list
    instead of one long "; "-joined sentence (spec follow-up: "give
    little format to answer... make chat a little [nicer], it is
    inconvenient look and all same") - a QLabel in plain-text mode still
    breaks on "\\n", so no rich text is needed for this to render as
    actual separate lines in the chat bubble (see app/ui/chat_window.py).
    """
    shown = labels[:max_shown]
    lines = "\n".join(f"• {label}" for label in shown)
    if len(labels) > max_shown:
        lines += f"\n… +{len(labels) - max_shown} more"
    return lines


def _numbered_list(labels: list[str], max_shown: int = 6) -> str:
    """Same idea as _format_bullet_list() above, but numbered - used for
    ambiguous-match clarifications (conversational-issues report P1:
    "Improve Ambiguous Action Responses") so a follow-up message can
    reference "the second one" naturally, matching the number actually
    shown."""
    shown = labels[:max_shown]
    lines = "\n".join(f"{i}. {label}" for i, label in enumerate(shown, start=1))
    if len(labels) > max_shown:
        lines += f"\n… +{len(labels) - max_shown} more"
    return lines


def _clarify_reaction(intro: str, entity_kind: str, items: list, label_attr: str, id_attr: str = "id") -> "ChatReaction":
    """Builds a "which one do you mean?" clarification as a numbered
    list rather than a raw "; "-joined dump, and remembers `items` as
    ordered candidates (see app/ai/conversation_state.py's
    remember_candidates()) so a follow-up "the second one" - or "all of
    them"/"the first two", via resolve_selection() - resolves against
    exactly this list. Never a guess: the caller only reaches this
    function when it genuinely can't tell which one entity was meant."""
    labels = [getattr(i, label_attr) for i in items]
    text = f"{intro}\n{_numbered_list(labels)}\n\nWhich one should I use?"
    return ChatReaction(
        text=text,
        emotion=Emotion.CONFUSED,
        animation=CharacterState.CONFUSED,
        conversation_state=convo.remember_candidates(
            entity_kind, [(getattr(i, id_attr), getattr(i, label_attr)) for i in items]
        ),
    )


def _clarify_typed_reaction(intro: str, combined: list[tuple[str, object]]) -> "ChatReaction":
    """Cross-type counterpart to _clarify_reaction() above, for
    _complete_ambiguous_reaction/_cancel_ambiguous_reaction where
    candidates span more than one entity type at once - see
    app/ai/conversation_state.py's remember_mixed_candidates()."""

    def _label(kind: str, item) -> str:
        title = item.label if kind == "timer" else item.title
        return f"{kind}: {title}"

    labels = [_label(kind, item) for kind, item in combined]
    text = f"{intro}\n{_numbered_list(labels)}\n\nWhich one should I use?"
    return ChatReaction(
        text=text,
        emotion=Emotion.CONFUSED,
        animation=CharacterState.CONFUSED,
        conversation_state=convo.remember_mixed_candidates(
            [(kind, item.id, _label(kind, item)) for kind, item in combined]
        ),
    )


def _list_timers_reaction() -> "ChatReaction":
    timer_manager.ensure_ready()
    timers = timer_manager.list_active_timers()
    if not timers:
        return ChatReaction(
            text="No timers running right now!",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
        )

    def _label(t) -> str:
        minutes, seconds = divmod(int(t.seconds_remaining), 60)
        remaining = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        return f"{t.label} - {remaining} left"

    plural = "timer" if len(timers) == 1 else "timers"
    return ChatReaction(
        text=f"You've got {len(timers)} {plural} running:\n{_format_bullet_list([_label(t) for t in timers])}",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
        conversation_state=convo.remember_candidates(
            "timer", [(t.id, t.label) for t in timers]
        ),
    )


# --- Glossary-driven "what have I finished" query (spec follow-up: "when
# ask remain task work reminder timer it should fetch from main table not
# done table" - this is the read side for the archive/done tables, see
# app/ai/db_glossary.py for the synonym matching and why the query itself
# is built deterministically rather than left to the LLM). -------------
_DONE_LIST_FNS = {
    "tasks": lambda: task_manager.list_archived_tasks(),
    "reminders": lambda: reminder_manager.list_archived_reminders(),
    "timers": lambda: timer_manager.list_archived_timers(),
}
_ALL_LIST_FNS = {
    "tasks": lambda: task_manager.list_tasks(),
    "reminders": lambda: reminder_manager.list_reminders(),
    "timers": lambda: timer_manager.list_active_timers() + timer_manager.list_archived_timers(),
}
_ENTITY_LABEL_ATTR = {"tasks": "title", "reminders": "title", "timers": "label"}
_ENTITY_NOUN = {"tasks": "task", "reminders": "reminder", "timers": "timer"}


def _query_done_reaction(tool_args: dict, _state: Optional[dict] = None) -> "ChatReaction":
    text = tool_args.get("query", "")
    task_manager.ensure_ready()
    reminder_manager.ensure_ready()
    timer_manager.ensure_ready()

    plan: Optional[QueryPlan] = build_plan(text)
    if plan is None:
        # Shouldn't happen (LIST_DONE_TRIGGER already requires an entity
        # word), but never crash chat over a glossary miss - fall back to
        # "done tasks" as a sane default.
        plan = QueryPlan(entity="tasks", status="done")
    # LIST_DONE_TRIGGER always implies a finished-state question, so make
    # sure "active" (the glossary's default when no status word is found)
    # never accidentally wins here.
    status = plan.status if plan.status != "active" else "done"

    fn_map = _ALL_LIST_FNS if status == "all" else _DONE_LIST_FNS
    items = fn_map[plan.entity]()
    if status == "cancelled":
        items = [i for i in items if getattr(i, "status", "") == "cancelled"]
    elif status == "done":
        items = [i for i in items if getattr(i, "status", "") in ("done", "completed")]

    label_attr = _ENTITY_LABEL_ATTR[plan.entity]
    noun = _ENTITY_NOUN[plan.entity]
    labels = [getattr(i, label_attr) for i in items]

    if not items:
        facts = f"{plan.entity}: 0 items with status={status}."
        deterministic_text = f"Nothing there - no {status} {noun}s yet!"
    else:
        facts = f"{plan.entity} ({status}, {len(items)} total): " + "; ".join(labels[:10])
        plural = noun if len(items) == 1 else f"{noun}s"
        deterministic_text = (
            f"You've got {len(items)} {status} {plural}:\n{_format_bullet_list(labels)}"
        )

    try:
        phrased = phrase_data_answer(text or f"what {status} {noun}s do I have", facts)
        response_text = phrased["response"]
        emotion, animation = _emotion_and_animation(phrased["emotion"])
    except LLMUnavailable:
        # No local LLM available - the deterministic, already-correct
        # text above is the full answer either way, just less flowery.
        response_text = deterministic_text
        emotion, animation = Emotion.CURIOUS, CharacterState.THINKING

    return ChatReaction(text=response_text, emotion=emotion, animation=animation)


class Ambiguous:
    """Sentinel returned by `_fuzzy_find` when two or more items tie for
    the best match score. Previously `_fuzzy_find` silently kept "the
    first item encountered" on a tie, which meant a message like "mark my
    task call as done" could complete/cancel the wrong record when the
    user had both "Call Mom" and "Call Dad" open (each word-overlap score
    of 1). Callers must check for this and ask the user which one they
    meant instead of ever acting on a guess - see spec section 41 and the
    README's promise to ask when more than one thing matches."""

    __slots__ = ("candidates",)

    def __init__(self, candidates: list):
        self.candidates = candidates


def _fuzzy_find(query: str, items: list, title_attr: str = "title"):
    """Best-matching item for `query` among `items` by word overlap, or
    None if nothing shares a word with it, or an `Ambiguous` sentinel if
    two or more items tie for the best score. Simple, deterministic
    scoring - good enough for resolving "mark X as done" against
    someone's own short task/reminder list; not meant to be a real search
    engine, and deliberately never guesses when the overlap is zero, or
    when multiple items are equally good, rather than silently acting on
    the wrong thing."""
    if not items or not query:
        return None
    query_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not query_words:
        return None
    query_lower = query.lower()
    scored = []
    for item in items:
        title_lower = getattr(item, title_attr).lower()
        title_words = set(re.findall(r"[a-z0-9]+", title_lower))
        score = len(query_words & title_words)
        if query_lower in title_lower or title_lower in query_lower:
            score += 1
        if score > 0:
            scored.append((score, item))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    top = [item for score, item in scored if score == best_score]
    if len(top) > 1:
        return Ambiguous(top)
    return top[0]


def _complete_task_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    task_manager.ensure_ready()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    if not open_tasks:
        return ChatReaction(
            text="You don't have any open tasks to mark done!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    # Multi-target reference ("three of them", "all of them", "the first
    # three") checked before fuzzy title matching - conversational-issues
    # report P0 (see app/ai/conversation_state.py's resolve_selection()).
    selection = convo.resolve_selection(query, state, open_tasks)
    if selection is not None:
        return _multi_action_reaction([("task", i) for i in selection], "complete")
    match = _fuzzy_find(query, open_tasks)
    if isinstance(match, Ambiguous):
        return _clarify_reaction(
            "I've got a few tasks that could match:", "task", match.candidates, "title"
        )
    # No title-word overlap at all (e.g. "mark it as done") - try
    # resolving "it"/"that"/"the second one" against what was last
    # created/listed/discussed before falling back to the single-open-item
    # heuristic below (security review I1/I3).
    if match is None:
        match = convo.resolve(query, state, open_tasks)
    if match is None and not query and len(open_tasks) == 1:
        match = open_tasks[0]
    if match is None:
        return _clarify_reaction("Not sure which task you mean:", "task", open_tasks, "title")
    task_manager.complete_task(match.id)
    return ChatReaction(
        text=f'Done! Marked "{match.title}" as complete.',
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        sound="chirp",
        conversation_state=convo.remember_entity("task", match.id, match.title),
    )


def _cancel_task_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    task_manager.ensure_ready()
    open_tasks = task_manager.list_tasks(status=task_manager.TaskStatus.OPEN)
    if not open_tasks:
        return ChatReaction(
            text="There's nothing on your task list to cancel!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    selection = convo.resolve_selection(query, state, open_tasks)
    if selection is not None:
        return _multi_action_reaction([("task", i) for i in selection], "cancel")
    match = _fuzzy_find(query, open_tasks)
    if isinstance(match, Ambiguous):
        return _clarify_reaction(
            "I've got a few tasks that could match:", "task", match.candidates, "title"
        )
    if match is None:
        match = convo.resolve(query, state, open_tasks)
    if match is None and not query and len(open_tasks) == 1:
        match = open_tasks[0]
    if match is None:
        return _clarify_reaction("Not sure which task you mean:", "task", open_tasks, "title")
    task_manager.cancel_task(match.id)
    return ChatReaction(
        text=f'Okay, cancelled "{match.title}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
        conversation_state=convo.remember_entity("task", match.id, match.title),
    )


def _complete_reminder_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    reminder_manager.ensure_ready()
    pending = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    if not pending:
        return ChatReaction(
            text="You don't have any pending reminders to mark done!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    selection = convo.resolve_selection(query, state, pending)
    if selection is not None:
        return _multi_action_reaction([("reminder", i) for i in selection], "complete")
    match = _fuzzy_find(query, pending)
    if isinstance(match, Ambiguous):
        return _clarify_reaction(
            "I've got a few reminders that could match:", "reminder", match.candidates, "title"
        )
    if match is None:
        match = convo.resolve(query, state, pending)
    if match is None and not query and len(pending) == 1:
        match = pending[0]
    if match is None:
        return _clarify_reaction("Not sure which reminder you mean:", "reminder", pending, "title")
    reminder_manager.complete_reminder(match.id)
    return ChatReaction(
        text=f'Done! Marked "{match.title}" as complete.',
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        sound="chirp",
        conversation_state=convo.remember_entity("reminder", match.id, match.title),
    )


def _cancel_reminder_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    reminder_manager.ensure_ready()
    pending = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    if not pending:
        return ChatReaction(
            text="You don't have any pending reminders to cancel!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    selection = convo.resolve_selection(query, state, pending)
    if selection is not None:
        return _multi_action_reaction([("reminder", i) for i in selection], "cancel")
    match = _fuzzy_find(query, pending)
    if isinstance(match, Ambiguous):
        return _clarify_reaction(
            "I've got a few reminders that could match:", "reminder", match.candidates, "title"
        )
    if match is None:
        match = convo.resolve(query, state, pending)
    if match is None and not query and len(pending) == 1:
        match = pending[0]
    if match is None:
        return _clarify_reaction("Not sure which reminder you mean:", "reminder", pending, "title")
    reminder_manager.cancel_reminder(match.id)
    return ChatReaction(
        text=f'Okay, cancelled "{match.title}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
        conversation_state=convo.remember_entity("reminder", match.id, match.title),
    )


def _cancel_timer_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    timer_manager.ensure_ready()
    active = timer_manager.list_active_timers()
    if not active:
        return ChatReaction(
            text="You don't have any timers running!",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    query = tool_args.get("query", "")
    selection = convo.resolve_selection(query, state, active)
    if selection is not None:
        return _multi_action_reaction([("timer", i) for i in selection], "cancel")
    match = _fuzzy_find(query, active, title_attr="label")
    if isinstance(match, Ambiguous):
        return _clarify_reaction(
            "I've got a few timers that could match:", "timer", match.candidates, "label"
        )
    if match is None:
        match = convo.resolve(query, state, active)
    if match is None and not query and len(active) == 1:
        match = active[0]
    if match is None:
        return _clarify_reaction("Not sure which timer you mean:", "timer", active, "label")
    timer_manager.cancel_timer(match.id)
    return ChatReaction(
        text=f'Okay, stopped "{match.label}".',
        emotion=Emotion.NEUTRAL,
        animation=CharacterState.IDLE,
        conversation_state=convo.remember_entity("timer", match.id, match.label),
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
def _check_on_reaction(tool_args: dict, _state: Optional[dict] = None) -> "ChatReaction":
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

    if isinstance(task_match, Ambiguous) or isinstance(reminder_match, Ambiguous):
        candidates = []
        if isinstance(reminder_match, Ambiguous):
            candidates += [r.title for r in reminder_match.candidates]
        elif reminder_match is not None:
            candidates.append(reminder_match.title)
        if isinstance(task_match, Ambiguous):
            candidates += [t.title for t in task_match.candidates]
        elif task_match is not None:
            candidates.append(task_match.title)
        return ChatReaction(
            text=f"I've got a few things that could match:\n{_numbered_list(candidates)}",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
        )

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
            conversation_state=convo.remember_entity("reminder", reminder_match.id, reminder_match.title),
        )
    if task_match is not None:
        due_note = (
            f" (due {task_match.due_at:%m-%d %I:%M %p})" if task_match.due_at else ""
        )
        return ChatReaction(
            text=f'Yep - "{task_match.title}" is still open on your task list{due_note}.',
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            conversation_state=convo.remember_entity("task", task_match.id, task_match.title),
        )
    return ChatReaction(
        text=f"I don't have anything like \"{query}\" saved as a task or reminder.",
        emotion=Emotion.CONFUSED,
        animation=CharacterState.CONFUSED,
    )


def _complete_ambiguous_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    """Handles "mark it as done" / "that's done" / "I finished it" - i.e.
    the same completion request as complete_task/complete_reminder, but
    phrased without the literal word "task"/"reminder" so TASK_DONE_TRIGGER/
    REMINDER_DONE_TRIGGER never match it (bug report: this used to fall to
    the open-ended LLM, which would say something like "Okay, I'll take
    care of it" without actually marking anything done anywhere - another
    hallucination). Resolves "it" against whatever's actually open across
    both stores; only auto-completes when that's unambiguous OR when
    `state` (app/ai/conversation_state.py) points at one specific item
    that's still genuinely open (security review I1/I3)."""
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
    query = tool_args.get("query", "it")
    if len(combined) > 1:
        # Multi-target reference ("three of them", "all of them") checked
        # first - conversational-issues report P0 (see app/ai/
        # conversation_state.py's resolve_selection_typed()).
        multi = convo.resolve_selection_typed(query, state, combined)
        if multi is not None:
            return _multi_action_reaction(multi, "complete")
        # Kind-qualified contextual reference ("that timer", "the task I
        # just added") - conversational-issues report P1 ("Expand
        # Conversational Reference Model") - checked before the generic
        # resolve_typed() below, since a named kind is a stronger signal
        # than a bare pronoun/ordinal.
        resolved = convo.resolve_contextual_kind(query, state, combined)
        if resolved is None:
            resolved = convo.resolve_typed(query, state, combined)
        if resolved is None:
            return _clarify_typed_reaction("Which one do you mean?", combined)
        kind, item = resolved
    else:
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
        conversation_state=convo.remember_entity(kind, item.id, item.title),
    )


def _cancel_ambiguous_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    """Handles "cancel it" / "delete it" / "scratch that" - the
    cancellation counterpart to _complete_ambiguous_reaction above (see
    AMBIGUOUS_CANCEL_TRIGGER in app/ai/intent.py for the full bug report).
    Resolves "it" against whatever's actually open/pending/running across
    all three stores (tasks, reminders, AND running timers - unlike
    completion, an active timer is a perfectly normal thing to want to
    cancel); only auto-cancels when that's unambiguous OR when `state`
    points at one specific item that's still actually there, and never
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

    query = tool_args.get("query", "it")
    if len(combined) > 1:
        multi = convo.resolve_selection_typed(query, state, combined)
        if multi is not None:
            return _multi_action_reaction(multi, "cancel")
        resolved = convo.resolve_contextual_kind(query, state, combined)
        if resolved is None:
            resolved = convo.resolve_typed(query, state, combined)
        if resolved is None:
            return _clarify_typed_reaction("Which one do you mean?", combined)
        kind, item = resolved
    else:
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
        conversation_state=convo.remember_entity(kind, item.id, label),
    )


def _reschedule_reference_reaction(tool_args: dict, state: Optional[dict] = None) -> "ChatReaction":
    """Handles "make it 8" / "change it to 8:30pm" - the review's
    canonical conversational-reference example ("remind me to call mom
    at 7" -> "make it 8"). "it"/"that" here is resolved deterministically
    against `state` (app/ai/conversation_state.py's remembered last
    entity), the same as the ambiguous complete/cancel handlers above -
    never guessed at, and never applied to a reminder/task that's since
    been completed/cancelled through some other path."""
    due_iso = tool_args.get("due_iso")
    try:
        due = datetime.fromisoformat(due_iso) if due_iso else None
    except ValueError:
        due = None
    if due is None:
        return ChatReaction(
            text='Change it to when? Try a time like "8pm".',
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    entity_type = state.get("entity_type") if state else None
    entity_id = state.get("entity_id") if state else None
    if entity_type not in ("reminder", "task") or entity_id is None:
        return ChatReaction(
            text="Change what, exactly? I don't have a specific reminder or task in mind right now.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )

    if entity_type == "reminder":
        reminder_manager.ensure_ready()
        reminder = reminder_manager.get_reminder(entity_id)
        if reminder is None:
            return ChatReaction(
                text="That reminder isn't around anymore - I can't reschedule it.",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
            )
        reminder_manager.update_reminder(entity_id, due_at=due)
        return ChatReaction(
            text=f'Got it - "{reminder.title}" is now set for {due:%I:%M %p}.',
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
            conversation_state=convo.remember_entity("reminder", entity_id, reminder.title),
        )

    task_manager.ensure_ready()
    task = task_manager.get_task(entity_id)
    if task is None:
        return ChatReaction(
            text="That task isn't around anymore - I can't reschedule it.",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
        )
    task_manager.set_due_date(entity_id, due)
    return ChatReaction(
        text=f'Got it - "{task.title}" is now due {due:%m-%d %I:%M %p}.',
        emotion=Emotion.HAPPY,
        animation=CharacterState.HAPPY,
        sound="chirp",
        conversation_state=convo.remember_entity("task", entity_id, task.title),
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
    "list_timers": _list_timers_reaction,
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
    "query_done": _query_done_reaction,
    "reschedule_reference": _reschedule_reference_reaction,
}


# Human-friendly rephrasing hints for the hybrid semantic layer's
# medium-confidence band (see semantic_intent.CONFIDENCE_LOW/CONFIDENCE_ACT
# and _semantic_clarify_intent below) - shown when the model has a guess
# but isn't confident enough to just act on it.
_SEMANTIC_CLARIFY_HINTS = {
    "create_reminder": ('set a reminder - try "remind me to ... at ..."'),
    "create_task": 'add a task - try "add task ..."',
    "start_timer": 'start a timer - try "timer for 10 minutes"',
    "list_reminders": 'check your reminders - try "what reminders do I have"',
    "list_tasks": 'check your tasks - try "what tasks do I have"',
    "list_timers": 'check your timers - try "what timers are running"',
    "complete_ambiguous": 'mark something as done - try "mark my task ... as done"',
    "cancel_ambiguous": 'cancel something - try "cancel my reminder ..."',
}


def _semantic_clarify_intent(guessed_name: str) -> DetectedIntent:
    """Medium-confidence semantic guess (roadmap section 6: 50-75% ->
    'soft suggestion / ask') - Mochi has a plausible read on what was
    meant but isn't sure enough to actually create/change anything, so it
    asks instead of guessing. Named anything other than "unknown" so the
    open-ended LLM fallback below in handle_message() doesn't overwrite
    this response - see that block's docstring note."""
    hint = _SEMANTIC_CLARIFY_HINTS.get(guessed_name, "do that")
    return DetectedIntent(
        name="semantic_clarify",
        emotion=Emotion.CONFUSED,
        animation=CharacterState.CONFUSED,
        response=f"Hmm, sounds like you might want to {hint} - mind rephrasing it a little more directly so I get it right?",
    )


def handle_message(
    text: str,
    history: Optional[list[tuple[str, str]]] = None,
    pending_action: Optional[dict] = None,
    conversation_state: Optional[dict] = None,
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
    resolved here before anything else runs. Any other message expires
    it immediately (see the block below) rather than carrying it forward,
    so a stale proposal can never be confirmed by an unrelated later
    "yes".

    `conversation_state` (security review I1/I3, app/ai/conversation_state.py)
    is Mochi's deterministic memory of the single most recent task/
    reminder/timer that was created, resolved, or listed - what "it"/
    "that"/"the second one" should resolve to on THIS call. Same
    ownership convention as `pending_action`: the caller reads back
    whatever the previous ChatReaction.conversation_state was and passes
    it straight in. Unlike `pending_action`, this is harmless to carry
    forward across unrelated turns (it's just a hint for reference
    resolution, never itself a write) - it's only ever replaced when a
    handler below has something fresher to remember.
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
        # Anything else is not a clear yes/no, so the proposal is treated
        # as abandoned rather than carried forward (security review:
        # "pending calendar proposal can survive unrelated open-ended
        # conversation" - a stray later "yes" must not be able to confirm
        # a proposal the user has moved on from, whether the intervening
        # message is a deterministic list/action, the semantic
        # clarification path, or plain small talk/LLM chat).
        #
        # Reassigning the local `pending_action` to None HERE, once, is
        # deliberate: every return point below this line already reads
        # from this same local variable rather than re-deciding for
        # itself, so a single decision here keeps every branch correct by
        # construction instead of relying on each handler to remember to
        # expire it individually (that's exactly how the previous fix
        # regressed - see the removed `%r`-logging block that used to sit
        # in the _ACTION_HANDLERS branch below).
        if pending_action is not None:
            logger.info(
                "Expiring stale pending_action kind=%s (not a yes/no)",
                pending_action.get("kind"),
            )
        pending_action = None

    intent: DetectedIntent = detect_intent(text)

    # --- Hybrid semantic fallback (understand intent by MEANING, not
    # just keywords) --------------------------------------------------
    # The keyword/regex matcher above is fast and fully deterministic,
    # but it only ever recognizes a message that happens to contain one
    # of its literal trigger phrases - a paraphrase like "don't let this
    # slip my mind, dentist thing at 4" means create_reminder just as
    # clearly as "remind me..." does, but shares none of its trigger
    # words, so it falls through to "unknown". This block is only ever
    # reached when the keyword pass found NOTHING at all - it never
    # overrides an actual keyword match, so it can only recognize MORE
    # messages, never change how an already-recognized one is handled.
    # See app/ai/semantic_intent.py for the model call + confidence bands
    # and app/ai/intent.py's build_semantic_intent() for why the actual
    # entity extraction (time/title/duration) still goes through the
    # exact same deterministic regex parsing as the keyword path, never
    # the model's own judgement - it only ever decides WHICH bucket a
    # message belongs to.
    if intent.name == "unknown":
        try:
            guess = semantic_intent.classify(text)
        except semantic_intent.SemanticUnavailable as exc:
            logger.info("Semantic intent classification unavailable: %s", exc)
            guess = None

        if guess is not None and guess.intent != "small_talk":
            if guess.confidence >= semantic_intent.CONFIDENCE_ACT:
                built = build_semantic_intent(guess.intent, text)
                if built is not None:
                    # No raw message text here (security review S1) - the
                    # logger's own policy in app/core/logger.py says user
                    # content shouldn't be logged, and this text can be a
                    # private reminder/task/appointment. Intent name and
                    # confidence are enough to debug misclassification.
                    logger.info(
                        "Semantic intent=%s confidence=%.2f (acting)",
                        guess.intent, guess.confidence,
                    )
                    intent = built
            elif guess.confidence >= semantic_intent.CONFIDENCE_LOW:
                logger.info(
                    "Semantic intent=%s confidence=%.2f (asking, not acting)",
                    guess.intent, guess.confidence,
                )
                intent = _semantic_clarify_intent(guess.intent)
            # else: below CONFIDENCE_LOW - stays "unknown", falls through
            # to the open-ended LLM chat reply exactly as before.

    # Observability (bug report: reminders/timers/tasks "not getting set"
    # with nothing in the logs to say why): log what every message was
    # actually classified as *before* any handler runs, so a message that
    # silently fails to match create_reminder/start_timer/create_task -
    # e.g. because the phrasing didn't match app/ai/intent.py's regex
    # triggers (and the semantic fallback above also had nothing) - is
    # visible in the log as "unknown"/some other intent instead of
    # leaving no trace at all.
    #
    # Deliberately NOT logging the raw message or the full tool_args dict
    # (security review S1) - both can contain private reminder/task
    # titles, appointment names, or other personal content, and
    # app/core/logger.py's own stated policy is that Mochi's logs must
    # not carry unnecessary sensitive user content. The intent/tool name
    # alone is enough to see *whether* a message was classified and
    # *which* handler it will hit, without persisting what it actually said.
    logger.info("Message classified: intent=%s tool=%s", intent.name, intent.tool)

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
        # `pending_action` is guaranteed None here (expired above unless
        # this message was a literal yes/no, which already returned) -
        # nothing further to carry forward or log.
        reaction.pending_action = None
        # Carry the old conversation_state forward only if this handler
        # didn't set a fresher one itself (list handlers always do, on
        # success - see _list_tasks_reaction etc.); a non-fatal DB-read
        # failure above falls back to the caller's existing memory rather
        # than wiping it.
        if reaction.conversation_state is None:
            reaction.conversation_state = conversation_state
        return reaction

    if intent.name in _ACTION_HANDLERS:
        try:
            reaction = _ACTION_HANDLERS[intent.name](intent.tool_args, conversation_state)
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
        # `pending_action` is guaranteed None here - see the comment
        # above the docstring's `pending_action` paragraph and the
        # expiration block near the top of this function.
        reaction.pending_action = None
        if reaction.conversation_state is None:
            reaction.conversation_state = conversation_state
        return reaction

    if intent.name in _PROPOSAL_HANDLERS:
        try:
            # Proposal handlers always set their own fresh pending_action
            # on the returned reaction - this deliberately replaces
            # whatever was passed in, since starting a new write request
            # supersedes an old unconfirmed one rather than stacking them.
            # A calendar proposal doesn't touch tasks/reminders/timers, so
            # the old conversation_state (if any) is simply carried
            # forward unchanged.
            reaction = _PROPOSAL_HANDLERS[intent.name](intent.tool_args)
            if reaction.conversation_state is None:
                reaction.conversation_state = conversation_state
            return reaction
        except Exception:  # noqa: BLE001 - never let a bad proposal crash chat
            logger.exception("Failed to build proposal for intent '%s'", intent.name)
            return ChatReaction(
                text="Oops, something went wrong setting that up.",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                # `pending_action` is guaranteed None here too (see the
                # expiration block near the top of this function) - kept
                # explicit rather than omitted so this doesn't look like
                # an oversight to a future reader.
                pending_action=pending_action,
                conversation_state=conversation_state,
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

    if intent.name == "relational" and familiarity in _FAMILIAR_RELATIONAL_RESPONSES:
        response = _FAMILIAR_RELATIONAL_RESPONSES[familiarity]

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
                # Log success/failure only, not the result dict - it can
                # contain a private task/reminder/appointment title
                # (security review S1's same reasoning applied here too).
                logger.info("Tool '%s' succeeded", intent.tool)
            except MochiError as exc:
                logger.info("Tool '%s' rejected: %s", intent.tool, exc)
                return ChatReaction(
                    text=f"Hmm, I couldn't do that: {exc}",
                    emotion=Emotion.CONFUSED,
                    animation=CharacterState.CONFUSED,
                    pending_action=pending_action,
                    conversation_state=conversation_state,
                )
            except Exception:  # noqa: BLE001 - never let a bad tool crash chat
                logger.exception("Unexpected error running tool '%s'", intent.tool)
                return ChatReaction(
                    text="Oops, something went wrong on my end.",
                    emotion=Emotion.CONFUSED,
                    animation=CharacterState.CONFUSED,
                    pending_action=pending_action,
                    conversation_state=conversation_state,
                )
            # Remember what was just created, so an immediate follow-up
            # ("actually delete it" / "make it 8") can resolve "it"
            # without repeating the title (security review I1/I3).
            new_entity_kind = _CREATE_TOOL_ENTITY_KINDS.get(intent.tool)
            if new_entity_kind is not None and isinstance(result, dict) and "id" in result:
                title = result.get("title") or result.get("label") or ""
                conversation_state = convo.remember_entity(new_entity_kind, result["id"], title)

    return ChatReaction(
        text=response,
        emotion=emotion,
        animation=animation,
        sound=sound,
        pending_action=pending_action,
        conversation_state=conversation_state,
    )
