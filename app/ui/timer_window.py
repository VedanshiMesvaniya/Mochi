"""
Quick timer window (V2).

Lets the user start a countdown timer with a label + duration, see active
timers with live remaining-time display, cancel one, or add time. Actual
"timer finished" behavior (wake/sound/speech/OS notification) is handled by
`app/timers/notifications.py`, subscribed to `Events.TIMER_DONE` - this
window only manages starting/viewing/cancelling timers.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer as QtQTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from app.core.exceptions import TimerError
from app.core.logger import get_logger
from app.timers import manager
from app.ui.base_window import TranslucentDialog

logger = get_logger("mochi.ui.timers")

UNIT_TO_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}


class TimerWindow(TranslucentDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("Mochi — Timers", parent)
        self.setMinimumSize(380, 420)

        self._build_ui()
        self.refresh_list()

        # Live countdown refresh, independent of the backend scheduler's
        # own (slower) polling - purely cosmetic, doesn't touch the DB.
        self._refresh_timer = QtQTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_list)
        self._refresh_timer.start(1000)

    def _build_ui(self) -> None:
        layout = self.content_layout

        label = QLabel("Start a timer")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("What's this timer for? (optional)")
        layout.addWidget(self.label_input)

        row = QHBoxLayout()
        self.amount_input = QSpinBox()
        self.amount_input.setRange(1, 999)
        self.amount_input.setValue(10)
        row.addWidget(self.amount_input)

        self.unit_input = QComboBox()
        self.unit_input.addItems(["minutes", "seconds", "hours"])
        row.addWidget(self.unit_input)
        layout.addLayout(row)

        self.start_button = QPushButton("Start timer")
        self.start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.start_button)

        list_label = QLabel("Active timers")
        list_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        layout.addWidget(list_label)

        self.timer_list = QListWidget()
        layout.addWidget(self.timer_list)

        button_row = QHBoxLayout()
        self.add_minute_button = QPushButton("+1 min")
        self.cancel_button = QPushButton("Cancel")
        self.add_minute_button.clicked.connect(self._on_add_minute_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        button_row.addWidget(self.add_minute_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

    def refresh_list(self) -> None:
        manager.ensure_ready()
        selected_id = self._selected_timer_id()

        self.timer_list.clear()
        timers = manager.list_active_timers()
        if not timers:
            item = QListWidgetItem("No active timers.")
            item.setFlags(Qt.NoItemFlags)
            self.timer_list.addItem(item)
            return

        for timer in timers:
            remaining = int(timer.seconds_remaining)
            mins, secs = divmod(remaining, 60)
            item = QListWidgetItem(f"{timer.label}  —  {mins:02d}:{secs:02d} remaining")
            item.setData(Qt.UserRole, timer.id)
            self.timer_list.addItem(item)
            if timer.id == selected_id:
                self.timer_list.setCurrentItem(item)

    def _selected_timer_id(self) -> int | None:
        item = self.timer_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _on_start_clicked(self) -> None:
        label = self.label_input.text().strip() or "Timer"
        amount = self.amount_input.value()
        unit_seconds = UNIT_TO_SECONDS[self.unit_input.currentText()]
        duration_seconds = amount * unit_seconds

        try:
            manager.start_timer(duration_seconds, label)
        except TimerError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
            return

        self.label_input.clear()
        self.refresh_list()

    def _on_add_minute_clicked(self) -> None:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return
        try:
            manager.add_time(timer_id, 60)
        except TimerError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()

    def _on_cancel_clicked(self) -> None:
        timer_id = self._selected_timer_id()
        if timer_id is None:
            return
        try:
            manager.cancel_timer(timer_id)
        except TimerError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._refresh_timer.stop()
        super().closeEvent(event)
