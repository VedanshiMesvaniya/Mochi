"""
Google Calendar integration - read access only (spec sections 22/23, V3).

This is Mode B from the spec: optional, off by default, requires an
internet connection and a one-time OAuth consent flow in the browser.
Mode A (a fully local calendar) doesn't need any of this and isn't
implemented yet.

Scope used: `calendar.readonly` - deliberately the narrowest scope that
can answer "what's on my calendar" (spec section 23: "Reading calendar
events can be allowed after the user grants permission... Never let the
small LLM directly modify the calendar"). Creating/updating/deleting
events is a later phase (V4) and will need the broader `calendar` scope
plus per-action confirmation - nothing in this module can write.

Setup (one-time, per spec's "Choose Google Calendar API scopes" guidance):
  1. In Google Cloud Console, create an OAuth client ID of type
     "Desktop app" and download its client secret JSON.
  2. Save it as `config/google_credentials.json` (path configurable via
     `MOCHI_GOOGLE_CLIENT_SECRET_FILENAME`).
  3. Set `MOCHI_GOOGLE_CALENDAR_ENABLED=true` in `.env`.
  4. `pip install -r requirements-calendar.txt` (kept out of the base
     `requirements.txt` - see that file's own docstring-equivalent header
     - so normal Mochi use never needs Google's client libraries).
  5. Say "connect my calendar" to Mochi in chat; a browser window opens
     for the one-time consent screen. The resulting token is cached
     locally at `config/token.json` (gitignored) so this only needs to
     happen once.

Every public function here either returns plain dicts/bools or raises a
`CalendarError` subclass (see app/core/exceptions.py) - callers
(app/tools/calendar_tools.py, app/ai/chat_engine.py) never need to know
anything about the google-api-python-client types underneath.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.exceptions import (
    CalendarError,
    GoogleCalendarNotConfigured,
    GoogleCalendarNotConnected,
)
from app.core.logger import get_logger

logger = get_logger("mochi.calendar.google")

# Read-only on purpose - see module docstring. Widening this later (for
# V4 write support) is a deliberate, separate change, not a side effect
# of anything in this file.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

_API_SERVICE_NAME = "calendar"
_API_VERSION = "v3"

# Cached, built `Resource` object (spec section 12-style reasoning: don't
# redo an expensive setup step - here, a credential refresh + HTTP client
# build - on every single chat query). Cleared on disconnect() or on any
# auth failure so the next call rebuilds from scratch rather than reusing
# something stale.
_service_cache = None


def _import_google_libraries():
    """Import the optional Google client libraries, raising a clear,
    actionable error if they're not installed rather than a bare
    ImportError. Kept as a function (not a module-level import) so the
    rest of the app - and every other subsystem's tests - never pay the
    cost of these imports existing at all unless calendar features are
    actually used, matching how app/core/config.py treats `dotenv`."""
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise GoogleCalendarNotConfigured(
            "Google Calendar support isn't installed. Run: "
            "pip install -r requirements-calendar.txt"
        ) from exc
    return Credentials, InstalledAppFlow, Request, RefreshError, build, HttpError


def _require_enabled() -> None:
    if not settings.google_calendar_enabled:
        raise GoogleCalendarNotConfigured(
            "Google Calendar isn't turned on. Set "
            "MOCHI_GOOGLE_CALENDAR_ENABLED=true in .env to enable it."
        )


def is_configured() -> bool:
    """True if the feature is enabled and a client secret file is present
    - i.e. connect() has a real chance of working. Does NOT mean the
    OAuth flow has actually been completed yet; see is_connected()."""
    if not settings.google_calendar_enabled:
        return False
    try:
        _import_google_libraries()
    except CalendarError:
        return False
    return settings.google_client_secret_path.exists()


def is_connected() -> bool:
    """True if a usable (valid or refreshable) cached token exists."""
    if not is_configured():
        return False
    try:
        _load_credentials()
    except CalendarError:
        return False
    return True


def _load_credentials():
    """Load cached credentials from disk, refreshing an expired access
    token if a refresh token is available. Raises GoogleCalendarNotConnected
    if there's no usable token yet."""
    Credentials, _InstalledAppFlow, Request, RefreshError, _build, _HttpError = (
        _import_google_libraries()
    )

    token_path: Path = settings.google_token_path
    if not token_path.exists():
        raise GoogleCalendarNotConnected(
            "Google Calendar isn't connected yet. Say \"connect my "
            "calendar\" to set it up."
        )

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except (ValueError, OSError) as exc:
        raise GoogleCalendarNotConnected(
            "Google Calendar's saved sign-in looks corrupted. Say "
            "\"disconnect my calendar\" then \"connect my calendar\" to "
            "redo it."
        ) from exc

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise GoogleCalendarNotConnected(
                "Google Calendar's sign-in expired and couldn't be "
                "refreshed. Say \"connect my calendar\" to reconnect."
            ) from exc
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    raise GoogleCalendarNotConnected(
        "Google Calendar's saved sign-in is no longer valid. Say "
        "\"connect my calendar\" to reconnect."
    )


def _get_service():
    global _service_cache
    if _service_cache is not None:
        return _service_cache

    _require_enabled()
    _Credentials, _InstalledAppFlow, _Request, _RefreshError, build, _HttpError = (
        _import_google_libraries()
    )
    creds = _load_credentials()
    _service_cache = build(_API_SERVICE_NAME, _API_VERSION, credentials=creds)
    return _service_cache


def connect() -> None:
    """Run the one-time (per-machine) OAuth consent flow: opens the
    user's browser to Google's consent screen and blocks until they
    finish (or the flow times out). Only ever called explicitly - e.g.
    from a "connect my calendar" chat command - never automatically,
    since it requires the user's active attention in a browser window.

    Safe to call from a background thread (see app/ui/chat_window.py's
    ChatWorker) - it does not touch Qt.
    """
    _require_enabled()
    Credentials, InstalledAppFlow, _Request, _RefreshError, _build, _HttpError = (
        _import_google_libraries()
    )

    secret_path = settings.google_client_secret_path
    if not secret_path.exists():
        raise GoogleCalendarNotConfigured(
            f"No Google OAuth client secret found at {secret_path}. "
            "Download one from Google Cloud Console (OAuth client ID, "
            "type 'Desktop app') and save it there - see README's "
            "Calendar section."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    try:
        # 5-minute bound (spec section 36: nothing should be able to hang
        # the app forever) - long enough for someone to actually complete
        # the consent screen, short enough that a user who closes the
        # browser tab isn't left with chat silently stuck "thinking".
        creds = flow.run_local_server(port=0, timeout_seconds=300)
    except Exception as exc:  # noqa: BLE001 - the underlying library can
        # raise several different things (socket errors, timeouts, the
        # user denying consent) - all of them mean "connecting failed",
        # never a reason to crash the app.
        raise CalendarError(
            "Connecting to Google Calendar didn't complete. Please try "
            "again."
        ) from exc

    settings.ensure_directories()
    settings.google_token_path.write_text(creds.to_json(), encoding="utf-8")
    global _service_cache
    _service_cache = None  # force a fresh build with the new credentials
    logger.info("Google Calendar connected (token saved to %s)", settings.google_token_path)


def disconnect() -> bool:
    """Delete the locally cached token. Does not revoke the grant on the
    Google account itself (spec doesn't ask for that, and doing it
    silently would be surprising) - just makes Mochi forget it locally,
    same as `data.mochi.db` memory deletion elsewhere."""
    global _service_cache
    _service_cache = None
    token_path = settings.google_token_path
    if token_path.exists():
        token_path.unlink()
        logger.info("Google Calendar disconnected (removed %s)", token_path)
        return True
    return False


def _iso(dt: _dt.datetime) -> str:
    # Google's API wants RFC3339; a timezone-aware ISO string with an
    # explicit offset satisfies it. Naive local datetimes throughout this
    # module are assumed to be the machine's local time (spec section 26:
    # "Display them in the user's local timezone").
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat()


def _serialize_event(event: dict) -> dict:
    start = event.get("start", {})
    end = event.get("end", {})
    all_day = "date" in start  # all-day events use 'date', not 'dateTime'
    return {
        "id": event.get("id"),
        "title": event.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": all_day,
        "location": event.get("location"),
        "html_link": event.get("htmlLink"),
    }


def list_events(
    time_min: _dt.datetime,
    time_max: _dt.datetime,
    query: Optional[str] = None,
    max_results: int = 10,
) -> list[dict]:
    """Low-level listing over a fixed window, optionally full-text
    filtered via Google's own `q` search param. Every higher-level
    function below (today/tomorrow/upcoming/search) is a thin wrapper
    over this one, matching spec section 24's tool list."""
    _, _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    try:
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=_iso(time_min),
                timeMax=_iso(time_max),
                q=query,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except HttpError as exc:
        raise CalendarError(
            f"Google Calendar couldn't be reached right now ({exc})."
        ) from exc

    return [_serialize_event(e) for e in response.get("items", [])]


def get_today_events() -> list[dict]:
    now = _dt.datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1)
    return list_events(start, end)


def get_tomorrow_events() -> list[dict]:
    now = _dt.datetime.now()
    start = (now + _dt.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + _dt.timedelta(days=1)
    return list_events(start, end)


def get_upcoming_events(days: int = 7, max_results: int = 10) -> list[dict]:
    now = _dt.datetime.now()
    return list_events(now, now + _dt.timedelta(days=days), max_results=max_results)


def search_events(query: str, days_ahead: int = 30, max_results: int = 10) -> list[dict]:
    if not query or not query.strip():
        raise CalendarError("Search query cannot be empty.")
    now = _dt.datetime.now()
    return list_events(
        now,
        now + _dt.timedelta(days=days_ahead),
        query=query.strip(),
        max_results=max_results,
    )
