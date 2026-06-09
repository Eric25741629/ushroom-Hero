import threading
import time
import uuid
import logging
from collections import deque
from enum import Enum, auto
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class Signal(Enum):
    SKIP_SLEEP = auto()
    FORCE_SLEEP = auto()
    WEB_CLOSE = auto()
    MANUAL_RELEASE = auto()


# Per-device one-shot signal set. Guarded by _global_lock. Replaces the 4 legacy
# *_flags dicts so device teardown clears every channel in one pop (no per-channel
# forgetting — see clear_offline_devices / sweep_stale_states).
_signals: Dict[str, set[Signal]] = {}


def raise_signal(ip: str, sig: Signal) -> None:
    with _global_lock:
        _signals.setdefault(ip, set()).add(sig)


def consume_signal(ip: str, sig: Signal) -> bool:
    with _global_lock:
        s = _signals.get(ip)
        if s and sig in s:
            s.discard(sig)
            return True
        return False

# 存放所有設備狀態的字典
# 結構範例:
# {
#   "emulator-5554": {
#       "status": "ONLINE",      # ONLINE, OFFLINE, ERROR
#       "task": "農場任務",       # 當前正在做什麼大任務
#       "step": "購買種子",       # 當前步驟細節
#       "last_update": 17000000, # 最後更新時間 (用來判斷是否卡死)
#       "paused": False,         # 是否處於暫停狀態
#       "logs": []               # 最新的幾條 log (可選)
#   }
# }
_states: Dict[str, Dict[str, Any]] = {}
_locks: Dict[str, threading.Lock] = {} # 每個 IP 一個鎖，避免讀寫衝突

# 控制信號 (暫停/恢復)
_pause_events: Dict[str, threading.Event] = {}

# 全域鎖，用於操作 _states 字典本身 (例如新增/刪除 key)
_global_lock = threading.Lock()

# Local in-process refresh trigger: the scan loop calls check_refresh_needed()
# to consume it. This is the LOCAL layer. The separate master->worker WIRE
# signal lives in control_panel_app._global_commands["refresh_needed"] — do not
# conflate the two; they are different layers (in-process vs cross-machine).
_refresh_needed = False

# Rolling window of screenshot durations (ms) per device — last 50 samples
_SCREENSHOT_WINDOW = 50
_screenshot_windows: Dict[str, deque] = {}

# Devices whose automation thread runs in THIS process (local). Remote worker
# devices are keyed "worker_id:ip" and are aggregated via report_status — they
# never run a local thread, so they never register here. This is the reliable
# local/remote discriminator: a TCP-attached emulator like "127.0.0.1:5555"
# contains a colon yet is local, so the legacy `":" in ip` heuristic misroutes
# its control commands (pause / force-sleep) to the remote queue.
_local_device_ids: set = set()


def register_local_device(ip: str) -> None:
    """Mark ``ip`` as a locally-managed device thread."""
    with _global_lock:
        _local_device_ids.add(ip)


def is_local_device(ip: str) -> bool:
    """Return True when ``ip`` is handled by a local thread in this process."""
    with _global_lock:
        return ip in _local_device_ids


def get_device_lock(ip: str) -> threading.Lock:
    """獲取指定設備的鎖，如果不存在則創建"""
    with _global_lock:
        if ip not in _locks:
            _locks[ip] = threading.Lock()
        return _locks[ip]

def init_device(ip: str):
    """
    初始化設備狀態，標記為上線。
    應在 main 函數最開始呼叫。
    """
    with get_device_lock(ip):
        _pause_events[ip] = threading.Event()
        _pause_events[ip].set() # 預設為 True (不暫停，直接執行)

        _states[ip] = {
            "status": "ONLINE",
            "task": "初始化",
            "step": "等待啟動...",
            "last_update": time.time(),
            "paused": False,
            "logs": []
        }
    # init_device() is only ever called by a local device thread (and the local
    # MuMu watchdog), so this is the canonical "local device came online" hook.
    register_local_device(ip)
    logger.debug(f"[BotState] 設備 {ip} 已上線並註冊狀態監控。")


def ensure_device_visible(ip: str, status: str = "OFFLINE", step: str = "等待啟動..."):
    """
    Ensure a device appears in dashboard state even before its worker thread starts.
    Does not overwrite existing state.
    """
    with get_device_lock(ip):
        if ip in _states:
            return
        if ip not in _pause_events:
            _pause_events[ip] = threading.Event()
            _pause_events[ip].set()
        _states[ip] = {
            "status": str(status or "OFFLINE").upper(),
            "task": "待命",
            "step": step or "等待啟動...",
            "last_update": time.time(),
            "paused": False,
            "logs": []
        }

def set_offline(ip: str, reason: str = "正常結束"):
    """
    標記設備為離線。
    應在 main 函數結束或異常退出時呼叫。
    """
    with get_device_lock(ip):
        if ip in _states:
            # 只在首次轉入 OFFLINE 時記錄時間，避免重複 set_offline 刷新計時
            if _states[ip].get("status") != "OFFLINE":
                _states[ip]["offline_since"] = time.time()
            _states[ip]["status"] = "OFFLINE"
            _states[ip]["step"] = reason
            _states[ip]["last_update"] = time.time()
    logger.info(f"[BotState] 設備 {ip} 已離線：{reason}")


def get_offline_since(ip: str) -> float | None:
    """回傳設備首次進入 OFFLINE 的時間戳，尚未離線或無紀錄則回傳 None。"""
    with get_device_lock(ip):
        st = _states.get(ip)
        if st is None or st.get("status") != "OFFLINE":
            return None
        return st.get("offline_since")

def update_state(
    ip: str,
    task: Optional[str] = None,
    step: Optional[str] = None,
    log: Optional[str] = None,
    next_wake_at: Optional[float] = None,
    paused: Optional[bool] = None,
    step_deadline: Optional[float] = None,
):
    """
    更新設備的當前狀態。

    `step_deadline` 是「這個步驟」的倒數截止時間戳 (epoch seconds)。前端從它
    倒數即可，不必拿會被背景心跳刷新的 `last_update` 當錨點。它的生命週期綁在
    step 上：設了新的 step 卻沒帶 deadline，就清掉舊的，避免儀表板繼續倒數一個
    已結束的任務 (例如開神燈跑完後)。
    """
    with get_device_lock(ip):
        # 允許遠端 worker 首次回報時自動建立狀態
        if ip not in _states:
            if ip not in _pause_events:
                _pause_events[ip] = threading.Event()
                _pause_events[ip].set()
            _states[ip] = {
                "status": "ONLINE",
                "task": "初始化",
                "step": "等待同步...",
                "last_update": time.time(),
                "paused": False,
                "logs": []
            }

        state = _states[ip]
        state["last_update"] = time.time()
        
        if task is not None:
            state["task"] = task
            # Once a device resumes active work, any previously scheduled wake time
            # is stale and should not keep showing in the dashboard countdown.
            if task != "休眠中":
                state.pop("next_wake_at", None)
        
        if step is not None:
            state["step"] = step
            # 倒數錨點綁在 step 上：新 step 沒帶自己的 deadline -> 清掉殘留的，
            # 否則前端會繼續對著上一個任務 (例如開神燈) 的截止時間倒數。
            if step_deadline is None:
                state.pop("step_deadline", None)

        if step_deadline is not None:
            state["step_deadline"] = float(step_deadline)

        if log is not None:
            # 只保留最近 10 筆 logs
            state["logs"].append(f"{time.strftime('%H:%M:%S')} - {log}")
            if len(state["logs"]) > 10:
                state["logs"].pop(0)

        if next_wake_at is not None:
            state["next_wake_at"] = float(next_wake_at)

        if paused is not None:
            state["paused"] = bool(paused)

def check_pause(ip: str):
    """
    檢查該 IP 是否被要求暫停。
    如果被暫停，執行緒會卡在這裡 (block)，直到被恢復。
    """
    # Capture event reference inside lock to prevent TOCTOU with clear_offline_devices()
    with _global_lock:
        event = _pause_events.get(ip)
    if event is None:
        return

    if not event.is_set():
        while not event.is_set():
            if has_pending_web_launch_request(ip):
                update_state(ip, step="*** 暫停中，但收到手動開網頁請求 ***")
                return True

            # 狀態更新為暫停中
            update_state(ip, step="*** 暫停中 (等待指令) ***")

            # 只短暫等待，讓手動開網頁這類高優先請求能插隊處理。
            event.wait(timeout=1.0)

        # 醒來後更新狀態
        update_state(ip, step="恢復執行")

def set_pause(ip: str, paused: bool):
    """
    設定暫停或恢復。
    paused=True  -> 暫停 (clear event)
    paused=False -> 恢復 (set event)
    """
    if ip not in _pause_events:
        logger.warning(f"[BotState] 無法設定暫停，找不到設備 {ip}")
        return

    # paused is now derived from the pause Event in get_all_states() (single
    # source of truth for local devices) — no stored bool write here.
    if paused:
        _pause_events[ip].clear() # 設為 False，觸發 wait
        logger.info(f"[BotState] 已發送暫停信號給 {ip}")
    else:
        _pause_events[ip].set()   # 設為 True，解除 wait
        logger.info(f"[BotState] 已發送恢復信號給 {ip}")


def get_pause_event(ip: str) -> Optional[threading.Event]:
    """Return the per-device pause Event (or None). Source of truth for pause state."""
    with _global_lock:
        return _pause_events.get(ip)


def record_screenshot_time(ip: str, duration_ms: float) -> None:
    """Record one screenshot duration sample for rolling-average tracking."""
    with _global_lock:
        if ip not in _states:
            return
        if ip not in _screenshot_windows:
            _screenshot_windows[ip] = deque(maxlen=_SCREENSHOT_WINDOW)
        _screenshot_windows[ip].append(duration_ms)


def update_remote_metrics(ip: str, logs=None, avg_screenshot_ms=None) -> None:
    """Update display-only fields for a (remote) device from a worker sync.

    Lock-guarded replacement for control_panel writing bot_state._states directly.
    Behaviour mirrors the old direct writes: no-op if the device is absent;
    does NOT touch last_update (heartbeat is driven elsewhere)."""
    with get_device_lock(ip):
        st = _states.get(ip)
        if st is None:
            return
        if logs is not None:
            st["logs"] = logs
        if avg_screenshot_ms is not None:
            st["avg_screenshot_ms"] = avg_screenshot_ms


def get_all_states() -> Dict[str, Dict[str, Any]]:
    """
    獲取所有設備的狀態快照 (給 UI 使用)。
    回傳的是 deep copy，避免 UI 讀取時被後台修改。
    """
    import copy
    with _global_lock:
        snapshot = copy.deepcopy(_states)
        for ip, state in snapshot.items():
            window = _screenshot_windows.get(ip)
            if window:
                state["avg_screenshot_ms"] = sum(window) / len(window)
            else:
                state["avg_screenshot_ms"] = None
            # paused is derived from the pause Event (single source of truth) for
            # LOCAL devices. Remote/worker devices (worker_id:ip) keep the value
            # reported via update_state(paused=...) — their local Event is only a
            # placeholder. Inline the _local_device_ids membership test: we already
            # hold _global_lock and is_local_device() would re-acquire it (plain Lock).
            if ip in _local_device_ids:
                ev = _pause_events.get(ip)
                if ev is not None:
                    state["paused"] = not ev.is_set()
        return snapshot


def record_emulator_restart(ip: str, reason: str, when_ts: Optional[float] = None):
    """Record emulator restart metrics for dashboard visibility."""
    ts = when_ts if when_ts is not None else time.time()
    with get_device_lock(ip):
        if ip not in _states:
            init_device(ip)
        st = _states[ip]
        st["restart_count"] = int(st.get("restart_count", 0)) + 1
        st["last_restart_at"] = ts
        st["last_restart_reason"] = reason
        st["last_update"] = ts


def update_watchdog_probe(ip: str, level: str = "L0", adb_failures: Optional[int] = None):
    """Update watchdog probe state (L0/L1/L2) for dashboard visibility."""
    with get_device_lock(ip):
        if ip not in _states:
            init_device(ip)
        st = _states[ip]
        st["watchdog_level"] = level
        if adb_failures is not None:
            st["adb_consecutive_failures"] = int(adb_failures)
        st["last_update"] = time.time()


def get_heartbeat_age_sec(ip: str, now_ts: Optional[float] = None) -> float:
    """Return elapsed seconds since last heartbeat update."""
    now = now_ts if now_ts is not None else time.time()
    with get_device_lock(ip):
        if ip not in _states:
            return float("inf")
        last = float(_states[ip].get("last_update", 0) or 0)
    return max(0.0, now - last)


def _set_refresh_needed_locked() -> None:
    """Set the local refresh flag. Caller MUST already hold _global_lock.

    Single source of the write. request_web_launch / submit_online_check_request
    are already inside the _global_lock critical section, so they call THIS (not
    the public set_refresh_needed) — _global_lock is a plain Lock, not an RLock,
    so re-acquiring it from within would self-deadlock.
    """
    global _refresh_needed
    _refresh_needed = True


def set_refresh_needed():
    """Set a global one-shot flag indicating workers should refresh device list.

    This is intended to be called by the master (e.g. via web UI) to notify
    the main loop to perform an immediate scan. The flag is cleared when a
    worker calls `check_refresh_needed()`.
    """
    with _global_lock:
        _set_refresh_needed_locked()


def clear_refresh_needed():
    """Clear the refresh flag without consuming it."""
    global _refresh_needed
    with _global_lock:
        _refresh_needed = False


def check_refresh_needed() -> bool:
    """Atomically check and consume the refresh flag.

    Returns True if a refresh was requested (and clears the flag), otherwise False.
    """
    global _refresh_needed
    with _global_lock:
        if _refresh_needed:
            _refresh_needed = False
            return True
        return False


# Web login / launch request mailbox.
_web_launch_requests: Dict[str, Dict[str, Any]] = {}

# Cross-thread online-check request/response mailbox.
#
# Decoupled model (2026-06-09): a request is NOT bound to a specific checker.
# It carries a `target_pid` and sits in a single FIFO pending list; ANY device
# in the configured `online_check_checkers` list may claim and serve it. The
# legacy single-checker behaviour is preserved by the default config
# (`online_check_checkers == ["emulator-5554"]`), so 5558→5554 still works
# exactly as before.
_online_check_pending: "list[str]" = []          # request ids awaiting a checker (FIFO)
_online_check_requests: Dict[str, Dict[str, Any]] = {}


def is_online_check_checker(ip: str) -> bool:
    """True iff `ip` is allowed to serve cross-device online-check requests.

    Reads the configured checker list (global `online_check_checkers`, default
    `["emulator-5554"]`). Lazily imports config_manager to avoid an import cycle
    and to stay safe if config is unavailable (then fall back to the legacy
    single checker so the 5558 protection never silently disappears).
    """
    try:
        import config_manager  # lazy: avoid import cycle / import-time config load
        checkers = config_manager.get_online_check_checkers()
    except Exception:
        checkers = ["emulator-5554"]
    return ip in checkers


def set_skip_sleep(ip: str):
    """Mark that the given device should skip its next sleep cycle (one-shot).

    Called by the control panel or remote command. Workers should call
    `check_skip_sleep(ip)` to consume the flag.
    """
    raise_signal(ip, Signal.SKIP_SLEEP)


def request_force_sleep(ip: str, reason: str = "強制休眠"):
    """Request the device to stop current work and enter sleep as soon as possible."""
    with _global_lock:
        sigs = _signals.setdefault(ip, set())
        sigs.add(Signal.FORCE_SLEEP)
        sigs.discard(Signal.SKIP_SLEEP)
        sigs.discard(Signal.MANUAL_RELEASE)
        req = _web_launch_requests.get(ip)
        if req and req.get("status") == "pending":
            req["status"] = "cancelled"
            req["completed_at"] = time.time()
            req["last_message"] = reason
        if ip in _states:
            st = _states[ip]
            st["task"] = "強制休眠"
            st["step"] = reason
            st.pop("next_wake_at", None)
            st["last_update"] = time.time()
        # Set pause event inside lock — Event.set() is non-blocking, safe inside lock
        if ip in _pause_events:
            _pause_events[ip].set()


def check_force_sleep(ip: str) -> bool:
    """Atomically check and consume the force-sleep flag for `ip`."""
    return consume_signal(ip, Signal.FORCE_SLEEP)


def request_web_close(ip: str) -> None:
    """Request the (web_h5) device thread to close its headless browser now.

    Unlike `request_force_sleep`, this does NOT sleep the device or change pause
    state: the device keeps running and will cold-restart the browser on its next
    loop iteration / wake. Used by the live-view "關閉瀏覽器" button to restart a
    browser. The owning device thread consumes it via `check_web_close(ip)`.
    """
    raise_signal(ip, Signal.WEB_CLOSE)


def check_web_close(ip: str) -> bool:
    """Atomically check and consume the close-browser flag for `ip`."""
    return consume_signal(ip, Signal.WEB_CLOSE)


def clear_skip_sleep(ip: str):
    """Clear skip_sleep flag for device without consuming it."""
    with _global_lock:
        s = _signals.get(ip)
        if s:
            s.discard(Signal.SKIP_SLEEP)


def check_skip_sleep(ip: str) -> bool:
    """Atomically check and consume the skip_sleep flag for `ip`.

    Returns True if a skip was requested, otherwise False.
    """
    return consume_signal(ip, Signal.SKIP_SLEEP)


def set_manual_release(ip: str):
    """Request manual-hold mode to end on next poll."""
    raise_signal(ip, Signal.MANUAL_RELEASE)


def check_manual_release(ip: str) -> bool:
    """Atomically check and consume the manual-hold release flag."""
    return consume_signal(ip, Signal.MANUAL_RELEASE)


def request_web_launch(ip: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Request the device thread to open/refresh its web page on next cycle."""
    with _global_lock:
        req_payload = dict(payload or {})
        _web_launch_requests[ip] = {
            "requested_at": time.time(),
            "consumed_at": None,
            "completed_at": None,
            "status": "pending",
            "last_message": str(req_payload.get("message", "")),
            "last_error": "",
            "payload": req_payload,
        }
        _set_refresh_needed_locked()  # already holding _global_lock


def consume_web_launch_request(ip: str) -> Optional[Dict[str, Any]]:
    """Consume one pending web-launch request for the given device."""
    with _global_lock:
        req = _web_launch_requests.get(ip)
        if not req or req.get("status") != "pending":
            return None
        req["status"] = "processing"
        req["consumed_at"] = time.time()
        return dict(req)


def has_pending_web_launch_request(ip: str) -> bool:
    """Return True when ip has an unconsumed web launch request."""
    with _global_lock:
        req = _web_launch_requests.get(ip)
        return bool(req and req.get("status") == "pending")


def complete_web_launch_request(ip: str, ok: bool, message: str = "", error: str = "") -> None:
    """Mark web-launch request as completed."""
    with _global_lock:
        req = _web_launch_requests.setdefault(ip, {})
        req["status"] = "done" if ok else "failed"
        req["completed_at"] = time.time()
        req["last_message"] = str(message or req.get("last_message", ""))
        req["last_error"] = str(error or "")


def get_web_launch_status(ip: str) -> Dict[str, Any]:
    with _global_lock:
        req = _web_launch_requests.get(ip, {})
        return {
            "running": req.get("status") in {"pending", "processing"},
            "status": req.get("status", "idle"),
            "requested_at": req.get("requested_at"),
            "consumed_at": req.get("consumed_at"),
            "completed_at": req.get("completed_at"),
            "last_message": req.get("last_message", ""),
            "last_error": req.get("last_error", ""),
        }


def _signal_all_checkers_locked() -> None:
    """Wake every configured checker so the freest one can claim the request.

    Must be called while holding _global_lock. Raising SKIP_SLEEP on each
    checker lets each one break its long sleep and poll the mailbox; the first
    to claim wins (pop is atomic under the lock), the rest find nothing pending.
    """
    try:
        import config_manager  # lazy: avoid import cycle
        checkers = config_manager.get_online_check_checkers()
    except Exception:
        checkers = ["emulator-5554"]
    for checker in checkers:
        _signals.setdefault(checker, set()).add(Signal.SKIP_SLEEP)


def submit_online_check_request(
    requester_ip: str,
    checker_ip: Optional[str] = None,
    *,
    target_pid: Optional[int] = None,
) -> str:
    """Submit a cross-thread online-check request and return request id.

    Decoupled (2026-06-09): the request carries a `target_pid` (the player to
    check) and is NOT bound to a single checker. Any device in the configured
    `online_check_checkers` list may claim and serve it.

    `checker_ip` is retained for backward compatibility (legacy callers passed
    `checker_ip='emulator-5554'`). It is recorded on the payload as a hint only
    and does NOT restrict which checker may serve the request.

    Dedupe: if there is already a pending or processing request for the same
    requester+target_pid, reuse it instead of enqueuing a duplicate — the
    checker only needs the current "busy?" answer, not one snapshot per retry
    (logs showed 5558 piling up ~60 identical requests on a sleeping 5554).
    """
    now = time.time()
    norm_pid = None if target_pid is None else int(target_pid)
    with _global_lock:
        # Scan the request mailbox (not just the pending list) — a claimed entry
        # has status="processing" and is no longer pending, but the answer is
        # still in flight, so a fresh submit should still reuse it. Dedup keys on
        # (requester, target_pid); checker is intentionally NOT part of the key
        # so re-submitting after a checker swap still coalesces.
        for existing_id, existing in _online_check_requests.items():
            if (
                existing.get("requester_ip") == requester_ip
                and existing.get("target_pid") == norm_pid
                and existing.get("status") in ("pending", "processing")
            ):
                existing["updated_at"] = now
                _signal_all_checkers_locked()
                return existing_id

        req_id = str(uuid.uuid4())
        payload = {
            "id": req_id,
            "requester_ip": requester_ip,
            "checker_ip": checker_ip,   # hint only; not a routing constraint
            "target_pid": norm_pid,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "result_busy": None,
            "detail": "",
            "error": "",
            "_event": threading.Event(),
        }
        _online_check_requests[req_id] = payload
        _online_check_pending.append(req_id)
        # Hint every configured checker to break its long sleep and poll ASAP.
        _signal_all_checkers_locked()
        # Also request an immediate device rescan so a cold-start-delayed checker
        # thread can be started right away instead of waiting for the next 30s loop.
        _set_refresh_needed_locked()  # already holding _global_lock
    return req_id


def pop_online_check_request(checker_ip: str) -> Optional[Dict[str, Any]]:
    """Claim one pending request for a checker thread; mark it processing.

    Only devices in the configured `online_check_checkers` list may claim
    requests. Any such checker may pop ANY unclaimed pending request (FIFO);
    requests are not bound to a specific checker. A non-checker `checker_ip`
    always gets None, so a stray caller can never steal a request.
    """
    with _global_lock:
        if not _online_check_pending:
            return None
        if not _is_online_check_checker_locked(checker_ip):
            return None
        req_id = _online_check_pending.pop(0)
        req = _online_check_requests.get(req_id)
        if not req:
            return None
        req["status"] = "processing"
        req["claimed_by"] = checker_ip
        req["updated_at"] = time.time()
        return {
            "id": req_id,
            "requester_ip": req.get("requester_ip"),
            "checker_ip": req.get("checker_ip"),
            "target_pid": req.get("target_pid"),
            "created_at": req.get("created_at"),
        }


def _is_online_check_checker_locked(ip: str) -> bool:
    """Lock-free checker-membership test (caller already holds _global_lock)."""
    try:
        import config_manager  # lazy: avoid import cycle
        checkers = config_manager.get_online_check_checkers()
    except Exception:
        checkers = ["emulator-5554"]
    return ip in checkers


def has_pending_online_check_request(checker_ip: str) -> bool:
    """True iff `checker_ip` is a configured checker AND a request is pending.

    A non-checker device always gets False — it must never early-wake on
    another device's online-check request.
    """
    with _global_lock:
        if not _online_check_pending:
            return False
        return _is_online_check_checker_locked(checker_ip)


def is_online_check_priority_active(checker_ip: str) -> bool:
    """True when `checker_ip` (a configured checker) has an in-flight request.

    Matches the checker that actually claimed the request (`claimed_by`), so a
    request being served by another checker does not pin this one awake.
    """
    with _global_lock:
        if not _is_online_check_checker_locked(checker_ip):
            return False
        for req in _online_check_requests.values():
            if (
                req.get("status") == "processing"
                and req.get("claimed_by") == checker_ip
            ):
                return True
        return False


def get_online_check_target_pid(req_id: str) -> Optional[int]:
    """Return the target player id recorded on an online-check request, or None."""
    with _global_lock:
        req = _online_check_requests.get(req_id)
        return None if req is None else req.get("target_pid")


def complete_online_check_request(req_id: str, is_busy: bool, detail: str = ""):
    with _global_lock:
        req = _online_check_requests.get(req_id)
        if not req:
            return
        req["status"] = "done"
        req["updated_at"] = time.time()
        req["result_busy"] = bool(is_busy)
        req["detail"] = str(detail or "")
        ev = req.get("_event")
    if isinstance(ev, threading.Event):
        ev.set()


def fail_online_check_request(req_id: str, error: str):
    with _global_lock:
        req = _online_check_requests.get(req_id)
        if not req:
            return
        req["status"] = "failed"
        req["updated_at"] = time.time()
        req["error"] = str(error or "")
        ev = req.get("_event")
    if isinstance(ev, threading.Event):
        ev.set()


def wait_online_check_result(req_id: str, timeout_sec: float = 120.0) -> Dict[str, Any]:
    """Wait for checker result and return snapshot."""
    with _global_lock:
        req = _online_check_requests.get(req_id)
        if not req:
            return {"status": "missing", "result_busy": None, "detail": "", "error": "request not found"}
        ev = req.get("_event")

    if isinstance(ev, threading.Event):
        ev.wait(timeout=max(0.1, float(timeout_sec)))

    with _global_lock:
        req = _online_check_requests.get(req_id)
        if not req:
            return {"status": "missing", "result_busy": None, "detail": "", "error": "request not found"}
        return {
            "id": req_id,
            "status": req.get("status", "pending"),
            "result_busy": req.get("result_busy"),
            "detail": req.get("detail", ""),
            "error": req.get("error", ""),
            "requester_ip": req.get("requester_ip"),
            "checker_ip": req.get("checker_ip"),
            "target_pid": req.get("target_pid"),
            "claimed_by": req.get("claimed_by"),
            "created_at": req.get("created_at"),
            "updated_at": req.get("updated_at"),
        }


def clear_offline_devices():
    """Remove devices whose status is OFFLINE.

    H7 fix: 兩段 lock 中間 device 可能已上線，必須在 per-device lock 內
    重新驗證 status 仍為 OFFLINE 才能刪除，避免清掉活躍 device 的狀態。
    同步清理 per-device 結構（_signals、_screenshot_windows）。
    """
    with _global_lock:
        to_remove = [ip for ip, st in _states.items() if str(st.get("status")) == "OFFLINE"]

    for ip in to_remove:
        with get_device_lock(ip):
            st = _states.get(ip)
            if st is None or str(st.get("status")) != "OFFLINE":
                # device 已重新上線或被別處刪除，跳過
                continue
            # per-device 結構（pause_events）由 per-device lock 護衛
            _states.pop(ip, None)
            _pause_events.pop(ip, None)
        # _signals / _screenshot_windows / _locks 在他處皆以 _global_lock 保護
        # （見 raise_signal/consume_signal、record_screenshot_time 等），結構變更
        # 也走 _global_lock 才不會與其他 reader/writer 競態。一次 pop _signals
        # 即清掉所有 one-shot 通道（skip/force/web_close/manual_release），不會漏。
        with _global_lock:
            _signals.pop(ip, None)
            _screenshot_windows.pop(ip, None)
            _locks.pop(ip, None)


def sweep_stale_states(mark_offline_after_sec: float = 20.0, remove_remote_after_sec: float = 120.0):
    """Mark stale devices offline and purge stale remote devices.

    - Any device with stale heartbeat will be marked OFFLINE.
    - Remote devices (worker_id:ip) are removed after longer stale timeout.
    """
    now = time.time()
    with _global_lock:
        ips = list(_states.keys())

    for ip in ips:
        with get_device_lock(ip):
            st = _states.get(ip)
            if not st:
                continue

            last = float(st.get("last_update", 0) or 0)
            age = max(0.0, now - last)

            if age >= mark_offline_after_sec and st.get("status") != "OFFLINE":
                st["status"] = "OFFLINE"
                st["step"] = f"心跳逾時 {int(age)} 秒"

            # 僅自動清理遠端裝置，避免誤刪本地機台。
            # 注意：本機 TCP 模擬器 (如 127.0.0.1:5555) 也帶冒號，必須用
            # is_local_device() 排除，否則心跳一逾時就被當遠端清掉，連帶移除
            # _pause_events 讓暫停/休眠路由失效。
            if ":" in ip and not is_local_device(ip) and age >= remove_remote_after_sec:
                _states.pop(ip, None)
                _pause_events.pop(ip, None)
                # _signals / _locks 由 _global_lock 護衛；一次 pop _signals 即清掉
                # 所有 one-shot 通道（skip/force/web_close/manual_release）。
                with _global_lock:
                    _signals.pop(ip, None)
                    _locks.pop(ip, None)
