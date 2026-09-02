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
from datetime import datetime, time as time_of_day, timedelta
from typing import Optional

from app.ai.conversation_state import MULTI_REFERENCE_SRC
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
# Bug report: "set 10 second timmer" (a doubled-letter typo, the most
# common way people mistype this word) fell through to the open-ended
# LLM fallback entirely - TIMER_TRIGGER only recognized the exact
# spelling "timer". `tim+er` tolerates one-or-more `m`s ("timer",
# "timmer", "timmmer", ...) everywhere the literal word would otherwise
# appear below, without loosening anything else about the match.
_TIMER_WORD = r"tim+er"

TIMER_TRIGGER = re.compile(
    rf"\b(set a {_TIMER_WORD}|start a {_TIMER_WORD}|{_TIMER_WORD} for|start a countdown|countdown for|"
    rf"\d+\s*(?:second|sec|minute|min|hour|hr)s?\s+{_TIMER_WORD})\b",
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
# Timers had no listing trigger at all before this - "what timers do I
# have" / "any timers running" fell all the way through to the
# open-ended LLM fallback, which has no real access to the timers table
# and would have to guess or deflect. Same shape as LIST_TASKS_TRIGGER/
# LIST_REMINDERS_TRIGGER above.
LIST_TIMERS_TRIGGER = re.compile(
    rf"\b(do i have (any )?{_TIMER_WORD}s?|what {_TIMER_WORD}s?|list (my )?{_TIMER_WORD}s?|"
    rf"show (me )?(my )?{_TIMER_WORD}s?|any {_TIMER_WORD}s?( running)?|{_TIMER_WORD}s? (left|remaining))\b",
    re.IGNORECASE,
)
# Glossary-driven "what have I finished/cancelled" query (spec follow-up:
# "when ask remain task work reminder timer it should fetch from main
# table not done table" - the flip side of that is asking about the
# *done* table, which nothing above covers). Deliberately requires BOTH
# an entity word (task/reminder/timer) AND an explicit done/history/
# cancelled word via lookaheads (order-independent, so "show completed
# tasks" and "which tasks are done" both match) - the done/history/
# cancelled requirement is what keeps this from ever intercepting a plain
# "what tasks do I have" (no such word present, so LIST_TASKS_TRIGGER
# above still wins) or a "what's the status of my task" check-on
# question (CHECK_ON_TRIGGER, checked later) - neither contains a
# done-style word either. See app/ai/db_glossary.py for how the matched
# entity/status actually gets resolved into a real query.
LIST_DONE_TRIGGER = re.compile(
    r"\b(?:what|which|show(?:\s+me)?|list|any|see|view|do i have)\b"
    rf"(?=.*\b(?:task|tasks|reminder|reminders|{_TIMER_WORD}|{_TIMER_WORD}s)\b)"
    r"(?=.*\b(?:done|completed?|finished|history|archive[ds]?|cancell?ed)\b)",
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
    rf"\b(?:cancel|stop|delete|remove)\b(?=.*\b{_TIMER_WORD}\b)|\b{_TIMER_WORD}\b.*\b(?:cancel|stop)\b",
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
# Shared fragment: what a bare/ordinal reference to "the thing we're
# talking about" can look like inside a trigger phrase (security review
# I1/I3 - see app/ai/conversation_state.py for how these get resolved to
# a real database row deterministically). Kept in one place so
# AMBIGUOUS_DONE_TRIGGER and AMBIGUOUS_CANCEL_TRIGGER below both support
# "cancel the second one" the same way they already support "cancel it".
_REFERENCE_TARGET = r"(?:it|that|this|the (?:first|second|third|fourth|fifth|last) one)"

# Multi-target counterpart to _REFERENCE_TARGET above (conversational-
# issues report P0: "three of them check as done" needs to reach the same
# ambiguous handlers as "mark it as done", just resolving to several
# entities instead of one - see app/ai/conversation_state.py's
# resolve_selection()/resolve_selection_typed(), which is what actually
# turns this phrase into real database rows). Sourced from
# conversation_state.MULTI_REFERENCE_SRC rather than duplicated here, so
# the trigger and the resolver can never recognise different phrasing.
_MULTI_REFERENCE_TARGET = rf"(?:{MULTI_REFERENCE_SRC})"

AMBIGUOUS_DONE_TRIGGER = re.compile(
    r"\b(mark " + _REFERENCE_TARGET + r" (?:as )?done|"
    + _REFERENCE_TARGET + r"(?:'s| is) done|"
    + _REFERENCE_TARGET + r"(?:'s| is) finished|"
    r"complete " + _REFERENCE_TARGET + r"|finish " + _REFERENCE_TARGET + r"|"
    r"i (?:did|finished) it|"
    r"mark " + _MULTI_REFERENCE_TARGET + r" (?:as )?done|"
    + _MULTI_REFERENCE_TARGET + r" (?:check(?:ed)? (?:as )?done|check(?:ed)? off|"
    r"(?:'s| is| are) (?:done|finished))|"
    r"complete " + _MULTI_REFERENCE_TARGET + r"|finish " + _MULTI_REFERENCE_TARGET
    + r")\b",
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
    r"\b(cancel " + _REFERENCE_TARGET + r"|delete " + _REFERENCE_TARGET + r"|"
    r"remove " + _REFERENCE_TARGET + r"|scratch that|undo that|nix it|"
    r"cancel " + _MULTI_REFERENCE_TARGET + r"|delete " + _MULTI_REFERENCE_TARGET
    + r"|remove " + _MULTI_REFERENCE_TARGET + r")\b",
    re.IGNORECASE,
)

# Tried in this order (multi-target first) so a phrase that could
# technically satisfy both - there isn't one today, since "one" is
# deliberately excluded from MULTI_REFERENCE_SRC, but keeping the more
# specific/quantified pattern first is the safer default - always prefers
# the multi-target reading over the singular one.
_ANY_REFERENCE_PATTERN = re.compile(
    _MULTI_REFERENCE_TARGET + "|" + _REFERENCE_TARGET, re.IGNORECASE
)


def _extract_reference_target(text: str) -> str:
    """Pulls out just the "it"/"that"/"the second one"/"three of them"
    part of an AMBIGUOUS_DONE_TRIGGER/AMBIGUOUS_CANCEL_TRIGGER match, so
    chat_engine.py's resolve_typed()/resolve_selection_typed() gets the
    actual phrase the user said instead of a hardcoded "it" - needed for
    ordinal references ("the second one") and multi-target references
    ("three of them", "all of them") to work through this path at all."""
    match = re.search(_ANY_REFERENCE_PATTERN, text)
    return match.group(0).lower() if match else "it"
# "make it 8" / "change it to 8:30pm" / "move that to tomorrow at 9" -
# reschedule counterpart to AMBIGUOUS_DONE_TRIGGER/AMBIGUOUS_CANCEL_TRIGGER
# above (security review I1/I3 - "conversational reference resolution").
# "it"/"that"/"this" here is resolved deterministically against
# app/ai/conversation_state.py's remembered last entity in
# chat_engine.py's _reschedule_reference_reaction, never guessed at.
# Deliberately requires "it"/"that"/"this" (not a bare "make 8") so this
# can't accidentally swallow an unrelated sentence that happens to start
# with "make"/"change"/"move"/"set".
RESCHEDULE_TRIGGER = re.compile(
    r"\b(?:make|change|move|set|reschedule) (?:it|that|this)\b(?:\s+to)?\s*",
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
    rf"\b(?:mark|complete(?:d)?|finish(?:ed)?|cancel|delete|remove|stop|"
    rf"task|reminder|{_TIMER_WORD}|as|is|my|the|to|done)\b",
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

# Bug report ("it can not reason ... one minute means current time plus
# one minute"): TIME_IN/DURATION_ONLY above only ever matched a DIGIT
# immediately before the unit word, so "in one minute" (spelled out, no
# digit at all) silently matched nothing and fell straight through to
# the "but when?" clarifying question - even though "in 1 minute" right
# next to it works fine. Rather than trying to teach two already-tricky
# regexes to also understand English number words directly, this
# normalizes spelled-out numbers to digits FIRST (deterministically, no
# model involved) so the exact same TIME_IN/DURATION_ONLY regexes above
# keep being the single source of truth for what "N <unit>" means.
#
# Only ever rewrites a number word when it's immediately followed by a
# time-unit word - "buy a book" or "renew a task" must never turn into
# "buy 1 book"/"renew 1 task" (which would corrupt _title_from's output),
# so this is intentionally much narrower than a general word-to-number
# converter.
_WORD_TO_NUM = {
    "a": "1", "an": "1", "couple": "2", "few": "3",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60",
}
_TIME_UNIT_WORDS = (
    r"minute|minutes|min|mins|hour|hours|hr|hrs|second|seconds|sec|secs"
)
# Longest-word-first so e.g. "thirteen" isn't cut short by "three"/"ten"
# matching part of it first.
_WORD_NUMBER_BEFORE_UNIT_RE = re.compile(
    r"\b(" + "|".join(sorted(_WORD_TO_NUM, key=len, reverse=True)) + r")"
    rf"(\s+(?:{_TIME_UNIT_WORDS}))\b",
    re.IGNORECASE,
)


def _normalize_word_numbers(text: str) -> str:
    """"in one minute" / "a couple minutes" / "in twenty mins" -> "in 1
    minute" / "a 2 minutes" / "in 20 mins", so TIME_IN/DURATION_ONLY
    above can parse them with the exact same digit-only regex used for
    "in 30 minutes". See the block comment above _WORD_TO_NUM for why
    this only ever touches a number word directly glued to a time unit.
    """

    def _replace(match: re.Match) -> str:
        return _WORD_TO_NUM[match.group(1).lower()] + match.group(2)

    return _WORD_NUMBER_BEFORE_UNIT_RE.sub(_replace, text)


def _strip_trigger(text: str, trigger: re.Pattern) -> str:
    return trigger.sub("", text, count=1).strip(" ,.!")


def _resolve_time_from_parts(hour: int, minute: int, meridiem: str, text: str, now: datetime) -> datetime:
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour < 8:
        # Bare small hours like "at 7" during evening planning -> assume PM,
        # matches how people actually talk to a desktop pet. Not perfect,
        # but transparent and easy to override by typing "7 am"/"19:00".
        hour += 12

    # Resolve the target *date* first, then apply the clock time to it -
    # NOT the other way around. The previous version applied today's date,
    # rolled forward a day whenever the clock time had already passed
    # today, and THEN separately added another day for "tomorrow" - so
    # "tomorrow at 5pm" typed after 5pm today ("5pm today already passed"
    # -> +1 day, PLUS "contains tomorrow" -> +1 day again) landed on the
    # day after tomorrow instead of tomorrow. An explicit "tomorrow"
    # always means "the next calendar day", full stop, regardless of what
    # time it is right now.
    explicit_tomorrow = "tomorrow" in text.lower()
    target_date = (now + timedelta(days=1)).date() if explicit_tomorrow else now.date()
    due = datetime.combine(target_date, time_of_day(hour=hour % 24, minute=minute))
    # Only roll forward to the next day when no explicit date word was
    # given and the clock time has already passed today - "at 7" said at
    # 9pm should mean tomorrow morning, but "tomorrow at 5pm" already has
    # its date pinned down above and must never roll again here.
    if not explicit_tomorrow and due <= now:
        due += timedelta(days=1)
    return due


def _parse_absolute_time(text: str, now: datetime) -> Optional[datetime]:
    match = TIME_AT.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    return _resolve_time_from_parts(hour, minute, meridiem, text, now)


# Bare clock time with no leading "at" - only used by the reschedule-
# reference path ("make it 8" / "change it to 8:30pm"). The "make it"/
# "change it to" trigger phrase itself already establishes that a time
# follows, so requiring the word "at" too would make an already-terse
# follow-up message even more awkward to type than it needs to be.
BARE_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)


def _parse_bare_time(text: str, now: datetime) -> Optional[datetime]:
    match = BARE_TIME.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    if hour > 23:
        return None  # not a plausible clock hour - avoid misreading a stray number
    minute = int(match.group(2) or 0)
    if minute > 59:
        return None
    meridiem = (match.group(3) or "").lower()
    return _resolve_time_from_parts(hour, minute, meridiem, text, now)


def _parse_relative_minutes(text: str) -> Optional[int]:
    match = TIME_IN.search(_normalize_word_numbers(text))
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    return amount * 60 if unit.startswith(("hour", "hr")) else amount


def _parse_duration_seconds(text: str) -> Optional[int]:
    match = DURATION_ONLY.search(_normalize_word_numbers(text))
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith(("hour", "hr")):
        return amount * 3600
    if unit.startswith(("min",)):
        return amount * 60
    return amount


# Filler words stripped when pulling a timer's purpose out of the rest of
# the sentence (conversational-issues report P0: "Preserve Timer
# Purpose/Label Information" - "set 10 second timer to remind me to pick
# my columns" used to keep the duration but silently discard "pick my
# columns"). Deliberately excludes "the" - unlike "a"/"for" (which are
# needed so a purpose-less "timer for 10 minutes" correctly reduces to
# nothing, see _timer_label_from() below), "the" is common inside a real
# purpose ("water the plants") and stripping it would mangle it.
_TIMER_FILLER = re.compile(
    r"\b(can you|could you|would you|please|set(?:\s+up)?|start|a|"
    r"countdown|for|to remind me(?:\s+to)?|remind me(?:\s+to)?)\b",
    re.IGNORECASE,
)


# Duration-phrase stripper for _timer_label_from() below. Deliberately a
# separate pattern from DURATION_ONLY (used for the actual duration
# parsing) rather than reusing it directly: DURATION_ONLY's alternation
# lists "minute" before "minutes", and without a trailing \b the engine
# accepts the "minute" prefix match against "minutes" and stops there,
# leaving a stray "s" behind - harmless for parsing the number itself,
# but that stray "s" would otherwise survive into the extracted label.
_DURATION_STRIP = re.compile(
    r"\d+\s*(?:minutes|minute|mins|min|hours|hour|hrs|hr|seconds|second|secs|sec)\b",
    re.IGNORECASE,
)


def _timer_label_from(text: str) -> Optional[str]:
    """Pulls the purpose out of a timer request, e.g. "set 10 second
    timer to remind me to pick my columns" -> "Pick my columns". Returns
    None when the request names no purpose beyond the duration/trigger
    words themselves (e.g. "timer for 10 minutes"), so the caller can
    fall back to the existing generic "Timer" label instead of inventing
    one - required acceptance criterion: "No invented purpose is added
    when none was provided."."""
    cleaned = _normalize_word_numbers(text)
    cleaned = _DURATION_STRIP.sub("", cleaned)
    cleaned = re.sub(rf"\b{_TIMER_WORD}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = _TIMER_FILLER.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?")
    cleaned = re.sub(r"^to\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return None
    return cleaned[:1].upper() + cleaned[1:]


def _title_from(text: str, fallback: str = "Reminder") -> str:
    # Drop a trailing time clause so "call mom at 7pm" -> "call mom" -
    # normalize word-numbers first so "in one minute" strips the same way
    # "in 1 minute" already did (otherwise "one minute" would be left
    # stuck in the title, since TIME_IN's regex only ever matches digits).
    cleaned = _normalize_word_numbers(text)
    cleaned = TIME_AT.sub("", cleaned)
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
    #
    # LIST_DONE_TRIGGER goes first of all of these: "what tasks are done"
    # contains both "what tasks" (which LIST_TASKS_TRIGGER below would
    # also match) and "done" - checking the done-specific query first is
    # what makes it answer with *finished* items instead of open ones.
    if LIST_DONE_TRIGGER.search(lowered):
        return DetectedIntent(
            name="query_done",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine fills this in from the real archive
            tool_args={"query": text},
        )
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
    if LIST_TIMERS_TRIGGER.search(lowered):
        return DetectedIntent(
            name="list_timers",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",  # chat_engine fills this in from the real timer list
            tool="list_timers",
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
    if RESCHEDULE_TRIGGER.search(lowered):
        body = _strip_trigger(text, RESCHEDULE_TRIGGER)
        # Relative ("in 20 minutes") is checked first and, if it matches,
        # bare-time parsing is skipped entirely - BARE_TIME has no "at"
        # requirement and would otherwise misread the "20" in "in 20
        # minutes" as a clock hour.
        minutes = _parse_relative_minutes(body)
        due = _parse_absolute_time(body, now)
        if due is None and minutes is None:
            due = _parse_bare_time(body, now)
        if due is None and minutes is not None:
            due = now + timedelta(minutes=minutes)
        if due is None:
            return DetectedIntent(
                name="reschedule_reference_needs_time",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                response='Change it to when? Try a time like "8pm" or "in 20 minutes".',
            )
        return DetectedIntent(
            name="reschedule_reference",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            response="",  # chat_engine fills this in once it resolves which entity "it" means
            tool_args={"due_iso": due.isoformat()},
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
        label = _timer_label_from(text) or "Timer"
        purpose_note = f" - I'll remind you to {label.lower()}" if label != "Timer" else ""
        return DetectedIntent(
            name="start_timer",
            emotion=Emotion.EXCITED,
            animation=CharacterState.EXCITED,
            sound="chirp",
            response=f"Timer started for {seconds // 60 or seconds}"
            f"{' min' if seconds >= 60 else ' sec'}!{purpose_note}",
            tool="start_timer",
            tool_args={"duration_seconds": seconds, "label": label},
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
            tool_args={"query": _extract_reference_target(text)},
        )
    if AMBIGUOUS_CANCEL_TRIGGER.search(lowered):
        return DetectedIntent(
            name="cancel_ambiguous",
            emotion=Emotion.NEUTRAL,
            animation=CharacterState.IDLE,
            response="",
            tool_args={"query": _extract_reference_target(text)},
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


# ---------------------------------------------------------------------------
# Hybrid semantic fallback (see app/ai/semantic_intent.py)
# ---------------------------------------------------------------------------


def build_semantic_intent(name: str, raw_text: str, now: Optional[datetime] = None) -> Optional[DetectedIntent]:
    """Build the same structured DetectedIntent contract detect_intent()
    produces above, but for a message the keyword pass above did NOT
    recognize (it fell all the way through to "unknown") and that
    app/ai/semantic_intent.py's model classified as `name` based on
    MEANING rather than exact phrasing - e.g. "don't let this slip my
    mind, dentist thing at 4" has none of REMINDER_TRIGGER's literal
    words, so detect_intent() above never matches it, but it is
    unambiguously a create_reminder.

    Deliberately reuses the EXACT SAME deterministic entity-extraction
    helpers the keyword path above uses (_parse_absolute_time /
    _parse_relative_minutes / _parse_duration_seconds / _title_from) -
    the semantic layer only ever decides WHICH bucket a message belongs
    to; it never invents the actual title/time/duration values that get
    written to the database. This is spec section 60's rule ("the LLM
    should reason about actions; it should not be trusted to directly
    perform actions") applied to intent detection itself: a small local
    model proposes a category, and this function's own regex-based
    parsing - the same parsing already trusted for the keyword path - is
    what actually turns that into a validated tool call.

    Returns None if `name` isn't one this function knows how to build
    (defensive - app/ai/semantic_intent.ALLOWED_INTENTS should always
    match what's handled here). Returns a "*_needs_time"/"*_needs_duration"
    DetectedIntent (same shape detect_intent() itself returns) if the
    semantic layer's category was right but no actual time/duration could
    be found in the text - callers should treat that as a clarifying
    question, never guess a default value silently.
    """
    now = now or datetime.now()
    text = raw_text.strip()

    if name == "create_reminder":
        due = _parse_absolute_time(text, now)
        minutes = _parse_relative_minutes(text)
        if due is None and minutes is not None:
            due = now + timedelta(minutes=minutes)
        title = _title_from(text)
        if due is None:
            return DetectedIntent(
                name="create_reminder_needs_time",
                emotion=Emotion.CONFUSED,
                animation=CharacterState.CONFUSED,
                response=(
                    f"Sounds like you want a reminder for \"{title}\" - but when? "
                    "Try \"at 7pm\" or \"in 30 minutes\"."
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

    if name == "start_timer":
        seconds = _parse_duration_seconds(text.lower())
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

    if name == "create_task":
        due = _parse_absolute_time(text, now)
        minutes = _parse_relative_minutes(text)
        if due is None and minutes is not None:
            due = now + timedelta(minutes=minutes)
        title = _title_from(text, fallback="New task")
        due_note = f" (due {due:%I:%M %p})" if due else ""
        tool_args: dict = {"title": title}
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

    if name in ("list_reminders", "list_tasks", "list_timers"):
        return DetectedIntent(
            name=name,
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            response="",
            tool=name,
        )

    if name in ("complete_ambiguous", "cancel_ambiguous"):
        return DetectedIntent(
            name=name,
            emotion=Emotion.HAPPY if name == "complete_ambiguous" else Emotion.NEUTRAL,
            animation=CharacterState.HAPPY if name == "complete_ambiguous" else CharacterState.IDLE,
            response="",
            tool_args={},
        )

    return None
