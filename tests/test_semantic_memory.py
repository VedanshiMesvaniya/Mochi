from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.exceptions import MemoryDisabled
from app.memory import semantic_memory as sm


def test_remember_fact_stores_and_returns_it(temp_db):
    fact = sm.remember_fact("User lives in Austin.", subject="lives_in", confidence=0.85)
    assert fact.text == "User lives in Austin."
    assert fact.subject == "lives_in"
    assert fact.status == sm.STATUS_ACTIVE


def test_remember_fact_rejects_empty_text(temp_db):
    with pytest.raises(ValueError):
        sm.remember_fact("   ")


def test_remember_fact_supersedes_same_subject(temp_db):
    old = sm.remember_fact("User uses Windows.", subject="uses:operating_system", confidence=0.8)
    new = sm.remember_fact("User switched to Linux.", subject="uses:operating_system", confidence=0.8)

    active = sm.list_facts(active_only=True)
    assert [f.id for f in active] == [new.id]

    everything = sm.list_facts(active_only=False)
    by_id = {f.id: f for f in everything}
    assert by_id[old.id].status == sm.STATUS_SUPERSEDED
    assert by_id[new.id].status == sm.STATUS_ACTIVE


def test_remember_fact_without_subject_never_supersedes_anything(temp_db):
    sm.remember_fact("User once mentioned liking rainy days.", subject=None)
    sm.remember_fact("User once mentioned a trip to Japan.", subject=None)

    active = sm.list_facts(active_only=True)
    assert len(active) == 2
    # Each open-ended note gets its own unique subject.
    assert len({f.subject for f in active}) == 2


def test_remember_fact_different_subjects_do_not_collide(temp_db):
    sm.remember_fact("User is allergic to peanuts.", subject="allergic_to:peanuts", confidence=0.9)
    sm.remember_fact("User is allergic to shellfish.", subject="allergic_to:shellfish", confidence=0.9)

    active = sm.list_facts(active_only=True)
    assert len(active) == 2


def test_list_facts_active_only_excludes_superseded(temp_db):
    sm.remember_fact("User uses Windows.", subject="uses:os")
    sm.remember_fact("User uses Linux.", subject="uses:os")

    assert len(sm.list_facts(active_only=True)) == 1
    assert len(sm.list_facts(active_only=False)) == 2


def test_forget_fact_deletes_it(temp_db):
    fact = sm.remember_fact("User lives in Austin.", subject="lives_in")
    assert sm.forget_fact(fact.id) is True
    assert sm.list_facts(active_only=False) == []


def test_forget_fact_returns_false_when_nothing_to_delete(temp_db):
    assert sm.forget_fact(9999) is False


def test_find_relevant_ranks_by_keyword_overlap(temp_db):
    sm.remember_fact("User lives in Austin.", subject="lives_in")
    sm.remember_fact("User's favorite color is teal.", subject="favorite:color")

    results = sm.find_relevant("do you know anything about austin")
    assert len(results) == 1
    assert "Austin" in results[0].text


def test_find_relevant_returns_empty_list_when_nothing_overlaps(temp_db):
    sm.remember_fact("User lives in Austin.", subject="lives_in")
    assert sm.find_relevant("what's the weather like") == []


def test_find_matching_is_substring_based(temp_db):
    sm.remember_fact("User lives in Austin.", subject="lives_in")
    assert len(sm.find_matching("austin")) == 1
    assert len(sm.find_matching("lives_in")) == 1  # subject also searched
    assert sm.find_matching("nonexistent") == []


def test_memory_disabled_raises_on_every_entry_point(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    with pytest.raises(MemoryDisabled):
        sm.remember_fact("User lives in Austin.")
    with pytest.raises(MemoryDisabled):
        sm.list_facts()
    with pytest.raises(MemoryDisabled):
        sm.forget_fact(1)
