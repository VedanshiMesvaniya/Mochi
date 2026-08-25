"""
Reminder management window (spec section 20, V1 scope).

A small Qt dialog that lets the user create, view, complete, snooze, and
delete local reminders directly - no AI/natural-language parsing required.
Once Phase 2 (AI) lands, natural-language requests will call the exact same
`app/reminders/manager.py` functions this window uses, so behavior stays
consistent between "type it in this form" and "just ask Mochi."
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
)

from app.core.exceptions import ReminderError
from app.core.logger import get_logger
from app.reminders import manager
from app.ui.base_window import TranslucentDialog

logger = get_logger("mochi.ui.reminders")

REPEAT_OPTIONS = [
    ("Does not repeat", None),
    ("Every day", "DAILY"),
    ("Every week", "WEEKLY"),
    ("Every month", "MONTHLY"),
]


class ReminderWindow(TranslucentDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("Mochi — Reminders", parent)
        self.setMinimumSize(420, 480)

        self._build_ui()
        self.refresh_list()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = self.content_layout

        # --- Create form ---
        form_label = QLabel("New reminder")
        form_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(form_label)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("What should I remind you about?")
        layout.addWidget(self.title_input)

        row = QHBoxLayout()
        self.datetime_input = QDateTimeEdit(datetime.now() + timedelta(minutes=5))
        self.datetime_input.setCalendarPopup(True)
        self.datetime_input.setDisplayFormat("yyyy-MM-dd HH:mm")
        row.addWidget(self.datetime_input)

        self.repeat_input = QComboBox()
        for label, _ in REPEAT_OPTIONS:
            self.repeat_input.addItem(label)
        row.addWidget(self.repeat_input)
        layout.addLayout(row)

        self.add_button = QPushButton("Add reminder")
        self.add_button.clicked.connect(self._on_add_clicked)
        layout.addWidget(self.add_button)

        # --- List ---
        list_label = QLabel("Your reminders")
        list_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        layout.addWidget(list_label)

        self.reminder_list = QListWidget()
        layout.addWidget(self.reminder_list)

        button_row = QHBoxLayout()
        self.complete_button = QPushButton("Mark done")
        self.snooze_button = QPushButton("Snooze 10m")
        self.delete_button = QPushButton("Delete")
        self.refresh_button = QPushButton("Refresh")

        self.complete_button.clicked.connect(self._on_complete_clicked)
        self.snooze_button.clicked.connect(self._on_snooze_clicked)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.refresh_button.clicked.connect(self.refresh_list)

        for button in (
            self.complete_button,
            self.snooze_button,
            self.delete_button,
            self.refresh_button,
        ):
            button_row.addWidget(button)
        layout.addLayout(button_row)

    # ------------------------------------------------------------------
    def refresh_list(self) -> None:
        manager.ensure_ready()
        self.reminder_list.clear()
        reminders = [
            r for r in manager.list_reminders() if r.status == "pending"
        ]
        if not reminders:
            item = QListWidgetItem("No pending reminders. Add one above!")
            item.setFlags(Qt.NoItemFlags)
            self.reminder_list.addItem(item)
            return

        for reminder in reminders:
            repeat_suffix = f"  (repeats {reminder.repeat_rule})" if reminder.repeat_rule else ""
            text = f"#{reminder.id}  {reminder.due_at:%Y-%m-%d %I:%M %p}  —  {reminder.title}{repeat_suffix}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, reminder.id)
            self.reminder_list.addItem(item)

    def _selected_reminder_id(self) -> int | None:
        item = self.reminder_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    # ------------------------------------------------------------------
    def _on_add_clicked(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "Mochi", "Give the reminder a title first!")
            return

        due_at = self.datetime_input.dateTime().toPython()
        repeat_rule = REPEAT_OPTIONS[self.repeat_input.currentIndex()][1]

        try:
            manager.create_reminder(title, due_at, repeat_rule)
        except ReminderError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
            return

        self.title_input.clear()
        self.refresh_list()

    def _on_complete_clicked(self) -> None:
        reminder_id = self._selected_reminder_id()
        if reminder_id is None:
            return
        try:
            manager.complete_reminder(reminder_id)
            from app.core.events import Events, event_bus

            event_bus.publish(Events.REMINDER_COMPLETED, {"id": reminder_id})
        except ReminderError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()

    def _on_snooze_clicked(self) -> None:
        reminder_id = self._selected_reminder_id()
        if reminder_id is None:
            return
        try:
            manager.snooze_reminder(reminder_id, minutes=10)
        except ReminderError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()

    def _on_delete_clicked(self) -> None:
        reminder_id = self._selected_reminder_id()
        if reminder_id is None:
            return
        try:
            manager.delete_reminder(reminder_id)
        except ReminderError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()
