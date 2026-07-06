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
    res = reg.acquire("phone-fc", reg.Owner.TOOL, reg.Channel.WS)
    assert res.ok is False
    assert res.reason == "protected"


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
