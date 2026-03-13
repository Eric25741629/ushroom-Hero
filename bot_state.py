import threading
import time
from typing import Dict, Optional, Any

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
    print(f"[BotState] 設備 {ip} 已上線並註冊狀態監控。")

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
    print(f"[BotState] 設備 {ip} 已離線: {reason}")

def update_state(ip: str, task: Optional[str] = None, step: Optional[str] = None, log: Optional[str] = None):
    """
    更新設備的當前狀態。
    """
    # 如果還沒初始化，先忽略或自動初始化 (這裡選擇安全忽略)
    if ip not in _locks: 
        return

    with get_device_lock(ip):
        if ip not in _states:
            return # 可能已經離線被清除了

        state = _states[ip]
        state["last_update"] = time.time()
        
        if task is not None:
            state["task"] = task
        
        if step is not None:
            state["step"] = step
            
        if log is not None:
            # 只保留最後 10 條 logs
            state["logs"].append(f"{time.strftime('%H:%M:%S')} - {log}")
            if len(state["logs"]) > 10:
                state["logs"].pop(0)

def check_pause(ip: str):
    """
    檢查該 IP 是否被要求暫停。
    如果被暫停，執行緒會卡在這裡 (block)，直到被恢復。
    """
    if ip not in _pause_events:
        return

    event = _pause_events[ip]
    
    if not event.is_set():
        # 狀態更新為暫停中
        update_state(ip, step="*** 暫停中 (等待指令) ***")
        
        # 阻塞在這裡，直到 set() 被呼叫
        event.wait()
        
        # 醒來後更新狀態
        update_state(ip, step="恢復執行")

def set_pause(ip: str, paused: bool):
    """
    設定暫停或恢復。
    paused=True  -> 暫停 (clear event)
    paused=False -> 恢復 (set event)
    """
    if ip not in _pause_events:
        print(f"[BotState] 無法設定暫停，找不到設備 {ip}")
        return

    with get_device_lock(ip):
        if ip in _states:
            _states[ip]["paused"] = paused

    if paused:
        _pause_events[ip].clear() # 設為 False，觸發 wait
        print(f"[BotState] 已發送暫停信號給 {ip}")
    else:
        _pause_events[ip].set()   # 設為 True，解除 wait
        print(f"[BotState] 已發送恢復信號給 {ip}")

def get_all_states() -> Dict[str, Dict[str, Any]]:
    """
    獲取所有設備的狀態快照 (給 UI 使用)。
    回傳的是 deep copy，避免 UI 讀取時被後台修改。
    """
    import copy
    with _global_lock:
        # 簡單的淺拷貝通常就夠了，因為我們只讀取第一層 key
        # 但為了保險起見，這裡做一個快照
        return copy.deepcopy(_states)


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
