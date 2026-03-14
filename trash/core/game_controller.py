# -*- coding: utf-8 -*-
"""
主遊戲控制器 - 整合所有模組的核心控制類
"""
import logging
import time
import threading
from typing import Dict, Any, Optional
from core.device_manager import DeviceManager
from core.state_detector import StateDetector
from core.task_scheduler import TaskScheduler
from core.time_manager import TimeManager
from config.game_config import config

class GameController:
    """主遊戲控制器"""
    
    def __init__(self, device_id: str, cnn_model, easyocr_reader, oracle_model=None):
        self.device_id = device_id
        self.logger = logging.getLogger(f"GameController-{device_id}")
        
        # 初始化核心組件
        self.device_manager = DeviceManager(device_id)
        self.state_detector = StateDetector(
            self.device_manager.device, device_id, cnn_model, easyocr_reader
        )
        self.task_scheduler = TaskScheduler(
            self.device_manager, self.state_detector, easyocr_reader, cnn_model, oracle_model
        )
        self.time_manager = TimeManager(device_id)
        
        # 運行狀態
        self.is_running = False
        self.wake_up_time = time.time()
        self.total_cycles = 0
        self.successful_cycles = 0
        
        # 統計信息
        self.stats = {
            'start_time': None,
            'last_activity': None,
            'errors': 0,
            'warnings': 0
        }
        
        # 錯誤恢復
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5
        
        self.logger.info(f"遊戲控制器初始化完成: {device_id}")
    
    def start(self):
        """啟動遊戲控制器"""
        if self.is_running:
            self.logger.warning("控制器已在運行中")
            return
        
        self.is_running = True
        self.stats['start_time'] = time.time()
        self.wake_up_time = time.time()
        
        self.logger.info("遊戲控制器啟動")
        
        try:
            self._main_loop()
        except KeyboardInterrupt:
            self.logger.info("收到中斷信號，正在停止...")
        except Exception as e:
            self.logger.error(f"主循環異常: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """停止遊戲控制器"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        try:
            # 清理資源
            self.device_manager.cleanup()
            self.state_detector.clear_cache()
            
            # 記錄統計信息
            self._log_final_stats()
            
        except Exception as e:
            self.logger.error(f"停止過程中發生錯誤: {e}")
        
        self.logger.info("遊戲控制器已停止")
    
    def _main_loop(self):
        """主循環"""
        while self.is_running:
            try:
                cycle_start = time.time()
                
                # 執行任務循環
                success = self.task_scheduler.execute_cycle()
                self.total_cycles += 1
                
                if success:
                    self.successful_cycles += 1
                    self.consecutive_errors = 0
                    self.stats['last_activity'] = time.time()
                else:
                    self.consecutive_errors += 1
                    self.stats['errors'] += 1
                
                # 錯誤恢復檢查
                if self.consecutive_errors >= self.max_consecutive_errors:
                    self.logger.error(f"連續錯誤次數過多 ({self.consecutive_errors})，執行恢復程序")
                    self._perform_error_recovery()
                
                # 檢查是否需要休眠
                if self._should_sleep():
                    self._enter_sleep_mode()
                    continue
                
                # 循環間隔控制
                cycle_duration = time.time() - cycle_start
                min_cycle_time = 5  # 最小循環時間
                
                if cycle_duration < min_cycle_time:
                    time.sleep(min_cycle_time - cycle_duration)
                
            except Exception as e:
                self.logger.error(f"主循環錯誤: {e}")
                self.stats['errors'] += 1
                time.sleep(10)  # 錯誤後等待
    
    def _should_sleep(self) -> bool:
        """判斷是否應該進入休眠模式"""
        current_time = time.localtime()
        
        # 檢查停車狀態
        last_park_time = self.time_manager.get_last_action_time("park")
        if last_park_time:
            park_expired = self.time_manager.is_action_expired(
                "park", config.PARK_EXPIRED_TIME
            )
        else:
            park_expired = True
        
        # 檢查最小運行時間
        min_wake_time_passed = (time.time() - self.wake_up_time) > config.WAKE_UP_MIN_TIME
        
        # 休眠條件
        sleep_conditions = [
            # 整點且為偶數小時
            current_time.tm_min == 0 and current_time.tm_hour % 2 == 0,
            # 早上9點前的整點
            current_time.tm_hour < 9 and current_time.tm_min == 0,
            # 停車過期且過了早上8點
            park_expired and current_time.tm_hour > 8 and min_wake_time_passed,
            # 23:00或23:45
            (current_time.tm_hour == 23 and current_time.tm_min in [0, 45])
        ]
        
        return any(sleep_conditions)
    
    def _enter_sleep_mode(self):
        """進入休眠模式"""
        self.logger.info("進入休眠模式")
        
        # 清理並關閉遊戲
        self.device_manager.cleanup()
        
        # 更新喚醒時間
        self.wake_up_time = time.time()
        
        # 休眠循環
        while self.is_running:
            time.sleep(config.CHECK_INTERVAL)
            
            if not self._should_sleep():
                self.logger.info("休眠結束，恢復運行")
                break
    
    def _perform_error_recovery(self):
        """執行錯誤恢復程序"""
        self.logger.info("開始錯誤恢復程序")
        
        try:
            # 1. 停止遊戲
            self.device_manager.stop_game()
            time.sleep(5)
            
            # 2. 重新連接設備
            if not self.device_manager.connect():
                self.logger.error("設備重連失敗")
                return
            
            # 3. 清除緩存
            self.state_detector.clear_cache()
            
            # 4. 重新啟動遊戲
            if self.device_manager.start_game():
                self.consecutive_errors = 0
                self.logger.info("錯誤恢復成功")
            else:
                self.logger.error("錯誤恢復失敗")
            
        except Exception as e:
            self.logger.error(f"錯誤恢復過程失敗: {e}")
    
    def _log_final_stats(self):
        """記錄最終統計信息"""
        if self.stats['start_time']:
            total_time = time.time() - self.stats['start_time']
            success_rate = (self.successful_cycles / max(self.total_cycles, 1)) * 100
            
            self.logger.info(f"運行統計:")
            self.logger.info(f"  總運行時間: {total_time/3600:.1f} 小時")
            self.logger.info(f"  總循環次數: {self.total_cycles}")
            self.logger.info(f"  成功循環: {self.successful_cycles}")
            self.logger.info(f"  成功率: {success_rate:.1f}%")
            self.logger.info(f"  錯誤次數: {self.stats['errors']}")
            
            # 獲取檢測統計
            detection_stats = self.state_detector.get_detection_stats()
            self.logger.info(f"  CNN成功率: {detection_stats.get('cnn_success_rate', 0):.1f}%")
            self.logger.info(f"  OCR成功率: {detection_stats.get('ocr_success_rate', 0):.1f}%")
    
    def get_status(self) -> Dict[str, Any]:
        """獲取控制器狀態"""
        current_time = time.time()
        
        status = {
            'device_id': self.device_id,
            'is_running': self.is_running,
            'uptime': current_time - self.stats['start_time'] if self.stats['start_time'] else 0,
            'total_cycles': self.total_cycles,
            'successful_cycles': self.successful_cycles,
            'success_rate': (self.successful_cycles / max(self.total_cycles, 1)) * 100,
            'consecutive_errors': self.consecutive_errors,
            'current_stage': self.state_detector.get_current_stage() if self.is_running else 'stopped',
            'device_info': self.device_manager.get_device_info(),
            'task_status': self.task_scheduler.get_task_status(),
            'detection_stats': self.state_detector.get_detection_stats(),
            'time_stats': self.time_manager.get_statistics()
        }
        
        return status
    
    def force_execute_task(self, task_name: str) -> bool:
        """強制執行指定任務"""
        for task in self.task_scheduler.tasks:
            if task.name == task_name:
                self.logger.info(f"強制執行任務: {task_name}")
                return self.task_scheduler._execute_task(task)
        
        self.logger.warning(f"未找到任務: {task_name}")
        return False
    
    def enable_task(self, task_name: str, enabled: bool = True):
        """啟用/禁用任務"""
        self.task_scheduler.enable_task(task_name, enabled)
    
    def get_task_list(self) -> list:
        """獲取任務列表"""
        return [
            {
                'name': task.name,
                'enabled': task.enabled,
                'priority': task.priority,
                'cooldown': task.cooldown,
                'last_execution': self.task_scheduler.task_history.get(task.name, {}).get('last_execution')
            }
            for task in self.task_scheduler.tasks
        ]

class GameAutomationManager:
    """遊戲自動化管理器 - 管理多個設備控制器"""
    
    def __init__(self, cnn_model, easyocr_reader, oracle_model=None):
        self.cnn_model = cnn_model
        self.easyocr_reader = easyocr_reader
        self.oracle_model = oracle_model
        self.logger = logging.getLogger("GameAutomationManager")
        
        # 控制器字典
        self.controllers: Dict[str, GameController] = {}
        self.controller_threads: Dict[str, threading.Thread] = {}
        
        # 管理器狀態
        self.is_running = False
    
    def add_device(self, device_id: str) -> bool:
        """添加設備"""
        if device_id in self.controllers:
            self.logger.warning(f"設備已存在: {device_id}")
            return False
        
        try:
            controller = GameController(
                device_id, self.cnn_model, self.easyocr_reader, self.oracle_model
            )
            self.controllers[device_id] = controller
            self.logger.info(f"設備已添加: {device_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"添加設備失敗 {device_id}: {e}")
            return False
    
    def remove_device(self, device_id: str) -> bool:
        """移除設備"""
        if device_id not in self.controllers:
            self.logger.warning(f"設備不存在: {device_id}")
            return False
        
        try:
            # 停止控制器
            if device_id in self.controller_threads:
                self.controllers[device_id].stop()
                self.controller_threads[device_id].join(timeout=10)
                del self.controller_threads[device_id]
            
            del self.controllers[device_id]
            self.logger.info(f"設備已移除: {device_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"移除設備失敗 {device_id}: {e}")
            return False
    
    def start_device(self, device_id: str) -> bool:
        """啟動指定設備"""
        if device_id not in self.controllers:
            self.logger.error(f"設備不存在: {device_id}")
            return False
        
        if device_id in self.controller_threads and self.controller_threads[device_id].is_alive():
            self.logger.warning(f"設備已在運行: {device_id}")
            return False
        
        try:
            controller = self.controllers[device_id]
            thread = threading.Thread(
                target=controller.start,
                name=f"GameController-{device_id}",
                daemon=True
            )
            
            self.controller_threads[device_id] = thread
            thread.start()
            
            self.logger.info(f"設備已啟動: {device_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"啟動設備失敗 {device_id}: {e}")
            return False
    
    def stop_device(self, device_id: str) -> bool:
        """停止指定設備"""
        if device_id not in self.controllers:
            self.logger.error(f"設備不存在: {device_id}")
            return False
        
        try:
            controller = self.controllers[device_id]
            controller.stop()
            
            if device_id in self.controller_threads:
                self.controller_threads[device_id].join(timeout=10)
                del self.controller_threads[device_id]
            
            self.logger.info(f"設備已停止: {device_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"停止設備失敗 {device_id}: {e}")
            return False
    
    def start_all(self):
        """啟動所有設備"""
        self.is_running = True
        self.logger.info("啟動所有設備控制器")
        
        for device_id in self.controllers:
            if device_id not in config.EXCLUDED_DEVICES:
                self.start_device(device_id)
    
    def stop_all(self):
        """停止所有設備"""
        self.is_running = False
        self.logger.info("停止所有設備控制器")
        
        for device_id in list(self.controllers.keys()):
            self.stop_device(device_id)
    
    def get_overall_status(self) -> Dict[str, Any]:
        """獲取整體狀態"""
        device_statuses = {}
        
        for device_id, controller in self.controllers.items():
            device_statuses[device_id] = controller.get_status()
        
        # 計算整體統計
        total_devices = len(self.controllers)
        running_devices = sum(1 for status in device_statuses.values() if status['is_running'])
        
        return {
            'total_devices': total_devices,
            'running_devices': running_devices,
            'manager_running': self.is_running,
            'device_statuses': device_statuses
        }
