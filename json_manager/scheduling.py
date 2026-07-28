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


# 菇菇武道會是全服共用的三週檔期，不能用各裝置上次執行日往後推算。
# 2026-07-27（週一）由多台裝置的本週成功紀錄確認為目前活動週；遊戲改檔期時只需更新此錨點。
_MUSHROOM_ARENA_ANCHOR_MONDAY = datetime.date(2026, 7, 27)
_MUSHROOM_ARENA_CYCLE_DAYS = 21


def is_mushroom_arena_week(today: Optional[datetime.date] = None) -> bool:
    """判斷今天是否為菇菇武道會活動週（日曆錨點，每 21 天一週）。"""
    if today is None:
        _tpe = datetime.timezone(datetime.timedelta(hours=8))
        today = datetime.datetime.now(_tpe).date()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday - _MUSHROOM_ARENA_ANCHOR_MONDAY).days % _MUSHROOM_ARENA_CYCLE_DAYS == 0


# sea 只在台灣時間此視窗內執行（end 為 exclusive hour，等同 10:00~24:00）。
# legacy Sea.sea 的中段航海操作本就限制在這個時段，但結尾的盲點返回 tap
# 不論時段都會跑；若在視窗外被排程呼叫，會跳過中段、結尾 tap 落錯位置，
# 把遊戲卡在未知頁並級聯跳過後續任務。因此在排程器這層就擋掉視窗外的呼叫。
SEA_WINDOW_START_HOUR = 10
SEA_WINDOW_END_HOUR = 24

# ponytail: 航海日曆錨點 — 2026-06-22(週一)為已知活動週、4 週一輪(與龍骸聖域同錨)。
# 取代原本以 sea_cycle_start(各裝置「上次跑」時間)往回推 4 週的判週法：那個錨點
# 會隨各裝置 run 歷史漂移、與遊戲實際檔期脫鉤，導致同一週各裝置顯示/執行不一致。
# 改用日曆錨點後，run 排程與 dashboard 共用同一真相、全裝置一致。若遊戲改檔期，
# 只需更新這兩個常數。
_SEA_ANCHOR_MONDAY = datetime.date(2026, 6, 22)
_SEA_CYCLE_DAYS = 28  # 4 weeks


def is_sea_week(today: Optional[datetime.date] = None) -> bool:
    """今天是否落在航海活動週(日曆錨點，與龍骸聖域 ``_is_dragon_week`` 同形)。"""
    if today is None:
        _tpe = datetime.timezone(datetime.timedelta(hours=8))
        today = datetime.datetime.now(_tpe).date()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday - _SEA_ANCHOR_MONDAY).days % _SEA_CYCLE_DAYS == 0


def should_execute_sea_with_cooldown(
    ip: str,
    cycle_weeks: int = 4,
    cooldown_hours: int = 4,
    logger=None,
    now: Optional[datetime.datetime] = None,
) -> Tuple[bool, bool]:
    """判斷是否該執行 sea（每 N 週中的1週，且該週內每 X 小時執行一次，
    且限制在台灣時間 10:00~24:00 視窗內）。"""
    _tpe = datetime.timezone(datetime.timedelta(hours=8))
    if now is None:
        now = datetime.datetime.now(_tpe)
    if not (SEA_WINDOW_START_HOUR <= now.hour < SEA_WINDOW_END_HOUR):
        if logger:
            logger.info(
                f"[{ip}] sea: 非執行時段 (hour={now.hour}, "
                f"視窗={SEA_WINDOW_START_HOUR}:00~{SEA_WINDOW_END_HOUR}:00) → 跳過"
            )
        return False, False

    if not is_sea_week(now.date()):
        if logger:
            logger.info(f"[{ip}] sea: 非航海週(日曆錨點) → 跳過")
        return False, False

    last_execution = _lazy_return_time(ip, "sea_last_execution")
    if last_execution is None:
        return True, True

    expired_time = cooldown_hours * 3600
    if is_timestamp_expired(last_execution.get("timestamp", 0), expired_time=expired_time):
        return True, False

    return False, False
