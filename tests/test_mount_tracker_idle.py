"""Phase 3：坐騎追蹤借用改走 session_registry 的接線測試。

借用「安全判定」（未被佔用 + 非保護帳號 + 非即將喚醒/空窗）已**原子收斂進
registry.acquire**（經 ws_session.ensure(check_wake=True) 觸發），消掉舊 mount-tracker
自製 _is_idle/_ws_active/_protected_roles 的 TOCTOU 與 OFFLINE 空窗誤判 bug#2。

這裡驗證 mount-tracker 端接線：
- ``_get_ws_client`` 以 owner=MOUNT_TRACKER + check_wake=True 取用；被拒（conflict /
  skip）即回 None → 呼叫端跳過該候選；取用成功才回呼 on_ensure 登記借用。
- ``_still_hold`` 續用判定 poll lease.preempted / owner，被 SCHEDULER 搶佔即讓位。
- ``_about_to_wake`` 續用安全網。
- MOUNT_TRACKER 借用owner 對 OFFLINE 空窗 / human_played 一律被 registry 拒絕。
"""
import pytest

import runtime_services.mount_tracker_service as mt
import runtime_services.session_registry as reg
from control_panel import ws_session as wss


@pytest.fixture(autouse=True)
def _reg(monkeypatch):
    """清空 registry、屏蔽 protected 載入 + pause 副作用；_device_state 預設無 row。"""
    with reg._lock:
        reg._leases.clear()
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(reg, "_is_human_played_device", lambda dev: False)
    monkeypatch.setattr(reg, "_safe_set_pause", lambda dev, paused: None)
    monkeypatch.setattr(reg, "_device_state", lambda dev: None)
    yield
    with reg._lock:
        reg._leases.clear()


# --- _get_ws_client 借用取用（ws_session.ensure → registry.acquire）----------

def test_get_ws_client_acquires_as_mount_tracker(monkeypatch):
    calls = {}

    def fake_ensure(dev, *, owner, preempt, check_wake):
        calls.update(owner=owner, preempt=preempt, check_wake=check_wake)
        return {"status": "ok"}

    monkeypatch.setattr(wss, "ensure", fake_ensure)
    monkeypatch.setattr(wss, "get_client", lambda dev: "CLIENT")
    seen = []
    client = mt._get_ws_client("d", on_ensure=lambda x: seen.append(x))
    assert client == "CLIENT"
    # 一律以借用型 owner + 不搶佔 + 即將喚醒閘門取用。
    assert calls == {"owner": reg.Owner.MOUNT_TRACKER,
                     "preempt": False, "check_wake": True}
    assert seen == ["d"]  # 取用成功當下即登記借用（防洩漏）


def test_get_ws_client_skip_returns_none_no_borrow(monkeypatch):
    # 即將自我喚醒 / 空窗 → ensure 回 skip → 跳過候選,不登記借用。
    monkeypatch.setattr(wss, "ensure", lambda dev, **k: {"status": "skip"})
    seen = []
    assert mt._get_ws_client("d", on_ensure=lambda x: seen.append(x)) is None
    assert seen == []


def test_get_ws_client_conflict_returns_none_no_borrow(monkeypatch):
    # 被別的 owner 佔用 → ensure 回 conflict → 跳過候選。
    monkeypatch.setattr(wss, "ensure", lambda dev, **k: {"status": "conflict"})
    seen = []
    assert mt._get_ws_client("d", on_ensure=lambda x: seen.append(x)) is None
    assert seen == []


def test_get_ws_client_marks_borrow_even_if_client_none(monkeypatch):
    # ensure ok（已取得 lease）但 get_client 回 None（連上瞬間死）→ 仍登記借用,收尾歸還。
    monkeypatch.setattr(wss, "ensure", lambda dev, **k: {"status": "ok"})
    monkeypatch.setattr(wss, "get_client", lambda dev: None)
    seen = []
    assert mt._get_ws_client("d", on_ensure=lambda x: seen.append(x)) is None
    assert seen == ["d"]  # lease 已握住 → 必須登記,避免洩漏


# --- _still_hold 續用判定（poll lease.preempted / owner）---------------------

def test_still_hold_true_while_holding():
    reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    assert mt._still_hold("d") is True


def test_still_hold_false_without_lease():
    assert mt._still_hold("d") is False


def test_still_hold_false_when_preempted_flag_set():
    r = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    r.lease.preempted.set()  # 模擬被 SCHEDULER 搶佔通知
    assert mt._still_hold("d") is False


def test_still_hold_false_when_scheduler_took_over():
    reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS, preempt=True)  # 搶佔改寫 lease
    assert mt._still_hold("d") is False  # owner 已非 MOUNT_TRACKER → 讓位


# --- _about_to_wake 續用安全網 ----------------------------------------------

def test_about_to_wake_within_lead(monkeypatch):
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt._about_to_wake("d", {"d": {"next_wake_at": 1060.0}}) is True


def test_about_to_wake_far_future(monkeypatch):
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt._about_to_wake("d", {"d": {"next_wake_at": 9_999_999.0}}) is False


def test_about_to_wake_missing_next_wake_is_false():
    # 續用安全網:已持有借用的裝置丟失 next_wake_at 不視為即將喚醒（嚴格保守在 acquire 才判）。
    assert mt._about_to_wake("d", {"d": {}}) is False


# --- 借用 owner 被 registry 硬擋（OFFLINE 空窗 / human_played）---------------

def test_offline_window_not_borrowable(monkeypatch):
    # bug#2:OFFLINE 空窗（無 next_wake_at + task 非休眠中）→ MOUNT_TRACKER 取用被拒。
    monkeypatch.setattr(reg, "_device_state",
                        lambda dev: {"status": "OFFLINE", "task": "每日任務"})
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is False
    assert res.reason == "about_to_wake"


def test_human_played_not_borrowable(monkeypatch):
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset({777}))
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS,
                      role_id=777, check_wake=True)
    assert res.ok is False
    assert res.reason == "protected"
