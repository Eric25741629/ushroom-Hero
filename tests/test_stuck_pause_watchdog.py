"""Tests for the stuck-pause watchdog in device_scan_service.

三類門檻:
  - ONLINE_MONITOR detector:軟 2h preempt / 硬 3h release
  - 其他腳本借用(ONLINE_CHECK/MOUNT_TRACKER/TOOL):軟 30m / 硬 45m
  - 手動暫停(無 registry lease):24h 保底 resume
軟門檻設 lease.preempted(請 owner 讓位);硬門檻強制 registry.release。
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

# device_scan_service → device.py → uiautomator2(測試不需要真機)
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
import runtime_services.session_registry as registry  # noqa: E402
from runtime_services.session_registry import Channel, Lease, Owner  # noqa: E402

_LOG = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
_NOW = 1_000_000.0


def _lease(owner: Owner, age_sec: float) -> Lease:
    return Lease(device="emulator-5554", owner=owner, channel=Channel.WS,
                 label="test", acquired_at=_NOW - age_sec, role_id=None)


def _install(monkeypatch, *, paused=True, local=True, lease=None,
             paused_since=None):
    """Stub bot_state + registry so _sweep runs against a single fake device."""
    released, resumed = [], []
    monkeypatch.setattr(bot_state, "get_all_states",
                        lambda: {"emulator-5554": {"paused": paused}})
    monkeypatch.setattr(bot_state, "is_local_device", lambda ip: local)
    monkeypatch.setattr(bot_state, "get_paused_since", lambda ip: paused_since)
    monkeypatch.setattr(bot_state, "set_pause",
                        lambda ip, p: resumed.append((ip, p)))
    monkeypatch.setattr(registry, "peek", lambda ip: lease)
    monkeypatch.setattr(registry, "release",
                        lambda ip, owner: released.append((ip, owner)))
    return released, resumed


# --- 線上監控 detector ---------------------------------------------------------

def test_monitor_below_soft_no_action(monkeypatch):
    lease = _lease(Owner.ONLINE_MONITOR, age_sec=90 * 60)  # 1.5h < 2h
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not released and not resumed and not lease.preempted.is_set()


def test_monitor_soft_sets_preempt_not_release(monkeypatch):
    lease = _lease(Owner.ONLINE_MONITOR, age_sec=2 * 3600 + 60)  # >2h, <3h
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert lease.preempted.is_set()
    assert not released and not resumed


def test_monitor_hard_releases(monkeypatch):
    lease = _lease(Owner.ONLINE_MONITOR, age_sec=3 * 3600 + 60)  # >3h
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert released == [("emulator-5554", Owner.ONLINE_MONITOR)]


# --- 其他腳本借用(30m/45m)----------------------------------------------------

def test_borrow_soft_sets_preempt(monkeypatch):
    lease = _lease(Owner.MOUNT_TRACKER, age_sec=35 * 60)  # >30m, <45m
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert lease.preempted.is_set()
    assert not released


def test_borrow_hard_releases(monkeypatch):
    lease = _lease(Owner.ONLINE_CHECK, age_sec=50 * 60)  # >45m
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert released == [("emulator-5554", Owner.ONLINE_CHECK)]


def test_borrow_below_soft_no_action(monkeypatch):
    lease = _lease(Owner.TOOL, age_sec=10 * 60)  # 10m < 30m
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not released and not lease.preempted.is_set()


# --- 手動暫停(無 lease,24h)-------------------------------------------------

def test_manual_below_24h_no_resume(monkeypatch):
    _, resumed = _install(monkeypatch, lease=None,
                          paused_since=_NOW - 23 * 3600)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not resumed


def test_manual_over_24h_resumes(monkeypatch):
    _, resumed = _install(monkeypatch, lease=None,
                          paused_since=_NOW - 25 * 3600)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert resumed == [("emulator-5554", False)]


def test_manual_no_paused_since_no_resume(monkeypatch):
    _, resumed = _install(monkeypatch, lease=None, paused_since=None)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not resumed


# --- 跳過條件 -----------------------------------------------------------------

def test_scheduler_lease_ignored(monkeypatch):
    lease = _lease(Owner.SCHEDULER, age_sec=10 * 3600)
    released, resumed = _install(monkeypatch, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not released and not resumed


def test_not_paused_ignored(monkeypatch):
    lease = _lease(Owner.ONLINE_MONITOR, age_sec=5 * 3600)
    released, resumed = _install(monkeypatch, paused=False, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not released and not resumed and not lease.preempted.is_set()


def test_remote_device_ignored(monkeypatch):
    lease = _lease(Owner.ONLINE_MONITOR, age_sec=5 * 3600)
    released, resumed = _install(monkeypatch, local=False, lease=lease)
    dss._sweep_stuck_pauses(_LOG, now=_NOW)
    assert not released and not resumed


# --- bot_state.paused_since 記錄行為 -----------------------------------------

def test_paused_since_recorded_and_cleared():
    ip = "test-paused-since"
    bot_state.init_device(ip)
    assert bot_state.get_paused_since(ip) is None
    bot_state.set_pause(ip, True)
    first = bot_state.get_paused_since(ip)
    assert first is not None
    # 重複暫停不刷新起始時間(否則 24h 保底永遠歸零)
    bot_state.set_pause(ip, True)
    assert bot_state.get_paused_since(ip) == first
    bot_state.set_pause(ip, False)
    assert bot_state.get_paused_since(ip) is None
