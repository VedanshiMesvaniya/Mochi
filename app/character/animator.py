"""
Dynamic sprite animation loader (spec section 11).

Animations live in assets/animations/<name>/ as sequential PNG frames
(frame_000.png, frame_001.png, ...) with transparent backgrounds. This module
knows nothing about *which* animations exist - it just discovers whatever is
on disk, so artists/animators can add or replace folders without touching
application code.

If a requested animation has no frames yet (e.g. during early development
before artwork exists), `get_frames` returns an empty list and the caller
should fall back to a placeholder/static frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger("mochi.animator")

FRAME_EXTENSIONS = (".png", ".webp")


@dataclass
class AnimationSet:
    """Cached mapping of animation name -> sorted list of frame paths."""

    animations_dir: Path = field(default_factory=lambda: settings.assets_dir / "animations")
    default_fps: int = 8
    _cache: dict[str, list[Path]] = field(default_factory=dict)

    def available_animations(self) -> list[str]:
        if not self.animations_dir.exists():
            return []
        return sorted(p.name for p in self.animations_dir.iterdir() if p.is_dir())

    def get_frames(self, name: str) -> list[Path]:
        if name in self._cache:
            return self._cache[name]

        folder = self.animations_dir / name
        if not folder.exists():
            logger.debug("Animation '%s' not found at %s", name, folder)
            self._cache[name] = []
            return []

        frames = sorted(
            p for p in folder.iterdir() if p.suffix.lower() in FRAME_EXTENSIONS
        )
        if not frames:
            logger.debug("Animation '%s' exists but has no frame files yet", name)
        self._cache[name] = frames
        return frames

    def reload(self) -> None:
        """Clear the cache, e.g. after hot-swapping assets during development."""
        self._cache.clear()


class Animator:
    """Drives a single animation's playback (frame index + timing)."""

    def __init__(self, animation_set: AnimationSet | None = None) -> None:
        self.animation_set = animation_set or AnimationSet()
        self.current_animation: str = "idle"
        self.frame_index: int = 0

    def play(self, name: str) -> None:
        if name != self.current_animation:
            self.current_animation = name
            self.frame_index = 0

    def current_frame_path(self) -> Path | None:
        frames = self.animation_set.get_frames(self.current_animation)
        if not frames:
            return None
        return frames[self.frame_index % len(frames)]

    def advance(self) -> None:
        frames = self.animation_set.get_frames(self.current_animation)
        if frames:
            self.frame_index = (self.frame_index + 1) % len(frames)
