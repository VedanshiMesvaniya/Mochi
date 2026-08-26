"""
Google Calendar integration (spec sections 22/23).

This is Mode B from the spec: optional, off by default, requires an
internet connection and a one-time OAuth consent flow in the browser.
Mode A (a fully local calendar) doesn't need any of this and isn't
implemented yet.

Two capability levels, gated by `settings.google_calendar_write_enabled`:

  * **Read (V3, default)** - scope `calendar.readonly`, the narrowest
    scope that can answer "what's on my calendar" (spec section 23:
    "Reading calendar events can be allowed after the user grants
    permission").
  * **Read + write (V4, opt-in)** - scope `calendar.events`, letting
    Mochi create/update/delete events. Per spec section 23 ("Never let
    the small LLM directly modify the calendar without... user
    confirmation"), every write in this module additionally requires an
    explicit `confirmed=True` at the `app/tools/calendar_tools.py`
    boundary - see that module and `app/ai/chat_engine.py`'s
    propose-then-confirm chat flow. Nothing in `app/ai/intent.py` (the
    only thing driven directly by user text) can reach a write call
    without going through that confirmation step first.

Turning `google_calendar_write_enabled` on does NOT retroactively grant
write access to an already-connected read-only token - Google enforces
the scope actually granted at consent time, and this module mirrors that
check locally (see `_capability_level`/`_required_level` below) so a
stale-scope token fails fast with a clear "reconnect" message instead of
a confusing live API error.

Setup (one-time, per spec's "Choose Google Calendar API scopes" guidance):
  1. In Google Cloud Console, create an OAuth client ID of type
     "Desktop app" and download its client secret JSON.
  2. Save it as `config/google_credentials.json` (path configurable via
     `MOCHI_GOOGLE_CLIENT_SECRET_FILENAME`).
  3. Set `MOCHI_GOOGLE_CALENDAR_ENABLED=true` in `.env` (add
     `MOCHI_GOOGLE_CALENDAR_WRITE_ENABLED=true` too for create/edit/
     delete - read-only otherwise).
  4. `pip install -r requirements-calendar.txt` (kept out of the base
     `requirements.txt` - so normal Mochi use never needs Google's
     client libraries).
  5. Say "connect my calendar" to Mochi in chat; a browser window opens
     for the one-time consent screen. The resulting token is cached
     locally at `config/token.json` (gitignored) so this only needs to
     happen once - or once per capability level, since turning write
     access on later requires reconnecting to upgrade the granted scope.

Every public function here either returns plain dicts/bools or raises a
`CalendarError` subclass (see app/core/exceptions.py) - callers
(app/tools/calendar_tools.py, app/ai/chat_engine.py) never need to know
anything about the google-api-python-client types underneath.
"""

from __future__ import annotations

import datetime as _dt
import os
import stat
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

# Read-only by default (see module docstring) - widened to include write
# access to events (never to full calendar management) once V4's
# `settings.google_calendar_write_enabled` is turned on. `SCOPES` is kept
# as the name existing callers reference; it now resolves dynamically via
# `_required_scopes()` rather than being a fixed constant, since the
# scope Mochi needs depends on whether write access is currently enabled.
SCOPE_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
# calendar.events (not the broader "calendar" scope) - "View and edit
# events on all your calendars" per Google's own scope description.
# Deliberately narrower than full calendar scope, which also allows
# creating/deleting calendars themselves - Mochi only ever touches
# events on the existing primary calendar.
SCOPE_EVENTS = "https://www.googleapis.com/auth/calendar.events"


def _required_scopes() -> list[str]:
    return [SCOPE_EVENTS] if settings.google_calendar_write_enabled else [SCOPE_READONLY]


def _capability_level(scopes) -> int:
    """0 = none, 1 = read-only, 2 = read+write. Based on granted scopes
    actually recorded in the saved token (see _load_credentials), not on
    the current setting - so a token connected before write access was
    turned on is correctly recognized as read-only until reconnected."""
    scopes = set(scopes or [])
    if SCOPE_EVENTS in scopes:
        return 2
    if SCOPE_READONLY in scopes:
        return 1
    return 0


def _required_level() -> int:
    return 2 if settings.google_calendar_write_enabled else 1


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
    if there's no usable token yet, or if the token's granted scope
    doesn't cover what's currently required (e.g. write access is now
    enabled but the saved token is only read-only - see
    _capability_level/_required_level above)."""
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
        # Deliberately NOT passing an explicit `scopes` argument here:
        # doing so would override whatever was actually granted at
        # consent time with our own assumption, defeating the capability
        # check below. Loading with scopes=None reads the real granted
        # scopes back out of the saved token file.
        creds = Credentials.from_authorized_user_file(str(token_path))
    except (ValueError, OSError) as exc:
        raise GoogleCalendarNotConnected(
            "Google Calendar's saved sign-in looks corrupted. Say "
            "\"disconnect my calendar\" then \"connect my calendar\" to "
            "redo it."
        ) from exc

    if _capability_level(getattr(creds, "scopes", None)) < _required_level():
        if _required_level() == 2:
            raise GoogleCalendarNotConnected(
                "Google Calendar is connected for read-only access, but "
                "this needs edit permission. Say \"connect my calendar\" "
                "to reconnect with edit access."
            )
        raise GoogleCalendarNotConnected(
            "Google Calendar's saved sign-in doesn't look valid. Say "
            "\"connect my calendar\" to reconnect."
        )

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
        _write_token(token_path, creds.to_json())
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

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), _required_scopes())
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
    _write_token(settings.google_token_path, creds.to_json())
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


def _write_token(token_path: Path, contents: str) -> None:
    """Save the OAuth token (access + refresh token - full account-level
    calendar access, not just a session cookie) and restrict it to the
    current OS user. `write_text()` alone creates the file with whatever
    permissions the process' default umask gives it - on a shared/
    multi-user machine that can mean group/world-readable, letting any
    other local account read out a live refresh token. chmod 0600
    (owner read/write only) right after writing closes that; wrapped in
    try/except since `os.chmod` support for POSIX mode bits is
    best-effort on Windows (NTFS ACLs, normally already user-scoped via
    the profile folder, aren't controlled by this call) and must never
    turn a successful token save into a crash.
    """
    token_path.write_text(contents, encoding="utf-8")
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - best-effort, platform-dependent
        logger.debug("Could not restrict permissions on %s", token_path, exc_info=True)


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


def find_event(
    query: Optional[str] = None,
    around: Optional[_dt.datetime] = None,
    days_ahead: int = 2,
) -> list[dict]:
    """Best-effort local search over upcoming events, used by the
    confirm-before-delete/update chat flow (spec example: "Cancel my 5 PM
    meeting"). Combines Google's own full-text `q` search (when `query`
    is given - e.g. "meeting with Devika") with a client-side
    time-of-day filter (when `around` is given - matches events starting
    within 30 minutes of that clock time, on any day in the window) -
    Google's API has no "same time of day, any date" filter of its own,
    so that part happens here instead of as a request parameter.

    Deliberately narrow (defaults to the next 2 days): this backs a
    "cancel *my next* 5pm meeting"-style command, not a general search -
    app/tools/calendar_tools.search_calendar_events already covers a
    wider, explicit search.
    """
    now = _dt.datetime.now()
    events = list_events(now, now + _dt.timedelta(days=days_ahead), query=query, max_results=25)
    if around is None:
        return events

    target_minutes = around.hour * 60 + around.minute
    matches = []
    for event in events:
        if event["all_day"] or not event["start"]:
            continue
        try:
            start_dt = _dt.datetime.fromisoformat(event["start"])
        except ValueError:
            continue
        event_minutes = start_dt.hour * 60 + start_dt.minute
        if abs(event_minutes - target_minutes) <= 30:
            matches.append(event)
    return matches


# ---------------------------------------------------------------------------
# Write operations (V4, opt-in via settings.google_calendar_write_enabled).
#
# Every function below hits _get_service() -> _load_credentials(), which
# enforces the capability check (raises GoogleCalendarNotConnected if the
# saved token's scope doesn't cover write access) before any request is
# made - so even a direct/programmatic call here can't silently write
# with an insufficient grant. The *explicit user confirmation*
# requirement (spec section 23) is enforced one layer up, in
# app/tools/calendar_tools.py's `confirmed` parameter - nothing in this
# module itself asks for confirmation, since it has no concept of a chat
# session to ask within.
# ---------------------------------------------------------------------------


def create_event(
    title: str,
    start: _dt.datetime,
    end: Optional[_dt.datetime] = None,
    all_day: bool = False,
    location: Optional[str] = None,
) -> dict:
    _, _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    if end is None:
        end = start + _dt.timedelta(hours=1)

    body: dict = {"summary": title}
    if location:
        body["location"] = location
    if all_day:
        body["start"] = {"date": start.date().isoformat()}
        body["end"] = {"date": end.date().isoformat()}
    else:
        body["start"] = {"dateTime": _iso(start)}
        body["end"] = {"dateTime": _iso(end)}

    try:
        created = service.events().insert(calendarId="primary", body=body).execute()
    except HttpError as exc:
        raise CalendarError(f"Google Calendar couldn't create that event ({exc}).") from exc

    logger.info("Created Google Calendar event %r", title)
    return _serialize_event(created)


def update_event(
    event_id: str,
    title: Optional[str] = None,
    start: Optional[_dt.datetime] = None,
    end: Optional[_dt.datetime] = None,
) -> dict:
    """Partial update (Google's `events.patch`, not `events.update`) -
    only the fields actually passed are changed; everything else on the
    existing event (location, description, attendees, ...) is left
    alone."""
    _, _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    body: dict = {}
    if title is not None:
        body["summary"] = title
    if start is not None:
        body["start"] = {"dateTime": _iso(start)}
    if end is not None:
        body["end"] = {"dateTime": _iso(end)}
    if not body:
        raise CalendarError("Nothing to update - no new title/start/end given.")

    try:
        updated = (
            service.events()
            .patch(calendarId="primary", eventId=event_id, body=body)
            .execute()
        )
    except HttpError as exc:
        raise CalendarError(f"Google Calendar couldn't update that event ({exc}).") from exc

    logger.info("Updated Google Calendar event %s", event_id)
    return _serialize_event(updated)


def delete_event(event_id: str) -> None:
    _, _, _, _, _build, HttpError = _import_google_libraries()
    service = _get_service()

    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
    except HttpError as exc:
        raise CalendarError(f"Google Calendar couldn't delete that event ({exc}).") from exc

    logger.info("Deleted Google Calendar event %s", event_id)
