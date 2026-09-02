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

# --- Multi-target selection ("three of them", "all of them", "both",
# "the first three", "the last two") - conversational-issues report P0
# ("Add Multi-Target Conversational Entity Resolution"): the patterns
# above only ever resolve to ONE entity. Real requests like "three of
# them check as done" need to resolve to several entities at once.
#
# `MULTI_REFERENCE_SRC` is a regex *source string* (not compiled) rather
# than kept private, because app/ai/intent.py's AMBIGUOUS_DONE_TRIGGER/
# AMBIGUOUS_CANCEL_TRIGGER need to recognise the exact same phrasing to
# route the message here in the first place - one definition shared by
# both the trigger and the resolver, so they can never drift apart.
#
# Deliberately excludes singular forms ("first one"/"last one"/"one of
# them") - those already resolve to a single entity via ordinal_index()/
# resolve() above, and must keep doing exactly that rather than becoming
# a one-item selection with different response wording.
_NUMBER_WORD = r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten)"
MULTI_REFERENCE_SRC = (
    r"\b(?:all\s+of\s+them|all|both|"
    rf"(?:the\s+)?first\s+{_NUMBER_WORD}|"
    rf"(?:the\s+)?last\s+{_NUMBER_WORD}|"
    rf"{_NUMBER_WORD}\s+of\s+(?:them|those|these))\b"
)

_WORD_TO_INT = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _to_int(word: str) -> Optional[int]:
    word = word.lower()
    if word.isdigit():
        return int(word)
    return _WORD_TO_INT.get(word)


def parse_selection(query: str) -> Optional[dict]:
    """Parse a multi-target reference into a selection descriptor:
    `{"type": "all"}`, `{"type": "both"}`, `{"type": "first_n", "n": 3}`,
    or `{"type": "last_n", "n": 2}`. Returns None if `query` isn't a
    multi-target reference this module recognises. Does not validate the
    count against how many candidates actually exist - see
    resolve_selection()/resolve_selection_typed(), which is where that
    "never guess on an out-of-range quantity" check happens, since only
    those functions know how many candidates are actually remembered."""
    lowered = query.strip().lower()
    if not lowered:
        return None
    if re.search(r"\ball\b", lowered):
        return {"type": "all"}
    if re.search(r"\bboth\b", lowered):
        return {"type": "both"}
    match = re.search(rf"\bfirst\s+({_NUMBER_WORD})\b", lowered)
    if match:
        n = _to_int(match.group(1))
        return {"type": "first_n", "n": n} if n and n >= 2 else None
    match = re.search(rf"\blast\s+({_NUMBER_WORD})\b", lowered)
    if match:
        n = _to_int(match.group(1))
        return {"type": "last_n", "n": n} if n and n >= 2 else None
    match = re.search(rf"\b({_NUMBER_WORD})\s+of\s+(?:them|those|these)\b", lowered)
    if match:
        n = _to_int(match.group(1))
        return {"type": "first_n", "n": n} if n and n >= 2 else None
    return None


def _pick_selection(selection: dict, remembered: list[dict]) -> Optional[list[dict]]:
    """Applies a parsed `selection` descriptor to the remembered candidate
    list, returning the chosen (id, title) dicts, or None if the request
    doesn't fit how many candidates are actually remembered - e.g. "the
    first three" when only two were ever shown, or "both" when the
    remembered list has three items. Never silently clamps the count."""
    sel_type = selection["type"]
    count = len(remembered)
    if sel_type == "all":
        return remembered
    if sel_type == "both":
        return remembered if count == 2 else None
    if sel_type == "first_n":
        n = selection["n"]
        return remembered[:n] if 0 < n <= count else None
    if sel_type == "last_n":
        n = selection["n"]
        return remembered[-n:] if 0 < n <= count else None
    return None


def resolve_selection(query: str, state: Optional[dict], candidates: list, id_attr: str = "id") -> Optional[list]:
    """Multi-target counterpart to resolve() above - resolves references
    like "three of them"/"all of them"/"the first three" against `state`'s
    remembered candidate list, restricted to `candidates` - real,
    currently-valid objects of the matching type. Returns the list of
    matched objects (in remembered order), or None if `query` isn't a
    multi-target reference, `state` has no remembered candidate list, the
    requested quantity doesn't fit how many candidates were remembered, or
    none of the remembered candidates are still present in `candidates`
    (e.g. everything referenced was already completed/deleted elsewhere -
    same "fall back to asking" rule as resolve())."""
    if not state or not candidates:
        return None
    remembered = state.get("candidates")
    if not remembered:
        return None
    selection = parse_selection(query)
    if selection is None:
        return None
    chosen = _pick_selection(selection, remembered)
    if chosen is None:
        return None
    by_id = {getattr(item, id_attr): item for item in candidates}
    resolved = [by_id[c["id"]] for c in chosen if c["id"] in by_id]
    return resolved or None


def resolve_selection_typed(query: str, state: Optional[dict], combined: list[tuple[str, object]], id_attr: str = "id") -> Optional[list]:
    """Same idea as resolve_selection(), but for the cross-type "three of
    them"/"all of them" case used by _complete_ambiguous_reaction/
    _cancel_ambiguous_reaction in chat_engine.py, where `combined` mixes
    tasks/reminders/timers together as (kind, item) pairs. `state`'s
    remembered candidates always come from a single-type list_* query
    (see remember_candidates()), so every chosen item shares the same
    remembered `entity_type` - the same restriction resolve_typed() above
    already applies to ordinal references."""
    if not state or not combined:
        return None
    remembered = state.get("candidates")
    if not remembered:
        return None
    selection = parse_selection(query)
    if selection is None:
        return None
    chosen = _pick_selection(selection, remembered)
    if chosen is None:
        return None
    target_type = state.get("entity_type")
    by_id = {
        getattr(item, id_attr): (kind, item)
        for kind, item in combined
        if kind == target_type
    }
    resolved = [by_id[c["id"]] for c in chosen if c["id"] in by_id]
    return resolved or None


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
