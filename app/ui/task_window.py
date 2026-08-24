"""
Task (to-do) management window (V2).

Same "manual UI now, AI-callable tools later" pattern as
app/ui/reminder_window.py, but for simple checklist items rather than
timed/repeating reminders.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
)

from app.core.exceptions import TaskError
from app.core.logger import get_logger
from app.tasks import manager
from app.ui.base_window import TranslucentDialog

logger = get_logger("mochi.ui.tasks")


class TaskWindow(TranslucentDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("Mochi — Tasks", parent)
        self.setMinimumSize(380, 440)

        self._build_ui()
        self.refresh_list()

    def _build_ui(self) -> None:
        layout = self.content_layout

        label = QLabel("Add a task")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        input_row = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("What do you need to do?")
        self.title_input.returnPressed.connect(self._on_add_clicked)
        input_row.addWidget(self.title_input)

        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._on_add_clicked)
        input_row.addWidget(self.add_button)
        layout.addLayout(input_row)

        # Optional deadline (spec follow-up: "task i add it should be in
        # task list unless i give it deadline it should be there [too]") -
        # a task never *requires* a deadline (that's what makes it
        # different from a reminder), but you can attach one. Disabled by
        # default so "just add a task" stays a single field + Enter, same
        # as before.
        deadline_row = QHBoxLayout()
        self.deadline_checkbox = QCheckBox("Set deadline")
        self.deadline_input = QDateTimeEdit(datetime.now() + timedelta(hours=1))
        self.deadline_input.setCalendarPopup(True)
        self.deadline_input.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.deadline_input.setEnabled(False)
        self.deadline_checkbox.toggled.connect(self.deadline_input.setEnabled)
        deadline_row.addWidget(self.deadline_checkbox)
        deadline_row.addWidget(self.deadline_input, stretch=1)
        layout.addLayout(deadline_row)

        list_label = QLabel("Your tasks")
        list_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        layout.addWidget(list_label)

        self.task_list = QListWidget()
        layout.addWidget(self.task_list)

        button_row = QHBoxLayout()
        self.toggle_button = QPushButton("Toggle done")
        self.delete_button = QPushButton("Delete")
        self.refresh_button = QPushButton("Refresh")

        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.refresh_button.clicked.connect(self.refresh_list)

        for button in (self.toggle_button, self.delete_button, self.refresh_button):
            button_row.addWidget(button)
        layout.addLayout(button_row)

    def refresh_list(self) -> None:
        manager.ensure_ready()
        self.task_list.clear()
        tasks = [t for t in manager.list_tasks() if t.status in ("open", "done")]
        if not tasks:
            item = QListWidgetItem("No tasks yet. Add one above!")
            item.setFlags(Qt.NoItemFlags)
            self.task_list.addItem(item)
            return

        for task in tasks:
            prefix = "☑" if task.status == "done" else "☐"
            deadline_suffix = f"  (due {task.due_at:%Y-%m-%d %H:%M})" if task.due_at else ""
            item = QListWidgetItem(f"{prefix}  {task.title}{deadline_suffix}")
            item.setData(Qt.UserRole, task.id)
            self.task_list.addItem(item)

    def _selected_task_id(self) -> int | None:
        item = self.task_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _on_add_clicked(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "Mochi", "Give the task a title first!")
            return
        due_at = (
            self.deadline_input.dateTime().toPython()
            if self.deadline_checkbox.isChecked()
            else None
        )
        try:
            manager.create_task(title, due_at=due_at)
        except TaskError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
            return
        self.title_input.clear()
        self.deadline_checkbox.setChecked(False)
        self.refresh_list()

    def _on_toggle_clicked(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            return
        task = manager.get_task(task_id)
        if task is None:
            self.refresh_list()
            return
        try:
            if task.status == "done":
                manager.reopen_task(task_id)
            else:
                manager.complete_task(task_id)
                from app.core.events import Events, event_bus

                event_bus.publish(Events.TASK_COMPLETED, {"id": task_id})
        except TaskError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()

    def _on_delete_clicked(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            return
        try:
            manager.delete_task(task_id)
        except TaskError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
        self.refresh_list()
