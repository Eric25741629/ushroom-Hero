"""Base layer for the json_manager package.

Pure helpers (timezone-aware date arithmetic, atomic JSON IO, casting),
the lightweight data models (MarketState, PurchaseCountState), and the
shared `JsonDataManager` base class. No dependency on other submodules.
"""

import datetime
import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, Union

import pytz

from utils.json_io import read_json_bom_safe

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
    raw = record.get("recorded_date")
    if raw:
        try:
            return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
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
            return read_json_bom_safe(filename)
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
