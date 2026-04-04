# -*- coding: utf-8 -*-
"""
時間管理器 - 處理時間記錄和到期檢查
"""
import time
import json
import os
import logging
import datetime
from typing import Optional, Dict, Any
from config.game_config import config

class TimeManager:
    """時間管理器"""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.filename = f"{device_id}_time.json"
        self.logger = logging.getLogger(f"TimeManager-{device_id}")
        
        # 確保數據文件存在
        self._ensure_data_file()
    
    def _ensure_data_file(self):
        """確保數據文件存在"""
        if not os.path.exists(self.filename):
            self._save_data({})
    
    def _load_data(self) -> Dict[str, Any]:
        """加載時間數據"""
        try:
            with open(self.filename, "r", encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.logger.warning(f"加載時間數據失敗: {e}")
            return {}
    
    def _save_data(self, data: Dict[str, Any]):
        """保存時間數據"""
        try:
            with open(self.filename, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存時間數據失敗: {e}")
    
    def record_action(self, action_name: str, extra_data: Dict[str, Any] = None):
        """
        記錄動作時間
        
        Args:
            action_name: 動作名稱
            extra_data: 額外數據
        """
        try:
            now = datetime.datetime.now(config.TPE)
            current_time = time.time()
            current_date = now.strftime("%Y-%m-%d")
            
            data = self._load_data()
            
            record = {
                "timestamp": current_time,
                "date": current_date,
                "datetime": now.isoformat()
            }
            
            # 添加額外數據
            if extra_data:
                record.update(extra_data)
            
            data[action_name] = record
            self._save_data(data)
            
            self.logger.debug(f"已記錄動作: {action_name}")
            
        except Exception as e:
            self.logger.error(f"記錄動作失敗 {action_name}: {e}")
    
    def get_last_action_time(self, action_name: str) -> Optional[Dict[str, Any]]:
        """
        獲取上次動作時間
        
        Args:
            action_name: 動作名稱
            
        Returns:
            時間記錄字典或None
        """
        try:
            data = self._load_data()
            record = data.get(action_name)
            
            if not record:
                return None
            
            # 處理舊格式數據（純浮點數時間戳）
            if isinstance(record, (int, float)):
                timestamp = float(record)
                recorded_date = datetime.datetime.fromtimestamp(timestamp, config.TPE).strftime("%Y-%m-%d")
                return {
                    "timestamp": timestamp,
                    "recorded_date": recorded_date,
                    "is_next_day": self._is_next_day(recorded_date)
                }
            
            # 新格式數據
            if isinstance(record, dict):
                timestamp = record.get("timestamp")
                recorded_date = record.get("date")
                
                if not timestamp or not recorded_date:
                    return None
                
                return {
                    "timestamp": timestamp,
                    "recorded_date": recorded_date,
                    "is_next_day": self._is_next_day(recorded_date),
                    "extra_data": {k: v for k, v in record.items() 
                                 if k not in ["timestamp", "date", "datetime"]}
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"獲取動作時間失敗 {action_name}: {e}")
            return None
    
    def is_action_expired(self, action_name: str, expire_seconds: int) -> bool:
        """
        檢查動作是否過期
        
        Args:
            action_name: 動作名稱
            expire_seconds: 過期時間（秒）
            
        Returns:
            是否過期
        """
        try:
            last_record = self.get_last_action_time(action_name)
            
            if not last_record:
                return True  # 沒有記錄視為過期
            
            # 檢查時間差異
            now = time.time()
            time_diff = now - last_record["timestamp"]
            time_exceeded = time_diff > expire_seconds
            
            # 檢查是否跨日
            is_next_day = last_record.get("is_next_day", False)
            
            # 任一條件滿足就過期
            expired = time_exceeded or is_next_day
            
            if expired:
                self.logger.debug(f"動作已過期 {action_name}: 時間差={time_diff:.1f}s, 跨日={is_next_day}")
            
            return expired
            
        except Exception as e:
            self.logger.error(f"檢查動作過期失敗 {action_name}: {e}")
            return True  # 出錯時視為過期
    
    def _is_next_day(self, recorded_date: str) -> bool:
        """檢查是否跨日"""
        try:
            current_date = datetime.datetime.now(config.TPE).strftime("%Y-%m-%d")
            return recorded_date != current_date
        except Exception:
            return False
    
    def get_time_until_expire(self, action_name: str, expire_seconds: int) -> Optional[float]:
        """
        獲取距離過期的剩餘時間
        
        Args:
            action_name: 動作名稱
            expire_seconds: 過期時間（秒）
            
        Returns:
            剩餘時間（秒）或None
        """
        try:
            last_record = self.get_last_action_time(action_name)
            
            if not last_record:
                return None
            
            # 如果已經跨日，直接過期
            if last_record.get("is_next_day", False):
                return 0
            
            # 計算剩餘時間
            now = time.time()
            elapsed = now - last_record["timestamp"]
            remaining = expire_seconds - elapsed
            
            return max(0, remaining)
            
        except Exception as e:
            self.logger.error(f"計算剩餘時間失敗 {action_name}: {e}")
            return None
    
    def get_action_frequency(self, action_name: str, days: int = 7) -> float:
        """
        獲取動作頻率（最近N天的平均執行次數）
        
        Args:
            action_name: 動作名稱
            days: 統計天數
            
        Returns:
            平均每天執行次數
        """
        try:
            # 這裡需要擴展數據結構來支持歷史記錄
            # 當前實現返回簡單估算
            last_record = self.get_last_action_time(action_name)
            if last_record:
                return 1.0 / days  # 簡單估算
            return 0.0
            
        except Exception as e:
            self.logger.error(f"計算動作頻率失敗 {action_name}: {e}")
            return 0.0
    
    def cleanup_old_records(self, days_to_keep: int = 30):
        """
        清理舊記錄
        
        Args:
            days_to_keep: 保留天數
        """
        try:
            data = self._load_data()
            cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
            
            cleaned_data = {}
            cleaned_count = 0
            
            for action_name, record in data.items():
                timestamp = None
                
                if isinstance(record, (int, float)):
                    timestamp = float(record)
                elif isinstance(record, dict):
                    timestamp = record.get("timestamp")
                
                if timestamp and timestamp >= cutoff_time:
                    cleaned_data[action_name] = record
                else:
                    cleaned_count += 1
            
            if cleaned_count > 0:
                self._save_data(cleaned_data)
                self.logger.info(f"已清理 {cleaned_count} 條舊記錄")
            
        except Exception as e:
            self.logger.error(f"清理舊記錄失敗: {e}")
    
    def get_all_records(self) -> Dict[str, Any]:
        """獲取所有記錄"""
        return self._load_data()
    
    def export_records(self, export_path: str):
        """
        導出記錄到文件
        
        Args:
            export_path: 導出文件路徑
        """
        try:
            data = self._load_data()
            
            # 添加導出時間信息
            export_data = {
                "export_time": datetime.datetime.now(config.TPE).isoformat(),
                "device_id": self.device_id,
                "records": data
            }
            
            with open(export_path, "w", encoding='utf-8') as f:
                json.dump(export_data, f, indent=4, ensure_ascii=False)
            
            self.logger.info(f"記錄已導出到: {export_path}")
            
        except Exception as e:
            self.logger.error(f"導出記錄失敗: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """獲取統計信息"""
        try:
            data = self._load_data()
            
            if not data:
                return {"total_actions": 0}
            
            timestamps = []
            for record in data.values():
                if isinstance(record, (int, float)):
                    timestamps.append(float(record))
                elif isinstance(record, dict) and "timestamp" in record:
                    timestamps.append(record["timestamp"])
            
            if not timestamps:
                return {"total_actions": len(data)}
            
            now = time.time()
            latest = max(timestamps)
            earliest = min(timestamps)
            
            return {
                "total_actions": len(data),
                "earliest_record": datetime.datetime.fromtimestamp(earliest, config.TPE).isoformat(),
                "latest_record": datetime.datetime.fromtimestamp(latest, config.TPE).isoformat(),
                "time_span_days": (latest - earliest) / (24 * 60 * 60),
                "last_activity_hours_ago": (now - latest) / 3600
            }
            
        except Exception as e:
            self.logger.error(f"獲取統計信息失敗: {e}")
            return {"error": str(e)}
