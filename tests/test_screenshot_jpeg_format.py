"""Screenshot capture format selection for the web_h5 (Playwright) backend.

The bot's own screenshot path (PlaywrightGameDevice.screenshot) defaults to
PNG, which is large and slow to encode/decode. A per-device config flag
``web_screenshot_jpeg_quality`` lets a device capture JPEG instead (much
faster + smaller), while leaving the default unchanged (PNG) so OCR/CNN
input is not altered until a quality floor has been benchmarked.

These tests stub ``_start`` (no real browser) and the Playwright IO boundary;
the real screenshot() arg-construction + cv2 decode path still run.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

device_wrapper = pytest.importorskip("device_wrapper")


def _make_png_bytes() -> bytes:
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[:] = (40, 80, 120)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


class _FakeShotTarget:
    """Stands in for ``page.locator(sel).first``; records screenshot kwargs."""

    def __init__(self, payload: bytes, *, raise_on_call: bool = False) -> None:
        self._payload = payload
        self.calls: list[dict] = []
        self._raise_on_call = raise_on_call

    def screenshot(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_on_call:
            raise RuntimeError("locator screenshot unavailable")
        return self._payload


class _FakeLocator:
    def __init__(self, target: _FakeShotTarget) -> None:
        self.first = target


class _FakePage:
    def __init__(self, target: _FakeShotTarget) -> None:
        self._target = target
        self.page_screenshot_calls: list[dict] = []

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._target)

    def screenshot(self, **kwargs):  # full-page fallback path
        self.page_screenshot_calls.append(kwargs)
        return self._target._payload


@pytest.fixture
def make_device(monkeypatch):
    """Build a PlaywrightGameDevice without launching a browser."""

    def _factory(cfg: dict, *, locator_raises: bool = False):
        monkeypatch.setattr(
            device_wrapper.PlaywrightGameDevice, "_start", lambda self: None
        )
        dev = device_wrapper.PlaywrightGameDevice("emulator-test", cfg=cfg)
        dev._ensure_browser_session = lambda *a, **k: None
        target = _FakeShotTarget(_make_png_bytes(), raise_on_call=locator_raises)
        page = _FakePage(target)
        dev._page = page
        return dev, target, page

    return _factory


# -- __init__ config parsing -------------------------------------------------

def test_init_parses_jpeg_quality_from_config(make_device):
    dev, _, _ = make_device({"web_screenshot_jpeg_quality": 85})
    assert dev.screenshot_jpeg_quality == 85


def test_init_jpeg_quality_none_by_default(make_device):
    dev, _, _ = make_device({})
    assert dev.screenshot_jpeg_quality is None


# -- screenshot() applies the format -----------------------------------------

def test_playwright_screenshot_passes_jpeg_kwargs_when_quality_configured(make_device):
    dev, target, _ = make_device({"web_screenshot_jpeg_quality": 85})
    dev.screenshot(format="opencv")
    assert target.calls, "locator screenshot should have been called"
    kwargs = target.calls[0]
    assert kwargs.get("type") == "jpeg"
    assert kwargs.get("quality") == 85


def test_playwright_screenshot_stays_png_when_quality_not_configured(make_device):
    dev, target, _ = make_device({})
    dev.screenshot(format="opencv")
    assert target.calls
    kwargs = target.calls[0]
    assert "type" not in kwargs
    assert "quality" not in kwargs


def test_opencv_and_pillow_formats_are_interchangeable_for_bgr_consumers(make_device):
    """classify_board accepts PIL (np.array -> RGB2BGR) or opencv (BGR) and must
    see identical pixels. This invariant is what lets the mining executor request
    format='opencv' instead of the default pillow with no behaviour change."""
    dev, _, _ = make_device({})
    bgr = dev.screenshot(format="opencv")
    pil = dev.screenshot(format="pillow")
    pil_as_bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    assert np.array_equal(bgr, pil_as_bgr)


def test_fallback_page_screenshot_also_gets_jpeg_kwargs(make_device):
    dev, _, page = make_device({"web_screenshot_jpeg_quality": 80}, locator_raises=True)
    dev.screenshot(format="opencv")
    assert page.page_screenshot_calls, "fallback page.screenshot should have run"
    kwargs = page.page_screenshot_calls[0]
    assert kwargs.get("type") == "jpeg"
    assert kwargs.get("quality") == 80
