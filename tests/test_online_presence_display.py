"""在線檢測顯示精確化 + 互檢 fast-path threshold（2026-07-05 使用者回報三症狀）。

1. dashboard 徽章（routes_status._account_presence）改用 last_login_ts==0 精確判定：
   monitor 快照烘入的 online bool 帶 120s 寬限（poll_friends threshold_sec=120），
   造成登出後最多 ~2.5 分鐘仍顯示在線。顯示層不該吃 guard 的保守寬限。
2. detector overlay：被 monitor 借用的裝置永遠不在自己好友列表 → 徽章不顯示在線，
   但 monitor 正持有它的活 WS session，應 overlay 為在線。
3. 互檢 fast path（online_check_service._check_monitor_snapshot）應用 requester 自己的
   online_check_threshold_sec 從原始 ts 重算，而不是回快照烘入的 120s bool。

harness 同 tests/test_online_check_service.py（真 bot_state mailbox）+
tests/test_config_role_id_cache.py（stub 重模組後 import routes_status）。
"""
from __future__ import annotations

import sys
import time
import types

import pytest

import bot_state
import config_manager


def _install_lightweight_stubs():
    # 同 test_config_role_id_cache.py：routes_status 頂層 import 的重模組換輕 stub。
    for name, attrs in (
        ("adb_operations", {"run_adb": lambda *a, **k: ""}),
        ("game_state.detector", {"stage_by_str": lambda d, ocr, img: "unknown"}),
        ("new_cnn.cnn_model", {"load_cnn_model": lambda path: None}),
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            sys.modules[name] = mod


_install_lightweight_stubs()

from control_panel import routes_status as rs  # noqa: E402
from runtime_services import online_check_service as svc  # noqa: E402
from ws_token import online_monitor  # noqa: E402
from ws_token.online_monitor import Snapshot, StatusEntry  # noqa: E402


def _snap(*entries, detector="det-dev", ts=None):
    return Snapshot(detector=detector,
                    timestamp=time.time() if ts is None else ts,
                    entries=tuple(entries))


def _entry(rid, online, last_login_ts):
    return StatusEntry(role_id=rid, name=f"p{rid}", online=online,
                       last_login_ts=last_login_ts)


# ---------------------------------------------------------------------------
# 1+2. dashboard 徽章：ts==0 精確判定 + detector overlay
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_role_id_cache():
    rs._device_role_id.cache_clear()
    yield
    rs._device_role_id.cache_clear()


def test_presence_online_only_when_sentinel_zero(monkeypatch):
    # ts==0 → 在線；ts=90 秒前（快照烘入 online=True 的 120s 寬限案例）→ 顯示離線。
    now = int(time.time())
    snap = _snap(_entry(111, True, 0), _entry(222, True, now - 90))
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: snap)
    monkeypatch.setattr(online_monitor, "current_detector", lambda: None)

    presence = rs._account_presence()
    assert presence[111] is True
    assert presence[222] is False   # 已登出 90s：徽章不該還寫在線


def test_presence_overlays_active_detector_as_online(monkeypatch):
    snap = _snap(_entry(111, False, 12345), detector="det-dev")
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: snap)
    monkeypatch.setattr(online_monitor, "current_detector", lambda: "det-dev")
    monkeypatch.setattr(config_manager, "get_device_role_id",
                        lambda dev: 999 if dev == "det-dev" else None)

    presence = rs._account_presence()
    assert presence[999] is True    # monitor 持有其 WS session → 在線


def test_presence_no_overlay_when_monitor_disconnected(monkeypatch):
    snap = _snap(_entry(111, False, 12345), detector="det-dev")
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: snap)
    monkeypatch.setattr(online_monitor, "current_detector", lambda: None)
    monkeypatch.setattr(config_manager, "get_device_role_id",
                        lambda dev: 999)

    presence = rs._account_presence()
    assert 999 not in presence      # 斷線時不 overlay


def test_presence_empty_without_snapshot(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: None)
    monkeypatch.setattr(online_monitor, "current_detector", lambda: "det-dev")
    assert rs._account_presence() == {}


# ---------------------------------------------------------------------------
# 3. 互檢 fast path：用 requester threshold 從原始 ts 重算
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_mailbox():
    def _reset():
        with bot_state._global_lock:
            bot_state._online_check_requests.clear()
            bot_state._online_check_pending.clear()
            bot_state._signals.clear()
            bot_state._refresh_needed = False
    _reset()
    yield
    _reset()


class _FakeMonitor:
    def poll_now(self):
        pass


def _wire_fast_path(monkeypatch, snap, threshold_sec=60):
    monkeypatch.setattr(online_monitor, "_monitor", _FakeMonitor())
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: snap)
    monkeypatch.setattr(
        config_manager, "get_online_check_checkers", lambda: ["c1"])
    monkeypatch.setattr(bot_state, "get_all_states", lambda: {})
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: {"online_check_threshold_sec": threshold_sec})


def _boom(*a, **k):
    raise AssertionError("fast path 有確定答案時不得走一次性 WS 登入")


def test_fast_path_recent_logout_is_offline_with_requester_threshold(monkeypatch):
    # 目標 90 秒前登出。快照烘入 bool=True（monitor 120s 寬限），但 requester
    # threshold=60 → fast path 應回離線，且不 fallback 一次性 WS。
    now = int(time.time())
    _wire_fast_path(monkeypatch, _snap(_entry(123, True, now - 90)),
                    threshold_sec=60)
    monkeypatch.setattr(svc, "check_via_ws", _boom)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done"
    assert res["result_busy"] is False


def test_fast_path_within_requester_threshold_is_online(monkeypatch):
    now = int(time.time())
    _wire_fast_path(monkeypatch, _snap(_entry(123, True, now - 90)),
                    threshold_sec=120)
    monkeypatch.setattr(svc, "check_via_ws", _boom)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done"
    assert res["result_busy"] is True


def test_fast_path_sentinel_zero_is_online(monkeypatch):
    _wire_fast_path(monkeypatch, _snap(_entry(123, True, 0)), threshold_sec=60)
    monkeypatch.setattr(svc, "check_via_ws", _boom)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done"
    assert res["result_busy"] is True


def test_fast_path_missing_ts_falls_back_to_one_shot(monkeypatch):
    # ts=None → fast path 判不出 → 走一次性 WS（check_via_ws 被呼叫）。
    _wire_fast_path(monkeypatch, _snap(_entry(123, False, None)),
                    threshold_sec=60)
    called = []

    def _check(c, pid, log, **kw):
        called.append(c)
        return False
    monkeypatch.setattr(svc, "check_via_ws", _check)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert called == ["c1"]
    assert res["status"] == "done"
    assert res["result_busy"] is False
