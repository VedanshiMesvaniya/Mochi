"""
Shared look for every Mochi popup window (chat, reminders, tasks, timers,
settings, ...): frameless, translucent frosted glass, rounded corners, a
small draggable header with macOS-style traffic-light dots.

Why frameless: rounded corners only render correctly if the OS window
manager isn't drawing its own square title bar/frame around the widget, so
`WA_TranslucentBackground` alone isn't enough - see spec section 4
("transparent window", "frameless window").

Subclasses just build their normal content into `self.content_layout`
instead of `self` directly, and get dragging / Escape-to-close / the
frosted-glass styling for free.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

CORNER_RADIUS = 22

# Soft frosted-glass palette - a light lavender-to-sky gradient tint, echoing
# Mochi's own cute-pastel character design rather than a generic dark panel.
PANEL_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
    " stop:0 rgba(255, 255, 255, 215),"
    " stop:0.5 rgba(232, 226, 250, 200),"
    " stop:1 rgba(213, 232, 250, 205))"
)
BORDER_HIGHLIGHT = "rgba(255, 255, 255, 160)"
TEXT_COLOR = "#3a3350"  # dark enough to read on light glass
MUTED_TEXT_COLOR = "#6e6785"

# Header dots, macOS-style: recognizable close/minimize affordance, and a
# third that's actually useful here (always-on-top) rather than decorative.
DOT_CLOSE = "#ff6b6b"
DOT_MINIMIZE = "#ffd166"
DOT_PIN = "#8ee6a3"
DOT_PIN_ACTIVE = "#4fbf6b"


class _DotButton(QPushButton):
    def __init__(self, color: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(13, 13)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._apply_style()

    def set_color(self, color: str) -> None:
        self._color = color
        self._apply_style()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self._color};
                border: 1px solid rgba(0, 0, 0, 35);
                border-radius: 6px;
                padding: 0;
            }}
            QPushButton:hover {{ background-color: {self._color}; border: 1px solid rgba(0,0,0,70); }}
            """
        )


class TranslucentDialog(QDialog):
    """Base class - frosted glass, rounded, frameless, draggable popup."""

    def __init__(self, title: str, parent=None, pinned_by_default: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._drag_offset: QPoint | None = None
        self._pinned = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)  # room for the drop shadow

        self.panel = QFrame(self)
        self.panel.setObjectName("mochiPanel")
        self.panel.setStyleSheet(
            f"""
            QFrame#mochiPanel {{
                background: {PANEL_GRADIENT};
                border-radius: {CORNER_RADIUS}px;
                border: 1px solid {BORDER_HIGHLIGHT};
            }}
            QLabel {{ color: {TEXT_COLOR}; background: transparent; }}
            QLineEdit, QListWidget, QTextEdit, QComboBox, QDateTimeEdit, QSpinBox {{
                background-color: rgba(255, 255, 255, 130);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 190);
                border-radius: 10px;
                padding: 5px 8px;
                selection-background-color: rgba(142, 156, 230, 150);
            }}
            QListWidget::item {{ padding: 3px 2px; }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 140);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 190);
                border-radius: 10px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: rgba(255, 255, 255, 190); }}
            QPushButton:pressed {{ background-color: rgba(255, 255, 255, 100); }}
            QPushButton:disabled {{ color: rgba(58, 51, 80, 110); }}
            """
        )
        outer.addWidget(self.panel)

        # Soft elevation - frameless translucent windows read as "floating
        # glass" rather than a flat sticker once they have a real shadow.
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(60, 50, 90, 90))
        self.panel.setGraphicsEffect(shadow)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(16, 12, 16, 16)
        panel_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)

        self._close_dot = _DotButton(DOT_CLOSE, "Close")
        self._close_dot.clicked.connect(self.close)
        header.addWidget(self._close_dot)

        self._minimize_dot = _DotButton(DOT_MINIMIZE, "Minimize")
        self._minimize_dot.clicked.connect(self.showMinimized)
        header.addWidget(self._minimize_dot)

        self._pin_dot = _DotButton(DOT_PIN, "Keep on top")
        self._pin_dot.clicked.connect(self._toggle_pinned)
        header.addWidget(self._pin_dot)

        header.addSpacing(8)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {MUTED_TEXT_COLOR};"
        )
        header.addWidget(self._title_label)
        header.addStretch(1)
        panel_layout.addLayout(header)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(8)
        panel_layout.addLayout(self.content_layout)

        # Some popups (chat, in particular) are meant to be interacted
        # with *while* the user does other things (switch to a terminal,
        # check something, come back) - without WindowStaysOnTopHint a
        # plain Qt.Tool window has no taskbar entry and can end up buried
        # behind whatever's clicked next, which reads as "it closed
        # itself". Default those to pinned; still toggleable via the dot.
        if pinned_by_default:
            self._toggle_pinned()

    # ------------------------------------------------------------------
    def _toggle_pinned(self) -> None:
        self._pinned = not self._pinned
        self._pin_dot.set_color(DOT_PIN_ACTIVE if self._pinned else DOT_PIN)
        flags = self.windowFlags()
        flags = (flags | Qt.WindowStaysOnTopHint) if self._pinned else (flags & ~Qt.WindowStaysOnTopHint)
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        # setWindowFlags() can recreate the native window on some
        # platforms, silently dropping WA_TranslucentBackground - reapply
        # it so pinning never turns the glass panel into an opaque box.
        self.setAttribute(Qt.WA_TranslucentBackground)
        if was_visible:
            self.show()

    # ------------------------------------------------------------------
    # Draggable via the whole panel (frameless windows have no OS title bar)
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
