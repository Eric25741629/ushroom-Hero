"""Gate 放行測試：互檢等待期間使用者按「開啟網頁」必須立即放行。

對應 memory `feedback-open-browser-always-responsive`：
`wait_for_checker_gate_before_start` 的每個阻塞點都要可被 web_launch 中斷。
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

# device_wrapper（被 web_session_service import）在 module 級 import cv2。
sys.modules.setdefault("cv2", types.ModuleType("cv2"))

from runtime_services import web_session_service as svc  # noqa: E402


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _FakeState:
    """可控的 bot_state 替身，計數每個 hook 被呼叫幾次。"""

    def __init__(self, *, results=None, web_launch=False, force_sleep=False):
        self._results = list(results or [])
        self.web_launch = web_launch
        self.force_sleep = force_sleep
        self.on_has_pending = None  # callable(state, call_idx) -> bool
        self.on_force_sleep = None  # callable(state, call_idx) -> bool
        self.submit_calls = 0
        self.update_calls = []
        self.has_pending_calls = 0
        self.force_sleep_calls = 0

    def check_force_sleep(self, ip):
        self.force_sleep_calls += 1
        if self.on_force_sleep is not None:
            return bool(self.on_force_sleep(self, self.force_sleep_calls))
        return self.force_sleep

    def has_pending_web_launch_request(self, ip):
        self.has_pending_calls += 1
        if self.on_has_pending is not None:
            return bool(self.on_has_pending(self, self.has_pending_calls))
        return self.web_launch

    def submit_online_check_request(self, requester_ip, checker_ip=None, target_pid=None):
        self.submit_calls += 1
        return "req-1"

    def wait_online_check_result(self, req_id, timeout_sec=60.0):
        if self._results:
            return self._results.pop(0)
        return {"status": "pending"}

    def update_state(self, ip, task=None, step=None):
        self.update_calls.append((task, step))


def _install(monkeypatch, state, *, interval_sec=1):
    monkeypatch.setattr(svc, "bot_state", state)
    monkeypatch.setattr(
        svc.config_manager,
        "get_device_config",
        lambda _ip: {"online_check_target_pid": 123, "online_check_interval_sec": interval_sec},
    )
    monkeypatch.setattr(svc.time, "sleep", lambda _s: None)


def test_gate_releases_immediately_when_web_launch_pending_at_start(monkeypatch):
    """進 gate 前就有 web launch → 立即放行，連 online-check 都不送。"""
    state = _FakeState(web_launch=True)
    _install(monkeypatch, state)

    svc.wait_for_checker_gate_before_start("emulator-5558", _Logger(), checker_ip="emulator-5554")

    assert state.submit_calls == 0


def test_gate_releases_when_web_launch_arrives_during_backoff(monkeypatch):
    """checker 忙碌進入 backoff，期間 web launch 進來 → 放行（不無限重試）。"""
    state = _FakeState(results=[{"status": "done", "result_busy": True}])
    # call#1 = 外層頂端檢查(False)；call#2 = backoff 首輪(True)
    state.on_has_pending = lambda st, i: i >= 2
    _install(monkeypatch, state, interval_sec=30)

    svc.wait_for_checker_gate_before_start("emulator-5558", _Logger(), checker_ip="emulator-5554")

    assert state.submit_calls == 1
    assert state.has_pending_calls >= 2


def test_gate_releases_when_web_launch_arrives_during_result_wait(monkeypatch):
    """checker 久不回應（result 一直 pending），期間 web launch 進來 → 放行。"""
    state = _FakeState(results=[])  # 永遠 pending
    state.on_has_pending = lambda st, i: i >= 2  # 頂端 False、result-wait 內 True
    _install(monkeypatch, state)

    svc.wait_for_checker_gate_before_start("emulator-5558", _Logger(), checker_ip="emulator-5554")

    assert state.submit_calls == 1


def test_gate_passes_normally_when_checker_free(monkeypatch):
    """回歸：無 web launch 且 checker 空閒 → 正常通過、不卡死。"""
    state = _FakeState(results=[{"status": "done", "result_busy": False}])
    _install(monkeypatch, state)

    svc.wait_for_checker_gate_before_start("emulator-5558", _Logger(), checker_ip="emulator-5554")

    assert state.submit_calls == 1


def test_gate_force_sleep_during_result_wait_propagates(monkeypatch):
    """force-sleep 在 result-wait 內觸發必須照常拋出，不被吞成 busy 重試。"""
    state = _FakeState(results=[])
    state.on_force_sleep = lambda st, i: i >= 2  # 頂端 False、result-wait 內 True
    _install(monkeypatch, state)

    with pytest.raises(svc.ForceSleepRequested):
        svc.wait_for_checker_gate_before_start("emulator-5558", _Logger(), checker_ip="emulator-5554")


def test_initialize_skips_ws_phase_when_web_launch_arrives_during_gate(monkeypatch):
    """gate 期間進來的 web launch 要讓後續 WS pre-phase 被跳過、瀏覽器立即開。"""
    cfg = {
        "backend": "web_h5",
        "web_url": "https://example.invalid",
        "online_check_target_pid": 123,
    }
    order: list[str] = []
    fake_web = SimpleNamespace()
    state = _FakeState(web_launch=False)

    monkeypatch.setattr(svc, "bot_state", state)
    monkeypatch.setattr(state, "is_online_check_checker", lambda _ip: False, raising=False)
    monkeypatch.setattr(svc.config_manager, "get_device_config", lambda _ip: cfg)

    # 模擬：gate 等待期間使用者按了「開啟網頁」。
    def fake_gate(ip, logger_obj, checker_ip="emulator-5554"):
        state.web_launch = True

    monkeypatch.setattr(svc, "wait_for_checker_gate_before_start", fake_gate)
    monkeypatch.setattr(
        svc, "create_web_device_if_enabled", lambda *a, **k: order.append("create_web") or fake_web
    )
    monkeypatch.setattr(svc, "MonitoredDevice", lambda device, ip: SimpleNamespace(device=device, ip=ip))

    def before_web_start():
        order.append("ws_phase")

    d_orig, d, backend_kind, skip_online = svc.initialize_runtime_device(
        "emulator-5558",
        _Logger(),
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ADB must not be used")),
        before_web_device_start=before_web_start,
    )

    assert backend_kind == "web_h5"
    assert skip_online is True
    assert order == ["create_web"]  # WS pre-phase 被跳過


@pytest.mark.parametrize(
    ("backend", "ws_enabled", "initial_skip", "handoff_ok", "expected"),
    [
        ("web_h5", True, False, True, True),
        ("web_h5", True, True, False, False),
        ("web_h5", False, True, False, True),
        ("web_h5", False, False, True, False),
        ("adb", True, True, False, True),
    ],
)
def test_resolve_skip_online_check_once(
    monkeypatch, backend, ws_enabled, initial_skip, handoff_ok, expected
):
    monkeypatch.setattr(
        svc.config_manager,
        "get_device_config",
        lambda _ip: {"ws_token": {"enabled": ws_enabled}},
    )
    monkeypatch.setattr(
        svc.bot_state,
        "get_ws_h5_handoff_ok",
        lambda _ip: handoff_ok,
    )

    assert svc.resolve_skip_online_check_once(
        "dev", backend, initial_skip=initial_skip
    ) is expected


def test_resolve_skip_online_check_once_fails_closed_on_config_error(monkeypatch):
    monkeypatch.setattr(
        svc.config_manager,
        "get_device_config",
        lambda _ip: (_ for _ in ()).throw(RuntimeError("bad config")),
    )

    assert svc.resolve_skip_online_check_once(
        "dev", "web_h5", initial_skip=True
    ) is False
