"""
System tray integration (spec section 4 - "system tray integration").

Gives the user a way to interact with Mochi even while the character window
is hidden/minimized, and a reliable way to quit the app.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.tray")

DEFAULT_ICON_PATH = settings.assets_dir / "icons" / "tray_icon.png"


def build_tray_icon(app: QApplication, pet_window) -> QSystemTrayIcon:
    icon_path: Path = DEFAULT_ICON_PATH
    if icon_path.exists():
        icon = QIcon(str(icon_path))
    else:
        # No artwork yet - fall back to a built-in Qt icon so the tray entry
        # still appears instead of crashing (spec section 36).
        icon = app.style().standardIcon(app.style().StandardPixmap.SP_ComputerIcon)
        logger.debug("Tray icon asset not found at %s; using fallback icon.", icon_path)

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("Mochi")

    menu = QMenu()
    show_action = menu.addAction("Show Mochi")
    hide_action = menu.addAction("Hide Mochi")
    menu.addSeparator()
    settings_action = menu.addAction("Settings")
    menu.addSeparator()
    exit_action = menu.addAction("Exit")

    show_action.triggered.connect(pet_window.show)
    hide_action.triggered.connect(pet_window.hide)
    exit_action.triggered.connect(app.quit)
    # Settings window wiring lands in a later phase; keep a safe no-op for now.
    settings_action.triggered.connect(lambda: logger.info("Settings requested (not implemented yet)"))

    tray.setContextMenu(menu)

    def _on_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if pet_window.isVisible():
                pet_window.hide()
            else:
                pet_window.show()

    tray.activated.connect(_on_activated)
    tray.show()
    return tray
