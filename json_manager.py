"""
統一的JSON數據管理器
整合了原本散佈在各個檔案中的JSON操作功能
"""

import copy
import json
import os
import datetime
import tempfile
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import pytz
from pytz import exceptions as pytz_exceptions


# ──────────────────────────────────────────────────────────────────────────────
# 模組私有工具函式  (供類別共用，避免重複程式碼)
# ──────────────────────────────────────────────────────────────────────────────

_TZ_TAIPEI = pytz.timezone("Asia/Taipei")


def _safe_cast(value: Any, caster: Callable[[Any], Any], default: Any) -> Any:
    """Safely cast a value to the desired type, returning a default on failure."""
    try:
        return caster(value)
    except (TypeError, ValueError):
        return default


def _ts_same_day(timestamp: float, tz=_TZ_TAIPEI) -> bool:
    """True if *timestamp* falls on the same calendar day as now (in *tz*)."""
    try:
        return (
            datetime.datetime.fromtimestamp(timestamp, tz).date()
            == datetime.datetime.now(tz).date()
        )
    except (OSError, OverflowError, ValueError, TypeError):
        return False


def _ts_same_week(timestamp: float, tz=_TZ_TAIPEI) -> bool:
    """True if *timestamp* falls in the same ISO week as now (in *tz*)."""
    try:
        rec = datetime.datetime.fromtimestamp(timestamp, tz).date()
        now = datetime.datetime.now(tz).date()
        return rec.isocalendar()[:2] == now.isocalendar()[:2]
    except (OSError, OverflowError, ValueError, TypeError):
        return False


def _parse_recorded_date(
    record: Optional[Dict[str, Any]],
    tz=_TZ_TAIPEI,
) -> Optional[datetime.date]:
    """從 time record dict 中取出日期，優先用 recorded_date 字串，其次 timestamp。"""
    if not isinstance(record, dict):
        return None
    # 優先：recorded_date 字串
    raw = record.get("recorded_date")
    if raw:
        try:
            return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    # 次要：timestamp
    ts = _safe_cast(record.get("timestamp"), float, 0.0)
    if ts > 0:
        try:
            return datetime.datetime.fromtimestamp(ts, tz).date()
        except (OSError, OverflowError, ValueError):
            pass
    return None


def _atomic_write_json(filepath: str, data, **json_kwargs) -> None:
    """Write *data* as JSON to *filepath* atomically using temp file + os.replace().

    On write failure, the original file is left untouched and the exception is re-raised.
    Temp file is always cleaned up.
    """
    dirpath = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **json_kwargs)
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ──────────────────────────────────────────────────────────────────────────────
# 資料模型
# ──────────────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────────────
# 基礎管理器
# ──────────────────────────────────────────────────────────────────────────────

class JsonDataManager:
    """統一的JSON數據管理器"""

    def __init__(self, device_id: str, file_suffix: str = ""):
        self.device_id = device_id
        self.file_suffix = file_suffix
        self.timezone = _TZ_TAIPEI

    def get_filename(self) -> str:
        if self.file_suffix:
            return f"{self.device_id}_{self.file_suffix}.json"
        return f"{self.device_id}.json"

    def load_data(self, default_data: Optional[Dict] = None) -> Dict[str, Any]:
        filename = self.get_filename()
        if default_data is None:
            default_data = {}
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=4, ensure_ascii=False)
            return default_data
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"JSON文件損壞，但保留原文件避免數據丟失: {e}")
            backup = f"{filename}.backup_{int(time.time())}"
            try:
                import shutil
                shutil.copy2(filename, backup)
                print(f"已創建備份文件: {backup}")
            except Exception:
                pass
            return default_data

    def save_data(self, data: Dict[str, Any]) -> bool:
        try:
            filename = self.get_filename()
            _atomic_write_json(filename, data, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"保存JSON文件失敗: {e}")
            return False

    def record_timestamp(self, name: str, additional_data: Optional[Dict] = None) -> None:
        data = self.load_data()
        now = datetime.datetime.now(self.timezone)
        record: Dict[str, Any] = {
            "timestamp": now.timestamp(),
            "date": now.strftime("%Y-%m-%d"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if additional_data:
            record.update(additional_data)
        data[name] = record
        self.save_data(data)
        print(f"已記錄 {name} 的時間戳記: {record['datetime']}")

    def get_record(self, name: str) -> Optional[Dict[str, Any]]:
        return self.load_data().get(name)

    def is_same_day(self, name: str) -> bool:
        record = self.get_record(name)
        if not record:
            return False
        try:
            if "timestamp" in record:
                return _ts_same_day(float(record["timestamp"]), self.timezone)
            if "date" in record:
                current = datetime.datetime.now(self.timezone).strftime("%Y-%m-%d")
                return record["date"] == current
        except (ValueError, TypeError):
            pass
        return False

    def is_same_week(self, name: str) -> bool:
        record = self.get_record(name)
        if not record:
            return False
        try:
            if "timestamp" in record:
                return _ts_same_week(float(record["timestamp"]), self.timezone)
            if "date" in record:
                rec_date = datetime.datetime.strptime(record["date"], "%Y-%m-%d").date()
                now = datetime.datetime.now(self.timezone).date()
                return rec_date.isocalendar()[:2] == now.isocalendar()[:2]
        except (ValueError, TypeError):
            pass
        return False

    def is_expired(self, name: str, expired_seconds: int = 3600) -> bool:
        record = self.get_record(name)
        if not record:
            return True
        try:
            ts = float(record.get("timestamp", 0))
            return (time.time() - ts) > expired_seconds
        except (ValueError, TypeError):
            return True

    def is_expired_or_next_day(self, name: str, expired_seconds: int = 3600) -> bool:
        return self.is_expired(name, expired_seconds) or not self.is_same_day(name)

    def get_numeric_value(self, name: str, key: str, default_value: Union[int, float] = 0) -> Union[int, float]:
        record = self.get_record(name)
        if not record:
            return default_value
        value = record.get(key, default_value)
        caster: Callable[[Any], Any] = type(default_value)
        if caster is bool:
            return bool(value)
        return _safe_cast(value, caster, default_value)

    def update_numeric_value(self, name: str, key: str, value: Union[int, float]) -> None:
        data = self.load_data()
        if name not in data:
            data[name] = {}
        data[name][key] = value
        self.save_data(data)


# ──────────────────────────────────────────────────────────────────────────────
# 停車場市場管理器
# ──────────────────────────────────────────────────────────────────────────────

class ParkMarketDataManager(JsonDataManager):
    """停車場市場數據管理器，專門處理停車相關的JSON數據"""

    SECTION_KEYS = ("daily", "weekly")

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")

    def get_default_structure(self) -> Dict[str, Any]:
        template = MarketState().to_payload()
        return {key: template.copy() for key in self.SECTION_KEYS}

    def load_data(self) -> Dict[str, Any]:
        data = super().load_data({})
        if self._normalize_sections(data):
            self.save_data(data)
        return data

    def get_buy_data(self) -> Tuple[float, int, float, int, int, int]:
        states = self._load_states()
        daily = states["daily"]
        weekly = states["weekly"]
        if daily.timestamp > 0 and not _ts_same_day(daily.timestamp, self.timezone):
            daily = MarketState(timestamp=daily.timestamp)
        if weekly.timestamp > 0 and not _ts_same_week(weekly.timestamp, self.timezone):
            weekly = MarketState(timestamp=weekly.timestamp)
        return (
            daily.timestamp,
            daily.buy_num,
            weekly.timestamp,
            weekly.buy_num,
            daily.check_time,
            weekly.check_time,
        )

    def _normalize_sections(self, data: Dict[str, Any]) -> bool:
        changed = False
        for key in self.SECTION_KEYS:
            original = data.get(key)
            normalized = MarketState.from_payload(original).to_payload()
            if normalized != original:
                data[key] = normalized
                changed = True
        return changed

    def _load_states(self) -> Dict[str, MarketState]:
        data = self.load_data()
        return {key: MarketState.from_payload(data.get(key)) for key in self.SECTION_KEYS}

    # Thin wrappers kept for readability in record_purchase / should_purchase
    def _is_same_day_from_timestamp(self, timestamp: float) -> bool:
        return _ts_same_day(timestamp, self.timezone)

    def _is_same_week_from_timestamp(self, timestamp: float) -> bool:
        return _ts_same_week(timestamp, self.timezone)

    def record_purchase(self, mode: str, buy_num: int, check_time: int = 0) -> None:
        if mode not in self.SECTION_KEYS:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")
        try:
            data = self.load_data()
            state = MarketState.from_payload(data.get(mode))
            now_ts = datetime.datetime.now(self.timezone).timestamp()
            buy_inc = _safe_cast(buy_num, int, 0)
            check_inc = _safe_cast(check_time, int, 0)
            if mode == "daily":
                should_reset = not _ts_same_day(state.timestamp, self.timezone)
            else:
                should_reset = not _ts_same_week(state.timestamp, self.timezone)
            if should_reset:
                state.buy_num = buy_inc
                state.check_time = check_inc
                print(f"{mode} 數據已重置，新的購買數量: {state.buy_num}, 新的檢查次數: {state.check_time}")
            else:
                state.buy_num += buy_inc
                state.check_time += check_inc
                print(f"{mode} 數據累加，總購買數量: {state.buy_num}, 總檢查次數: {state.check_time}")
            state.timestamp = float(now_ts)
            data[mode] = state.to_payload()
            self.save_data(data)
            print(f"已記錄 {mode} 購買數據，總數量: {state.buy_num}, 總檢查次數: {state.check_time}")
        except Exception as e:
            print(f"記錄購買數據失敗: {e}")

    def should_purchase(self, mode: str, max_purchases: int = 2, max_checks: int = 2) -> bool:
        if mode not in self.SECTION_KEYS:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")
        states = self._load_states()
        state = states[mode]
        if mode == "daily":
            is_new_cycle = not _ts_same_day(state.timestamp, self.timezone)
            label, unit = "Daily", "天"
        else:
            is_new_cycle = not _ts_same_week(state.timestamp, self.timezone)
            label, unit = "Weekly", "週"
        within_purchase_limit = state.buy_num < max_purchases
        within_check_limit = state.check_time < max_checks
        should_buy = is_new_cycle or (within_purchase_limit and within_check_limit)
        print(
            f"{label} 檢查: 新的一{unit}={is_new_cycle}, 購買次數={state.buy_num}/{max_purchases}, "
            f"檢查次數={state.check_time}/{max_checks}, 應該購買={should_buy}"
        )
        return should_buy


# ──────────────────────────────────────────────────────────────────────────────
# 家族市場管理器
# ──────────────────────────────────────────────────────────────────────────────

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
        return PurchaseCountState.from_data(self.load_data(), timestamp_key, buy_key)

    def set_state(self, mode: str, buy_num: int, timestamp: Optional[float] = None) -> None:
        timestamp_key, buy_key = self._get_keys(mode)
        data = self.load_data()
        state = PurchaseCountState(
            timestamp=float(timestamp) if timestamp is not None
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


# ──────────────────────────────────────────────────────────────────────────────
# 時間記錄管理器
# ──────────────────────────────────────────────────────────────────────────────

class TimeRecordDataManager(JsonDataManager):
    """時間記錄數據管理器，處理各種行為的時間記錄"""

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")

    def record_time(self, name: str) -> None:
        self.record_timestamp(name)

    def get_time_record(self, name: str) -> Optional[Dict[str, Any]]:
        """獲取時間記錄，兼容舊格式（scalar timestamp / dict）。"""
        record = self.get_record(name)
        if record is None:
            return None

        # 舊格式：直接儲存純數字 timestamp
        if isinstance(record, (int, float, str)):
            ts = _safe_cast(record, float, 0.0)
            if ts <= 0:
                return None
            try:
                rec_dt = datetime.datetime.fromtimestamp(ts, self.timezone)
                now = datetime.datetime.now(self.timezone).date()
                rec_date = rec_dt.date()
                return {
                    "timestamp": ts,
                    "recorded_date": rec_dt.strftime("%Y-%m-%d"),
                    "is_next_day": rec_date != now,
                    "is_next_week": rec_date.isocalendar()[:2] != now.isocalendar()[:2],
                }
            except (OSError, OverflowError, ValueError):
                return None

        if not isinstance(record, dict):
            return None

        ts = _safe_cast(record.get("timestamp"), float, 0.0)
        date_str = record.get("date")

        rec_date: Optional[datetime.date] = None
        if date_str:
            try:
                rec_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                rec_date = None
        if rec_date is None and ts > 0:
            try:
                rec_dt = datetime.datetime.fromtimestamp(ts, self.timezone)
                date_str = rec_dt.strftime("%Y-%m-%d")
                rec_date = rec_dt.date()
            except (OSError, OverflowError, ValueError):
                pass

        if rec_date is None or ts <= 0:
            return None

        now = datetime.datetime.now(self.timezone).date()
        return {
            "timestamp": ts,
            "recorded_date": date_str,
            "is_next_day": rec_date != now,
            "is_next_week": rec_date.isocalendar()[:2] != now.isocalendar()[:2],
        }


# ──────────────────────────────────────────────────────────────────────────────
# 商店管理器
# ──────────────────────────────────────────────────────────────────────────────

class StoreDataManager(JsonDataManager):
    """商店數據管理器，處理購買記錄（過渡期相容舊欄位）"""

    def __init__(self, device_id: str, timezone_str: str = "Asia/Taipei"):
        super().__init__(device_id, file_suffix="")
        try:
            self.timezone = pytz.timezone(timezone_str)
        except Exception:
            self.timezone = _TZ_TAIPEI

    # ── 寫入 ──────────────────────────────────────────────────────────────────

    def record_purchase(self, title: str, content: Dict[str, Any]) -> None:
        """記錄購買行為（統一 UTC 儲存，並寫入相容欄位）。"""
        data = self.load_data()
        payload = copy.deepcopy(content)

        utc_now = datetime.datetime.now(datetime.timezone.utc)
        payload["last_time_utc"] = utc_now.isoformat().replace("+00:00", "Z")
        payload["last_time"] = utc_now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            payload["last_time_local"] = utc_now.astimezone(self.timezone).isoformat()
        except Exception:
            pass

        if "count" in payload:
            payload["count"] = _safe_cast(payload["count"], int, 1)
        else:
            old = data.get(title, {})
            payload["count"] = _safe_cast(old.get("count", 0), int, 0) + 1

        payload["_schema"] = "utc_v1"
        data[title] = payload
        self.save_data(data)
        print(f"已記錄購買項目 '{title}': {payload}")

    # ── 讀取 ──────────────────────────────────────────────────────────────────

    def get_purchase_record(self, title: str) -> Optional[Dict[str, Any]]:
        return self.get_record(title)

    def is_purchased_today(self, title: str, period: str = "day") -> bool:
        """period='day'（預設）或 'week'。"""
        record = self.get_purchase_record(title)
        print(record)
        if not record:
            return False
        try:
            local_dt = self._extract_last_time_as_local(record)
            if local_dt is None:
                return False
            now_local = datetime.datetime.now(self.timezone)
            if period == "week":
                rec_y, rec_w, _ = local_dt.date().isocalendar()
                now_y, now_w, _ = now_local.date().isocalendar()
                print(f"比較週：記錄=(Y{rec_y},W{rec_w})，現在=(Y{now_y},W{now_w})")
                return rec_y == now_y and rec_w == now_w
            # default: day
            print(f"比較日期：記錄={local_dt.date()}，現在={now_local.date()}")
            return local_dt.date() == now_local.date()
        except (ValueError, AttributeError, pytz_exceptions.PytzError):
            return False

    # ── 解析工具 ─────────────────────────────────────────────────────────────

    def _extract_last_time_as_local(self, record: Dict[str, Any]) -> Optional[datetime.datetime]:
        """讀取最後動作時間並轉成 self.timezone 的 aware datetime。優先順序 new→old。"""
        # 1) last_time_utc
        s = record.get("last_time_utc")
        if s:
            dt = self._parse_iso_as_utc(s)
            if dt:
                return dt.astimezone(self.timezone)
        # 2) last_time（無時區，視為 UTC）
        s = record.get("last_time")
        if s:
            dt = self._parse_legacy_naive_as_utc(s)
            if dt:
                self._migrate_fill_fields(record, dt)
                return dt.astimezone(self.timezone)
        # 3) last_time_local（帶偏移）
        s = record.get("last_time_local")
        if s:
            dt = self._parse_iso_with_tz(s)
            if dt:
                self._migrate_fill_fields(record, dt.astimezone(datetime.timezone.utc))
                return dt.astimezone(self.timezone)
        # 4) 舊 time / timestamp 字串
        s = record.get("time") or record.get("timestamp")
        if s:
            try:
                naive = datetime.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
                local_dt = self.timezone.localize(naive)
                self._migrate_fill_fields(record, local_dt.astimezone(datetime.timezone.utc))
                return local_dt
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_iso_as_utc(s: str) -> Optional[datetime.datetime]:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_iso_with_tz(s: str) -> Optional[datetime.datetime]:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else None
        except Exception:
            return None

    @staticmethod
    def _parse_legacy_naive_as_utc(s: str) -> Optional[datetime.datetime]:
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )
        except Exception:
            return None

    def _migrate_fill_fields(self, record: Dict[str, Any], dt_utc: datetime.datetime) -> None:
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
                data = self.load_data()
                for k, v in data.items():
                    if v is record:
                        data[k] = record
                        self.save_data(data)
                        break
                warnings.warn(
                    "已自動移轉購買時間欄位到 UTC 標準格式（last_time_utc）。",
                    RuntimeWarning,
                )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# 便利工廠函式
# ──────────────────────────────────────────────────────────────────────────────

def create_park_manager(device_id: str) -> ParkMarketDataManager:
    return ParkMarketDataManager(device_id)

def create_family_market_manager(device_id: str) -> FamilyMarketDataManager:
    return FamilyMarketDataManager(device_id)

def create_time_manager(device_id: str) -> TimeRecordDataManager:
    return TimeRecordDataManager(device_id)

def create_store_manager(device_id: str) -> StoreDataManager:
    return StoreDataManager(device_id)


# ──────────────────────────────────────────────────────────────────────────────
# 向後相容包裝函式
# ──────────────────────────────────────────────────────────────────────────────

def time_recording(ip: str, name: str = "") -> None:
    create_time_manager(ip).record_time(name)


def return_time(ip: str, name: str = "") -> Optional[Dict[str, Any]]:
    return create_time_manager(ip).get_time_record(name)


def record_json(ip: str, title: str, content: Dict[str, Any]) -> None:
    create_store_manager(ip).record_purchase(title, content)


def check_json(ip: str, title: str) -> Optional[Dict[str, Any]]:
    return create_store_manager(ip).get_purchase_record(title)


# ──────────────────────────────────────────────────────────────────────────────
# 週期判斷函式
# ──────────────────────────────────────────────────────────────────────────────

def should_execute_cycle_from_record(
    record: Optional[Dict[str, Any]],
    *,
    cycle_weeks: int = 4,
    today: Optional[datetime.date] = None,
    allowed_weekdays: Optional[List[int]] = None,
) -> Tuple[bool, bool]:
    """從 return_time 的 record 判斷是否該執行週期任務。"""
    _tpe = datetime.timezone(datetime.timedelta(hours=8))
    if today is None:
        today = datetime.datetime.now(_tpe).date()

    if record is None:
        return True, True

    recorded_date = _parse_recorded_date(record)
    if recorded_date is None:
        return True, True

    delta_days = (today - recorded_date).days
    weeks_passed = delta_days // 7

    is_allowed_weekday = (allowed_weekdays is None) or (today.weekday() in allowed_weekdays)
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
    return should_execute_cycle_from_record(
        return_time(ip, name=record_name),
        cycle_weeks=cycle_weeks,
        today=today,
        allowed_weekdays=allowed_weekdays,
    )


def is_record_expired(
    record: Optional[Dict[str, Any]],
    expired_seconds: int,
    *,
    check_next_day: bool = True,
) -> bool:
    """判斷 return_time 記錄是否過期（可選擇跨日視為過期）。"""
    if record is None:
        return True
    if check_next_day and record.get("is_next_day", False):
        return True
    ts = _safe_cast(record.get("timestamp", 0), float, 0.0)
    if ts <= 0:
        return True
    return (time.time() - ts) > expired_seconds


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


# ──────────────────────────────────────────────────────────────────────────────
# 原始 timestamp 過期判斷（接受浮點數 timestamp，非 record dict）
# ──────────────────────────────────────────────────────────────────────────────

def is_timestamp_expired(
    last_park_time: float,
    expired_time: int = 60 * 60 * 3 + 55 * 60,
) -> bool:
    """判斷原始 timestamp 是否過期（超過指定秒數 或 跨日）。"""
    _tpe = datetime.timezone(datetime.timedelta(hours=8))
    now = time.time()
    time_exceeded = (now - last_park_time) > expired_time
    current_date = datetime.datetime.now(_tpe).strftime("%Y-%m-%d")
    try:
        recorded_date = datetime.datetime.fromtimestamp(last_park_time, _tpe).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return True
    return time_exceeded or (recorded_date != current_date)


# 向後相容別名：舊程式呼叫 is_expired(timestamp, expired_time) 的地方不需修改
is_expired = is_timestamp_expired


# ──────────────────────────────────────────────────────────────────────────────
# 舊版 _should_execute_cycle（委派給 should_execute_cycle_from_record）
# ──────────────────────────────────────────────────────────────────────────────

def _should_execute_cycle(
    ip: str,
    record_name: str,
    cycle_weeks: int = 4,
    logger=None,
) -> Tuple[bool, bool]:
    """
    判斷指定週期是否該執行（每 cycle_weeks 週執行 1 週）。
    使用 Monday-anchored 週計算。
    """
    _tpe = datetime.timezone(datetime.timedelta(hours=8))
    record = return_time(ip, name=record_name)
    now = datetime.datetime.now(_tpe).date()
    current_monday = now - datetime.timedelta(days=now.weekday())

    if record is None:
        if logger:
            logger.info(f"[{ip}] {record_name}: 無記錄，第一次執行")
        return True, True

    recorded_date = _parse_recorded_date(record)
    if recorded_date is None:
        if logger:
            logger.warning(f"[{ip}] {record_name}: 記錄格式異常，重新執行")
        return True, True

    recorded_monday = recorded_date - datetime.timedelta(days=recorded_date.weekday())
    weeks_since = max(0, (current_monday - recorded_monday).days // 7)
    should_execute = (weeks_since % cycle_weeks) == 0

    if logger:
        logger.info(
            f"[{ip}] {record_name}: 記錄日期={recorded_date}, 當前週一={current_monday}, "
            f"距離{weeks_since}週, 週期={cycle_weeks}週, 應執行={should_execute}"
        )
    return should_execute, False


def should_execute_sea_with_cooldown(
    ip: str,
    cycle_weeks: int = 4,
    cooldown_hours: int = 4,
    logger=None,
) -> Tuple[bool, bool]:
    """判斷是否該執行 sea（每 N 週中的1週，且該週內每 X 小時執行一次）。"""
    in_correct_week, need_week_record = _should_execute_cycle(
        ip, "sea_cycle_start", cycle_weeks=cycle_weeks, logger=logger
    )
    if not in_correct_week:
        return False, False

    last_execution = return_time(ip, name="sea_last_execution")
    if last_execution is None:
        return True, need_week_record

    expired_time = cooldown_hours * 3600
    if is_timestamp_expired(last_execution.get("timestamp", 0), expired_time=expired_time):
        return True, False

    return False, False
