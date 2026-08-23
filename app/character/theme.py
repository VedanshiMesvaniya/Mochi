"""
Mochi's visual palette.

There used to be four selectable "glow themes" here (Purple/Blue/Mint/
Rose) that tinted the *whole* face one uniform hue regardless of
expression. That's gone: Mochi now has exactly one casing/shell look
(the CASING theme below - screen, ears, edge, whiskers), and instead the
face's glow color changes *per expression*, the way the EMO-style
reference sheet specifies (angry glows crimson, happy glows green, sad
glows cyan, etc). Expression geometry (eye shapes, mouth curves, brow
angles) still never changes - only which color a given expression draws
in - so this is still purely a color lookup, no re-layout.

EXPRESSION_COLORS is the single source of truth for that lookup: one hex
per expression, no light/dark variant pairs. It's intentionally a flat
dict keyed by CharacterState so pixel_face.py never has to special-case
"which theme is active" - there's only ever one.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

from app.character.state_machine import CharacterState


@dataclass(frozen=True)
class Theme:
    """The one, unified casing look - not user-selectable. Only the glow
    color drawn *inside* this shell changes, per expression."""

    key: str
    label: str
    screen: QColor    # near-black casing
    ear: QColor       # ears, same material family as the casing
    edge: QColor      # rim/outline so the silhouette reads on any wallpaper
    whisker: QColor   # thin decorative whiskers


CASING = Theme(
    key="mochi",
    label="Mochi",
    screen=QColor(18, 16, 22, 240),
    ear=QColor(30, 27, 36, 255),
    edge=QColor(84, 78, 98, 170),
    whisker=QColor(214, 206, 226, 130),
)


def _hex(value: str) -> QColor:
    return QColor(value)


# One canonical hex per expression (spec: "remove theme, make it one
# theme but also change color on expression"). Alert and Angry use the
# updated warning-red / deep-crimson pair; everything else keeps the
# original EMO Expression Pixel Matrix mapping.
EXPRESSION_COLORS: dict[CharacterState, QColor] = {
    CharacterState.IDLE: _hex("#8A2BE2"),
    CharacterState.HAPPY: _hex("#00FF66"),
    CharacterState.SAD: _hex("#00D2FF"),
    CharacterState.ANGRY: _hex("#D50000"),
    CharacterState.CONFUSED: _hex("#FFB300"),
    CharacterState.SURPRISED: _hex("#00E5FF"),
    CharacterState.THINKING: _hex("#A855F7"),
    CharacterState.SLEEPY: _hex("#6A608A"),
    CharacterState.SLEEP: _hex("#312E81"),
    CharacterState.TALKING: _hex("#34D399"),
    CharacterState.EXCITED: _hex("#FF007F"),
    CharacterState.ALERT: _hex("#FF1744"),
    CharacterState.BLUSH: _hex("#FF6B8B"),
    CharacterState.SHY: _hex("#F472B6"),
    CharacterState.HEART: _hex("#FF1493"),
    CharacterState.WINK: _hex("#00F0FF"),
    # Fallbacks for states outside the 16-expression reference sheet -
    # reuse the nearest sheet color rather than inventing a new hue.
    CharacterState.LOCKED: _hex("#6A608A"),
    CharacterState.DIZZY: _hex("#A855F7"),
    CharacterState.WAKE: _hex("#8A2BE2"),
    CharacterState.DRAGGED: _hex("#00E5FF"),
    CharacterState.WALK_LEFT: _hex("#8A2BE2"),
    CharacterState.WALK_RIGHT: _hex("#8A2BE2"),
    CharacterState.LOOK_LEFT: _hex("#8A2BE2"),
    CharacterState.LOOK_RIGHT: _hex("#8A2BE2"),
    CharacterState.LOOK_UP: _hex("#8A2BE2"),
    CharacterState.LOOK_DOWN: _hex("#8A2BE2"),
    CharacterState.PLAY: _hex("#FF007F"),
    CharacterState.JUMP: _hex("#00E5FF"),
    CharacterState.STRETCH: _hex("#6A608A"),
    CharacterState.YAWN: _hex("#6A608A"),
}

DEFAULT_EXPRESSION_COLOR = EXPRESSION_COLORS[CharacterState.IDLE]


def get_expression_color(state: CharacterState | None) -> QColor:
    """Always returns a usable QColor - an unmapped state falls back to
    idle's violet rather than raising, same fail-safe policy the old
    get_theme() had for a bad persisted value."""
    if state is None:
        return DEFAULT_EXPRESSION_COLOR
    return EXPRESSION_COLORS.get(state, DEFAULT_EXPRESSION_COLOR)
