"""Coverage for app/ai/db_glossary.py's synonym matching - the glossary
that turns a person's own words ("what's left on my to-do list", "show
completed reminders") into a real (entity, status) query plan, per the
spec follow-up asking for a "universal" synonym glossary over Mochi's
database (see that module's docstring for the full design reasoning)."""

from __future__ import annotations

from app.ai import db_glossary


def test_match_entity_recognizes_task_synonyms():
    assert db_glossary.match_entity("what's on my todo list") == "tasks"
    assert db_glossary.match_entity("any chores left") == "tasks"


def test_match_entity_recognizes_reminder_synonyms():
    assert db_glossary.match_entity("any alerts pending") == "reminders"


def test_match_entity_recognizes_timer_synonyms():
    assert db_glossary.match_entity("is my countdown still going") == "timers"


def test_match_entity_returns_none_when_nothing_matches():
    assert db_glossary.match_entity("how's the weather today") is None


def test_match_status_defaults_to_active():
    assert db_glossary.match_status("what tasks do i have") == "active"


def test_match_status_recognizes_done_synonyms():
    assert db_glossary.match_status("what have i finished") == "done"
    assert db_glossary.match_status("show my history") == "done"


def test_match_status_recognizes_cancelled_synonyms():
    assert db_glossary.match_status("what got cancelled") == "cancelled"


def test_build_plan_combines_entity_and_status():
    plan = db_glossary.build_plan("what tasks are remaining")
    assert plan is not None
    assert plan.entity == "tasks"
    assert plan.status == "active"


def test_build_plan_returns_none_without_a_recognized_entity():
    assert db_glossary.build_plan("how are you today") is None


def test_describe_schema_reads_live_columns(temp_db):
    from app.memory.database import initialize_schema

    initialize_schema()
    schema_text = db_glossary.describe_schema()
    assert "tasks(" in schema_text
    assert "reminders(" in schema_text
    assert "timers(" in schema_text
    assert "tasks_done(" in schema_text
