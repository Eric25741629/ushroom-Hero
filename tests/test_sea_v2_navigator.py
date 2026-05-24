"""Pure coordinate-math tests for sea_v2.navigator.

Calibration facts (measured live on 5554, 2026-05-25):
- cocos visible/design space = 720x1280; screenshot/click frame = 540x960.
- camera.worldToScreen(home base world (-31910,-1867)) -> logical (164,224).
- horizontal drag of 260 frame-px (left) -> camera world-x +520  => 2.0 world/px, sign inverse.
"""
import math
import pytest

from sea_v2 import navigator as nav


def test_world_to_pixel_maps_home_base_logical_to_bottom_left_frame():
    # logical (164,224) is the live-measured worldToScreen of home base.
    px, py = nav.world_to_pixel(164, 224)
    # x scales by 540/720 = 0.75 ; y flips then scales: (1280-224)*0.75.
    assert px == pytest.approx(123.0, abs=0.5)
    assert py == pytest.approx(792.0, abs=0.5)


def test_world_to_pixel_logical_center_maps_to_frame_center():
    px, py = nav.world_to_pixel(360, 640)
    assert (px, py) == pytest.approx((270.0, 480.0))


def test_is_on_screen_true_inside_with_margin():
    assert nav.is_on_screen((270, 480), margin=100) is True


def test_is_on_screen_false_outside_margin():
    assert nav.is_on_screen((10, 480), margin=100) is False
    assert nav.is_on_screen((270, 5), margin=100) is False


def test_world_delta_to_drag_px_inverts_sign_and_uses_2_world_per_px():
    # To move camera +520 world-x we drag -260 frame-px (leftward). (ADB open-loop)
    assert nav.world_delta_to_drag_px(520) == pytest.approx(-260.0)
    assert nav.world_delta_to_drag_px(-520) == pytest.approx(260.0)


def test_center_drag_for_tile_at_right_edge_moves_content_left():
    # Tile to the RIGHT of center must be dragged left (negative dx) to centre it.
    start, end = nav.center_drag((500, 480))
    assert end[0] < start[0]          # leftward drag
    assert math.isclose(end[1], start[1], abs_tol=1.0)  # no vertical move needed


def test_center_drag_for_centered_tile_is_noop():
    start, end = nav.center_drag((270, 480))
    assert start == pytest.approx(end, abs=1.0)


def test_center_drag_step_magnitude_is_clamped_to_avoid_overshoot():
    # A far-off tile must NOT produce a single giant drag (overshoot / inertia risk);
    # one step is capped so the closed loop converges in several small pans.
    start, end = nav.center_drag((5000, 480), max_step=180)
    assert abs(end[0] - start[0]) <= 180 + 1e-6
