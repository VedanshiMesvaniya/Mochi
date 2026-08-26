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
# Bug report: "what time is it" had no rule-based handler at all, so it
# fell all the way through to the open-ended LLM bucket - meaning if
# Ollama isn't installed/running (spec section 5: it's explicitly
# optional, chat must stay usable without it), asking Mochi the time got
# either a generic "I'm not sure what you mean" or an "install Ollama"
# message instead of an actual answer. Reading the system clock needs no
# AI at all, so it's answered directly and deterministically here, the
# same way reminders/dates already are (spec section 26).
TIME_QUERY_TRIGGER = re.compile(
    r"\b(what(?:'s| is) the time|what time (?:is it|do you have)|"
    r"current time|got the time)\b",
    re.IGNORECASE,
)
DATE_QUERY_TRIGGER = re.compile(
    r"\b(what(?:'s| is) (?:the |today'?s )?date|what day (?:is it|of the week is it)"
    r"|what'?s today)\b",
    re.IGNORECASE,
)
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

# On-demand expression command (spec: "in chat if i say make <expression>
# for me then it show me [it] cutely") - maps a requested expression name
# onto one of the 16 pixel-face states (see app/character/pixel_face.py).
# Deliberately excludes LOCKED/DIZZY - those are easter-egg-only states
# (lock-screen, shake-the-window), not part of the performable set.
EXPRESSION_TRIGGER = re.compile(r"\b(?:make|show|do)\b", re.IGNORECASE)
_EXPRESSION_FILLER_WORDS = {
    "for", "me", "you", "your", "please", "a", "an", "the",
    "face", "expression", "eyes", "look", "some", "my", "cutely", "cute",
}
EXPRESSION_ALIASES: dict[str, CharacterState] = {
    "happy": CharacterState.HAPPY,
    "sad": CharacterState.SAD,
    "angry": CharacterState.ANGRY,
    "mad": CharacterState.ANGRY,
    "confused": CharacterState.CONFUSED,
    "surprised": CharacterState.SURPRISED,
    "shocked": CharacterState.SURPRISED,
    "thinking": CharacterState.THINKING,
    "sleepy": CharacterState.SLEEPY,
    "tired": CharacterState.SLEEPY,
    "sleeping": CharacterState.SLEEP,
    "asleep": CharacterState.SLEEP,
    "talking": CharacterState.TALKING,
    "excited": CharacterState.EXCITED,
    "alert": CharacterState.ALERT,
    "blush": CharacterState.BLUSH,
    "blushing": CharacterState.BLUSH,
    "shy": CharacterState.SHY,
    "heart": CharacterState.HEART,
    "heart eyes": CharacterState.HEART,
    "love": CharacterState.HEART,
    "wink": CharacterState.WINK,
    "winking": CharacterState.WINK,
    "idle": CharacterState.IDLE,
    "neutral": CharacterState.IDLE,
}


def _match_expression_command(lowered: str) -> Optional[CharacterState]:
    if not EXPRESSION_TRIGGER.search(lowered):
        return None
    # Strip the trigger word, then the filler words, so "make a happy face
    # for me", "show me your wink", and "do sad expression" all reduce to
    # just the bare expression name before matching EXPRESSION_ALIASES.
    body = EXPRESSION_TRIGGER.sub("", lowered, count=1)
    words = [w for w in re.findall(r"[a-z]+", body) if w not in _EXPRESSION_FILLER_WORDS]
    if not words:
        return None
    state = EXPRESSION_ALIASES.get(" ".join(words))
    if state is not None:
        return state
    return EXPRESSION_ALIASES.get(words[-1])  # e.g. leftover ordering quirks


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


REMINDER_TRIGGER = re.compile(
    r"\b(remind me|set a reminder|create a reminder|make a reminder|reminder to|"
    r"don'?t let me forget)\b",
    re.IGNORECASE,
)
TIMER_TRIGGER = re.compile(
    r"\b(set a timer|start a timer|timer for|start a countdown|countdown for|"
    r"\d+\s*(?:second|sec|minute|min|hour|hr)s?\s+timer)\b",
    re.IGNORECASE,
)
# Listing/query phrasing, checked BEFORE the creation triggers above so
# something like "remind me what tasks I have" (contains "remind me" but
# is clearly a query, not a new reminder) still resolves correctly.
# Deliberately its own local, deterministic DB read (see chat_engine.py)
# rather than being left to the LLM - a factual "what's in my database"
# question is exactly the kind of thing a small local model will
# confidently hallucinate an answer to instead of admitting it doesn't
# know (spec: "it can not read db, make it read db so it can answer").
LIST_TASKS_TRIGGER = re.compile(
    r"\b(do i have (any )?tasks?|what tasks?|list (my )?tasks?|show (me )?(my )?tasks?|"
    r"any tasks?( to do)?|tasks? (for )?today)\b",
    re.IGNORECASE,
)
LIST_REMINDERS_TRIGGER = re.compile(
    r"\b(do i have (any )?reminders?|what reminders?|list (my )?reminders?|"
    r"show (me )?(my )?reminders?|any reminders?|reminders? (for )?today|"
    r"what(\'s| is) on my (reminders|schedule))\b",
    re.IGNORECASE,
)
TASK_TRIGGER = re.compile(
    r"\b(remember (that )?i need to|add (a )?task|new task:?|create (a )?task|todo:?|task:)",
    re.IGNORECASE,
)

# --- Completing/cancelling an existing task/reminder/timer -------------
# Previously totally unreachable from chat (spec bug report: "mark my
# task to call aunt as done" fell through to the open-ended LLM, which
# had no task list to check against and hallucinated a reply instead of
# doing anything). These deliberately require the literal word "task" /
# "reminder" / "timer" so "mark X as done" unambiguously routes to the
# right store rather than guessing - see chat_engine.py's fuzzy title
# match, which resolves the free-text query against whatever's actually
# open/active in the DB.
TASK_DONE_TRIGGER = re.compile(
    r"(?:\bmark\b(?=.*\btask\b)(?=.*\b(?:done|complete(?:d)?|finished)\b))"
    r"|(?:\bcomplete(?:d)?\b(?:\s+(?:the|my))?\s+task\b)"
    r"|(?:\bfinish(?:ed)?\b(?:\s+(?:the|my))?\s+task\b)"
    r"|(?:\btask\b.*\b(?:is\s+)?(?:done|complete(?:d)?|finished)\b)",
    re.IGNORECASE,
)
TASK_CANCEL_TRIGGER = re.compile(
    r"\b(?:cancel|delete|remove)\b(?=.*\btask\b)|\btask\b.*\b(?:cancel|delete|remove)\b",
    re.IGNORECASE,
)
REMINDER_DONE_TRIGGER = re.compile(
    r"(?:\bmark\b(?=.*\breminder\b)(?=.*\b(?:done|complete(?:d)?|finished)\b))"
    r"|(?:\bcomplete(?:d)?\b(?:\s+(?:the|my))?\s+reminder\b)"
    r"|(?:\breminder\b.*\b(?:is\s+)?(?:done|complete(?:d)?|finished)\b)",
    re.IGNORECASE,
)
REMINDER_CANCEL_TRIGGER = re.compile(
    r"\b(?:cancel|delete|remove)\b(?=.*\breminder\b)|\breminder\b.*\b(?:cancel|delete|remove)\b",
    re.IGNORECASE,
)
TIMER_CANCEL_TRIGGER = re.compile(
    r"\b(?:cancel|stop|delete|remove)\b(?=.*\btimer\b)|\btimer\b.*\b(?:cancel|stop)\b",
    re.IGNORECASE,
)
# Bug report: "check on X" / "mark it as done" (without the literal word
# task/reminder/timer) fell all the way through to the open-ended LLM
# fallback, which has no actual database access and would just invent a
# plausible-sounding reply ("I'll remind you..." / "Okay, I'll take care
# of it") without doing or checking anything real - see chat_engine.py's
# _check_on_reaction/_complete_ambiguous_reaction, which answer from the
# real task/reminder tables instead. Checked *after* the create_reminder/
# create_task/start_timer triggers below (deliberately - so "remind me to
# check on my aunt" is still a reminder creation, not a status check) but
# before small talk.
CHECK_ON_TRIGGER = re.compile(
    r"\b(check(?:ed)? on|any update on|status of|what'?s the status of|"
    r"did (?:you|i) (?:forget|remind))\b",
    re.IGNORECASE,
)
# Accusation phrasing specifically ("you forgot to remind me", "you never
# remind me", "you didn't remind me") - bug report: this contains the
# literal phrase "remind me", so it was matching REMINDER_TRIGGER below and
# being (mis)read as a brand-new reminder request with the complaint itself
# as the title (e.g. "you dumb cat you forgot to remind me" -> "Got it -
# \"You dumb cat you forgot to\" - but when?"). Deliberately its own
# narrow trigger (rather than folded into CHECK_ON_TRIGGER above) so it can
# be checked before REMINDER_TRIGGER without disturbing CHECK_ON_TRIGGER's
# existing position/reasoning - "remind me to check on my aunt" must still
# hit REMINDER_TRIGGER and create a reminder, not be caught here; the "you"
# subject is what makes this unambiguously about something Mochi
# supposedly already failed to do, not a new request.
REMINDER_ACCUSATION_TRIGGER = re.compile(
    r"\byou(?:'ve| have)? (?:forgot(?:ten)? to remind|never remind(?:ed)?|didn'?t remind)\b"
    # "did you forget to remind me" has the same "remind me" substring
    # problem as the "you forgot" phrasing above and needs the same early
    # interception, ahead of REMINDER_TRIGGER, rather than waiting for
    # CHECK_ON_TRIGGER's later position where REMINDER_TRIGGER already won.
    r"|\bdid (?:you|i) forget to remind\b",
    re.IGNORECASE,
)
AMBIGUOUS_DONE_TRIGGER = re.compile(
    r"\b(mark (?:it|that|this) (?:as )?done|(?:it|that|this)(?:'s| is) done|"
    r"(?:it|that|this)(?:'s| is) finished|complete it|finish it|"
    r"i (?:did|finished) it)\b",
    re.IGNORECASE,
)
# Same reasoning as AMBIGUOUS_DONE_TRIGGER, but for cancelling rather than
# completing - bug report ("chat loses context"/"forgets what it just
# did"): "cancel it" / "delete it" / "scratch that" have no literal
# "task"/"reminder"/"timer" word, so TASK_CANCEL_TRIGGER/
# REMINDER_CANCEL_TRIGGER/TIMER_CANCEL_TRIGGER (which all require that
# word) never matched them - the message fell all the way through to the
# open-ended LLM fallback, which has no access to the real reminder/task/
# timer tables and would happily reply as if it had cancelled something
# ("Okay, cancelled!") without touching the database at all. That reads
# exactly like Mochi forgetting what was just being discussed, even
# though nothing was ever actually created to forget - it simply never
# acted. See _cancel_ambiguous_reaction in chat_engine.py, which - same
# as complete_ambiguous - resolves "it" against whatever's actually
# open/pending/running across all three stores instead of guessing.
#
# Deliberately does NOT include bare "never mind"/"forget it" - those are
# extremely common as plain conversational dismissals unrelated to any
# reminder/task/timer (e.g. "eh, never mind, it's not important"), and
# would make ordinary small talk get answered with "I don't have
# anything open to cancel!" instead of just moving on. Only phrasing that
# names a specific, unambiguous cancel action is matched here.
AMBIGUOUS_CANCEL_TRIGGER = re.compile(
    r"\b(cancel it|cancel that|delete it|remove it|scratch that|undo that|nix it)\b",
    re.IGNORECASE,
)
# "count 1 to 10" / "count from 1 to 10" -> groups 1/2; "count to 10"
# (implicit start of 1) -> group 3. See the count-handling block in
# detect_intent() below for why this is deterministic rather than an LLM bit.
COUNT_TRIGGER = re.compile(
    r"\bcount\b(?:\s+from)?\s+(\d{1,3})\s*(?:to|-)\s*(\d{1,3})\b"
    r"|\bcount\s+to\s+(\d{1,3})\b",
    re.IGNORECASE,
)
# Strips control words (mark/complete/cancel/.../as/is/my/the/to plus the
# store keyword itself) out of a matched sentence, leaving just the
# free-text query to fuzzy-match against real titles in chat_engine.py.
# e.g. "mark my task to call aunt as done" -(strip "task")-> "call aunt".
_ACTION_STOPWORDS = re.compile(
    r"\b(?:mark|complete(?:d)?|finish(?:ed)?|cancel|delete|remove|stop|"
    r"task|reminder|timer|as|is|my|the|to|done)\b",
    re.IGNORECASE,
)


def _extract_action_query(text: str) -> str:
    cleaned = _ACTION_STOPWORDS.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.!")

# --- Google Calendar (spec sections 22-24, V3: read-only) --------------
# Checked before the small-talk/fallback bucket but after reminders/
# timers/tasks, same reasoning as LIST_REMINDERS_TRIGGER above: these are
# read-only DB/API queries handled deterministically in chat_engine.py,
# never left to the LLM (spec section 41 - and doubly true here, since a
# hallucinated calendar answer is worse than a hallucinated reminder).
CALENDAR_CONNECT_TRIGGER = re.compile(
    r"\b(connect|link|set ?up) (my |google )*calendar\b", re.IGNORECASE
)
CALENDAR_DISCONNECT_TRIGGER = re.compile(
    r"\b(disconnect|unlink|remove) (my |google )*calendar\b", re.IGNORECASE
)
# "today"/"tomorrow" are each their own trigger (rather than folding into
# UPCOMING) so "what's on my calendar today" gets today's actual events
# instead of a generic 7-day lookahead answer. Order matters where these
# overlap - detect_intent() checks TODAY and TOMORROW before the more
# general UPCOMING, same reasoning as LIST_REMINDERS_TRIGGER above.
CALENDAR_TODAY_TRIGGER = re.compile(
    r"\b(what(?:'s| is) on my calendar( for)? today"
    r"|do i have (anything|any events?|any meetings?) today"
    r"|what (do i have|am i doing) today"
    r"|today'?s (events?|schedule|meetings?))\b",
    re.IGNORECASE,
)
CALENDAR_TOMORROW_TRIGGER = re.compile(
    r"\b(what(?:'s| is) on my calendar( for)? tomorrow"
    r"|do i have (anything|any events?|any meetings?) tomorrow"
    r"|what (do i have|am i doing) tomorrow"
    r"|tomorrow'?s (events?|schedule|meetings?))\b",
    re.IGNORECASE,
)
CALENDAR_UPCOMING_TRIGGER = re.compile(
    r"\b(what(?:'s| is) on my calendar"
    r"|what(?:'s| is) coming up"
    r"|upcoming (events?|meetings?)"
    r"|when(?:'s| is) my next (meeting|event)"
    r"|what am i doing this week)\b",
    re.IGNORECASE,
)

# --- Google Calendar writes (spec section 23, V4: create/cancel events,
# both requiring confirmation - see app/ai/chat_engine.py's
# propose-then-confirm flow, which is the only place either of these
# DetectedIntents' tool_args actually get executed). Checked after the
# read-only calendar triggers above so e.g. "what's on my calendar" is
# never misread as a create/delete request.
CALENDAR_CREATE_TRIGGER = re.compile(
    r"\b(?:add|create|schedule|book) (?:a |an |the )?(meeting|event|appointment|call)\b",
    re.IGNORECASE,
)
# Lookahead rather than requiring the noun immediately after the verb -
# spec example "Cancel my 5 PM meeting" has the time-of-day sitting
# between them, so "cancel" + noun just need to both appear within a
# short span, not be adjacent. Zero-width match (ends right after the
# verb) so downstream code can search the *entire* remaining message
# (verb onward) for both the time-of-day and the noun itself, since
# either can appear on either side of the other ("cancel my 5pm meeting"
# vs "cancel my meeting with Devika").
CALENDAR_DELETE_TRIGGER = re.compile(
    r"\b(?:cancel|delete|remove)\b(?=.{0,40}?\b(?:meeting|event|appointment|call)\b)",
    re.IGNORECASE,
)
# Bare time-of-day like "5 pm" / "5:30pm" - used to figure out *which*
# event a delete command means (spec example: "Cancel my 5 PM meeting"),
# distinct from TIME_AT above since that one requires the word "at".
CALENDAR_DELETE_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)

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


def _title_from(text: str, fallback: str = "Reminder") -> str:
    # Drop a trailing time clause so "call mom at 7pm" -> "call mom"
    cleaned = TIME_AT.sub("", text)
    cleaned = TIME_IN.sub("", cleaned)
    cleaned = re.sub(r"\btomorrow\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^to\s+", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ,.!")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else fallback


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

    # --- Task/reminder listing (a real DB read, see chat_engine.py) ---
    # Checked first, before the creation triggers below, so query phrasing
    # that happens to contain "remind me" (e.g. "remind me what tasks I
    # have") still resolves as a listing request, not a new reminder.
    if LIST_TASKS_TRIGGER.search(lowered):
        return DetectedIntent(
            name="list_tasks",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine fills this in from the real task list
            tool="list_tasks",
        )
    if LIST_REMINDERS_TRIGGER.search(lowered):
        return DetectedIntent(
            name="list_reminders",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine fills this in from the real reminder list
            tool="list_reminders",
        )

    # --- Completing/cancelling an existing task/reminder/timer ---------
    # Checked before the create triggers below and before calendar (no
    # overlap risk either way, but grouped with the listing checks above
    # since these are all "look something up / act on something that
    # already exists" rather than "create something new"). response=""
    # the same way list_tasks/list_reminders do - chat_engine.py's
    # fuzzy-match-then-act handler builds the real reply from whatever
    # actually matches in the DB, never a canned line here.
    if TASK_DONE_TRIGGER.search(lowered):
        return DetectedIntent(
            name="complete_task",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            response="",
            tool_args={"query": _extract_action_query(text)},
        )
    if TASK_CANCEL_TRIGGER.search(lowered):
        return DetectedIntent(
            name="cancel_task",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
            response="",
            tool_args={"query": _extract_action_query(text)},
        )
    if REMINDER_DONE_TRIGGER.search(lowered):
        return DetectedIntent(
            name="complete_reminder",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            response="",
            tool_args={"query": _extract_action_query(text)},
        )
    if REMINDER_CANCEL_TRIGGER.search(lowered):
        return DetectedIntent(
            name="cancel_reminder",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
            response="",
            tool_args={"query": _extract_action_query(text)},
        )
    if TIMER_CANCEL_TRIGGER.search(lowered):
        return DetectedIntent(
            name="cancel_timer",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
            response="",
            tool_args={"query": _extract_action_query(text)},
        )

    # --- Google Calendar (spec sections 22-24, V3: read-only) -----------
    # Connect/disconnect checked first - "connect my calendar" would
    # otherwise also satisfy the looser UPCOMING phrasing below.
    if CALENDAR_CONNECT_TRIGGER.search(lowered):
        return DetectedIntent(
            name="calendar_connect",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine fills this in (success/failure)
            tool="calendar_connect",
        )
    if CALENDAR_DISCONNECT_TRIGGER.search(lowered):
        return DetectedIntent(
            name="calendar_disconnect",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
            response="",
            tool="calendar_disconnect",
        )
    if CALENDAR_TODAY_TRIGGER.search(lowered):
        return DetectedIntent(
            name="calendar_today",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine fills this in from a live API read
            tool="calendar_today",
        )
    if CALENDAR_TOMORROW_TRIGGER.search(lowered):
        return DetectedIntent(
            name="calendar_tomorrow",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",
            tool="calendar_tomorrow",
        )
    if CALENDAR_UPCOMING_TRIGGER.search(lowered):
        return DetectedIntent(
            name="calendar_upcoming",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",
            tool="calendar_upcoming",
        )

    # --- Google Calendar writes (spec section 23, V4) -------------------
    # Matched against `text` (not `lowered`) like the reminder-parsing
    # block below, since group text here needs to preserve the original
    # casing for the title (e.g. "Devika", not "devika").
    create_match = CALENDAR_CREATE_TRIGGER.search(text)
    if create_match:
        noun = create_match.group(1).capitalize()
        body = text[create_match.end():].strip()
        due = _parse_absolute_time(body, now)
        minutes = _parse_relative_minutes(body)
        if due is None and minutes is not None:
            due = now + timedelta(minutes=minutes)

        # Build a title from whatever's left after stripping the time
        # clause, e.g. "schedule a meeting with Devika tomorrow at 5pm"
        # -> noun="Meeting", suffix="with Devika" -> "Meeting with Devika".
        suffix = TIME_AT.sub("", body)
        suffix = TIME_IN.sub("", suffix)
        suffix = re.sub(r"\btomorrow\b", "", suffix, flags=re.IGNORECASE)
        suffix = suffix.strip(" ,.!")
        title = f"{noun} {suffix}" if suffix else noun
        title = title[:1].upper() + title[1:]

        if due is None:
            return DetectedIntent(
                name="calendar_create_needs_time",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                response=(
                    f"Got it - \"{title}\" - but when? Try "
                    "\"tomorrow at 5pm\" or \"in 2 hours\"."
                ),
            )
        return DetectedIntent(
            name="calendar_create_event",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine builds the confirmation prompt
            tool="calendar_create_event",
            tool_args={"title": title, "start_iso": due.isoformat()},
        )

    delete_match = CALENDAR_DELETE_TRIGGER.search(text)
    if delete_match:
        body = text[delete_match.end():].strip()
        time_match = CALENDAR_DELETE_TIME.search(body)
        query = None
        time_of_day = None
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            meridiem = time_match.group(3).lower()
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            time_of_day = f"{hour:02d}:{minute:02d}"
        else:
            # e.g. "cancel my meeting with Devika" -> search by title
            # text: drop the noun itself ("meeting") plus filler words,
            # leaving just the identifying part ("Devika").
            noun_match = re.search(
                r"\b(meeting|event|appointment|call)\b", body, re.IGNORECASE
            )
            cleaned = body
            if noun_match:
                cleaned = body[: noun_match.start()] + " " + body[noun_match.end():]
            cleaned = re.sub(r"^\s*(my|the)\s+", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(
                r"^(with|called|titled|about)\s+", "", cleaned, flags=re.IGNORECASE
            )
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!")
            query = cleaned or None
        return DetectedIntent(
            name="calendar_delete_event",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine builds the confirmation prompt
            tool="calendar_delete_event",
            tool_args={"query": query, "time_of_day": time_of_day},
        )

    # --- Accusation about a forgotten reminder ---------------------------
    # Checked BEFORE the create-reminder trigger below: see
    # REMINDER_ACCUSATION_TRIGGER's definition above for why "you forgot to
    # remind me" needs to be caught here specifically, ahead of
    # REMINDER_TRIGGER, instead of at CHECK_ON_TRIGGER's normal (later)
    # position.
    if REMINDER_ACCUSATION_TRIGGER.search(lowered):
        body = _strip_trigger(text, REMINDER_ACCUSATION_TRIGGER)
        # The trigger match stops right before a trailing "me"/"you"
        # (e.g. "...to remind [me]"), so strip a leftover leading/trailing
        # pronoun too - otherwise a bare complaint like "you forgot to
        # remind me" leaves a stray "me" as the whole "query", which would
        # print verbatim in the "I don't have anything like ..." fallback.
        body = re.sub(r"^(me|you)\b|\b(me|you)$", "", body, flags=re.IGNORECASE).strip(" ,.!?")
        return DetectedIntent(
            name="check_on",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",
            tool_args={"query": body.strip(" ,.!?") or None},
        )

    # --- Reminders / Timers / Tasks -------------------------------------
    # Bug report ("timer/reminder/task mix up"): these three used to be
    # three independent `if` blocks checked in a fixed order (reminder,
    # then timer, then task), so ANY message that happened to contain a
    # reminder-trigger phrase anywhere in it always won, even when a
    # timer/task trigger was what the person was actually asking for -
    # e.g. "start a timer and remind me when it's done" contains "remind
    # me" and matched REMINDER_TRIGGER first, so it asked "but when?"
    # for a reminder instead of starting the timer it was clearly asked
    # to start; "remind me to add task buy milk" would silently create a
    # reminder instead of the task explicitly named. Fixed at the root by
    # checking all three triggers up front and resolving to whichever one
    # actually matched *earliest* in what the person typed, rather than
    # a fixed reminder > timer > task priority - the phrase they said
    # first is what they meant, regardless of which category's regex
    # happens to be checked first in this function. Ties (same start
    # index, not achievable with these distinct trigger phrases in
    # practice) fall back to the original reminder > timer > task order
    # for determinism.
    reminder_match = REMINDER_TRIGGER.search(lowered)
    timer_match = TIMER_TRIGGER.search(lowered)
    task_match = TASK_TRIGGER.search(lowered)
    _creation_candidates = [
        m.start()
        for m in (reminder_match, timer_match, task_match)
        if m is not None
    ]
    winner = min(_creation_candidates) if _creation_candidates else None

    if reminder_match is not None and reminder_match.start() == winner:
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
            response=f"Okay! I'll remind you to {title.lower()} at {due:%I:%M %p}.",
            tool="create_reminder",
            tool_args={"title": title, "datetime_iso": due.isoformat()},
        )

    if timer_match is not None and timer_match.start() == winner:
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

    if task_match is not None and task_match.start() == winner:
        body = TASK_TRIGGER.sub("", text, count=1).strip(" ,.!")
        # Tasks are checklist items with no required deadline (unlike
        # reminders) - but spec follow-up: "unless i give it deadline it
        # should be there [too]", so reuse the same at/in-N-minutes
        # parsing reminders use and attach it when present, without
        # requiring it the way reminders do.
        due = _parse_absolute_time(body, now) if body else None
        minutes = _parse_relative_minutes(body) if body else None
        if due is None and minutes is not None:
            due = now + timedelta(minutes=minutes)
        title = _title_from(body, fallback="New task") if body else "New task"
        due_note = f" (due {due:%I:%M %p})" if due else ""
        tool_args = {"title": title}
        if due is not None:
            tool_args["due_at_iso"] = due.isoformat()
        return DetectedIntent(
            name="create_task",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            sound="chirp",
            response=f"Noted! I'll remember: {title.lower()}{due_note}.",
            tool="create_task",
            tool_args=tool_args,
        )

    # --- Check-on / ambiguous done (see CHECK_ON_TRIGGER/AMBIGUOUS_DONE_TRIGGER
    # definitions above for why these are checked here specifically; the
    # narrower REMINDER_ACCUSATION_TRIGGER case is handled earlier, right
    # before the create_reminder block) ----------------------------------
    if CHECK_ON_TRIGGER.search(lowered):
        body = _strip_trigger(text, CHECK_ON_TRIGGER)
        return DetectedIntent(
            name="check_on",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",
            tool_args={"query": body.strip(" ,.!?") or None},
        )
    if AMBIGUOUS_DONE_TRIGGER.search(lowered):
        return DetectedIntent(
            name="complete_ambiguous",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            response="",
            tool_args={},
        )
    if AMBIGUOUS_CANCEL_TRIGGER.search(lowered):
        return DetectedIntent(
            name="cancel_ambiguous",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
            response="",
            tool_args={},
        )

    # --- On-demand expression command --------------------------------
    # Checked after reminders/timers/tasks (so "make a reminder to..."
    # etc. are never shadowed by this) but before small talk, since it's
    # a deliberate, specific command rather than something that should be
    # outranked by a stray keyword match.
    requested_state = _match_expression_command(lowered)
    if requested_state is not None:
        display = requested_state.value.replace("_", " ")
        return DetectedIntent(
            name="expression_request",
            emotion=Emotion.PLAYFUL,
            animation=requested_state,
            sound="chirp",
            response=f"Hehe, here's my {display} face for you~",
        )

    # --- On-demand counting command -----------------------------------
    # Bug report: "mochi count 1 to 10" got a single flat sentence back
    # ("Counting to 10, one at a time.") from the open-ended LLM fallback -
    # a kid asking a companion to count with them wants to actually hear
    # it counted, with excitement, not be told that counting is happening.
    # Handled deterministically (same reasoning as the expression command
    # above) so it's always the same fun, reliable bit rather than
    # depending on whether a local LLM happens to be installed/running.
    count_match = COUNT_TRIGGER.search(lowered)
    if count_match:
        start_str, end_str, to_only_str = count_match.groups()
        if to_only_str is not None:
            start, end = 1, int(to_only_str)
        else:
            start, end = int(start_str or 1), int(end_str)
        if start > end:
            start, end = end, start
        # Cap the range so a typo like "count to 1000" can't produce a
        # wall of text instead of a fun little bit.
        end = min(end, start + 30)
        numbers = "! ".join(str(n) for n in range(start, end + 1)) + "!"
        return DetectedIntent(
            name="count",
            emotion=Emotion.EXCITED,
            animation=CharacterState.EXCITED,
            sound="chirp",
            response=f"Ooh okay, here we go!! {numbers} Yay, I did it!! 🎉",
        )

    # --- Time / date queries (spec section 26: always answer these from
    # the real clock, never a guess) - checked in the small-talk section
    # but ahead of HOW_ARE_YOU/WHAT_DOING since neither of those overlaps
    # with "what time/day is it" phrasing.
    if TIME_QUERY_TRIGGER.search(lowered):
        return DetectedIntent(
            name="time_query",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.TALKING,
            response=f"It's {now:%I:%M %p} right now.",
        )
    if DATE_QUERY_TRIGGER.search(lowered):
        # now.day (not the %-d/%#d strftime extension, which isn't
        # portable between Linux and Windows - spec section 3: Windows is
        # the primary target) avoids a leading zero on single-digit days
        # without relying on a platform-specific format code.
        return DetectedIntent(
            name="date_query",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.TALKING,
            response=f"It's {now:%A, %B} {now.day}.",
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
