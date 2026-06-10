"""Regression tests for opengold_v2.ui_controller.click_cocos_node coordinate math.

Issue #13 / dup-1 STEP 1: the hand-computed world->pixel conversion in
``click_cocos_node`` is replaced by a call to ``sea_v2.navigator.world_to_pixel``.

The result fed to ``device.click`` must stay byte-identical to the old formula:

    px = round(wp["x"] * 540.0 / wp["dw"])
    py = round((wp["dh"] - wp["y"]) * 960.0 / wp["dh"])

So these tests stub the page/device, drive ``click_cocos_node``, and assert the
clicked (px, py) match the old formula exactly (including int type from round()).
"""
import pytest

from opengold_v2.ui_controller import UIController


def _old_formula(wp):
    px = round(wp["x"] * 540.0 / wp["dw"])
    py = round((wp["dh"] - wp["y"]) * 960.0 / wp["dh"])
    return px, py


class _FakePage:
    def __init__(self, wp):
        self._wp = wp

    def evaluate(self, _js, _arg):
        return self._wp


class _FakeDevice:
    def __init__(self, wp):
        self._page = _FakePage(wp)
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


@pytest.mark.parametrize(
    "wp",
    [
        # design 720x1280 (the common cocos visible size)
        {"x": 164.0, "y": 224.0, "dw": 720, "dh": 1280},
        {"x": 360.0, "y": 640.0, "dw": 720, "dh": 1280},
        {"x": 0.0, "y": 0.0, "dw": 720, "dh": 1280},
        {"x": 720.0, "y": 1280.0, "dw": 720, "dh": 1280},
        # non-default design dims (fallback path / odd values to exercise rounding)
        {"x": 333.3, "y": 777.7, "dw": 540, "dh": 960},
        {"x": 271.5, "y": 575.5, "dw": 750, "dh": 1334},
        {"x": 12.34, "y": 56.78, "dw": 1080, "dh": 1920},
    ],
)
def test_click_cocos_node_matches_old_formula(wp):
    device = _FakeDevice(wp)
    ui = UIController(device)

    result = ui.click_cocos_node("UIRoot/Foo/btn")

    assert result is True
    expected = _old_formula(wp)
    assert device.clicks == [expected]
    # round() returns int; make sure we did not regress to float.
    assert isinstance(device.clicks[0][0], int)
    assert isinstance(device.clicks[0][1], int)


def test_click_cocos_node_returns_false_when_no_page():
    class _NoPageDevice:
        _page = None

        def click(self, x, y):  # pragma: no cover - must not be called
            raise AssertionError("click should not run without a page")

    ui = UIController(_NoPageDevice())
    assert ui.click_cocos_node("UIRoot/Foo/btn") is False


def test_click_cocos_node_returns_false_when_node_missing():
    class _NullPage:
        def evaluate(self, _js, _arg):
            return None

    class _Device:
        def __init__(self):
            self._page = _NullPage()

        def click(self, x, y):  # pragma: no cover - must not be called
            raise AssertionError("click should not run for a missing node")

    ui = UIController(_Device())
    assert ui.click_cocos_node("UIRoot/Missing") is False
