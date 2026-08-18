"""
Smoke tests for app/character/pet.py's PetWindow - mainly to catch wiring
mistakes (missing attributes, bad signal connections) in the theme-menu
and lock-screen-easter-egg integration that unit tests of the individual
modules (theme.py, lock_watcher.py) can't catch on their own.
"""

from __future__ import annotations

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
