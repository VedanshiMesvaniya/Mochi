from app.character.movement import Mover, ScreenBounds


def make_bounds() -> ScreenBounds:
    return ScreenBounds(left=0, top=0, right=800, bottom=600)


def test_mover_steps_within_bounds():
    bounds = make_bounds()
    mover = Mover(x=100, y=500, width=50, height=50, speed_px_per_tick=10, direction=1)
    x, y = mover.step(bounds)
    assert x == 110
    assert y == 500


def test_mover_bounces_off_right_edge():
    bounds = make_bounds()
    mover = Mover(x=790, y=500, width=50, height=50, speed_px_per_tick=10, direction=1)
    mover.step(bounds)
    assert mover.direction == -1


def test_mover_bounces_off_left_edge():
    bounds = make_bounds()
    mover = Mover(x=5, y=500, width=50, height=50, speed_px_per_tick=10, direction=-1)
    mover.step(bounds)
    assert mover.direction == 1


def test_teleport_clamps_into_bounds():
    bounds = make_bounds()
    mover = Mover(x=0, y=0, width=50, height=50)
    x, y = mover.teleport(10000, -500, bounds)
    assert x == bounds.right - 50
    assert y == bounds.top


def test_clamp_respects_all_edges():
    bounds = make_bounds()
    x, y = bounds.clamp(-100, -100, 50, 50)
    assert (x, y) == (0, 0)
    x, y = bounds.clamp(10000, 10000, 50, 50)
    assert (x, y) == (750, 550)
