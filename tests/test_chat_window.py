"""
Tests for app/ui/chat_window.py's background ChatWorker - the fix for
chat freezing/appearing to "fail to answer" while a local LLM call was
running synchronously on the UI thread.
"""

from __future__ import annotations

import time

from app.ai.chat_engine import ChatReaction
from app.character.state_machine import CharacterState, Emotion


def _bubble_text(message_log, index: int) -> str:
    """Pull the rendered text back out of a ChatBubble row for assertions
    (see app/ui/chat_window.py's ChatBubble - messages are now bubble
    widgets via setItemWidget, not plain item text)."""
    from PySide6.QtWidgets import QLabel

    item = message_log.item(index)
    bubble = message_log.itemWidget(item)
    label = bubble.findChild(QLabel)
    return label.text()


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
    # call above should return right away, with input marked busy to signal
    # "Mochi is thinking" rather than the field just sitting there mute.
    #
    # Read-only rather than disabled, deliberately: disabling a focused
    # widget can yank keyboard focus away from the dialog entirely, which
    # on some platforms is enough to make a Qt.Tool popup like this one
    # drop out of view mid-wait (see _on_send_clicked's comment) - staying
    # read-only keeps focus right where it was.
    assert window.input_field.isReadOnly() is True
    assert window.send_button.isEnabled() is False

    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert window.input_field.isReadOnly() is False
    assert window.send_button.isEnabled() is True
    assert any("done" in _bubble_text(window.message_log, i) for i in range(window.message_log.count()))


def test_window_is_reshown_if_hidden_while_reply_pending(qapp, monkeypatch):
    """Regression: 'chat window closes after I send a message, I have to
    open it again'. Whatever the platform-level cause of the window
    dropping out of view mid-wait, the fix must not depend on the window
    still being visible when the reply lands - _on_reaction_ready has to
    actively re-show it, not just raise_()/activateWindow() (which are
    no-ops on an already-hidden window)."""
    from app.ui.chat_window import ChatWindow

    def _slow_reply(_text):
        time.sleep(0.2)
        return ChatReaction(text="done", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY)

    monkeypatch.setattr("app.ui.chat_window.handle_message", _slow_reply)

    window = ChatWindow()
    window.show()
    window.input_field.setText("hi there")
    window._on_send_clicked()

    # Simulate whatever hid it mid-wait (focus change, window manager, ...)
    window.hide()
    assert window.isVisible() is False

    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert window.isVisible() is True
    window.close()


def test_typing_indicator_shows_while_waiting_and_clears_on_reply(qapp, monkeypatch):
    """Regression: while a reply was pending, the chat window's own log gave
    no feedback at all (the only sign of life was the character's face
    changing state elsewhere on the desktop), which read as the chat having
    silently frozen or closed."""
    from app.ui.chat_window import ChatWindow

    def _slow_reply(_text):
        time.sleep(0.3)
        return ChatReaction(text="done", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY)

    monkeypatch.setattr("app.ui.chat_window.handle_message", _slow_reply)

    window = ChatWindow()
    window.input_field.setText("hi there")
    window._on_send_clicked()

    assert window._typing_item is not None
    assert "thinking" in _bubble_text(window.message_log, window.message_log.count() - 1).lower()

    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert window._typing_item is None
    assert not any(
        "thinking" in _bubble_text(window.message_log, i).lower()
        for i in range(window.message_log.count())
    )
    window.close()


def test_messages_render_as_chat_bubbles_not_plain_text(qapp):
    from app.ui.chat_window import ChatBubble, ChatWindow

    window = ChatWindow()
    window._append("You", "hey mochi")
    window._append("Mochi", "hehe hi!")

    last_two = [window.message_log.item(i) for i in range(window.message_log.count())][-2:]
    bubbles = [window.message_log.itemWidget(item) for item in last_two]
    assert all(isinstance(bubble, ChatBubble) for bubble in bubbles)
    assert bubbles[0]._is_user is True   # "You" message
    assert bubbles[1]._is_user is False  # Mochi's reply

    assert _bubble_text(window.message_log, window.message_log.count() - 2) == "hey mochi"
    assert _bubble_text(window.message_log, window.message_log.count() - 1) == "hehe hi!"
    window.close()


def test_chat_bubbles_are_not_selectable_list_items(qapp):
    from PySide6.QtCore import Qt
    from app.ui.chat_window import ChatWindow

    window = ChatWindow()
    window._append("Mochi", "just a bubble")
    item = window.message_log.item(window.message_log.count() - 1)
    assert item.flags() == Qt.NoItemFlags
    window.close()
