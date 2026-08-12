"""
Desktop movement helpers for Mochi.

Keeps positioning math separate from the Qt widget code in app/ui, so it's
easy to unit test and reason about independently of PySide6.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScreenBounds:
    left: int
    top: int
    right: int
    bottom: int

    def clamp(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        clamped_x = max(self.left, min(x, self.right - width))
        clamped_y = max(self.top, min(y, self.bottom - height))
        return clamped_x, clamped_y


@dataclass
class Mover:
    """Simple horizontal walker: moves Mochi left/right along the taskbar
    line at a configurable speed, bouncing off screen edges."""

    x: int
    y: int
    width: int
    height: int
    speed_px_per_tick: int = 4
    direction: int = 1  # 1 = right, -1 = left

    def step(self, bounds: ScreenBounds) -> tuple[int, int]:
        new_x = self.x + self.speed_px_per_tick * self.direction
        if new_x <= bounds.left or new_x + self.width >= bounds.right:
            self.direction *= -1
            new_x = self.x + self.speed_px_per_tick * self.direction
        self.x, self.y = bounds.clamp(new_x, self.y, self.width, self.height)
        return self.x, self.y

    def teleport(self, x: int, y: int, bounds: ScreenBounds) -> tuple[int, int]:
        self.x, self.y = bounds.clamp(x, y, self.width, self.height)
        return self.x, self.y
