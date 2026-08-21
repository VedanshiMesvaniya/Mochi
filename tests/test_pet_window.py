"""
Smoke tests for app/character/pet.py's PetWindow - mainly to catch wiring
mistakes (missing attributes, bad signal connections) in the theme-menu
and lock-screen-easter-egg integration that unit tests of the individual
modules (theme.py, lock_watcher.py) can't catch on their own.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt

from app.character.state_machine import CharacterState


def test_pet_window_constructs_without_error(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    assert window.face is not None
    window.close()


def test_theme_menu_has_four_options_and_defaults_to_purple(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    assert set(window._theme_actions.keys()) == {"purple", "blue", "mint", "rose"}
    assert window._theme_actions["purple"].isChecked() is True
    assert window.face._theme.key == "purple"
    window.close()


def test_selecting_theme_persists_and_reapplies_on_restart(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    window._set_theme("rose")
    assert window.face._theme.key == "rose"
    assert window._theme_actions["rose"].isChecked() is True
    assert window._theme_actions["purple"].isChecked() is False
    window.close()

    # Simulate a restart - a fresh PetWindow should pick up the saved theme.
    window2 = PetWindow()
    assert window2.face._theme.key == "rose"
    window2.close()


def test_lock_signal_closes_eyes_and_unlock_wakes_excited(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    window.state_machine.set_state(CharacterState.IDLE)

    window._on_screen_locked()
    assert window.state_machine.state == CharacterState.LOCKED

    window._on_peek()  # must not raise while locked

    window._on_screen_unlocked()
    assert window.state_machine.state == CharacterState.EXCITED
    window.close()


def test_peek_before_lock_is_harmless(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    window.state_machine.set_state(CharacterState.IDLE)
    window._on_peek()  # not locked - should just do nothing
    assert window.state_machine.state == CharacterState.IDLE
    window.close()


def test_show_reaction_holds_then_reverts_to_idle(qapp, temp_db):
    """Regression test for the timing bug: a reaction expression must
    actually hold for its intended duration rather than being stomped
    back to idle by the next behavior tick almost immediately."""
    from app.character.pet import PetWindow

    window = PetWindow()
    window._show_reaction(CharacterState.HAPPY, hold_ms=50)
    assert window.state_machine.state == CharacterState.HAPPY

    # Behavior-engine ticks happening *during* the hold must not cut it
    # short (this is the actual bug being fixed).
    window.behavior_engine.mark_interacted()
    window._on_behavior_tick()
    assert window.state_machine.state == CharacterState.HAPPY

    # Once the hold timer actually fires, it settles back to the resting
    # default - HAPPY here (not a flat IDLE), since default_expression()
    # returns HAPPY while still within the just-interacted window (see
    # BehaviorEngine.mark_interacted / default_expression).
    window._on_expression_hold_expired()
    assert window.state_machine.state == CharacterState.HAPPY
    window.close()


def test_show_reaction_is_not_reverted_if_superseded(qapp, temp_db):
    """If something else changes the state before the hold timer fires
    (e.g. a second, newer reaction), the stale timer must not stomp the
    newer state back to idle."""
    from app.character.pet import PetWindow

    window = PetWindow()
    window._show_reaction(CharacterState.HAPPY, hold_ms=50)
    window.state_machine.set_state(CharacterState.LOCKED)  # something else takes over

    window._on_expression_hold_expired()
    assert window.state_machine.state == CharacterState.LOCKED
    window.close()


def test_chat_reaction_expression_and_bubble_share_timing(qapp, temp_db):
    from app.character.pet import PetWindow
    from app.ai.chat_engine import ChatReaction
    from app.character.state_machine import Emotion

    window = PetWindow()
    reaction = ChatReaction(text="yay!", emotion=Emotion.EXCITED, animation=CharacterState.EXCITED)
    window._on_chat_reaction(reaction)

    assert window.state_machine.state == CharacterState.EXCITED
    assert window.speech_bubble.text() == "yay!"
    assert window._speech_bubble_timer.isActive()
    assert window._expression_hold_timer.isActive()
    window.close()


def test_speech_bubble_adapts_to_os_dark_mode(qapp, monkeypatch):
    """spec: 'now you made font dark what if it's in dark background it
    should be adaptive' - the bubble must switch to a light-on-dark
    palette when the OS is in dark mode, not just always assume light."""
    import app.character.pet as pet_module

    bubble = pet_module._SpeechBubble()

    monkeypatch.setattr(pet_module, "_is_dark_mode", lambda: False)
    bubble.setText("light mode text")
    assert bubble._dark is False

    monkeypatch.setattr(pet_module, "_is_dark_mode", lambda: True)
    bubble.setText("dark mode text")
    assert bubble._dark is True
    bubble.close()


def test_is_dark_mode_never_raises(qapp):
    """Theme detection must degrade to a safe default rather than crash
    bubble rendering if styleHints()/colorScheme() misbehaves in some
    environment."""
    from app.character.pet import _is_dark_mode

    assert _is_dark_mode() in (True, False)


def test_bored_expression_can_trigger_a_joke(qapp, monkeypatch):
    """spec: 'once in a while it should crawl internet and fetch...
    so it be more of sense of humor' - wired into the bored self-play
    tier: picking a new bored expression can (with some probability,
    subject to a cooldown) fetch and show a joke."""
    from app.character.pet import PetWindow
    from app.character.state_machine import CharacterState

    window = PetWindow()
    # Force the roll to always succeed and the worker to run synchronously
    # in spirit (we still go through the real QThread, just wait for it).
    monkeypatch.setattr("app.character.pet.random.random", lambda: 0.0)
    monkeypatch.setattr(
        "app.ai.humor.get_joke", lambda: "test joke about cats"
    )

    window._apply_behavior_state(CharacterState.EXCITED)  # a real BORED_EXPRESSIONS member

    assert window._humor_worker is not None
    assert window._humor_worker.wait(3000)  # let the background fetch finish
    # Process the queued joke_ready signal on the UI thread.
    from PySide6.QtWidgets import QApplication
    for _ in range(20):
        QApplication.processEvents()
        if window._humor_worker is None:
            break
        time.sleep(0.05)

    assert "test joke about cats" in window.speech_bubble.text()
    window.close()


def test_joke_respects_cooldown(qapp, monkeypatch):
    from app.character.pet import PetWindow
    from app.character.state_machine import CharacterState

    window = PetWindow()
    monkeypatch.setattr("app.character.pet.random.random", lambda: 0.0)
    window._last_joke_time = time.time()  # just told one

    window._apply_behavior_state(CharacterState.EXCITED)

    assert window._humor_worker is None  # cooldown blocked it
    window.close()


def test_joke_never_fires_outside_bored_expressions(qapp, monkeypatch):
    from app.character.pet import PetWindow
    from app.character.state_machine import CharacterState

    window = PetWindow()
    monkeypatch.setattr("app.character.pet.random.random", lambda: 0.0)

    window._apply_behavior_state(CharacterState.HAPPY)  # not a bored expression

    assert window._humor_worker is None
    window.close()


def test_shake_triggers_dizzy_then_angry(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    window.state_machine.set_state(CharacterState.IDLE)

    window._play_shake_reaction()
    assert window.state_machine.state == CharacterState.DIZZY
    assert window._shake_active is True

    window._on_shake_angry()
    assert window.state_machine.state == CharacterState.ANGRY
    assert window._shake_active is True  # still true through the angry hold

    # Settles back to the resting default (HAPPY, not a flat IDLE - see
    # default_expression) once the hold timer fires, same as any other
    # reaction.
    window._on_expression_hold_expired()
    assert window.state_machine.state == CharacterState.HAPPY
    assert window._shake_active is False
    window.close()


def test_mouse_release_does_not_interrupt_shake_sequence(qapp, temp_db):
    """Releasing the mouse mid-shake-reaction must not cut the dizzy/angry
    sequence short back to idle."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from app.character.pet import PetWindow

    window = PetWindow()
    window._play_shake_reaction()
    assert window.state_machine.state == CharacterState.DIZZY

    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(10, 10),
        QPointF(100, 100),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    window.mouseReleaseEvent(release)
    assert window.state_machine.state == CharacterState.DIZZY
    window.close()


def test_shaking_the_window_via_real_mouse_events_triggers_dizzy(qapp, temp_db, monkeypatch):
    """End-to-end: actual QMouseEvent press+wiggle sequence (not calling
    _play_shake_reaction directly) must reach DIZZY through the real
    mousePressEvent/mouseMoveEvent handlers and the shake detector."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from app.character.pet import PetWindow

    window = PetWindow()
    window.show()

    fake_time = [0.0]
    monkeypatch.setattr("app.character.pet.time.monotonic", lambda: fake_time[0])

    def _press(x):
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(x, 10),
            QPointF(x, 10),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        window.mousePressEvent(event)

    def _move(x):
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(x, 10),
            QPointF(x, 10),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        window.mouseMoveEvent(event)

    _press(100)
    x = 100
    for i in range(10):
        fake_time[0] += 0.08
        x += 40 if i % 2 == 0 else -40
        _move(x)

    assert window.state_machine.state == CharacterState.DIZZY
    window.close()
