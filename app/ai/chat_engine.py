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

from app.ai.intent import DetectedIntent, detect_intent
from app.ai.llm import LLMUnavailable, ask as ask_llm
from app.character.state_machine import EMOTION_PROFILE, CharacterState, Emotion
from app.core.exceptions import MochiError
from app.core.logger import get_logger
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


def handle_message(text: str) -> ChatReaction:
    """Process one chat message end-to-end and return how Mochi should react."""
    intent: DetectedIntent = detect_intent(text)
    response = intent.response
    emotion = intent.emotion
    animation = intent.animation
    sound = intent.sound

    # The rule-based detector above is intentionally deterministic for
    # actionable things (reminders/timers/tasks - spec section 41, these
    # must never be left to an LLM's judgement). But its catch-all for
    # everything else it doesn't recognize is a single canned line, which
    # is the actual "chat can't answer anything" gap. For that bucket
    # only, try a real local LLM reply and fall back to the canned line
    # if one isn't available (see app/ai/llm.py for why this is safe).
    if intent.name == "unknown":
        try:
            llm_reply = ask_llm(text)
            response = llm_reply["response"]
            emotion, animation = _emotion_and_animation(llm_reply["emotion"])
            sound = EMOTION_PROFILE.get(emotion, {}).get("sound")
        except LLMUnavailable as exc:
            logger.info("Local LLM unavailable, using rule-based fallback: %s", exc)

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
