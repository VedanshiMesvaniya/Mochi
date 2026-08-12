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


class CalendarError(MochiError):
    """Raised on local or Google calendar failures."""


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
