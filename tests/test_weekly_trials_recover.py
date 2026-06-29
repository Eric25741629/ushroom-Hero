"""萬神試煉 (battle.weekly_trials.fight_test) 收尾必須回主頁。

Regression: fight_test 打完 rogue + 秘寶閣後停在非主頁面 (OCR=未知)，導致本輪
後續任務 (雲端戰鬥/好友禮物/開神燈/轉盤金幣) 全部因「不在主頁面」被跳過。
契約：進『副本』後，無論成功或中止，收尾都要呼叫 _recover_to_home 返回主頁。
"""
from __future__ import annotations

import sys
import types

import pytest

# --- Heavy-dep stubs (so importing battle.weekly_trials stays light) ----------
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

# battle/__init__ → battle.manager 需要 easyocr.Reader;別的測試檔可能先以缺 .Reader 的 stub
# 佔住 sys.modules，這裡補上，避免 collection order 造成 import error。
sys.modules["easyocr"].Reader = getattr(sys.modules["easyocr"], "Reader", object)


@pytest.fixture
def wt():
    import importlib
    return importlib.import_module("battle.weekly_trials")


class _FakeImg:
    """Keyword-scripted stand-in for img_tools used by fight_test."""

    def __init__(self, click_map, region_map):
        self._click_map = click_map
        self._region_map = region_map

    @staticmethod
    def _pop(table, kw):
        v = table.get(kw, False)
        return (v.pop(0) if v else False) if isinstance(v, list) else v

    def click_str_by_server(self, d, kw, **_):
        return self._pop(self._click_map, kw)

    def check_str_in_region(self, d, kw, **_):
        return self._pop(self._region_map, kw)


@pytest.fixture
def patched(monkeypatch, wt):
    monkeypatch.setattr(wt.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(wt, "buy_god_everyweek", lambda d: True)
    recovered: list[object] = []
    monkeypatch.setattr(wt, "_recover_to_home", lambda d, **_: recovered.append(d))
    return recovered


def _fake_device():
    return types.SimpleNamespace(swipe=lambda *a, **k: None, click=lambda *a, **k: None)


def test_recovers_to_home_after_successful_run(wt, monkeypatch, patched):
    # 單局完整流程：入場 → 開始挑戰(打一關後敗) → 結束本局/確定 → 回主頁
    d = _fake_device()
    monkeypatch.setattr(wt, "img_tools", _FakeImg(
        click_map={
            "副本": True, "萬神試煉": True,
            "開始挑戰": [True, False], "點擊": True,
            "結束本局": True, "確定": True,   # _settle_run 退出序
        },
        region_map={"開始挑戰": True, "點擊": True, "失敗": True},
    ))
    assert wt.fight_test(d, rounds=1) is True
    assert patched == [d]  # _recover_to_home called exactly once with the device


def test_recovers_to_home_when_entry_not_found(wt, monkeypatch, patched):
    d = _fake_device()
    monkeypatch.setattr(wt, "img_tools", _FakeImg(
        click_map={"副本": True, "萬神試煉": False},  # entry never found → abort
        region_map={},
    ))
    assert wt.fight_test(d, rounds=8) is False
    assert patched == [d]  # still returns home on the abort path
