"""_battle_loop 戰鬥中『跳過』快轉 + 結果窗逾時去同步恢復 (2026-07-11 手機 0/8 根因修復)。

真機戰鬥 >1min，舊碼每關只等 18s 就誤判本局結束、對戰鬥畫面點紅箭頭 → 全任務中止。
新行為：等結果窗期間見『跳過』就點；找不到『開始挑戰』但仍在戰鬥中 → 跳過續等而非 break。
"""
from __future__ import annotations

import sys
import types

import pytest

for _name in ("opencc", "paddleocr", "easyocr", "img_tools", "cv2", "tools"):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        if _name == "opencc":
            _m.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        if _name == "tools":
            _m.click_white = lambda *a, **kw: None
        if _name == "easyocr":
            _m.Reader = object
        sys.modules[_name] = _m

if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

sys.modules["easyocr"].Reader = getattr(sys.modules["easyocr"], "Reader", object)


@pytest.fixture
def wt(monkeypatch):
    import importlib
    mod = importlib.import_module("battle.weekly_trials")
    fake_now = [0.0]
    monkeypatch.setattr(mod.time, "sleep", lambda s: fake_now.__setitem__(0, fake_now[0] + s))
    monkeypatch.setattr(mod.time, "monotonic", lambda: fake_now[0])
    return mod


class _Script:
    """click_str_by_server / check_str_in_region 依關鍵字回放預錄序列。"""

    def __init__(self, clicks: dict[str, list[bool]], checks: dict[str, list[bool]]):
        self.clicks, self.checks = clicks, checks
        self.clicked: list[str] = []

    def click_str_by_server(self, d, kw, **_):
        seq = self.clicks.get(kw)
        ok = seq.pop(0) if seq else False
        if ok:
            self.clicked.append(kw)
        return ok

    def check_str_in_region(self, d, kw, **_):
        seq = self.checks.get(kw)
        return seq.pop(0) if seq else False


def test_still_in_battle_clicks_skip_instead_of_break(wt, monkeypatch):
    # 開始挑戰 一直找不到(還在戰鬥畫面)；第一輪見跳過 → 快轉；之後全空 → break
    s = _Script(
        clicks={"開始挑戰": [False, False], "跳過": [True]},
        checks={"跳過": [True, False]},
    )
    monkeypatch.setattr(wt, "img_tools", s)
    fought = wt._battle_loop(object(), max_stages=5)
    assert fought == 0
    assert "跳過" in s.clicked  # 有快轉而不是直接 break


def test_result_wait_clicks_skip_until_result_window(wt, monkeypatch):
    # 第 1 關：等結果窗前兩輪還在戰鬥(跳過 x2) → 第三輪出結果窗(點擊) → 關窗;之後本局結束
    s = _Script(
        clicks={"開始挑戰": [True, False], "跳過": [True, True], "點擊": [True]},
        checks={"點擊": [False, False, True], "跳過": [True, True]},
    )
    monkeypatch.setattr(wt, "img_tools", s)
    fought = wt._battle_loop(object(), max_stages=5)
    assert fought == 1
    assert s.clicked.count("跳過") == 2


def test_settle_pre_guard_dismisses_leftover_battle(wt, monkeypatch):
    # _settle_run 前殘留戰鬥畫面 → 先跳過+關結果窗，再點紅箭頭開出結束本局
    s = _Script(
        clicks={"跳過": [True], "點擊": [True], "確定": [True]},
        checks={"跳過": [True, False], "點擊": [True, False], "結束本局": [True]},
    )
    monkeypatch.setattr(wt, "img_tools", s)
    monkeypatch.setattr(wt, "_at_rogue_home", lambda d: True)
    clicked_xy: list[tuple] = []
    d = types.SimpleNamespace(click=lambda *a, **k: clicked_xy.append(a))
    assert wt._settle_run(d) is True
    assert s.clicked[:2] == ["跳過", "點擊"]  # 先收殘留窗
    assert wt._EXIT_ARROW_XY in clicked_xy   # 才點紅箭頭
