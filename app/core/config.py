"""
Central configuration for Project Mochi.

All configuration is loaded from environment variables (via a `.env` file at
the project root). Nothing in this file should be hard-coded that a user
might reasonably want to change - see `.env.example` for the full list of
supported keys.

Usage:
    from app.core.config import settings
    settings.llm_model
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a lightweight required dep
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
MODELS_DIR = PROJECT_ROOT / "models"
CONFIG_DIR = PROJECT_ROOT / "config"


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _safe_filename(value: str, default: str) -> str:
    """Reject anything that isn't a bare filename.

    `google_client_secret_filename`/`google_token_filename` are always
    joined onto `config_dir` with `Path.__truediv__` (see
    `google_client_secret_path`/`google_token_path` below). Pathlib's `/`
    operator does NOT sandbox that join - `Path("/a/b") / "../../x"`
    walks outside `config_dir`, and `Path("/a/b") / "/etc/passwd"`
    (an absolute right-hand side) discards the left side entirely and
    resolves to `/etc/passwd`. These two values come from `.env`, which
    is locally-trusted config rather than remote input, but they're still
    an env var away from pointing Mochi's OAuth client secret/token I/O
    at an arbitrary path on disk - a config value should not be able to
    do that just because it wasn't validated. Reject anything containing
    a path separator, `..`, or an absolute-path form, and fall back to
    the documented default rather than silently using a mangled value.
    """
    if (
        not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or Path(value).is_absolute()
    ):
        return default
    return value


@dataclass
class Settings:
    """Typed, validated view over environment configuration."""

    # AI / LLM
    # qwen3:0.6b was chosen purely for footprint, but it's weak enough at
    # following the structured-JSON-reply instruction (see app/ai/llm.py)
    # that chat often fell back to the canned reply instead of a real
    # answer - reading as "Mochi won't react". qwen2.5:1.5b is a
    # meaningfully smarter step up (better instruction-following/JSON
    # reliability) while still staying under a 1GB footprint. Still fully
    # swappable via MOCHI_LLM_MODEL - see .env.example.
    llm_model: str = "qwen2.5:1.5b"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 300

    # Voice
    tts_enabled: bool = True
    stt_enabled: bool = True

    # Memory
    memory_enabled: bool = True
    conversation_history_length: int = 20

    # Calendar (spec section 22/23 - Mode B, optional Google Calendar
    # integration; Mode A, the fully-local calendar, doesn't need any of
    # this). Off by default - Mochi never talks to Google unless this is
    # explicitly turned on, per spec section 27 (Privacy).
    #
    # V3 scope is read-only (spec: "Google Calendar read access"): Mochi
    # can answer "what's on my calendar" but cannot create/modify/delete
    # events yet - that's V4, and per spec section 23 will require
    # explicit per-action confirmation even once it lands.
    google_calendar_enabled: bool = False
    # V4 (spec section 23, "Level 3 - Calendar control"): widens the
    # requested OAuth scope from calendar.readonly to calendar.events so
    # Mochi can create/update/delete events - but ONLY after the user
    # explicitly confirms each individual action in chat (see
    # app/ai/chat_engine.py's pending_action confirmation flow). This
    # flag does not itself grant write access - it only changes what
    # scope the *next* "connect my calendar" flow requests; an
    # already-connected read-only token still can't write until the user
    # reconnects (see app/calendar/google_calendar.py's capability check).
    google_calendar_write_enabled: bool = False
    # OAuth 2.0 "installed app" client secret, downloaded from Google
    # Cloud Console (APIs & Services -> Credentials -> OAuth client ID ->
    # Desktop app) - see README's Calendar section for the one-time setup
    # steps. Never committed (see .gitignore).
    google_client_secret_filename: str = "google_credentials.json"
    # Where the OAuth token (access + refresh token) is cached locally
    # after the one-time browser consent flow, so Mochi doesn't need to
    # re-prompt every run. Also never committed. Deleting this file (or
    # saying "disconnect my calendar") revokes local access without
    # touching the Google account's actual grant.
    google_token_filename: str = "token.json"

    # Humor (spec: "once in a while it should crawl internet and fetch...
    # so it be more of sense of humor") - the one optional feature that
    # reaches the open internet for something other than an explicit
    # integration; see app/ai/humor.py. On by default since it's exactly
    # what was asked for, but always degrades to a small offline joke
    # list on any network failure, and is one env var away from fully off.
    humor_enabled: bool = True

    # Trend-awareness (opt-in, off by default): lets Mochi's LLM chat
    # replies occasionally reference a real current general-interest topic
    # for extra flavor, on top of the joke-list humor above - see
    # app/humor/trend_fetcher.py. Off by default since, unlike the joke
    # API, this fetches real headlines and paraphrases them, which is a
    # bigger internet-dependency ask than a canned joke lookup.
    trend_awareness_enabled: bool = False
    trend_fetch_interval_hours: int = 6

    # General behavior
    start_with_windows: bool = False
    always_on_top: bool = True
    autonomous_behavior: bool = True

    # Window
    window_width: int = 180
    window_height: int = 180

    # Animation - frames per second for sprite playback. Kept low on
    # purpose (spec section 10/11: cute, readable 2D animation, not a fast
    # flicker) - 8fps on short 6-8 frame loops looked like frames were
    # being scrubbed through rather than a character moving.
    animation_fps: int = 4

    # Logging
    log_level: str = "INFO"

    # Paths (not overridden by env, derived from project layout)
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    assets_dir: Path = field(default_factory=lambda: ASSETS_DIR)
    models_dir: Path = field(default_factory=lambda: MODELS_DIR)
    config_dir: Path = field(default_factory=lambda: CONFIG_DIR)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mochi.db"

    @property
    def google_client_secret_path(self) -> Path:
        return self.config_dir / self.google_client_secret_filename

    @property
    def google_token_path(self) -> Path:
        return self.config_dir / self.google_token_filename

    @classmethod
    def load(cls) -> "Settings":
        env_path = PROJECT_ROOT / ".env"
        if load_dotenv is not None and env_path.exists():
            load_dotenv(env_path)

        return cls(
            llm_model=os.getenv("MOCHI_LLM_MODEL", "qwen2.5:1.5b"),
            llm_temperature=_float(os.getenv("MOCHI_LLM_TEMPERATURE"), 0.7),
            llm_max_tokens=_int(os.getenv("MOCHI_LLM_MAX_TOKENS"), 300),
            tts_enabled=_bool(os.getenv("MOCHI_TTS_ENABLED"), True),
            stt_enabled=_bool(os.getenv("MOCHI_STT_ENABLED"), True),
            memory_enabled=_bool(os.getenv("MOCHI_MEMORY_ENABLED"), True),
            conversation_history_length=_int(
                os.getenv("MOCHI_CONVERSATION_HISTORY_LENGTH"), 20
            ),
            google_calendar_enabled=_bool(
                os.getenv("MOCHI_GOOGLE_CALENDAR_ENABLED"), False
            ),
            google_calendar_write_enabled=_bool(
                os.getenv("MOCHI_GOOGLE_CALENDAR_WRITE_ENABLED"), False
            ),
            google_client_secret_filename=_safe_filename(
                os.getenv("MOCHI_GOOGLE_CLIENT_SECRET_FILENAME", "google_credentials.json"),
                "google_credentials.json",
            ),
            google_token_filename=_safe_filename(
                os.getenv("MOCHI_GOOGLE_TOKEN_FILENAME", "token.json"), "token.json"
            ),
            humor_enabled=_bool(os.getenv("MOCHI_HUMOR_ENABLED"), True),
            trend_awareness_enabled=_bool(
                os.getenv("MOCHI_TREND_AWARENESS_ENABLED"), False
            ),
            trend_fetch_interval_hours=_int(
                os.getenv("MOCHI_TREND_FETCH_INTERVAL_HOURS"), 6
            ),
            start_with_windows=_bool(os.getenv("MOCHI_START_WITH_WINDOWS"), False),
            always_on_top=_bool(os.getenv("MOCHI_ALWAYS_ON_TOP"), True),
            autonomous_behavior=_bool(os.getenv("MOCHI_AUTONOMOUS_BEHAVIOR"), True),
            window_width=_int(os.getenv("MOCHI_WINDOW_WIDTH"), 180),
            window_height=_int(os.getenv("MOCHI_WINDOW_HEIGHT"), 180),
            animation_fps=_int(os.getenv("MOCHI_ANIMATION_FPS"), 4),
            log_level=os.getenv("MOCHI_LOG_LEVEL", "INFO"),
        )

    def ensure_directories(self) -> None:
        """Create local data directories if they don't exist yet."""
        for path in (self.data_dir, self.models_dir, self.config_dir):
            path.mkdir(parents=True, exist_ok=True)
        # config_dir holds the Google OAuth client secret and cached
        # token (see google_client_secret_path/google_token_path) - both
        # are sensitive local credentials. Restrict the directory itself
        # to the current user, best-effort, on top of the per-file chmod
        # google_calendar.py already applies to the token - defense in
        # depth against a permissive umask on a shared machine. Never
        # allowed to break startup if the platform doesn't support it.
        try:
            import os
            import stat

            os.chmod(self.config_dir, stat.S_IRWXU)
        except OSError:  # pragma: no cover - best-effort, platform-dependent
            pass


# Singleton settings instance used throughout the app.
settings = Settings.load()
