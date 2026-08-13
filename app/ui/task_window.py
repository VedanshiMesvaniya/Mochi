"""
Task (to-do) management window (V2).

Same "manual UI now, AI-callable tools later" pattern as
app/ui/reminder_window.py, but for simple checklist items rather than
timed/repeating reminders.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.core.exceptions import TaskError
from app.core.logger import get_logger
from app.tasks import manager

logger = get_logger("mochi.ui.tasks")


class TaskWindow(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mochi — Tasks")
        self.setMinimumSize(380, 440)

        self._build_ui()
        self.refresh_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

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
            item = QListWidgetItem(f"{prefix}  {task.title}")
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
        try:
            manager.create_task(title)
        except TaskError as exc:
            QMessageBox.warning(self, "Mochi", str(exc))
            return
        self.title_input.clear()
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
