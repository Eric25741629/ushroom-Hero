"""Background pure-WS server for cross-device online-check (master-only).

A single daemon thread polls `bot_state` for pending online-check requests and
answers them over a one-shot WS login (`runtime_services.ws_online_checker
.check_via_ws`) using an *idle* checker's own creds. This decouples online-check
from the per-device wake loop: no device is ever woken / cold-starts a browser
to serve a check.

Background: the old path raised `Signal.SKIP_SLEEP` on every configured checker
on each request, so under `online_check_checkers == ["*"]` the whole web_h5
fleet woke every ~30s and re-logged-in — the "帳號一直在重啟" the user saw.

Idle = the checker is sleeping / not running a session, because `check_via_ws`
logs in with the checker's account and would kick a live session (異地登入).
A sleeping web_h5 device has its browser closed, so the login is conflict-free.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import bot_state
import config_manager
from runtime_services.ws_online_checker import check_via_ws
from utils.logging_utils import logger

# State strings that mean "safe to log in as this checker" (no live session to
# kick). A device with no state yet (thread not started) is also safe.
_IDLE_TASKS = ("休眠中", "啟動後休眠")
_POLL_SEC = 2.0

_thread: Optional[threading.Thread] = None
_started = False
_start_lock = threading.Lock()


def _is_idle(ip: str, states: Dict[str, Dict[str, Any]]) -> bool:
    """True iff logging in as ``ip`` won't kick a live session."""
    st = states.get(ip)
    if not st:
        return True  # no running thread / never started → safe
    if str(st.get("status") or "").upper() == "OFFLINE":
        return True
    return str(st.get("task") or "") in _IDLE_TASKS


def _idle_checkers() -> List[str]:
    """Configured checkers that are currently idle, in configured order."""
    try:
        checkers = config_manager.get_online_check_checkers()
    except Exception:  # noqa: BLE001 — config read must never kill the loop
        checkers = ["emulator-5554"]
    states = bot_state.get_all_states()
    return [c for c in checkers if _is_idle(c, states)]


def _threshold_for(requester_ip: Optional[str]) -> int:
    try:
        return int(config_manager.get_device_config(requester_ip).get(
            "online_check_threshold_sec", 60))
    except Exception:  # noqa: BLE001
        return 60


def _guild_for(checker_ip: str) -> Optional[int]:
    try:
        return config_manager.get_device_config(checker_ip).get(
            "online_check_guild_id") or None
    except Exception:  # noqa: BLE001
        return None


def _serve_one(req: Dict[str, Any], candidates: List[str]) -> None:
    """Answer one request: try idle checkers until one gives a definite
    True/False; fail it only when every candidate is undetermined (``None``),
    so the requester retries and never 放行 on an unknown result."""
    req_id = req.get("id")
    target_pid = req.get("target_pid")
    if not target_pid:
        bot_state.fail_online_check_request(req_id, "online_check_target_pid not set")
        return

    threshold_sec = _threshold_for(req.get("requester_ip"))
    for checker in candidates:
        result = check_via_ws(
            checker, int(target_pid), logger,
            guild_id=_guild_for(checker), threshold_sec=threshold_sec)
        if result is not None:
            bot_state.complete_online_check_request(
                req_id, is_busy=result,
                detail=f"ws online-check by {checker} (busy={result})")
            return
    bot_state.fail_online_check_request(req_id, "undetermined by all idle checkers")


def _serve_pending_once() -> None:
    """Drain every pending request this tick, if any idle checker exists."""
    candidates = _idle_checkers()
    if not candidates:
        return  # no safe checker right now → leave pending for the next poll
    claimer = candidates[0]  # pop is gated on checker-membership; any idle one works
    while True:
        req = bot_state.pop_online_check_request(claimer)
        if not req:
            return
        try:
            _serve_one(req, candidates)
        except Exception as exc:  # noqa: BLE001 — one bad request must not stall the rest
            logger.warning(f"[online-check-service] serve failed: {exc}")
            bot_state.fail_online_check_request(req.get("id"), str(exc))


def _run_loop() -> None:
    logger.info("[online-check-service] started (pure-WS background server)")
    while True:
        try:
            _serve_pending_once()
        except Exception as exc:  # noqa: BLE001 — loop must never die
            logger.warning(f"[online-check-service] poll error: {exc}")
        time.sleep(_POLL_SEC)


def ensure_online_check_service_started() -> None:
    """Start the single background server thread (master-only, idempotent)."""
    global _thread, _started
    with _start_lock:
        if _started and _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=_run_loop, name="online-check-service", daemon=True)
        _thread.start()
        _started = True
