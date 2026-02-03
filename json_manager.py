"""
統一的JSON數據管理器
整合了原本散佈在各個檔案中的JSON操作功能
"""

import json
import os
import datetime
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, Union, List

import pytz
from pytz import exceptions as pytz_exceptions


def _safe_cast(value: Any, caster: Callable[[Any], Any], default: Any) -> Any:
    """Safely cast a value to the desired type, returning a default on failure."""
    try:
        return caster(value)
    except (TypeError, ValueError):
        return default


@dataclass
class MarketState:
    """Structured representation of the market section inside JSON data."""

    timestamp: float = 0.0
    buy_num: int = 0
    check_time: int = 0

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "MarketState":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            timestamp=_safe_cast(payload.get("car_market_timestamp"), float, 0.0),
            buy_num=_safe_cast(payload.get("car_market_buy_num"), int, 0),
            check_time=_safe_cast(payload.get("car_market_check_time"), int, 0),
        )

    def to_payload(self) -> Dict[str, Any]:
        return {
            "car_market_timestamp": float(self.timestamp),
            "car_market_buy_num": int(self.buy_num),
            "car_market_check_time": int(self.check_time),
        }

    def reset_counters(self) -> None:
        self.buy_num = 0
        self.check_time = 0


@dataclass
class PurchaseCountState:
    """Simple purchase state containing timestamp and buy count."""

    timestamp: float = 0.0
    buy_num: int = 0

    @classmethod
    def from_data(cls, data: Dict[str, Any], timestamp_key: str, buy_key: str) -> "PurchaseCountState":
        if not isinstance(data, dict):
            data = {}
        return cls(
            timestamp=_safe_cast(data.get(timestamp_key), float, 0.0),
            buy_num=_safe_cast(data.get(buy_key), int, 0),
        )

    def apply_to(self, data: Dict[str, Any], timestamp_key: str, buy_key: str) -> None:
        data[timestamp_key] = float(self.timestamp)
        data[buy_key] = int(self.buy_num)


class JsonDataManager:
    """統一的JSON數據管理器"""
    
    def __init__(self, device_id: str, file_suffix: str = ""):
        """
        初始化JSON管理器
        
        Args:
            device_id: 設備ID
            file_suffix: 檔案後綴，用於區分不同類型的數據文件
        """
        self.device_id = device_id
        self.file_suffix = file_suffix
        self.timezone = pytz.timezone('Asia/Taipei')
        
    def get_filename(self) -> str:
        """獲取JSON檔案名稱"""
        if self.file_suffix:
            return f"{self.device_id}_{self.file_suffix}.json"
        return f"{self.device_id}.json"
    
    def load_data(self, default_data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        載入JSON數據，保護現有數據不被覆寫
        
        Args:
            default_data: 默認數據結構（僅用於新文件）
            
        Returns:
            載入的數據字典
        """
        filename = self.get_filename()
        
        if default_data is None:
            default_data = {}
            
        if not os.path.exists(filename):
            # 文件不存在，創建並寫入默認結構
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(default_data, f, indent=4, ensure_ascii=False)
            return default_data
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 只返回現有數據，不自動補全缺失的鍵值
            # 這樣可以保護用戶現有的數據結構
            return data
            
        except (json.JSONDecodeError, ValueError) as e:
            print(f"JSON文件損壞，但保留原文件避免數據丟失: {e}")
            # 文件損壞時，不直接覆寫，而是創建備份
            backup_filename = f"{filename}.backup_{int(time.time())}"
            try:
                import shutil
                shutil.copy2(filename, backup_filename)
                print(f"已創建備份文件: {backup_filename}")
            except Exception:
                pass
            return default_data
    
    def save_data(self, data: Dict[str, Any]) -> bool:
        """
        保存數據到JSON文件
        
        Args:
            data: 要保存的數據
            
        Returns:
            是否保存成功
        """
        try:
            filename = self.get_filename()
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"保存JSON文件失敗: {e}")
            return False
    
    def record_timestamp(self, name: str, additional_data: Optional[Dict] = None) -> None:
        """
        記錄時間戳記和額外數據
        
        Args:
            name: 記錄名稱
            additional_data: 額外的數據
        """
        data = self.load_data()
        now = datetime.datetime.now(self.timezone)
        
        record = {
            "timestamp": now.timestamp(),
            "date": now.strftime("%Y-%m-%d"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        if additional_data:
            record.update(additional_data)
            
        data[name] = record
        self.save_data(data)
        print(f"已記錄 {name} 的時間戳記: {record['datetime']}")
    
    def get_record(self, name: str) -> Optional[Dict[str, Any]]:
        """
        獲取指定名稱的記錄
        
        Args:
            name: 記錄名稱
            
        Returns:
            記錄數據，如果不存在則返回None
        """
        data = self.load_data()
        return data.get(name)
    
    def is_same_day(self, name: str) -> bool:
        """
        檢查記錄是否為同一天
        
        Args:
            name: 記錄名稱
            
        Returns:
            是否為同一天
        """
        record = self.get_record(name)
        if not record:
            return False
            
        try:
            if 'date' in record:
                recorded_date = record['date']
            elif 'timestamp' in record:
                timestamp = float(record['timestamp'])
                recorded_date = datetime.datetime.fromtimestamp(timestamp, self.timezone).strftime("%Y-%m-%d")
            else:
                return False
                
            current_date = datetime.datetime.now(self.timezone).strftime("%Y-%m-%d")
            return recorded_date == current_date
            
        except (ValueError, TypeError, OSError):
            return False
    
    def is_same_week(self, name: str) -> bool:
        """
        檢查記錄是否為同一週（ISO週）
        
        Args:
            name: 記錄名稱
            
        Returns:
            是否為同一週
        """
        record = self.get_record(name)
        if not record:
            return False
            
        try:
            if 'timestamp' in record:
                timestamp = float(record['timestamp'])
                recorded_date = datetime.datetime.fromtimestamp(timestamp, self.timezone).date()
            elif 'date' in record:
                recorded_date = datetime.datetime.strptime(record['date'], "%Y-%m-%d").date()
            else:
                return False
                
            current_date = datetime.datetime.now(self.timezone).date()
            
            # 使用ISO週進行比較
            recorded_year, recorded_week, _ = recorded_date.isocalendar()
            current_year, current_week, _ = current_date.isocalendar()
            
            return recorded_year == current_year and recorded_week == current_week
            
        except (ValueError, TypeError, OSError):
            return False
    
    def is_expired(self, name: str, expired_seconds: int = 3600) -> bool:
        """
        檢查記錄是否已過期
        
        Args:
            name: 記錄名稱
            expired_seconds: 過期秒數
            
        Returns:
            是否已過期
        """
        record = self.get_record(name)
        if not record:
            return True  # 沒有記錄視為已過期
            
        try:
            if 'timestamp' in record:
                timestamp = float(record['timestamp'])
            else:
                return True
                
            current_time = time.time()
            return (current_time - timestamp) > expired_seconds
            
        except (ValueError, TypeError):
            return True
    
    def is_expired_or_next_day(self, name: str, expired_seconds: int = 3600) -> bool:
        """
        檢查是否過期或跨日
        
        Args:
            name: 記錄名稱
            expired_seconds: 過期秒數
            
        Returns:
            是否過期或跨日
        """
        return self.is_expired(name, expired_seconds) or not self.is_same_day(name)
    
    def get_numeric_value(self, name: str, key: str, default_value: Union[int, float] = 0) -> Union[int, float]:
        """
        獲取記錄中的數字值
        
        Args:
            name: 記錄名稱
            key: 鍵名
            default_value: 默認值
            
        Returns:
            數字值
        """
        record = self.get_record(name)
        if not record:
            return default_value
            
        value = record.get(key, default_value)
        caster: Callable[[Any], Any] = type(default_value)
        if caster is bool:
            return bool(value)
        return _safe_cast(value, caster, default_value)
    
    def update_numeric_value(self, name: str, key: str, value: Union[int, float]) -> None:
        """
        更新記錄中的數字值
        
        Args:
            name: 記錄名稱
            key: 鍵名
            value: 新值
        """
        data = self.load_data()
        if name not in data:
            data[name] = {}
            
        data[name][key] = value
        self.save_data(data)


class ParkMarketDataManager(JsonDataManager):
    """停車場市場數據管理器，專門處理停車相關的JSON數據"""

    SECTION_KEYS = ("daily", "weekly")

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")  # 統一使用 {device_id}.json，不使用後綴

    def get_default_structure(self) -> Dict[str, Any]:
        """獲取默認的數據結構"""
        template = MarketState().to_payload()
        return {key: template.copy() for key in self.SECTION_KEYS}

    def load_data(self) -> Dict[str, Any]:
        """載入停車市場數據，兼容現有數據格式"""
        data = super().load_data({})  # 不提供默認結構，保護現有數據
        if self._normalize_sections(data):
            self.save_data(data)
        return data

    def get_buy_data(self) -> Tuple[float, int, float, int, int, int]:
        """
        獲取購買數據

        Returns:
            (daily_timestamp, daily_buy_num, weekly_timestamp, weekly_buy_num, daily_check_time, weekly_check_time)
        """
        states = self._load_states()

        daily_state = states["daily"]
        weekly_state = states["weekly"]

        if daily_state.timestamp > 0 and not self._is_same_day_from_timestamp(daily_state.timestamp):
            daily_state = MarketState(timestamp=daily_state.timestamp)

        if weekly_state.timestamp > 0 and not self._is_same_week_from_timestamp(weekly_state.timestamp):
            weekly_state = MarketState(timestamp=weekly_state.timestamp)

        return (
            daily_state.timestamp,
            daily_state.buy_num,
            weekly_state.timestamp,
            weekly_state.buy_num,
            daily_state.check_time,
            weekly_state.check_time,
        )

    def _normalize_sections(self, data: Dict[str, Any]) -> bool:
        """確保 daily/weekly 區塊結構完整且型別正確。"""
        changed = False
        for key in self.SECTION_KEYS:
            original_payload = data.get(key)
            state = MarketState.from_payload(original_payload)
            normalized_payload = state.to_payload()
            if normalized_payload != original_payload:
                data[key] = normalized_payload
                changed = True
        return changed

    def _load_states(self) -> Dict[str, MarketState]:
        data = self.load_data()
        return {key: MarketState.from_payload(data.get(key)) for key in self.SECTION_KEYS}
    
    def _is_same_day_from_timestamp(self, timestamp: float) -> bool:
        """從時間戳檢查是否為同一天"""
        try:
            recorded_date = datetime.datetime.fromtimestamp(timestamp, self.timezone).date()
            current_date = datetime.datetime.now(self.timezone).date()
            return recorded_date == current_date
        except (OSError, OverflowError, ValueError, TypeError):
            return False
    
    def _is_same_week_from_timestamp(self, timestamp: float) -> bool:
        """從時間戳檢查是否為同一週"""
        try:
            recorded_date = datetime.datetime.fromtimestamp(timestamp, self.timezone).date()
            current_date = datetime.datetime.now(self.timezone).date()
            
            recorded_year, recorded_week, _ = recorded_date.isocalendar()
            current_year, current_week, _ = current_date.isocalendar()
            
            return recorded_year == current_year and recorded_week == current_week
        except (OSError, OverflowError, ValueError, TypeError):
            return False
    
    def record_purchase(self, mode: str, buy_num: int, check_time: int = 0) -> None:
        """
        記錄購買數據
        
        Args:
            mode: 'daily' 或 'weekly'
            buy_num: 這次實際購買的數量（不是累計總數）
            check_time: 這次的檢查次數（不是累計總數）
        """
        if mode not in self.SECTION_KEYS:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")

        try:
            data = self.load_data()
            state = MarketState.from_payload(data.get(mode))

            now_ts = datetime.datetime.now(self.timezone).timestamp()
            buy_increment = _safe_cast(buy_num, int, 0)
            check_increment = _safe_cast(check_time, int, 0)

            if mode == "daily":
                should_reset = not self._is_same_day_from_timestamp(state.timestamp)
            else:
                should_reset = not self._is_same_week_from_timestamp(state.timestamp)

            if should_reset:
                state.buy_num = buy_increment
                state.check_time = check_increment
                print(f"{mode} 數據已重置，新的購買數量: {state.buy_num}, 新的檢查次數: {state.check_time}")
            else:
                state.buy_num += buy_increment
                state.check_time += check_increment
                print(f"{mode} 數據累加，總購買數量: {state.buy_num}, 總檢查次數: {state.check_time}")

            state.timestamp = float(now_ts)
            data[mode] = state.to_payload()

            self.save_data(data)
            print(f"已記錄 {mode} 購買數據，總數量: {state.buy_num}, 總檢查次數: {state.check_time}")

        except Exception as e:
            print(f"記錄購買數據失敗: {e}")
    
    def should_purchase(self, mode: str, max_purchases: int = 2, max_checks: int = 2) -> bool:
        """
        檢查是否需要購買
        
        Args:
            mode: 'daily' 或 'weekly'
            max_purchases: 最大購買次數
            max_checks: 最大檢查次數（防止無限循環檢查）
            
        Returns:
            是否需要購買
        """
        if mode not in self.SECTION_KEYS:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")

        states = self._load_states()
        state = states[mode]

        if mode == "daily":
            is_new_cycle = not self._is_same_day_from_timestamp(state.timestamp)
            label = "Daily"
            unit = "天"
        else:
            is_new_cycle = not self._is_same_week_from_timestamp(state.timestamp)
            label = "Weekly"
            unit = "週"

        within_purchase_limit = state.buy_num < max_purchases
        within_check_limit = state.check_time < max_checks

        should_buy = is_new_cycle or (within_purchase_limit and within_check_limit)
        print(
            f"{label} 檢查: 新的一{unit}={is_new_cycle}, 購買次數={state.buy_num}/{max_purchases}, "
            f"檢查次數={state.check_time}/{max_checks}, 應該購買={should_buy}"
        )
        return should_buy


class FamilyMarketDataManager(JsonDataManager):
    """家族市場數據管理器，統一處理家族每日/每週購買紀錄"""

    MODE_KEY_MAP = {
        "daily": ("family_market_timestamp", "family_market_buy_num"),
        "weekly": ("week_family_market_timestamp", "week_family_market_buy_num"),
    }

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")

    def _get_keys(self, mode: str) -> Tuple[str, str]:
        if mode not in self.MODE_KEY_MAP:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")
        return self.MODE_KEY_MAP[mode]

    def get_state(self, mode: str) -> PurchaseCountState:
        timestamp_key, buy_key = self._get_keys(mode)
        data = self.load_data()
        return PurchaseCountState.from_data(data, timestamp_key, buy_key)

    def set_state(self, mode: str, buy_num: int, timestamp: Optional[float] = None) -> None:
        timestamp_key, buy_key = self._get_keys(mode)
        data = self.load_data()

        state = PurchaseCountState(
            timestamp=float(timestamp)
            if timestamp is not None
            else datetime.datetime.now(self.timezone).timestamp(),
            buy_num=_safe_cast(buy_num, int, 0),
        )

        state.apply_to(data, timestamp_key, buy_key)
        self.save_data(data)

    def reset_state(self, mode: str) -> None:
        self.set_state(mode, buy_num=0, timestamp=0.0)

    def increment_buy_num(self, mode: str, increment: int = 1) -> PurchaseCountState:
        timestamp_key, buy_key = self._get_keys(mode)
        data = self.load_data()
        state = PurchaseCountState.from_data(data, timestamp_key, buy_key)
        state.buy_num += _safe_cast(increment, int, 0)
        state.timestamp = datetime.datetime.now(self.timezone).timestamp()
        state.apply_to(data, timestamp_key, buy_key)
        self.save_data(data)
        return state


class TimeRecordDataManager(JsonDataManager):
    """時間記錄數據管理器，處理各種行為的時間記錄"""
    
    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")  # 統一使用 {device_id}.json，與停車數據共用同一檔案
    
    def record_time(self, name: str) -> None:
        """記錄指定行為的時間"""
        self.record_timestamp(name)
    
    def get_time_record(self, name: str) -> Optional[Dict[str, Any]]:
        """獲取時間記錄，兼容舊格式"""
        record = self.get_record(name)
        
        if record is None:
            return None
            
        # 舊格式可能為單純的 timestamp（float 或可轉換）
        if isinstance(record, (int, float, str)):
            timestamp = _safe_cast(record, float, 0.0)
            if timestamp <= 0:
                return None
            try:
                recorded_dt = datetime.datetime.fromtimestamp(timestamp, self.timezone)
                recorded_date = recorded_dt.strftime("%Y-%m-%d")
                recorded_date_obj = recorded_dt.date()
                current_date = datetime.datetime.now(self.timezone).date()

                recorded_year, recorded_week, _ = recorded_date_obj.isocalendar()
                current_year, current_week, _ = current_date.isocalendar()

                return {
                    "timestamp": timestamp,
                    "recorded_date": recorded_date,
                    "is_next_day": recorded_date_obj != current_date,
                    "is_next_week": (recorded_year, recorded_week) != (current_year, current_week),
                }
            except (OSError, OverflowError, ValueError):
                return None

        if isinstance(record, dict):
            timestamp = _safe_cast(record.get("timestamp"), float, 0.0)
            recorded_date = record.get("date")

            recorded_date_obj = None

            if recorded_date:
                try:
                    recorded_date_obj = datetime.datetime.strptime(recorded_date, "%Y-%m-%d").date()
                except ValueError:
                    recorded_date_obj = None

            if not recorded_date_obj and timestamp > 0:
                try:
                    recorded_dt = datetime.datetime.fromtimestamp(timestamp, self.timezone)
                    recorded_date = recorded_dt.strftime("%Y-%m-%d")
                    recorded_date_obj = recorded_dt.date()
                except (OSError, OverflowError, ValueError):
                    recorded_date = None
                    recorded_date_obj = None

            if not recorded_date_obj or timestamp <= 0:
                return None

            current_date = datetime.datetime.now(self.timezone).date()
            recorded_year, recorded_week, _ = recorded_date_obj.isocalendar()
            current_year, current_week, _ = current_date.isocalendar()

            return {
                "timestamp": timestamp,
                "recorded_date": recorded_date,
                "is_next_day": recorded_date_obj != current_date,
                "is_next_week": (recorded_year, recorded_week) != (current_year, current_week),
            }

        return None


import datetime
import copy
import warnings
from typing import Any, Dict, Optional

import pytz
from pytz import exceptions as pytz_exceptions


class StoreDataManager(JsonDataManager):
    """商店數據管理器，處理購買記錄（過渡期相容舊欄位）"""

    def __init__(self, device_id: str, timezone_str: str = "Asia/Taipei"):
        # 使用 {device_id}.json 與其他管理器共用
        super().__init__(device_id, file_suffix="")
        # 若上層沒設 timezone，這裡自訂一個
        try:
            self.timezone = getattr(self, "timezone", pytz.timezone(timezone_str))
        except Exception:
            self.timezone = pytz.timezone("Asia/Taipei")

    # -------------------------
    # 寫入：統一以 UTC 儲存 + 相容舊欄位
    # -------------------------
    def record_purchase(self, title: str, content: Dict[str, Any]) -> None:
        """
        記錄購買行為（統一 UTC 儲存，並寫入相容欄位）
        欄位：
          - last_time_utc: ISO 8601，UTC，例：2025-10-25T16:40:48Z
          - last_time: 舊相容欄位，UTC、無時區字串，例：2025-10-25 16:40:48
          - last_time_local: （可選）self.timezone 的 ISO 8601，例：2025-10-26T00:40:48+08:00
        """
        data = self.load_data()
        payload = copy.deepcopy(content)

        utc_now = datetime.datetime.now(datetime.timezone.utc)
        payload["last_time_utc"] = utc_now.isoformat().replace("+00:00", "Z")
        payload["last_time"] = utc_now.strftime("%Y-%m-%d %H:%M:%S")  # 舊欄位，仍寫 UTC（無時區）
        try:
            local_now = utc_now.astimezone(self.timezone)
            payload["last_time_local"] = local_now.isoformat()
        except Exception:
            pass

        # 可選：自動維護 count（如需要）
        if "count" in payload:
            try:
                payload["count"] = int(payload["count"])
            except Exception:
                payload["count"] = 1
        else:
            # 若有舊值則 +1，否則設 1
            old = data.get(title, {})
            try:
                payload["count"] = int(old.get("count", 0)) + 1
            except Exception:
                payload["count"] = 1

        # schema 標示（方便之後清舊碼）
        payload["_schema"] = "utc_v1"

        data[title] = payload
        self.save_data(data)
        print(f"已記錄購買項目 '{title}': {payload}")

    # -------------------------
    # 讀取：相容舊格式
    # -------------------------
    def get_purchase_record(self, title: str) -> Optional[Dict[str, Any]]:
        """獲取購買記錄"""
        return self.get_record(title)

    def is_purchased_today(self, title: str, period: str = "day") -> bool:
        """檢查是否已購買。

        參數:
          - title: 購買項目名稱
          - period: 'day' (預設) 或 'week'。

        行為:
          - period='day'：以 self.timezone 的當地日期判斷（原本行為）。
          - period='week'：以 ISO 週（週一為一週起始）判斷，若記錄與現在屬於同一 ISO 週則回傳 True，反之 False。
        """
        record = self.get_purchase_record(title)
        print(record)
        if not record:
            return False

        try:
            local_dt = self._extract_last_time_as_local(record)
            if local_dt is None:
                return False

            now_local = datetime.datetime.now(self.timezone)

            if period == "day":
                # 原本的日比較
                print(f"比較日期：記錄={local_dt.date()}，現在={now_local.date()}")
                return local_dt.date() == now_local.date()
            elif period == "week":
                # 使用 ISO 週，比較 (year, week)
                rec_year, rec_week, _ = local_dt.date().isocalendar()
                now_year, now_week, _ = now_local.date().isocalendar()
                print(f"比較週：記錄=(Y{rec_year},W{rec_week})，現在=(Y{now_year},W{now_week})")
                return rec_year == now_year and rec_week == now_week
            else:
                # 未知 period，回退到日比較以保持相容
                print(f"未知的 period={period}，使用預設日比較")
                return local_dt.date() == now_local.date()
        except (ValueError, AttributeError, pytz_exceptions.PytzError):
            return False

    # -------------------------
    # 工具：時間抽取與過渡期移轉
    # -------------------------
    def _extract_last_time_as_local(self, record: Dict[str, Any]) -> Optional[datetime.datetime]:
        """
        從 record 中抽取最後動作時間並轉成 self.timezone 的 aware datetime。
        讀取順序（新→舊）：
          1) last_time_utc（ISO 8601, Z）
          2) last_time（無時區，當作 UTC）
          3) last_time_local（ISO 8601，帶偏移）
          4) 落地：找得到任何『看起來像時間』的舊字串，就當成本地時間 localize（最後手段）
        若讀到舊格式，會嘗試「就地移轉」寫回新欄位（不改變原有語意）。
        """
        # 1) 標準：last_time_utc
        s = record.get("last_time_utc")
        if s:
            dt_utc = self._parse_iso_as_utc(s)
            if dt_utc:
                return dt_utc.astimezone(self.timezone)

        # 2) 舊欄位：last_time（視為 UTC）
        s = record.get("last_time")
        if s:
            dt_utc = self._parse_legacy_naive_as_utc(s)
            if dt_utc:
                # 過渡期：補寫 last_time_utc 與 last_time_local
                self._migrate_fill_fields(record, dt_utc)
                return dt_utc.astimezone(self.timezone)

        # 3) 可讀欄位：last_time_local（帶偏移）
        s = record.get("last_time_local")
        if s:
            dt_local = self._parse_iso_with_tz(s)
            if dt_local:
                # 過渡期：補寫標準 UTC 欄位
                self._migrate_fill_fields(record, dt_local.astimezone(datetime.timezone.utc))
                return dt_local.astimezone(self.timezone)

        # 4) 最後手段：任何疑似時間字串，當成本地時間
        #    這裡僅嘗試舊格式 "YYYY-mm-dd HH:MM:SS"
        s = record.get("time") or record.get("timestamp")
        if s:
            try:
                naive_local = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                local_dt = self.timezone.localize(naive_local)
                # 過渡期：補寫標準 UTC 欄位
                self._migrate_fill_fields(record, local_dt.astimezone(datetime.timezone.utc))
                return local_dt
            except Exception:
                pass

        return None

    # ---- 解析工具 ----
    @staticmethod
    def _parse_iso_as_utc(s: str) -> Optional[datetime.datetime]:
        """把 ISO 8601（可能結尾 Z）字串 parse 成 aware UTC datetime"""
        try:
            s2 = s.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            else:
                dt = dt.astimezone(datetime.timezone.utc)
            return dt
        except Exception:
            return None

    @staticmethod
    def _parse_iso_with_tz(s: str) -> Optional[datetime.datetime]:
        """把帶偏移的 ISO 8601 字串 parse 成 aware datetime（保留原時區）"""
        try:
            s2 = s.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                return None
            return dt
        except Exception:
            return None

    @staticmethod
    def _parse_legacy_naive_as_utc(s: str) -> Optional[datetime.datetime]:
        """把舊的 'YYYY-mm-dd HH:MM:SS' 字串視為 UTC 解析"""
        try:
            dt_naive = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return dt_naive.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            return None

    # ---- 移轉工具 ----
    def _migrate_fill_fields(self, record: Dict[str, Any], dt_utc: datetime.datetime) -> None:
        """
        讀到舊格式時，補寫：
          - last_time_utc（標準）
          - last_time（舊相容，UTC、無時區）
          - last_time_local（可讀）
        並嘗試立即寫回檔案（最佳努力，失敗就算了）。
        """
        try:
            changed = False
            if not record.get("last_time_utc"):
                record["last_time_utc"] = dt_utc.isoformat().replace("+00:00", "Z")
                changed = True
            if not record.get("last_time"):
                record["last_time"] = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
                changed = True
            try:
                local_dt = dt_utc.astimezone(self.timezone)
                if not record.get("last_time_local"):
                    record["last_time_local"] = local_dt.isoformat()
                    changed = True
            except Exception:
                pass
            if changed:
                record["_schema"] = "utc_v1"
                # 嘗試回存（不知道 key，所以需要外層提供 title；這裡用 replace 回寫全集）
                data = self.load_data()
                # 用 record 的 title 可能不在資料裡，故全表掃一遍比對物件身分不可靠；
                # 安全作法：呼叫端在更新後再 save_data。這裡盡力處理：
                # 找到第一個值等於此 record（依引用）就回存；否則跳過（避免誤寫）。
                for k, v in data.items():
                    if v is record:
                        data[k] = record
                        self.save_data(data)
                        break
                else:
                    # 找不到引用：不強存，避免覆蓋別人變更
                    pass
                warnings.warn("已自動移轉購買時間欄位到 UTC 標準格式（last_time_utc）。", RuntimeWarning)
        except Exception:
            # 過渡期最佳努力，不讓功能中斷
            pass

    
    # def is_purchase_expired(self, title: str, hours: int = 24) -> bool:
    #     """
    #     檢查購買記錄是否過期
        
    #     Args:
    #         title: 購買項目名稱
    #         hours: 過期小時數
            
    #     Returns:
    #         是否過期
    #     """
    #     record = self.get_purchase_record(title)
    #     if not record:
    #         return True
            
    #     last_time_str = record.get("last_time")
    #     if not last_time_str:
    #         return True
            
    #     try:
    #         last_action_datetime = datetime.datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
    #         last_action_datetime = self.timezone.localize(last_action_datetime)
    #         now = datetime.datetime.now(self.timezone)

    #         return (now - last_action_datetime) > datetime.timedelta(hours=hours)
    #     except (ValueError, AttributeError, pytz_exceptions.PytzError):
    #         return True


# 便利函數，保持與原有代碼的兼容性
def create_park_manager(device_id: str) -> ParkMarketDataManager:
    """創建停車場數據管理器"""
    return ParkMarketDataManager(device_id)


def create_family_market_manager(device_id: str) -> FamilyMarketDataManager:
    """創建家族市場數據管理器"""
    return FamilyMarketDataManager(device_id)


def create_time_manager(device_id: str) -> TimeRecordDataManager:
    """創建時間記錄管理器"""
    return TimeRecordDataManager(device_id)


def create_store_manager(device_id: str) -> StoreDataManager:
    """創建商店數據管理器"""
    return StoreDataManager(device_id)


# 向後兼容的函數
def time_recording(ip: str, name: str = '') -> None:
    """向後兼容的時間記錄函數"""
    manager = create_time_manager(ip)
    manager.record_time(name)


def return_time(ip: str, name: str = '') -> Optional[Dict[str, Any]]:
    """向後兼容的時間獲取函數"""
    manager = create_time_manager(ip)
    return manager.get_time_record(name)


def should_execute_cycle_from_record(
    record: Optional[Dict[str, Any]],
    *,
    cycle_weeks: int = 4,
    today: Optional[datetime.date] = None,
    allowed_weekdays: Optional[List[int]] = None,
) -> Tuple[bool, bool]:
    """從 return_time 的 record 判斷是否該執行週期任務。"""
    tpe = datetime.timezone(datetime.timedelta(hours=8))

    if today is None:
        today = datetime.datetime.now(tpe).date()

    if record is None:
        return True, True

    recorded_date: Optional[datetime.date] = None
    try:
        recorded_date = datetime.datetime.strptime(record.get("recorded_date", ""), "%Y-%m-%d").date()
    except Exception:
        recorded_date = None

    if recorded_date is None:
        return True, True

    delta_days = (today - recorded_date).days
    weeks_passed = delta_days // 7

    is_allowed_weekday = True
    if allowed_weekdays is not None:
        is_allowed_weekday = today.weekday() in allowed_weekdays

    is_cycle_week = (weeks_passed % cycle_weeks) == 0

    should_execute = is_cycle_week and is_allowed_weekday
    need_week_record = is_cycle_week and (weeks_passed == 0)

    return should_execute, need_week_record


def should_execute_cycle(
    ip: str,
    record_name: str,
    *,
    cycle_weeks: int = 4,
    today: Optional[datetime.date] = None,
    allowed_weekdays: Optional[List[int]] = None,
) -> Tuple[bool, bool]:
    """以 record_name 判斷週期任務是否該執行。"""
    record = return_time(ip, name=record_name)
    return should_execute_cycle_from_record(
        record,
        cycle_weeks=cycle_weeks,
        today=today,
        allowed_weekdays=allowed_weekdays,
    )


def is_record_expired(record: Optional[Dict[str, Any]], expired_seconds: int, *, check_next_day: bool = True) -> bool:
    """判斷 return_time 記錄是否過期（可選擇跨日視為過期）。"""
    if record is None:
        return True

    if check_next_day and record.get("is_next_day", False):
        return True

    try:
        timestamp = float(record.get("timestamp", 0))
    except (TypeError, ValueError):
        return True

    if timestamp <= 0:
        return True

    return (time.time() - timestamp) > expired_seconds


def should_execute_cycle_with_cooldown(
    ip: str,
    *,
    cycle_record_name: str,
    last_execution_name: str,
    cycle_weeks: int = 4,
    cooldown_seconds: int = 4 * 60 * 60,
    today: Optional[datetime.date] = None,
    allowed_weekdays: Optional[List[int]] = None,
) -> Tuple[bool, bool]:
    """每 N 週中的 1 週執行，且在該週內有冷卻時間限制。"""
    in_cycle_week, need_week_record = should_execute_cycle(
        ip,
        cycle_record_name,
        cycle_weeks=cycle_weeks,
        today=today,
        allowed_weekdays=allowed_weekdays,
    )

    if not in_cycle_week:
        return False, False

    last_execution = return_time(ip, name=last_execution_name)
    if last_execution is None:
        return True, need_week_record

    if is_record_expired(last_execution, cooldown_seconds, check_next_day=True):
        return True, False

    return False, False


def record_json(ip: str, title: str, content: Dict[str, Any]) -> None:
    """向後兼容的記錄函數"""
    manager = create_store_manager(ip)
    manager.record_purchase(title, content)


def check_json(ip: str, title: str) -> Optional[Dict[str, Any]]:
    """向後兼容的檢查函數"""
    manager = create_store_manager(ip)
    return manager.get_purchase_record(title)


def is_expired(last_park_time: float, expired_time: int = 60 * 60 * 3 + 55*60) -> bool:
    """
    判斷時間戳是否過期(超過指定時間或跨日)。
    
    Args:
        last_park_time: 上次記錄的時間戳
        expired_time: 過期時間(秒),默認3小時55分
        
    Returns:
        True 表示已過期或跨日
    """
    TPE = datetime.timezone(datetime.timedelta(hours=8))
    
    # 計算當前時間
    now = time.time()

    # 計算是否超過指定時間
    time_exceeded = (now - last_park_time) > expired_time

    # 取得台灣當前日期
    current_date = datetime.datetime.now(TPE).strftime("%Y-%m-%d")
    recorded_date = datetime.datetime.fromtimestamp(
        last_park_time, TPE).strftime("%Y-%m-%d")

    # 判斷是否跨日
    is_next_day = (recorded_date != current_date)

    # 只要符合其中一個條件就回傳 True
    return time_exceeded or is_next_day


def _should_execute_cycle(ip: str, record_name: str, cycle_weeks: int = 4, logger=None) -> tuple:
    """
    判斷指定週期是否該執行（每 cycle_weeks 週執行 1 週）。
    
    Args:
        ip: 設備ID
        record_name: 記錄名稱
        cycle_weeks: 週期(週數),默認4週
        logger: 日誌記錄器(可選)
        
    Returns:
        (should_execute, need_record): 是否應執行, 是否需要記錄
    """
    TPE = datetime.timezone(datetime.timedelta(hours=8))
    
    record = return_time(ip, name=record_name)
    now = datetime.datetime.now(TPE).date()
    current_monday = now - datetime.timedelta(days=now.weekday())

    if record is None:
        if logger:
            logger.info(f"[{ip}] {record_name}: 無記錄，第一次執行")
        return True, True

    recorded_date = None
    try:
        if isinstance(record, dict) and record.get("recorded_date"):
            recorded_date = datetime.datetime.strptime(record.get("recorded_date"), "%Y-%m-%d").date()
        elif isinstance(record, dict) and record.get("timestamp"):
            recorded_date = datetime.datetime.fromtimestamp(float(record.get("timestamp")), TPE).date()
        else:
            if logger:
                logger.warning(f"[{ip}] {record_name}: 記錄格式異常，重新執行")
            return True, True
    except Exception as e:
        if logger:
            logger.error(f"[{ip}] {record_name}: 解析記錄失敗 {e}，重新執行")
        return True, True

    recorded_monday = recorded_date - datetime.timedelta(days=recorded_date.weekday())
    weeks_since = (current_monday - recorded_monday).days // 7
    if weeks_since < 0:
        weeks_since = 0

    # 判斷是否在同一週內
    in_same_week = (weeks_since == 0)
    # 判斷是否輪到執行週（每 cycle_weeks 週執行一次）
    should_execute = (weeks_since % cycle_weeks) == 0
    
    if logger:
        logger.info(f"[{ip}] {record_name}: 記錄日期={recorded_date}, 當前週一={current_monday}, 距離{weeks_since}週, 週期={cycle_weeks}週, 同一週={in_same_week}, 應執行={should_execute}")
    
    return should_execute, False


def should_execute_sea_with_cooldown(ip: str, cycle_weeks: int = 4, cooldown_hours: int = 4, logger=None) -> tuple:
    """
    判斷是否該執行 sea（每 N 週中的1週，且該週內每 X 小時執行一次）。
    
    Args:
        ip: 設備ID
        cycle_weeks: 週期(週數),默認4週
        cooldown_hours: 冷卻時間(小時),默認4小時
        logger: 日誌記錄器(可選)
        
    Returns:
        (should_execute, need_week_record): 是否應執行, 是否需要更新週期記錄
    """
    # 先檢查是否在正確的週期
    in_correct_week, need_week_record = _should_execute_cycle(ip, "sea_cycle_start", cycle_weeks=cycle_weeks, logger=logger)
    
    if not in_correct_week:
        return False, False
    
    # 在正確的週期內，檢查冷卻時間
    last_execution = return_time(ip, name="sea_last_execution")
    
    if last_execution is None:
        # 第一次執行
        return True, need_week_record
    
    # 檢查是否已過指定小時數
    expired_time = cooldown_hours * 60 * 60
    if is_expired(last_execution.get("timestamp", 0), expired_time=expired_time):
        return True, False  # 不需要更新週期記錄，只需更新執行時間
    
    return False, False