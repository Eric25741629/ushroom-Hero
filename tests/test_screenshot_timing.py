"""TDD tests for per-device screenshot timing (avg_screenshot_ms).

Covers:
- bot_state.record_screenshot_time() rolling-average bookkeeping
- get_all_states() / get_device_state() surface avg_screenshot_ms
- MonitoredDevice.screenshot() records timing via bot_state
"""
import time
import importlib
import sys
import types
import pytest


# ---------------------------------------------------------------------------
# Helpers to get a fresh bot_state module each test (avoids shared state)
# ---------------------------------------------------------------------------

def _fresh_bot_state():
    for key in list(sys.modules):
        if key == "bot_state" or key.startswith("bot_state."):
            del sys.modules[key]
    return importlib.import_module("bot_state")


# ---------------------------------------------------------------------------
# bot_state unit tests
# ---------------------------------------------------------------------------

class TestRecordScreenshotTime:
    def test_first_record_sets_avg(self):
        bs = _fresh_bot_state()
        bs.init_device("test-01")
        bs.record_screenshot_time("test-01", 120.0)
        state = bs.get_all_states()["test-01"]
        assert state["avg_screenshot_ms"] == pytest.approx(120.0)

    def test_multiple_records_compute_average(self):
        bs = _fresh_bot_state()
        bs.init_device("test-02")
        bs.record_screenshot_time("test-02", 100.0)
        bs.record_screenshot_time("test-02", 200.0)
        bs.record_screenshot_time("test-02", 300.0)
        state = bs.get_all_states()["test-02"]
        assert state["avg_screenshot_ms"] == pytest.approx(200.0)

    def test_rolling_window_caps_at_50_samples(self):
        bs = _fresh_bot_state()
        bs.init_device("test-03")
        # Push 60 values: first 10 are 0.0, last 50 are 100.0
        for _ in range(10):
            bs.record_screenshot_time("test-03", 0.0)
        for _ in range(50):
            bs.record_screenshot_time("test-03", 100.0)
        state = bs.get_all_states()["test-03"]
        # Window should only contain last 50 (all 100.0)
        assert state["avg_screenshot_ms"] == pytest.approx(100.0)

    def test_device_without_timing_has_no_avg(self):
        bs = _fresh_bot_state()
        bs.init_device("test-04")
        state = bs.get_all_states()["test-04"]
        assert state.get("avg_screenshot_ms") is None

    def test_record_for_unknown_device_does_not_crash(self):
        bs = _fresh_bot_state()
        # Should silently ignore (device not in _states yet)
        bs.record_screenshot_time("ghost-device", 50.0)

    def test_update_state_does_not_reset_avg(self):
        bs = _fresh_bot_state()
        bs.init_device("test-05")
        bs.record_screenshot_time("test-05", 80.0)
        bs.update_state("test-05", task="挖礦", step="掘進中")
        state = bs.get_all_states()["test-05"]
        assert state["avg_screenshot_ms"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# MonitoredDevice integration test (patches the underlying ADB device)
# ---------------------------------------------------------------------------

class TestMonitoredDeviceTimesScreenshot:
    def _make_device(self, bs, ip="mon-01"):
        """Build a MonitoredDevice with a fake ADB backing device."""
        # Patch bot_state in device_wrapper so it uses our fresh instance
        import device_wrapper as dw
        dw.bot_state = bs

        class FakeAdb:
            def screenshot(self, *args, **kwargs):
                time.sleep(0.02)   # 20 ms simulated latency
                from PIL import Image
                return Image.new("RGB", (10, 10))

        bs.init_device(ip)
        device = dw.MonitoredDevice(FakeAdb(), ip)
        return device

    def test_screenshot_records_positive_timing(self):
        bs = _fresh_bot_state()
        dev = self._make_device(bs, "mon-01")
        dev.screenshot()
        state = bs.get_all_states()["mon-01"]
        avg = state.get("avg_screenshot_ms")
        assert avg is not None
        assert avg > 0

    def test_multiple_screenshots_accumulate(self):
        bs = _fresh_bot_state()
        dev = self._make_device(bs, "mon-02")
        for _ in range(3):
            dev.screenshot()
        state = bs.get_all_states()["mon-02"]
        avg = state.get("avg_screenshot_ms")
        # 3 samples of ~20ms each; avg should be within 5..200 ms
        assert avg is not None
        assert 5 < avg < 200
