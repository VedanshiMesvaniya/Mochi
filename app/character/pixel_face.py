"""
Mochi's EMO-style pixel face (replaces the earlier sprite/walking-cat
design). The whole visible character is a black rounded "screen" with a
programmatically drawn face - two eyes and a mouth - rather than PNG
sprite frames. This is deliberately vector/parametric: every expression is
just a set of numbers (how open each eye is, where the pupils look, what
mouth shape to draw), so adding a new expression later never requires new
artwork, and CPU cost stays tiny (no image decoding, just simple shapes).

Covers exactly the 12 states requested:
    Idle, Happy, Sad, Angry, Confused, Surprised, Thinking, Sleepy,
    Sleeping, Talking, Excited, Alert

Personality touches baked into the renderer itself (not just state swaps):
  - a slow "breathing" pulse on the idle glow, so Mochi never looks static
  - autonomous blinking, independent of whatever state is active
  - pupils that drift toward the mouse cursor while idle/alert - a small
    but important "I'm paying attention to you" cue for the
    playful-kitten-wants-attention personality (see docs/personality)
  - a 2-3 frame mouth cycle while TALKING
  - a floating "Z" while SLEEPING
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from app.character.state_machine import CharacterState


class MouthType(str, Enum):
    NONE = "none"           # sleeping - no mouth drawn
    FLAT = "flat"            # idle
    SMILE = "smile"          # happy / excited
    BIG_SMILE = "big_smile"  # excited emphasis
    FROWN = "frown"          # sad
    ANGRY_V = "angry_v"      # angry (inverted-V grimace)
    O = "o"                  # surprised / alert
    WAVY = "wavy"            # confused
    TALK_OPEN = "talk_open"  # talking frame A
    TALK_SMALL = "talk_small"  # talking frame B


@dataclass(frozen=True)
class Expression:
    """One static expression - see the state->Expression table below."""

    eye_open: float = 1.0          # 0 (closed) .. 1 (fully open)
    eye_open_right: Optional[float] = None  # override for asymmetric (confused); None = same as eye_open
    eye_offset_y: float = 0.0      # -1 (look up) .. 1 (look down), added to cursor-follow
    brow_angle: float = 0.0        # degrees, positive = angled up-and-out (angry/sad)
    mouth: MouthType = MouthType.FLAT
    glow: float = 0.55             # base screen glow intensity
    cursor_follow: bool = False    # pupils drift toward the mouse
    look_up_fixed: bool = False    # thinking - fixed upward glance, ignores cursor
    show_zzz: bool = False


# The 12 requested states, plus sensible fallbacks for any legacy
# body-motion states still referenced elsewhere in the app (DRAGGED, WAKE,
# LOOK_*, etc.) so nothing crashes if one of them is ever reached.
FACE_EXPRESSIONS: dict[CharacterState, Expression] = {
    CharacterState.IDLE: Expression(eye_open=1.0, mouth=MouthType.FLAT, cursor_follow=True, glow=0.55),
    CharacterState.HAPPY: Expression(eye_open=0.75, mouth=MouthType.SMILE, glow=0.75),
    CharacterState.SAD: Expression(eye_open=0.6, eye_offset_y=0.25, brow_angle=-12, mouth=MouthType.FROWN, glow=0.35),
    CharacterState.ANGRY: Expression(eye_open=0.45, brow_angle=18, mouth=MouthType.ANGRY_V, glow=0.8),
    CharacterState.CONFUSED: Expression(eye_open=0.85, eye_open_right=0.5, brow_angle=10, mouth=MouthType.WAVY, glow=0.55),
    CharacterState.SURPRISED: Expression(eye_open=1.35, mouth=MouthType.O, glow=0.9),
    CharacterState.THINKING: Expression(eye_open=0.8, eye_offset_y=-0.5, look_up_fixed=True, mouth=MouthType.FLAT, glow=0.5),
    CharacterState.SLEEPY: Expression(eye_open=0.25, eye_offset_y=0.15, mouth=MouthType.FLAT, glow=0.3),
    CharacterState.SLEEP: Expression(eye_open=0.0, mouth=MouthType.NONE, glow=0.2, show_zzz=True),
    CharacterState.TALKING: Expression(eye_open=0.9, mouth=MouthType.TALK_OPEN, glow=0.65, cursor_follow=True),
    CharacterState.EXCITED: Expression(eye_open=1.1, mouth=MouthType.BIG_SMILE, glow=0.95),
    CharacterState.ALERT: Expression(eye_open=1.25, mouth=MouthType.O, glow=0.85, cursor_follow=True),
    # Fallbacks for states outside the 12-state face table:
    CharacterState.WAKE: Expression(eye_open=0.6, mouth=MouthType.FLAT, glow=0.5),
    CharacterState.DRAGGED: Expression(eye_open=1.2, mouth=MouthType.O, glow=0.7),
    CharacterState.WALK_LEFT: Expression(eye_open=1.0, mouth=MouthType.FLAT, cursor_follow=True),
    CharacterState.WALK_RIGHT: Expression(eye_open=1.0, mouth=MouthType.FLAT, cursor_follow=True),
    CharacterState.LOOK_LEFT: Expression(eye_open=1.0, mouth=MouthType.FLAT),
    CharacterState.LOOK_RIGHT: Expression(eye_open=1.0, mouth=MouthType.FLAT),
    CharacterState.LOOK_UP: Expression(eye_open=1.0, eye_offset_y=-0.6, mouth=MouthType.FLAT),
    CharacterState.LOOK_DOWN: Expression(eye_open=1.0, eye_offset_y=0.6, mouth=MouthType.FLAT),
    CharacterState.PLAY: Expression(eye_open=1.1, mouth=MouthType.BIG_SMILE, glow=0.9),
    CharacterState.JUMP: Expression(eye_open=1.2, mouth=MouthType.O, glow=0.85),
    CharacterState.STRETCH: Expression(eye_open=0.5, mouth=MouthType.FLAT, glow=0.4),
    CharacterState.YAWN: Expression(eye_open=0.3, mouth=MouthType.O, glow=0.35),
}

TALK_FRAMES = (MouthType.TALK_OPEN, MouthType.TALK_SMALL, MouthType.SMILE)

_BLINK_INTERVAL_MIN_S = 2.5
_BLINK_INTERVAL_MAX_S = 6.0
_BLINK_DURATION_S = 0.14
_PULSE_PERIOD_S = 3.2
_TALK_FRAME_S = 0.16

# Palette - purple/lavender glow, matching the reference "EMO desktop
# companion" mockup rather than the earlier blue.
GLOW_COLOR = QColor(196, 165, 255)      # eyes, mouth, brows, Zzz
GLOW_HALO_COLOR = QColor(151, 111, 220)  # soft radial "breathing" glow
SCREEN_COLOR = QColor(21, 15, 30, 240)   # near-black casing, faint purple tint
EAR_COLOR = QColor(34, 26, 48, 255)      # same family as the casing, slightly lighter
EDGE_COLOR = QColor(90, 74, 118, 170)    # subtle rim so the silhouette reads on any desktop background
WHISKER_COLOR = QColor(220, 205, 245, 130)  # thin, subtle - decorative, not a UI element


class PixelFaceWidget(QWidget):
    """Draws the whole character: black rounded screen + expressive face.

    Call `set_state()` when the state machine changes, and `tick(dt)` on a
    regular timer (see PetWindow) to advance blink/pulse/talk animation
    and re-render.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._state = CharacterState.IDLE
        self._expr = FACE_EXPRESSIONS[CharacterState.IDLE]

        self._clock = 0.0
        self._next_blink_at = _BLINK_INTERVAL_MIN_S
        self._blink_progress: Optional[float] = None  # None = not blinking
        self._talk_frame_index = 0
        self._talk_clock = 0.0

        self._cursor_local: Optional[QPointF] = None  # cursor pos relative to widget center, normalized

        import random

        self._rng = random.Random()

    # ------------------------------------------------------------------
    def set_state(self, state: CharacterState) -> None:
        self._state = state
        self._expr = FACE_EXPRESSIONS.get(state, FACE_EXPRESSIONS[CharacterState.IDLE])
        self._talk_frame_index = 0
        self._talk_clock = 0.0

    def set_cursor_hint(self, dx_norm: Optional[float], dy_norm: Optional[float]) -> None:
        """dx/dy are the cursor's offset from the widget's center, already
        normalized to roughly [-1, 1] (clamped by the caller). Pass None to
        stop following (e.g. cursor moved to another monitor)."""
        if dx_norm is None or dy_norm is None:
            self._cursor_local = None
        else:
            self._cursor_local = QPointF(dx_norm, dy_norm)

    def tick(self, dt_seconds: float) -> None:
        self._clock += dt_seconds

        # Autonomous blinking - independent of whatever state is showing,
        # except while already fully closed (sleeping) or mid-surprise.
        if self._expr.eye_open > 0.05:
            if self._blink_progress is None:
                if self._clock >= self._next_blink_at:
                    self._blink_progress = 0.0
            else:
                self._blink_progress += dt_seconds
                if self._blink_progress >= _BLINK_DURATION_S:
                    self._blink_progress = None
                    self._clock = 0.0
                    self._next_blink_at = self._rng.uniform(
                        _BLINK_INTERVAL_MIN_S, _BLINK_INTERVAL_MAX_S
                    )

        # Talking mouth cycle
        if self._state == CharacterState.TALKING:
            self._talk_clock += dt_seconds
            if self._talk_clock >= _TALK_FRAME_S:
                self._talk_clock = 0.0
                self._talk_frame_index = (self._talk_frame_index + 1) % len(TALK_FRAMES)

        self.update()

    # ------------------------------------------------------------------
    def _current_eye_open(self) -> tuple[float, float]:
        left = self._expr.eye_open
        right = self._expr.eye_open_right if self._expr.eye_open_right is not None else self._expr.eye_open
        if self._blink_progress is not None:
            # Simple triangular blink envelope (open -> closed -> open)
            phase = self._blink_progress / _BLINK_DURATION_S
            close_amount = 1.0 - abs(phase - 0.5) * 2.0
            left = max(0.0, left * (1.0 - close_amount))
            right = max(0.0, right * (1.0 - close_amount))
        return left, right

    def _current_pupil_offset(self) -> QPointF:
        y = self._expr.eye_offset_y
        x = 0.0
        if self._expr.cursor_follow and self._cursor_local is not None and not self._expr.look_up_fixed:
            x += self._cursor_local.x() * 0.35
            y += self._cursor_local.y() * 0.35
        return QPointF(x, max(-0.8, min(0.8, y)))

    def _current_mouth(self) -> MouthType:
        if self._state == CharacterState.TALKING:
            return TALK_FRAMES[self._talk_frame_index]
        return self._expr.mouth

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        full_rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        # Screen sits inset from the widget's full bounds, leaving room
        # above for cat ears and to the sides for whiskers - both drawn
        # outside the screen itself, like a physical device's casing.
        ear_room = full_rect.height() * 0.16
        whisker_room = full_rect.width() * 0.12
        rect = QRectF(
            full_rect.left() + whisker_room,
            full_rect.top() + ear_room,
            full_rect.width() - whisker_room * 2,
            full_rect.height() - ear_room - full_rect.height() * 0.04,
        )
        radius = min(rect.width(), rect.height()) * 0.22

        self._draw_ears(painter, rect)

        # Screen body
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, SCREEN_COLOR)
        edge_pen = QPen(EDGE_COLOR)
        edge_pen.setWidthF(max(1.0, rect.width() * 0.006))
        painter.setPen(edge_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)

        # Soft "breathing" glow behind the face - part of the idle-alive feel.
        pulse = 0.5 + 0.5 * math.sin((self._clock / _PULSE_PERIOD_S) * 2 * math.pi)
        glow_alpha = int(30 + 40 * self._expr.glow * pulse)
        glow = QRadialGradient(rect.center(), rect.width() * 0.55)
        glow.setColorAt(0.0, QColor(GLOW_HALO_COLOR.red(), GLOW_HALO_COLOR.green(), GLOW_HALO_COLOR.blue(), glow_alpha))
        glow.setColorAt(1.0, QColor(GLOW_HALO_COLOR.red(), GLOW_HALO_COLOR.green(), GLOW_HALO_COLOR.blue(), 0))
        painter.setClipPath(path)
        painter.fillRect(rect, glow)
        painter.setClipping(False)

        self._draw_face(painter, rect)
        self._draw_whiskers(painter, rect, full_rect)
        painter.end()

    @staticmethod
    def _draw_ears(painter: QPainter, screen_rect: QRectF) -> None:
        """Two simple triangular cat ears sitting on top of the screen,
        same material/color as the casing (not glowing) - purely a shape
        cue, like the reference mockup's physical device. A thin rim
        keeps them visible against light or dark desktop backgrounds."""
        ear_w = screen_rect.width() * 0.30
        ear_h = screen_rect.height() * 0.26
        inset = screen_rect.width() * 0.08

        pen = QPen(EDGE_COLOR)
        pen.setWidthF(max(1.0, screen_rect.width() * 0.006))
        painter.setPen(pen)
        painter.setBrush(EAR_COLOR)
        for side in (-1, 1):
            base_x = (
                screen_rect.left() + inset
                if side == -1
                else screen_rect.right() - inset - ear_w
            )
            tip_x = base_x + (ear_w * 0.15 if side == -1 else ear_w * 0.85)
            path = QPainterPath()
            path.moveTo(base_x, screen_rect.top() + ear_h * 0.15)
            path.lineTo(base_x + ear_w, screen_rect.top() + ear_h * 0.15)
            path.lineTo(tip_x, screen_rect.top() - ear_h * 0.7)
            path.closeSubpath()
            painter.drawPath(path)
        painter.setPen(Qt.NoPen)

    @staticmethod
    def _draw_whiskers(painter: QPainter, screen_rect: QRectF, full_rect: QRectF) -> None:
        """A few thin lines poking out either side, filling the margin
        left between the screen and the widget's outer edge - decorative
        only, sized to reliably read at small window sizes."""
        pen = QPen(WHISKER_COLOR)
        pen.setWidthF(max(1.6, screen_rect.width() * 0.01))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        base_y = screen_rect.top() + screen_rect.height() * 0.58
        spacing = screen_rect.height() * 0.08

        for i, dy in enumerate((-spacing, 0, spacing)):
            y = base_y + dy
            tilt = (i - 1) * screen_rect.height() * 0.035
            painter.drawLine(
                QPointF(screen_rect.left() - 1, y),
                QPointF(full_rect.left(), y + tilt),
            )
            painter.drawLine(
                QPointF(screen_rect.right() + 1, y),
                QPointF(full_rect.right(), y + tilt),
            )
        painter.setPen(Qt.NoPen)

    def _draw_face(self, painter: QPainter, rect: QRectF) -> None:
        w, h = rect.width(), rect.height()
        eye_color = GLOW_COLOR
        cx = rect.center().x()
        cy = rect.center().y() - h * 0.05

        eye_gap = w * 0.22
        eye_w = w * 0.16
        eye_max_h = h * 0.20

        left_open, right_open = self._current_eye_open()
        pupil = self._current_pupil_offset()
        pupil_shift = QPointF(pupil.x() * w * 0.05, pupil.y() * h * 0.05)

        eye_centers: list[float] = []
        for side, open_amount in ((-1, left_open), (1, right_open)):
            eye_h = max(2.0, eye_max_h * open_amount)
            ex = cx + side * eye_gap - eye_w / 2 + pupil_shift.x()
            ey = cy - eye_h / 2 + pupil_shift.y()
            eye_rect = QRectF(ex, ey, eye_w, eye_h)
            eye_centers.append(ex + eye_w / 2)
            painter.setBrush(eye_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(eye_rect, eye_w * 0.4, eye_w * 0.4)

        if abs(self._expr.brow_angle) > 0.5:
            self._draw_brows(painter, eye_centers, cy - eye_max_h * 0.75, eye_w, w, self._expr.brow_angle)

        mouth_color = GLOW_COLOR
        mouth_y = cy + h * 0.24
        mouth_w = w * 0.22
        painter.setPen(Qt.NoPen)
        painter.setBrush(mouth_color)

        mouth = self._current_mouth()
        if mouth == MouthType.NONE:
            pass
        elif mouth == MouthType.FLAT:
            painter.drawRoundedRect(QRectF(cx - mouth_w / 2, mouth_y, mouth_w, h * 0.025), 3, 3)
        elif mouth in (MouthType.SMILE, MouthType.TALK_SMALL):
            self._draw_arc_mouth(painter, cx, mouth_y, mouth_w, h * 0.10, upward=True)
        elif mouth == MouthType.BIG_SMILE:
            self._draw_arc_mouth(painter, cx, mouth_y, mouth_w * 1.2, h * 0.14, upward=True)
        elif mouth == MouthType.FROWN:
            self._draw_arc_mouth(painter, cx, mouth_y + h * 0.06, mouth_w, h * 0.08, upward=False)
        elif mouth == MouthType.ANGRY_V:
            self._draw_arc_mouth(painter, cx, mouth_y, mouth_w, h * 0.10, upward=False)
        elif mouth in (MouthType.O, MouthType.TALK_OPEN):
            radius = h * 0.07 if mouth == MouthType.O else h * 0.09
            painter.drawEllipse(QPointF(cx, mouth_y + h * 0.02), radius, radius)
        elif mouth == MouthType.WAVY:
            self._draw_wavy_mouth(painter, cx, mouth_y, mouth_w, h * 0.05)

        if self._expr.show_zzz:
            self._draw_zzz(painter, rect)

    @staticmethod
    def _draw_brows(
        painter: QPainter, eye_centers: list, y: float, eye_w: float, w: float, brow_angle: float
    ) -> None:
        """Simple angled eyebrow lines. Positive brow_angle furrows the
        inner ends downward (angry); negative raises them (sad/worried)."""
        pen = QPen(GLOW_COLOR)
        pen.setWidthF(max(2.0, w * 0.014))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        half_len = eye_w * 0.6
        sign = 1.0 if brow_angle > 0 else -1.0
        slope = eye_w * 0.10 * sign  # inner end shifts by +slope, outer by -slope

        for index, cx_eye in enumerate(eye_centers):
            inner_x = cx_eye + half_len if index == 0 else cx_eye - half_len
            outer_x = cx_eye - half_len if index == 0 else cx_eye + half_len
            painter.drawLine(
                QPointF(outer_x, y - slope), QPointF(inner_x, y + slope)
            )
        painter.setPen(Qt.NoPen)

    @staticmethod
    def _draw_arc_mouth(painter: QPainter, cx: float, y: float, width: float, height: float, upward: bool) -> None:
        rect = QRectF(cx - width / 2, y - height / 2, width, height)
        path = QPainterPath()
        if upward:
            path.moveTo(rect.left(), rect.top())
            path.quadTo(rect.center().x(), rect.bottom() + height * 0.6, rect.right(), rect.top())
        else:
            path.moveTo(rect.left(), rect.bottom())
            path.quadTo(rect.center().x(), rect.top() - height * 0.6, rect.right(), rect.bottom())
        path.lineTo(path.currentPosition().x(), path.currentPosition().y() + height * 0.35)
        painter.drawPath(path)

    @staticmethod
    def _draw_wavy_mouth(painter: QPainter, cx: float, y: float, width: float, height: float) -> None:
        path = QPainterPath()
        segments = 4
        seg_w = width / segments
        start_x = cx - width / 2
        path.moveTo(start_x, y)
        for i in range(segments):
            up = i % 2 == 0
            path.quadTo(
                start_x + seg_w * (i + 0.5), y + (-height if up else height),
                start_x + seg_w * (i + 1), y,
            )
        pen = painter.pen()
        painter.setPen(GLOW_COLOR)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(pen)

    def _draw_zzz(self, painter: QPainter, rect: QRectF) -> None:
        bob = math.sin(self._clock * 1.4) * rect.height() * 0.02
        painter.setPen(QColor(GLOW_COLOR.red(), GLOW_COLOR.green(), GLOW_COLOR.blue(), 220))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(max(8.0, rect.height() * 0.09))
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.right() - rect.width() * 0.3, rect.top() + bob, rect.width() * 0.3, rect.height() * 0.25),
            Qt.AlignRight | Qt.AlignTop,
            "Z z",
        )
