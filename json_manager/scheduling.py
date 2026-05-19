"""週期判斷與過期判斷函式。

純函式 + 以 ip 查 record 的便利版本。`return_time` 透過 lazy import
從套件 `__init__` 取得，避免循環匯入（wrappers 反過來不會需要本檔）。
"""

import datetime
import time
from typing import Any, Dict, List, Optional, Tuple

from .base import _parse_recorded_date, _safe_cast


def _lazy_return_time(ip: str, name: str) -> Optional[Dict[str, Any]]:
    from . import return_time
    return return_time(ip, name=name)


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
        _lazy_return_time(ip, record_name),
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

    last_execution = _lazy_return_time(ip, last_execution_name)
    if last_execution is None:
        return True, need_week_record

    if is_record_expired(last_execution, cooldown_seconds, check_next_day=True):
        return True, False

    return False, False


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


is_expired = is_timestamp_expired


def _should_execute_cycle(
    ip: str,
    record_name: str,
    cycle_weeks: int = 4,
    logger=None,
) -> Tuple[bool, bool]:
    """Monday-anchored 週期判斷（舊版簽章，game_actions.periodic_tasks 仍在用）。"""
    _tpe = datetime.timezone(datetime.timedelta(hours=8))
    record = _lazy_return_time(ip, record_name)
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

    last_execution = _lazy_return_time(ip, "sea_last_execution")
    if last_execution is None:
        return True, need_week_record

    expired_time = cooldown_hours * 3600
    if is_timestamp_expired(last_execution.get("timestamp", 0), expired_time=expired_time):
        return True, False

    return False, False
