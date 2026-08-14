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


@dataclass
class Settings:
    """Typed, validated view over environment configuration."""

    # AI / LLM
    llm_model: str = "qwen3:0.6b"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 300

    # Voice
    tts_enabled: bool = True
    stt_enabled: bool = True

    # Memory
    memory_enabled: bool = True
    conversation_history_length: int = 20

    # Calendar
    google_calendar_enabled: bool = False

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

    @classmethod
    def load(cls) -> "Settings":
        env_path = PROJECT_ROOT / ".env"
        if load_dotenv is not None and env_path.exists():
            load_dotenv(env_path)

        return cls(
            llm_model=os.getenv("MOCHI_LLM_MODEL", "qwen3:0.6b"),
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


# Singleton settings instance used throughout the app.
settings = Settings.load()
