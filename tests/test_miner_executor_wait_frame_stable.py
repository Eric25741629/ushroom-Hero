"""Tests for `wait_frame_stable` — the adaptive animation-settle helper.

Without this helper the executor used a fixed 0.5-1.0s tail sleep after every
dig/item use, which wasn't enough for the game's slower animations (pit
reward popups, row-6 scroll, bomb explosions). The helper polls screenshots
and returns once two consecutive frames stay within a pixel-diff threshold.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import numpy as np
import pytest


# ----- light-weight import shim -----
#
# `miner.planning.executor` pulls `miner.core.ocr_utils` which in turn does
# `import img_tools`. `img_tools` requires opencc / paddleocr etc., none of
# which are relevant for this unit test. Inject a stub module so the import
# chain succeeds without the heavy deps.
for stub_name in ("opencc", "paddleocr", "img_tools"):
    if stub_name not in sys.modules:
        stub = types.ModuleType(stub_name)
        if stub_name == "opencc":
            stub.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[stub_name] = stub

# Stub `tools` module — the real one has heavy imports too.
if "tools" not in sys.modules:
    tools_stub = types.ModuleType("tools")
    tools_stub.click_white = lambda *a, **kw: None
    sys.modules["tools"] = tools_stub

from miner.planning.executor import wait_frame_stable  # noqa: E402


class _FakeDevice:
    """Minimal device mock returning a scripted sequence of frames."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._idx = 0
        self.screenshot_count = 0

    def screenshot(self, format="opencv"):  # noqa: A002 - match real API
        self.screenshot_count += 1
        frame = self._frames[min(self._idx, len(self._frames) - 1)]
        self._idx += 1
        return frame

    def sleep(self, _):  # used by tap_cell in real code; unused here
        pass


def _frame(value: int, shape=(960, 540, 3)) -> np.ndarray:
    return np.full(shape, value, dtype=np.uint8)


def test_wait_frame_stable_returns_immediately_when_frames_identical():
    # Two identical frames → first poll pair matches → should exit fast.
    stable = _frame(100)
    dev = _FakeDevice([stable, stable, stable])
    with patch("miner.planning.executor.time.sleep"):  # drop real sleeps
        result = wait_frame_stable(
            dev, poll_interval=0.0, max_wait=1.0, diff_threshold=1.0, min_wait=0.0
        )
    assert result is stable
    # Expected: 1 screenshot pre-loop + 1 inside loop = 2. Some flex for impl.
    assert dev.screenshot_count <= 3


def test_wait_frame_stable_waits_until_animation_settles():
    # Simulate an animation: 3 changing frames then stable.
    dev = _FakeDevice([_frame(0), _frame(40), _frame(80), _frame(100), _frame(100), _frame(100)])
    with patch("miner.planning.executor.time.sleep"):
        result = wait_frame_stable(
            dev, poll_interval=0.0, max_wait=2.0, diff_threshold=1.0, min_wait=0.0
        )
    # Should land on one of the stable frames (value 100).
    assert int(result.mean()) == 100
    # Must have polled through the animation — at least 4 screenshots.
    assert dev.screenshot_count >= 4


def test_wait_frame_stable_gives_up_at_max_wait():
    # Never-stable animation. Helper must return the last frame instead of
    # hanging forever.
    frames = [_frame(v) for v in range(0, 255, 30)]
    dev = _FakeDevice(frames + [_frame(250)] * 50)

    call_count = {"n": 0}

    def fake_sleep(seconds):
        # Simulate passage of time so max_wait triggers eventually.
        call_count["n"] += 1

    with patch("miner.planning.executor.time.sleep", side_effect=fake_sleep), \
         patch("miner.planning.executor.time.time", side_effect=_fake_clock(step=0.5)):
        result = wait_frame_stable(
            dev, poll_interval=0.5, max_wait=1.0, diff_threshold=1.0, min_wait=0.0
        )
    assert result is not None
    # Not crashed, returned a real frame.
    assert result.shape == (960, 540, 3)


def _fake_clock(step: float):
    """Monotonically advancing fake time.time() to trigger max_wait exits."""
    t = [0.0]

    def _now():
        t[0] += step
        return t[0]

    return _now


def test_wait_frame_stable_respects_roi():
    # Two frames differ only OUTSIDE the ROI. With ROI set, they should look
    # stable; without ROI (full frame compare), they wouldn't.
    shape = (960, 540, 3)
    f1 = _frame(100, shape)
    f2 = f1.copy()
    f2[0:100, 0:100] = 255  # big change in top-left, outside our ROI

    # ROI covers only the middle board region — the changes should be invisible.
    roi = (300, 800, 50, 500)
    dev = _FakeDevice([f1, f2, f2])
    with patch("miner.planning.executor.time.sleep"):
        result = wait_frame_stable(
            dev, roi=roi, poll_interval=0.0, max_wait=1.0, diff_threshold=1.0, min_wait=0.0
        )
    assert result is not None
    # With ROI limiting the compare window, helper should settle fast.
    assert dev.screenshot_count <= 3


def test_wait_frame_stable_honors_min_wait():
    # `min_wait` is a hard sleep before polling starts — useful to let click
    # events reach the device before we screenshot.
    dev = _FakeDevice([_frame(100), _frame(100)])
    sleep_calls = []
    with patch("miner.planning.executor.time.sleep", side_effect=sleep_calls.append):
        wait_frame_stable(
            dev, poll_interval=0.0, max_wait=1.0, diff_threshold=1.0, min_wait=0.5
        )
    # First sleep call must be the min_wait pre-poll.
    assert sleep_calls and sleep_calls[0] == 0.5
