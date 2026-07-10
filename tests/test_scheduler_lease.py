"""Task 4: 喚醒路徑 SCHEDULER lease 取得/等待/搶回/釋放。"""
import pytest

from runtime_services import session_registry as registry
from runtime_services.session_registry import Channel, Owner


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """每測隔離 registry 狀態 + 關掉外部 seam。"""
    monkeypatch.setattr(registry, "_leases", {})
    monkeypatch.setattr(registry, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(registry, "_is_human_played_device", lambda d: False)
    monkeypatch.setattr(registry, "_safe_set_pause", lambda d, p: None)
    yield


@pytest.fixture()
def ws_phase(monkeypatch):
    from game_actions import ws_phase as mod
    monkeypatch.setattr(mod, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def test_acquire_idle_registers_scheduler(ws_phase):
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)
    lease = registry.peek("dev")
    assert lease is not None and lease.owner is Owner.SCHEDULER


def test_acquire_preempts_yielding_borrower(ws_phase):
    registry.acquire("dev", Owner.MOUNT_TRACKER, Channel.WS, label="追蹤")
    borrower = registry.peek("dev")
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)
    assert registry.peek("dev").owner is Owner.SCHEDULER
    assert borrower.preempted.is_set()


def test_acquire_waits_for_tool_release(ws_phase, monkeypatch):
    registry.acquire("dev", Owner.TOOL, Channel.WS, label="工具")
    polls = {"n": 0}

    def fake_sleep(sec):
        polls["n"] += 1
        if polls["n"] >= 2:
            registry.release("dev", Owner.TOOL)  # 第二輪 poll 後工具釋放

    monkeypatch.setattr(ws_phase.time, "sleep", fake_sleep)
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)
    assert registry.peek("dev").owner is Owner.SCHEDULER
    assert polls["n"] >= 2


def test_acquire_web_launch_interrupts_tool_wait(ws_phase, monkeypatch):
    registry.acquire("dev", Owner.TOOL, Channel.WS, label="工具")
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: True)
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)  # 不可 hang
    assert registry.peek("dev").owner is Owner.TOOL  # 放行但未搶佔


def test_acquire_protected_passes_without_lease(ws_phase, monkeypatch):
    monkeypatch.setattr(registry, "_is_human_played_device", lambda d: True)
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)  # 不可 hang / 不可 raise
    assert registry.peek("dev") is None


def test_sleep_entry_releases_scheduler_lease():
    from runtime_services.sleep_service import _release_scheduler_lease
    registry.acquire("dev", Owner.SCHEDULER, Channel.WS, label="喚醒週期")
    _release_scheduler_lease("dev")
    assert registry.peek("dev") is None
    _release_scheduler_lease("dev")  # 冪等，不 raise
