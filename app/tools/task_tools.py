"""
Task tools (spec section 25) - the future AI execution target for
requests like "remember I need to submit my assignment" (as a checklist
item, distinct from a timed reminder). See app/tools/reminder_tools.py for
the pattern this follows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.exceptions import TaskError, ToolValidationError
from app.core.logger import get_logger
from app.tasks import manager
from app.tasks.manager import Task

logger = get_logger("mochi.tools.tasks")

TOOL_SCHEMAS = {
    "create_task": {
        "title": "str, required",
        "due_at_iso": "str, optional ISO 8601 datetime - omit for no deadline",
    },
    "list_tasks": {"status": "str, optional ('open' | 'done' | 'cancelled' | null = all)"},
    "complete_task": {"task_id": "int, required"},
    "reopen_task": {"task_id": "int, required"},
    "cancel_task": {"task_id": "int, required"},
    "delete_task": {"task_id": "int, required"},
    "update_task": {"task_id": "int, required", "title": "str, required"},
    "set_task_due_date": {
        "task_id": "int, required",
        "due_at_iso": "str, optional ISO 8601 datetime - omit/null clears the deadline",
    },
}


def _serialize(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "due_at": task.due_at.isoformat() if task.due_at else None,
    }


def create_task(title: str, due_at_iso: Optional[str] = None) -> dict:
    due_at = None
    if due_at_iso:
        try:
            due_at = datetime.fromisoformat(due_at_iso)
        except ValueError as exc:
            raise ToolValidationError(f"Invalid due_at_iso: {due_at_iso!r}") from exc
    try:
        task = manager.create_task(title, due_at=due_at)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(task)


def set_task_due_date(task_id: int, due_at_iso: Optional[str] = None) -> dict:
    due_at = None
    if due_at_iso:
        try:
            due_at = datetime.fromisoformat(due_at_iso)
        except ValueError as exc:
            raise ToolValidationError(f"Invalid due_at_iso: {due_at_iso!r}") from exc
    try:
        task = manager.set_due_date(task_id, due_at)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(task)


def list_tasks(status: Optional[str] = None) -> list[dict]:
    return [_serialize(t) for t in manager.list_tasks(status=status)]


def complete_task(task_id: int) -> dict:
    try:
        task = manager.complete_task(task_id)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(task)


def reopen_task(task_id: int) -> dict:
    try:
        task = manager.reopen_task(task_id)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(task)


def cancel_task(task_id: int) -> dict:
    try:
        manager.cancel_task(task_id)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    task = manager.get_task(task_id)
    return _serialize(task) if task else {"id": task_id, "status": "cancelled"}


def delete_task(task_id: int) -> dict:
    try:
        manager.delete_task(task_id)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    return {"id": task_id, "deleted": True}


def update_task(task_id: int, title: str) -> dict:
    try:
        task = manager.update_task(task_id, title)
    except TaskError as exc:
        raise ToolValidationError(str(exc)) from exc
    return _serialize(task)
