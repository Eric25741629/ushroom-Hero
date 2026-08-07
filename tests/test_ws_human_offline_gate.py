"""human_played 裝置:自己的 WS 階段在登入前先等真人下線,避免異地登入踢掉真人。

對照 tasks/todo.md 2026-06-25 段。涵蓋:
- _wait_until_human_offline: online→重查、None(看不到)→重查、offline→放行、無creds→放行。
- run_ws_phase: 只有 human_played 才檢查;非 human_played 直接跑。
- online_monitor.account_online: 快照新鮮→回 online、過期/缺/不在好友清單→None。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("cv2", types.SimpleNamespace())

import config_manager  # noqa: E402
from game_actions import ws_phase  # noqa: E402
from ws_token import online_monitor  # noqa: E402
from ws_token.runner import RunReport  # noqa: E402


def _cfg(monkeypatch, ws, *, backend="adb", human_played=False):
    merged_ws = {"bootstrap_token": False}
    merged_ws.update(ws)
    fields = {"ws_token": merged_ws, "backend": backend,
              "human_played": human_played}
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  fields.get(k, d)})())


def _report(tasks, errors=None, login_ok=True):
    return RunReport(device="dev", login_ok=login_ok, spend=False,
                     tasks=tasks, errors=errors or {})


# --- _wait_until_human_offline ------------------------------------------------

def test_wait_returns_immediately_when_offline(monkeypatch):
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: False)
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger)
    assert slept == []  # 確認離線 → 不等待


def test_wait_breaks_on_force_sleep_even_if_human_online(monkeypatch):
    """使用者按強制休眠 → 打斷「等待真人下線」、回 True，不再無限等（2026-07-16）。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: True)  # 真人一直在線
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.has_pending_force_sleep", lambda ip: True)
    monkeypatch.setattr("bot_state.is_paused", lambda ip: False)

    aborted = ws_phase._wait_until_human_offline("dev", ws_phase.logger,
                                                 human_played=True)
    assert aborted is True   # 中斷 → 呼叫端放棄本輪 WS
    assert slept == []       # 立即打斷，不進等待迴圈


def test_wait_breaks_on_pause_even_if_human_online(monkeypatch):
    """使用者暫停 → 同樣打斷等待真人下線、回 True。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: True)
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.has_pending_force_sleep", lambda ip: False)
    monkeypatch.setattr("bot_state.is_paused", lambda ip: True)

    assert ws_phase._wait_until_human_offline("dev", ws_phase.logger,
                                              human_played=True) is True
    assert slept == []


def test_wait_loops_until_human_goes_offline(monkeypatch):
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    seq = iter([True, True, False])
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: next(seq))
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger)
    # 在線兩輪 → 各睡一整個輪詢週期（切成 1s 切片）
    assert slept == [1] * (int(ws_phase._HUMAN_WAIT_POLL_SEC) * 2)


def test_wait_force_sleep_mid_wait_breaks_within_one_second(monkeypatch):
    """等待中強制休眠落在 1s 切片內就中斷，不必等滿 30s 輪詢週期。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: True)
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    state = {"force": False, "paused": False}
    monkeypatch.setattr("bot_state.has_pending_force_sleep",
                        lambda ip: state["force"])
    monkeypatch.setattr("bot_state.is_paused", lambda ip: state["paused"])

    def _flip_after(n):
        def fake_sleep(s):
            slept.append(s)
            state["force"] = len(slept) >= n
        return fake_sleep

    monkeypatch.setattr(ws_phase.time, "sleep", _flip_after(5))
    aborted = ws_phase._wait_until_human_offline("dev", ws_phase.logger,
                                                 human_played=True)
    assert aborted is True
    assert len(slept) == 5            # 第 5 片才收到強制休眠 → 立即打斷
    assert slept == [1] * 5           # 全部 1s 切片


def test_wait_human_played_treats_unknown_as_online(monkeypatch):
    """human_played：觀察者看不到(None)→ 當作可能在線、無限等(使用者 2026-06-25 指定)。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    seq = iter([None, None, False])
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: next(seq))
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=True)
    assert slept == [1] * (int(ws_phase._HUMAN_WAIT_POLL_SEC) * 2)


def test_wait_bot_device_best_effort_releases_after_max_polls(monkeypatch):
    """非 human_played：觀察者一直看不到(None)→ 重查上限後 best-effort 放行,不卡死。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: None)  # 永遠看不到
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=False)
    # 重查 _UNDETERMINED_MAX_POLLS 輪(各睡一個週期)後第 N+1 輪放行
    assert slept == [1] * (int(ws_phase._HUMAN_WAIT_POLL_SEC)
                           * ws_phase._UNDETERMINED_MAX_POLLS)


def test_wait_releases_immediately_when_device_is_detector(monkeypatch):
    """本裝置正是當前偵測器 → 直接放行,連 presence 都不查。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: "dev")
    called = []
    monkeypatch.setattr(ws_phase, "_account_online",
                        lambda rid, **k: called.append(rid))
    monkeypatch.setattr(ws_phase.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("不該等")))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=False)
    assert called == []


def test_wait_releases_immediately_on_web_launch_request(monkeypatch):
    """等待中使用者按「開啟網頁」→ 立即放行(即使帳號還在線)。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: True)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid, **k: True)  # 真人在玩
    monkeypatch.setattr(ws_phase.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("不該等")))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=True)


def test_wait_passes_through_when_no_creds(monkeypatch):
    """解不出 roleId(無 creds)→ 直接放行,連 presence 都不查。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: None)
    called = []
    monkeypatch.setattr(ws_phase, "_account_online",
                        lambda rid, **k: called.append(rid))
    monkeypatch.setattr(ws_phase.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("不該等")))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger)
    assert called == []


# --- run_ws_phase 整合 --------------------------------------------------------

def test_run_ws_phase_gates_human_played(monkeypatch):
    """human_played → 進 WS 前先呼叫 _wait_until_human_offline，human_played=True。"""
    _cfg(monkeypatch, {"enabled": True}, human_played=True)
    gate = []
    monkeypatch.setattr(ws_phase, "_wait_until_human_offline",
                        lambda ip, log, **k: gate.append((ip, k.get("human_played"))))
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw: _report({"lamp": {}}))
    skips = ws_phase.run_ws_phase("dev")
    assert gate == [("dev", True)]
    assert "開神燈" in skips


def test_run_ws_phase_gates_bot_device_too(monkeypatch):
    """非 human_played(emulator)也是真人帳號 → 一樣先等下線,human_played=False。"""
    _cfg(monkeypatch, {"enabled": True}, human_played=False)
    gate = []
    monkeypatch.setattr(ws_phase, "_wait_until_human_offline",
                        lambda ip, log, **k: gate.append((ip, k.get("human_played"))))
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw: _report({"lamp": {}}))
    ws_phase.run_ws_phase("dev")
    assert gate == [("dev", False)]


# --- online_monitor.account_online -------------------------------------------

def _snap(ts, entries, now_detector="emulator-5554"):
    return online_monitor.Snapshot(
        detector=now_detector, timestamp=ts,
        entries=tuple(online_monitor.StatusEntry(rid, str(rid), on, ts)
                      for rid, on, ts in entries))


def test_account_online_fresh_snapshot(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot",
                        lambda: _snap(1000.0, [(123, True, None), (456, False, None)]))
    assert online_monitor.account_online(123, now=1030.0) is True
    assert online_monitor.account_online(456, now=1030.0) is False


def test_account_online_unknown_when_not_in_friend_list(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot",
                        lambda: _snap(1000.0, [(123, True, None)]))
    assert online_monitor.account_online(999, now=1030.0) is None


def test_account_online_unknown_when_stale(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot",
                        lambda: _snap(1000.0, [(123, True, None)]))
    assert online_monitor.account_online(123, now=1000.0 + 61) is None


def test_account_online_unknown_when_no_snapshot(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: None)
    assert online_monitor.account_online(123, now=1030.0) is None


def _monitor_state(monkeypatch, *, snap, active=None, yield_ts=None):
    mon = online_monitor.OnlineMonitor()
    with mon._lock:
        mon._snapshot = snap
        mon._active_detector = active
        mon._intentional_yield = (
            online_monitor._IntentionalYield(
                reason="no_idle_detector", snapshot_timestamp=yield_ts)
            if yield_ts is not None else None
        )
    monkeypatch.setattr(online_monitor, "_monitor", mon)
    return mon


def test_wake_gate_uses_normal_fresh_snapshot(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True, 1000), (456, False, None)]),
        active="emulator-5554",
    )
    assert online_monitor.account_online_for_wake_gate(123, now=1030.0) is True
    assert online_monitor.account_online_for_wake_gate(456, now=1030.0) is False


def test_wake_gate_accepts_recent_no_idle_stale_offline(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False, None)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1599.0) is False


def test_wake_gate_rejects_expired_no_idle_snapshot(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False, None)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1600.1) is None


def test_wake_gate_never_accepts_stale_online(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True, 1000)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(123, now=1100.0) is None


def test_wake_gate_requires_matching_yield_snapshot(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1001.0, [(456, False, None)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1100.0) is None


def test_wake_gate_requires_monitor_to_remain_disconnected(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False, None)]),
        active="emulator-5556",
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1100.0) is None


def test_wake_gate_rejects_stale_offline_without_yield_marker(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False, None)]),
        active=None,
        yield_ts=None,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1100.0) is None


def test_wake_gate_recomputes_online_from_raw_ts(monkeypatch):
    """快照新、baked bool 卻說在線 → 改用原始 last_login_ts 重算（修強制休眠延遲）。"""
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True, 900), (456, False, 1000)]),
        active="emulator-5554",
    )
    # last_login_ts 90s 前、threshold 60 → 判定已離線（不吃 baked True）
    assert online_monitor.account_online_for_wake_gate(123, now=990.0) is False
    # 30s 內登入 → 判定在線
    assert online_monitor.account_online_for_wake_gate(456, now=1030.0) is True


def test_wake_gate_raw_ts_zero_means_online(monkeypatch):
    """ts==0：server presence sentinel（session 確認存活）→ 判在線。"""
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True, 0)]),
        active="emulator-5554",
    )
    assert online_monitor.account_online_for_wake_gate(123, now=1050.0) is True


def test_wake_gate_missing_ts_means_offline(monkeypatch):
    """快照新但無 ts（parse 時已判離線）→ False，不誤放行。"""
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, False, None)]),
        active="emulator-5554",
    )
    assert online_monitor.account_online_for_wake_gate(123, now=1030.0) is False


def test_wake_gate_honors_custom_threshold_sec(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True, 900)]),
        active="emulator-5554",
    )
    assert online_monitor.account_online_for_wake_gate(
        123, now=990.0, threshold_sec=120.0) is True  # 120s 內 → 在線
    assert online_monitor.account_online_for_wake_gate(
        123, now=1030.0, threshold_sec=120.0) is False  # 130s 前 → 離線


def test_ws_phase_account_lookup_uses_wake_gate_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        online_monitor, "account_online_for_wake_gate",
        lambda rid, **k: calls.append((rid, k)) or False,
    )
    assert ws_phase._account_online(123) is False
    assert calls == [(123, {"threshold_sec": 60.0})]


def test_ws_phase_account_online_forwards_threshold(monkeypatch):
    calls = []
    monkeypatch.setattr(
        online_monitor, "account_online_for_wake_gate",
        lambda rid, **k: calls.append(k) or False,
    )
    assert ws_phase._account_online(123, threshold_sec=120.0) is False
    assert calls == [{"threshold_sec": 120.0}]


def _threshold_cfg(monkeypatch, threshold_sec):
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  threshold_sec if k == "online_check_threshold_sec"
                                  else d})())


def test_presence_threshold_sec_reads_device_config(monkeypatch):
    _threshold_cfg(monkeypatch, 120)
    assert ws_phase._presence_threshold_sec("dev") == 120.0
    _threshold_cfg(monkeypatch, None)
    assert ws_phase._presence_threshold_sec("dev") == 60.0


def test_wait_uses_presence_threshold_from_config(monkeypatch):
    """閘門等待用裝置的 online_check_threshold_sec 重算在線。"""
    _threshold_cfg(monkeypatch, 120)
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    seen = []
    monkeypatch.setattr(ws_phase, "_account_online",
                        lambda rid, **k: seen.append(k) or False)
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: None)
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger)
    assert seen == [{"threshold_sec": 120.0}]


# --- control_panel.ws_session.is_active ---------------------------------------


def test_ws_session_is_active_reflects_registry_all_owners(monkeypatch):
    # Phase 2：is_active 改讀 registry，涵蓋全 owner（不只工具/追蹤建立的純 WS，
    # 還含 SCHEDULER 等）——正是喚醒閘門要消除的盲區。
    from control_panel import ws_session as wss
    from runtime_services import session_registry as reg

    monkeypatch.setattr(reg, "_safe_set_pause", lambda dev, paused: None)
    try:
        assert wss.is_active("gate-dev") is False
        # 由 SCHEDULER（非工具建立）佔用，舊版 _sessions 盲區看不到，新版看得到。
        reg.acquire("gate-dev", reg.Owner.SCHEDULER, reg.Channel.WS)
        assert wss.is_active("gate-dev") is True
        assert wss.is_active("no-such-dev") is False
    finally:
        reg.release("gate-dev", reg.Owner.SCHEDULER)
