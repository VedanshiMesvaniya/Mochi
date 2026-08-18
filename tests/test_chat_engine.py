from app.ai.chat_engine import handle_message
from app.character.state_machine import CharacterState, Emotion
from app.memory import relationship
from app.reminders import manager as reminder_manager


def test_greeting_reaction_has_no_side_effects(temp_db):
    reaction = handle_message("hi mochi")
    assert reaction.emotion == Emotion.HAPPY
    assert reaction.animation == CharacterState.HAPPY
    assert reaction.text


def test_reminder_message_actually_creates_a_reminder(temp_db):
    reaction = handle_message("remind me to test the chat engine in 5 minutes")
    assert reaction.text

    after = reminder_manager.list_reminders()
    assert len(after) == 1
    assert after[-1].title == "Test the chat engine"


def test_malformed_input_never_crashes(temp_db):
    reaction = handle_message("")
    assert reaction.text


def test_every_message_records_an_interaction(temp_db):
    handle_message("hi")
    handle_message("bye")
    assert relationship.get_interaction_count() == 2


def test_greeting_changes_once_familiar(temp_db):
    first_reaction = handle_message("hi")
    # Rack up enough interactions to cross into "familiar" territory.
    for _ in range(30):
        handle_message("hi")
    familiar_reaction = handle_message("hi")
    assert familiar_reaction.text != first_reaction.text
    assert "back" in familiar_reaction.text.lower()


def test_unknown_message_with_llm_unavailable_gives_a_setup_hint(temp_db, monkeypatch):
    """Regression test: previously an LLM-unavailable fallback used the
    exact same generic line as a genuinely-unrecognized message, so a
    working-as-designed 'Ollama isn't running' situation looked
    indistinguishable from a broken/confused Mochi. It must now say
    something actionable instead."""
    from app.ai.llm import LLMUnavailable

    def _boom(*_args, **_kwargs):
        raise LLMUnavailable("connection refused")

    monkeypatch.setattr("app.ai.chat_engine.ask_llm", _boom)

    reaction = handle_message("what is the meaning of life")
    assert "ollama" in reaction.text.lower()
    assert reaction.animation == CharacterState.SLEEPY
