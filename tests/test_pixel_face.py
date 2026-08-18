import pytest

from app.character.pixel_face import FACE_EXPRESSIONS, PixelFaceWidget
from app.character.state_machine import CharacterState
from app.character.theme import THEME_ORDER

REQUIRED_STATES = [
    CharacterState.IDLE,
    CharacterState.HAPPY,
    CharacterState.SAD,
    CharacterState.ANGRY,
    CharacterState.CONFUSED,
    CharacterState.SURPRISED,
    CharacterState.THINKING,
    CharacterState.SLEEPY,
    CharacterState.SLEEP,
    CharacterState.TALKING,
    CharacterState.EXCITED,
    CharacterState.ALERT,
]

# The four expressions that were missing from the reference sheet until
# now - the ones the user specifically flagged as "pending".
PENDING_STATES = [
    CharacterState.BLUSH,
    CharacterState.SHY,
    CharacterState.HEART,
    CharacterState.WINK,
]


@pytest.mark.parametrize("state", REQUIRED_STATES)
def test_all_required_states_have_an_expression(state):
    assert state in FACE_EXPRESSIONS


def test_sleep_state_has_closed_eyes_and_zzz():
    expr = FACE_EXPRESSIONS[CharacterState.SLEEP]
    assert expr.eye_open == 0.0
    assert expr.show_zzz is True


def test_sleepy_eyes_more_open_than_sleep_but_less_than_idle():
    sleepy = FACE_EXPRESSIONS[CharacterState.SLEEPY].eye_open
    idle = FACE_EXPRESSIONS[CharacterState.IDLE].eye_open
    sleep = FACE_EXPRESSIONS[CharacterState.SLEEP].eye_open
    assert sleep < sleepy < idle


def test_confused_is_asymmetric():
    expr = FACE_EXPRESSIONS[CharacterState.CONFUSED]
    assert expr.eye_open_right is not None
    assert expr.eye_open_right != expr.eye_open


def test_thinking_looks_upward_and_ignores_cursor():
    expr = FACE_EXPRESSIONS[CharacterState.THINKING]
    assert expr.eye_offset_y < 0
    assert expr.look_up_fixed is True


def test_surprised_and_alert_have_wide_eyes():
    assert FACE_EXPRESSIONS[CharacterState.SURPRISED].eye_open > 1.0
    assert FACE_EXPRESSIONS[CharacterState.ALERT].eye_open > 1.0


def test_widget_set_state_updates_current_expression(qapp):
    widget = PixelFaceWidget()
    widget.set_state(CharacterState.HAPPY)
    assert widget._expr == FACE_EXPRESSIONS[CharacterState.HAPPY]


@pytest.mark.parametrize("state", REQUIRED_STATES)
def test_widget_renders_every_state_without_crashing(qapp, state):
    widget = PixelFaceWidget()
    widget.resize(160, 160)
    widget.set_state(state)
    for _ in range(5):
        widget.tick(0.05)
    pixmap = widget.grab()
    assert pixmap.width() == 160
    assert pixmap.height() == 160


@pytest.mark.parametrize("state", PENDING_STATES)
def test_pending_expressions_exist_and_render(qapp, state):
    assert state in FACE_EXPRESSIONS
    widget = PixelFaceWidget()
    widget.resize(160, 160)
    widget.set_state(state)
    for _ in range(5):
        widget.tick(0.05)
    pixmap = widget.grab()
    assert pixmap.width() == 160


def test_heart_expression_uses_heart_eyes():
    assert FACE_EXPRESSIONS[CharacterState.HEART].heart_eyes is True


def test_wink_is_asymmetric_like_a_real_wink():
    expr = FACE_EXPRESSIONS[CharacterState.WINK]
    assert expr.eye_open_right is not None
    assert expr.eye_open_right < expr.eye_open


def test_blush_and_shy_have_blush_flag():
    assert FACE_EXPRESSIONS[CharacterState.BLUSH].blush is True
    assert FACE_EXPRESSIONS[CharacterState.SHY].blush is True


def test_locked_state_has_closed_eyes_and_no_zzz():
    expr = FACE_EXPRESSIONS[CharacterState.LOCKED]
    assert expr.eye_open == 0.0
    assert expr.show_zzz is False


def test_peek_one_eye_opens_an_eye_while_locked(qapp):
    widget = PixelFaceWidget()
    widget.set_state(CharacterState.LOCKED)
    for _ in range(40):  # let the spring settle fully closed first
        widget.tick(0.05)
    closed_left, closed_right = widget._render_eye_open()
    assert closed_left == pytest.approx(0.0, abs=0.01)
    assert closed_right == pytest.approx(0.0, abs=0.01)

    widget.peek_one_eye(duration=0.5)
    widget.tick(0.05)
    left, right = widget._render_eye_open()
    assert left > 0.0 or right > 0.0

    # Peek expires on its own.
    widget.tick(1.0)
    left, right = widget._render_eye_open()
    assert left == pytest.approx(0.0, abs=0.001)
    assert right == pytest.approx(0.0, abs=0.001)


@pytest.mark.parametrize("theme_key", THEME_ORDER)
@pytest.mark.parametrize("state", REQUIRED_STATES + PENDING_STATES)
def test_every_theme_renders_every_state_without_crashing(qapp, theme_key, state):
    widget = PixelFaceWidget(theme_key=theme_key)
    widget.resize(140, 140)
    widget.set_state(state)
    widget.tick(0.05)
    assert widget._theme.key == theme_key
    widget.grab()  # must not raise


def test_zzz_particles_stay_within_widget_bounds(qapp):
    """Regression test: the previous Zzz rendering could draw text past
    the widget's own edge. All three cartoon Zzz glyphs' anchor points
    must stay inside the widget bounds at every phase of their loop."""
    widget = PixelFaceWidget()
    widget.resize(160, 160)
    widget.set_state(CharacterState.SLEEP)

    from PySide6.QtCore import QRectF

    full_rect = QRectF(widget.rect()).adjusted(3, 3, -3, -3)

    for _ in range(60):  # sweep through more than one full Zzz loop
        widget.tick(0.05)
        base_x = full_rect.right() - full_rect.width() * 0.26
        base_y = full_rect.top() + full_rect.height() * 0.30
        travel_y = full_rect.height() * 0.16
        travel_x = full_rect.width() * 0.05
        for i in range(3):
            phase = ((widget._clock / 1.8) + (i / 3)) % 1.0
            eased = phase * phase * (3.0 - 2.0 * phase)
            x = base_x + travel_x * eased
            y = base_y - travel_y * eased
            assert full_rect.left() <= x <= full_rect.right()
            assert full_rect.top() <= y <= full_rect.bottom()


def test_talking_cycles_through_multiple_mouth_frames(qapp):
    widget = PixelFaceWidget()
    widget.set_state(CharacterState.TALKING)
    frames_seen = set()
    for _ in range(20):
        widget.tick(0.05)
        frames_seen.add(widget._current_mouth())
    assert len(frames_seen) > 1


def test_cursor_hint_shifts_pupils_when_state_follows_cursor(qapp):
    widget = PixelFaceWidget()
    widget.set_state(CharacterState.IDLE)  # cursor_follow=True
    widget.set_cursor_hint(None, None)
    baseline = widget._current_pupil_offset()

    widget.set_cursor_hint(1.0, 0.0)
    shifted = widget._current_pupil_offset()
    assert shifted.x() != baseline.x()


def test_cursor_hint_ignored_when_state_does_not_follow(qapp):
    widget = PixelFaceWidget()
    widget.set_state(CharacterState.THINKING)  # look_up_fixed=True
    widget.set_cursor_hint(1.0, 1.0)
    offset = widget._current_pupil_offset()
    assert offset.x() == 0.0


def test_eventually_blinks_on_its_own(qapp):
    widget = PixelFaceWidget()
    widget.set_state(CharacterState.IDLE)
    blinked = False
    for _ in range(300):  # 300 * 0.05s = 15s, comfortably past max blink interval
        widget.tick(0.05)
        if widget._blink_progress is not None:
            blinked = True
            break
    assert blinked
