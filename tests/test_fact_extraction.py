from __future__ import annotations

import pytest

from app.ai import fact_extraction as fx


def test_extract_returns_none_for_ordinary_chat():
    assert fx.extract("hows it going") is None
    assert fx.extract("I finally finished the API") is None
    assert fx.extract("thanks so much!") is None


def test_extract_lives_in():
    c = fx.extract("I live in Austin")
    assert c.text == "User lives in Austin."
    assert c.subject == "lives_in"
    assert c.source == "inferred"


def test_extract_moved_to_also_maps_to_lives_in():
    c = fx.extract("I've moved to Seattle")
    assert c.subject == "lives_in"
    assert "Seattle" in c.text


def test_extract_works_at():
    c = fx.extract("I work at Anthropic")
    assert c.text == "User works at Anthropic."
    assert c.subject == "works_at"


def test_extract_job_role():
    c = fx.extract("I work as a backend engineer")
    assert c.subject == "job_role"
    assert "backend engineer" in c.text


def test_extract_job_role_rejects_long_tail():
    # Not actually a job title - shouldn't produce nonsense.
    c = fx.extract("I work as hard as I possibly can every single day of the week")
    assert c is None


def test_extract_favorite():
    c = fx.extract("my favorite color is teal")
    assert c.text == "User's favorite color is teal."
    assert c.subject == "favorite:color"


def test_extract_allergic_to_has_high_confidence():
    c = fx.extract("I am allergic to peanuts")
    assert c.text == "User is allergic to peanuts."
    assert c.subject == "allergic_to:peanuts"
    assert c.confidence >= 0.85


def test_extract_diet_vegetarian():
    c = fx.extract("I'm vegetarian")
    assert c.text == "User is vegetarian."
    assert c.subject == "diet"


def test_extract_diet_vegan():
    c = fx.extract("I am vegan")
    assert c.text == "User is vegan."
    assert c.subject == "diet"


def test_extract_dislikes():
    c = fx.extract("I hate mondays")
    assert c.text == "User dislikes mondays."
    assert c.subject == "dislikes:mondays"

    c2 = fx.extract("I don't like cilantro")
    assert c2.text == "User dislikes cilantro."


def test_extract_preference():
    c = fx.extract("I prefer concise reminders")
    assert c.text == "User prefers concise reminders."
    assert c.subject == "preference:concise reminders"


def test_extract_likes():
    c = fx.extract("I like jazz")
    assert c.text == "User likes jazz."
    assert c.subject == "likes:jazz"


def test_extract_considering_is_hedged_and_low_confidence():
    """Spec section 8's exact example: an uncertain statement must be
    stored hedged, not as a confident current-state fact."""
    c = fx.extract("I might switch to Linux")
    assert c.text == "User is considering switching to Linux."
    assert c.subject is None
    assert c.confidence < 0.6


def test_extract_thinking_about_is_also_hedged():
    c = fx.extract("I'm thinking about learning Rust")
    assert c.text == "User is considering learning Rust."
    assert c.subject is None


@pytest.mark.parametrize(
    "text,expected_subject",
    [
        ("I use Windows", "uses:operating_system"),
        ("I switched to Linux", "uses:operating_system"),
        ("I'm using vim", "uses:editor"),
        ("I use pycharm", "uses:editor"),
        ("I use Python", "uses:primary_language"),
    ],
)
def test_extract_uses_maps_known_categories(text, expected_subject):
    c = fx.extract(text)
    assert c.subject == expected_subject
    assert c.confidence >= 0.8


def test_extract_uses_unknown_thing_has_no_subject_and_lower_confidence():
    """A "use"/"switched to" statement about something outside the small
    fixed category lookup shouldn't pretend to know how to bucket it for
    contradiction purposes - see app/ai/fact_extraction.py's module
    docstring on why that lookup is deliberately narrow."""
    c = fx.extract("I use a standing desk")
    assert c is not None
    assert c.subject is None
    assert c.confidence < 0.8


def test_windows_to_linux_is_the_same_subject_as_spec_section_9_example():
    """The exact contradiction-handling example from the spec: these two
    statements must resolve to the SAME subject so
    app/memory/semantic_memory.remember_fact can supersede one with the
    other, rather than storing both as separate, conflicting facts."""
    old = fx.extract("I use Windows")
    new = fx.extract("I switched to Linux")
    assert old.subject == new.subject == "uses:operating_system"


def test_extract_first_match_wins_when_patterns_could_overlap():
    # Allergy pattern is checked ahead of the generic "likes"/"prefer"
    # patterns - an allergy statement should never be mistaken for a
    # generic preference, even when the sentence also contains "like".
    c = fx.extract("I am allergic to shellfish and I like sushi")
    assert c.subject.startswith("allergic_to:")
