"""
Mochi's on-screen presence: a small, transparent, frameless, draggable,
always-on-top widget (spec section 4 and Phase 1).

This is intentionally "dumb" about AI - it only knows how to show frames,
respond to mouse interaction, and forward events (click, drag, right-click)
onto the event bus / callbacks. Chat, reminders, etc. are wired in from
app/main.py and app/ui/ so this widget stays reusable and testable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPixmap, QMouseEvent, QAction
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QMenu, QApplication

SPEECH_BUBBLE_DEFAULT_MS = 6000

from app.character.animator import Animator
from app.character.behavior import BehaviorEngine
from app.character.movement import Mover, ScreenBounds
from app.character.state_machine import CharacterState, CharacterStateMachine
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.pet")


class PetWindow(QWidget):
    """The floating desktop cat."""

    def __init__(self) -> None:
        super().__init__()

        self.state_machine = CharacterStateMachine()
        self.animator = Animator()
        self.behavior_engine = BehaviorEngine(enabled=settings.autonomous_behavior)
        self.mover: Mover | None = None

        self._drag_offset: QPoint | None = None
        self._is_dragging = False

        self._setup_window()
        self._setup_ui()
        self._setup_tray_free_menu()
        self._setup_timers()
        self._place_on_screen()

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

        self.sprite_label = QLabel(self)
        self.sprite_label.setAlignment(Qt.AlignCenter)
        self.sprite_label.setScaledContents(True)
        layout.addWidget(self.sprite_label)

        # Speech bubble - a small floating label shown above Mochi's head
        # for reminder notifications (Phase 1.5) and chat responses (Phase 2+).
        self.speech_bubble = QLabel(self)
        self.speech_bubble.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.speech_bubble.setAttribute(Qt.WA_TranslucentBackground)
        self.speech_bubble.setStyleSheet(
            "background-color: rgba(255, 255, 255, 235);"
            "border-radius: 10px; padding: 8px 12px; font-size: 12px;"
        )
        self.speech_bubble.setWordWrap(True)
        self.speech_bubble.setMaximumWidth(240)
        self.speech_bubble.hide()

        self._speech_bubble_timer = QTimer(self)
        self._speech_bubble_timer.setSingleShot(True)
        self._speech_bubble_timer.timeout.connect(self.speech_bubble.hide)

        self._refresh_sprite()

    def _setup_tray_free_menu(self) -> None:
        """Right-click context menu (spec section 13)."""
        self.context_menu = QMenu(self)
        self.action_chat = QAction("Chat", self)
        self.action_reminders = QAction("Reminders", self)
        self.action_calendar = QAction("Calendar", self)
        self.action_memories = QAction("Memories", self)
        self.action_settings = QAction("Settings", self)
        self.action_sleep = QAction("Sleep", self)
        self.action_exit = QAction("Exit", self)

        self.action_exit.triggered.connect(QApplication.quit)
        self.action_sleep.triggered.connect(
            lambda: self.state_machine.set_state(CharacterState.SLEEP)
        )

        for action in (
            self.action_chat,
            self.action_reminders,
            self.action_calendar,
            self.action_memories,
            self.action_settings,
            self.action_sleep,
            self.action_exit,
        ):
            self.context_menu.addAction(action)

    def _setup_timers(self) -> None:
        # Animation frame advance timer
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._on_animation_tick)
        self.animation_timer.start(1000 // max(self.animator.animation_set.default_fps, 1))

        # Autonomous behavior timer (spec section 12 - deterministic, no LLM)
        self.behavior_timer = QTimer(self)
        self.behavior_timer.setSingleShot(True)
        self.behavior_timer.timeout.connect(self._on_behavior_tick)
        self._schedule_next_behavior()

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

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _refresh_sprite(self) -> None:
        frame_path = self.animator.current_frame_path()
        if frame_path is not None:
            pixmap = QPixmap(str(frame_path))
            self.sprite_label.setPixmap(pixmap)
        else:
            # No artwork yet for this animation - keep window transparent
            # rather than crashing. See spec section 36 (graceful degradation).
            self.sprite_label.clear()
            self.sprite_label.setText("")

    def _on_animation_tick(self) -> None:
        self.animator.play(self.state_machine.state.value)
        self.animator.advance()
        self._refresh_sprite()

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
    def _schedule_next_behavior(self) -> None:
        interval_ms = int(self.behavior_engine.next_interval() * 1000)
        self.behavior_timer.start(interval_ms)

    def _on_behavior_tick(self) -> None:
        if not self._is_dragging:
            self.behavior_engine.tick(self.state_machine.set_state)
            self._maybe_walk()
        self._schedule_next_behavior()

    def _maybe_walk(self) -> None:
        if self.mover is None:
            return
        bounds = self._get_screen_bounds()
        if bounds is None:
            return
        if self.state_machine.state in (
            CharacterState.WALK_LEFT,
            CharacterState.WALK_RIGHT,
        ):
            self.mover.direction = -1 if self.state_machine.state == CharacterState.WALK_LEFT else 1
            x, y = self.mover.step(bounds)
            self.move(x, y)

    # ------------------------------------------------------------------
    # Mouse interaction (spec section 13)
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.state_machine.set_state(CharacterState.DRAGGED)
        elif event.button() == Qt.RightButton:
            self.context_menu.exec(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging and self._drag_offset is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            if self.mover is not None:
                self.mover.x, self.mover.y = new_pos.x(), new_pos.y()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
            self.state_machine.set_state(CharacterState.IDLE)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            logger.info("Mochi double-clicked -> open chat")
            # Wired up to the real chat window in app/main.py
            self.on_open_chat_requested()

    # ------------------------------------------------------------------
    # Speech bubble (used by reminder notifications and, later, chat)
    # ------------------------------------------------------------------
    def show_speech_bubble(self, text: str, duration_ms: int = SPEECH_BUBBLE_DEFAULT_MS) -> None:
        self.speech_bubble.setText(text)
        self.speech_bubble.adjustSize()

        bubble_x = self.x() + (self.width() // 2) - (self.speech_bubble.width() // 2)
        bubble_y = self.y() - self.speech_bubble.height() - 8
        self.speech_bubble.move(bubble_x, bubble_y)

        self.speech_bubble.show()
        self._speech_bubble_timer.start(duration_ms)

    # ------------------------------------------------------------------
    # Hooks for app/main.py to override/connect
    # ------------------------------------------------------------------
    def on_open_chat_requested(self) -> None:
        """Overridden/monkeypatched by main.py to open the chat popup."""
        pass
