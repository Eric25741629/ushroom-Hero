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

import random
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
    """Configured checkers that are currently idle, in RANDOM order.

    Order is shuffled so each request picks a random sleeping device to open the
    one-shot WS login, instead of always hammering the first configured checker
    (5554). Callers still fall through the whole list on an undetermined (None)
    result, so randomising only spreads load — it never weakens the gate.
    """
    try:
        checkers = config_manager.get_online_check_checkers()
    except Exception:  # noqa: BLE001 — config read must never kill the loop
        checkers = ["emulator-5554"]
    states = bot_state.get_all_states()
    idle = [c for c in checkers if _is_idle(c, states)]
    random.shuffle(idle)  # ponytail: stdlib shuffle; spread WS-login load across idle devices
    return idle


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


def _check_monitor_snapshot(target_pid: int, threshold_sec: int) -> Optional[bool]:
    """Fast path: signal monitor to refresh, then read the snapshot.

    Cold start (snapshot is None): wait up to 10s for the first snapshot.
    Warm path (snapshot exists): signal poll_now, wait up to 3s for a <5s-fresh one.

    Presence 從 entry 的原始 last_login_ts 以 requester 自己的 ``threshold_sec``
    重算——快照烘入的 bool 帶 monitor 的 120s guard 寬限，直接沿用會讓互檢在
    目標登出後多報 ~2 分鐘在線。ts 缺失回 None（落一次性 WS 路徑）。
    """
    try:
        from ws_token.online_monitor import get_snapshot, _monitor
        if _monitor is None:
            return None
        _monitor.poll_now()
        # cold start: wait longer for monitor to connect + first poll
        max_wait = 10.0 if get_snapshot() is None else 3.0
        deadline = time.time() + max_wait
        while time.time() < deadline:
            snap = get_snapshot()
            if snap and time.time() - snap.timestamp < 5:
                break
            time.sleep(0.3)
        else:
            snap = get_snapshot()
        if snap is None:
            return None
        if time.time() - snap.timestamp > 60:
            return None
        for entry in snap.entries:
            if entry.role_id == target_pid:
                ts = entry.last_login_ts
                if ts is None:
                    return None
                if ts == 0:
                    return True  # server presence sentinel: currently online
                return (int(time.time()) - int(ts)) < int(threshold_sec)
    except Exception:  # noqa: BLE001 — monitor import/read must never block
        pass
    return None


def _serve_one(req: Dict[str, Any], candidates: List[str]) -> None:
    """Answer one request: monitor snapshot first (instant), then one-shot WS fallback."""
    req_id = req.get("id")
    target_pid = req.get("target_pid")
    if not target_pid:
        bot_state.fail_online_check_request(req_id, "online_check_target_pid not set")
        return

    threshold_sec = _threshold_for(req.get("requester_ip"))

    # fast path: persistent monitor has a fresh answer
    cached = _check_monitor_snapshot(int(target_pid), threshold_sec)
    if cached is not None:
        bot_state.complete_online_check_request(
            req_id, is_busy=cached,
            detail=f"online-monitor snapshot (busy={cached})")
        return

    # slow path: one-shot WS login (fallback when monitor is not running)
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
