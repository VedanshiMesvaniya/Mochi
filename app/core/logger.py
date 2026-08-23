"""
Application-wide logging setup.

Per project rules (see README / spec section 35), Mochi's logs must never
contain secrets: no passwords, OAuth tokens, raw microphone audio, or
unnecessary sensitive memory/calendar content. Keep log messages structured
and free of raw user secrets.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings

LOG_DIR = settings.data_dir / "logs"
LOG_FILE = LOG_DIR / "mochi.log"

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def get_logger(name: str = "mochi") -> logging.Logger:
    """Return a configured logger. Safe to call repeatedly (idempotent)."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # If we can't write logs to disk, still allow console logging.
        logger.warning("Could not open log file at %s; file logging disabled.", LOG_FILE)

    logger.propagate = False
    return logger
