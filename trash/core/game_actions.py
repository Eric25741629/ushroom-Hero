# -*- coding: utf-8 -*-
"""
遊戲動作執行器 - 整合原有的遊戲邏輯函數
"""
import logging
import time
import random
import numpy as np
from typing import Optional, Union
from core.device_manager import DeviceManager
from core.state_detector import StateDetector
from config.game_config import config

class GameActions:
    """遊戲動作執行器"""
    
    def __init__(self, device_manager: DeviceManager, state_detector: StateDetector, 
                 easyocr_reader, cnn_model, oracle_model=None):
        self.device_manager = device_manager
        self.state_detector = state_detector
        self.easyocr_reader = easyocr_reader
        self.cnn_model = cnn_model
        self.oracle_model = oracle_model
        self.device = device_manager.device
        self.device_id = device_manager.device_id
        self.logger = logging.getLogger(f"GameActions-{self.device_id}")
        
        # 初始化各種管理器（需要時才導入，避免循環依賴）
        self._managers = {}
        
    def get_manager(self, manager_type: str):
        """延遲初始化管理器"""
        if manager_type not in self._managers:
            try:
                if manager_type == "parking":
                    from park import ParkingManager
                    self._managers[manager_type] = ParkingManager(
                        device=self.device, reader=self.easyocr_reader, 
                        ip=self.device_id, cnn_model=self.cnn_model
                    )
                elif manager_type == "battle":
                    import new_battle
                    self._managers[manager_type] = new_battle.BattleManager(
                        device=self.device, reader=self.easyocr_reader, 
                        cnn_model=self.cnn_model
                    )
                elif manager_type == "wheel":
                    from Spin_Wheel import spin_wheel
                    self._managers[manager_type] = spin_wheel(
                        device=self.device, cnn_model=self.cnn_model
                    )
                elif manager_type == "mission":
                    from Mission import mission
                    self._managers[manager_type] = mission(
                        device=self.device, ip=self.device_id
                    )
                elif manager_type == "family":
                    from family import Family_manager
                    self._managers[manager_type] = Family_manager(
                        device=self.device, ip=self.device_id, 
                        cnn_model=self.cnn_model
                    )
                elif manager_type == "assistant":
                    from Assistant import assistant
                    self._managers[manager_type] = assistant(
                        d=self.device, cnn_model=self.cnn_model
                    )
                elif manager_type == "mining":
                    from miner.Mining import MiningPlanner
                    import torch
                    self._managers[manager_type] = MiningPlanner(
                        self.oracle_model,
                        device='cuda' if torch.cuda.is_available() else 'cpu'
                    )
            except ImportError as e:
                self.logger.error(f"無法初始化管理器 {manager_type}: {e}")
                return None
        return self._managers.get(manager_type)
    
    # ===== 狀態處理動作 =====
    
    def handle_announcement(self) -> bool:
        """處理公告"""
        try:
            self.logger.info("處理公告")
            self.device_manager.click_safe(248, 812)
            time.sleep(1)
            self.click_white()
            time.sleep(1)
            return True
        except Exception as e:
            self.logger.error(f"處理公告失敗: {e}")
            return False
    
    def handle_reward(self) -> bool:
        """處理獎勵"""
        try:
            self.logger.info("處理獎勵")
            return self.reward()
        except Exception as e:
            self.logger.error(f"處理獎勵失敗: {e}")
            return False
    
    def handle_other_login(self) -> bool:
        """處理其他登錄"""
        try:
            self.logger.info("處理其他登錄")
            # 實現登出邏輯
            self.click_text("確定")
            time.sleep(2)
            self.device_manager.stop_game()
            time.sleep(3)
            return True
        except Exception as e:
            self.logger.error(f"處理其他登錄失敗: {e}")
            return False
    
    # ===== 主要遊戲動作 =====
    
    def handle_treasure_box(self) -> bool:
        """點擊寶箱"""
        try:
            x = random.randint(261, 271)
            y = 369
            self.device_manager.click_safe(x, y)
            time.sleep(1)
            return True
        except Exception as e:
            self.logger.error(f"點擊寶箱失敗: {e}")
            return False
    
    def handle_farm(self) -> bool:
        """處理農場"""
        try:
            self.logger.info("處理農場")
            return self.farm()
        except Exception as e:
            self.logger.error(f"處理農場失敗: {e}")
            return False
    
    def handle_martial_soul(self) -> bool:
        """處理武魂"""
        try:
            self.logger.info("處理武魂")
            return self.get_martial_soul()
        except Exception as e:
            self.logger.error(f"處理武魂失敗: {e}")
            return False
    
    def handle_skill_and_partner(self) -> bool:
        """處理技能和夥伴"""
        try:
            self.logger.info("處理技能和夥伴")
            from Skill import get_skill_and_partner
            get_skill_and_partner(self.device)
            time.sleep(3)
            return True
        except Exception as e:
            self.logger.error(f"處理技能和夥伴失敗: {e}")
            return False
    
    def handle_family(self) -> bool:
        """處理家族"""
        try:
            self.logger.info("處理家族")
            family_manager = self.get_manager("family")
            if family_manager:
                family_manager.go_to_family()
                return True
            return False
        except Exception as e:
            self.logger.error(f"處理家族失敗: {e}")
            return False
    
    def handle_assistant(self) -> bool:
        """處理助手"""
        try:
            self.logger.info("處理助手")
            assistant_manager = self.get_manager("assistant")
            if assistant_manager:
                assistant_manager.go_to_get_assistant()
                return True
            return False
        except Exception as e:
            self.logger.error(f"處理助手失敗: {e}")
            return False
    
    def handle_parking(self) -> bool:
        """處理停車"""
        try:
            self.logger.info("處理停車")
            parking_manager = self.get_manager("parking")
            if parking_manager:
                status = parking_manager.check_and_park()
                if status:
                    self.record_action_time("park")
                return status
            return False
        except Exception as e:
            self.logger.error(f"處理停車失敗: {e}")
            return False
    
    def handle_battle(self) -> bool:
        """處理戰鬥"""
        try:
            self.logger.info("處理戰鬥")
            battle_manager = self.get_manager("battle")
            if battle_manager:
                self.device_manager.click_safe(228, 926)
                time.sleep(2)
                battle_manager.execute_all_battles(check=True)
                return True
            return False
        except Exception as e:
            self.logger.error(f"處理戰鬥失敗: {e}")
            return False
    
    def handle_oracle(self) -> bool:
        """處理Oracle挖礦"""
        try:
            self.logger.info("處理Oracle挖礦")
            mining_manager = self.get_manager("mining")
            if mining_manager:
                from new_main_before20250514 import oralce
                oralce(self.device, self.easyocr_reader, mining_manager, self.device_id)
                return True
            return False
        except Exception as e:
            self.logger.error(f"處理Oracle挖礦失敗: {e}")
            return False
    
    def handle_spin_wheel(self) -> bool:
        """處理轉盤"""
        try:
            self.logger.info("處理轉盤")
            wheel_manager = self.get_manager("wheel")
            if wheel_manager:
                wheel_manager.spin()
                return True
            return False
        except Exception as e:
            self.logger.error(f"處理轉盤失敗: {e}")
            return False
    
    def handle_mission(self) -> bool:
        """處理任務"""
        try:
            self.logger.info("處理任務")
            mission_manager = self.get_manager("mission")
            if mission_manager:
                # 實現任務邏輯
                return True
            return False
        except Exception as e:
            self.logger.error(f"處理任務失敗: {e}")
            return False
    
    # ===== 輔助方法 =====
    
    def click_white(self):
        """點擊白色區域"""
        try:
            from tools import click_white
            click_white(self.device)
        except ImportError:
            # 如果沒有tools模塊，使用默認實現
            self.device_manager.click_safe(0.5, 0.5)
    
    def click_text(self, text: str) -> bool:
        """點擊包含指定文字的區域"""
        try:
            from new_main_before20250514 import click_str
            click_str(text, self.device, self.easyocr_reader)
            return True
        except Exception as e:
            self.logger.error(f"點擊文字 '{text}' 失敗: {e}")
            return False
    
    def reward(self) -> bool:
        """處理獎勵的具體實現"""
        try:
            from new_main_before20250514 import reward
            reward(self.device, self.easyocr_reader)
            return True
        except Exception as e:
            self.logger.error(f"獎勵處理失敗: {e}")
            return False
    
    def farm(self) -> bool:
        """農場的具體實現"""
        try:
            from new_main_before20250514 import farm
            farm(self.device, self.device_id, self.cnn_model)
            return True
        except Exception as e:
            self.logger.error(f"農場處理失敗: {e}")
            return False
    
    def get_martial_soul(self) -> bool:
        """武魂收集的具體實現"""
        try:
            from new_main_before20250514 import get_Martial_Soul
            get_Martial_Soul(self.device)
            return True
        except Exception as e:
            self.logger.error(f"武魂收集失敗: {e}")
            return False
    
    def record_action_time(self, action_name: str):
        """記錄動作時間"""
        try:
            from new_main_before20250514 import time_recording
            time_recording(self.device_id, name=action_name)
        except Exception as e:
            self.logger.error(f"記錄時間失敗 {action_name}: {e}")
    
    def check_martial_soul(self) -> bool:
        """檢查是否需要收集武魂"""
        try:
            # 實現檢查邏輯
            return True
        except Exception:
            return False
    
    def check_parking_expired(self) -> bool:
        """檢查停車是否過期"""
        try:
            from new_main_before20250514 import return_time, is_expired
            last_park_time = return_time(self.device_id, name="park")
            if last_park_time is None:
                return True
            try:
                timestamp = last_park_time["timestamp"]
                return is_expired(timestamp)
            except:
                return True
        except Exception as e:
            self.logger.error(f"檢查停車過期失敗: {e}")
            return True
