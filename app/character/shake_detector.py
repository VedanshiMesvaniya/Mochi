"""
Shake-gesture detector.

Feeds a stream of (timestamp_seconds, x) mouse positions while the
character is being dragged, and flags a "shake" once the cursor reverses
horizontal direction enough times in a short enough window (spec: "if user
shake it over screen show the eyes spinning animation... after it get
angry for shaking it").

Deliberately not Qt-aware - it's plain arithmetic on numbers fed to it -
so it's trivially unit-testable without a real QWidget/mouse, and reusable
if drag input ever comes from something other than a QMouseEvent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ShakeDetector:
    reversal_threshold: int = 4   # direction reversals needed...
    window_seconds: float = 1.0   # ...within this many seconds
    min_travel_px: float = 12.0   # ignore tiny jitter as real movement
    cooldown_seconds: float = 4.0  # don't re-fire immediately after a hit

    _last_x: float | None = field(default=None, init=False, repr=False)
    _last_direction: int = field(default=0, init=False, repr=False)
    _reversal_times: list = field(default_factory=list, init=False, repr=False)
    _cooldown_until: float = field(default=0.0, init=False, repr=False)

    def reset(self) -> None:
        """Call whenever a fresh drag starts, so leftover state from a
        previous drag never counts toward a new gesture."""
        self._last_x = None
        self._last_direction = 0
        self._reversal_times = []

    def feed(self, timestamp: float, x: float) -> bool:
        """Feed one (timestamp, x) sample. Returns True exactly once when
        a shake is newly detected; stays False for `cooldown_seconds`
        afterward so one gesture doesn't fire repeatedly."""
        if self._last_x is None:
            self._last_x = x
            return False

        dx = x - self._last_x
        self._last_x = x
        if abs(dx) < self.min_travel_px * 0.3:
            return False  # too small to count as a directional movement

        direction = 1 if dx > 0 else -1
        if self._last_direction != 0 and direction != self._last_direction:
            self._reversal_times.append(timestamp)
        self._last_direction = direction

        cutoff = timestamp - self.window_seconds
        self._reversal_times = [t for t in self._reversal_times if t >= cutoff]

        if timestamp < self._cooldown_until:
            return False

        if len(self._reversal_times) >= self.reversal_threshold:
            self._reversal_times = []
            self._cooldown_until = timestamp + self.cooldown_seconds
            return True
        return False
