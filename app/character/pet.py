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
from PySide6.QtGui import QCursor, QMouseEvent, QAction
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMenu, QApplication, QLabel

SPEECH_BUBBLE_DEFAULT_MS = 6000

from app.character.behavior import BehaviorEngine
from app.character.movement import Mover, ScreenBounds
from app.character.pixel_face import PixelFaceWidget
from app.character.state_machine import CharacterState, CharacterStateMachine
from app.core.config import settings
from app.core.events import Events, event_bus
from app.core.logger import get_logger

SPEECH_BUBBLE_CHAT_MS = 5000
FACE_TICK_MS = 50  # ~20fps - cheap since it's vector drawing, not sprite decoding
BEHAVIOR_TICK_MS = 2000  # must match BehaviorEngine.tick_interval_seconds below

logger = get_logger("mochi.pet")


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

        self._setup_window()
        self._setup_ui()
        self._setup_tray_free_menu()
        self._setup_timers()
        self._place_on_screen()
        self._subscribe_to_events()

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

    def _setup_tray_free_menu(self) -> None:
        """Right-click context menu (spec section 13)."""
        self.context_menu = QMenu(self)
        self.action_chat = QAction("Chat", self)
        self.action_reminders = QAction("Reminders", self)
        self.action_tasks = QAction("Tasks", self)
        self.action_timers = QAction("Timers", self)
        self.action_calendar = QAction("Calendar", self)
        self.action_memories = QAction("Memories", self)
        self.action_settings = QAction("Settings", self)
        self.action_sleep = QAction("Sleep", self)
        self.action_exit = QAction("Exit", self)

        self.action_exit.triggered.connect(QApplication.quit)
        self.action_sleep.triggered.connect(
            lambda: self.state_machine.set_state(CharacterState.SLEEP)
        )
        self.action_chat.triggered.connect(self.on_open_chat_requested)
        self.action_reminders.triggered.connect(self._open_reminder_window)
        self.action_tasks.triggered.connect(self._open_task_window)
        self.action_timers.triggered.connect(self._open_timer_window)

        for action in (
            self.action_chat,
            self.action_reminders,
            self.action_tasks,
            self.action_timers,
            self.action_calendar,
            self.action_memories,
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
        self.state_machine.set_state(CharacterState.HAPPY)

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
            self.behavior_engine.tick(self.state_machine.set_state)

    # ------------------------------------------------------------------
    # Mouse interaction (spec section 13)
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.behavior_engine.mark_interacted()
            self.state_machine.set_state(CharacterState.SURPRISED)
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
    # Reminders window (spec section 13/20 - V1)
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
    # Tasks window (V2)
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
    # Timers window (V2)
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
            )
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _on_chat_thinking(self) -> None:
        """Called the instant a message is sent, before a reply exists -
        spec: 'think while the local model is processing'. Chat may fall
        through to a local LLM call (bounded by a few seconds), so this
        gives immediate visual feedback rather than a dead pause."""
        self.behavior_engine.mark_interacted()
        self.state_machine.set_state(CharacterState.THINKING)

    def _on_chat_reaction(self, reaction) -> None:
        """Called by the chat window once a message's intent has been
        detected (spec: 'on chat detect user intent and then mochi
        react') - this is where the reaction actually becomes visible on
        the character: animation, sound, and a speech bubble.
        """
        if reaction.animation is not None:
            self.state_machine.set_state(reaction.animation)
        if reaction.emotion is not None:
            # react=False: we already picked the exact animation above,
            # this just keeps mood tracking in sync without overriding it.
            self.state_machine.set_emotion(reaction.emotion, react=False)
        if reaction.sound:
            from app.core.events import Events, event_bus

            event_bus.publish(Events.SOUND_REQUESTED, {"sound": reaction.sound})
        self.show_speech_bubble(reaction.text, duration_ms=SPEECH_BUBBLE_CHAT_MS)
