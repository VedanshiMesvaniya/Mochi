"""
Pytest fixtures shared across the test suite.

Reminder/database tests must never touch the real `data/mochi.db` file -
this fixture points `app.core.config.settings` at a temporary directory for
the duration of each test that requests it.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Redirect settings.data_dir to a temp directory so reminder tests get
    a fresh, isolated SQLite file."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield tmp_path
