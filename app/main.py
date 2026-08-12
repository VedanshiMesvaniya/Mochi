"""
Project Mochi - application entry point.

Phase 1 scope (see README "Development Phases"): transparent desktop
character with idle/walk/drag behavior, system tray, and exit menu. No AI
yet - that lands in Phase 2 (app/ai/).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.character.pet import PetWindow
from app.core.config import settings
from app.core.events import Events, event_bus
from app.core.logger import get_logger
from app.ui.tray import build_tray_icon

logger = get_logger("mochi.main")


def main() -> int:
    settings.ensure_directories()
    logger.info("Starting Mochi (LLM model configured: %s)", settings.llm_model)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep running via tray even if window closes

    pet_window = PetWindow()
    pet_window.show()

    tray = build_tray_icon(app, pet_window)  # noqa: F841 - must stay referenced to avoid GC

    event_bus.publish(Events.APP_STARTUP)

    exit_code = app.exec()

    event_bus.publish(Events.APP_SHUTDOWN)
    logger.info("Mochi shut down (exit code %s)", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
