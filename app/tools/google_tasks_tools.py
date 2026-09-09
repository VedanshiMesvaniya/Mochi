"""
Google Tasks tools - read + confirmed writes.

Same shape as calendar_tools.py (which this deliberately mirrors) and
the same reasoning: plain JSON-friendly arguments in, plain dicts out,
`ToolValidationError` on bad input, and `create_google_task` /
`complete_google_task` / `delete_google_task` all require an explicit
`confirmed=True` keyword and raise `ConfirmationRequiredError`
otherwise. Enforced *here*, at the tool boundary, so a write can never
happen without confirmation regardless of what calls this module.

Distinct from task_tools.py, which is Mochi's own local to-do list -
this only talks to the optional Google Tasks sync
(app/tasks/google_tasks.py). Named with the `google_` prefix throughout
(function names, tool schema keys) specifically so intent matching and
the LLM's tool choice can never confuse "add a task" (local) with "add
it to my Google tasks" (synced) - see app/ai/intent.py.
"""

from __future__ import annotations

from typing import Optional

from app.core.exceptions import ConfirmationRequiredError, TaskSyncError, ToolValidationError
from app.core.logger import get_logger
from app.tasks import google_tasks

logger = get_logger("mochi.tools.google_tasks")

TOOL_SCHEMAS = {
    "connect_google_tasks": {},
    "disconnect_google_tasks": {},
    "get_google_tasks": {"show_completed": "bool, optional, default false"},
    "find_google_task": {"query": "str, required"},
    "create_google_task": {
        "title": "str, required",
        "notes": "str, optional",
        "due_date": "str, optional (YYYY-MM-DD)",
        "confirmed": "bool, required - must be true (user confirmation)",
    },
    "complete_google_task": {
        "task_id": "str, required",
        "confirmed": "bool, required - must be true (user confirmation)",
    },
    "delete_google_task": {
        "task_id": "str, required",
        "confirmed": "bool, required - must be true (user confirmation)",
    },
}


def connect_google_tasks() -> dict:
    """Same underlying OAuth flow as connect_google_calendar() - kept as
    a separate tool name so the AI/tests can call it by the name that
    matches what it's asking for, even though it's one shared sign-in."""
    try:
        google_tasks.connect()
    except TaskSyncError as exc:
        raise ToolValidationError(str(exc)) from exc
    return {"connected": True}


def disconnect_google_tasks() -> dict:
    removed = google_tasks.disconnect()
    return {"disconnected": True, "had_connection": removed}


def get_google_tasks(show_completed: bool = False) -> list[dict]:
    try:
        return google_tasks.list_tasks(show_completed=show_completed)
    except TaskSyncError as exc:
        raise ToolValidationError(str(exc)) from exc


def find_google_task(query: str) -> list[dict]:
    if not query or not query.strip():
        raise ToolValidationError("query cannot be empty.")
    try:
        return google_tasks.find_task(query)
    except TaskSyncError as exc:
        raise ToolValidationError(str(exc)) from exc


def create_google_task(
    title: str,
    notes: Optional[str] = None,
    due_date: Optional[str] = None,
    confirmed: bool = False,
) -> dict:
    if not confirmed:
        raise ConfirmationRequiredError(
            "Creating a Google Task requires explicit user confirmation."
        )
    if not title or not title.strip():
        raise ToolValidationError("Task title cannot be empty.")
    try:
        task = google_tasks.create_task(title.strip(), notes=notes, due_iso_date=due_date)
    except TaskSyncError as exc:
        raise ToolValidationError(str(exc)) from exc
    logger.info("Chat-confirmed Google Task created: '%s'", title)
    return task


def complete_google_task(task_id: str, confirmed: bool = False) -> dict:
    if not confirmed:
        raise ConfirmationRequiredError(
            "Completing a Google Task requires explicit user confirmation."
        )
    if not task_id:
        raise ToolValidationError("task_id is required.")
    try:
        task = google_tasks.complete_task(task_id)
    except TaskSyncError as exc:
        raise ToolValidationError(str(exc)) from exc
    logger.info("Chat-confirmed Google Task completed: %s", task_id)
    return task


def delete_google_task(task_id: str, confirmed: bool = False) -> dict:
    if not confirmed:
        raise ConfirmationRequiredError(
            "Deleting a Google Task requires explicit user confirmation."
        )
    if not task_id:
        raise ToolValidationError("task_id is required.")
    try:
        google_tasks.delete_task(task_id)
    except TaskSyncError as exc:
        raise ToolValidationError(str(exc)) from exc
    logger.info("Chat-confirmed Google Task deleted: %s", task_id)
    return {"task_id": task_id, "deleted": True}
