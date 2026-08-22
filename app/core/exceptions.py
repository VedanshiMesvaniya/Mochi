"""
Custom exception hierarchy for Project Mochi.

Using specific exception types lets the UI/orchestration layer degrade
gracefully per-subsystem (spec section 36 - Error Handling) instead of
letting one failure crash the whole application.
"""


class MochiError(Exception):
    """Base class for all Mochi application errors."""


class ConfigError(MochiError):
    """Raised when configuration is invalid or missing."""


class LLMUnavailableError(MochiError):
    """Raised when the local LLM (Ollama) cannot be reached."""


class STTUnavailableError(MochiError):
    """Raised when speech-to-text cannot be used (mic/model missing)."""


class TTSUnavailableError(MochiError):
    """Raised when text-to-speech cannot be used."""


class MemoryError_(MochiError):
    """Raised on local memory/database failures."""


class ReminderError(MochiError):
    """Raised on reminder creation/scheduling failures."""


class TaskError(MochiError):
    """Raised on task (simple to-do item) failures."""


class TimerError(MochiError):
    """Raised on quick-timer failures."""


class CalendarError(MochiError):
    """Raised on local or Google calendar failures."""


class GoogleCalendarNotConfigured(CalendarError):
    """Raised when Google Calendar is disabled, or its optional client
    libraries / OAuth client secret aren't set up. Distinct from
    `GoogleCalendarNotConnected` so callers (chat_engine) can point the
    user at the right fix - install/enable vs. connect."""


class GoogleCalendarNotConnected(CalendarError):
    """Raised when Google Calendar is enabled and configured, but the
    user hasn't completed the one-time OAuth consent flow yet (or the
    stored token was revoked/expired and refresh failed)."""


class ToolValidationError(MochiError):
    """
    Raised when an LLM-proposed tool call/action fails schema or permission
    validation. This must NEVER be bypassed - see spec section 41 (Security).
    """


class ConfirmationRequiredError(MochiError):
    """
    Raised when an action (e.g. calendar create/delete) requires explicit
    user confirmation before it may be executed.
    """
