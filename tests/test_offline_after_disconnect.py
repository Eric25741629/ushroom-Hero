"""Tests for the「掉線超過 1 小時 → 直接判定離線」rules in bot_state.

Covers:
- set_offline() clears next_wake_at so dashboard stops showing「喚醒中...」.
- update_state(status=...) pass-through (master ingest of worker-reported
  status) with the same offline_since / next_wake_at transition semantics.
- set_online() / clear_offline_anchor() recovery helpers.
- sweep_stale_remote_devices(): remote (worker_id:ip) entries whose heartbeat
  is older than the threshold are marked OFFLINE; local devices are never
  touched (sleeping local devices do not heartbeat).
"""
import time

import bot_state


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._wake_overrides.pop(ip, None)
        bot_state._locks.pop(ip, None)
        bot_state._local_device_ids.discard(ip)


# --------------------------------------------------------------------------
# set_offline / update_state(status=...)
# --------------------------------------------------------------------------


def test_set_offline_clears_next_wake_at():
    ip = "test-offline-nextwake-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.update_state(ip, task="休眠中", next_wake_at=time.time() + 600)
        bot_state.set_offline(ip, reason="測試離線")
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "OFFLINE"
        assert "next_wake_at" not in st
    finally:
        _cleanup(ip)


def test_update_state_status_offline_sets_anchor_and_clears_next_wake():
    ip = "w1:test-status-passthrough"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="休眠中", next_wake_at=time.time() + 600)
        bot_state.update_state(ip, task="x", status="OFFLINE")
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "OFFLINE"
        assert st.get("offline_since") is not None
        assert "next_wake_at" not in st
    finally:
        _cleanup(ip)


def test_update_state_status_back_online_clears_anchor():
    ip = "w1:test-status-revive"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="x", status="OFFLINE")
        bot_state.update_state(ip, task="y", status="ONLINE")
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "ONLINE"
        assert "offline_since" not in st
    finally:
        _cleanup(ip)


def test_update_state_status_none_does_not_touch_status():
    ip = "w1:test-status-none"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="x", status="OFFLINE")
        bot_state.update_state(ip, task="y")  # no status → unchanged
        assert bot_state.get_all_states()[ip]["status"] == "OFFLINE"
    finally:
        _cleanup(ip)


# --------------------------------------------------------------------------
# set_online / clear_offline_anchor
# --------------------------------------------------------------------------


def test_set_online_restores_status_and_clears_anchor():
    ip = "test-setonline-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_offline(ip, reason="斷線")
        bot_state.set_online(ip, reason="ADB 重新連線")
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "ONLINE"
        assert "offline_since" not in st
        assert st["step"] == "ADB 重新連線"
    finally:
        _cleanup(ip)


def test_set_online_noop_when_no_state():
    ip = "test-setonline-missing-5554"
    _cleanup(ip)
    bot_state.set_online(ip)
    assert ip not in bot_state.get_all_states()
    _cleanup(ip)


def test_clear_offline_anchor_keeps_offline_but_unblocks_restart():
    ip = "test-anchor-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_offline(ip, reason="斷線")
        assert bot_state.get_offline_since(ip) is not None
        bot_state.clear_offline_anchor(ip)
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "OFFLINE"  # 狀態仍離線
        assert bot_state.get_offline_since(ip) is None  # 但 3h 重啟封鎖解除
    finally:
        _cleanup(ip)


# --------------------------------------------------------------------------
# sweep_stale_remote_devices
# --------------------------------------------------------------------------


def _make_stale(ip: str, age_sec: float) -> None:
    bot_state.update_state(ip, task="休眠中", next_wake_at=time.time() + 60)
    with bot_state.get_device_lock(ip):
        bot_state._states[ip]["last_update"] = time.time() - age_sec


def test_sweep_marks_stale_remote_offline():
    ip = "w1:test-sweep-stale"
    _cleanup(ip)
    try:
        _make_stale(ip, 4000)
        bot_state.sweep_stale_remote_devices(offline_after_sec=3600)
        st = bot_state.get_all_states()[ip]
        assert st["status"] == "OFFLINE"
        assert "未回報" in st["step"]
        assert "next_wake_at" not in st
        assert st.get("offline_since") is not None
    finally:
        _cleanup(ip)


def test_sweep_keeps_fresh_remote_online():
    ip = "w1:test-sweep-fresh"
    _cleanup(ip)
    try:
        _make_stale(ip, 100)
        bot_state.sweep_stale_remote_devices(offline_after_sec=3600)
        assert bot_state.get_all_states()[ip]["status"] == "ONLINE"
    finally:
        _cleanup(ip)


def test_sweep_never_touches_local_devices():
    plain = "emulator-5554-test-sweep"
    tcp_local = "127.0.0.1:5555-test-sweep"
    _cleanup(plain)
    _cleanup(tcp_local)
    try:
        _make_stale(plain, 999999)
        _make_stale(tcp_local, 999999)
        bot_state.register_local_device(tcp_local)  # 本機 TCP 模擬器帶冒號
        bot_state.sweep_stale_remote_devices(offline_after_sec=3600)
        assert bot_state.get_all_states()[plain]["status"] == "ONLINE"
        assert bot_state.get_all_states()[tcp_local]["status"] == "ONLINE"
    finally:
        _cleanup(plain)
        _cleanup(tcp_local)


def test_sweep_does_not_refresh_existing_offline_anchor():
    ip = "w1:test-sweep-already-offline"
    _cleanup(ip)
    try:
        _make_stale(ip, 4000)
        bot_state.update_state(ip, status="OFFLINE")
        anchor = bot_state.get_all_states()[ip]["offline_since"]
        with bot_state.get_device_lock(ip):
            bot_state._states[ip]["last_update"] = time.time() - 4000
        bot_state.sweep_stale_remote_devices(offline_after_sec=3600)
        assert bot_state.get_all_states()[ip]["offline_since"] == anchor
    finally:
        _cleanup(ip)


def test_sweep_does_not_delete_entries():
    ip = "w1:test-sweep-keep-entry"
    _cleanup(ip)
    try:
        _make_stale(ip, 999999)
        bot_state.sweep_stale_remote_devices(offline_after_sec=3600)
        assert ip in bot_state.get_all_states()
    finally:
        _cleanup(ip)
