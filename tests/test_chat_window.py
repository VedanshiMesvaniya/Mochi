"""
Tests for app/ui/chat_window.py's background ChatWorker - the fix for
chat freezing/appearing to "fail to answer" while a local LLM call was
running synchronously on the UI thread.
"""

from __future__ import annotations

import time

from app.ai.chat_engine import ChatReaction
from app.character.state_machine import CharacterState, Emotion


def test_chat_worker_runs_off_thread_and_emits_reaction(qapp, monkeypatch):
    from app.ui.chat_window import ChatWorker

    monkeypatch.setattr(
        "app.ui.chat_window.handle_message",
        lambda text: ChatReaction(
            text=f"echo: {text}", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY
        ),
    )

    results = []
    worker = ChatWorker("hello mochi")
    worker.finished_reaction.connect(results.append)
    worker.start()
    assert worker.wait(5000), "worker did not finish in time"
    qapp.processEvents()

    assert len(results) == 1
    assert results[0].text == "echo: hello mochi"


def test_chat_worker_falls_back_gracefully_on_exception(qapp, monkeypatch):
    from app.ui.chat_window import ChatWorker

    def _boom(_text):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("app.ui.chat_window.handle_message", _boom)

    results = []
    worker = ChatWorker("anything")
    worker.finished_reaction.connect(results.append)
    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()

    assert len(results) == 1
    assert "hiccuped" in results[0].text.lower()


def test_send_disables_input_while_waiting_then_reenables(qapp, monkeypatch):
    from app.ui.chat_window import ChatWindow

    def _slow_reply(_text):
        time.sleep(0.3)
        return ChatReaction(text="done", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY)

    monkeypatch.setattr("app.ui.chat_window.handle_message", _slow_reply)

    window = ChatWindow()
    window.input_field.setText("hi there")
    window._on_send_clicked()

    # Immediately after sending, the UI must not be frozen/blocked - the
    # call above should return right away, with input disabled to signal
    # "Mochi is thinking" rather than the field just sitting there mute.
    assert window.input_field.isEnabled() is False
    assert window.send_button.isEnabled() is False

    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert window.input_field.isEnabled() is True
    assert window.send_button.isEnabled() is True
    assert any("done" in window.message_log.item(i).text() for i in range(window.message_log.count()))
