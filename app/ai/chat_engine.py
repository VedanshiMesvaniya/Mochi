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

from dataclasses import dataclass
from typing import Optional

from app.ai.intent import DetectedIntent, detect_intent
from app.ai.llm import LLMUnavailable, ask as ask_llm
from app.character.state_machine import EMOTION_PROFILE, CharacterState, Emotion
from app.core.config import settings
from app.core.exceptions import MochiError
from app.core.logger import get_logger
from app.humor.trend_fetcher import pick_one_trend
from app.memory import relationship
from app.reminders import manager as reminder_manager
from app.tasks import manager as task_manager
from app.timers import manager as timer_manager
from app.tools import reminder_tools, task_tools, timer_tools

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
    shown = "; ".join(t.title for t in tasks[:5])
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
    shown = "; ".join(f"{r.title} at {r.due_at:%H:%M}" for r in reminders[:5])
    more = f" (+{len(reminders) - 5} more)" if len(reminders) > 5 else ""
    plural = "reminder" if len(reminders) == 1 else "reminders"
    return ChatReaction(
        text=f"You've got {len(reminders)} {plural}: {shown}{more}.",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
    )


# Read-only DB queries (spec: "it can not read db, make it read db so it
# can answer") - handled entirely separately from _TOOL_MODULES below.
# Those are fire-and-forget writes whose response text is authored ahead
# of time in intent.py; these need to read the DB *first* and build the
# reply from whatever's actually in it, so a small local LLM never gets a
# chance to hallucinate an answer to a factual "what's in my database"
# question (see the list_tasks/list_reminders DetectedIntents).
_LIST_HANDLERS = {
    "list_tasks": _list_tasks_reaction,
    "list_reminders": _list_reminders_reaction,
}


def handle_message(
    text: str, history: Optional[list[tuple[str, str]]] = None
) -> ChatReaction:
    """Process one chat message end-to-end and return how Mochi should react.

    `history` (spec: "for chat it should store the current chat memory...
    remember whole chat [until closed]") is the calling chat window's own
    session-so-far as (role, text) pairs, oldest first - see
    app/ui/chat_window.py, which owns and clears it. It's only actually
    used for the open-ended LLM fallback below; deterministic intents
    (reminders/tasks/etc.) don't need conversational context to act
    correctly on a single, self-contained command.
    """
    intent: DetectedIntent = detect_intent(text)

    if intent.name in _LIST_HANDLERS:
        try:
            return _LIST_HANDLERS[intent.name]()
        except Exception:  # noqa: BLE001 - never let a bad DB read crash chat
            logger.exception("Failed to read DB for intent '%s'", intent.name)
            return ChatReaction(
                text="Hmm, I couldn't check that just now.",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
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
            # pick_one_trend() is a cheap cache read (near-instant no-op
            # unless settings.trend_awareness_enabled is on and something
            # is already cached) - never fetches over the network here,
            # only reads whatever the background job already cached. See
            # app/humor/trend_fetcher.py.
            llm_reply = ask_llm(
                text, familiarity=familiarity, history=history, trend_topic=pick_one_trend()
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
                tool_fn(**intent.tool_args)
            except MochiError as exc:
                logger.info("Tool '%s' rejected: %s", intent.tool, exc)
                return ChatReaction(
                    text=f"Hmm, I couldn't do that: {exc}",
                    emotion=Emotion.CONFUSED,
                    animation=CharacterState.CONFUSED,
                )
            except Exception:  # noqa: BLE001 - never let a bad tool crash chat
                logger.exception("Unexpected error running tool '%s'", intent.tool)
                return ChatReaction(
                    text="Oops, something went wrong on my end.",
                    emotion=Emotion.CONFUSED,
                    animation=CharacterState.CONFUSED,
                )

    return ChatReaction(text=response, emotion=emotion, animation=animation, sound=sound)
