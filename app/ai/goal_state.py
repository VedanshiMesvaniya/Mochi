"""
Deterministic active-goal state for single-slot clarification loops
(Cognitive Upgrade spec sections 3-4: "Active Conversation State" /
"Goal Stack").

The gap this closes: a message like

    User: schedule something with Devika tomorrow
    Mochi: Got it - "Meeting with Devika" - but when?
    User: 5

used to fail, because "5" alone matches none of app/ai/intent.py's
creation triggers - it was processed as a brand-new, unrelated message
and fell through to "unknown". A human reading the same transcript would
obviously know "5" answers the question Mochi itself just asked.

This module is deliberately NOT the model deciding what the missing slot
means - it's a tiny, fully deterministic piece of state
app/ai/chat_engine.py threads between handle_message() calls, owned by
the caller exactly the way `pending_action` and `conversation_state`
already are (see app/ui/chat_window.py's `_pending_action`/
`_conversation_state` - `_active_goal` follows the identical pattern:
read in, acted on, written back, reset to None whenever it's not
freshly re-issued). The actual slot value is still only ever parsed by
app/ai/intent.py's existing deterministic time/duration parsers (see
resolve_pending_goal() there) - this module only remembers WHICH intent
is waiting and WHAT was already extracted before the question was asked.

Phase 1 scope (deliberate, documented decision - see docs/ROADMAP.md's
Cognitive Upgrade entry): exactly one missing slot per goal - time, for
create_reminder/calendar_create_event/reschedule_reference, or duration,
for start_timer. This covers every "*_needs_time"/"*_needs_duration"
DetectedIntent app/ai/intent.py currently produces. A goal with more than
one simultaneously-missing slot (the fuller multi-slot example from spec
section 4) is intentionally NOT implemented here, since no current
Mochi intent actually needs more than one clarifying question in a row -
building that machinery without a real caller would be speculative and
untested. Extending `known_slots`/`awaiting` to a list rather than a
single string is the natural next step if/when a multi-slot flow is
added.

Like `pending_action`, an active_goal never survives an unrelated
message: app/ai/chat_engine.handle_message() only ever keeps one around
by explicitly re-issuing it (via a fresh "*_needs_*" intent), and drops
it the instant app/ai/intent.resolve_pending_goal() can't make sense of
the reply - never lets a stale clarifying question outlive the
conversation that asked it (spec section 27: "never guess on a
consequential action").
"""

from __future__ import annotations

from typing import Optional

# The one missing slot a goal can be waiting on (Phase 1 scope - see
# module docstring above).
_VALID_AWAITING = {"time", "duration"}

# Which "*_needs_*" DetectedIntent.name app/ai/intent.py can produce maps
# to which intent this goal should resume as once its slot is filled, and
# which kind of value it's waiting on. Single source of truth shared by
# every caller (chat_engine.py) rather than re-deriving it ad hoc.
GOAL_TRIGGERS: dict[str, tuple[str, str]] = {
    "calendar_create_needs_time": ("calendar_create_event", "time"),
    "create_reminder_needs_time": ("create_reminder", "time"),
    "reschedule_reference_needs_time": ("reschedule_reference", "time"),
    "create_timer_needs_duration": ("start_timer", "duration"),
}


def create_goal(kind: str, awaiting: str, known_slots: Optional[dict] = None) -> dict:
    """New active_goal after a "*_needs_time"/"*_needs_duration" intent.

    `kind` is the intent name completion should resume as once the slot
    is filled (see GOAL_TRIGGERS above). `awaiting` is which single slot
    value is still missing ("time" or "duration"). `known_slots` is
    whatever was already extracted before the clarifying question was
    asked (e.g. {"title": "Meeting with Devika"}) - carried in the
    triggering DetectedIntent's own `tool_args`, which is otherwise
    unused for a "*_needs_*" intent (it never has a `tool` to run).
    """
    if awaiting not in _VALID_AWAITING:
        raise ValueError(f"Unknown awaiting slot: {awaiting!r}")
    return {
        "kind": kind,
        "awaiting": awaiting,
        "known_slots": dict(known_slots or {}),
    }


def from_intent_name(intent_name: str, known_slots: Optional[dict] = None) -> Optional[dict]:
    """create_goal(), looked up by the triggering "*_needs_*" intent name
    via GOAL_TRIGGERS. Returns None if `intent_name` isn't one that opens
    a goal (the normal case for almost every message) - callers should
    only ever store the result when it isn't None."""
    trigger = GOAL_TRIGGERS.get(intent_name)
    if trigger is None:
        return None
    kind, awaiting = trigger
    return create_goal(kind, awaiting, known_slots)


def is_goal(value: Optional[dict]) -> bool:
    """True if `value` is a well-formed active_goal this module produced
    (as opposed to None, {}, or some unrelated dict)."""
    return bool(value) and "kind" in value and "awaiting" in value
