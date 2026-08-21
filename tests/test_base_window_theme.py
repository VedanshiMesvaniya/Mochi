"""
base_window.py's popups (reminders, tasks, timers, settings, ...) used to
be a single fixed light frosted-glass style, unlike the speech bubble in
app/character/pet.py which already adapts to OS dark mode. This checks
popups actually pick up the same signal.
"""

from app.ui import base_window


def test_current_palette_light(monkeypatch):
    monkeypatch.setattr("app.character.pet._is_dark_mode", lambda: False)
    palette = base_window._current_palette()
    assert palette["text_color"] == base_window._LIGHT_TEXT_COLOR


def test_current_palette_dark(monkeypatch):
    monkeypatch.setattr("app.character.pet._is_dark_mode", lambda: True)
    palette = base_window._current_palette()
    assert palette["text_color"] == base_window._DARK_TEXT_COLOR


def test_current_palette_never_raises_if_detection_fails(monkeypatch):
    def _boom():
        raise RuntimeError("no display")

    monkeypatch.setattr("app.character.pet._is_dark_mode", _boom)
    palette = base_window._current_palette()
    assert palette["text_color"] == base_window._LIGHT_TEXT_COLOR


def test_translucent_dialog_uses_current_palette(qapp, monkeypatch):
    monkeypatch.setattr("app.character.pet._is_dark_mode", lambda: True)
    dialog = base_window.TranslucentDialog("Test")
    style = dialog.panel.styleSheet()
    assert base_window._DARK_TEXT_COLOR in style
    assert base_window._LIGHT_TEXT_COLOR not in style
