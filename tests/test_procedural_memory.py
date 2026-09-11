from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.exceptions import MemoryDisabled
from app.memory import procedural_memory as pm


def test_learn_rule_stores_and_returns_it(temp_db):
    rule = pm.learn_rule("Calendar auth can expire.", scope="calendar")
    assert rule.rule == "Calendar auth can expire."
    assert rule.scope == "calendar"
    assert rule.trigger_count == 1


def test_learn_rule_deduplicates_identical_rule_and_scope(temp_db):
    first = pm.learn_rule("Calendar auth can expire.", scope="calendar")
    second = pm.learn_rule("Calendar auth can expire.", scope="calendar")

    assert first.id == second.id
    assert second.trigger_count == 2


def test_learn_rule_same_text_different_scope_is_a_separate_rule(temp_db):
    pm.learn_rule("Sign-in can expire.", scope="calendar")
    pm.learn_rule("Sign-in can expire.", scope="google_tasks")

    calendar_rules = pm.relevant_rules("calendar")
    tasks_rules = pm.relevant_rules("google_tasks")
    assert len(calendar_rules) == 1
    assert len(tasks_rules) == 1


def test_relevant_rules_orders_by_trigger_count(temp_db):
    pm.learn_rule("Rule A", scope="calendar")
    pm.learn_rule("Rule B", scope="calendar")
    pm.learn_rule("Rule B", scope="calendar")  # triggered twice

    rules = pm.relevant_rules("calendar")
    assert rules[0].rule == "Rule B"
    assert rules[0].trigger_count == 2


def test_relevant_rules_returns_empty_for_unknown_scope(temp_db):
    pm.learn_rule("Rule A", scope="calendar")
    assert pm.relevant_rules("nonexistent_scope") == []


def test_has_recurring_issue_none_below_threshold(temp_db):
    pm.learn_rule("Rule A", scope="calendar")  # only triggered once
    assert pm.has_recurring_issue("calendar") is None


def test_has_recurring_issue_returns_rule_at_threshold(temp_db):
    pm.learn_rule("Rule A", scope="calendar")
    pm.learn_rule("Rule A", scope="calendar")  # second trigger

    recurring = pm.has_recurring_issue("calendar")
    assert recurring is not None
    assert recurring.rule == "Rule A"


def test_has_recurring_issue_none_for_scope_with_nothing_stored(temp_db):
    assert pm.has_recurring_issue("calendar") is None


def test_learn_rule_returns_none_when_memory_disabled(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    assert pm.learn_rule("Rule A", scope="calendar") is None


def test_has_recurring_issue_never_raises_when_memory_disabled(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    assert pm.has_recurring_issue("calendar") is None  # must not raise


def test_relevant_rules_raises_when_memory_disabled(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_enabled", False)
    with pytest.raises(MemoryDisabled):
        pm.relevant_rules("calendar")


def test_learn_rule_never_raises_even_on_bad_input(temp_db, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(pm, "get_connection", _boom)
    assert pm.learn_rule("Rule A", scope="calendar") is None
