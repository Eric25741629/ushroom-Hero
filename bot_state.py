import threading
import time
import uuid
import logging
from collections import deque
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

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

# refresh flag for prompting immediate ADB rescan
_refresh_needed = False

# Rolling window of screenshot durations (ms) per device — last 50 samples
_SCREENSHOT_WINDOW = 50
_screenshot_windows: Dict[str, deque] = {}


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
            _states[ip]["status"] = "OFFLINE"
            _states[ip]["step"] = reason
            _states[ip]["last_update"] = time.time()
    logger.info(f"[BotState] 設備 {ip} 已離線：{reason}")

def update_state(
    ip: str,
    task: Optional[str] = None,
    step: Optional[str] = None,
    log: Optional[str] = None,
    next_wake_at: Optional[float] = None,
    paused: Optional[bool] = None,
):
    """
    更新設備的當前狀態。
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
    if ip not in _pause_events:
        return

    event = _pause_events[ip]
    
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

    with get_device_lock(ip):
        if ip in _states:
            _states[ip]["paused"] = paused

    if paused:
        _pause_events[ip].clear() # 設為 False，觸發 wait
        logger.info(f"[BotState] 已發送暫停信號給 {ip}")
    else:
        _pause_events[ip].set()   # 設為 True，解除 wait
        logger.info(f"[BotState] 已發送恢復信號給 {ip}")

def record_screenshot_time(ip: str, duration_ms: float) -> None:
    """Record one screenshot duration sample for rolling-average tracking."""
    with _global_lock:
        if ip not in _states:
            return
        if ip not in _screenshot_windows:
            _screenshot_windows[ip] = deque(maxlen=_SCREENSHOT_WINDOW)
        _screenshot_windows[ip].append(duration_ms)


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


def set_refresh_needed():
    """Set a global one-shot flag indicating workers should refresh device list.

    This is intended to be called by the master (e.g. via web UI) to notify
    the main loop to perform an immediate scan. The flag is cleared when a
    worker calls `check_refresh_needed()`.
    """
    global _refresh_needed
    with _global_lock:
        _refresh_needed = True


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


# skip_sleep one-shot flags
_skip_sleep_flags: Dict[str, bool] = {}
# manual-hold one-shot release flags
_manual_release_flags: Dict[str, bool] = {}
# force-sleep one-shot flags
_force_sleep_flags: Dict[str, bool] = {}

# Web login / launch request mailbox.
_web_launch_requests: Dict[str, Dict[str, Any]] = {}

# Cross-thread online-check request/response mailbox.
_online_check_queue_by_checker: Dict[str, list[str]] = {}
_online_check_requests: Dict[str, Dict[str, Any]] = {}


def set_skip_sleep(ip: str):
    """Mark that the given device should skip its next sleep cycle (one-shot).

    Called by the control panel or remote command. Workers should call
    `check_skip_sleep(ip)` to consume the flag.
    """
    with _global_lock:
        _skip_sleep_flags[ip] = True


def request_force_sleep(ip: str, reason: str = "強制休眠"):
    """Request the device to stop current work and enter sleep as soon as possible."""
    with _global_lock:
        _force_sleep_flags[ip] = True
        _skip_sleep_flags.pop(ip, None)
        _manual_release_flags.pop(ip, None)
        req = _web_launch_requests.get(ip)
        if req and req.get("status") == "pending":
            req["status"] = "cancelled"
            req["completed_at"] = time.time()
            req["last_message"] = reason
        if ip in _states:
            st = _states[ip]
            st["paused"] = False
            st["task"] = "強制休眠"
            st["step"] = reason
            st.pop("next_wake_at", None)
            st["last_update"] = time.time()
        # Set pause event inside lock — Event.set() is non-blocking, safe inside lock
        if ip in _pause_events:
            _pause_events[ip].set()


def check_force_sleep(ip: str) -> bool:
    """Atomically check and consume the force-sleep flag for `ip`."""
    with _global_lock:
        return bool(_force_sleep_flags.pop(ip, False))


def clear_skip_sleep(ip: str):
    """Clear skip_sleep flag for device without consuming it."""
    with _global_lock:
        if ip in _skip_sleep_flags:
            del _skip_sleep_flags[ip]


def check_skip_sleep(ip: str) -> bool:
    """Atomically check and consume the skip_sleep flag for `ip`.

    Returns True if a skip was requested, otherwise False.
    """
    with _global_lock:
        if _skip_sleep_flags.pop(ip, False):
            return True
        return False


def set_manual_release(ip: str):
    """Request manual-hold mode to end on next poll."""
    with _global_lock:
        _manual_release_flags[ip] = True


def check_manual_release(ip: str) -> bool:
    """Atomically check and consume the manual-hold release flag."""
    with _global_lock:
        if _manual_release_flags.pop(ip, False):
            return True
        return False


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
        global _refresh_needed
        _refresh_needed = True


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


def submit_online_check_request(requester_ip: str, checker_ip: str) -> str:
    """Submit a cross-thread online-check request and return request id.

    Dedupe: if there is already a pending or processing request for the same
    (requester, checker) pair, reuse it instead of enqueuing a duplicate. The
    checker only needs the current "busy?" answer, not one snapshot per retry —
    seen in logs where 5558 piled up ~60 identical requests on a sleeping 5554.
    """
    now = time.time()
    with _global_lock:
        # Scan the request mailbox (not just the queue) — a popped entry has
        # status="processing" and is no longer in the queue, but the answer
        # is still in flight, so a fresh submit should still reuse it.
        for existing_id, existing in _online_check_requests.items():
            if (
                existing.get("requester_ip") == requester_ip
                and existing.get("checker_ip") == checker_ip
                and existing.get("status") in ("pending", "processing")
            ):
                existing["updated_at"] = now
                _skip_sleep_flags[checker_ip] = True
                return existing_id

        req_id = str(uuid.uuid4())
        payload = {
            "id": req_id,
            "requester_ip": requester_ip,
            "checker_ip": checker_ip,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "result_busy": None,
            "detail": "",
            "error": "",
            "_event": threading.Event(),
        }
        _online_check_requests[req_id] = payload
        _online_check_queue_by_checker.setdefault(checker_ip, []).append(req_id)
        # Hint checker thread to break long sleep and process mailbox ASAP.
        _skip_sleep_flags[checker_ip] = True
        # Also request an immediate device rescan so a cold-start-delayed checker
        # thread can be started right away instead of waiting for the next 30s loop.
        global _refresh_needed
        _refresh_needed = True
    return req_id


def pop_online_check_request(checker_ip: str) -> Optional[Dict[str, Any]]:
    """Pop one pending request for checker thread; mark as processing."""
    with _global_lock:
        q = _online_check_queue_by_checker.get(checker_ip, [])
        if not q:
            return None
        req_id = q.pop(0)
        req = _online_check_requests.get(req_id)
        if not req:
            return None
        req["status"] = "processing"
        req["updated_at"] = time.time()
        return {
            "id": req_id,
            "requester_ip": req.get("requester_ip"),
            "checker_ip": req.get("checker_ip"),
            "created_at": req.get("created_at"),
        }


def has_pending_online_check_request(checker_ip: str) -> bool:
    """Check whether checker has pending mailbox items."""
    with _global_lock:
        q = _online_check_queue_by_checker.get(checker_ip, [])
        return bool(q)


def is_online_check_priority_active(checker_ip: str) -> bool:
    """Return True when checker has a request currently being processed (in-flight)."""
    with _global_lock:
        for req in _online_check_requests.values():
            if (
                req.get("checker_ip") == checker_ip
                and req.get("status") == "processing"
            ):
                return True
        return False


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
            "created_at": req.get("created_at"),
            "updated_at": req.get("updated_at"),
        }


def clear_offline_devices():
    """Remove devices whose status is OFFLINE.

    H7 fix: 兩段 lock 中間 device 可能已上線，必須在 per-device lock 內
    重新驗證 status 仍為 OFFLINE 才能刪除，避免清掉活躍 device 的狀態。
    同步清理之前漏掉的 per-device 結構（_manual_release_flags、_screenshot_windows）。
    """
    with _global_lock:
        to_remove = [ip for ip, st in _states.items() if str(st.get("status")) == "OFFLINE"]

    for ip in to_remove:
        with get_device_lock(ip):
            st = _states.get(ip)
            if st is None or str(st.get("status")) != "OFFLINE":
                # device 已重新上線或被別處刪除，跳過
                continue
            # per-device 結構（pause_events / *_flags）由 per-device lock 護衛
            _states.pop(ip, None)
            _pause_events.pop(ip, None)
            _skip_sleep_flags.pop(ip, None)
            _force_sleep_flags.pop(ip, None)
        # _manual_release_flags / _screenshot_windows / _locks 在他處皆以
        # _global_lock 保護（見 record_screenshot_time、set_manual_release 等），
        # 結構變更也走 _global_lock 才不會與其他 reader/writer 競態。
        with _global_lock:
            _manual_release_flags.pop(ip, None)
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

            # 僅自動清理遠端裝置，避免誤刪本地機台
            if ":" in ip and age >= remove_remote_after_sec:
                _states.pop(ip, None)
                _pause_events.pop(ip, None)
                _skip_sleep_flags.pop(ip, None)
                _force_sleep_flags.pop(ip, None)
                with _global_lock:
                    _locks.pop(ip, None)
