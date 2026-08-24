"""
Mochi's visual palette.

There used to be four selectable "glow themes" here (Purple/Blue/Mint/
Rose) that tinted the *whole* face one uniform hue regardless of
expression. That's gone: Mochi now has exactly one casing/shell look
(the CASING theme below - screen, ears, edge, whiskers), and instead the
face's glow color changes *per expression*, the way the EMO-style
reference sheet specifies (angry glows red, happy glows green, sad
glows blue, etc). Expression geometry (eye shapes, mouth curves, brow
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
# theme but also change color on expression"; palette re-tuned per the
# arousal/valence color-psychology table - e.g. Happy=green/joy,
# Sad=blue/melancholy, Angry=red/danger, Alert=amber/warning-light,
# Confused=teal/mental-fog, Thinking=deep-blue/logic, Talking=cyan/flow).
EXPRESSION_COLORS: dict[CharacterState, QColor] = {
    # High-arousal & positive
    CharacterState.HAPPY: _hex("#22C55E"),      # green - joy, warmth, optimism
    CharacterState.EXCITED: _hex("#F97316"),    # orange - energy, enthusiasm
    CharacterState.WINK: _hex("#FFEA00"),       # bright yellow - playful, cheerful mischief
    # Low-arousal & passive
    CharacterState.IDLE: _hex("#C8C8D2"),       # off-white/grey - neutral, resting
    CharacterState.SAD: _hex("#3B82F6"),        # blue - melancholy, tears
    CharacterState.SLEEPY: _hex("#C4B5FD"),     # light lavender - twilight, drowsiness
    CharacterState.SLEEP: _hex("#312E81"),      # dark indigo - deep night, stillness
    # Intense & reactive
    CharacterState.ANGRY: _hex("#E53935"),      # red - fury, danger
    CharacterState.ALERT: _hex("#FFC107"),      # bright amber - warning light
    CharacterState.HEART: _hex("#EC4899"),      # pink - affection, love
    # Social & shy
    CharacterState.BLUSH: _hex("#F8A6C0"),      # soft pink - blood rushing to cheeks
    CharacterState.SHY: _hex("#FFAB91"),        # peachy pink - modest, warm, self-conscious
    # Cognitive & complex
    CharacterState.CONFUSED: _hex("#14B8A6"),   # teal - disorientation, mystery
    CharacterState.THINKING: _hex("#1E3A8A"),   # deep blue - logic, structured analysis
    CharacterState.SURPRISED: _hex("#C6FF00"),  # bright yellow-green - sudden, unpredictable
    CharacterState.TALKING: _hex("#22D3EE"),    # cyan - steady communication, flow
    # Fallbacks for states outside the 16-expression reference sheet -
    # reuse the nearest sheet color rather than inventing a new hue.
    CharacterState.LOCKED: _hex("#C4B5FD"),
    CharacterState.DIZZY: _hex("#1E3A8A"),
    CharacterState.WAKE: _hex("#C8C8D2"),
    CharacterState.DRAGGED: _hex("#C6FF00"),
    CharacterState.WALK_LEFT: _hex("#C8C8D2"),
    CharacterState.WALK_RIGHT: _hex("#C8C8D2"),
    CharacterState.LOOK_LEFT: _hex("#C8C8D2"),
    CharacterState.LOOK_RIGHT: _hex("#C8C8D2"),
    CharacterState.LOOK_UP: _hex("#C8C8D2"),
    CharacterState.LOOK_DOWN: _hex("#C8C8D2"),
    CharacterState.PLAY: _hex("#F97316"),
    CharacterState.JUMP: _hex("#C6FF00"),
    CharacterState.STRETCH: _hex("#C4B5FD"),
    CharacterState.YAWN: _hex("#C4B5FD"),
}

DEFAULT_EXPRESSION_COLOR = EXPRESSION_COLORS[CharacterState.IDLE]


def get_expression_color(state: CharacterState | None) -> QColor:
    """Always returns a usable QColor - an unmapped state falls back to
    idle's violet rather than raising, same fail-safe policy the old
    get_theme() had for a bad persisted value."""
    if state is None:
        return DEFAULT_EXPRESSION_COLOR
    return EXPRESSION_COLORS.get(state, DEFAULT_EXPRESSION_COLOR)
