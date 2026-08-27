"""
Tests for the hybrid semantic fallback wired into chat_engine.handle_message
(see chat_engine.py's block right after detect_intent()).

Reminders/tasks/timers manager tables need a real (temp) DB, so every test
here uses `temp_db`. The local model itself is never actually called - only
app.ai.semantic_intent.classify() is mocked, so these tests are about
chat_engine's ROUTING logic (act / ask / ignore based on confidence), not
about the model call itself (see test_semantic_intent.py for that).
"""

from __future__ import annotations


from app.ai import chat_engine
from app.ai.semantic_intent import SemanticGuess, SemanticUnavailable
from app.reminders import manager as reminder_manager


def test_high_confidence_semantic_reminder_is_created_without_keyword(monkeypatch, temp_db):
    """A message with NONE of REMINDER_TRIGGER's literal words (no
    'remind'/'reminder') must still create a real reminder when the
    semantic layer is confident and a time is present in the text."""
    monkeypatch.setattr(
        chat_engine.semantic_intent,
        "classify",
        lambda text: SemanticGuess(intent="create_reminder", confidence=0.9),
    )
    reaction = chat_engine.handle_message("don't let this slip my mind, call mom at 7pm")

    assert "call mom" in reaction.text.lower()
    reminder_manager.ensure_ready()
    reminders = reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING)
    assert len(reminders) == 1
    assert "call mom" in reminders[0].title.lower()


def test_high_confidence_semantic_reminder_without_time_asks_for_time(monkeypatch, temp_db):
    """Confident about the CATEGORY but no time found in the text at all -
    must ask a clarifying question, never guess a default time."""
    monkeypatch.setattr(
        chat_engine.semantic_intent,
        "classify",
        lambda text: SemanticGuess(intent="create_reminder", confidence=0.9),
    )
    reaction = chat_engine.handle_message("don't let this slip my mind, call mom")

    assert "when" in reaction.text.lower()
    reminder_manager.ensure_ready()
    assert reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING) == []


def test_medium_confidence_semantic_guess_asks_instead_of_acting(monkeypatch, temp_db):
    monkeypatch.setattr(
        chat_engine.semantic_intent,
        "classify",
        lambda text: SemanticGuess(intent="start_timer", confidence=0.6),
    )
    reaction = chat_engine.handle_message("something something ten minutes maybe")

    assert "rephrasing" in reaction.text.lower() or "rephrase" in reaction.text.lower()


def test_low_confidence_semantic_guess_falls_through_to_unknown(monkeypatch, temp_db):
    monkeypatch.setattr(
        chat_engine.semantic_intent,
        "classify",
        lambda text: SemanticGuess(intent="create_task", confidence=0.2),
    )
    # No Ollama running for the open-chat fallback either -> canned setup-hint line.
    reaction = chat_engine.handle_message("hmm not sure what I'm even saying")
    assert reaction is not None  # never crashes; falls to the normal unknown path


def test_small_talk_semantic_guess_never_triggers_an_action(monkeypatch, temp_db):
    monkeypatch.setattr(
        chat_engine.semantic_intent,
        "classify",
        lambda text: SemanticGuess(intent="small_talk", confidence=0.99),
    )
    chat_engine.handle_message("just chatting here")
    reminder_manager.ensure_ready()
    assert reminder_manager.list_reminders(status=reminder_manager.ReminderStatus.PENDING) == []


def test_semantic_unavailable_falls_back_gracefully(monkeypatch, temp_db):
    def _boom(text):
        raise SemanticUnavailable("no ollama")

    monkeypatch.setattr(chat_engine.semantic_intent, "classify", _boom)
    reaction = chat_engine.handle_message("some totally unrecognized message")
    assert reaction is not None


def test_keyword_match_is_never_overridden_by_semantic_layer(monkeypatch, temp_db):
    """The hybrid fallback must only ever run when the keyword pass found
    NOTHING - a message the keyword matcher already understood must never
    even reach semantic_intent.classify()."""
    calls = []
    monkeypatch.setattr(
        chat_engine.semantic_intent,
        "classify",
        lambda text: calls.append(text) or SemanticGuess(intent="small_talk", confidence=0.9),
    )
    chat_engine.handle_message("remind me to call mom at 7pm")
    assert calls == []
