"""Background pure-WS online-check server (decouple from the wake loop).

`runtime_services/online_check_service.py` is a single master-only thread that
polls `bot_state` for pending online-check requests and answers them over a
one-shot WS login (`runtime_services.ws_online_checker.check_via_ws`) using an
*idle* (sleeping) checker — so no device ever wakes / cold-starts a browser to
serve an online-check (the old SKIP_SLEEP-every-checker path restarted web_h5
devices every ~30s).

These tests drive the real `bot_state` mailbox with `check_via_ws` and the
config checker-list / device-config monkeypatched, so they never touch a real
device, Playwright, OCR, or the network.
"""
from __future__ import annotations

import bot_state
import config_manager
import pytest

import runtime_services.session_registry as reg
from runtime_services import online_check_service as svc
from runtime_services.session_registry import Channel, Owner


@pytest.fixture(autouse=True)
def _clean_mailbox(monkeypatch):
    def _reset():
        with bot_state._global_lock:
            bot_state._online_check_requests.clear()
            bot_state._online_check_pending.clear()
            bot_state._signals.clear()
            bot_state._refresh_needed = False
        with reg._lock:
            reg._leases.clear()
    _reset()
    # Neutralise registry protected/pause seams so short-lease acquire/release
    # in the slow path stays hermetic (no real bot_state pause, no config read).
    monkeypatch.setattr(reg, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(reg, "_is_human_played_device", lambda dev: False)
    monkeypatch.setattr(reg, "_safe_set_pause", lambda dev, paused: None)
    yield
    _reset()


def _set_checkers(monkeypatch, lst):
    monkeypatch.setattr(
        config_manager, "get_online_check_checkers", lambda: list(lst))


def _all_idle(monkeypatch):
    # Empty state snapshot → every checker is "absent" → idle (no live session
    # to kick). Mirrors a freshly-started master before any device thread runs.
    monkeypatch.setattr(bot_state, "get_all_states", lambda: {})


def _stub_config(monkeypatch):
    monkeypatch.setattr(config_manager, "get_device_config", lambda ip: {})


def _no_shuffle(monkeypatch):
    # Neutralise the random shuffle so candidate order == configured order,
    # keeping fall-through order deterministic for tests that assert it.
    monkeypatch.setattr(svc.random, "shuffle", lambda seq: None)


# ---------------------------------------------------------------------------
# definite answers complete the request
# ---------------------------------------------------------------------------

def test_idle_checker_busy_true_completes(monkeypatch):
    _set_checkers(monkeypatch, ["emulator-5554"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    monkeypatch.setattr(svc, "check_via_ws", lambda c, pid, log, **kw: True)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done"
    assert res["result_busy"] is True


def test_idle_checker_offline_false_completes(monkeypatch):
    _set_checkers(monkeypatch, ["emulator-5554"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    monkeypatch.setattr(svc, "check_via_ws", lambda c, pid, log, **kw: False)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done"
    assert res["result_busy"] is False


# ---------------------------------------------------------------------------
# undetermined (None) → try the next checker; only fail when ALL are None
# ---------------------------------------------------------------------------

def test_first_checker_none_falls_through_to_next(monkeypatch):
    _set_checkers(monkeypatch, ["c1", "c2"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    _no_shuffle(monkeypatch)               # deterministic order to assert fall-through

    seen = []

    def _check(c, pid, log, **kw):
        seen.append(c)
        return None if c == "c1" else False
    monkeypatch.setattr(svc, "check_via_ws", _check)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert seen == ["c1", "c2"]            # tried c1, then c2
    assert res["status"] == "done"
    assert res["result_busy"] is False


def test_spreads_across_idle_checkers(monkeypatch):
    # Each request stops at its first (random) idle checker, since every checker
    # returns a definite answer. Over many requests the first-tried checker
    # varies → WS-login load is spread, not pinned to the configured head.
    _set_checkers(monkeypatch, ["c1", "c2", "c3"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)

    first_tried: set = set()

    def _check(c, pid, log, **kw):
        first_tried.add(c)
        return False                       # definite → stops at the first tried
    monkeypatch.setattr(svc, "check_via_ws", _check)

    for _ in range(50):
        req_id = bot_state.submit_online_check_request(
            requester_ip="emulator-5558", target_pid=123)
        svc._serve_pending_once()
        bot_state.wait_online_check_result(req_id, timeout_sec=0.5)

    assert len(first_tried) >= 2            # randomised, not pinned to one checker


def test_all_checkers_none_fails_request(monkeypatch):
    _set_checkers(monkeypatch, ["c1", "c2"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    monkeypatch.setattr(svc, "check_via_ws", lambda c, pid, log, **kw: None)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "failed"       # requester retries next round
    assert res["result_busy"] is None      # never 放行 on undetermined


# ---------------------------------------------------------------------------
# never log in with a busy checker (would kick its live session)
# ---------------------------------------------------------------------------

def test_busy_checker_is_skipped(monkeypatch):
    _set_checkers(monkeypatch, ["busy1", "idle2"])
    # busy1 is mid-task → must NOT be used; idle2 absent → idle.
    monkeypatch.setattr(
        bot_state, "get_all_states", lambda: {"busy1": {"task": "挖礦中"}})
    _stub_config(monkeypatch)

    seen = []

    def _check(c, pid, log, **kw):
        seen.append(c)
        return True
    monkeypatch.setattr(svc, "check_via_ws", _check)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    assert seen == ["idle2"]               # busy1 never logged in
    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done" and res["result_busy"] is True


def test_no_idle_checker_leaves_request_pending(monkeypatch):
    _set_checkers(monkeypatch, ["busy1"])
    monkeypatch.setattr(
        bot_state, "get_all_states", lambda: {"busy1": {"task": "喚醒檢查"}})
    _stub_config(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("must not log in when no checker is idle")
    monkeypatch.setattr(svc, "check_via_ws", _boom)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()              # no idle checker → no-op

    # Request is untouched: still pending, claimable next round.
    assert bot_state.has_pending_online_check_request("busy1") is True
    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.1)
    assert res["status"] == "pending"


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------

def test_missing_target_pid_fails(monkeypatch):
    _set_checkers(monkeypatch, ["emulator-5554"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("must not log in without a target_pid")
    monkeypatch.setattr(svc, "check_via_ws", _boom)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=None)
    svc._serve_pending_once()

    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.1)
    assert res["status"] == "failed"


def test_ensure_started_is_idempotent(monkeypatch):
    threads = []

    class _FakeThread:
        def __init__(self, *a, **k):
            self._alive = True

        def start(self):
            threads.append(self)

        def is_alive(self):
            return self._alive

    monkeypatch.setattr(svc.threading, "Thread", _FakeThread)
    # Reset the module singleton so the test is order-independent.
    monkeypatch.setattr(svc, "_thread", None)
    monkeypatch.setattr(svc, "_started", False)

    assert svc.ensure_online_check_service_started() is True
    assert svc.ensure_online_check_service_started() is True
    assert len(threads) == 1               # second call is a no-op


# ---------------------------------------------------------------------------
# session_registry 接線 (Phase 4)
# ---------------------------------------------------------------------------

def test_checker_held_by_registry_is_not_idle(monkeypatch):
    """bug#4:checker 被別的 owner 佔用(即使 bot_state 顯示 idle)→ 不可借。"""
    _all_idle(monkeypatch)  # bot_state 空 → 薄弱判定會誤判 idle
    reg.acquire("emulator-5554", Owner.SCHEDULER, Channel.WS)
    assert svc._is_idle("emulator-5554", bot_state.get_all_states()) is False
    # 未被佔用者仍 idle。
    assert svc._is_idle("emulator-5556", bot_state.get_all_states()) is True


def test_occupied_checker_excluded_from_candidates(monkeypatch):
    _set_checkers(monkeypatch, ["busy-reg", "free1"])
    _all_idle(monkeypatch)
    reg.acquire("busy-reg", Owner.ONLINE_MONITOR, Channel.WS)  # 監控 detector 佔用
    assert svc._idle_checkers() == ["free1"]


def test_slow_path_acquires_and_releases_short_lease(monkeypatch):
    """一次性 WS 登入期間持 ONLINE_CHECK lease,完成後 finally release(不殘留)。"""
    _set_checkers(monkeypatch, ["emulator-5554"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    monkeypatch.setattr(svc, "_check_monitor_snapshot", lambda pid, thr: None)  # 走慢路徑

    held = {}

    def _check(c, pid, log, **kw):
        lease = reg.peek(c)                 # 登入當下應握有 lease
        held["owner"] = lease.owner if lease else None
        return True
    monkeypatch.setattr(svc, "check_via_ws", _check)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()

    assert held["owner"] is Owner.ONLINE_CHECK
    assert reg.peek("emulator-5554") is None   # 完成後已 release
    res = bot_state.wait_online_check_result(req_id, timeout_sec=0.5)
    assert res["status"] == "done" and res["result_busy"] is True


def test_slow_path_releases_lease_even_on_error(monkeypatch):
    """check_via_ws 拋例外時,lease 仍在 finally release,不卡佔用。"""
    _set_checkers(monkeypatch, ["emulator-5554"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    monkeypatch.setattr(svc, "_check_monitor_snapshot", lambda pid, thr: None)

    def _boom(c, pid, log, **kw):
        raise RuntimeError("ws down")
    monkeypatch.setattr(svc, "check_via_ws", _boom)

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123)
    svc._serve_pending_once()  # 例外由 _serve_pending_once 的 try 吞掉

    assert reg.peek("emulator-5554") is None   # 例外路徑仍釋放


def test_slow_path_skips_checker_grabbed_by_scheduler(monkeypatch):
    """挑到 checker 後、登入前若被 SCHEDULER 搶走(TOCTOU)→ acquire conflict → 換下一台。"""
    _set_checkers(monkeypatch, ["c1", "c2"])
    _all_idle(monkeypatch)
    _stub_config(monkeypatch)
    _no_shuffle(monkeypatch)
    monkeypatch.setattr(svc, "_check_monitor_snapshot", lambda pid, thr: None)
    # c1 在候選產生後、_serve_one 之前被別的服務搶走佔用。
    reg.acquire("c1", Owner.SCHEDULER, Channel.WS)

    seen = []

    def _check(c, pid, log, **kw):
        seen.append(c)
        return False
    monkeypatch.setattr(svc, "check_via_ws", _check)

    # 直接 serve 一個 req,candidates 傳入含 c1(模擬 TOCTOU：候選已算好但 c1 剛被搶)。
    req = {"id": bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=123), "target_pid": 123,
        "requester_ip": "emulator-5558"}
    svc._serve_one(req, ["c1", "c2"])

    assert seen == ["c2"]                       # c1 被佔用 → 未登入,直接跳到 c2
    assert reg.peek("c2") is None               # c2 短租已釋放
