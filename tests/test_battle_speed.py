# -*- coding: utf-8 -*-
"""Behaviour tests for utils.battle_speed.

The idempotency ("注入一次不重複裝") and the scale<=1 clear path live entirely
inside the injected JS string, executed by the browser — a Python fake page
cannot run them. So we test the Python contract with a fake page (what scale is
passed to evaluate, return relaying, None/error handling) and pin the JS guard
with source assertions, which honestly catch a regression if someone edits the
injected script.
"""
from utils import battle_speed
from utils.battle_speed import (
    coerce_battle_speed_scale,
    ensure_battle_speed_on_device,
    install_battle_speed,
)


class FakePage:
    """Records evaluate(js, arg) calls; returns a canned value or raises."""

    def __init__(self, ret="installed:4", raises=False):
        self.calls = []
        self._ret = ret
        self._raises = raises

    def evaluate(self, js, arg=None):
        self.calls.append((js, arg))
        if self._raises:
            raise RuntimeError("boom")
        return self._ret


class FakeDevice:
    def __init__(self, page=None, cfg=None):
        self._page = page
        self.cfg = cfg if cfg is not None else {}


# --- coerce ---------------------------------------------------------------

def test_coerce_battle_speed_scale():
    assert coerce_battle_speed_scale(4) == 4.0
    assert coerce_battle_speed_scale(99) == 10.0
    assert coerce_battle_speed_scale(0) == 1.0
    assert coerce_battle_speed_scale("2.5") == 2.5
    assert coerce_battle_speed_scale("x", default=4) == 4.0


# --- install_battle_speed -------------------------------------------------

def test_install_passes_coerced_scale_to_page():
    page = FakePage()
    install_battle_speed(page, 99)
    assert len(page.calls) == 1
    js, arg = page.calls[0]
    assert js is battle_speed._INSTALL_JS
    assert arg == 10.0  # 99 clamped to the 10x ceiling before injection


def test_install_coerces_invalid_scale_to_default():
    page = FakePage()
    install_battle_speed(page, "not-a-number")
    assert page.calls[0][1] == 4.0


def test_install_relays_page_return_verbatim():
    page = FakePage(ret="updated:2")
    assert install_battle_speed(page, 2) == "updated:2"


def test_install_none_page_returns_none():
    assert install_battle_speed(None, 4) is None


def test_install_swallows_evaluate_error():
    page = FakePage(raises=True)
    assert install_battle_speed(page, 4) is None
    assert len(page.calls) == 1  # it did try once


# --- ensure_battle_speed_on_device ----------------------------------------

def test_ensure_none_device_returns_none():
    assert ensure_battle_speed_on_device(None) is None


def test_ensure_device_without_page_is_noop():
    # An adb device has no Playwright _page → best-effort no-op, returns None.
    assert ensure_battle_speed_on_device(FakeDevice(page=None)) is None


def test_ensure_reads_scale_from_cfg_when_omitted():
    page = FakePage()
    dev = FakeDevice(page=page, cfg={"battle_speed_scale": 2})
    ensure_battle_speed_on_device(dev)
    assert page.calls[0][1] == 2.0


def test_ensure_defaults_to_four_when_cfg_missing():
    page = FakePage()
    dev = FakeDevice(page=page, cfg={})
    ensure_battle_speed_on_device(dev)
    assert page.calls[0][1] == 4.0


def test_ensure_explicit_scale_overrides_cfg():
    page = FakePage()
    dev = FakeDevice(page=page, cfg={"battle_speed_scale": 2})
    ensure_battle_speed_on_device(dev, scale=4)
    assert page.calls[0][1] == 4.0


# --- injected JS contract (source assertions) -----------------------------

def test_install_js_has_idempotency_guard():
    js = battle_speed._INSTALL_JS
    # Second call short-circuits on the existing interval handle rather than
    # installing another setInterval.
    assert "window.__bot_battle_speed_iv" in js
    assert "return 'updated:'" in js
    assert "setInterval(apply, 250)" in js


def test_install_js_has_scale_one_clear_branch():
    js = battle_speed._INSTALL_JS
    assert "s <= 1.0001" in js
    assert "clearInterval" in js
    assert "return 'cleared'" in js
