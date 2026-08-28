"""
Mochi's on-screen presence: a small, transparent, frameless, draggable,
always-on-top widget (spec section 4 and Phase 1).

This is intentionally "dumb" about AI - it only knows how to show frames,
respond to mouse interaction, and forward events (click, drag, right-click)
onto the event bus / callbacks. Chat, reminders, etc. are wired in from
app/main.py and app/ui/ so this widget stays reusable and testable.
"""

from __future__ import annotations

import random
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QPoint, QThread, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent, QAction, QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMenu, QApplication, QLabel

SPEECH_BUBBLE_DEFAULT_MS = 6000

from app.character.behavior import BORED_EXPRESSIONS, BehaviorEngine
from app.character.lock_watcher import LockWatcher
from app.character.movement import Mover, ScreenBounds
from app.character.pixel_face import PixelFaceWidget
from app.character.shake_detector import ShakeDetector
from app.character.state_machine import CharacterState, CharacterStateMachine
from app.core.config import settings
from app.core.events import Events, event_bus
from app.core.logger import get_logger
from app.memory import settings_store

SPEECH_BUBBLE_CHAT_MS = 5000
FACE_TICK_MS = 50  # ~20fps - cheap since it's vector drawing, not sprite decoding
BEHAVIOR_TICK_MS = 2000  # must match BehaviorEngine.tick_interval_seconds below

# How long a reaction expression actually holds before Mochi settles back
# to idle (see PetWindow._show_reaction). Tuned per-emotion rather than
# one blanket number: a surprised flash should be quick, a sulk should
# linger. Anything not listed uses DEFAULT_REACTION_HOLD_MS.
REACTION_HOLD_MS: dict[CharacterState, int] = {
    # Bug report: expressions were "so less like i can not properly see it" -
    # the shortest ones (SURPRISED/WINK/ALERT) were cut noticeably shorter
    # than the rest, which read as a flicker rather than a readable
    # expression. Raised so every reaction gets at least ~2.5s on screen -
    # still clearly quicker than the "settle in" emotions like SAD/ANGRY,
    # just no longer so brief it's easy to miss.
    CharacterState.SURPRISED: 2500,
    CharacterState.ALERT: 3000,
    CharacterState.WINK: 2600,
    CharacterState.HAPPY: 3200,
    CharacterState.EXCITED: 3200,
    CharacterState.PLAY: 3200,
    CharacterState.BLUSH: 3600,
    CharacterState.SHY: 3600,
    CharacterState.HEART: 3600,
    CharacterState.SAD: 4200,
    CharacterState.ANGRY: 3800,
    CharacterState.CONFUSED: 3000,
    CharacterState.SLEEPY: 3000,
}
DEFAULT_REACTION_HOLD_MS = 3200

# Shake-the-window easter egg (see app/character/shake_detector.py):
# spin dizzily first, then get properly annoyed about it.
SHAKE_DIZZY_MS = 1400
SHAKE_ANGRY_HOLD_MS = 3200

# "Sense of humor" (spec: "once in a while it should crawl internet and
# fetch... so it be more of sense of humor") - piggybacks on the existing
# bored self-play tier (see BORED_EXPRESSIONS/BehaviorEngine) rather than
# its own separate timer: every time Mochi picks a new bored expression,
# there's a small chance it also tells a joke. Both a minimum cooldown and
# a low per-roll chance keep this to "once in a while", not "every time
# it's bored" - it's a light seasoning, not a running commentary.
HUMOR_MIN_INTERVAL_SECONDS = 900  # at most one joke per 15 minutes
HUMOR_CHANCE_PER_BORED_TICK = 0.2

logger = get_logger("mochi.pet")


class _HumorWorker(QThread):
    """Fetches one joke off the UI thread (see app/ai/humor.py) - a
    network call, even a fast one, has no business running on the same
    thread that's driving the character's animation timer."""

    joke_ready = Signal(str)

    def run(self) -> None:  # noqa: D102 - QThread override
        from app.ai.humor import get_joke  # local import - keeps this
        # network-adjacent module out of pet.py's always-loaded surface

        try:
            joke = get_joke()
        except Exception:  # noqa: BLE001 - a joke must never crash the app
            logger.exception("Humor fetch failed unexpectedly")
            joke = "Hehe, I had a joke but I forgot it. Ask me later!"
        self.joke_ready.emit(joke)


class _RefreshTrendsWorker(QThread):
    """Manual "Refresh trends & memes" action (right-click menu) - runs
    app/humor/trend_fetcher.py + app/humor/meme_fetcher.py's network
    fetches off the UI thread, same reasoning as _HumorWorker above.

    This is the on-demand counterpart to whatever periodic background
    schedule main.py sets up for these two modules (see the "Background
    scheduling note" at the bottom of each) - lets the person force an
    immediate re-crawl right before chatting, instead of waiting for the
    next scheduled interval.

    Also runs app/humor/subreddit_crawler.py's link crawler, but only if
    settings.crawl_sources_path is actually configured - unlike the two
    fetches above, this has no "always on if trend awareness is enabled"
    default of its own; a person has to point it at a real markdown file
    first (see Settings.crawl_sources_path's docstring in
    app/core/config.py). A crawl failure (bad path, all links already
    stored, network trouble) is isolated from the trend/meme fetches
    above so it can never suppress an otherwise-successful refresh's
    results.
    """

    finished_ok = Signal(int, int, int)  # (trend_count, meme_count, crawled_count)
    finished_error = Signal()

    def run(self) -> None:  # noqa: D102 - QThread override
        # Local imports - keeps these network-adjacent modules out of
        # pet.py's always-loaded surface, same reasoning as _HumorWorker.
        from app.humor.meme_fetcher import fetch_memes
        from app.humor.trend_fetcher import fetch_trends

        try:
            trend_count = fetch_trends()
            meme_count = fetch_memes()
        except Exception:  # noqa: BLE001 - a manual refresh must never crash the app
            logger.exception("Manual trend/meme refresh failed unexpectedly")
            self.finished_error.emit()
            return

        crawled_count = 0
        if settings.crawl_sources_path:
            from app.humor.subreddit_crawler import crawl_markdown_file

            try:
                result = crawl_markdown_file(
                    settings.crawl_sources_path,
                    settings.crawl_source_list_name or None,
                )
                crawled_count = result.fetched
            except Exception:  # noqa: BLE001 - same "never crash the app" reasoning as above; a bad/missing crawl_sources_path must not take down the trend/meme refresh that already succeeded
                logger.exception(
                    "Manual link crawl failed unexpectedly (path=%s)",
                    settings.crawl_sources_path,
                )

        self.finished_ok.emit(trend_count, meme_count, crawled_count)


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp `value` into [low, high] - safe even when high < low (e.g. a
    window wider than the available screen), in which case `low` wins so
    the result never ends up further out of range than `value` started."""
    return max(low, min(value, high))


def _is_dark_mode() -> bool:
    """Best-effort OS/desktop dark-mode detection (Qt6's cross-platform
    color-scheme API - Windows, macOS, and most Linux desktop environments
    that expose a preference). Used by _SpeechBubble to pick a palette
    that's guaranteed readable either way (spec: "what if it's in dark
    background it should be adaptive") rather than hardcoding one fixed
    light-box/dark-text combo that only really suits a light desktop.

    Defensive by design: styleHints()/colorScheme() can be unavailable in
    some environments (e.g. a bare offscreen test platform) - fall back to
    light mode rather than let a detection failure crash bubble rendering.
    """
    try:
        hints = QGuiApplication.styleHints()
        return hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - never let theme detection break the UI
        return False


# Two guaranteed-readable palettes for _SpeechBubble - deliberately high
# contrast box+text pairs, not a literal sample of whatever's behind the
# widget (which would need screen-capture and is a lot more fragile);
# "adaptive" here means "follows the OS's own light/dark preference".
_BUBBLE_LIGHT_BG = QColor(255, 255, 255, 245)
_BUBBLE_LIGHT_TEXT = "#3a3350"
_BUBBLE_DARK_BG = QColor(28, 26, 36, 235)
_BUBBLE_DARK_TEXT = "#f1edff"


class _SpeechBubble(QWidget):
    """Mochi's floating speech bubble - a small always-on-top popup shown
    above the character for reminders, chat replies, and reactions.

    Deliberately NOT styled via a QSS `background-color` on this widget.
    This is a translucent (`WA_TranslucentBackground`), frameless,
    top-level `Qt.Tool` popup, and that specific combination is a known Qt
    trouble spot: a stylesheet `background-color` + `border-radius` can
    silently fail to composite on some platforms/graphics drivers, leaving
    only the text visible, floating directly over whatever's on the
    desktop behind it with no box at all - exactly the "I can't read the
    reply, it's not adapting to my background" report. Painting the
    rounded background ourselves with QPainter is the reliable, portable
    way to do a translucent top-level popup.

    On top of that, the palette itself now follows the OS's light/dark
    setting (see _is_dark_mode) - re-checked every time new text is shown,
    since a person could toggle OS theme while Mochi's running - so the
    bubble stays high-contrast and readable in either case, not just
    "always a light box" (which itself becomes a dark-background-adjacent
    problem if the desktop theme, taskbar, and every app around it is
    dark and a stark white popup looks/feels out of place - spec: "now you
    made font dark what if it's in dark background it should be adaptive").
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(220)
        self._dark = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self._label)
        self._apply_palette()

    def _apply_palette(self) -> None:
        self._dark = _is_dark_mode()
        text_color = _BUBBLE_DARK_TEXT if self._dark else _BUBBLE_LIGHT_TEXT
        self._label.setStyleSheet(f"background: transparent; color: {text_color}; font-size: 12px;")
        self.update()

    def setText(self, text: str) -> None:
        self._apply_palette()  # OS theme may have changed since last shown
        self._label.setText(text)
        self.adjustSize()

    def text(self) -> str:
        return self._label.text()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_BUBBLE_DARK_BG if self._dark else _BUBBLE_LIGHT_BG)
        painter.drawRoundedRect(self.rect(), 10, 10)
        super().paintEvent(event)


class PetWindow(QWidget):
    """Mochi's on-screen presence: a small black rounded "screen" with an
    EMO-style programmatic pixel face (see app/character/pixel_face.py) -
    no sprite artwork, no walking around the desktop."""

    def __init__(self) -> None:
        super().__init__()

        self.state_machine = CharacterStateMachine()
        self.behavior_engine = BehaviorEngine(
            enabled=settings.autonomous_behavior,
            tick_interval_seconds=BEHAVIOR_TICK_MS / 1000,
        )
        self.mover: Mover | None = None

        self._drag_offset: QPoint | None = None
        self._is_dragging = False
        self._reminder_window = None
        self._task_window = None
        self._timer_window = None
        self._chat_window = None
        self._humor_worker: Optional[_HumorWorker] = None
        self._last_joke_time: float = 0.0
        self._refresh_trends_worker: Optional[_RefreshTrendsWorker] = None

        self.lock_watcher = LockWatcher(parent=self)

        # Expression-hold timer (see _show_reaction): whatever reaction
        # expression is currently showing (chat reply, task-completed
        # happy, unlock-excited, ...) reverts to idle after its own tuned
        # duration (REACTION_HOLD_MS) rather than being cut short by the
        # next autonomous-behavior tick.
        self._expression_hold_timer = QTimer(self)
        self._expression_hold_timer.setSingleShot(True)
        self._expression_hold_timer.timeout.connect(self._on_expression_hold_expired)
        self._held_state: CharacterState | None = None

        # Shake-the-window easter egg.
        self.shake_detector = ShakeDetector()
        self._shake_active = False
        self._shake_angry_timer = QTimer(self)
        self._shake_angry_timer.setSingleShot(True)
        self._shake_angry_timer.timeout.connect(self._on_shake_angry)

        self._setup_window()
        self._setup_ui()
        self._setup_tray_free_menu()
        self._setup_timers()
        self._place_on_screen()
        self._subscribe_to_events()
        self._subscribe_to_lock_watcher()
        self.lock_watcher.start()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup_window(self) -> None:
        self.setWindowTitle("Mochi")
        flags = Qt.FramelessWindowHint | Qt.Tool
        if settings.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(settings.window_width, settings.window_height)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.face = PixelFaceWidget(self)
        layout.addWidget(self.face)

        # Speech bubble - a small floating popup shown above Mochi's head
        # for reminder notifications (Phase 1.5) and chat responses (Phase
        # 2+). See _SpeechBubble for why this isn't a plain styled QLabel.
        self.speech_bubble = _SpeechBubble(self)
        self.speech_bubble.hide()

        self._speech_bubble_timer = QTimer(self)
        self._speech_bubble_timer.setSingleShot(True)
        self._speech_bubble_timer.timeout.connect(self.speech_bubble.hide)

    def _setup_tray_free_menu(self) -> None:
        """Right-click context menu (spec section 13).

        Reminders/Tasks/Timers/Calendar are deliberately NOT here - the
        whole point of Mochi is that you talk to it, not fill in a form
        ("remind me to call mom at 7pm" / "add task buy milk" / "timer for
        10 minutes", see app/ai/intent.py). Checking them is a chat query
        too ("do I have any tasks?" - see app/ai/chat_engine.py's
        list_tasks/list_reminders handling). A menu item whose only job is
        opening a window to do the same thing manually defeats the point
        of a chat-first companion - don't re-add these without confirming
        with the user first. The window classes themselves
        (app/ui/reminder_window.py etc.) are untouched and still fully
        working/tested, just not wired to this menu.
        """
        self.context_menu = QMenu(self)
        self.action_chat = QAction("Chat", self)
        self.action_memories = QAction("Memories", self)
        self.action_refresh_trends = QAction("Refresh trends && memes", self)
        self.action_settings = QAction("Settings", self)
        self.action_sleep = QAction("Sleep", self)
        self.action_exit = QAction("Exit", self)

        self.action_exit.triggered.connect(QApplication.quit)
        self.action_sleep.triggered.connect(
            lambda: self.state_machine.set_state(CharacterState.SLEEP)
        )
        self.action_chat.triggered.connect(self.on_open_chat_requested)
        self.action_refresh_trends.triggered.connect(self._on_refresh_trends_requested)

        for action in (
            self.action_chat,
            self.action_memories,
            self.action_refresh_trends,
            self.action_settings,
            self.action_sleep,
            self.action_exit,
        ):
            self.context_menu.addAction(action)

    def _setup_timers(self) -> None:
        # Face animation tick - blink/pulse/talk-frame advance + redraw.
        self.face_timer = QTimer(self)
        self.face_timer.timeout.connect(self._on_face_tick)
        self.face_timer.start(FACE_TICK_MS)

        # Autonomous behavior timer (spec section 12/31) - fixed cadence
        # now that behavior is inactivity-tiered rather than a randomized
        # weighted walk (see app/character/behavior.py).
        self.behavior_timer = QTimer(self)
        self.behavior_timer.timeout.connect(self._on_behavior_tick)
        self.behavior_timer.start(BEHAVIOR_TICK_MS)

    def _place_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        bounds = ScreenBounds(
            left=geometry.left(),
            top=geometry.top(),
            right=geometry.right(),
            bottom=geometry.bottom(),
        )
        start_x = geometry.left() + 100
        start_y = geometry.bottom() - self.height() - 40
        self.mover = Mover(
            x=start_x, y=start_y, width=self.width(), height=self.height()
        )
        x, y = self.mover.teleport(start_x, start_y, bounds)
        self.move(x, y)

    def _subscribe_to_events(self) -> None:
        # Brief happy reaction when a reminder/task is completed (spec:
        # "look happy when a task is completed"). See app/ui/reminder_window.py
        # and app/ui/task_window.py for where these are published.
        event_bus.subscribe(Events.REMINDER_COMPLETED, self._on_completion_event)
        event_bus.subscribe(Events.TASK_COMPLETED, self._on_completion_event)

    def _on_completion_event(self, _payload) -> None:
        self.behavior_engine.mark_interacted()
        self._show_reaction(CharacterState.HAPPY)

    # ------------------------------------------------------------------
    # Expression timing (fixes reactions being cut short almost
    # immediately by the next autonomous-behavior tick - see
    # BehaviorEngine._choose_state). Every "Mochi should react to X for a
    # bit" call site (chat replies, completions, unlock, ...) should go
    # through this rather than calling state_machine.set_state directly,
    # so the hold duration is consistent and centrally tunable
    # (REACTION_HOLD_MS above).
    # ------------------------------------------------------------------
    def _show_reaction(self, state: CharacterState, hold_ms: int | None = None) -> None:
        if hold_ms is None:
            hold_ms = REACTION_HOLD_MS.get(state, DEFAULT_REACTION_HOLD_MS)
        self.state_machine.set_state(state)
        self._held_state = state
        self._expression_hold_timer.stop()
        self._expression_hold_timer.start(hold_ms)

    def _on_expression_hold_expired(self) -> None:
        # Only revert if nothing else has taken over in the meantime
        # (another reaction, sleep, a lock, a fresh shake, ...) - each of
        # those either calls _show_reaction again (which restarts this
        # timer against the new state) or stops this timer outright.
        #
        # Reverts to behavior_engine.default_expression() rather than a
        # hardcoded IDLE: right after an interaction Mochi's resting face
        # is HAPPY (spec: "make default happy"), settling to a calm IDLE
        # only once that brief window has passed - see BehaviorEngine.
        if self._held_state is not None and self.state_machine.state == self._held_state:
            self.state_machine.set_state(self.behavior_engine.default_expression())
        self._held_state = None
        self._shake_active = False

    # ------------------------------------------------------------------
    # Lock-screen easter egg (just for fun - spec: eyes close on lock,
    # peek playfully while locked, wake up excited on unlock). Windows
    # only; LockWatcher is a safe no-op everywhere else - see
    # app/character/lock_watcher.py.
    # ------------------------------------------------------------------
    def _subscribe_to_lock_watcher(self) -> None:
        self.lock_watcher.locked.connect(self._on_screen_locked)
        self.lock_watcher.unlocked.connect(self._on_screen_unlocked)
        self.lock_watcher.peek.connect(self._on_peek)

    def _on_screen_locked(self) -> None:
        self._expression_hold_timer.stop()
        self.state_machine.set_state(CharacterState.LOCKED)

    def _on_peek(self) -> None:
        if self.state_machine.state == CharacterState.LOCKED:
            self.face.peek_one_eye()

    def _on_screen_unlocked(self) -> None:
        # Welcome-back reaction, then settle back to normal autonomous
        # behavior after a beat rather than snapping straight to idle.
        self.behavior_engine.mark_interacted()
        self._show_reaction(CharacterState.EXCITED, hold_ms=1500)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _on_face_tick(self) -> None:
        self.face.set_state(self.state_machine.state)

        # Personality touch: pupils drift toward the mouse cursor while
        # idle/alert/talking, so Mochi reads as "paying attention to you"
        # rather than staring blankly - see FACE_EXPRESSIONS' cursor_follow.
        cursor = QCursor.pos()
        center = self.mapToGlobal(self.rect().center())
        half_w = max(1, self.width() / 2)
        half_h = max(1, self.height() / 2)
        dx = max(-1.0, min(1.0, (cursor.x() - center.x()) / (half_w * 4)))
        dy = max(-1.0, min(1.0, (cursor.y() - center.y()) / (half_h * 4)))
        self.face.set_cursor_hint(dx, dy)

        self.face.tick(FACE_TICK_MS / 1000)

    def _get_screen_bounds(self) -> ScreenBounds | None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.availableGeometry()
        return ScreenBounds(
            left=geometry.left(),
            top=geometry.top(),
            right=geometry.right(),
            bottom=geometry.bottom(),
        )

    # ------------------------------------------------------------------
    # Autonomous behavior (spec section 12 / 31)
    # ------------------------------------------------------------------
    def _on_behavior_tick(self) -> None:
        if not self._is_dragging:
            self.behavior_engine.tick(self._apply_behavior_state)

    def _apply_behavior_state(self, state: CharacterState) -> None:
        self.state_machine.set_state(state)
        if state in BORED_EXPRESSIONS:
            self._maybe_tell_joke()

    def _maybe_tell_joke(self) -> None:
        """Occasionally, while genuinely bored and self-entertaining
        (never otherwise - a joke mid-reminder or mid-chat would just be
        noise), fetch and show a joke. See HUMOR_MIN_INTERVAL_SECONDS /
        HUMOR_CHANCE_PER_BORED_TICK for how rarely this actually fires,
        and Settings.humor_enabled for the network-vs-offline-only switch.
        """
        if self._humor_worker is not None:
            return  # already fetching one
        now = time.time()
        if now - self._last_joke_time < HUMOR_MIN_INTERVAL_SECONDS:
            return
        if random.random() > HUMOR_CHANCE_PER_BORED_TICK:
            return
        self._last_joke_time = now
        self._humor_worker = _HumorWorker(self)
        self._humor_worker.joke_ready.connect(self._on_joke_ready)
        self._humor_worker.start()

    def _on_joke_ready(self, joke: str) -> None:
        self.show_speech_bubble(f"Hehe~ {joke}", duration_ms=8000)
        if self._humor_worker is not None:
            self._humor_worker.deleteLater()
            self._humor_worker = None

    def _on_refresh_trends_requested(self) -> None:
        """"Refresh trends & memes" menu action - manually force an
        immediate re-crawl of app/humor/trend_fetcher.py +
        app/humor/meme_fetcher.py rather than waiting for the next
        scheduled background interval. Also runs the link crawler
        (app/humor/subreddit_crawler.py) if settings.crawl_sources_path
        is configured - see _RefreshTrendsWorker's docstring. No-ops
        (with an explanatory speech bubble) if the feature is off
        entirely, since firing a network call the person has explicitly
        disabled would be wrong even on an explicit manual request.
        """
        if not settings.trend_awareness_enabled:
            self.show_speech_bubble(
                "Trend/meme awareness is off right now - "
                "turn on MOCHI_TREND_AWARENESS_ENABLED to use this.",
                duration_ms=6000,
            )
            return
        if self._refresh_trends_worker is not None:
            return  # already refreshing
        self.show_speech_bubble("Crawling for what's new... give me a sec!", duration_ms=4000)
        self._refresh_trends_worker = _RefreshTrendsWorker(self)
        self._refresh_trends_worker.finished_ok.connect(self._on_refresh_trends_done)
        self._refresh_trends_worker.finished_error.connect(self._on_refresh_trends_failed)
        self._refresh_trends_worker.start()

    def _on_refresh_trends_done(self, trend_count: int, meme_count: int, crawled_count: int) -> None:
        if self._refresh_trends_worker is not None:
            self._refresh_trends_worker.deleteLater()
            self._refresh_trends_worker = None
        if trend_count == 0 and meme_count == 0 and crawled_count == 0:
            self.show_speech_bubble(
                "Couldn't reach anything new just now - might be offline. I'll try again later!",
                duration_ms=6000,
            )
            return
        message = f"All caught up! Got {trend_count} trend(s) and {meme_count} meme(s) fresh."
        if crawled_count > 0:
            # Only mentioned when it actually did something - most people
            # never configure crawl_sources_path (see app/core/config.py),
            # so this stays silent for them rather than always reporting
            # "0 pages crawled".
            message = message[:-1] + f", plus {crawled_count} new page(s) crawled."
        self.show_speech_bubble(message, duration_ms=6000)

    def _on_refresh_trends_failed(self) -> None:
        if self._refresh_trends_worker is not None:
            self._refresh_trends_worker.deleteLater()
            self._refresh_trends_worker = None
        self.show_speech_bubble("Hmm, that refresh didn't work. I'll try again later!", duration_ms=6000)

    # ------------------------------------------------------------------
    # Mouse interaction (spec section 13)
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.behavior_engine.mark_interacted()
            self.shake_detector.reset()
            self.shake_detector.feed(time.monotonic(), event.globalPosition().x())
            self._show_reaction(CharacterState.SURPRISED)
        elif event.button() == Qt.RightButton:
            self.context_menu.exec(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging and self._drag_offset is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            if self.mover is not None:
                self.mover.x, self.mover.y = new_pos.x(), new_pos.y()

            if not self._shake_active:
                shook = self.shake_detector.feed(time.monotonic(), event.globalPosition().x())
                if shook:
                    self._play_shake_reaction()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
            self.shake_detector.reset()
            if not self._shake_active:
                self._expression_hold_timer.stop()
                self.state_machine.set_state(CharacterState.IDLE)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            logger.info("Mochi double-clicked -> open chat")
            self.on_open_chat_requested()

    # ------------------------------------------------------------------
    # Shake-the-window easter egg (see app/character/shake_detector.py):
    # shake it rapidly and it gets dizzy, then annoyed with you about it.
    # ------------------------------------------------------------------
    def _play_shake_reaction(self) -> None:
        self._shake_active = True
        self._shake_angry_timer.stop()
        self.behavior_engine.mark_interacted()
        self._expression_hold_timer.stop()
        self.state_machine.set_state(CharacterState.DIZZY)
        self.show_speech_bubble("Whoa?! Stop shaking me!!", duration_ms=SHAKE_DIZZY_MS + 200)
        self._shake_angry_timer.start(SHAKE_DIZZY_MS)

    def _on_shake_angry(self) -> None:
        self.show_speech_bubble("Hmph. I did NOT like that.", duration_ms=SHAKE_ANGRY_HOLD_MS)
        self._show_reaction(CharacterState.ANGRY, hold_ms=SHAKE_ANGRY_HOLD_MS)

    # ------------------------------------------------------------------
    # Speech bubble (used by reminder notifications and, later, chat)
    # ------------------------------------------------------------------
    def show_speech_bubble(self, text: str, duration_ms: int = SPEECH_BUBBLE_DEFAULT_MS) -> None:
        self.speech_bubble.setText(text)  # also resizes to fit (see _SpeechBubble.setText)

        bubble_x = self.x() + (self.width() // 2) - (self.speech_bubble.width() // 2)
        bubble_y = self.y() - self.speech_bubble.height() - 8

        # Same clamp as _position_chat_window above, same reason: a
        # character docked near a screen edge/corner must never push the
        # bubble itself past the visible display area.
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is not None:
            bubble_x = _clamp(bubble_x, avail.left(), avail.right() - self.speech_bubble.width())
            bubble_y = _clamp(bubble_y, avail.top(), avail.bottom() - self.speech_bubble.height())

        self.speech_bubble.move(bubble_x, bubble_y)

        self.speech_bubble.show()
        self._speech_bubble_timer.start(duration_ms)

    # ------------------------------------------------------------------
    # Reminders window (spec section 13/20 - V1)
    #
    # Not on the right-click menu - chat is the primary/intended way to
    # create and check reminders (see app/ai/intent.py + chat_engine.py).
    # Kept here, fully working, in case a future chat command wants to pop
    # the visual list open ("let me see that") rather than answering in text.
    # ------------------------------------------------------------------
    def _open_reminder_window(self) -> None:
        # Local import avoids a hard PySide6-widget dependency for anything
        # that only needs the lightweight character window.
        from app.ui.reminder_window import ReminderWindow

        if self._reminder_window is None:
            self._reminder_window = ReminderWindow()
        self._reminder_window.refresh_list()
        self._reminder_window.show()
        self._reminder_window.raise_()
        self._reminder_window.activateWindow()

    # ------------------------------------------------------------------
    # Tasks window (V2) - see _open_reminder_window's note above; same
    # story, not on the right-click menu, kept for future reuse.
    # ------------------------------------------------------------------
    def _open_task_window(self) -> None:
        from app.ui.task_window import TaskWindow

        if self._task_window is None:
            self._task_window = TaskWindow()
        self._task_window.refresh_list()
        self._task_window.show()
        self._task_window.raise_()
        self._task_window.activateWindow()

    # ------------------------------------------------------------------
    # Timers window (V2) - see _open_reminder_window's note above; same
    # story, not on the right-click menu, kept for future reuse.
    # ------------------------------------------------------------------
    def _open_timer_window(self) -> None:
        from app.ui.timer_window import TimerWindow

        if self._timer_window is None:
            self._timer_window = TimerWindow()
        self._timer_window.refresh_list()
        self._timer_window.show()
        self._timer_window.raise_()
        self._timer_window.activateWindow()

    # ------------------------------------------------------------------
    # Chat window (spec section 14 / Phase 2)
    # ------------------------------------------------------------------
    def on_open_chat_requested(self) -> None:
        from app.ui.chat_window import ChatWindow

        self.behavior_engine.mark_interacted()

        if self._chat_window is None:
            self._chat_window = ChatWindow(
                on_reaction=self._on_chat_reaction,
                on_thinking=self._on_chat_thinking,
                parent=self,
            )
        self._position_chat_window()
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _position_chat_window(self) -> None:
        """Anchor the chat popup near the character, but never let any of
        it render past the edge of the screen.

        Bug report ("chat is went out of screen"): this window previously
        had no explicit position at all - it relied entirely on Qt's
        default placement for a frameless Tool-flagged dialog, which does
        nothing to keep it on-screen. A desktop pet is very often parked
        right at a screen edge/corner (that's the whole point of a
        corner-docked companion), so the chat window - being much wider
        than the character itself - could easily open with a large chunk
        of it, including the newest message bubble, rendered past the
        visible display area with no way to scroll or drag it back short
        of moving the character first.

        Opens to whichever side of the character actually has room
        (prefers the left, since bottom-right corner docking is the most
        common case), then clamps the result to the character's own
        screen's available geometry - so the window is always fully
        visible no matter where the character currently sits, including
        after the character has been dragged around.
        """
        window = self._chat_window
        size = window.size()
        if size.width() < window.minimumWidth() or size.height() < window.minimumHeight():
            # Not shown/sized yet (first open) - fall back to the known
            # minimum rather than whatever tiny default QDialog size Qt
            # hands back before a widget has ever been laid out.
            size = window.minimumSize()

        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None

        x = self.x() - size.width() - 12
        if avail is not None and x < avail.left():
            # No room to the left (character is near the left edge) -
            # try the right side instead.
            x = self.x() + self.width() + 12
        y = self.y() + self.height() - size.height()

        if avail is not None:
            x = _clamp(x, avail.left(), avail.right() - size.width())
            y = _clamp(y, avail.top(), avail.bottom() - size.height())

        window.move(x, y)

    def _on_chat_thinking(self) -> None:
        """Called the instant a message is sent, before a reply exists -
        spec: 'think while the local model is processing'. Chat may fall
        through to a local LLM call (bounded by ~30s, see app/ai/llm.py),
        so this gives immediate visual feedback rather than a dead pause.

        Uses enter_busy() (not mark_interacted()) so the THINKING face
        actually survives the whole wait: mark_interacted() alone queues a
        happy-acknowledgment that the very next ~2s autonomous tick would
        apply, stomping THINKING almost immediately - see
        BehaviorEngine.enter_busy()'s docstring for the full bug report.
        """
        self.behavior_engine.enter_busy()
        self._expression_hold_timer.stop()
        self._held_state = None
        self.state_machine.set_state(CharacterState.THINKING)

    def _on_chat_reaction(self, reaction) -> None:
        """Called by the chat window once a message's intent has been
        detected (spec: 'on chat detect user intent and then mochi
        react') - this is where the reaction actually becomes visible on
        the character: animation, sound, and a speech bubble.

        The expression and the speech bubble are held for the same
        duration (see REACTION_HOLD_MS) so they read as one reaction and
        disappear together, rather than the face snapping back to idle
        seconds before the bubble (or vice versa).
        """
        self.behavior_engine.exit_busy()  # release the THINKING hold from _on_chat_thinking
        hold_ms = DEFAULT_REACTION_HOLD_MS
        if reaction.animation is not None:
            hold_ms = REACTION_HOLD_MS.get(reaction.animation, DEFAULT_REACTION_HOLD_MS)
            self._show_reaction(reaction.animation, hold_ms=hold_ms)
        if reaction.emotion is not None:
            # react=False: we already picked the exact animation above,
            # this just keeps mood tracking in sync without overriding it.
            self.state_machine.set_emotion(reaction.emotion, react=False)
        if reaction.sound:
            event_bus.publish(Events.SOUND_REQUESTED, {"sound": reaction.sound})
        self.show_speech_bubble(reaction.text, duration_ms=max(hold_ms, 2500))
