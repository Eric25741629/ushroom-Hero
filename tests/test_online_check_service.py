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

from runtime_services import online_check_service as svc


@pytest.fixture(autouse=True)
def _clean_mailbox():
    def _reset():
        with bot_state._global_lock:
            bot_state._online_check_requests.clear()
            bot_state._online_check_pending.clear()
            bot_state._signals.clear()
            bot_state._refresh_needed = False
    _reset()
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

    svc.ensure_online_check_service_started()
    svc.ensure_online_check_service_started()
    assert len(threads) == 1               # second call is a no-op
