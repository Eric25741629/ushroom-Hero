"""Tests for `_wait_for_phone_connection` in utils.wake_up_handler.

Regression (掉線判離線 fix): fc65396d/192.168 直連手機斷線時，原本的
screen-check 無限重試迴圈完全不更新 bot_state、也不理 dashboard 控制，
儀表板凍結在「休眠中/喚醒中...」。抽出後的 helper 必須：
- 每輪重試前更新狀態 (task=連線中斷)
- 每輪呼叫 _honor_dashboard_controls（暫停/強制休眠可中斷）
- 連上後回傳可用的 device 物件
"""
from __future__ import annotations

import sys
import time
import types

import pytest

# 與 test_wake_loop_escape.py 相同的輕量 stub 集。
for stub_name in ("opencc", "paddleocr", "img_tools"):
    if stub_name not in sys.modules:
        stub = types.ModuleType(stub_name)
        if stub_name == "opencc":
            stub.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[stub_name] = stub

if "uiautomator2" not in sys.modules:
    sys.modules["uiautomator2"] = types.ModuleType("uiautomator2")

for name in ("adb_operations", "device", "game_initialization", "config_manager"):
    if name not in sys.modules:
        m = types.ModuleType(name)
        if name == "adb_operations":
            m.connect_u2_with_retries = lambda *a, **k: None
            m.get_battery_level = lambda *a, **k: 100
            # superset：後續收集的 control_panel_app / worker_webhook_api
            # 也會 import 這些，缺了會交叉汙染其他測試檔。
            m.run_adb = lambda *a, **k: ""
            m.reset_screen_settings = lambda *a, **k: None
            m.set_screen_for_game = lambda *a, **k: None
        if name == "device":
            m.close_notification = lambda *a, **k: None
        if name == "game_initialization":
            m.check_on_line = lambda *a, **k: False
        if name == "config_manager":
            m.get_device_config = lambda ip: {}
            m.get_global_config = lambda: {}
        sys.modules[name] = m


class _FakeBotState:
    def __init__(self):
        self.updates: list[dict] = []

    def update_state(self, ip, **kw):
        self.updates.append({"ip": ip, **kw})

    def check_pause(self, ip):
        return None

    def check_force_sleep(self, ip):
        return False

    def has_pending_web_launch_request(self, ip):
        return False


class _FlakyDevice:
    """`.info` raises N times, then behaves like a connected device."""

    def __init__(self, fail_times: int):
        self._fails_left = fail_times

    @property
    def info(self):
        if self._fails_left > 0:
            self._fails_left -= 1
            raise RuntimeError("device offline")
        return {"screenOn": False}


@pytest.fixture
def fresh_handler(monkeypatch):
    fake_state = _FakeBotState()
    # teardown 必須「還原」原模組物件而非 pop：pop 會讓之後才 lazy import 的
    # 模組（如 device_wrapper）拿到重新 import 的新 bot_state，與其他測試檔
    # 既有的 bot_state 參照分裂（跨檔收集汙染）。
    orig_bot_state = sys.modules.get("bot_state")
    sys.modules.pop("utils.wake_up_handler", None)
    sys.modules["bot_state"] = fake_state  # type: ignore[assignment]
    import utils.wake_up_handler as wuh

    assert wuh.bot_state is fake_state
    monkeypatch.setattr(time, "sleep", lambda s: None)
    yield wuh, fake_state
    sys.modules.pop("utils.wake_up_handler", None)
    if orig_bot_state is not None:
        sys.modules["bot_state"] = orig_bot_state
    else:
        sys.modules.pop("bot_state", None)


def test_returns_device_immediately_when_connected(fresh_handler):
    wuh, state = fresh_handler
    d = _FlakyDevice(fail_times=0)
    result = wuh._wait_for_phone_connection(d, "fc65396d-test", wuh.logger)
    assert result is d
    assert state.updates == []  # 沒斷線就不該寫「連線中斷」


def test_retries_update_dashboard_state(fresh_handler):
    wuh, state = fresh_handler
    d = _FlakyDevice(fail_times=2)
    # 重連失敗（回 None）也要繼續用原 d 重試
    wuh.connect_u2_with_retries = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no route"))
    result = wuh._wait_for_phone_connection(d, "fc65396d-test", wuh.logger)
    assert result is d
    tasks = {u.get("task") for u in state.updates}
    assert "連線中斷" in tasks
    assert len(state.updates) == 2  # 每輪重試各一筆


def test_reconnect_returns_new_device(fresh_handler):
    wuh, state = fresh_handler
    dead = _FlakyDevice(fail_times=10**9)
    good = _FlakyDevice(fail_times=0)
    wuh.connect_u2_with_retries = lambda ip, logger=None: good
    result = wuh._wait_for_phone_connection(dead, "fc65396d-test", wuh.logger)
    assert result is good


# --- Task A: 手機離線降級為純 WS（bounded wait）---------------------------


def _install_fake_clock(wuh, monkeypatch):
    """Replace wuh.time.time with a controllable monotonic counter that the
    no-op `time.sleep` advances, so a bounded wait can time out without
    real wall-clock blocking."""
    clock = {"now": 0.0}

    def fake_time():
        return clock["now"]

    def fake_sleep(sec):
        clock["now"] += float(sec)

    monkeypatch.setattr(wuh.time, "time", fake_time)
    monkeypatch.setattr(wuh.time, "sleep", fake_sleep)
    return clock


def test_default_max_wait_keeps_retrying_until_connected(fresh_handler, monkeypatch):
    # max_wait_sec=None（預設）→ 無限重試直到接上，不因 bounded 邏輯誤放棄。
    wuh, state = fresh_handler
    _install_fake_clock(wuh, monkeypatch)
    d = _FlakyDevice(fail_times=5)
    wuh.connect_u2_with_retries = lambda *a, **k: None  # 重連回 None → 沿用原 d
    result = wuh._wait_for_phone_connection(d, "fc65396d-test", wuh.logger)
    assert result is d
    assert len(state.updates) == 5  # 失敗 5 次各寫一筆「連線中斷」


def test_bounded_wait_raises_phone_unreachable_when_always_down(fresh_handler, monkeypatch):
    from runtime_services.device_runtime_service import PhoneUnreachableError

    wuh, state = fresh_handler
    _install_fake_clock(wuh, monkeypatch)
    dead = _FlakyDevice(fail_times=10**9)
    wuh.connect_u2_with_retries = lambda *a, **k: None
    with pytest.raises(PhoneUnreachableError):
        wuh._wait_for_phone_connection(dead, "fc65396d-test", wuh.logger, max_wait_sec=180)
    # 放棄前最後一筆狀態應說明已放棄（降級純 WS）。
    assert state.updates, "should have written at least one state update"
    last_step = str(state.updates[-1].get("step", ""))
    assert "放棄" in last_step or "WS" in last_step


def test_bounded_wait_self_heals_before_deadline(fresh_handler, monkeypatch):
    # 寬鬆 deadline 下失敗兩次後接上 → 仍回傳裝置（bounded 不破壞自癒）。
    wuh, state = fresh_handler
    _install_fake_clock(wuh, monkeypatch)
    d = _FlakyDevice(fail_times=2)
    wuh.connect_u2_with_retries = lambda *a, **k: None
    result = wuh._wait_for_phone_connection(d, "fc65396d-test", wuh.logger, max_wait_sec=10**6)
    assert result is d
    assert len(state.updates) == 2


def test_phone_connect_max_wait_constant_is_positive_int(fresh_handler):
    wuh, _state = fresh_handler
    assert isinstance(wuh.PHONE_CONNECT_MAX_WAIT_SEC, int)
    assert wuh.PHONE_CONNECT_MAX_WAIT_SEC > 0
