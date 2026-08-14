"""
Shared look for every Mochi popup window (chat, reminders, tasks, timers,
settings, ...): frameless, translucent, rounded corners, small draggable
title bar with a close button.

Why frameless: rounded corners only render correctly if the OS window
manager isn't drawing its own square title bar/frame around the widget, so
`WA_TranslucentBackground` alone isn't enough - see spec section 4
("transparent window", "frameless window").

Subclasses just build their normal content into `self.content_layout`
instead of `self` directly, and get dragging / Escape-to-close / rounded
translucent styling for free.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

CORNER_RADIUS = 16
PANEL_BACKGROUND = "rgba(30, 27, 38, 235)"  # dark translucent panel
ACCENT_TEXT = "#f5f1fa"


class TranslucentDialog(QDialog):
    """Base class - translucent, rounded, frameless, draggable popup."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._drag_offset: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame(self)
        self.panel.setObjectName("mochiPanel")
        self.panel.setStyleSheet(
            f"""
            QFrame#mochiPanel {{
                background-color: {PANEL_BACKGROUND};
                border-radius: {CORNER_RADIUS}px;
                border: 1px solid rgba(255, 255, 255, 30);
            }}
            QLabel {{ color: {ACCENT_TEXT}; }}
            QLineEdit, QListWidget, QTextEdit, QComboBox, QDateTimeEdit, QSpinBox {{
                background-color: rgba(255, 255, 255, 18);
                color: {ACCENT_TEXT};
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 8px;
                padding: 4px 6px;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 30);
                color: {ACCENT_TEXT};
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: rgba(255, 255, 255, 48); }}
            QPushButton:pressed {{ background-color: rgba(255, 255, 255, 20); }}
            """
        )
        outer.addWidget(self.panel)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(14, 10, 14, 14)
        panel_layout.setSpacing(8)

        header = QHBoxLayout()
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(self._title_label)
        header.addStretch(1)

        self._close_button = QPushButton("×")
        self._close_button.setFixedSize(24, 24)
        self._close_button.setStyleSheet(
            "QPushButton { border-radius: 12px; font-weight: bold; padding: 0; }"
        )
        self._close_button.clicked.connect(self.close)
        header.addWidget(self._close_button)
        panel_layout.addLayout(header)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(8)
        panel_layout.addLayout(self.content_layout)

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
