"""Online monitor must never connect as a human-played account (異地登入 kick).

Regression for: 真人在手機登入立刻被 WS 彈出. The monitor used to default its
persistent detector to the phone account (fc65396d_u999) and reclaim it on every
disconnect, fighting the human. The detector must default to an emulator bot
account and refuse to log in as any protected (human-played) roleId.
"""
from __future__ import annotations

import json

import pytest

import runtime_services.session_registry as reg
from runtime_services.session_registry import Channel, Owner
from ws_token import online_monitor as om


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Empty registry + neutralised protected/pause seams for every test, so
    detector selection sees a clean account-ownership table (no cross-test leak).
    """
    with reg._lock:
        reg._leases.clear()
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(reg, "_is_human_played_device", lambda dev: False)
    monkeypatch.setattr(reg, "_safe_set_pause", lambda dev, paused: None)
    yield
    with reg._lock:
        reg._leases.clear()


class _FakeCreds:
    def __init__(self, role_id):
        self.role_id = role_id


def test_default_preferred_is_not_the_phone():
    mon = om.OnlineMonitor()
    assert mon._preferred == "emulator-5554"


def test_connect_refuses_protected_account(monkeypatch):
    # load_creds yields the protected (human) roleId; monitor must refuse before
    # ever constructing a WS client (constructing == logging in == kicking human).
    monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds(89565100509472))
    built = []
    monkeypatch.setattr(om, "WSGameClient", lambda *a, **k: built.append(1))
    mon = om.OnlineMonitor(protected_role_ids=frozenset({89565100509472}))

    client = mon._connect("fc65396d_u999")

    assert client is None
    assert built == []


def test_connect_allows_unprotected_account(monkeypatch):
    monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds(89555436834913))

    class _C:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            return None

    monkeypatch.setattr(om, "WSGameClient", _C)
    mon = om.OnlineMonitor(protected_role_ids=frozenset({89565100509472}))

    client = mon._connect("emulator-5554")

    assert client is not None


def _states(monkeypatch, mapping):
    import bot_state
    monkeypatch.setattr(bot_state, "get_all_states", lambda: mapping)


def _allow_creds(monkeypatch):
    """All synthetic devices have loadable (unprotected) creds."""
    monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds(1))


def test_select_detector_prefers_idle_preferred(monkeypatch):
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5556": {"task": "休眠中"},
    })
    assert mon._select_detector() == "emulator-5554"


def test_select_detector_hands_off_when_preferred_busy(monkeypatch):
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "喚醒檢查"},  # busy bot → never use as detector
        "emulator-5556": {"task": "休眠中"},     # idle → hand off here
    })
    assert mon._select_detector() == "emulator-5556"


def test_select_detector_none_when_all_busy(monkeypatch):
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "挖礦"},
        "emulator-5556": {"task": "農場"},
    })
    assert mon._select_detector() is None


def test_select_detector_excludes_creds_less_device(monkeypatch):
    """5558 (no creds) is never a detector — only ever a monitored target."""
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5558"}

    def _load(dev):
        if dev == "emulator-5558":
            raise FileNotFoundError
        return _FakeCreds(1)

    monkeypatch.setattr(om, "load_creds", _load)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5558": {"task": "休眠中"},
    })
    assert mon._select_detector() == "emulator-5554"

    mon._role_map = {2: "emulator-5558"}  # only the creds-less device
    assert mon._select_detector() is None


def test_select_detector_sticky_keeps_current(monkeypatch):
    """Once on a detector, stay — never bounce back to preferred when it frees."""
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},  # preferred is free…
        "emulator-5556": {"task": "休眠中"},
    })
    # …but current 5556 is still eligible → keep it (sticky).
    assert mon._select_detector(current="emulator-5556", snapshot=None) == "emulator-5556"


def test_select_detector_reselect_only_picks_snapshot_offline(monkeypatch):
    """Reselect connects only to accounts the snapshot confirms OFFLINE, even
    if that means skipping the preferred (which a human is on)."""
    import config_manager
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5556": {"task": "休眠中"},
    })
    monkeypatch.setattr(config_manager, "get_device_role_id",
                        lambda dev: {"emulator-5554": 11, "emulator-5556": 22}.get(dev))
    snap = om.Snapshot(detector="x", timestamp=1000.0, entries=(
        om.StatusEntry(11, "a", True, None),    # 5554 online (human) → exclude
        om.StatusEntry(22, "b", False, None),   # 5556 offline → safe
    ))
    assert mon._select_detector(current=None, snapshot=snap) == "emulator-5556"


def test_select_detector_excludes_about_to_wake(monkeypatch):
    """A candidate whose bot wakes within the lead window is skipped."""
    mon = om.OnlineMonitor(preferred="emulator-5554", now=lambda: 1000.0)
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中", "next_wake_at": 1030.0},   # 30s < lead → skip
        "emulator-5556": {"task": "休眠中", "next_wake_at": 9_999_999.0},
    })
    assert mon._select_detector() == "emulator-5556"


def test_select_detector_sticky_breaks_when_current_about_to_wake(monkeypatch):
    """Current detector about to run its own script → hand off before it wakes."""
    mon = om.OnlineMonitor(preferred="emulator-5554", now=lambda: 1000.0)
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中", "next_wake_at": 1030.0},  # current, waking soon
        "emulator-5556": {"task": "休眠中"},
    })
    assert mon._select_detector(current="emulator-5554", snapshot=None) == "emulator-5556"


def test_last_switch_records_transition():
    clock = {"t": 100.0}
    mon = om.OnlineMonitor(now=lambda: clock["t"])
    assert mon.last_switch is None

    mon._set_active("emulator-5554")            # None -> 5554
    assert mon.last_switch == {"frm": None, "to": "emulator-5554", "ts": 100.0}

    clock["t"] = 160.0
    mon._set_active("emulator-5556")            # 5554 -> 5556
    sw = mon.last_switch
    assert sw["frm"] == "emulator-5554" and sw["to"] == "emulator-5556"
    assert sw["ts"] == 160.0

    clock["t"] = 200.0
    mon._set_active("emulator-5556")            # no-op (unchanged) → not recorded
    assert mon.last_switch["ts"] == 160.0


def test_switch_cooldown_blocks_rapid_reconnect():
    clock = {"t": 1_000_000.0}
    mon = om.OnlineMonitor(switch_cooldown_sec=300.0, now=lambda: clock["t"])

    # Nothing switched yet → first connect is allowed immediately.
    assert mon._switch_allowed() is True

    # Just switched → blocked until the 5-min cooldown elapses.
    mon._last_switch_ts = clock["t"]
    assert mon._switch_allowed() is False
    clock["t"] += 299
    assert mon._switch_allowed() is False
    clock["t"] += 2  # 301s total
    assert mon._switch_allowed() is True


def test_discover_role_map_excludes_protected(tmp_path, monkeypatch):
    import config_manager
    monkeypatch.setattr(config_manager, "load_config", lambda: {})

    def _write(name, rid):
        (tmp_path / f"_auth_capture_{name}.json").write_text(
            json.dumps({"creds": {"roleId": rid}}), encoding="utf-8")

    _write("emulator-5554", 89555436834913)
    _write("fc65396d_u999", 89565100509472)

    mapping = om.discover_role_map(
        auth_dir=tmp_path, protected_role_ids=frozenset({89565100509472}))

    assert 89555436834913 in mapping
    assert 89565100509472 not in mapping


# --- stale-escape: 過期 >5min 時強制盲選 idle bot 重新刷新 -----------------------
# 使用者 2026-06-25：snapshot 卡住超過 5 分鐘沒更新 → 強制隨機挑一台「沒在跑
# ws/h5 的 bot」直接登入刷新，跳過離線驗證（可能踢到剛好在玩該 bot 帳號的真人）。
# 5558 永遠不可被選中。human_played 主帳仍排除（不在 role_map）。

def test_force_refresh_blind_picks_when_snapshot_stale(monkeypatch):
    """卡 >門檻：有 idle bot 但 snapshot 驗不出離線（顯示在線）→ 仍盲連一台。"""
    import config_manager
    t = 1_000_000.0
    mon = om.OnlineMonitor(preferred="emulator-5554",
                           force_refresh_sec=300.0, now=lambda: t)
    mon._role_map = {1: "emulator-5554"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {"emulator-5554": {"task": "休眠中"}})
    monkeypatch.setattr(config_manager, "get_device_role_id", lambda dev: 11)
    # snapshot 6 分鐘前、且 5554 顯示在線 → 正常路徑 safe 為空
    snap = om.Snapshot("x", t - 360.0, (om.StatusEntry(11, "a", True, None),))
    assert mon._select_detector(current=None, snapshot=snap) == "emulator-5554"
    assert mon._last_pick_forced is True


def test_no_force_when_snapshot_fresh(monkeypatch):
    """未過門檻：safe 空就維持 None（不強制、不踢人）。"""
    import config_manager
    t = 1_000_000.0
    mon = om.OnlineMonitor(preferred="emulator-5554",
                           force_refresh_sec=300.0, now=lambda: t)
    mon._role_map = {1: "emulator-5554"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {"emulator-5554": {"task": "休眠中"}})
    monkeypatch.setattr(config_manager, "get_device_role_id", lambda dev: 11)
    snap = om.Snapshot("x", t - 60.0, (om.StatusEntry(11, "a", True, None),))
    assert mon._select_detector(current=None, snapshot=snap) is None
    assert mon._last_pick_forced is False


def test_force_refresh_never_picks_excluded_5558(monkeypatch):
    """5558 縱使有 creds、idle 也永不被強制選中。"""
    import config_manager
    t = 1_000_000.0
    mon = om.OnlineMonitor(preferred="emulator-5554", force_refresh_sec=300.0,
                           force_exclude=("emulator-5558",), now=lambda: t)
    mon._role_map = {1: "emulator-5556", 2: "emulator-5558"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5556": {"task": "休眠中"},
        "emulator-5558": {"task": "休眠中"},
    })
    monkeypatch.setattr(config_manager, "get_device_role_id",
                        lambda dev: {"emulator-5556": 22, "emulator-5558": 58}.get(dev))
    snap = om.Snapshot("x", t - 360.0, (
        om.StatusEntry(22, "b", True, None),
        om.StatusEntry(58, "c", True, None),
    ))
    assert mon._select_detector(current=None, snapshot=snap) == "emulator-5556"

    mon._role_map = {2: "emulator-5558"}  # 只剩被排除的 → 無從強制
    assert mon._select_detector(current=None, snapshot=snap) is None
    assert mon._last_pick_forced is False


def test_snapshot_offline_treats_self_as_offline():
    """偵測器讀不到自己(不在自己好友列表)→ 視為離線，否則永遠無法重選回自己。"""
    mon = om.OnlineMonitor()
    snap = om.Snapshot("7fe98fc6", 1000.0, ())  # 空好友列表，不含自己
    assert mon._snapshot_offline("7fe98fc6", snap) is True


def test_reselect_repicks_previous_detector_not_in_own_friendlist(monkeypatch):
    """只剩上一任偵測器 idle 且它不在自己好友列表 → 立即重選回來(不必等強制刷新)。"""
    import config_manager
    mon = om.OnlineMonitor(preferred="emulator-5554", now=lambda: 1_000_000.0)
    mon._role_map = {99: "7fe98fc6", 11: "emulator-5554"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "7fe98fc6": {"task": "休眠中"},        # 上一任偵測器，現在 idle
        "emulator-5554": {"task": "挖礦"},      # 其他在忙 → 不在 pool
    })
    monkeypatch.setattr(config_manager, "get_device_role_id",
                        lambda dev: {"7fe98fc6": 99, "emulator-5554": 11}.get(dev))
    # 小寶讀的 snapshot：含好友 5554，但「不含小寶自己」(rid 99)，且很新(5s)。
    snap = om.Snapshot("7fe98fc6", 1_000_000.0 - 5.0,
                       (om.StatusEntry(11, "5554", False, None),))
    assert mon._select_detector(current=None, snapshot=snap) == "7fe98fc6"
    assert mon._last_pick_forced is False  # 正常重選，不是強制盲選


def test_setup_monitor_log_writes_to_dedicated_file(tmp_path, monkeypatch):
    """偵測器 log 獨立成 logs/system/online_monitor.log（方便排錯）。"""
    from logging.handlers import RotatingFileHandler
    from utils.log_paths import LogPaths
    monkeypatch.setattr(LogPaths, "ROOT", tmp_path)
    om._log_handler_attached = False
    try:
        om._setup_monitor_log()
        om.logger.info("hello-detector-probe")
        for h in om.logger.handlers:
            h.flush()
        f = tmp_path / "system" / "online_monitor.log"
        assert f.exists()
        assert "hello-detector-probe" in f.read_text(encoding="utf-8")
    finally:
        for h in list(om.logger.handlers):
            if isinstance(h, RotatingFileHandler):
                om.logger.removeHandler(h)
                h.close()
        om._log_handler_attached = False


# --- session_registry 接線 (Phase 4) ----------------------------------------
# detector 選擇要對候選 registry.peek：被別的 owner 佔用者跳過；5558 一般路徑也硬
# 排除；被更高優先權搶佔時要收線讓位。

def test_5558_excluded_on_general_select_path(monkeypatch):
    """bug#3:5558 縱使 idle+有 creds,一般 _select_detector 路徑也永不被選中。"""
    mon = om.OnlineMonitor(preferred="emulator-5558",
                           force_exclude=("emulator-5558",))
    mon._role_map = {1: "emulator-5558", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5558": {"task": "休眠中"},   # idle 但被硬排除
        "emulator-5556": {"task": "休眠中"},
    })
    assert mon._select_detector() == "emulator-5556"

    mon._role_map = {1: "emulator-5558"}  # 只剩被排除者 → 無可選
    assert mon._select_detector() is None


def test_select_skips_device_owned_by_other(monkeypatch):
    """候選被別的 owner(SCHEDULER)佔用 → 跳過,改選未被佔用的。"""
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5556": {"task": "休眠中"},
    })
    reg.acquire("emulator-5554", Owner.SCHEDULER, Channel.WS)  # 別的 owner 佔用
    assert mon._select_detector() == "emulator-5556"


def test_select_keeps_own_detector_lease(monkeypatch):
    """自己(ONLINE_MONITOR)持有的 lease 不算「被別人佔用」→ sticky 仍成立。"""
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {"emulator-5556": {"task": "休眠中"}})
    reg.acquire("emulator-5556", Owner.ONLINE_MONITOR, Channel.WS)
    assert mon._select_detector(current="emulator-5556", snapshot=None) == "emulator-5556"


def test_preempted_true_when_own_lease_preempted():
    """自己 lease 的 preempted Event 被 set → _preempted 回 True(該收線讓位)。"""
    mon = om.OnlineMonitor()
    res = reg.acquire("emulator-5554", Owner.ONLINE_MONITOR, Channel.WS)
    assert mon._preempted("emulator-5554") is False
    res.lease.preempted.set()
    assert mon._preempted("emulator-5554") is True


def test_preempted_false_for_none_or_other_owner():
    mon = om.OnlineMonitor()
    assert mon._preempted(None) is False
    # 被 SCHEDULER 搶走後,current 的 lease 已不是我們的 → 不再自認被搶(已交出)。
    reg.acquire("emulator-5554", Owner.ONLINE_MONITOR, Channel.WS)
    reg.acquire("emulator-5554", Owner.SCHEDULER, Channel.WS, preempt=True)
    assert mon._preempted("emulator-5554") is False


def test_scheduler_preempt_yields_and_releases():
    """SCHEDULER 搶佔 online-monitor 的 detector:舊 lease 被通知 + 收回佔用。"""
    old = reg.acquire("emulator-5554", Owner.ONLINE_MONITOR, Channel.WS)
    mon = om.OnlineMonitor()
    assert mon._preempted("emulator-5554") is False
    reg.acquire("emulator-5554", Owner.SCHEDULER, Channel.WS, preempt=True)
    assert old.lease.preempted.is_set()               # 舊 owner 收到讓位通知
    assert reg.peek("emulator-5554").owner is Owner.SCHEDULER
    # monitor 讓位後 release 自己的 lease 是冪等 no-op(owner 已不符)。
    mon._release_detector("emulator-5554")
    assert reg.peek("emulator-5554").owner is Owner.SCHEDULER
