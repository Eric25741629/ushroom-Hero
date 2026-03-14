"""
事件驅動系統 - 允許透過網站 HTTP API 或 WebSocket 觸發遊戲自動化任務
"""
import threading
import queue
import json
import logging
from typing import Callable, Dict, Any, List
from enum import Enum
from datetime import datetime
import time

# ============ 事件類型定義 ============
class EventType(Enum):
    """所有支持的事件類型"""
    START_GAME = "start_game"              # 啟動遊戲
    STOP_GAME = "stop_game"                # 停止遊戲
    TRIGGER_FARMING = "trigger_farming"    # 觸發農業
    TRIGGER_PARKING = "trigger_parking"    # 觸發停車
    TRIGGER_BATTLE = "trigger_battle"      # 觸發戰鬥
    TRIGGER_MINING = "trigger_mining"      # 觸發挖礦
    TRIGGER_MISSION = "trigger_mission"    # 觸發任務
    PAUSE_DEVICE = "pause_device"          # 暫停設備
    RESUME_DEVICE = "resume_device"        # 恢復設備
    FORCE_WAKE = "force_wake"              # 強制喚醒
    SHUTDOWN = "shutdown"                  # 關閉程式

class EventPriority(Enum):
    """事件優先級"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4

# ============ 事件類 ============
class GameEvent:
    """遊戲事件"""
    def __init__(self, 
                 event_type: EventType,
                 device_ip: str,
                 priority: EventPriority = EventPriority.NORMAL,
                 data: Dict[str, Any] = None,
                 callback: Callable = None):
        self.event_type = event_type
        self.device_ip = device_ip
        self.priority = priority
        self.data = data or {}
        self.callback = callback
        self.timestamp = datetime.now()
        self.event_id = f"{device_ip}_{event_type.value}_{int(time.time() * 1000)}"
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典"""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "device_ip": self.device_ip,
            "priority": self.priority.name,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data
        }

# ============ 事件隊列管理器 ============
class EventQueue:
    """優先級事件隊列"""
    def __init__(self):
        self.queues = {
            EventPriority.CRITICAL: queue.Queue(),
            EventPriority.HIGH: queue.Queue(),
            EventPriority.NORMAL: queue.Queue(),
            EventPriority.LOW: queue.Queue(),
        }
        self.lock = threading.Lock()
    
    def put(self, event: GameEvent):
        """放入事件"""
        with self.lock:
            self.queues[event.priority].put(event)
    
    def get(self, timeout=None) -> GameEvent:
        """獲取優先級最高的事件"""
        # 優先取 CRITICAL，然後 HIGH，然後 NORMAL，最後 LOW
        for priority in [EventPriority.CRITICAL, EventPriority.HIGH, 
                        EventPriority.NORMAL, EventPriority.LOW]:
            try:
                return self.queues[priority].get_nowait()
            except queue.Empty:
                continue
        
        # 如果所有隊列都空，阻塞等待
        if timeout:
            return self.queues[EventPriority.CRITICAL].get(timeout=timeout)
        else:
            return self.queues[EventPriority.CRITICAL].get()
    
    def is_empty(self) -> bool:
        """檢查隊列是否為空"""
        with self.lock:
            return all(q.empty() for q in self.queues.values())
    
    def size(self) -> int:
        """獲取隊列總大小"""
        with self.lock:
            return sum(q.qsize() for q in self.queues.values())

# ============ 事件管理器 ============
class EventManager:
    """全局事件管理器"""
    def __init__(self, logger: logging.Logger = None):
        self.event_queue = EventQueue()
        self.event_history: List[GameEvent] = []
        self.logger = logger or logging.getLogger(__name__)
        self.device_states: Dict[str, str] = {}  # device_ip -> state (running/paused/stopped)
        self.event_handlers: Dict[EventType, List[Callable]] = {et: [] for et in EventType}
        self.lock = threading.Lock()
        self.max_history = 1000
    
    def register_handler(self, event_type: EventType, handler: Callable):
        """註冊事件處理器"""
        with self.lock:
            if handler not in self.event_handlers[event_type]:
                self.event_handlers[event_type].append(handler)
                self.logger.info(f"已註冊處理器: {event_type.value}")
    
    def unregister_handler(self, event_type: EventType, handler: Callable):
        """取消註冊事件處理器"""
        with self.lock:
            if handler in self.event_handlers[event_type]:
                self.event_handlers[event_type].remove(handler)
                self.logger.info(f"已取消註冊處理器: {event_type.value}")
    
    def emit_event(self, event: GameEvent):
        """發出事件"""
        # 加入隊列
        self.event_queue.put(event)
        
        # 記錄到歷史
        with self.lock:
            self.event_history.append(event)
            if len(self.event_history) > self.max_history:
                self.event_history.pop(0)
        
        # 執行同步處理器（可選）
        handlers = self.event_handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                self.logger.error(f"處理事件失敗 {event.event_id}: {e}")
        
        self.logger.info(f"事件已發出: {event.event_id} ({event.event_type.value}) for {event.device_ip}")
    
    def get_next_event(self, timeout=1) -> GameEvent:
        """獲取下一個事件"""
        try:
            return self.event_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def set_device_state(self, device_ip: str, state: str):
        """設定設備狀態"""
        with self.lock:
            self.device_states[device_ip] = state
        self.logger.info(f"[{device_ip}] 設備狀態已設定為: {state}")
    
    def get_device_state(self, device_ip: str) -> str:
        """獲取設備狀態"""
        return self.device_states.get(device_ip, "unknown")
    
    def get_history(self, device_ip: str = None, limit: int = 100) -> List[Dict]:
        """獲取事件歷史"""
        with self.lock:
            events = self.event_history
            if device_ip:
                events = [e for e in events if e.device_ip == device_ip]
            return [e.to_dict() for e in events[-limit:]]
    
    def get_stats(self) -> Dict[str, Any]:
        """獲取統計信息"""
        return {
            "queue_size": self.event_queue.size(),
            "history_count": len(self.event_history),
            "device_states": dict(self.device_states),
            "timestamp": datetime.now().isoformat()
        }

# ============ 全局實例 ============
_global_event_manager: EventManager = None
_event_manager_lock = threading.Lock()

def get_event_manager(logger: logging.Logger = None) -> EventManager:
    """獲取全局事件管理器"""
    global _global_event_manager
    if _global_event_manager is None:
        with _event_manager_lock:
            if _global_event_manager is None:
                _global_event_manager = EventManager(logger)
    return _global_event_manager

def emit(event_type: EventType, device_ip: str, 
         priority: EventPriority = EventPriority.NORMAL,
         data: Dict = None) -> str:
    """便捷函數：發出事件"""
    manager = get_event_manager()
    event = GameEvent(event_type, device_ip, priority, data)
    manager.emit_event(event)
    return event.event_id

# ============ 事件守護線程 ============
class EventDaemon(threading.Thread):
    """事件處理守護線程"""
    def __init__(self, event_manager: EventManager, logger: logging.Logger = None):
        super().__init__(daemon=True)
        self.event_manager = event_manager
        self.logger = logger or logging.getLogger(__name__)
        self.running = True
    
    def run(self):
        """持續處理事件"""
        self.logger.info("事件守護線程已啟動")
        while self.running:
            event = self.event_manager.get_next_event(timeout=1)
            if event:
                self.logger.debug(f"正在處理事件: {event.event_id}")
            time.sleep(0.1)
    
    def stop(self):
        """停止守護線程"""
        self.running = False
        self.logger.info("事件守護線程已停止")
