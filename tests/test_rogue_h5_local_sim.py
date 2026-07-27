# -*- coding: utf-8 -*-
"""battle_loop local_sim 路徑單元測試（TDD，純 Mock，不接真 device / Playwright）。

sim["result"] 語意：0 = 攻方（我方）勝，非 0（通常 1）= 失敗。
來源：simulate.py SIM_JS 中 `const result = sim.result`，
競技場邏輯 `wid = (0 === result) ? atk.id : defRole.id` 即「result=0 攻方贏」；
rogue 用相同欄位，result_body_from_sim 直接回 {"result": int, "precent": int}。
同時見 BATTLE_SIM_ARCHITECTURE.md:49 — 小寶 2026-07-17 live 5 次確認。
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, call, patch


# battle.rogue_h5 自帶 utils.logging_utils fallback（try/except），直接載入即可
from battle import rogue_h5 as rh  # noqa: E402


# ── 假 page：最小化實作，讓 state() 可控 ─────────────────────────────────────

class _FakePage:
    """每次 evaluate() 依 JS 片段回傳預設值；外層 monkeypatch 替換特定函式。"""

    def __init__(self):
        pass

    def evaluate(self, js: str, arg: Any = None):
        # 狀態 JS → 回 {} (UNKNOWN)，讓 battle_loop 走非 STAGE 路徑
        # 測試透過 monkeypatch 替換 rh.state/rh.emit 控制流程
        return {}


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_run_sim_path(results: list):
    """每次呼叫從 results pop 最前面的 dict 回傳。"""
    def _run_sim_path(*a, **kw):
        return results.pop(0) if results else {"ok": False, "err": "empty"}
    return _run_sim_path


# ══════════════════════════════════════════════════════════════════════════════
# 正式測試
# ══════════════════════════════════════════════════════════════════════════════


class TestBattleLoopLocalSim:
    """battle_loop(page, mode="local_sim") 的行為規格。"""

    def test_calls_run_sim_path_not_wait_result(self, monkeypatch):
        """local_sim 路徑必須呼叫 run_sim_path，不可進入 _wait_result 90 秒輪詢。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        # 控制狀態序列：第 1 次 STAGE，之後 HOME（讓迴圈自然退出）
        states = iter([rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)

        wait_result_calls = []
        monkeypatch.setattr(rh, "_wait_result", lambda _p: wait_result_calls.append(1) or rh.RESULT_WIN)

        sim_calls = []
        def _fake_run_sim_path(*a, **kw):
            sim_calls.append(kw)
            return {"ok": True, "sim": {"result": 1, "precent": 0}}  # 立刻失敗，結束迴圈

        with patch.dict("sys.modules", {
            "battle_calc.runner": types.SimpleNamespace(run_sim_path=_fake_run_sim_path),
            "battle_calc.page_hooks": types.SimpleNamespace(
                install_hooks=lambda _: "installed",
                clear_combat=lambda *a: None,
                set_block_result=lambda *a: None,
            ),
        }):
            rh.battle_loop(page, mode="local_sim")

        assert sim_calls, "run_sim_path 必須被呼叫"
        assert not wait_result_calls, "_wait_result 不應在 local_sim 路徑被呼叫"

    def test_win_continues_next_stage(self, monkeypatch):
        """sim result=0（我方勝）→ 繼續下一關；result=1 → 本局結束。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        # 兩關 STAGE，第三次才結束
        states = iter([rh.STAGE, rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)
        monkeypatch.setattr(rh, "_wait_result", lambda _p: rh.RESULT_WIN)

        # 第 1 關贏，第 2 關輸
        results = [
            {"ok": True, "sim": {"result": 0, "precent": 80}},  # win
            {"ok": True, "sim": {"result": 1, "precent": 0}},   # lose
        ]

        with patch.dict("sys.modules", {
            "battle_calc.runner": types.SimpleNamespace(run_sim_path=_make_run_sim_path(results)),
            "battle_calc.page_hooks": types.SimpleNamespace(
                install_hooks=lambda _: None,
                clear_combat=lambda *a: None,
                set_block_result=lambda *a: None,
            ),
        }):
            fought = rh.battle_loop(page, mode="local_sim")

        assert fought == 2, f"應打 2 關(1贏1輸)，實際 {fought}"

    def test_loss_ends_loop_with_correct_count(self, monkeypatch):
        """sim result=1（失敗）→ 本局結束，回傳完成關數包含失敗關。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        states = iter([rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)

        results = [{"ok": True, "sim": {"result": 1, "precent": 0}}]  # 第 1 關輸

        with patch.dict("sys.modules", {
            "battle_calc.runner": types.SimpleNamespace(run_sim_path=_make_run_sim_path(results)),
            "battle_calc.page_hooks": types.SimpleNamespace(
                install_hooks=lambda _: None,
                clear_combat=lambda *a: None,
                set_block_result=lambda *a: None,
            ),
        }):
            fought = rh.battle_loop(page, mode="local_sim")

        assert fought == 1, f"第 1 關輸應回傳 1，實際 {fought}"

    def test_sim_fail_fallback_to_wait_result(self, monkeypatch):
        """run_sim_path ok=False → fallback 到 _wait_result，不整局炸掉。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        states = iter([rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)

        wait_result_calls = []
        def _fake_wait(p):
            wait_result_calls.append(1)
            return rh.RESULT_LOSE  # 讓 fallback 路徑正常結束

        monkeypatch.setattr(rh, "_wait_result", _fake_wait)

        results = [{"ok": False, "err": "sim timeout"}]  # sim 失敗

        with patch.dict("sys.modules", {
            "battle_calc.runner": types.SimpleNamespace(run_sim_path=_make_run_sim_path(results)),
            "battle_calc.page_hooks": types.SimpleNamespace(
                install_hooks=lambda _: None,
                clear_combat=lambda *a: None,
                set_block_result=lambda *a: None,
            ),
        }):
            fought = rh.battle_loop(page, mode="local_sim")

        assert wait_result_calls, "sim 失敗時應 fallback 到 _wait_result"
        # 不應 raise，應正常回傳整數
        assert isinstance(fought, int)

    def test_sim_fail_block_released(self, monkeypatch):
        """run_sim_path 失敗時，block 必須被釋放（防後續官方 result 被永久吞掉）。

        runner.run_sim_path 的 finally 已處理 set_block_result(False)；
        此測試驗證 battle_loop 自身不會再次 set_block_result(True) 而遮蔽 finally。
        """
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        states = iter([rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)
        monkeypatch.setattr(rh, "_wait_result", lambda _p: rh.RESULT_LOSE)

        set_block_calls = []

        def _fake_run_sim_path(*a, **kw):
            # 模擬 runner 的 finally 行為
            set_block_calls.append(("finally_unblock",))
            return {"ok": False, "err": "test"}

        with patch.dict("sys.modules", {
            "battle_calc.runner": types.SimpleNamespace(run_sim_path=_fake_run_sim_path),
            "battle_calc.page_hooks": types.SimpleNamespace(
                install_hooks=lambda _: None,
                clear_combat=lambda *a: None,
                set_block_result=lambda _p, b: set_block_calls.append(("outer", b)),
            ),
        }):
            rh.battle_loop(page, mode="local_sim")

        # runner finally 已 unblock；outer 不應再 set True 而卡住
        # set_block_calls 包含 ("outer", bool) 和 ("finally_unblock",) 兩種 tuple；
        # 只過濾 outer 的 True 呼叫
        outer_trues = [
            b for t in set_block_calls
            if len(t) == 2 and t[0] == "outer"
            for b in (t[1],) if b is True
        ]
        # 重點：不應在 sim fail 後再次置 True
        assert len(outer_trues) <= 1, (
            f"sim 失敗後不應二次 set_block_result(True)：{set_block_calls}"
        )


class TestBattleLoopAnimationRegression:
    """mode='animation'（預設）行為應與改前完全一致。"""

    def test_animation_mode_uses_wait_result(self, monkeypatch):
        """animation 路徑必須呼叫 _wait_result，不碰 run_sim_path。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        states = iter([rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)

        wait_calls = []
        monkeypatch.setattr(rh, "_wait_result", lambda _p: wait_calls.append(1) or rh.RESULT_LOSE)

        rh.battle_loop(page, mode="animation")  # 預設

        assert wait_calls, "animation 模式應呼叫 _wait_result"

    def test_default_mode_is_animation(self, monkeypatch):
        """無 mode 參數 → 走 animation 路徑（向後兼容）。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)

        states = iter([rh.STAGE, rh.HOME])
        monkeypatch.setattr(rh, "state", lambda _p: next(states, rh.HOME))
        monkeypatch.setattr(rh, "emit", lambda _p, _path: True)

        wait_calls = []
        monkeypatch.setattr(rh, "_wait_result", lambda _p: wait_calls.append(1) or rh.RESULT_LOSE)

        rh.battle_loop(page)  # 不傳 mode

        assert wait_calls, "預設 mode 應走 animation → _wait_result"


class TestRunRoundsModePassthrough:
    """run_rounds 的 mode 參數須正確傳給 battle_loop。"""

    def test_run_rounds_passes_mode_to_battle_loop(self, monkeypatch):
        """run_rounds(page, rounds=1, mode='local_sim') → battle_loop 收到 mode='local_sim'。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)
        monkeypatch.setattr(rh, "_STATE_POLL", 0)

        # 讓 advance_to_stage 立刻成功
        monkeypatch.setattr(rh, "advance_to_stage", lambda _p, shot=None: True)
        # 讓 settle_run 立刻成功
        monkeypatch.setattr(rh, "settle_run", lambda _p, shot=None: True)

        received_modes = []

        def _fake_battle_loop(p, shot=None, mode="animation", ip=""):
            received_modes.append(mode)
            return 3  # 完成 3 關

        monkeypatch.setattr(rh, "battle_loop", _fake_battle_loop)

        completed = rh.run_rounds(page, rounds=1, mode="local_sim")

        assert received_modes == ["local_sim"], f"battle_loop 應收到 local_sim，實際 {received_modes}"
        assert completed == 1

    def test_run_rounds_default_animation(self, monkeypatch):
        """run_rounds 預設 mode='animation'（向後兼容）。"""
        page = _FakePage()
        monkeypatch.setattr(rh, "_PACE", 0)
        monkeypatch.setattr(rh, "advance_to_stage", lambda _p, shot=None: True)
        monkeypatch.setattr(rh, "settle_run", lambda _p, shot=None: True)

        received_modes = []

        def _fake_battle_loop(p, shot=None, mode="animation", ip=""):
            received_modes.append(mode)
            return 0

        monkeypatch.setattr(rh, "battle_loop", _fake_battle_loop)

        rh.run_rounds(page, rounds=1)

        assert received_modes == ["animation"]
