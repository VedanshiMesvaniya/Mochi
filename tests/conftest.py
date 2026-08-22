"""
Pytest fixtures shared across the test suite.

Reminder/database tests must never touch the real `data/mochi.db` file -
this fixture points `app.core.config.settings` at a temporary directory for
the duration of each test that requests it.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.config import settings


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication instance shared across any test that needs to
    construct real Qt widgets (e.g. PixelFaceWidget rendering tests).
    Session-scoped since Qt only allows one QApplication per process.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Redirect settings.data_dir to a temp directory so reminder tests get
    a fresh, isolated SQLite file."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield tmp_path


@pytest.fixture()
def temp_config_dir(tmp_path, monkeypatch):
    """Redirect settings.config_dir to a temp directory so calendar tests
    never touch a real (or gitignored-but-present) config/google_credentials.json
    / config/token.json on the developer's machine."""
    monkeypatch.setattr(settings, "config_dir", tmp_path)
    yield tmp_path
