"""Decoupled cross-device online-check: checker is no longer hardcoded to 5554.

Covers the 2026-06-09 generalization (todo.md S5b):
  - A request carries a `target_pid` and is NOT bound to a specific checker.
  - Any device in the configured `online_check_checkers` list may pop/serve any
    pending request; a device outside the list never can.
  - `has_pending_online_check_request` / `is_online_check_priority_active` are
    True only for configured checkers.
  - Requester is not limited to emulator-5558.
  - **Backward compat (critical):** with the default config
    (`online_check_checkers == ["emulator-5554"]`) the legacy 5558→5554
    submit/pop/complete/wait round-trip behaves exactly as before.

All tests drive `bot_state` directly with the config checker-list monkeypatched,
so they never touch a real device, Playwright, OCR, or the network.
"""
from __future__ import annotations

import bot_state
import config_manager
import pytest


@pytest.fixture(autouse=True)
def _clean_mailbox():
    """Reset the online-check mailbox + signals before and after each test."""
    def _reset():
        with bot_state._global_lock:
            bot_state._online_check_requests.clear()
            bot_state._online_check_pending.clear()
            bot_state._signals.clear()
            bot_state._refresh_needed = False
    _reset()
    yield
    _reset()


@pytest.fixture
def checkers(monkeypatch):
    """Set the configured checker list; returns a setter for per-test control."""
    state = {"list": ["emulator-5554"]}
    monkeypatch.setattr(
        config_manager, "get_online_check_checkers", lambda: list(state["list"])
    )

    def _set(lst):
        state["list"] = list(lst)

    return _set


# ---------------------------------------------------------------------------
# Backward compatibility: default config == legacy 5558 -> 5554 behaviour
# ---------------------------------------------------------------------------

def test_default_config_5558_to_5554_round_trip(checkers):
    checkers(["emulator-5554"])  # the default

    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558",
        checker_ip="emulator-5554",
        target_pid=89565100511322,
    )
    assert isinstance(req_id, str)

    # 5554 (the default checker) sees and claims it.
    assert bot_state.has_pending_online_check_request("emulator-5554") is True
    popped = bot_state.pop_online_check_request("emulator-5554")
    assert popped is not None
    assert popped["id"] == req_id
    assert popped["requester_ip"] == "emulator-5558"
    assert popped["target_pid"] == 89565100511322

    # In-flight on the claiming checker.
    assert bot_state.is_online_check_priority_active("emulator-5554") is True
    # Queue now empty.
    assert bot_state.has_pending_online_check_request("emulator-5554") is False

    bot_state.complete_online_check_request(req_id, is_busy=False, detail="ok")
    result = bot_state.wait_online_check_result(req_id, timeout_sec=1.0)
    assert result["status"] == "done"
    assert result["result_busy"] is False
    assert result["requester_ip"] == "emulator-5558"


def test_default_config_protects_5558_non_checker_cannot_steal(checkers):
    """Under default config, a non-checker (5558 itself) cannot pop/serve."""
    checkers(["emulator-5554"])
    bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=111,
    )
    # 5558 is NOT a checker -> never claims, never early-wakes on the request.
    assert bot_state.has_pending_online_check_request("emulator-5558") is False
    assert bot_state.pop_online_check_request("emulator-5558") is None
    # The real checker still can.
    assert bot_state.has_pending_online_check_request("emulator-5554") is True


# ---------------------------------------------------------------------------
# Multi-checker: any configured checker can serve; non-checker cannot
# ---------------------------------------------------------------------------

def test_two_checkers_either_can_pop_and_complete(checkers):
    checkers(["emulator-5554", "emulator-5560"])

    req_id = bot_state.submit_online_check_request(
        requester_ip="7fe98fc6", target_pid=222,
    )

    # Both checkers see the same pending request.
    assert bot_state.has_pending_online_check_request("emulator-5554") is True
    assert bot_state.has_pending_online_check_request("emulator-5560") is True

    # The second checker claims it (any checker may serve any request).
    popped = bot_state.pop_online_check_request("emulator-5560")
    assert popped is not None and popped["id"] == req_id

    # Now it is in-flight on 5560 only, not 5554.
    assert bot_state.is_online_check_priority_active("emulator-5560") is True
    assert bot_state.is_online_check_priority_active("emulator-5554") is False
    # And no longer pending for anyone.
    assert bot_state.has_pending_online_check_request("emulator-5554") is False
    assert bot_state.has_pending_online_check_request("emulator-5560") is False

    bot_state.complete_online_check_request(req_id, is_busy=True, detail="online")
    res = bot_state.wait_online_check_result(req_id, timeout_sec=1.0)
    assert res["status"] == "done" and res["result_busy"] is True
    assert res["claimed_by"] == "emulator-5560"


def test_ip_outside_checker_list_cannot_pop(checkers):
    checkers(["emulator-5554", "emulator-5560"])
    bot_state.submit_online_check_request(requester_ip="emulator-5558", target_pid=333)

    # A device not in the checker list cannot pop, regardless of pending work.
    assert bot_state.pop_online_check_request("emulator-5556") is None
    assert bot_state.has_pending_online_check_request("emulator-5556") is False
    assert bot_state.is_online_check_priority_active("emulator-5556") is False

    # A configured checker still can.
    assert bot_state.pop_online_check_request("emulator-5554") is not None


def test_is_online_check_checker_helper(checkers):
    checkers(["emulator-5554", "emulator-5560"])
    assert bot_state.is_online_check_checker("emulator-5554") is True
    assert bot_state.is_online_check_checker("emulator-5560") is True
    assert bot_state.is_online_check_checker("emulator-5558") is False
    assert bot_state.is_online_check_checker("7fe98fc6") is False


# ---------------------------------------------------------------------------
# Request carries target_pid, not bound to a checker; requester is unrestricted
# ---------------------------------------------------------------------------

def test_request_carries_target_pid_unbound_from_checker(checkers):
    checkers(["emulator-5554"])
    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5560",   # requester != 5558
        target_pid=44455566,
    )
    assert bot_state.get_online_check_target_pid(req_id) == 44455566

    popped = bot_state.pop_online_check_request("emulator-5554")
    assert popped["target_pid"] == 44455566
    # checker_ip hint is None (legacy callers may still pass it) and never
    # constrains routing.
    assert popped.get("checker_ip") is None


def test_any_requester_can_submit(checkers):
    checkers(["emulator-5554"])
    for requester in ("emulator-5558", "emulator-5560", "7fe98fc6", "192.168.1.50:5555"):
        rid = bot_state.submit_online_check_request(requester_ip=requester, target_pid=1)
        assert isinstance(rid, str)
    # 4 distinct requesters -> 4 distinct pending requests (different requester key).
    assert len(bot_state._online_check_pending) == 4
    # The single default checker can drain all of them in FIFO order.
    seen = []
    while True:
        p = bot_state.pop_online_check_request("emulator-5554")
        if p is None:
            break
        seen.append(p["requester_ip"])
    assert seen == ["emulator-5558", "emulator-5560", "7fe98fc6", "192.168.1.50:5555"]


# ---------------------------------------------------------------------------
# Dedup is preserved (per requester+target_pid), independent of checker
# ---------------------------------------------------------------------------

def test_dedup_same_requester_and_target(checkers):
    checkers(["emulator-5554"])
    first = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=777,
    )
    second = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=777,
    )
    assert first == second
    assert len(bot_state._online_check_pending) == 1

    # Still deduped while in-flight (status=processing, popped off pending).
    bot_state.pop_online_check_request("emulator-5554")
    third = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=777,
    )
    assert third == first

    # Different target_pid -> a new request.
    other = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=888,
    )
    assert other != first


# ---------------------------------------------------------------------------
# undetermined result -> requester does NOT 放行
# ---------------------------------------------------------------------------

def test_failed_result_is_not_offline(checkers):
    """When a checker cannot determine (fail_online_check_request), the waiter
    must not read result_busy as a 放行 (False). It stays None/failed so the
    requester keeps waiting / retries."""
    checkers(["emulator-5554"])
    req_id = bot_state.submit_online_check_request(
        requester_ip="emulator-5558", target_pid=999,
    )
    bot_state.pop_online_check_request("emulator-5554")
    bot_state.fail_online_check_request(req_id, "undetermined by emulator-5554: target_not_in_friend_list")
    res = bot_state.wait_online_check_result(req_id, timeout_sec=1.0)
    assert res["status"] == "failed"
    # result_busy was never set to False -> caller's `bool(result_busy, True)` /
    # status!='done' guard keeps it conservative (busy).
    assert res["result_busy"] is None
