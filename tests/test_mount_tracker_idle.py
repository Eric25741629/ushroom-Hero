"""Task 4：idle 裝置借用判定測試。

全程 monkeypatch 間接層（_states / _ws_active / _protected_roles / _now），
不碰真裝置 / creds / ws_session。
"""
import runtime_services.mount_tracker_service as mt


def test_sleeping_device_is_safe(monkeypatch):
    monkeypatch.setattr(mt, "_states", lambda: {"d": {"task": "休眠中"}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: set())
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is True


def test_online_device_is_busy(monkeypatch):
    # task = 一般任務（非休眠）→ 有 live session，不可借。
    monkeypatch.setattr(mt, "_states", lambda: {"d": {"task": "每日任務"}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: set())
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is False


def test_about_to_wake_is_skipped(monkeypatch):
    # next_wake_at = now + 60s（< 120s lead）→ 讓位給自身喚醒。
    monkeypatch.setattr(
        mt, "_states", lambda: {"d": {"task": "休眠中", "next_wake_at": 1060.0}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: set())
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is False


def test_far_wake_is_safe(monkeypatch):
    # next_wake_at 遠在 120s 之外 → 仍可借。
    monkeypatch.setattr(
        mt, "_states", lambda: {"d": {"task": "休眠中", "next_wake_at": 9_999_999.0}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: set())
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is True


def test_held_by_ws_session_is_skipped(monkeypatch):
    # dashboard 已持有純 WS 連線 → 借用會互踢，跳過。
    monkeypatch.setattr(mt, "_states", lambda: {"d": {"task": "休眠中"}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: True)
    monkeypatch.setattr(mt, "_protected_roles", lambda: set())
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is False


def test_protected_role_is_skipped(monkeypatch):
    # 有保護集合且該裝置 roleId 在其中 → 絕不借。
    monkeypatch.setattr(mt, "_states", lambda: {"d": {"task": "休眠中"}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: {777})
    monkeypatch.setattr(mt, "_role_id", lambda ip: 777)
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is False


def test_protected_set_but_no_creds_is_skipped(monkeypatch):
    # 有保護集合但讀不出 roleId → 無法確認非保護帳號，保守跳過。
    monkeypatch.setattr(mt, "_states", lambda: {"d": {"task": "休眠中"}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: {777})
    monkeypatch.setattr(mt, "_role_id", lambda ip: None)
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is False


def test_pick_idle_returns_first_safe(monkeypatch):
    safe = {"emulator-5556"}
    monkeypatch.setattr(mt, "is_safe_to_borrow", lambda ip: ip in safe)
    assert mt.pick_idle_device(["7fe98fc6", "emulator-5556", "emulator-5560"]) == "emulator-5556"


def test_pick_idle_none_when_all_busy(monkeypatch):
    monkeypatch.setattr(mt, "is_safe_to_borrow", lambda ip: False)
    assert mt.pick_idle_device(["a", "b"]) is None
