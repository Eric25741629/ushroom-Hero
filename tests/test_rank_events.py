"""Tests for rank_events.park_spring (坐騎衝刺 / 衝刺-發條) rewrite.

Written BEFORE the rewrite (TDD). Covers:
  - scheduling gate (weekday / cycle / already-ran / settlement cutoff / config)
  - the feed flow (OCR-verified steps, success vs abort)
  - quantity input (web_h5 cocos EditBox vs ADB `input text`)

Heavy deps (img_tools / uiautomator2 / cv2 / opencc) are stubbed so the
import stays light and OCR is fully controllable.
"""
from __future__ import annotations

import datetime
import sys
import types

import pytest

# --- Heavy-dep stubs (must run before importing rank_events) ------------------
for _name in ("opencc", "paddleocr", "easyocr", "cv2"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["opencc"].OpenCC = lambda *a, **k: types.SimpleNamespace(convert=lambda s: s)

# img_tools stub with a controllable OCR queue.
_img = types.ModuleType("img_tools")
_img._queue = []  # list[list[str]] consumed FIFO by get_all_text


def _get_all_text(img, *a, **k):
    return _img._queue.pop(0) if _img._queue else []


_img.get_all_text = _get_all_text
sys.modules["img_tools"] = _img

if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

if "bot_state" not in sys.modules:
    _bs = types.ModuleType("bot_state")
    _bs.update_state = lambda *a, **k: None
    sys.modules["bot_state"] = _bs

import rank_events  # noqa: E402

_TPE = datetime.timezone(datetime.timedelta(hours=8))


# --- Fakes --------------------------------------------------------------------
class FakeDevice:
    """Records clicks/shell calls. `page` set => web_h5 branch."""

    def __init__(self, page=None):
        self.clicks = []
        self.shells = []
        self._page = page

    def click(self, x, y, *a, **k):
        self.clicks.append((x, y))

    def screenshot(self, *a, **k):
        return "IMG"

    def shell(self, cmd, *a, **k):
        self.shells.append(cmd)


class FakePage:
    def __init__(self, eval_result="ok:7000"):
        self.eval_result = eval_result
        self.evals = []

    def evaluate(self, js, arg=None):
        self.evals.append((js, arg))
        return self.eval_result

    def screenshot(self, *a, **k):
        return None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(rank_events.time, "sleep", lambda *a, **k: None)
    _img._queue.clear()


@pytest.fixture
def sched(monkeypatch):
    """Default: cycle due, no prior record this week, device enabled qty=7000."""
    calls = {"recorded": []}
    monkeypatch.setattr(rank_events.json_manager, "return_time", lambda ip, name: None)
    monkeypatch.setattr(
        rank_events.json_manager, "should_execute_cycle",
        lambda ip, name, **kw: (True, True),
    )
    monkeypatch.setattr(
        rank_events.json_manager, "time_recording",
        lambda ip, name: calls["recorded"].append(name),
    )
    monkeypatch.setattr(
        rank_events.config_manager, "get_device_config",
        lambda ip: {"enable_mount_sprint": True, "mount_sprint_quantity": 7000, "backend": "adb"},
    )
    return calls


# --- Scheduling gate ----------------------------------------------------------
def test_skips_when_not_tue_to_thu(sched, monkeypatch):
    feeds = []
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: feeds.append(1) or True)
    monday = datetime.datetime(2026, 6, 1, 10, 0, tzinfo=_TPE)  # weekday 0
    rank_events.park_spring(FakeDevice(), "dev", now=monday)
    assert feeds == []
    assert sched["recorded"] == []


def test_ui_fallback_never_feeds_from_local_cycle(sched, monkeypatch):
    """同服活動未經 WS 型別確認時，UI fallback 不能用本機週期盲餵。"""
    feeds = []
    monkeypatch.setattr(
        rank_events,
        "_run_feed_flow",
        lambda *a, **k: feeds.append(1) or True,
    )
    tue = datetime.datetime(2026, 6, 2, 10, 0, tzinfo=_TPE)

    rank_events.park_spring(FakeDevice(), "dev", now=tue)

    assert feeds == []
    assert sched["recorded"] == []

def test_skips_wednesday_after_settlement(sched, monkeypatch):
    feeds = []
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: feeds.append(1) or True)
    wed_late = datetime.datetime(2026, 6, 3, 22, 30, tzinfo=_TPE)  # weekday 2, >=22:00
    rank_events.park_spring(FakeDevice(), "dev", now=wed_late)
    assert feeds == []


def test_skips_thursday_entirely(sched, monkeypatch):
    feeds = []
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: feeds.append(1) or True)
    thu = datetime.datetime(2026, 6, 4, 10, 0, tzinfo=_TPE)  # weekday 3 no longer allowed
    rank_events.park_spring(FakeDevice(), "dev", now=thu)
    assert feeds == []


def test_skips_when_already_ran_this_week(sched, monkeypatch):
    feeds = []
    monkeypatch.setattr(rank_events.json_manager, "return_time",
                        lambda ip, name: {"is_next_week": False})
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: feeds.append(1) or True)
    tue = datetime.datetime(2026, 6, 2, 10, 0, tzinfo=_TPE)
    rank_events.park_spring(FakeDevice(), "dev", now=tue)
    assert feeds == []


def test_skips_when_cycle_not_due(sched, monkeypatch):
    feeds = []
    monkeypatch.setattr(rank_events.json_manager, "should_execute_cycle",
                        lambda ip, name, **kw: (False, False))
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: feeds.append(1) or True)
    tue = datetime.datetime(2026, 6, 2, 10, 0, tzinfo=_TPE)
    rank_events.park_spring(FakeDevice(), "dev", now=tue)
    assert feeds == []


def test_skips_when_disabled_in_config(sched, monkeypatch):
    feeds = []
    monkeypatch.setattr(rank_events.config_manager, "get_device_config",
                        lambda ip: {"enable_mount_sprint": False, "backend": "adb"})
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: feeds.append(1) or True)
    tue = datetime.datetime(2026, 6, 2, 10, 0, tzinfo=_TPE)
    rank_events.park_spring(FakeDevice(), "dev", now=tue)
    assert feeds == []


def test_no_record_on_feed_failure(sched, monkeypatch):
    monkeypatch.setattr(rank_events, "_run_feed_flow", lambda *a, **k: False)
    tue = datetime.datetime(2026, 6, 2, 10, 0, tzinfo=_TPE)
    rank_events.park_spring(FakeDevice(), "dev", now=tue)
    assert sched["recorded"] == []


def test_ui_fallback_ignores_configured_quantity(sched, monkeypatch):
    """即使本機設定了數量，也不能繞過伺服器活動確認。"""
    seen = {}
    monkeypatch.setattr(
        rank_events.config_manager,
        "get_device_config",
        lambda ip: {
            "enable_mount_sprint": True,
            "mount_sprint_quantity": 1234,
            "backend": "adb",
        },
    )
    monkeypatch.setattr(
        rank_events,
        "_run_feed_flow",
        lambda d, ip, qty: seen.setdefault("qty", qty) or True,
    )
    tue = datetime.datetime(2026, 6, 2, 10, 0, tzinfo=_TPE)

    rank_events.park_spring(FakeDevice(), "dev", now=tue)

    assert seen == {}
    assert sched["recorded"] == []

# --- Feed flow ----------------------------------------------------------------
def _stage_success_ocr():
    """Queue the OCR results for a fully successful ADB feed flow."""
    _img._queue.extend([
        ["坐騎升級", "青羽", "9階"],                 # after mount entry
        ["升一級", "一鍵餵養", "祝福值"],            # after upgrade tab
        ["無限時發條", "使用"],                      # after feed btn
        ["是否消耗無限時發條*7000", "確定", "取消"],  # after 使用
        ["坐騎升級", "青羽", "一鍵餵養"],            # after 確定 (no 是否消耗)
    ])


def test_feed_flow_success_taps_all_steps():
    _stage_success_ocr()
    d = FakeDevice()  # adb (no page)
    assert rank_events._run_feed_flow(d, "dev", 7000) is True
    # all key coordinates were tapped
    for coord in (rank_events.MOUNT_ENTRY, rank_events.UPGRADE_TAB,
                  rank_events.FEED_BTN, rank_events.USE_BTN,
                  rank_events.CONFIRM_OK, rank_events.CLOSE_BTN):
        assert coord in d.clicks
    # adb quantity input issued
    assert any("input text 7000" in s for s in d.shells)
    assert any("input keyevent 66" in s for s in d.shells)


def test_feed_flow_aborts_when_horse_view_missing():
    _img._queue.append(["主頁面", "角色", "家園"])  # not the horse view
    d = FakeDevice()
    assert rank_events._run_feed_flow(d, "dev", 7000) is False
    # only the entry tap happened; never reached the feed button
    assert rank_events.FEED_BTN not in d.clicks


def test_feed_flow_aborts_when_confirm_missing_after_use():
    _img._queue.extend([
        ["坐騎升級", "青羽"],
        ["升一級", "一鍵餵養"],
        ["無限時發條", "使用"],
        ["坐騎升級", "青羽"],   # after 使用: NO 消耗/確定 -> abort
    ])
    d = FakeDevice()
    assert rank_events._run_feed_flow(d, "dev", 7000) is False
    assert rank_events.CONFIRM_OK not in d.clicks


# --- Quantity input -----------------------------------------------------------
def test_set_quantity_adb_uses_input_text_and_enter():
    d = FakeDevice()  # no page -> adb
    assert rank_events._set_quantity(d, "dev", 7000) is True
    assert rank_events.QTY_EDITBOX in d.clicks
    assert any("input text 7000" in s for s in d.shells)
    assert any("input keyevent 66" in s for s in d.shells)  # ENTER commit


def test_set_quantity_web_uses_cocos_editbox():
    page = FakePage(eval_result="ok:7000")
    d = FakeDevice(page=page)
    assert rank_events._set_quantity(d, "dev", 7000) is True
    assert page.evals and page.evals[0][1] == "7000"
    assert not d.shells  # web must NOT use adb shell


def test_set_quantity_web_returns_false_on_js_error():
    page = FakePage(eval_result="err:no EditBox")
    d = FakeDevice(page=page)
    assert rank_events._set_quantity(d, "dev", 7000) is False
