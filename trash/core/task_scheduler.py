# -*- coding: utf-8 -*-
"""
任務調度器 - 管理和執行各種遊戲任務
"""
import logging
import time
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass
from core.device_manager import DeviceManager
from core.state_detector import StateDetector
from core.game_actions import GameActions
from config.game_config import config

@dataclass
class Task:
    """任務數據類"""
    name: str
    condition: Callable[[], bool]  # 執行條件
    action: Callable[[], bool]     # 執行動作
    priority: int = 0              # 優先級（數字越大優先級越高）
    cooldown: int = 0              # 冷卻時間（秒）
    max_retry: int = 3             # 最大重試次數
    enabled: bool = True           # 是否啟用

class TaskScheduler:
    """任務調度器"""
    
    def __init__(self, device_manager: DeviceManager, state_detector: StateDetector, 
                 easyocr_reader, cnn_model, oracle_model=None):
        self.device_manager = device_manager
        self.state_detector = state_detector
        self.device_id = device_manager.device_id
        self.logger = logging.getLogger(f"TaskScheduler-{self.device_id}")
        
        # 初始化遊戲動作執行器
        self.game_actions = GameActions(
            device_manager, state_detector, easyocr_reader, cnn_model, oracle_model
        )
        
        # 任務列表
        self.tasks: List[Task] = []
        
        # 任務執行記錄
        self.task_history = {}
        
        # 運行狀態
        self.is_running = False
        self.cycle_count = 0
        
        # 註冊默認任務
        self._register_default_tasks()
    
    def add_task(self, task: Task):
        """添加任務"""
        self.tasks.append(task)
        self.tasks.sort(key=lambda t: t.priority, reverse=True)
        self.logger.info(f"任務已添加: {task.name}")
    
    def remove_task(self, task_name: str):
        """移除任務"""
        self.tasks = [t for t in self.tasks if t.name != task_name]
        self.logger.info(f"任務已移除: {task_name}")
    
    def enable_task(self, task_name: str, enabled: bool = True):
        """啟用/禁用任務"""
        for task in self.tasks:
            if task.name == task_name:
                task.enabled = enabled
                self.logger.info(f"任務 {task_name} {'啟用' if enabled else '禁用'}")
                break
    
    def execute_cycle(self) -> bool:
        """執行一個任務循環"""
        try:
            self.cycle_count += 1
            self.logger.debug(f"開始執行任務循環 #{self.cycle_count}")
            
            # 檢查設備連接
            if not self.device_manager.ensure_connection():
                self.logger.error("設備連接失敗，跳過此循環")
                return False
            
            # 設備特殊處理
            self._handle_device_specific_setup()
            
            # 檢查遊戲狀態
            if not self._ensure_game_running():
                return False
            
            # 執行任務
            executed_tasks = 0
            for task in self.tasks:
                if not task.enabled:
                    continue
                
                if self._should_execute_task(task):
                    success = self._execute_task(task)
                    if success:
                        executed_tasks += 1
                        self._record_task_execution(task)
            
            self.logger.debug(f"循環 #{self.cycle_count} 完成，執行了 {executed_tasks} 個任務")
            return True
            
        except Exception as e:
            self.logger.error(f"任務循環執行失敗: {e}")
            return False
    
    def _handle_device_specific_setup(self):
        """處理設備特殊設置"""
        device_config = config.DEVICE_CONFIGS.get(self.device_id, {})
        
        # 解鎖設備
        if device_config.get('need_unlock'):
            self.device_manager.unlock_device()
        
        # 在線檢查
        if device_config.get('online_check'):
            self._perform_online_check()
        
        # 設備延遲
        if 'delay' in device_config:
            time.sleep(device_config['delay'])
    
    def _ensure_game_running(self) -> bool:
        """確保遊戲正在運行"""
        if not self.device_manager.is_game_running():
            self.logger.info("遊戲未運行，嘗試啟動")
            return self.device_manager.start_game()
        return True
    
    def _should_execute_task(self, task: Task) -> bool:
        """判斷是否應該執行任務"""
        # 檢查冷卻時間
        if task.name in self.task_history:
            last_time = self.task_history[task.name].get('last_execution', 0)
            if time.time() - last_time < task.cooldown:
                return False
        
        # 檢查執行條件
        try:
            return task.condition()
        except Exception as e:
            self.logger.warning(f"任務條件檢查失敗 {task.name}: {e}")
            return False
    
    def _execute_task(self, task: Task) -> bool:
        """執行任務"""
        retry_count = 0
        
        while retry_count <= task.max_retry:
            try:
                self.logger.debug(f"執行任務: {task.name} (嘗試 {retry_count + 1})")
                success = task.action()
                
                if success:
                    self.logger.info(f"任務執行成功: {task.name}")
                    return True
                else:
                    self.logger.warning(f"任務執行失敗: {task.name}")
                
            except Exception as e:
                self.logger.error(f"任務執行異常 {task.name}: {e}")
            
            retry_count += 1
            if retry_count <= task.max_retry:
                time.sleep(1)  # 重試前等待
        
        self.logger.error(f"任務執行失敗，已達最大重試次數: {task.name}")
        return False
    def _record_task_execution(self, task: Task):
        """記錄任務執行"""
        if task.name not in self.task_history:
            self.task_history[task.name] = {}
        
        self.task_history[task.name]['last_execution'] = time.time()
        self.task_history[task.name]['execution_count'] = \
            self.task_history[task.name].get('execution_count', 0) + 1
    
    def _register_default_tasks(self):
        """註冊默認任務"""
        # 狀態處理任務（最高優先級）
        self.add_task(Task(
            name="處理公告",
            condition=lambda: self.state_detector.get_current_stage() == "公告",
            action=self.game_actions.handle_announcement,
            priority=100
        ))
        
        self.add_task(Task(
            name="處理獎勵",
            condition=lambda: self.state_detector.get_current_stage() in ["放置獎勵", "離線獎勵"],
            action=self.game_actions.handle_reward,
            priority=99
        ))
        
        self.add_task(Task(
            name="處理其他登錄",
            condition=lambda: self.state_detector.get_current_stage() == "其他登錄",
            action=self.game_actions.handle_other_login,
            priority=98
        ))
        
        # 主頁面基礎任務
        self.add_task(Task(
            name="寶箱點擊",
            condition=lambda: self.state_detector.get_current_stage() == "主頁面",
            action=self.game_actions.handle_treasure_box,
            priority=90,
            cooldown=300  # 5分鐘冷卻
        ))
        
        self.add_task(Task(
            name="農場管理",
            condition=lambda: self.state_detector.get_current_stage() == "主頁面",
            action=self.game_actions.handle_farm,
            priority=85,
            cooldown=1800  # 30分鐘冷卻
        ))
        
        self.add_task(Task(
            name="武魂收集",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             self.game_actions.check_martial_soul()),
            action=self.game_actions.handle_martial_soul,
            priority=80,
            cooldown=3600  # 1小時冷卻
        ))
        
        self.add_task(Task(
            name="技能和夥伴",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             self.device_id != "emulator-5568"),
            action=self.game_actions.handle_skill_and_partner,
            priority=75,
            cooldown=7200  # 2小時冷卻
        ))
        
        # 時間條件任務
        self.add_task(Task(
            name="家族管理",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             (time.localtime().tm_hour == 23 or
                              self._should_do_family())),
            action=self.game_actions.handle_family,
            priority=70,
            cooldown=1800  # 30分鐘冷卻
        ))
        
        self.add_task(Task(
            name="助手管理",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             time.localtime().tm_hour % 4 == 0),
            action=self.game_actions.handle_assistant,
            priority=65,
            cooldown=14400  # 4小時冷卻
        ))
        
        self.add_task(Task(
            name="停車檢查",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             self.game_actions.check_parking_expired()),
            action=self.game_actions.handle_parking,
            priority=60,
            cooldown=600  # 10分鐘冷卻
        ))
        
        self.add_task(Task(
            name="戰鬥管理",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             (time.localtime().tm_hour % 4 == 0 or 
                              time.localtime().tm_hour == 23)),
            action=self.game_actions.handle_battle,
            priority=55,
            cooldown=7200  # 2小時冷卻
        ))
        
        self.add_task(Task(
            name="Oracle挖礦",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             time.localtime().tm_hour % 4 == 0),
            action=self.game_actions.handle_oracle,
            priority=50,
            cooldown=14400  # 4小時冷卻
        ))
        
        self.add_task(Task(
            name="轉盤抽獎",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             time.localtime().tm_hour % 6 == 0),
            action=self.game_actions.handle_spin_wheel,
            priority=45,
            cooldown=21600  # 6小時冷卻
        ))
        
        self.add_task(Task(
            name="每日任務",
            condition=lambda: (self.state_detector.get_current_stage() == "主頁面" and
                             time.localtime().tm_hour == 20),
            action=self.game_actions.handle_mission,
            priority=40,
            cooldown=86400  # 24小時冷卻
        ))
    
    def _should_do_family(self) -> bool:
        """檢查是否應該執行家族任務"""
        try:
            # 檢查OCR結果
            img = self.device_manager.screenshot_manager.take_screenshot()
            if img is not None:
                result = self.game_actions.easyocr_reader.readtext(img, detail=0)
                from new_main_before20250514 import stage_by_str
                stage = stage_by_str(self.device_manager.device, result)
                return stage == "主頁面"
            return False
        except Exception as e:
            self.logger.error(f"檢查家族條件失敗: {e}")
            return False
    
    def get_task_statistics(self) -> Dict[str, Any]:
        """獲取任務執行統計"""
        stats = {
            'total_cycles': self.cycle_count,
            'task_history': self.task_history.copy(),
            'active_tasks': len([t for t in self.tasks if t.enabled]),
            'total_tasks': len(self.tasks)
        }
        return stats
    def reset_task_cooldown(self, task_name: str):
        """重置任務冷卻時間"""
        if task_name in self.task_history:
            self.task_history[task_name]['last_execution'] = 0
            self.logger.info(f"任務冷卻時間已重置: {task_name}")
            return True
        return False
    
    def _check_martial_soul(self) -> bool:
        """檢查武魂狀態"""
        try:
            img = self.state_detector.screenshot_manager.take_screenshot()
            if img is not None:
                elements = self.state_detector.detect_special_elements(img)
                return elements.get('martial_soul', False)
            return False
        except Exception:
            return False
    
    def _perform_online_check(self):
        """執行在線檢查"""
        device_config = config.DEVICE_CONFIGS.get(self.device_id, {})
        check_count = device_config.get('check_count', 6)
        check_interval = device_config.get('check_interval', 300)
        
        for i in range(check_count):
            # 實現在線檢查邏輯
            time.sleep(check_interval)
    
    def get_task_status(self) -> Dict[str, Any]:
        """獲取任務狀態"""
        return {
            'cycle_count': self.cycle_count,
            'total_tasks': len(self.tasks),
            'enabled_tasks': len([t for t in self.tasks if t.enabled]),
            'task_history': self.task_history
        }
