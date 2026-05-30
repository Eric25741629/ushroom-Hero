"""Gate tests for farm_v2.manager.farm().

The farm task used to unconditionally `navigate_to_farm()` on every wake (5554
wakes hourly), paying the full page-switch + 25s collection loop every time.
The gate throttles entry to once per 8h (sliding window) and still honours
enable_farm. Seed restock (daily) / harvest card (weekly) keep their own
internal cadence — 8h < daily < weekly, so each is caught on some 8h visit.

Decision rule (records only, no page switch needed):
  - enable_farm == False                    -> skip entirely
  - < 8h since last farm visit              -> skip entirely
  - >= 8h (or never visited)                -> enter, then stamp farm_visit
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
from unittest.mock import MagicMock, patch


# --- Hermetic imports: use the real module when present, else a minimal stub. --
# Lets this test run in a deps-light dev env without clobbering a CI that has
# cv2/torch/torchvision installed (an unconditional stub would shadow them).
def _ensure(name, **attrs):
    try:
        return importlib.import_module(name)
    except Exception:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m


_ensure("opencc", OpenCC=lambda *a, **k: types.SimpleNamespace(convert=lambda s: s))
_ensure("paddleocr")
_ensure("img_tools")
_ensure("easyocr")
_ensure("cv2")
_ensure("uiautomator2", Device=object)
# new_cnn.cnn_model imports torchvision; stub the leaf when that chain is absent.
_ensure("new_cnn")
_ensure("new_cnn.cnn_model", predict_image=lambda *a, **k: None)
sys.modules["new_cnn"].cnn_model = sys.modules["new_cnn.cnn_model"]

import farm_v2.manager as manager  # noqa: E402

EIGHT_HOURS = 8 * 3600


class _NavReached(Exception):
    """Raised by the patched navigate_to_farm to prove the gate let us through."""


class _FakeTM:
    """Minimal time-record manager covering the calls farm() makes.

    `expired` drives the 8h gate (is_expired). The seed/card getters return
    'not due' so that, for the record test, no sub-step side effects fire.
    """

    def __init__(self, expired=True):
        self._expired = expired
        self.recorded: list[str] = []
        self.expired_calls: list[tuple[str, int]] = []

    def is_expired(self, name, seconds):
        self.expired_calls.append((name, seconds))
        return self._expired

    def get_time_record(self, name):
        return {"is_next_day": False}  # seed not due

    def is_same_week(self, name):
        return True  # harvest card not due

    def record_time(self, name):
        self.recorded.append(name)


def _run_farm(tm, *, enable_farm=True):
    with patch.object(manager, "navigate_to_farm", side_effect=_NavReached), patch(
        "config_manager.get_device_config", return_value={"enable_farm": enable_farm}
    ):
        return manager.farm(MagicMock(), "emulator-5554", time_manager=tm)


def test_skips_when_not_yet_8h():
    """< 8h since last visit -> never switch into the farm."""
    tm = _FakeTM(expired=False)
    assert _run_farm(tm) == 0.0  # returned before navigate_to_farm (no _NavReached)


def test_skips_when_enable_farm_false():
    """enable_farm=false -> skip even when the 8h window has elapsed."""
    tm = _FakeTM(expired=True)
    assert _run_farm(tm, enable_farm=False) == 0.0


def test_enters_when_8h_elapsed():
    """>= 8h (or never) -> switch into the farm, throttled by an 8h window."""
    tm = _FakeTM(expired=True)
    with pytest.raises(_NavReached):
        _run_farm(tm)
    assert ("farm_visit", EIGHT_HOURS) in tm.expired_calls


def test_stamps_farm_visit_after_a_full_run():
    """A completed visit must record farm_visit, else the 8h gate never trips."""
    tm = _FakeTM(expired=True)
    # Walk the whole body without real device work or the 25s wall-clock loop.
    fake_time = types.SimpleNamespace(
        time=iter([1000.0] + [9000.0] * 50).__next__,  # start, then loop exits at once
        sleep=lambda *a, **k: None,
        localtime=lambda *a, **k: __import__("time").localtime(0),
    )
    with patch.object(manager, "navigate_to_farm", return_value=0.0), patch.object(
        manager, "check_if_parttime", return_value=True
    ), patch.object(manager, "_ensure_work_active"), patch.object(
        manager, "navigate_to_home", return_value=0.0
    ), patch.object(
        manager, "time", fake_time
    ), patch(
        "config_manager.get_device_config", return_value={"enable_farm": True}
    ):
        manager.farm(MagicMock(), "emulator-5554", time_manager=tm)
    assert "farm_visit" in tm.recorded
