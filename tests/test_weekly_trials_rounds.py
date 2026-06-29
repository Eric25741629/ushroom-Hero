"""萬神試煉 8-局迴圈 orchestration (battle.weekly_trials.fight_test rounds loop).

2026-06-29 重寫：rogue 一次失敗只扣 1 試煉之心、可續打、❤=0 才真結束。使用者定案
「一局 = 開始→爬到第一次失敗→結束本局(退出)→重進」，跑滿 N 局(預設 8、可調)才算完成。

這些測試 mock 子函式(_advance_to_stage / _battle_loop / _settle_run)只驗證迴圈編排：
  - 跑滿 rounds 局才回 True
  - 任一局進不去 / 結算退出失敗 → 停止、回 False(不寫週記錄、下次重試)
  - 單局內「失敗」(由 _battle_loop 內部處理)不會中止整個任務 → 仍 settle + 進下一局
  - _recover_to_home 收尾恰一次
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

# battle/__init__ → battle.manager 需要 easyocr.Reader;若別的測試檔先以缺 .Reader 的 stub
# 佔住 sys.modules，這裡補上，避免 collection order 造成 import error。
sys.modules["easyocr"].Reader = getattr(sys.modules["easyocr"], "Reader", object)


@pytest.fixture
def wt():
    import importlib
    return importlib.import_module("battle.weekly_trials")


@pytest.fixture
def harness(monkeypatch, wt):
    """Mock the 3 per-round sub-functions + IO; record call order."""
    calls: list[str] = []
    monkeypatch.setattr(wt.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(wt, "buy_god_everyweek", lambda d: True)
    recovered: list[object] = []
    monkeypatch.setattr(wt, "_recover_to_home", lambda d, **_: recovered.append(d))
    # default img_tools: 副本 / 萬神試煉 入場 succeed
    monkeypatch.setattr(wt, "img_tools", types.SimpleNamespace(
        click_str_by_server=lambda d, kw, **_: kw in ("副本", "萬神試煉"),
        check_str_in_region=lambda d, kw, **_: False,
    ))
    return types.SimpleNamespace(calls=calls, recovered=recovered)


def _fake_device():
    return types.SimpleNamespace(swipe=lambda *a, **k: None, click=lambda *a, **k: None)


def _wire(monkeypatch, wt, harness, *, advance, battle, settle):
    monkeypatch.setattr(wt, "_advance_to_stage", lambda d: (harness.calls.append("advance"), advance.pop(0) if advance else True)[1])
    monkeypatch.setattr(wt, "_battle_loop", lambda d, **_: (harness.calls.append("battle"), battle.pop(0) if battle else 1)[1])
    monkeypatch.setattr(wt, "_settle_run", lambda d: (harness.calls.append("settle"), settle.pop(0) if settle else True)[1])


def test_runs_all_rounds_and_returns_true(wt, monkeypatch, harness):
    _wire(monkeypatch, wt, harness, advance=[True, True, True], battle=[2, 5, 0], settle=[True, True, True])
    assert wt.fight_test(_fake_device(), rounds=3) is True
    # 3 局，每局 advance→battle→settle
    assert harness.calls == ["advance", "battle", "settle"] * 3
    assert len(harness.recovered) == 1


def test_loss_in_a_round_does_not_abort_task(wt, monkeypatch, harness):
    # _battle_loop 回傳關卡數(含失敗結束的局)；回 0 代表第一關就敗，仍要 settle + 進下一局
    _wire(monkeypatch, wt, harness, advance=[True, True], battle=[0, 0], settle=[True, True])
    assert wt.fight_test(_fake_device(), rounds=2) is True
    assert harness.calls.count("settle") == 2  # 兩局都 settle 了，沒有提早 break


def test_stops_and_false_when_a_round_cannot_enter(wt, monkeypatch, harness):
    _wire(monkeypatch, wt, harness, advance=[True, False], battle=[3], settle=[True])
    assert wt.fight_test(_fake_device(), rounds=3) is False
    # 第2局 advance 失敗 → 不再續；只 settle 過一次(第1局)
    assert harness.calls == ["advance", "battle", "settle", "advance"]
    assert len(harness.recovered) == 1


def test_stops_and_false_when_settle_fails(wt, monkeypatch, harness):
    _wire(monkeypatch, wt, harness, advance=[True, True], battle=[3, 3], settle=[False])
    assert wt.fight_test(_fake_device(), rounds=3) is False
    assert harness.calls == ["advance", "battle", "settle"]  # settle 失敗即停


def test_false_when_entry_not_found(wt, monkeypatch, harness):
    monkeypatch.setattr(wt, "img_tools", types.SimpleNamespace(
        click_str_by_server=lambda d, kw, **_: kw == "副本",  # 萬神試煉 入口找不到
        check_str_in_region=lambda d, kw, **_: False,
    ))
    _wire(monkeypatch, wt, harness, advance=[], battle=[], settle=[])
    assert wt.fight_test(_fake_device(), rounds=8) is False
    assert harness.calls == []  # 沒進到任何一局
    assert len(harness.recovered) == 1


def test_default_rounds_is_eight(wt, monkeypatch, harness):
    _wire(monkeypatch, wt, harness, advance=[True] * 8, battle=[1] * 8, settle=[True] * 8)
    assert wt.fight_test(_fake_device()) is True  # 不帶 rounds → 預設 8
    assert harness.calls.count("advance") == 8
