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
from app.memory.database import (
    archive_row,
    get_archived,
    get_connection,
    initialize_schema,
    list_done,
    restore_row,
)

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


def get_task_any(task_id: int) -> Optional[Task]:
    """Look up a task by id whether it's still active (`tasks`) or
    already archived as finished (`tasks_done`), without moving it. The
    task checklist UI shows both open and recently-done tasks together
    (see app/ui/task_window.py) and needs to inspect either kind without
    triggering a restore just to read its current state."""
    task = get_task(task_id)
    if task is not None:
        return task
    row = get_archived("tasks", task_id)
    return Task.from_row(row) if row is not None else None


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
    """Mark a task done, then move it out of the active `tasks` table into
    the `tasks_done` archive (product rule: the main table only ever holds
    tasks still open - see app/memory/database.py's archive_row())."""
    task = get_task(task_id)
    if task is None:
        raise TaskError(f"Task #{task_id} not found.")

    now = datetime.now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.DONE, now.strftime(ISO_FORMAT), task_id),
        )
    # Read back before archiving - archive_row() deletes the `tasks` row.
    completed = get_task(task_id)
    archive_row("tasks", task_id)
    logger.info("Completed task #%s: '%s'", task_id, task.title)
    return completed  # type: ignore[return-value]


def reopen_task(task_id: int) -> Task:
    """Reopen a task. Since complete_task()/cancel_task() move a finished
    task into `tasks_done`, the row usually has to be restored from there
    first - restore_row() is a no-op if it's actually still in `tasks`
    (e.g. reopening something that was never archived for some reason),
    so this works either way."""
    restore_row("tasks", task_id)
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
    """Cancel a task and archive it into `tasks_done` (status
    'cancelled') - same "main table = active only" rule as
    complete_task() above."""
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", (TaskStatus.CANCELLED, task_id)
        )
        if result.rowcount == 0:
            raise TaskError(f"Task #{task_id} not found.")
    archive_row("tasks", task_id)
    logger.info("Cancelled task #%s", task_id)


def delete_task(task_id: int) -> None:
    """Hard delete - checks the active `tasks` table first, then falls
    back to `tasks_done` (a finished task the UI is showing from the
    archive - see get_task_any() above - must still be deletable)."""
    with get_connection() as conn:
        result = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if result.rowcount > 0:
            logger.info("Deleted task #%s", task_id)
            return
        result = conn.execute("DELETE FROM tasks_done WHERE id = ?", (task_id,))
        if result.rowcount == 0:
            raise TaskError(f"Task #{task_id} not found.")
    logger.info("Deleted archived task #%s", task_id)


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


def list_archived_tasks(limit: int = 20) -> list[Task]:
    """Most-recently-finished (done or cancelled) tasks, newest first -
    reads `tasks_done`, never `tasks`. Used for "what have I finished"
    chat queries (app/ai/db_glossary.py) and by the task checklist UI to
    still show recently-checked-off items alongside open ones."""
    return [Task.from_row(row) for row in list_done("tasks", limit=limit)]
