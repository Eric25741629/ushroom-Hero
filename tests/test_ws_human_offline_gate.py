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
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid: False)
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger)
    assert slept == []  # 確認離線 → 不等待


def test_wait_loops_until_human_goes_offline(monkeypatch):
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    seq = iter([True, True, False])
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid: next(seq))
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger)
    assert slept == [ws_phase._HUMAN_WAIT_POLL_SEC] * 2  # 在線兩輪 → 等兩次


def test_wait_human_played_treats_unknown_as_online(monkeypatch):
    """human_played：觀察者看不到(None)→ 當作可能在線、無限等(使用者 2026-06-25 指定)。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    seq = iter([None, None, False])
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid: next(seq))
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=True)
    assert slept == [ws_phase._HUMAN_WAIT_POLL_SEC] * 2


def test_wait_bot_device_best_effort_releases_after_max_polls(monkeypatch):
    """非 human_played：觀察者一直看不到(None)→ 重查上限後 best-effort 放行,不卡死。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid: None)  # 永遠看不到
    slept = []
    monkeypatch.setattr(ws_phase.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("bot_state.update_state", lambda *a, **k: None)
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=False)
    # 重查 _UNDETERMINED_MAX_POLLS 輪(各睡一次)後第 N+1 輪放行
    assert slept == [ws_phase._HUMAN_WAIT_POLL_SEC] * ws_phase._UNDETERMINED_MAX_POLLS


def test_wait_releases_immediately_when_device_is_detector(monkeypatch):
    """本裝置正是當前偵測器 → 直接放行,連 presence 都不查。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: "dev")
    called = []
    monkeypatch.setattr(ws_phase, "_account_online",
                        lambda rid: called.append(rid))
    monkeypatch.setattr(ws_phase.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("不該等")))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=False)
    assert called == []


def test_wait_releases_immediately_on_web_launch_request(monkeypatch):
    """等待中使用者按「開啟網頁」→ 立即放行(即使帳號還在線)。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: 123)
    monkeypatch.setattr(ws_phase, "_current_detector", lambda: None)
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: True)
    monkeypatch.setattr(ws_phase, "_account_online", lambda rid: True)  # 真人在玩
    monkeypatch.setattr(ws_phase.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("不該等")))
    ws_phase._wait_until_human_offline("dev", ws_phase.logger, human_played=True)


def test_wait_passes_through_when_no_creds(monkeypatch):
    """解不出 roleId(無 creds)→ 直接放行,連 presence 都不查。"""
    monkeypatch.setattr(ws_phase, "_account_role_id", lambda ip: None)
    called = []
    monkeypatch.setattr(ws_phase, "_account_online",
                        lambda rid: called.append(rid))
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
        entries=tuple(online_monitor.StatusEntry(rid, str(rid), on, None)
                      for rid, on in entries))


def test_account_online_fresh_snapshot(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot",
                        lambda: _snap(1000.0, [(123, True), (456, False)]))
    assert online_monitor.account_online(123, now=1030.0) is True
    assert online_monitor.account_online(456, now=1030.0) is False


def test_account_online_unknown_when_not_in_friend_list(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot",
                        lambda: _snap(1000.0, [(123, True)]))
    assert online_monitor.account_online(999, now=1030.0) is None


def test_account_online_unknown_when_stale(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot",
                        lambda: _snap(1000.0, [(123, True)]))
    assert online_monitor.account_online(123, now=1000.0 + 61) is None


def test_account_online_unknown_when_no_snapshot(monkeypatch):
    monkeypatch.setattr(online_monitor, "get_snapshot", lambda: None)
    assert online_monitor.account_online(123, now=1030.0) is None


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
