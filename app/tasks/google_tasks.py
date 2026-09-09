"""
Google Tasks integration.

Shares Google Calendar's OAuth flow and token file (see
app/calendar/google_calendar.py) - there is only ever ONE Google sign-in
for the whole app. Turning on `settings.google_tasks_enabled` simply
widens what that one "connect my calendar" flow requests, so Tasks
permission is granted alongside Calendar permission in the same consent
screen, not a second separate sign-in. This module never runs its own
OAuth flow - connect()/disconnect() here just delegate to
app/calendar/google_calendar.py's.

Distinct from app/tasks/manager.py, which is Mochi's own fully local
to-do list (no Google account, no internet). This module is only the
optional Google Tasks sync - off by default, like Calendar.

Two capability levels, gated by `settings.google_tasks_write_enabled`:

  * **Read (default)** - scope `tasks.readonly`, enough to answer
    "what's on my Google task list".
  * **Read + write (opt-in)** - scope `tasks`, letting Mochi
    create/complete/delete Google Tasks. Every write in this module
    additionally requires an explicit `confirmed=True` at the
    `app/tools/google_tasks_tools.py` boundary - see that module and
    `app/ai/chat_engine.py`'s propose-then-confirm chat flow.

Operates on the user's default Google Tasks list ("@default") - Mochi
doesn't yet support choosing between multiple task lists.

Setup: same steps as Calendar (README's Calendar section), plus setting
`MOCHI_GOOGLE_TASKS_ENABLED=true` in `.env`. No separate credentials
file and no separate "connect" step - reconnecting once (or for the
first time) after turning this on is enough to also grant Tasks access.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.core.exceptions import (
    GoogleTasksNotConfigured,
    GoogleTasksNotConnected,
    TaskSyncError,
)
from app.core.logger import get_logger

logger = get_logger("mochi.tasks.google")

# Read-only by default - widened to full read/write once
# `settings.google_tasks_write_enabled` is turned on. See
# app/calendar/google_calendar.py's SCOPE_READONLY/SCOPE_EVENTS for the
# same pattern applied to Calendar.
SCOPE_READONLY = "https://www.googleapis.com/auth/tasks.readonly"
SCOPE_TASKS = "https://www.googleapis.com/auth/tasks"

_API_SERVICE_NAME = "tasks"
_API_VERSION = "v1"
_DEFAULT_TASKLIST = "@default"

# Cached, built `Resource` object - same reasoning as
# google_calendar._service_cache. Cleared on disconnect() or any auth
# failure so the next call rebuilds from scratch.
_service_cache = None


def required_scopes() -> list[str]:
    """The Tasks scope(s) that should be included in Calendar's combined
    connect() flow when Tasks is enabled - see
    app/calendar/google_calendar.py's _required_scopes()."""
    return [SCOPE_TASKS] if settings.google_tasks_write_enabled else [SCOPE_READONLY]


def _capability_level(scopes) -> int:
    """0 = none, 1 = read-only, 2 = read+write. Based on the scopes
    actually granted in the saved (shared) token, not the current
    setting - so a token connected before write access was turned on is
    correctly recognized as read-only until reconnected."""
    scopes = set(scopes or [])
    if SCOPE_TASKS in scopes:
        return 2
    if SCOPE_READONLY in scopes:
        return 1
    return 0


def _required_level() -> int:
    return 2 if settings.google_tasks_write_enabled else 1


def _import_google_libraries():
    """Same optional-import pattern as google_calendar.py's - kept as a
    function so the rest of the app never pays the import cost unless
    Google Tasks is actually used."""
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise GoogleTasksNotConfigured(
            "Google Tasks support isn't installed. Run: "
            "pip install -r requirements-calendar.txt"
        ) from exc
    return Credentials, Request, RefreshError, build, HttpError


def _require_enabled() -> None:
    if not settings.google_tasks_enabled:
        raise GoogleTasksNotConfigured(
            "Google Tasks isn't turned on. Set MOCHI_GOOGLE_TASKS_ENABLED=true "
            "in .env to enable it."
        )


def is_configured() -> bool:
    """True if the feature is enabled and the (shared) client secret
    file is present - does NOT mean the OAuth flow has actually been
    completed with Tasks permission yet; see is_connected()."""
    if not settings.google_tasks_enabled:
        return False
    try:
        _import_google_libraries()
    except TaskSyncError:
        return False
    return settings.google_client_secret_path.exists()


def is_connected() -> bool:
    """True if the shared token exists and currently covers the Tasks
    scope this needs."""
    if not is_configured():
        return False
    try:
        _load_credentials()
    except TaskSyncError:
        return False
    return True


def _load_credentials():
    """Load the SAME cached credentials Calendar uses (one shared
    token.json), refreshing an expired access token if possible. Raises
    GoogleTasksNotConnected if there's no usable token yet, or if the
    granted scope doesn't cover Tasks (e.g. only Calendar was ever
    connected, or Tasks write access is now on but the saved token only
    has read-only Tasks scope)."""
    Credentials, Request, RefreshError, _build, _HttpError = _import_google_libraries()

    token_path = settings.google_token_path
    if not token_path.exists():
        raise GoogleTasksNotConnected(
            "Google Tasks isn't connected yet. Say \"connect my calendar\" "
            "to set it up - the same sign-in covers Tasks."
        )

    try:
        creds = Credentials.from_authorized_user_file(str(token_path))
    except (ValueError, OSError) as exc:
        raise GoogleTasksNotConnected(
            "Google's saved sign-in looks corrupted. Say \"disconnect my "
            "calendar\" then \"connect my calendar\" to redo it."
        ) from exc

    if _capability_level(getattr(creds, "scopes", None)) < _required_level():
        if _required_level() == 2:
            raise GoogleTasksNotConnected(
                "Google Tasks is connected for read-only access, but this "
                "needs edit permission. Say \"connect my calendar\" to "
                "reconnect with edit access."
            )
        raise GoogleTasksNotConnected(
            "Google Tasks permission hasn't been granted yet. Say "
            "\"connect my calendar\" to reconnect - it now also asks for "
            "Tasks access."
        )

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise GoogleTasksNotConnected(
                "Google's saved sign-in expired and couldn't be refreshed. "
                "Say \"connect my calendar\" to reconnect."
            ) from exc
        from app.calendar.google_calendar import _write_token

        _write_token(token_path, creds.to_json())
        return creds

    raise GoogleTasksNotConnected(
        "Google's saved sign-in is no longer valid. Say \"connect my "
        "calendar\" to reconnect."
    )


def _get_service():
    global _service_cache
    if _service_cache is not None:
        return _service_cache

    _require_enabled()
    _Credentials, _Request, _RefreshError, build, _HttpError = _import_google_libraries()
    creds = _load_credentials()
    _service_cache = build(_API_SERVICE_NAME, _API_VERSION, credentials=creds)
    return _service_cache


def connect() -> None:
    """Delegates to Calendar's connect() - same OAuth flow, same shared
    token. Requires `settings.google_calendar_enabled` too, since
    "connect my calendar" is the one trigger phrase Tasks piggybacks
    on rather than having a separate sign-in of its own."""
    from app.calendar import google_calendar

    google_calendar.connect()


def disconnect() -> bool:
    """Delegates to Calendar's disconnect() - deletes the one shared
    token, so disconnecting also disconnects Tasks."""
    global _service_cache
    _service_cache = None
    from app.calendar import google_calendar

    return google_calendar.disconnect()


def _serialize_task(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("title") or "(untitled)",
        "notes": item.get("notes"),
        "due": item.get("due"),
        "status": item.get("status", "needsAction"),
        "completed": item.get("status") == "completed",
    }


def list_tasks(show_completed: bool = False, max_results: int = 50) -> list[dict]:
    _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    try:
        response = (
            service.tasks()
            .list(
                tasklist=_DEFAULT_TASKLIST,
                showCompleted=show_completed,
                showHidden=show_completed,
                maxResults=max_results,
            )
            .execute()
        )
    except HttpError as exc:
        raise TaskSyncError(f"Google Tasks couldn't be reached right now ({exc}).") from exc

    return [_serialize_task(t) for t in response.get("items", [])]


def find_task(query: str) -> list[dict]:
    """Best-effort local title search - the Tasks API has no full-text
    search parameter like Calendar's `q`, so this fetches the (small,
    per-list) task set and filters client-side. Used by the
    confirm-before-delete/complete chat flow."""
    if not query or not query.strip():
        raise TaskSyncError("Search text cannot be empty.")
    needle = query.strip().lower()
    tasks = list_tasks(show_completed=True)
    return [t for t in tasks if needle in t["title"].lower()]


# ---------------------------------------------------------------------------
# Write operations (opt-in via settings.google_tasks_write_enabled).
#
# Every function below hits _get_service() -> _load_credentials(), which
# enforces the capability check before any request is made. The
# *explicit user confirmation* requirement is enforced one layer up, in
# app/tools/google_tasks_tools.py's `confirmed` parameter - nothing in
# this module itself asks for confirmation, since it has no concept of a
# chat session to ask within.
# ---------------------------------------------------------------------------


def create_task(title: str, notes: Optional[str] = None, due_iso_date: Optional[str] = None) -> dict:
    if not title or not title.strip():
        raise TaskSyncError("Task title cannot be empty.")

    _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    body: dict = {"title": title.strip()}
    if notes:
        body["notes"] = notes
    if due_iso_date:
        # Tasks API only stores a date for `due`, not a time of day -
        # RFC3339 with a T00:00:00Z time component is what it expects.
        body["due"] = f"{due_iso_date}T00:00:00.000Z"

    try:
        created = service.tasks().insert(tasklist=_DEFAULT_TASKLIST, body=body).execute()
    except HttpError as exc:
        raise TaskSyncError(f"Google Tasks couldn't create that task ({exc}).") from exc

    logger.info("Created Google Task %r", title)
    return _serialize_task(created)


def complete_task(task_id: str) -> dict:
    _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    try:
        updated = (
            service.tasks()
            .patch(tasklist=_DEFAULT_TASKLIST, task=task_id, body={"status": "completed"})
            .execute()
        )
    except HttpError as exc:
        raise TaskSyncError(f"Google Tasks couldn't complete that task ({exc}).") from exc

    logger.info("Completed Google Task %s", task_id)
    return _serialize_task(updated)


def delete_task(task_id: str) -> None:
    _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    try:
        service.tasks().delete(tasklist=_DEFAULT_TASKLIST, task=task_id).execute()
    except HttpError as exc:
        raise TaskSyncError(f"Google Tasks couldn't delete that task ({exc}).") from exc

    logger.info("Deleted Google Task %s", task_id)
