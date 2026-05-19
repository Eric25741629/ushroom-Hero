"""統一的 JSON 數據管理器（套件版）。

從 2026-05-19 起 ``json_manager`` 由單一檔案拆成套件：
    base.py / park.py / family.py / time_record.py / store.py / scheduling.py
本 ``__init__`` 對外維持原本的 import 介面，所有舊呼叫不需修改。
"""

from typing import Any, Dict, Optional

from .base import (
    _atomic_write_json,
    _parse_recorded_date,
    _safe_cast,
    _ts_same_day,
    _ts_same_week,
    JsonDataManager,
    MarketState,
    PurchaseCountState,
)
from .park import ParkMarketDataManager
from .family import FamilyMarketDataManager
from .time_record import TimeRecordDataManager
from .store import StoreDataManager
from .scheduling import (
    is_expired,
    is_record_expired,
    is_timestamp_expired,
    should_execute_cycle,
    should_execute_cycle_from_record,
    should_execute_cycle_with_cooldown,
    should_execute_sea_with_cooldown,
    _should_execute_cycle,
)


def create_park_manager(device_id: str) -> ParkMarketDataManager:
    return ParkMarketDataManager(device_id)


def create_family_market_manager(device_id: str) -> FamilyMarketDataManager:
    return FamilyMarketDataManager(device_id)


def create_time_manager(device_id: str) -> TimeRecordDataManager:
    return TimeRecordDataManager(device_id)


def create_store_manager(device_id: str) -> StoreDataManager:
    return StoreDataManager(device_id)


def time_recording(ip: str, name: str = "") -> None:
    create_time_manager(ip).record_time(name)


def return_time(ip: str, name: str = "") -> Optional[Dict[str, Any]]:
    return create_time_manager(ip).get_time_record(name)


def record_json(ip: str, title: str, content: Dict[str, Any]) -> None:
    create_store_manager(ip).record_purchase(title, content)


def check_json(ip: str, title: str) -> Optional[Dict[str, Any]]:
    return create_store_manager(ip).get_purchase_record(title)


__all__ = [
    # data classes / base
    "JsonDataManager",
    "MarketState",
    "PurchaseCountState",
    # specialised managers
    "ParkMarketDataManager",
    "FamilyMarketDataManager",
    "TimeRecordDataManager",
    "StoreDataManager",
    # factories
    "create_park_manager",
    "create_family_market_manager",
    "create_time_manager",
    "create_store_manager",
    # convenience wrappers
    "time_recording",
    "return_time",
    "record_json",
    "check_json",
    # scheduling
    "should_execute_cycle",
    "should_execute_cycle_from_record",
    "should_execute_cycle_with_cooldown",
    "is_record_expired",
    "is_timestamp_expired",
    "is_expired",
    "_should_execute_cycle",
    "should_execute_sea_with_cooldown",
    # internal helpers re-exported for tests
    "_atomic_write_json",
    "_safe_cast",
    "_ts_same_day",
    "_ts_same_week",
    "_parse_recorded_date",
]
