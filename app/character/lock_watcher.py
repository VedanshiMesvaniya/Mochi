"""
Lock-screen easter egg (just-for-fun, spec: "when entering password it
close eyes and playfully open one [eye]").

Windows-only, via ctypes - no admin rights, no extra dependency, no
registering for WTS session-change notifications (which needs a native
message-loop window). Instead this uses a well-known trick:
`user32!OpenInputDesktop` fails (returns NULL) whenever the interactive
desktop is inaccessible, which is exactly the case while the workstation
is locked. Polled on a plain QTimer, so it costs nothing but a cheap
syscall a couple of times a second.

On every other platform (and if anything about the probe goes wrong)
`is_session_locked()` just returns False and `LockWatcher` never starts
its timer - the rest of the app must never depend on this working.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.logger import get_logger

logger = get_logger("mochi.lock_watcher")

_IS_WINDOWS = sys.platform == "win32"

DEFAULT_POLL_MS = 1000
DEFAULT_PEEK_MS = 2200


def is_session_locked() -> bool:
    """Best-effort probe. Always returns False on non-Windows platforms or
    if the Windows API call itself fails for any reason - this must never
    raise, since it's polled continuously."""
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        # DESKTOP_SWITCHDESKTOP (0x0100) is enough access to test with;
        # OpenInputDesktop returns NULL when the current desktop can't be
        # opened, which happens while the workstation is locked.
        desktop = user32.OpenInputDesktop(0, False, 0)
        if desktop:
            user32.CloseDesktop(desktop)
            return False
        return True
    except Exception:  # noqa: BLE001 - never let this crash the poll timer
        logger.debug("Lock-state probe failed; assuming unlocked", exc_info=True)
        return False


class LockWatcher(QObject):
    """Polls `is_session_locked()` and emits signals on state *changes*
    only (not every poll), plus a periodic `peek` signal while locked for
    the "peek one eye open" behavior.

    `probe` is injectable so this is testable without an actual Windows
    session (see tests/test_lock_watcher.py).
    """

    locked = Signal()
    unlocked = Signal()
    peek = Signal()

    def __init__(
        self,
        probe=is_session_locked,
        poll_ms: int = DEFAULT_POLL_MS,
        peek_ms: int = DEFAULT_PEEK_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._probe = probe
        self._is_locked = False

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._check)
        self._poll_ms = poll_ms

        self._peek_timer = QTimer(self)
        self._peek_timer.timeout.connect(self.peek.emit)
        self._peek_ms = peek_ms

    def start(self) -> None:
        """No-op (and cheap) on platforms where locking can't be
        detected - see is_session_locked()."""
        if not _IS_WINDOWS:
            logger.debug("Lock-screen easter egg is Windows-only; not starting watcher.")
            return
        self._poll_timer.start(self._poll_ms)

    def stop(self) -> None:
        self._poll_timer.stop()
        self._peek_timer.stop()

    def check_now(self) -> None:
        """Exposed for tests - runs one poll cycle synchronously."""
        self._check()

    def _check(self) -> None:
        locked_now = self._probe()
        if locked_now and not self._is_locked:
            self._is_locked = True
            self._peek_timer.start(self._peek_ms)
            self.locked.emit()
        elif not locked_now and self._is_locked:
            self._is_locked = False
            self._peek_timer.stop()
            self.unlocked.emit()
