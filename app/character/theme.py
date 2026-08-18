"""
Selectable color themes for Mochi's pixel face.

The user can pick a glow color preference (spec section 29 "Settings ->
Personality/Appearance"): four curated options, all built around the same
"dark screen + glowing face" look as the reference mockup - only the hue
changes. Expression *geometry* (eye shapes, mouth curves, brow angles) never
changes between themes, only these colors, so switching is purely cosmetic
and instant (no re-layout, no asset swap).

The active choice is persisted locally in SQLite (see
app/memory/settings_store.py) so it survives restarts.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    glow: QColor          # eyes, mouth, brows, Zzz
    glow_halo: QColor     # soft radial "breathing" glow behind the face
    screen: QColor        # near-black casing
    ear: QColor           # ears, same material family as the casing
    edge: QColor          # rim/outline so the silhouette reads on any wallpaper
    whisker: QColor       # thin decorative whiskers


def _theme(key: str, label: str, glow, halo, screen, ear, edge, whisker) -> Theme:
    return Theme(
        key=key,
        label=label,
        glow=QColor(*glow),
        glow_halo=QColor(*halo),
        screen=QColor(*screen),
        ear=QColor(*ear),
        edge=QColor(*edge),
        whisker=QColor(*whisker),
    )


# Four curated options (spec: "give cool option only 4, include blue and
# purple, other you decide which will suit"). Purple matches the original
# reference mockup and stays the default.
THEMES: dict[str, Theme] = {
    "purple": _theme(
        "purple", "Purple",
        (196, 165, 255), (151, 111, 220),
        (21, 15, 30, 240), (34, 26, 48, 255),
        (90, 74, 118, 170), (220, 205, 245, 130),
    ),
    "blue": _theme(
        "blue", "Blue",
        (140, 190, 255), (90, 140, 220),
        (12, 18, 32, 240), (20, 30, 48, 255),
        (70, 95, 140, 170), (185, 215, 245, 130),
    ),
    "mint": _theme(
        "mint", "Mint",
        (150, 235, 205), (95, 190, 160),
        (10, 24, 22, 240), (18, 38, 34, 255),
        (70, 120, 105, 170), (185, 240, 220, 130),
    ),
    "rose": _theme(
        "rose", "Rose",
        (255, 170, 190), (220, 120, 145),
        (28, 14, 20, 240), (46, 24, 32, 255),
        (130, 80, 95, 170), (245, 195, 210, 130),
    ),
}

DEFAULT_THEME_KEY = "purple"
THEME_ORDER = ("purple", "blue", "mint", "rose")


def get_theme(key: str | None) -> Theme:
    """Always returns a usable Theme - unknown/None keys fall back to the
    default rather than raising, since a bad persisted value must never
    prevent Mochi from starting."""
    return THEMES.get((key or DEFAULT_THEME_KEY).strip().lower(), THEMES[DEFAULT_THEME_KEY])
