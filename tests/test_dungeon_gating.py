"""daily_pipeline 每日任務 gating 測試。

驗證 DailyContext 上的 granular 開關能各自獨立地跳過對應任務：
  - enable_hellgate  → Task 1 地獄之門
  - enable_arena     → Task 10 競技場
  - enable_mining    → Task 11 挖礦/Oracle
  - enable_cloud_battle → Task 16 雲端戰鬥
  - enable_wanshen   → Task 15 轉發給 _run_weekly_dungeon 的 flag
  - enable_biweekly  → Task 17 轉發給 _run_biweekly_dungeon 的 flag

daily_pipeline 會拉入大量 ML/vision/裝置重依賴（torch / cv2 / img_tools …），
在測試沙箱裡不可能真的載入。這裡用一個 meta_path finder 把那些重套件換成
可任意 subclass / call 的 dummy，讓 daily_pipeline 純粹以 Python 匯入成功；
接著把 _run_tasks 會呼叫到的每個外部 callable monkeypatch 成 MagicMock，
只讓被測 gate 對應的函式維持可觀察，斷言它「有無被呼叫」。
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- Heavy-dependency stub harness ------------------------------------------
_HEAVY = {
    "cv2", "opencc", "paddleocr", "easyocr", "torch", "torchvision", "numpy",
    "img_tools", "sklearn", "scipy", "PIL", "onnxruntime", "paddle",
    "matplotlib", "pandas",
}


class _DummyAny:
    """可被 subclass、可被 call、任意屬性存取都回自身型別的萬用替身。"""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _DummyAny()

    def __getattr__(self, name):
        return _DummyAny()


class _AutoStubModule(types.ModuleType):
    __path__ = []  # 標記為 package，讓 submodule (torch.nn) 也能匯入

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _DummyAny


class _HeavyFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in _HEAVY:
            return importlib.machinery.ModuleSpec(name, self)
        return None

    def create_module(self, spec):
        return _AutoStubModule(spec.name)

    def exec_module(self, module):
        pass


# 強制覆蓋(可能已被別的測試 stub 成空 module)，並掛上 finder 處理 submodule。
for _name in _HEAVY:
    sys.modules[_name] = _AutoStubModule(_name)
if not any(isinstance(f, _HeavyFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _HeavyFinder())
if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

import game_actions.daily_pipeline as dp  # noqa: E402


def _tuesday_10_05():
    """Tuesday 10:05 — tm_min<20 (地獄之門窗) 且非 20-23 點(略過 Task 12)。"""
    return time.strptime("2026-04-21 10:05:00", "%Y-%m-%d %H:%M:%S")


def _neutralize(monkeypatch):
    """把 _run_tasks 會碰到的所有外部 callable 換掉，回傳共用 new_battle mock。"""
    # bot_state：避免真實 per-device state 依賴
    monkeypatch.setattr(dp.bot_state, "check_force_sleep", lambda ip: False)
    monkeypatch.setattr(dp.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(dp.bot_state, "check_pause", lambda ip: False)
    monkeypatch.setattr(dp.bot_state, "check_skip_sleep", lambda ip: False)

    # 頁面判定一律「主頁面」，紀錄一律「未做過」
    monkeypatch.setattr(dp, "get_stage_with_check", lambda *a, **k: "主頁面")
    monkeypatch.setattr(dp, "return_time", lambda *a, **k: None)
    monkeypatch.setattr(dp, "time_recording", lambda *a, **k: None)
    monkeypatch.setattr(dp, "is_record_expired", lambda *a, **k: False)
    monkeypatch.setattr(dp, "log_main_page_mismatch", lambda *a, **k: None)
    monkeypatch.setattr(dp, "save_error_screenshot", lambda *a, **k: None)

    def _fake_run_at_main_page(d, ip, cnn, task_name=None, mismatch_reason=None,
                               fn=None, *, step=None, log=None):
        if fn is not None:
            fn()
        return "主頁面"

    monkeypatch.setattr(dp, "_run_at_main_page", _fake_run_at_main_page)

    for name in (
        "farm_manager", "reward", "get_Guardian_Spirit", "get_skill_and_partner",
        "Store", "rank_events", "daily_acceleration", "click_arena_challenges",
        "oracle", "_run_periodic_cycle", "run_statue_weekly_if_due",
        "run_dragon_realm_if_due", "run_fannaoxiao_if_due",
        "run_ladder_reward_if_due", "run_redpack_check_if_due",
        "run_carpark_check_if_due", "_run_weekly_dungeon", "_run_biweekly_dungeon",
        "daily_gift_task", "_run_lamp_if_due", "switch_skill", "click_white",
        "_sea_dispatch",
    ):
        if hasattr(dp, name):
            monkeypatch.setattr(dp, name, MagicMock(), raising=False)

    new_battle_mock = MagicMock()
    monkeypatch.setattr(dp, "new_battle", new_battle_mock)
    return new_battle_mock


def _run(monkeypatch, **flags):
    """建 DailyContext 跑一輪 _run_tasks，回傳 (new_battle_mock)。"""
    nb = _neutralize(monkeypatch)
    ctx = dp.DailyContext(
        d=MagicMock(),
        ip="emu-test",
        Cnn_model=object(),
        clf=object(),
        rl_recorder=object(),
        current_time=_tuesday_10_05(),
        enable_dungeon_manager=True,
        wheel_manager=MagicMock(),
        mission_manager=MagicMock(),
        family_manager=MagicMock(),
        **flags,
    )
    dp._run_tasks(ctx)
    return nb


# --- 地獄之門 (enable_hellgate) ---------------------------------------------

def test_hellgate_runs_when_enabled(monkeypatch):
    nb = _run(monkeypatch, enable_hellgate=True)
    assert nb.hell_door.called


def test_hellgate_skipped_when_disabled(monkeypatch):
    nb = _run(monkeypatch, enable_hellgate=False)
    assert not nb.hell_door.called


# --- 競技場 (enable_arena) ---------------------------------------------------

def test_arena_runs_when_enabled(monkeypatch):
    _run(monkeypatch, enable_arena=True)
    assert dp.click_arena_challenges.called


def test_arena_skipped_when_disabled(monkeypatch):
    _run(monkeypatch, enable_arena=False)
    assert not dp.click_arena_challenges.called


# --- 挖礦 (enable_mining) ----------------------------------------------------

def test_mining_runs_when_enabled(monkeypatch):
    _run(monkeypatch, enable_mining=True)
    assert dp.oracle.called


def test_mining_skipped_when_disabled(monkeypatch):
    _run(monkeypatch, enable_mining=False)
    assert not dp.oracle.called


# --- 挖礦 gate 與 WS-skip 併存：WS 已完成時即使 enabled 也不跑 -----------------

def test_mining_skipped_when_ws_done_even_if_enabled(monkeypatch):
    nb = _neutralize(monkeypatch)  # noqa: F841
    ctx = dp.DailyContext(
        d=MagicMock(), ip="emu-test", Cnn_model=object(), clf=object(),
        rl_recorder=object(), current_time=_tuesday_10_05(),
        enable_dungeon_manager=True, wheel_manager=MagicMock(),
        mission_manager=MagicMock(), family_manager=MagicMock(),
        enable_mining=True, ws_done=frozenset({"挖礦/Oracle"}),
    )
    dp._run_tasks(ctx)
    assert not dp.oracle.called


# --- 雲端戰鬥 (enable_cloud_battle) -----------------------------------------

def test_cloud_battle_runs_when_enabled(monkeypatch):
    nb = _run(monkeypatch, enable_cloud_battle=True)
    assert nb.run_weekly_cloud_fighting_single.called


def test_cloud_battle_skipped_when_disabled(monkeypatch):
    nb = _run(monkeypatch, enable_cloud_battle=False)
    assert not nb.run_weekly_cloud_fighting_single.called


def test_cloud_battle_and_ladder_reward_skipped_when_ws_done(monkeypatch):
    nb = _neutralize(monkeypatch)
    ctx = dp.DailyContext(
        d=MagicMock(), ip="emu-test", Cnn_model=object(), clf=object(),
        rl_recorder=object(), current_time=_tuesday_10_05(),
        enable_dungeon_manager=True, wheel_manager=MagicMock(),
        mission_manager=MagicMock(), family_manager=MagicMock(),
        enable_cloud_battle=True,
        ws_done=frozenset({"雲端戰鬥", "天梯每週獎勵"}),
    )
    dp._run_tasks(ctx)
    assert not nb.run_weekly_cloud_fighting_single.called
    assert not dp.run_ladder_reward_if_due.called


# --- 萬神試煉 / 雙週：daily_pipeline 只負責把 flag 轉發給排程器 --------------

def test_wanshen_flag_forwarded_to_weekly_dungeon(monkeypatch):
    _run(monkeypatch, enable_wanshen=False)
    assert dp._run_weekly_dungeon.called
    # 位置引數: (d, ip, stage, enable_wanshen, current_time)
    assert dp._run_weekly_dungeon.call_args.args[3] is False


def test_biweekly_flag_forwarded_to_biweekly_dungeon(monkeypatch):
    _run(monkeypatch, enable_biweekly=False)
    assert dp._run_biweekly_dungeon.called
    # 位置引數: (d, ip, stage, enable_biweekly, now_local)
    assert dp._run_biweekly_dungeon.call_args.args[3] is False


# --- 各 flag 獨立：關 hellgate 不影響 arena/mining --------------------------

def test_flags_are_independent(monkeypatch):
    nb = _run(monkeypatch, enable_hellgate=False, enable_arena=True, enable_mining=True)
    assert not nb.hell_door.called
    assert dp.click_arena_challenges.called
    assert dp.oracle.called
