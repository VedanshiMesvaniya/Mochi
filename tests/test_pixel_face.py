import pytest

from app.character.pixel_face import FACE_EXPRESSIONS, PixelFaceWidget
from app.character.state_machine import CharacterState

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
