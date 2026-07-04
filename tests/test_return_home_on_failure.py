"""Regression tests for audit B6/B7: failure/abort paths must return to 主頁面.

Both 每日加速 (daily_acceleration) and 萬神試煉 (fight_test) previously bailed
out while the game sat on an unknown / non-home page, cascading subsequent
tasks into 「不在主頁面」 aborts. These tests assert the shared reusable helper
game_actions.navigation.navigate_to_main_page is invoked on those paths.

Heavy runtime deps (img_tools / new_cnn / uiautomator2) and the real
navigation chain are stubbed so the tests stay fast and import-safe.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- Heavy-dep stubs (registered before any target import) -------------------
def _ensure_stub(name: str) -> types.ModuleType:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


_img = _ensure_stub("img_tools")
_img.click_str_by_server = MagicMock(return_value=False)

_ncnn = _ensure_stub("new_cnn")
_cnnmod = _ensure_stub("new_cnn.cnn_model")
_cnnmod.predict_image = MagicMock(return_value="not_home")  # never 'homeplace'
_ncnn.cnn_model = _cnnmod

if "uiautomator2" not in sys.modules:
    _u2 = _ensure_stub("uiautomator2")
    _u2.Device = object

# Stub game_actions.navigation so the real OCR/cocos chain never loads and we
# can assert the call. game_actions/__init__.py is empty so importing the
# package is cheap.
_nav = types.ModuleType("game_actions.navigation")
_nav.navigate_to_main_page = MagicMock(return_value=True)
sys.modules["game_actions.navigation"] = _nav


@pytest.fixture(autouse=True)
def _reset_nav_mock():
    _nav.navigate_to_main_page.reset_mock()
    yield


# --- Fix 1: daily_acceleration ----------------------------------------------
def test_daily_acceleration_returns_home_when_cannot_enter_homeplace(monkeypatch):
    from game_actions import daily_tasks

    # Force should_execute=True and never detect 'homeplace'.
    monkeypatch.setattr(daily_tasks, "return_time", lambda *a, **k: None)
    monkeypatch.setattr(daily_tasks.time, "sleep", lambda *a, **k: None)

    d = MagicMock()
    cnn = MagicMock()
    daily_tasks.daily_acceleration(d, "emulator-5554", Cnn_model=cnn)

    daily_tasks.navigate_to_main_page.assert_called_once()
    assert daily_tasks.navigate_to_main_page.call_args.args[0] is d


# --- Fix 2: 萬神試煉 fight_test ----------------------------------------------
@pytest.fixture(scope="module")
def weekly_trials_mod():
    # Load battle/weekly_trials.py without triggering the heavy battle/__init__.
    battle_pkg = types.ModuleType("battle")
    battle_pkg.__path__ = []  # mark as package for relative imports
    sys.modules["battle"] = battle_pkg
    store = types.ModuleType("battle.store")
    store.buy_god_everyweek = MagicMock()
    sys.modules["battle.store"] = store

    path = os.path.join(_REPO, "battle", "weekly_trials.py")
    spec = importlib.util.spec_from_file_location("battle.weekly_trials", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["battle.weekly_trials"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fight_test_returns_home_when_trial_entry_missing(monkeypatch, weekly_trials_mod):
    wt = weekly_trials_mod
    # OCR never finds 萬神試煉 -> abort path.
    monkeypatch.setattr(wt.img_tools, "click_str_by_server", lambda *a, **k: False)
    monkeypatch.setattr(wt.time, "sleep", lambda *a, **k: None)

    d = MagicMock()
    wt.fight_test(d)

    _nav.navigate_to_main_page.assert_called_once()
    assert _nav.navigate_to_main_page.call_args.args[0] is d
