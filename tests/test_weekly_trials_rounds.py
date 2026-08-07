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

import asyncio
import sys
import threading
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


def test_pure_ws_runs_on_thread_without_callers_running_loop(wt, monkeypatch):
    """web_h5 的 sync Playwright loop 不可污染 pure-WS 的 B 瀏覽器。"""
    caller_thread = threading.get_ident()
    worker_state = {}
    expected = object()

    def fake_sync(*_args, **_kwargs):
        worker_state["thread"] = threading.get_ident()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            worker_state["has_running_loop"] = False
        else:
            worker_state["has_running_loop"] = True
        return expected

    monkeypatch.setattr(wt, "_run_pure_ws_wanshen_sync", fake_sync, raising=False)
    stuck_loop = asyncio.new_event_loop()
    asyncio.events._set_running_loop(stuck_loop)
    try:
        result = wt._run_pure_ws_wanshen(object(), "web-001", 10, {})
    finally:
        asyncio.events._set_running_loop(None)
        stuck_loop.close()

    assert result is expected
    assert worker_state["has_running_loop"] is False
    assert worker_state["thread"] != caller_thread


def test_pure_ws_worker_forwards_until_cap(wt, monkeypatch):
    captured = {}

    def fake_sync(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(wt, "_run_pure_ws_wanshen_sync", fake_sync, raising=False)

    wt._run_pure_ws_wanshen(
        object(), "web-001", 10, {"web_debug_port": 9223}, until_cap=True
    )

    assert captured["args"] == ("web-001", 10, {"web_debug_port": 9223})
    assert captured["kwargs"] == {"until_cap": True}


def test_local_sim_loss_sync_error_preserves_page_without_followup_actions(
    wt, monkeypatch, harness
):
    """失敗 UI 未同步時不可買秘寶閣、回主頁或強制導航。"""
    import config_manager
    from battle import rogue_h5

    page = object()
    dev = types.SimpleNamespace(
        backend_kind="web_h5",
        device_id="web-test",
        _page=page,
    )
    bought = []
    monkeypatch.setattr(wt, "buy_god_everyweek", lambda _d: bought.append(True))
    monkeypatch.setattr(rogue_h5, "open_home", lambda _page: True)
    monkeypatch.setattr(
        rogue_h5,
        "run_rounds",
        lambda *_a, **_k: (_ for _ in ()).throw(
            rogue_h5.LossResultSyncError("loss UI timeout")
        ),
    )
    monkeypatch.setattr(
        config_manager,
        "get_device_config",
        lambda _ip: {"wanshen_battle_mode": "local_sim"},
    )

    assert wt.fight_test(dev, rounds=1) is False
    assert bought == []
    assert harness.recovered == []


# ---- _settle_run fail-safe (2026-06-30「沒有正常退出」回歸防護) -------------------

class _RecDevice:
    def __init__(self):
        self.clicks = []

    def click(self, x, y, *a, **k):
        self.clicks.append((x, y))
        return True

    def swipe(self, *a, **k):
        return True


def _stateful_settle(monkeypatch, wt, region_fn, click_set):
    monkeypatch.setattr(wt.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(wt, "img_tools", types.SimpleNamespace(
        check_str_in_region=lambda d, kw, **_: region_fn(kw),
        click_str_by_server=lambda d, kw, **_: kw in click_set,
    ))


def test_settle_true_when_back_home(wt, monkeypatch):
    # 結束本局(按鈕座標)→確定→無覆蓋層+神樹祝福 → True;點過 紅箭頭+結束本局按鈕,無盲點報告 ✕
    calls = {"end": 0}
    def region(kw):
        if kw == "結束本局":   # gate 第一次 True(對話框在),之後 False(已點掉)
            calls["end"] += 1
            return calls["end"] == 1
        return kw in ("神樹祝福", "結算倒計時")  # 主面板字常駐;覆蓋層字皆 False
    _stateful_settle(monkeypatch, wt, region, {"確定"})
    dev = _RecDevice()
    assert wt._settle_run(dev) is True
    assert dev.clicks == [wt._EXIT_ARROW_XY, wt._END_RUN_BTN_XY]  # 結束本局走按鈕座標(非 OCR)
    assert wt._REPORT_CLOSE_XY not in dev.clicks


def test_settle_false_and_no_blind_taps_when_never_home(wt, monkeypatch):
    # 一直停在關卡視圖(開始挑戰 覆蓋層字常在) → 回 False，且**絕不**盲點報告 ✕(舊 bug 根因)
    def region(kw):
        return kw in ("結束本局", "開始挑戰")  # 對話框在(gate 過)、開始挑戰=永遠覆蓋層
    _stateful_settle(monkeypatch, wt, region, {"確定"})
    dev = _RecDevice()
    assert wt._settle_run(dev) is False
    assert dev.clicks == [wt._EXIT_ARROW_XY, wt._END_RUN_BTN_XY]  # 紅箭頭+結束本局;未盲點 ✕
    assert wt._REPORT_CLOSE_XY not in dev.clicks


def test_settle_false_when_no_end_dialog(wt, monkeypatch):
    # 紅箭頭沒開出『結束本局』對話框 → 直接 False(不在可結算狀態),連按鈕都不點
    _stateful_settle(monkeypatch, wt, lambda kw: False, set())
    dev = _RecDevice()
    assert wt._settle_run(dev) is False
    assert dev.clicks == [wt._EXIT_ARROW_XY]


def test_settle_home_false_positive_guard_report_overlay(wt, monkeypatch):
    # 報告蓋著但主面板『神樹祝福』透出 → 不可判 home;偵測『抵達關卡』點 ✕ 關報告後才回主面板。
    phase = {"n": 0}  # 0=對話框 1=報告(神樹祝福透出) 2=乾淨主面板
    def region(kw):
        if kw == "結束本局":
            return phase["n"] == 0
        if kw == "抵達關卡":
            return phase["n"] == 1          # 報告覆蓋層字
        if kw == "神樹祝福":
            return phase["n"] >= 1          # 透出:報告階段就看得到(假陽性誘餌)
        if kw == "結算倒計時":
            return phase["n"] >= 2
        return False
    _stateful_settle(monkeypatch, wt, region, {"確定"})
    dev = _RecDevice()
    orig = dev.click
    def _click(x, y, *a, **k):
        if (x, y) == wt._END_RUN_BTN_XY and phase["n"] == 0:
            phase["n"] = 1
        elif (x, y) == wt._REPORT_CLOSE_XY and phase["n"] == 1:
            phase["n"] = 2
        return orig(x, y, *a, **k)
    dev.click = _click
    assert wt._settle_run(dev) is True
    # 報告階段(神樹祝福透出)沒被誤判 home → 有去點報告 ✕
    assert wt._REPORT_CLOSE_XY in dev.clicks
    assert dev.clicks == [wt._EXIT_ARROW_XY, wt._END_RUN_BTN_XY, wt._REPORT_CLOSE_XY]
