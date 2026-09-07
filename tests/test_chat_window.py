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


def test_bubble_label_width_reserves_room_for_full_css_padding(qapp):
    """Regression guard (conversational-issues report P1, "Fix Chat
    Bubble Width / Text Clipping"): the fixed width handed to
    setFixedWidth() must budget the label's *real* horizontal CSS
    padding (13px left + 13px right - see _MOCHI_BUBBLE_STYLE), not less,
    or the content area left for text ends up narrower than the text's
    own natural width and a single-line reply wraps/clips right at the
    edge."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QLabel

    from app.ui.chat_window import _bubble_label_width

    label = QLabel("A reasonably short reply")
    width = _bubble_label_width("A reasonably short reply", label)
    natural = QFontMetrics(label.font()).horizontalAdvance("A reasonably short reply")

    assert width >= natural + 26  # 13px left + 13px right, at minimum


def test_bubble_label_width_still_caps_at_max_width(qapp):
    from PySide6.QtWidgets import QLabel

    from app.ui.chat_window import _BUBBLE_MAX_WIDTH, _bubble_label_width

    label = QLabel()
    width = _bubble_label_width("x" * 500, label)

    assert width == _BUBBLE_MAX_WIDTH


def test_chat_worker_runs_off_thread_and_emits_reaction(qapp, monkeypatch):
    from app.ui.chat_window import ChatWorker

    monkeypatch.setattr(
        "app.ui.chat_window.handle_message",
        lambda text, history=None, pending_action=None, conversation_state=None: ChatReaction(
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

    def _boom(_text, history=None, pending_action=None, conversation_state=None):
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

    def _slow_reply(_text, history=None, pending_action=None, conversation_state=None):
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

    def _slow_reply(_text, history=None, pending_action=None, conversation_state=None):
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

    def _slow_reply(_text, history=None, pending_action=None, conversation_state=None):
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


def test_session_history_accumulates_and_is_passed_to_handle_message(qapp, monkeypatch):
    """spec: 'for chat it should store the current chat memory... it
    should remember whole chat [until closed]' - every turn this session
    must be forwarded to handle_message so the LLM fallback has real
    conversational context, not just the latest isolated message."""
    from app.ui.chat_window import ChatWindow

    seen_histories = []

    def _capture(text, history=None, pending_action=None, conversation_state=None):
        seen_histories.append(list(history or []))
        return ChatReaction(text=f"reply to {text}", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY)

    monkeypatch.setattr("app.ui.chat_window.handle_message", _capture)

    def _send(window, text):
        window.input_field.setText(text)
        window._on_send_clicked()
        deadline = time.time() + 5
        while window._worker is not None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

    window = ChatWindow()  # greeting already seeded into history

    _send(window, "first message")
    assert seen_histories[-1] == [("mochi", "Hehe, hi! What are we up to?")]

    _send(window, "second message")
    assert seen_histories[-1] == [
        ("mochi", "Hehe, hi! What are we up to?"),
        ("user", "first message"),
        ("mochi", "reply to first message"),
    ]
    window.close()


def test_session_history_is_cleared_on_close(qapp, monkeypatch):
    from app.ui.chat_window import ChatWindow

    monkeypatch.setattr(
        "app.ui.chat_window.handle_message",
        lambda text, history=None, pending_action=None, conversation_state=None: ChatReaction(
            text="ok", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY
        ),
    )

    window = ChatWindow()
    window.input_field.setText("remember this")
    window._on_send_clicked()
    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert len(window._history) > 0

    window.close()
    assert window._history == []


def test_pending_action_is_carried_across_messages_and_passed_through(qapp, monkeypatch):
    """spec section 23 (V4): a calendar write proposal awaiting yes/no
    must survive to the *next* handle_message() call so a plain 'yes'
    can resolve it - the window is responsible for round-tripping
    ChatReaction.pending_action back in as handle_message's kwarg."""
    from app.ui.chat_window import ChatWindow

    seen_pending_actions = []

    def _propose_then_track(text, history=None, pending_action=None, conversation_state=None):
        seen_pending_actions.append(pending_action)
        if pending_action is None:
            return ChatReaction(
                text="confirm?",
                emotion=Emotion.CURIOUS,
                animation=CharacterState.THINKING,
                pending_action={"kind": "calendar_create", "title": "Sync"},
            )
        return ChatReaction(text="done!", emotion=Emotion.HAPPY, animation=CharacterState.HAPPY)

    monkeypatch.setattr("app.ui.chat_window.handle_message", _propose_then_track)

    def _send(window, text):
        window.input_field.setText(text)
        window._on_send_clicked()
        deadline = time.time() + 5
        while window._worker is not None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

    window = ChatWindow()
    _send(window, "schedule a meeting")
    assert seen_pending_actions[-1] is None
    assert window._pending_action == {"kind": "calendar_create", "title": "Sync"}

    _send(window, "yes")
    assert seen_pending_actions[-1] == {"kind": "calendar_create", "title": "Sync"}
    window.close()


def test_pending_action_is_cleared_on_close(qapp, monkeypatch):
    from app.ui.chat_window import ChatWindow

    monkeypatch.setattr(
        "app.ui.chat_window.handle_message",
        lambda text, history=None, pending_action=None, conversation_state=None: ChatReaction(
            text="confirm?",
            emotion=Emotion.CURIOUS,
            animation=CharacterState.THINKING,
            pending_action={"kind": "calendar_create", "title": "Sync"},
        ),
    )

    window = ChatWindow()
    window.input_field.setText("schedule a meeting")
    window._on_send_clicked()
    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert window._pending_action is not None

    window.close()
    assert window._pending_action is None


def test_conversation_state_is_carried_across_messages_and_passed_through(qapp, monkeypatch):
    """Security review I1/I3 - app/ai/conversation_state.py's memory must
    survive to the *next* handle_message() call so "actually delete it"
    can resolve against what was just created - the window is
    responsible for round-tripping ChatReaction.conversation_state back
    in as handle_message's kwarg, same as it already does for
    pending_action."""
    from app.ui.chat_window import ChatWindow

    seen_states = []

    def _create_then_reference(text, history=None, pending_action=None, conversation_state=None):
        seen_states.append(conversation_state)
        if conversation_state is None:
            return ChatReaction(
                text="added!",
                emotion=Emotion.HAPPY,
                animation=CharacterState.HAPPY,
                conversation_state={"entity_type": "task", "entity_id": 1, "entity_title": "Buy milk", "candidates": None},
            )
        return ChatReaction(text="deleted!", emotion=Emotion.NEUTRAL, animation=CharacterState.IDLE)

    monkeypatch.setattr("app.ui.chat_window.handle_message", _create_then_reference)

    def _send(window, text):
        window.input_field.setText(text)
        window._on_send_clicked()
        deadline = time.time() + 5
        while window._worker is not None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

    window = ChatWindow()
    _send(window, "add task buy milk")
    assert seen_states[-1] is None
    assert window._conversation_state == {
        "entity_type": "task", "entity_id": 1, "entity_title": "Buy milk", "candidates": None,
    }

    _send(window, "actually delete it")
    assert seen_states[-1] == {
        "entity_type": "task", "entity_id": 1, "entity_title": "Buy milk", "candidates": None,
    }
    window.close()


def test_conversation_state_is_cleared_on_close(qapp, monkeypatch):
    from app.ui.chat_window import ChatWindow

    monkeypatch.setattr(
        "app.ui.chat_window.handle_message",
        lambda text, history=None, pending_action=None, conversation_state=None: ChatReaction(
            text="added!",
            emotion=Emotion.HAPPY,
            animation=CharacterState.HAPPY,
            conversation_state={"entity_type": "task", "entity_id": 1, "entity_title": "Buy milk", "candidates": None},
        ),
    )

    window = ChatWindow()
    window.input_field.setText("add task buy milk")
    window._on_send_clicked()
    deadline = time.time() + 5
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert window._conversation_state is not None

    window.close()
    assert window._conversation_state is None
