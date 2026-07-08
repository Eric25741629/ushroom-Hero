"""Phase 2:ws_session 改走 session_registry。

不碰真 socket / creds:stub 掉 load_creds 與 WSGameClient,seam 掉 registry 的
set_pause。驗證 ensure 先取權再連、conflict 不建連、disconnect/sweeper 釋放 lease、
is_active 涵蓋全 owner。
"""
import pytest

from control_panel import ws_session as wss
from runtime_services import session_registry as reg


class _FakeClient:
    """假 WSGameClient:connect/close/is_running 皆可控。"""

    def __init__(self, creds, *, connect_ok=True, running=True):
        self.creds = creds
        self._connect_ok = connect_ok
        self._running = running
        self.kick_handler = None
        self.closed = False

    def set_kick_handler(self, fn):
        self.kick_handler = fn

    def connect(self):
        if not self._connect_ok:
            raise RuntimeError("boom")

    def is_running(self):
        return self._running

    def close(self):
        self.closed = True


class _Creds:
    def __init__(self, role_id=123):
        self.role_id = role_id


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with reg._lock:
        reg._leases.clear()
    with wss._lock:
        wss._sessions.clear()
    # 屏蔽保護載入與真實 pause 副作用。
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(reg, "_is_human_played_device", lambda dev: False)
    monkeypatch.setattr(reg, "_safe_set_pause", lambda dev, paused: None)
    monkeypatch.setattr(wss, "load_creds", lambda dev: _Creds())
    yield
    with reg._lock:
        reg._leases.clear()
    with wss._lock:
        wss._sessions.clear()


def _use_client(monkeypatch, **kw):
    monkeypatch.setattr(wss, "WSGameClient",
                        lambda creds: _FakeClient(creds, **kw))


def test_ensure_acquires_lease_then_connects(monkeypatch):
    _use_client(monkeypatch)
    res = wss.ensure("d")
    assert res["status"] == "ok"
    lease = reg.peek("d")
    assert lease is not None and lease.owner is reg.Owner.TOOL
    assert lease.channel is reg.Channel.WS
    assert "d" in wss._sessions


def test_ensure_conflict_does_not_connect(monkeypatch):
    # 先由 SCHEDULER 佔用 → TOOL(最低優先)無法搶 → conflict,不建連。
    reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS)
    created = []
    monkeypatch.setattr(
        wss, "WSGameClient",
        lambda creds: created.append(creds) or _FakeClient(creds))
    res = wss.ensure("d")
    assert res["status"] == "conflict"
    assert res["conflict"]["owner"] == "scheduler"
    assert created == []  # 沒有嘗試連線
    assert "d" not in wss._sessions
    # 現任未變。
    assert reg.peek("d").owner is reg.Owner.SCHEDULER


def test_ensure_connect_failure_releases_lease(monkeypatch):
    _use_client(monkeypatch, connect_ok=False)
    res = wss.ensure("d")
    assert res["status"] == "error"
    # 連線失敗要把剛拿的 lease 還掉,避免裝置永久卡佔用/暫停。
    assert reg.peek("d") is None
    assert "d" not in wss._sessions


def test_ensure_missing_creds_no_lease(monkeypatch):
    monkeypatch.setattr(wss, "load_creds",
                        lambda dev: (_ for _ in ()).throw(FileNotFoundError()))
    _use_client(monkeypatch)
    res = wss.ensure("d")
    assert res["status"] == "error"
    assert reg.peek("d") is None


def test_reacquire_running_session_renews(monkeypatch):
    _use_client(monkeypatch)
    wss.ensure("d")
    first = wss._sessions["d"].client
    res = wss.ensure("d")
    assert res["status"] == "ok"
    # 同一條連線被復用,未重建。
    assert wss._sessions["d"].client is first
    assert reg.peek("d").owner is reg.Owner.TOOL


def test_disconnect_releases_lease(monkeypatch):
    _use_client(monkeypatch)
    wss.ensure("d")
    assert reg.peek("d") is not None
    wss.disconnect("d")
    assert reg.peek("d") is None
    assert "d" not in wss._sessions
    # 冪等。
    wss.disconnect("d")
    assert reg.peek("d") is None


def test_sweep_releases_dead_session(monkeypatch):
    _use_client(monkeypatch, running=False)
    # 直接塞一個已死(is_running False)的 session + 對應 lease。
    reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS)
    with wss._lock:
        wss._sessions["d"] = wss._Session(
            client=_FakeClient(_Creds(), running=False),
            last_seen=0.0, owner=reg.Owner.TOOL)
    wss._sweep_once()
    assert "d" not in wss._sessions
    assert reg.peek("d") is None


def test_is_active_covers_all_owners():
    assert wss.is_active("d") is False
    reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    assert wss.is_active("d") is True


def test_ensure_tool_auto_preempts_yielding_borrower(monkeypatch):
    """工具撞到借用型佔用(在線監控等)→ 自動 preempt 重試,連線成功;
    被搶的借用者收到 preempted 讓位。修「監控佔 5554 期間工具全被擋」。"""
    _use_client(monkeypatch)
    r_mon = reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
    res = wss.ensure("d")
    assert res["status"] == "ok"
    assert reg.peek("d").owner is reg.Owner.TOOL
    assert r_mon.lease.preempted.is_set()
    assert "d" in wss._sessions


def test_ensure_borrower_owner_does_not_auto_preempt(monkeypatch):
    """自動升級只給人的 TOOL:借用型 owner(坐騎追蹤)撞到監控仍是 conflict。"""
    created = []
    monkeypatch.setattr(
        wss, "WSGameClient",
        lambda creds: created.append(creds) or _FakeClient(creds))
    reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
    res = wss.ensure("d", owner=reg.Owner.MOUNT_TRACKER)
    assert res["status"] == "conflict"
    assert created == []
    assert reg.peek("d").owner is reg.Owner.ONLINE_MONITOR


def test_ensure_check_wake_skips_about_to_wake(monkeypatch):
    # 借用型 + check_wake:裝置即將自我喚醒 / 空窗 → skip,不建連、不佔 lease。
    created = []
    monkeypatch.setattr(
        wss, "WSGameClient",
        lambda creds: created.append(creds) or _FakeClient(creds))
    # registry 判定該裝置 wake-unsafe(無 state row → 保守拒絕)。
    monkeypatch.setattr(reg, "_device_state", lambda dev: None)
    res = wss.ensure("d", owner=reg.Owner.MOUNT_TRACKER, check_wake=True)
    assert res["status"] == "skip"
    assert created == []            # 未嘗試連線
    assert reg.peek("d") is None    # 未佔 lease
    assert "d" not in wss._sessions
