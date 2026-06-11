"""Tests for the ADB-absence offline rule in device_scan_service.

掉線（不在 `adb devices`）超過 1 小時 → set_offline；回到清單 →
thread 活著就 set_online、死了就 clear_offline_anchor（解除 3h 重啟封鎖）。
"""
from __future__ import annotations

import sys
import time
import types
from types import SimpleNamespace

# device_scan_service → device.py → uiautomator2（測試不需要真機）
if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

if "device" not in sys.modules:
    _dev = types.ModuleType("device")
    _dev.get_adb_devices = lambda *a, **k: []
    _dev.open_notification = lambda *a, **k: None
    _dev.close_notification = lambda *a, **k: None
    sys.modules["device"] = _dev

import bot_state  # noqa: E402
import runtime_services.device_scan_service as dss  # noqa: E402


_LOG = SimpleNamespace(
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
)


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._wake_overrides.pop(ip, None)
        bot_state._locks.pop(ip, None)
        bot_state._local_device_ids.discard(ip)
    dss._adb_last_seen.pop(ip, None)
    dss._adb_absence_offlined.discard(ip)


def _alive_thread() -> SimpleNamespace:
    return SimpleNamespace(is_alive=lambda: True)


def test_absent_under_threshold_keeps_online():
    ip = "test-absence-under"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        now = time.time()
        dss._apply_adb_absence_rule({ip}, {ip: _alive_thread()}, _LOG, now=now)
        dss._apply_adb_absence_rule(set(), {ip: _alive_thread()}, _LOG, now=now + 600)
        assert bot_state.get_all_states()[ip]["status"] == "ONLINE"
    finally:
        _cleanup(ip)


def test_absent_over_threshold_marks_offline():
    ip = "test-absence-over"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.update_state(ip, task="休眠中", next_wake_at=time.time() + 60)
        now = time.time()
        dss._apply_adb_absence_rule({ip}, {ip: _alive_thread()}, _LOG, now=now)
        dss._apply_adb_absence_rule(set(), {ip: _alive_thread()}, _LOG, now=now + 3601)
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "OFFLINE"
        assert "斷線" in st["step"]
        assert "next_wake_at" not in st
    finally:
        _cleanup(ip)


def test_reappear_with_alive_thread_restores_online():
    ip = "test-absence-revive-alive"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        threads = {ip: _alive_thread()}
        now = time.time()
        dss._apply_adb_absence_rule({ip}, threads, _LOG, now=now)
        dss._apply_adb_absence_rule(set(), threads, _LOG, now=now + 3601)
        assert bot_state.get_all_states()[ip]["status"] == "OFFLINE"
        dss._apply_adb_absence_rule({ip}, threads, _LOG, now=now + 3700)
        assert bot_state.get_all_states()[ip]["status"] == "ONLINE"
    finally:
        _cleanup(ip)


def test_reappear_with_dead_thread_clears_restart_block():
    ip = "test-absence-revive-dead"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        now = time.time()
        dss._apply_adb_absence_rule({ip}, {}, _LOG, now=now)
        dss._apply_adb_absence_rule(set(), {}, _LOG, now=now + 3601)
        # 模擬缺席期間 thread 已退出（set_offline anchor 很舊 → 3h 規則會封鎖重啟）
        assert bot_state.get_offline_since(ip) is not None
        dss._apply_adb_absence_rule({ip}, {}, _LOG, now=now + 20000)
        # 仍 OFFLINE（沒 thread），但 anchor 已清 → scan 重啟 gate 放行
        assert bot_state.get_all_states()[ip]["status"] == "OFFLINE"
        assert bot_state.get_offline_since(ip) is None
    finally:
        _cleanup(ip)


def test_already_offline_absent_device_not_overwritten_but_tracked():
    ip = "test-absence-already-offline"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        now = time.time()
        dss._apply_adb_absence_rule({ip}, {}, _LOG, now=now)
        bot_state.set_offline(ip, reason="init failed: xxx")
        dss._apply_adb_absence_rule(set(), {}, _LOG, now=now + 3601)
        # 不覆寫既有離線原因
        assert bot_state.get_all_states()[ip]["step"] == "init failed: xxx"
        # 但回連時 anchor 仍會被清（解除 3h 封鎖）
        dss._apply_adb_absence_rule({ip}, {}, _LOG, now=now + 20000)
        assert bot_state.get_offline_since(ip) is None
    finally:
        _cleanup(ip)


def test_never_seen_devices_are_not_tracked():
    ip = "test-absence-never-seen"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)  # 有狀態但從未出現在 adb 掃描（web_h5 / ws_token）
        dss._apply_adb_absence_rule(set(), {}, _LOG, now=time.time() + 999999)
        assert bot_state.get_all_states()[ip]["status"] == "ONLINE"
    finally:
        _cleanup(ip)
