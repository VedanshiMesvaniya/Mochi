"""
Local task (to-do) manager — V2.

Tasks are deliberately simpler than reminders: no due date, no repeat
rule, no scheduler/notification involvement. They're just a lightweight
checklist Mochi can hold for you ("remember I need to: buy milk, submit
assignment, ...") and mark off as you go.

Fully local, SQLite-backed, no AI dependency - same "storage layer works
standalone, AI calls into it later" pattern as reminders
(see PROJECT_ARCHITECTURE.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.core.exceptions import TaskError
from app.core.logger import get_logger
from app.memory.database import get_connection, initialize_schema

logger = get_logger("mochi.tasks")

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


class TaskStatus:
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: int
    title: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    # Optional deadline (spec follow-up: "task i add it should be in task
    # list unless i give it deadline it should be there [too]") - a task
    # with no deadline just sits in the checklist forever until done; one
    # *with* a deadline additionally shows up sorted to the front of the
    # list by due date, see list_tasks() below. None means no deadline.
    due_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Task":
        return cls(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            created_at=datetime.strptime(row["created_at"], ISO_FORMAT),
            completed_at=(
                datetime.strptime(row["completed_at"], ISO_FORMAT)
                if row["completed_at"]
                else None
            ),
            due_at=(
                datetime.strptime(row["due_at"], ISO_FORMAT)
                # sqlite3.Row from a schema-migrated older DB always has
                # the column once initialize_schema() has run, but guard
                # with keys() anyway in case a row is ever built by hand.
                if "due_at" in row.keys() and row["due_at"]
                else None
            ),
        )


def ensure_ready() -> None:
    initialize_schema()


def create_task(title: str, due_at: Optional[datetime] = None) -> Task:
    if not title or not title.strip():
        raise TaskError("Task title cannot be empty.")

    now = datetime.now()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (title, status, created_at, due_at) VALUES (?, ?, ?, ?)",
            (
                title.strip(),
                TaskStatus.OPEN,
                now.strftime(ISO_FORMAT),
                due_at.strftime(ISO_FORMAT) if due_at else None,
            ),
        )
        task_id = cursor.lastrowid

    logger.info(
        "Created task #%s: '%s'%s",
        task_id,
        title,
        f" (due {due_at:%Y-%m-%d %H:%M})" if due_at else "",
    )
    task = get_task(task_id)
    if task is None:  # pragma: no cover - defensive
        raise TaskError("Failed to read back newly created task.")
    return task


def get_task(task_id: int) -> Optional[Task]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return Task.from_row(row) if row else None


def list_tasks(status: Optional[str] = None) -> list[Task]:
    query = "SELECT * FROM tasks"
    params: list = []
    if status is not None:
        query += " WHERE status = ?"
        params.append(status)
    # Tasks with a deadline surface first (soonest due first, per spec
    # follow-up), undated tasks fall back to plain creation order behind
    # them - `due_at IS NULL` sorts false (0) before true (1) so dated
    # rows always win the primary ORDER BY key.
    query += " ORDER BY (due_at IS NULL) ASC, due_at ASC, created_at ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Task.from_row(row) for row in rows]


def complete_task(task_id: int) -> Task:
    task = get_task(task_id)
    if task is None:
        raise TaskError(f"Task #{task_id} not found.")

    now = datetime.now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.DONE, now.strftime(ISO_FORMAT), task_id),
        )
    logger.info("Completed task #%s: '%s'", task_id, task.title)
    return get_task(task_id)  # type: ignore[return-value]


def reopen_task(task_id: int) -> Task:
    task = get_task(task_id)
    if task is None:
        raise TaskError(f"Task #{task_id} not found.")

    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = NULL WHERE id = ?",
            (TaskStatus.OPEN, task_id),
        )
    logger.info("Reopened task #%s", task_id)
    return get_task(task_id)  # type: ignore[return-value]


def cancel_task(task_id: int) -> None:
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", (TaskStatus.CANCELLED, task_id)
        )
        if result.rowcount == 0:
            raise TaskError(f"Task #{task_id} not found.")
    logger.info("Cancelled task #%s", task_id)


def delete_task(task_id: int) -> None:
    with get_connection() as conn:
        result = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if result.rowcount == 0:
            raise TaskError(f"Task #{task_id} not found.")
    logger.info("Deleted task #%s", task_id)


def update_task(task_id: int, title: str) -> Task:
    if not title or not title.strip():
        raise TaskError("Task title cannot be empty.")

    task = get_task(task_id)
    if task is None:
        raise TaskError(f"Task #{task_id} not found.")

    with get_connection() as conn:
        conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (title.strip(), task_id))
    logger.info("Updated task #%s", task_id)
    return get_task(task_id)  # type: ignore[return-value]


def set_due_date(task_id: int, due_at: Optional[datetime]) -> Task:
    """Set (or, with due_at=None, clear) a task's deadline."""
    task = get_task(task_id)
    if task is None:
        raise TaskError(f"Task #{task_id} not found.")

    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET due_at = ? WHERE id = ?",
            (due_at.strftime(ISO_FORMAT) if due_at else None, task_id),
        )
    logger.info("Set due date for task #%s: %s", task_id, due_at)
    return get_task(task_id)  # type: ignore[return-value]
