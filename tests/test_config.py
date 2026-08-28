"""Regression tests for security review finding S2: `data/` (which holds
mochi.db and data/logs/mochi.log - both can contain personal task/
reminder/appointment content) previously wasn't permission-hardened the
same way `config/` (which holds the Google OAuth secret/token) already
was. Both should now be restricted to the current user, best-effort, on
platforms that support POSIX permission bits.
"""

from __future__ import annotations

import stat
import sys

import pytest

from app.core.config import Settings, harden_directory

posix_only = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX permission bits only apply on POSIX platforms"
)


@posix_only
def test_harden_directory_restricts_to_owner_only(tmp_path):
    target = tmp_path / "some_dir"
    target.mkdir()

    harden_directory(target)

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == stat.S_IRWXU


@posix_only
def test_harden_directory_never_raises_on_missing_path(tmp_path):
    # Best-effort by design (see app/core/config.py) - a bad path must
    # never take the app down.
    harden_directory(tmp_path / "does_not_exist")


@posix_only
def test_ensure_directories_hardens_both_data_dir_and_config_dir(tmp_path):
    settings = Settings.load()
    settings.data_dir = tmp_path / "data"
    settings.config_dir = tmp_path / "config"
    settings.models_dir = tmp_path / "models"

    settings.ensure_directories()

    for path in (settings.data_dir, settings.config_dir):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == stat.S_IRWXU
