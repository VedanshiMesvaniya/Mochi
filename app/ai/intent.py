"""
Local, rule-based intent + emotion detection for Mochi's chat window.

Per spec sections 6-9: the LLM (when wired up in a later phase) should only
ever *propose* structured output like

    {"intent": "create_reminder", "emotion": "happy", ...}

and Python validates/executes it. This module provides that same structured
contract today using deterministic keyword/pattern matching instead of an
LLM call, so:

  * chat works immediately with zero setup (no Ollama install/model pull
    required) - this is what was actually broken before (there was no
    app/ai/ module and no chat window at all)
  * it never needs the network or a GPU
  * app/ai/chat_engine.py (and, later, a real LLM-backed layer) can share
    the exact same downstream tool-execution code

Returns a `DetectedIntent` with everything the character needs to react:
what Mochi should say, feel, animate, and (optionally) which local tool to
run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.character.state_machine import CharacterState, Emotion

# ---------------------------------------------------------------------------
# Structured result
# ---------------------------------------------------------------------------


@dataclass
class DetectedIntent:
    name: str
    emotion: Emotion = Emotion.NEUTRAL
    animation: CharacterState = CharacterState.TALKING
    sound: Optional[str] = None
    response: str = ""
    # Optional local tool call, e.g. ("create_reminder", {"title": ..., ...})
    tool: Optional[str] = None
    tool_args: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Keyword tables (kept small and easy to extend - spec section 8: personality)
# ---------------------------------------------------------------------------

GREETINGS = ("hi", "hello", "hey", "yo", "good morning", "good evening", "morning")
FAREWELLS = ("bye", "goodbye", "see you", "gtg", "good night", "night")
THANKS = ("thanks", "thank you", "ty", "thx")
# Compliments are split by intensity so the reaction actually varies (spec:
# "give all pending expressions" - BLUSH and HEART were previously
# unreachable from chat): a mild "cute"/"good cat" gets a shy BLUSH, while
# an outright "I love you" earns the bigger HEART-eyes reaction.
COMPLIMENTS_MILD = ("good boy", "good girl", "good cat", "cute")
COMPLIMENTS_STRONG = ("i love you", "love you")
INSULTS = ("stupid", "dumb", "shut up", "annoying", "useless")
SLEEPY_WORDS = ("i'm tired", "im tired", "so sleepy", "i am sleepy", "exhausted")
BORED_WORDS = ("i'm bored", "im bored", "bored")
HOW_ARE_YOU = ("how are you", "how r u", "how are u", "hows it going")
WHAT_DOING = ("what are you doing", "whatcha doing", "what r u doing")
MEMORY_QUERY = ("what do you remember", "remember about me")
# Direct identity questions (spec: "clarify the intent" - these were
# previously falling through to the open-ended LLM bucket, which could
# answer evasively or vaguely instead of just saying who Mochi is).
# Regex (rather than the plain phrase tuples above) so "are you cat?" and
# "are you a cat?" both match without listing every article/phrasing
# combination by hand.
IDENTITY_TRIGGER = re.compile(
    r"\bare you (a |an )?(cat|real|human|ai|a\.?i\.?|robot|bot)\b"
    r"|\b(what|who) (are|is) (you|mochi)\b",
    re.IGNORECASE,
)

def _matches_any(text: str, phrases: tuple[str, ...]) -> bool:
    """Whole-word/phrase containment check.

    Plain `substring in text` was a real bug here: the greeting keyword
    "yo" matched inside "how are **yo**u" and "**yo**u're so stupid",
    hijacking completely unrelated messages. \b-bounded regex avoids that.
    """
    for phrase in phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False


REMINDER_TRIGGER = re.compile(r"\bremind me\b", re.IGNORECASE)
TIMER_TRIGGER = re.compile(r"\b(set a timer|start a timer|timer for)\b", re.IGNORECASE)
TASK_TRIGGER = re.compile(
    r"\b(remember (that )?i need to|add (a )?task|todo:?)\b", re.IGNORECASE
)

TIME_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
TIME_IN = re.compile(
    r"\bin\s+(\d+)\s*(minute|minutes|min|mins|hour|hours|hr|hrs)\b", re.IGNORECASE
)
DURATION_ONLY = re.compile(
    r"(\d+)\s*(minute|minutes|min|mins|hour|hours|hr|hrs|second|seconds|sec|secs)"
)


def _strip_trigger(text: str, trigger: re.Pattern) -> str:
    return trigger.sub("", text, count=1).strip(" ,.!")


def _parse_absolute_time(text: str, now: datetime) -> Optional[datetime]:
    match = TIME_AT.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()

    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour < 8:
        # Bare small hours like "at 7" during evening planning -> assume PM,
        # matches how people actually talk to a desktop pet. Not perfect,
        # but transparent and easy to override by typing "7 am"/"19:00".
        hour += 12

    due = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    if "tomorrow" in text.lower():
        due += timedelta(days=1)
    return due


def _parse_relative_minutes(text: str) -> Optional[int]:
    match = TIME_IN.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    return amount * 60 if unit.startswith(("hour", "hr")) else amount


def _parse_duration_seconds(text: str) -> Optional[int]:
    match = DURATION_ONLY.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith(("hour", "hr")):
        return amount * 3600
    if unit.startswith(("min",)):
        return amount * 60
    return amount


def _title_from(text: str) -> str:
    # Drop a trailing time clause so "call mom at 7pm" -> "call mom"
    cleaned = TIME_AT.sub("", text)
    cleaned = TIME_IN.sub("", cleaned)
    cleaned = re.sub(r"\btomorrow\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^to\s+", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ,.!")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Reminder"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def detect_intent(raw_text: str, now: Optional[datetime] = None) -> DetectedIntent:
    """Classify one user chat message into a DetectedIntent.

    `now` is injectable for tests; defaults to the real current local time
    (spec section 26 - never let free-form text guess "today"/"tomorrow"
    without an actual clock).
    """

    now = now or datetime.now()
    text = raw_text.strip()
    lowered = text.lower()

    if not lowered:
        return DetectedIntent(
            name="empty",
            emotion=Emotion.CONFUSED,
            animation=CharacterState.CONFUSED,
            response="Mrrp? Say something!",
        )

    # --- Reminders -------------------------------------------------
    if REMINDER_TRIGGER.search(lowered):
        body = _strip_trigger(text, REMINDER_TRIGGER)
        due = _parse_absolute_time(body, now)
        minutes = _parse_relative_minutes(body)
        title = _title_from(body)
        if due is None and minutes is not None:
            due = now + timedelta(minutes=minutes)
        if due is None:
            return DetectedIntent(
                name="create_reminder_needs_time",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                response=(
                    f"Got it - \"{title}\" - but when? Try "
                    "\"at 7pm\" or \"in 30 minutes\"."
                ),
            )
        return DetectedIntent(
            name="create_reminder",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
            response=f"Okay! I'll remind you to {title.lower()} at {due:%H:%M}.",
            tool="create_reminder",
            tool_args={"title": title, "datetime_iso": due.isoformat()},
        )

    # --- Timers ------------------------------------------------------
    if TIMER_TRIGGER.search(lowered):
        seconds = _parse_duration_seconds(lowered)
        if not seconds:
            return DetectedIntent(
                name="create_timer_needs_duration",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                response="How long should the timer be? e.g. \"timer for 10 minutes\".",
            )
        return DetectedIntent(
            name="start_timer",
            emotion=Emotion.EXCITED,
            animation=CharacterState.EXCITED,
            sound="chirp",
            response=f"Timer started for {seconds // 60 or seconds}"
            f"{' min' if seconds >= 60 else ' sec'}! I'll let you know.",
            tool="start_timer",
            tool_args={"duration_seconds": seconds, "label": "Timer"},
        )

    # --- Tasks ---------------------------------------------------------
    if TASK_TRIGGER.search(lowered):
        body = TASK_TRIGGER.sub("", text, count=1).strip(" ,.!")
        title = _title_from(body) if body else "New task"
        return DetectedIntent(
            name="create_task",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
            response=f"Noted! I'll remember: {title.lower()}.",
            tool="create_task",
            tool_args={"title": title},
        )

    # --- Small talk ------------------------------------------------------
    if _matches_any(lowered, FAREWELLS):
        return DetectedIntent(
            name="farewell",
            emotion=Emotion.SAD,
            animation=CharacterState.SAD,
            response="Aww, okay... come back soon!",
        )
    if _matches_any(lowered, GREETINGS):
        return DetectedIntent(
            name="greeting",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
            response="Hehe, hi! What are we up to?",
        )
    if _matches_any(lowered, THANKS):
        return DetectedIntent(
            name="thanks",
            emotion=Emotion.PLAYFUL,
            animation=CharacterState.EXCITED,
            sound="purr",
            response="Purrr~ anytime!",
        )
    if _matches_any(lowered, COMPLIMENTS_STRONG):
        return DetectedIntent(
            name="compliment_strong",
            emotion=Emotion.EXCITED,
            animation=CharacterState.HEART,
            sound="chirp",
            response="Nya~! I love you too!",
        )
    if _matches_any(lowered, COMPLIMENTS_MILD):
        return DetectedIntent(
            name="compliment_mild",
            emotion=Emotion.PLAYFUL,
            animation=CharacterState.BLUSH,
            sound="purr",
            response="Hehe... say that again?",
        )
    if _matches_any(lowered, INSULTS):
        return DetectedIntent(
            name="insult",
            emotion=Emotion.ANNOYED,
            animation=CharacterState.ANGRY,
            response="Hmph. That wasn't very nice.",
        )
    if _matches_any(lowered, SLEEPY_WORDS):
        return DetectedIntent(
            name="user_sleepy",
            emotion=Emotion.SLEEPY,
            animation=CharacterState.SLEEPY,
            sound="yawn",
            response="Me too... let's take a break.",
        )
    if _matches_any(lowered, BORED_WORDS):
        return DetectedIntent(
            name="user_bored",
            emotion=Emotion.PLAYFUL,
            animation=CharacterState.EXCITED,
            sound="purr",
            response="Then stop working and play with me!",
        )
    if _matches_any(lowered, HOW_ARE_YOU):
        return DetectedIntent(
            name="how_are_you",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            response="I'm doing great now that you're here!",
        )
    if _matches_any(lowered, WHAT_DOING):
        return DetectedIntent(
            name="what_doing",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="Just watching you work. What about you?",
        )
    # Checked after WHAT_DOING (not before): "what are you doing" would
    # otherwise also match the shorter "what are you" identity phrase.
    if IDENTITY_TRIGGER.search(lowered):
        return DetectedIntent(
            name="identity",
            emotion=Emotion.PLAYFUL,
            animation=CharacterState.TALKING,
            response=(
                "I'm Mochi! Your own little cat friend who lives right here "
                "on your desktop - not a real cat, but I try my best. hehe"
            ),
        )
    if _matches_any(lowered, MEMORY_QUERY):
        return DetectedIntent(
            name="memory_query",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="Memory isn't wired up yet - soon!",
        )

    # --- Fallback: still react, just stay generic (spec section 36) ------
    return DetectedIntent(
        name="unknown",
        emotion=Emotion.CURIOUS,
        animation=CharacterState.THINKING,
        response="Hmm, I'm not sure what you mean yet, but I'm listening!",
    )
