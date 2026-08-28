"""
Smoke tests for app/character/pet.py's PetWindow - mainly to catch wiring
mistakes (missing attributes, bad signal connections) in the
lock-screen-easter-egg integration that unit tests of the individual
modules (lock_watcher.py) can't catch on their own.

Note: PetWindow used to also own a right-click "Theme" submenu (4
selectable glow palettes, persisted via settings_store.KEY_GLOW_THEME).
That's gone - see app/character/theme.py - so there's nothing theme-menu
related left to smoke-test here.
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


def test_refresh_trends_action_noops_when_disabled(qapp, monkeypatch):
    """Manual 'Refresh trends & memes' menu action must not fire a network
    call when the feature is off - respects the same opt-in gate as the
    background job, even for an explicit manual request."""
    from app.character.pet import PetWindow
    from app.core.config import settings

    window = PetWindow()
    monkeypatch.setattr(settings, "trend_awareness_enabled", False)

    window._on_refresh_trends_requested()

    assert window._refresh_trends_worker is None
    assert "off right now" in window.speech_bubble.text()
    window.close()


def test_refresh_trends_action_runs_and_reports_counts(qapp, monkeypatch):
    from app.character.pet import PetWindow
    from app.core.config import settings

    window = PetWindow()
    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    monkeypatch.setattr("app.humor.trend_fetcher.fetch_trends", lambda: 3)
    monkeypatch.setattr("app.humor.meme_fetcher.fetch_memes", lambda: 2)

    window._on_refresh_trends_requested()
    assert window._refresh_trends_worker is not None
    assert window._refresh_trends_worker.wait(3000)

    from PySide6.QtWidgets import QApplication
    for _ in range(20):
        QApplication.processEvents()
        if window._refresh_trends_worker is None:
            break
        time.sleep(0.05)

    assert "3 trend(s) and 2 meme(s)" in window.speech_bubble.text()
    window.close()


def test_refresh_trends_action_reports_nothing_new(qapp, monkeypatch):
    from app.character.pet import PetWindow
    from app.core.config import settings

    window = PetWindow()
    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    monkeypatch.setattr("app.humor.trend_fetcher.fetch_trends", lambda: 0)
    monkeypatch.setattr("app.humor.meme_fetcher.fetch_memes", lambda: 0)

    window._on_refresh_trends_requested()
    assert window._refresh_trends_worker.wait(3000)

    from PySide6.QtWidgets import QApplication
    for _ in range(20):
        QApplication.processEvents()
        if window._refresh_trends_worker is None:
            break
        time.sleep(0.05)

    assert "offline" in window.speech_bubble.text()
    window.close()


def test_refresh_trends_action_also_crawls_when_source_path_configured(qapp, monkeypatch, tmp_path, temp_db):
    """When settings.crawl_sources_path is set, the same manual
    'Refresh trends & memes' click must also run the link crawler and
    report how many new pages it stored - see _RefreshTrendsWorker."""
    from app.character.pet import PetWindow
    from app.core.config import settings
    from app.humor import subreddit_crawler

    md_file = tmp_path / "links.md"
    md_file.write_text("[r/funny](https://www.reddit.com/r/funny/)\n")

    window = PetWindow()
    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    monkeypatch.setattr(settings, "crawl_sources_path", str(md_file))
    monkeypatch.setattr("app.humor.trend_fetcher.fetch_trends", lambda: 0)
    monkeypatch.setattr("app.humor.meme_fetcher.fetch_memes", lambda: 0)
    monkeypatch.setattr(subreddit_crawler, "_fetch_page", lambda url: ("Funny", "some jokes"))

    window._on_refresh_trends_requested()
    assert window._refresh_trends_worker is not None
    assert window._refresh_trends_worker.wait(3000)

    from PySide6.QtWidgets import QApplication
    for _ in range(20):
        QApplication.processEvents()
        if window._refresh_trends_worker is None:
            break
        time.sleep(0.05)

    assert "1 new page(s) crawled" in window.speech_bubble.text()
    window.close()


def test_refresh_trends_action_never_crawls_when_source_path_unset(qapp, monkeypatch):
    """No crawl_sources_path configured (the default) -> the crawler must
    never even be imported/called, same 'opt-in, off unless explicitly
    configured' philosophy as every other network feature here."""
    from app.character.pet import PetWindow
    from app.core.config import settings

    window = PetWindow()
    monkeypatch.setattr(settings, "trend_awareness_enabled", True)
    monkeypatch.setattr(settings, "crawl_sources_path", "")
    monkeypatch.setattr("app.humor.trend_fetcher.fetch_trends", lambda: 1)
    monkeypatch.setattr("app.humor.meme_fetcher.fetch_memes", lambda: 1)

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("crawl_markdown_file must not be called when crawl_sources_path is unset")

    monkeypatch.setattr(
        "app.humor.subreddit_crawler.crawl_markdown_file", _must_not_be_called
    )

    window._on_refresh_trends_requested()
    assert window._refresh_trends_worker.wait(3000)

    from PySide6.QtWidgets import QApplication
    for _ in range(20):
        QApplication.processEvents()
        if window._refresh_trends_worker is None:
            break
        time.sleep(0.05)

    assert "1 trend(s) and 1 meme(s)" in window.speech_bubble.text()
    assert "crawled" not in window.speech_bubble.text()
    window.close()


def test_chat_window_position_stays_within_screen_when_character_near_edge(qapp, temp_db):
    """Bug report ('chat is went out of screen'): a character docked at
    or beyond a screen edge must never produce a chat window position
    that renders any part of the window off-screen."""
    from app.character.pet import PetWindow

    window = PetWindow()
    screen = window.screen()
    assert screen is not None, "test requires a screen (even the offscreen QPA provides one)"
    avail = screen.availableGeometry()

    # Park the character right at the screen's far edge - the exact
    # scenario from the bug report (a corner-docked desktop pet).
    window.move(avail.right() - window.width() // 2, avail.bottom() - window.height() // 2)

    window.on_open_chat_requested()
    chat = window._chat_window
    assert chat is not None

    chat_size = chat.size()
    if chat_size.width() < chat.minimumWidth() or chat_size.height() < chat.minimumHeight():
        chat_size = chat.minimumSize()

    assert chat.x() >= avail.left()
    assert chat.y() >= avail.top()
    assert chat.x() + chat_size.width() <= avail.right()
    assert chat.y() + chat_size.height() <= avail.bottom()
    window.close()


def test_speech_bubble_position_stays_within_screen_when_character_near_edge(qapp, temp_db):
    from app.character.pet import PetWindow

    window = PetWindow()
    screen = window.screen()
    assert screen is not None
    avail = screen.availableGeometry()

    window.move(avail.left(), avail.top())  # top-left corner this time
    window.show_speech_bubble("Hello!")

    assert window.speech_bubble.x() >= avail.left()
    assert window.speech_bubble.y() >= avail.top()
    assert window.speech_bubble.x() + window.speech_bubble.width() <= avail.right()
    window.close()


def test_clamp_helper_handles_widget_larger_than_screen():
    from app.character.pet import _clamp

    # A window/bubble wider than the whole available screen must still
    # resolve to a real, in-range position (the left/top edge) rather
    # than a nonsensical negative-width range.
    assert _clamp(500, 0, -50) == 0
    assert _clamp(-999, 0, 800) == 0
    assert _clamp(400, 0, 800) == 400
