"""Phase 1 單元測試:session_registry 帳號佔用單一真相。

全程不碰真 socket / creds。protected 前置與 pause 配對都透過 seam monkeypatch。
"""
import threading

import pytest

import runtime_services.session_registry as reg


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """每個測試前清空 registry、屏蔽 protected 載入、記錄 set_pause 呼叫。"""
    with reg._lock:
        reg._leases.clear()
    # 預設:無保護帳號、無 human_played 裝置(個別測試可覆寫)。
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(reg, "_is_human_played_device", lambda dev: False)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(reg, "_safe_set_pause",
                        lambda dev, paused: calls.append((dev, paused)))
    yield calls
    with reg._lock:
        reg._leases.clear()


# --- acquire 基本 ------------------------------------------------------------

def test_acquire_free_device_succeeds():
    res = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, label="倉庫")
    assert res.ok is True
    assert res.lease is not None
    assert res.lease.owner is reg.Owner.TOOL
    assert res.lease.channel is reg.Channel.WS
    assert res.lease.label == "倉庫"
    assert reg.peek("d") is res.lease


def test_same_owner_reacquire_renews_not_conflict():
    r1 = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, label="a")
    r2 = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, label="b")
    assert r2.ok is True
    # 續租更新同一 device 的 lease(label 更新),不新增第二筆。
    assert r2.lease is not None
    assert r2.lease.label == "b"
    assert reg.peek("d").label == "b"
    # preempted Event 不因續租被觸發。
    assert not r1.lease.preempted.is_set()


def test_different_owner_without_preempt_conflicts():
    reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    res = reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
    assert res.ok is False
    assert res.conflict is not None
    assert res.conflict.owner is reg.Owner.MOUNT_TRACKER
    # 佔用者未變。
    assert reg.peek("d").owner is reg.Owner.MOUNT_TRACKER


# --- preempt 搶佔 ------------------------------------------------------------

def test_higher_priority_preempt_sets_event_and_takes_over():
    r_old = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    r_new = reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS, preempt=True)
    assert r_new.ok is True
    # 舊 lease 被通知讓位。
    assert r_old.lease.preempted.is_set()
    assert reg.peek("d").owner is reg.Owner.SCHEDULER


def test_preempt_flag_but_not_higher_priority_cannot_steal():
    # MOUNT_TRACKER(30) 想搶 ONLINE_MONITOR(40) → 優先權不足,即使 preempt=True。
    r_old = reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, preempt=True)
    assert res.ok is False
    assert res.conflict is not None
    assert not r_old.lease.preempted.is_set()
    assert reg.peek("d").owner is reg.Owner.ONLINE_MONITOR


def test_equal_priority_cannot_preempt():
    # ONLINE_MONITOR(40) vs ONLINE_CHECK(40):同級不得搶佔。
    reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
    res = reg.acquire("d", reg.Owner.ONLINE_CHECK, reg.Channel.WS, preempt=True)
    assert res.ok is False


# --- TOOL 人授權搶佔借用型(design §1.3 的唯一例外) ---------------------------

@pytest.mark.parametrize("borrower", [
    reg.Owner.ONLINE_MONITOR, reg.Owner.ONLINE_CHECK, reg.Owner.MOUNT_TRACKER])
def test_tool_preempt_takes_over_yielding_borrower(borrower):
    """人的手動工具操作(preempt=True)可搶佔任何借用型 owner;被搶者收到
    preempted 讓位(監控會自動換一台 detector)。修 2026-07-08 監控佔 5554
    80 分鐘、工具被鎖死只能乾等的實作落差。"""
    r_old = reg.acquire("d", borrower, reg.Channel.WS)
    r_new = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, preempt=True)
    assert r_new.ok is True
    assert r_old.lease.preempted.is_set()
    assert reg.peek("d").owner is reg.Owner.TOOL


def test_tool_preempt_cannot_steal_scheduler():
    # bot 主迴圈(SCHEDULER)不可被工具搶佔。
    reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS)
    res = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, preempt=True)
    assert res.ok is False
    assert reg.peek("d").owner is reg.Owner.SCHEDULER


def test_tool_without_preempt_still_conflicts_with_borrower():
    # 未帶 preempt 的 TOOL acquire 維持 conflict(自動升級在 ws_session.ensure)。
    reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
    res = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS)
    assert res.ok is False
    assert reg.peek("d").owner is reg.Owner.ONLINE_MONITOR


# --- protected 保護 ----------------------------------------------------------

def test_acquire_rejected_for_protected_role(monkeypatch):
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset({777}))
    res = reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS, role_id=777)
    assert res.ok is False
    assert res.reason == "protected"
    assert reg.peek("d") is None


def test_acquire_rejected_for_human_played_device(monkeypatch):
    monkeypatch.setattr(reg, "_is_human_played_device",
                        lambda dev: dev == "phone-fc")
    res = reg.acquire("phone-fc", reg.Owner.SCHEDULER, reg.Channel.WS)
    assert res.ok is False
    assert res.reason == "protected"


def test_tool_bypasses_protected_role(monkeypatch):
    # dashboard TOOL 是人明確操作(按「連線」),允許登入保護帳號。
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset({777}))
    res = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, role_id=777)
    assert res.ok is True


def test_tool_bypasses_human_played_device(monkeypatch):
    monkeypatch.setattr(reg, "_is_human_played_device",
                        lambda dev: dev == "phone-fc")
    res = reg.acquire("phone-fc", reg.Owner.TOOL, reg.Channel.WS)
    assert res.ok is True


def test_non_protected_role_still_acquires(monkeypatch):
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset({777}))
    res = reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS, role_id=123)
    assert res.ok is True


# --- release 冪等 ------------------------------------------------------------

def test_release_is_idempotent():
    reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS)
    reg.release("d", reg.Owner.TOOL)
    assert reg.peek("d") is None
    # 重複 release 不炸。
    reg.release("d", reg.Owner.TOOL)
    assert reg.peek("d") is None


def test_release_wrong_owner_does_not_evict():
    reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    reg.release("d", reg.Owner.TOOL)  # owner 不符 → 不動
    assert reg.peek("d") is not None
    assert reg.peek("d").owner is reg.Owner.MOUNT_TRACKER


# --- peek 無副作用 -----------------------------------------------------------

def test_peek_has_no_side_effect():
    reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS)
    before = reg.peek("d")
    after = reg.peek("d")
    assert before is after  # 回傳 live lease,無複製/無變更
    assert reg.peek("no-such") is None


def test_peek_all_snapshot_is_detached_mapping():
    reg.acquire("a", reg.Owner.TOOL, reg.Channel.WS)
    reg.acquire("b", reg.Owner.SCHEDULER, reg.Channel.H5)
    snap = reg.peek_all()
    assert set(snap) == {"a", "b"}
    # 對快照 dict 的變更不影響 registry。
    snap.pop("a")
    assert reg.peek("a") is not None


# --- 借用型 pause 配對(seam 記錄) ------------------------------------------

def test_borrowing_owner_pauses_on_acquire(_clean_registry):
    calls = _clean_registry
    reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS)
    assert ("d", True) in calls


def test_scheduler_does_not_pause_on_acquire(_clean_registry):
    calls = _clean_registry
    reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS)
    assert ("d", True) not in calls


def test_release_of_borrowing_owner_unpauses(_clean_registry):
    calls = _clean_registry
    reg.acquire("d", reg.Owner.TOOL, reg.Channel.WS)
    reg.release("d", reg.Owner.TOOL)
    assert ("d", False) in calls


def test_scheduler_preempt_unpauses_the_borrowed_device(_clean_registry):
    calls = _clean_registry
    reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)  # → pause True
    calls.clear()
    reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS, preempt=True)
    # 舊借用被搶佔 → 恢復 bot loop;SCHEDULER 本身不再額外 pause True。
    assert ("d", False) in calls
    assert ("d", True) not in calls


# --- check_wake 即將喚醒 / OFFLINE 空窗保守閘門(修 bug#2) --------------------

def test_check_wake_borrows_far_future_wake(monkeypatch):
    # 明確休眠中 + next_wake_at 遠在 120s 之外 → 借用型可借。
    monkeypatch.setattr(reg, "_now", lambda: 1000.0)
    monkeypatch.setattr(
        reg, "_device_state",
        lambda dev: {"task": "休眠中", "next_wake_at": 9_999_999.0})
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is True


def test_check_wake_refuses_about_to_wake(monkeypatch):
    # next_wake_at = now + 60s(< 120s lead)→ 即將自我喚醒,拒絕借用。
    monkeypatch.setattr(reg, "_now", lambda: 1000.0)
    monkeypatch.setattr(
        reg, "_device_state",
        lambda dev: {"task": "休眠中", "next_wake_at": 1060.0})
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is False
    assert res.reason == "about_to_wake"
    assert reg.peek("d") is None


def test_check_wake_refuses_offline_window_no_next_wake(monkeypatch):
    # bug#2:OFFLINE 空窗(status OFFLINE、task 非休眠中、next_wake_at 被 pop)→ 保守拒絕。
    monkeypatch.setattr(reg, "_now", lambda: 1000.0)
    monkeypatch.setattr(
        reg, "_device_state",
        lambda dev: {"status": "OFFLINE", "task": "每日任務"})
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is False
    assert res.reason == "about_to_wake"


def test_check_wake_refuses_missing_state_row(monkeypatch):
    # 無 state row(thread 未起 / 不明)+ check_wake → 保守拒絕(不再誤判可借)。
    monkeypatch.setattr(reg, "_device_state", lambda dev: None)
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is False
    assert res.reason == "about_to_wake"


def test_check_wake_allows_sleeping_without_next_wake(monkeypatch):
    # 明確休眠中但暫無 next_wake_at(force_sleep 剛把 task 設休眠中並 pop 喚醒時刻)→ 安全。
    monkeypatch.setattr(reg, "_device_state", lambda dev: {"task": "休眠中"})
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is True


def test_check_wake_ignored_without_flag(monkeypatch):
    # 不帶 check_wake(預設 False)→ 不讀 bot_state,一律照舊可借(Phase 1/2 相容)。
    called = []
    monkeypatch.setattr(reg, "_device_state",
                        lambda dev: called.append(dev) or None)
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS)
    assert res.ok is True
    assert called == []  # 未帶 flag → 完全不觸碰 wake 判定


def test_check_wake_not_applied_to_scheduler(monkeypatch):
    # SCHEDULER 非借用型:即使帶 check_wake,也不套即將喚醒閘門(它就是喚醒本體)。
    monkeypatch.setattr(reg, "_device_state", lambda dev: None)
    res = reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS, check_wake=True)
    assert res.ok is True


def test_check_wake_occupied_returns_conflict_not_wake(monkeypatch):
    # 裝置被別人佔用時,回 conflict(語意更精確),而非 about_to_wake。
    monkeypatch.setattr(reg, "_device_state", lambda dev: None)
    reg.acquire("d", reg.Owner.SCHEDULER, reg.Channel.WS)
    res = reg.acquire("d", reg.Owner.MOUNT_TRACKER, reg.Channel.WS, check_wake=True)
    assert res.ok is False
    assert res.conflict is not None
    assert res.reason is None


# --- thread safety 基本 ------------------------------------------------------

def test_concurrent_acquire_single_winner():
    winners: list[bool] = []
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        res = reg.acquire("d", reg.Owner.ONLINE_MONITOR, reg.Channel.WS)
        winners.append(res.ok)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 同 owner:全部成功(續租),但 registry 只有一筆。
    assert all(winners)
    assert reg.peek("d").owner is reg.Owner.ONLINE_MONITOR


def test_concurrent_distinct_owners_exactly_one_holds():
    results: list = []
    barrier = threading.Barrier(4)
    owners = [reg.Owner.ONLINE_MONITOR, reg.Owner.ONLINE_CHECK,
              reg.Owner.MOUNT_TRACKER, reg.Owner.TOOL]

    def worker(owner):
        barrier.wait()
        results.append(reg.acquire("d", owner, reg.Channel.WS))

    threads = [threading.Thread(target=worker, args=(o,)) for o in owners]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    oks = [r for r in results if r.ok]
    assert len(oks) == 1  # 恰一個先到先得,其餘 conflict
    assert reg.peek("d") is not None
