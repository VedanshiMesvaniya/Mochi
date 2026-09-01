from __future__ import annotations

from dataclasses import dataclass

from app.ai import conversation_state as cs


@dataclass
class _Item:
    id: int
    title: str


def test_is_bare_reference_recognizes_common_pronouns():
    for phrase in ("it", "that", "this", "that one", "the last one", ""):
        assert cs.is_bare_reference(phrase)


def test_is_bare_reference_does_not_swallow_real_titles():
    assert not cs.is_bare_reference("call mom")
    assert not cs.is_bare_reference("buy milk")


def test_ordinal_index_resolves_common_phrasings():
    assert cs.ordinal_index("the first one", count=3) == 0
    assert cs.ordinal_index("the second one", count=3) == 1
    assert cs.ordinal_index("third one", count=3) == 2
    assert cs.ordinal_index("the last one", count=3) == 2


def test_ordinal_index_out_of_range_returns_none():
    assert cs.ordinal_index("the third one", count=2) is None
    assert cs.ordinal_index("the fifth one", count=0) is None


def test_ordinal_index_non_ordinal_text_returns_none():
    assert cs.ordinal_index("call mom", count=3) is None


def test_remember_entity_shape():
    state = cs.remember_entity("task", 7, "Buy milk")
    assert state == {
        "entity_type": "task",
        "entity_id": 7,
        "entity_title": "Buy milk",
        "candidates": None,
    }


def test_remember_candidates_shape():
    state = cs.remember_candidates("task", [(1, "Buy milk"), (2, "Call mom")])
    assert state["candidates"] == [
        {"id": 1, "title": "Buy milk"},
        {"id": 2, "title": "Call mom"},
    ]


def test_resolve_bare_reference_finds_remembered_entity():
    state = cs.remember_entity("task", 2, "Buy milk")
    candidates = [_Item(1, "Call mom"), _Item(2, "Buy milk")]
    assert cs.resolve("it", state, candidates) is candidates[1]


def test_resolve_bare_reference_misses_when_entity_no_longer_present():
    # Simulates the referenced task having been completed/deleted through
    # some other path since it was remembered - must fall back to None,
    # never guess at something else.
    state = cs.remember_entity("task", 99, "Ghost task")
    candidates = [_Item(1, "Call mom")]
    assert cs.resolve("it", state, candidates) is None


def test_resolve_ordinal_finds_matching_candidate():
    state = cs.remember_candidates("task", [(1, "Call mom"), (2, "Buy milk")])
    candidates = [_Item(1, "Call mom"), _Item(2, "Buy milk")]
    assert cs.resolve("the second one", state, candidates) is candidates[1]


def test_resolve_real_title_is_not_treated_as_a_reference():
    state = cs.remember_entity("task", 2, "Buy milk")
    candidates = [_Item(1, "Call mom"), _Item(2, "Buy milk")]
    # A real search query should never be swallowed by reference
    # resolution - the caller's normal fuzzy search handles this case.
    assert cs.resolve("call mom", state, candidates) is None


def test_resolve_with_no_state_returns_none():
    candidates = [_Item(1, "Call mom")]
    assert cs.resolve("it", None, candidates) is None


def test_resolve_typed_matches_type_and_id():
    state = cs.remember_entity("reminder", 5, "Call mom")
    combined = [("task", _Item(5, "Different task, same id")), ("reminder", _Item(5, "Call mom"))]
    kind, item = cs.resolve_typed("it", state, combined)
    assert kind == "reminder"
    assert item.title == "Call mom"


def test_resolve_typed_does_not_cross_match_same_id_different_type():
    state = cs.remember_entity("reminder", 5, "Call mom")
    combined = [("task", _Item(5, "Unrelated task"))]
    assert cs.resolve_typed("it", state, combined) is None
