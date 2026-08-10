"""Regression tests for audit B6/B7: failure/abort paths must return to 主頁面.

B6: 每日加速 (daily_acceleration) — cannot enter 家園 → previously bare return
    while off-home, cascading later tasks into 「不在主頁面」 aborts.
B7: 萬神試煉Beta (battle/weekly_trials.fight_test) — entry / settlement
    (_settle_run) failure paths previously relied only on the blind
    _recover_to_home click sequence (23 off-home occurrences on 5558); the
    abort path must additionally invoke the shared
    game_actions.navigation.navigate_to_main_page helper.

Heavy runtime deps (img_tools / new_cnn / uiautomator2) and the real
navigation chain are stubbed so the tests stay fast and import-safe.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- Heavy-dep stubs (registered before any target import) -------------------
def _ensure_stub(name: str) -> types.ModuleType:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


_img = _ensure_stub("img_tools")
_img.click_str_by_server = MagicMock(return_value=False)
_img.check_str_in_region = MagicMock(return_value=False)

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


# --- B6: daily_acceleration ---------------------------------------------------
def test_daily_acceleration_returns_home_when_cannot_enter_homeplace(monkeypatch):
    from game_actions import daily_tasks

    # Force should_execute=True and never detect 'homeplace'.
    monkeypatch.setattr(daily_tasks, "is_due", lambda *a, **k: True)
    monkeypatch.setattr(daily_tasks.time, "sleep", lambda *a, **k: None)

    d = MagicMock()
    cnn = MagicMock()
    daily_tasks.daily_acceleration(d, "emulator-5554", Cnn_model=cnn)

    daily_tasks.navigate_to_main_page.assert_called_once()
    assert daily_tasks.navigate_to_main_page.call_args.args[0] is d


def test_daily_acceleration_does_not_record_when_final_home_check_fails(monkeypatch):
    from game_actions import daily_tasks

    monkeypatch.setattr(daily_tasks, "is_due", lambda *a, **k: True)
    monkeypatch.setattr(daily_tasks.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        daily_tasks,
        "navigate_to_main_page",
        MagicMock(return_value=False),
    )
    recorded: list[str] = []
    monkeypatch.setattr(
        daily_tasks,
        "time_recording",
        lambda ip, name: recorded.append(name),
    )

    result = daily_tasks.daily_acceleration(
        MagicMock(), "emulator-5554", Cnn_model=None
    )

    assert result is False
    assert recorded == []
    daily_tasks.navigate_to_main_page.assert_called_once()


def test_daily_acceleration_reports_due_skip_as_task_result(monkeypatch):
    from game_actions import daily_tasks
    from game_actions.task_registry import TaskOutcome, TaskResult

    monkeypatch.setattr(daily_tasks, "is_due", lambda *a, **k: False)
    result = daily_tasks.daily_acceleration(
        MagicMock(), "emulator-5554", Cnn_model=None
    )

    assert isinstance(result, TaskResult)
    assert result.outcome is TaskOutcome.SKIPPED


# --- B7: 萬神試煉Beta fight_test ------------------------------------------------
@pytest.fixture(scope="module")
def weekly_trials_mod():
    # Load battle/weekly_trials.py without triggering the heavy battle/__init__.
    battle_pkg = types.ModuleType("battle")
    battle_pkg.__path__ = []  # mark as package for relative imports
    sys.modules["battle"] = battle_pkg
    helpers = types.ModuleType("battle._helpers")
    helpers._recover_to_home = MagicMock(return_value=True)
    sys.modules["battle._helpers"] = helpers
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
    # OCR never finds 萬神試煉 in the dungeon list -> entry abort path.
    monkeypatch.setattr(wt.img_tools, "click_str_by_server", lambda *a, **k: False)
    monkeypatch.setattr(wt.time, "sleep", lambda *a, **k: None)

    d = MagicMock()
    result = wt.fight_test(d, rounds=2)

    assert result is False
    _nav.navigate_to_main_page.assert_called_once()
    assert _nav.navigate_to_main_page.call_args.args[0] is d


def test_fight_test_returns_home_when_settle_run_fails(monkeypatch, weekly_trials_mod):
    """Audited path (:134 -> :198): 結算開不出『結束本局』 -> abort -> 強制回主頁."""
    wt = weekly_trials_mod
    # Entry succeeds; battle rounds run; settlement fails.
    monkeypatch.setattr(wt.img_tools, "click_str_by_server", lambda *a, **k: True)
    monkeypatch.setattr(wt.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(wt, "_advance_to_stage", lambda d: True)
    monkeypatch.setattr(wt, "_battle_loop", lambda d: 3)
    monkeypatch.setattr(wt, "_settle_run", lambda d: False)

    d = MagicMock()
    result = wt.fight_test(d, rounds=2)

    assert result is False  # did not complete the 2 rounds
    wt._recover_to_home.assert_called()  # existing best-effort still runs
    _nav.navigate_to_main_page.assert_called_once()
    assert _nav.navigate_to_main_page.call_args.args[0] is d


def test_fight_test_success_path_does_not_force_navigation(monkeypatch, weekly_trials_mod):
    """Full rounds completed -> only _recover_to_home; no forced navigation."""
    wt = weekly_trials_mod
    monkeypatch.setattr(wt.img_tools, "click_str_by_server", lambda *a, **k: True)
    monkeypatch.setattr(wt.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(wt, "_advance_to_stage", lambda d: True)
    monkeypatch.setattr(wt, "_battle_loop", lambda d: 3)
    monkeypatch.setattr(wt, "_settle_run", lambda d: True)
    # pure_ws is the production default; keep this unit test independent from
    # local credentials and exercise the authoritative successful report.
    monkeypatch.setattr(
        wt,
        "_run_pure_ws_wanshen",
        lambda *a, **k: SimpleNamespace(
            success=True,
            rounds_completed=2,
            cap_reached=True,
        ),
    )

    d = MagicMock()
    result = wt.fight_test(d, rounds=2)

    assert result is True
    _nav.navigate_to_main_page.assert_not_called()
