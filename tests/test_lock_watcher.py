"""
Tests for app/character/lock_watcher.py - uses an injected fake probe so
this exercises the transition logic without needing an actual Windows
session (the real ctypes probe is exercised only implicitly: it must
return False on this Linux test runner, which test_probe_is_false_off_windows
below confirms).
"""

from __future__ import annotations

from app.character import lock_watcher as lock_watcher_module
from app.character.lock_watcher import LockWatcher, is_session_locked


def test_probe_is_false_off_windows():
    # This test suite always runs on a non-Windows CI/sandbox platform.
    assert is_session_locked() is False


def test_emits_locked_once_on_transition(qapp):
    state = {"locked": False}
    watcher = LockWatcher(probe=lambda: state["locked"])

    seen = []
    watcher.locked.connect(lambda: seen.append("locked"))

    watcher.check_now()  # unlocked -> unlocked, no signal
    assert seen == []

    state["locked"] = True
    watcher.check_now()  # unlocked -> locked
    watcher.check_now()  # still locked - must not fire again
    assert seen == ["locked"]


def test_emits_unlocked_once_on_transition(qapp):
    state = {"locked": True}
    watcher = LockWatcher(probe=lambda: state["locked"])
    watcher.check_now()  # starts locked

    seen = []
    watcher.unlocked.connect(lambda: seen.append("unlocked"))

    state["locked"] = False
    watcher.check_now()
    watcher.check_now()
    assert seen == ["unlocked"]


def test_peek_timer_only_runs_while_locked(qapp):
    state = {"locked": False}
    watcher = LockWatcher(probe=lambda: state["locked"], peek_ms=50)

    assert watcher._peek_timer.isActive() is False

    state["locked"] = True
    watcher.check_now()
    assert watcher._peek_timer.isActive() is True

    state["locked"] = False
    watcher.check_now()
    assert watcher._peek_timer.isActive() is False


def test_start_is_a_safe_noop_off_windows(qapp, monkeypatch):
    """Root-cause fix: this used to call watcher.start() and assert the
    timer stayed inactive with no control over which platform it's
    actually running on - which made the test's own name a lie whenever
    it ran on real Windows (where start() is *supposed* to actually
    poll, and correctly did), rather than testing the off-Windows
    no-op path it claimed to. Force the condition it's meant to cover by
    monkeypatching the module's platform flag directly, so the result is
    the same on every OS the suite happens to run on."""
    monkeypatch.setattr(lock_watcher_module, "_IS_WINDOWS", False)
    watcher = LockWatcher()
    watcher.start()  # must not raise, must not actually poll when off Windows
    assert watcher._poll_timer.isActive() is False


def test_start_actually_polls_on_windows(qapp, monkeypatch):
    """The other half of the platform branch in start() - previously
    untested in either direction on a single given machine. Also
    monkeypatched rather than relying on the real OS, so both branches
    are always exercised regardless of what platform the suite runs on."""
    monkeypatch.setattr(lock_watcher_module, "_IS_WINDOWS", True)
    watcher = LockWatcher()
    try:
        watcher.start()
        assert watcher._poll_timer.isActive() is True
    finally:
        watcher.stop()
