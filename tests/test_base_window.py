"""
Tests for app/ui/base_window.py's pinned_by_default option - used by
ChatWindow so it doesn't get buried behind other applications mid-
conversation (see app/ui/chat_window.py).
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from app.ui.base_window import TranslucentDialog


def test_default_dialog_is_not_pinned(qapp):
    dialog = TranslucentDialog("Test")
    assert dialog._pinned is False
    assert not (dialog.windowFlags() & Qt.WindowStaysOnTopHint)
    dialog.close()


def test_pinned_by_default_dialog_stays_on_top(qapp):
    dialog = TranslucentDialog("Test", pinned_by_default=True)
    assert dialog._pinned is True
    assert bool(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
    dialog.close()


def test_pinned_dot_can_still_be_toggled_off(qapp):
    dialog = TranslucentDialog("Test", pinned_by_default=True)
    dialog._toggle_pinned()
    assert dialog._pinned is False
    assert not (dialog.windowFlags() & Qt.WindowStaysOnTopHint)
    dialog.close()


def test_chat_window_is_pinned_by_default(qapp):
    from app.ui.chat_window import ChatWindow

    window = ChatWindow()
    assert window._pinned is True
    assert bool(window.windowFlags() & Qt.WindowStaysOnTopHint)
    window.close()
