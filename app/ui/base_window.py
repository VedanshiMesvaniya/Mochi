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
_LIGHT_PANEL_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
    " stop:0 rgba(255, 255, 255, 215),"
    " stop:0.5 rgba(232, 226, 250, 200),"
    " stop:1 rgba(213, 232, 250, 205))"
)
_LIGHT_BORDER_HIGHLIGHT = "rgba(255, 255, 255, 160)"
_LIGHT_INPUT_BORDER = "rgba(255, 255, 255, 190)"
_LIGHT_TEXT_COLOR = "#3a3350"  # dark enough to read on light glass
_LIGHT_MUTED_TEXT_COLOR = "#6e6785"
_LIGHT_INPUT_BG = "rgba(255, 255, 255, 130)"
_LIGHT_BUTTON_BG = "rgba(255, 255, 255, 140)"
_LIGHT_BUTTON_HOVER = "rgba(255, 255, 255, 190)"
_LIGHT_BUTTON_PRESSED = "rgba(255, 255, 255, 100)"
_LIGHT_BUTTON_DISABLED_TEXT = "rgba(58, 51, 80, 110)"

# Dark counterpart (spec: "now you made font dark what if it's in dark
# background it should be adaptive") - a light-on-dark version of the same
# frosted-glass look, not a plain flat panel, so popups still read as
# "Mochi's UI" rather than a generic dark-mode override. Same shape/values
# as the light palette above so it's easy to keep the two in sync.
_DARK_PANEL_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
    " stop:0 rgba(40, 36, 58, 225),"
    " stop:0.5 rgba(32, 29, 48, 220),"
    " stop:1 rgba(26, 32, 46, 220))"
)
_DARK_BORDER_HIGHLIGHT = "rgba(255, 255, 255, 40)"
_DARK_INPUT_BORDER = "rgba(255, 255, 255, 55)"
_DARK_TEXT_COLOR = "#f1edff"
_DARK_MUTED_TEXT_COLOR = "#b9b2d1"
_DARK_INPUT_BG = "rgba(255, 255, 255, 22)"
_DARK_BUTTON_BG = "rgba(255, 255, 255, 28)"
_DARK_BUTTON_HOVER = "rgba(255, 255, 255, 50)"
_DARK_BUTTON_PRESSED = "rgba(255, 255, 255, 16)"
_DARK_BUTTON_DISABLED_TEXT = "rgba(241, 237, 255, 90)"

# Kept as the original names for anything that still imports these
# directly (e.g. other modules referencing base_window.TEXT_COLOR) - these
# stay the light values; TranslucentDialog itself picks light-vs-dark
# fresh at construction time via _current_palette() below, so it actually
# adapts rather than being stuck on whichever of these was imported.
PANEL_GRADIENT = _LIGHT_PANEL_GRADIENT
BORDER_HIGHLIGHT = _LIGHT_BORDER_HIGHLIGHT
TEXT_COLOR = _LIGHT_TEXT_COLOR
MUTED_TEXT_COLOR = _LIGHT_MUTED_TEXT_COLOR

# Header dots, macOS-style: recognizable close/minimize affordance, and a
# third that's actually useful here (always-on-top) rather than decorative.
DOT_CLOSE = "#ff6b6b"
DOT_MINIMIZE = "#ffd166"
DOT_PIN = "#8ee6a3"
DOT_PIN_ACTIVE = "#4fbf6b"


def _current_palette() -> dict:
    """Light or dark frosted-glass color set for right now, following the
    OS/Qt dark-mode setting - reuses app.character.pet._is_dark_mode so
    popups and Mochi's own speech bubble agree on "dark mode or not"
    rather than each having their own detector. Imported lazily (function
    body, not module level) to avoid a circular import, since
    app.character.pet also imports from app.ui at various points.
    """
    try:
        from app.character.pet import _is_dark_mode

        dark = _is_dark_mode()
    except Exception:  # noqa: BLE001 - theme detection must never break a popup
        dark = False

    if dark:
        return {
            "panel_gradient": _DARK_PANEL_GRADIENT,
            "border_highlight": _DARK_BORDER_HIGHLIGHT,
            "input_border": _DARK_INPUT_BORDER,
            "text_color": _DARK_TEXT_COLOR,
            "muted_text_color": _DARK_MUTED_TEXT_COLOR,
            "input_bg": _DARK_INPUT_BG,
            "button_bg": _DARK_BUTTON_BG,
            "button_hover": _DARK_BUTTON_HOVER,
            "button_pressed": _DARK_BUTTON_PRESSED,
            "button_disabled_text": _DARK_BUTTON_DISABLED_TEXT,
        }
    return {
        "panel_gradient": _LIGHT_PANEL_GRADIENT,
        "border_highlight": _LIGHT_BORDER_HIGHLIGHT,
        "input_border": _LIGHT_INPUT_BORDER,
        "text_color": _LIGHT_TEXT_COLOR,
        "muted_text_color": _LIGHT_MUTED_TEXT_COLOR,
        "input_bg": _LIGHT_INPUT_BG,
        "button_bg": _LIGHT_BUTTON_BG,
        "button_hover": _LIGHT_BUTTON_HOVER,
        "button_pressed": _LIGHT_BUTTON_PRESSED,
        "button_disabled_text": _LIGHT_BUTTON_DISABLED_TEXT,
    }


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

        # Read fresh at construction time (not the module-level light
        # defaults above) so every popup reflects the OS's actual current
        # dark/light setting rather than whatever was true at import time.
        palette = _current_palette()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)  # room for the drop shadow

        self.panel = QFrame(self)
        self.panel.setObjectName("mochiPanel")
        self.panel.setStyleSheet(
            f"""
            QFrame#mochiPanel {{
                background: {palette['panel_gradient']};
                border-radius: {CORNER_RADIUS}px;
                border: 1px solid {palette['border_highlight']};
            }}
            QLabel {{ color: {palette['text_color']}; background: transparent; }}
            QLineEdit, QListWidget, QTextEdit, QComboBox, QDateTimeEdit, QSpinBox {{
                background-color: {palette['input_bg']};
                color: {palette['text_color']};
                border: 1px solid {palette['input_border']};
                border-radius: 10px;
                padding: 5px 8px;
                selection-background-color: rgba(142, 156, 230, 150);
            }}
            QListWidget::item {{ padding: 3px 2px; }}
            QPushButton {{
                background-color: {palette['button_bg']};
                color: {palette['text_color']};
                border: 1px solid {palette['input_border']};
                border-radius: 10px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: {palette['button_hover']}; }}
            QPushButton:pressed {{ background-color: {palette['button_pressed']}; }}
            QPushButton:disabled {{ color: {palette['button_disabled_text']}; }}
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
            f"font-weight: 600; font-size: 13px; color: {palette['muted_text_color']};"
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
