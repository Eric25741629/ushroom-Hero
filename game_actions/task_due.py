"""任務 due-registry — 各 client 任務「今天/本週期是否該執行」的單一真相。

Phase A：把散在各 execute 函式內的 due 判斷收斂到這裡的純 predicate。
所有判準 **複用現有 json_manager 記錄 + 時間/排程**，不重寫 due 數學。

predicate 只回答「due 與否」：只讀記錄 + 時間，**不 side-effect、不寫記錄、
不碰遊戲客戶端**。時間一律台北時區。

對外只有 ``is_due(task, ip, now=None) -> bool``；未知 task raise ``KeyError``，
呼叫端自行決定 fallback。
"""

import datetime
from typing import Callable, Dict, Optional

import json_manager

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _resolve_now(now: Optional[datetime.datetime]) -> datetime.datetime:
    return now if now is not None else datetime.datetime.now(_TPE)


# --------------------------------------------------------------------------
# predicates（每個對照現有邏輯 1:1）
# --------------------------------------------------------------------------
def _due_hellgate(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_pipeline.py:225-230
    record = json_manager.return_time(ip, name="地獄之門")
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_day", False)
    return bool(should_execute and now.minute < 20)


def _due_daily_acceleration(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_tasks.py:16-21
    record = json_manager.return_time(ip, name="daily_acceleration")
    if record is None:
        return True
    return bool(record.get("is_next_day", False))


def _due_arena_challenges(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_tasks.py:82-87
    record = json_manager.return_time(ip, name="arena_challenges")
    if record is None:
        return True
    return bool(record.get("is_next_day", False))


def _due_mount_sprint(ip: str, now: datetime.datetime) -> bool:
    # 對照 rank_events.py:77-109（park_spring 的 due 判斷）
    # lazy import 破循環：rank_events.park_spring 反過來會 import 本模組。
    import rank_events

    # 1. 僅限活動開放窗（週二~週三22:00）
    if not rank_events.is_mount_sprint_open(now):
        return False
    # 2. 本週是否已執行過（record 存在且非跨週 → not due）
    record = json_manager.return_time(ip, name=rank_events.SPRINT_RECORD)
    if record and not record.get("is_next_week", True):
        return False
    # 3. 是否為 4 週週期的執行週
    should_run, _ = json_manager.should_execute_cycle(
        ip,
        rank_events.SPRINT_RECORD,
        cycle_weeks=4,
        allowed_weekdays=rank_events.ALLOWED_WEEKDAYS,
        today=now.date(),
    )
    return bool(should_run)


def _due_cloud_fighting(ip: str, now: datetime.datetime) -> bool:
    # 對照 battle/cloud.py:219-244（run_weekly_cloud_fighting_single 的 due 判斷）
    if now.weekday() != 0:  # 僅週一
        return False
    if now.hour < 3:  # 週一凌晨 3 點後
        return False

    try:
        rec = json_manager.return_time(ip, name="cloud_fighting_weekly")
    except Exception:
        rec = None

    # 已於本週執行過 → not due；週序比較異常時視為未執行（due），與原邏輯一致。
    if rec and isinstance(rec, dict) and rec.get("timestamp"):
        try:
            last_ts = float(rec.get("timestamp"))
            last_week = datetime.datetime.fromtimestamp(last_ts, now.tzinfo).isocalendar()[1]
            curr_week = now.date().isocalendar()[1]
            if last_week == curr_week:
                return False
        except Exception:
            return True
    return True


def _due_mushroom_arena(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/periodic_tasks.py:44-48（_run_periodic_cycle 的 cycle + daily_limit）
    # lazy import：periodic_tasks 會拉 img_tools/tools 等重模組。
    from game_actions import periodic_tasks

    should, _ = periodic_tasks.should_execute_mushroom_arena(ip)
    if not should:
        return False
    daily = json_manager.return_time(ip, name="mushroom_arena_daily")
    if daily is None:
        return True
    return bool(daily.get("is_next_day", False))


def _due_sea(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_pipeline.py:402-405（_run_periodic_cycle 用
    # should_execute_sea_with_cooldown 當 should_execute_fn，無 daily_limit）。
    # 該函式已含時段視窗 + 冷卻 + 日曆錨點。
    should, _ = json_manager.should_execute_sea_with_cooldown(ip, now=now)
    return bool(should)


_REGISTRY: Dict[str, Callable[[str, datetime.datetime], bool]] = {
    "地獄之門": _due_hellgate,
    "每日加速": _due_daily_acceleration,
    "競技場挑戰": _due_arena_challenges,
    "坐騎衝刺": _due_mount_sprint,
    "雲端戰鬥": _due_cloud_fighting,
    "菇菇武道會": _due_mushroom_arena,
    "航海": _due_sea,
}


def is_due(task: str, ip: str, now: Optional[datetime.datetime] = None) -> bool:
    """任務今天/本週期是否該執行。未知 task → raise KeyError。

    Args:
        task: 任務名（見 _REGISTRY key）。
        ip: 裝置 id。
        now: 供測試注入；預設台北時間現在。
    """
    try:
        predicate = _REGISTRY[task]
    except KeyError:
        raise KeyError(f"unknown task for due-check: {task!r}")
    return predicate(ip, _resolve_now(now))
