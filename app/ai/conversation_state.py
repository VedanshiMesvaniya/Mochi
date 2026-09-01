"""
Deterministic conversational reference resolution.

Security review finding I1/I3 ("the biggest intelligence gap remaining"):
Mochi previously had no structured memory of "the thing we were just
talking about". A message like

    User: add task buy milk
    Mochi: Noted!
    User: actually delete it

fell back to the same fuzzy title search used for a fresh command, which
only works when there's exactly one open task/reminder/timer in the whole
app - with two or more open items it always asked "which one?", even
though a human reading the same transcript would obviously know "it"
means the task literally just created a message ago.

This module is deliberately NOT the model doing entity resolution - it's a
tiny, fully deterministic piece of state app/ai/chat_engine.py threads
between handle_message() calls, owned by the caller exactly the way
`pending_action` already is (see app/ui/chat_window.py's `_pending_action`
- `_conversation_state` follows the identical pattern: read in, acted on,
written back, reset to None when the chat window closes). The model is
still only ever asked WHICH INTENT ("complete_ambiguous", "reschedule_reference",
...) - this module is what lets the deterministic layer know WHICH ENTITY
to act on when the message doesn't repeat a title, by remembering the
single most recent thing that was created, resolved, or listed.

Deliberately conservative by design (spec section 41: never guess on a
destructive action): resolution only ever succeeds when it can point to
one exact, still-real database row - a bare reference that doesn't match
anything in `state`, or a real object that's since been completed/deleted
elsewhere, resolves to None and the caller falls back to asking, exactly
like it already did before this module existed.
"""

from __future__ import annotations

import re
from typing import Optional

# Words/phrases that refer back to "the thing we were just talking about"
# rather than naming anything new. Deliberately narrow - anything not in
# this set is treated as a normal search query against real titles, same
# as before this module existed, so this can never accidentally swallow a
# real title that happens to be short (e.g. a task literally titled "It").
_BARE_REFERENCES = {
    "",
    "it",
    "that",
    "this",
    "that one",
    "this one",
    "the same one",
    "same one",
    "same",
    "the last one",
    "the one i just made",
    "the one i just added",
    "the one i just set",
}

# "the Nth one" -> 0-based index into the most recent ordered list of
# candidates (the results of a list_* query, most-recently-shown first -
# see remember_candidates()).
_ORDINAL_WORDS = {
    "first": 0, "1st": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
}
_ORDINAL_PATTERN = re.compile(
    r"\b(?:the\s+)?(" + "|".join(_ORDINAL_WORDS) + r")\s+one\b", re.IGNORECASE
)
_LAST_PATTERN = re.compile(r"\bthe\s+last\s+one\b", re.IGNORECASE)


def is_bare_reference(query: str) -> bool:
    """True if `query` doesn't actually name anything - it's just a
    pronoun/placeholder standing in for whatever was last discussed."""
    return query.strip().lower() in _BARE_REFERENCES


def ordinal_index(query: str, count: int) -> Optional[int]:
    """0-based index `query` refers to among `count` candidates ("the
    second one" -> 1), or None if `query` isn't an ordinal reference, or
    if it names an index that doesn't exist among `count` candidates."""
    if count <= 0:
        return None
    lowered = query.strip().lower()
    if _LAST_PATTERN.search(lowered):
        return count - 1
    match = _ORDINAL_PATTERN.search(lowered)
    if not match:
        return None
    index = _ORDINAL_WORDS[match.group(1).lower()]
    return index if index < count else None


def remember_entity(entity_type: str, entity_id: int, entity_title: str) -> dict:
    """New conversation_state after creating/completing/cancelling/
    rescheduling, or unambiguously looking up, one specific entity - what
    "it"/"that" should resolve to on the very next turn. `entity_type` is
    one of "task"/"reminder"/"timer"."""
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_title": entity_title,
        "candidates": None,
    }


def remember_candidates(entity_type: str, items: list[tuple[int, str]]) -> dict:
    """New conversation_state after a list_* query - what "the first
    one"/"the second one" should resolve to on the next turn. `items`
    must be plain (id, title) pairs in the exact order actually shown to
    the user."""
    return {
        "entity_type": entity_type,
        "entity_id": None,
        "entity_title": None,
        "candidates": [{"id": item_id, "title": title} for item_id, title in items],
    }


def resolve(query: str, state: Optional[dict], candidates: list, id_attr: str = "id"):
    """Resolve a bare/ordinal reference in `query` against `state`
    (this module's own dict shape) restricted to `candidates` - real,
    currently-valid objects of the matching type. Returns the matching
    object from `candidates`, or None if `query` isn't a reference this
    module recognises, `state` has nothing relevant, or the referenced
    entity isn't (or is no longer) among `candidates` - e.g. it was
    already completed/deleted through some other path in the meantime, in
    which case the caller must fall back to its normal "which one?"
    handling rather than silently acting on a stale id.
    """
    if not state or not candidates:
        return None

    if is_bare_reference(query) and state.get("entity_id") is not None:
        target_id = state["entity_id"]
        for item in candidates:
            if getattr(item, id_attr) == target_id:
                return item
        return None

    ordinal_candidates = state.get("candidates")
    if ordinal_candidates:
        index = ordinal_index(query, len(ordinal_candidates))
        if index is not None:
            target_id = ordinal_candidates[index]["id"]
            for item in candidates:
                if getattr(item, id_attr) == target_id:
                    return item
    return None


def resolve_typed(query: str, state: Optional[dict], combined: list[tuple[str, object]], id_attr: str = "id"):
    """Same idea as `resolve()`, but for the cross-type "it"/"that"/"the
    second one" case (_complete_ambiguous_reaction/_cancel_ambiguous_reaction
    in chat_engine.py, where the candidate list mixes tasks/reminders/
    timers together as (kind, item) pairs) - the referenced entity must
    match both the remembered type AND id, not just the id, since a task
    and a reminder could coincidentally share a database id. Ordinal
    references ("the second one") only resolve when `state` came from a
    single-type list (list_tasks/list_reminders/list_timers all remember
    candidates of one type) - a mixed "which one?" prompt from this
    module's own caller doesn't produce a candidate list to begin with."""
    if not state or not combined:
        return None
    target_type = state.get("entity_type")

    if is_bare_reference(query) and state.get("entity_id") is not None:
        target_id = state["entity_id"]
        for kind, item in combined:
            if kind == target_type and getattr(item, id_attr) == target_id:
                return kind, item
        return None

    ordinal_candidates = state.get("candidates")
    if ordinal_candidates:
        index = ordinal_index(query, len(ordinal_candidates))
        if index is not None:
            target_id = ordinal_candidates[index]["id"]
            for kind, item in combined:
                if kind == target_type and getattr(item, id_attr) == target_id:
                    return kind, item
    return None
