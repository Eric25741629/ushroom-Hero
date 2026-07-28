import datetime
import logging
import time
from typing import Callable, Optional, Tuple

import img_tools
import json_manager
import new_battle
from Sea import sea

SEA_CYCLE_WEEKS = 4
MUSHROOM_ARENA_CYCLE_WEEKS = 3


def _get_logger(logger: Optional[logging.Logger]) -> logging.Logger:
    return logger if logger is not None else logging.getLogger(__name__)


def should_execute_sea(ip: str) -> Tuple[bool, bool]:
    return json_manager.should_execute_cycle(
        ip,
        "sea_cycle_start",
        cycle_weeks=SEA_CYCLE_WEEKS,
    )


def should_execute_sea_with_cooldown(ip: str) -> Tuple[bool, bool]:
    return json_manager.should_execute_cycle_with_cooldown(
        ip,
        cycle_record_name="sea_cycle_start",
        last_execution_name="sea_last_execution",
        cycle_weeks=SEA_CYCLE_WEEKS,
        cooldown_seconds=4 * 60 * 60,
    )


def should_execute_mushroom_arena(ip: str) -> Tuple[bool, bool]:
    # 活動週是全服共用的日曆檔期，不可被各裝置上次執行時間帶偏。
    return json_manager.is_mushroom_arena_week(), False


def mushroom_arena(ip: str, d, logger: Optional[logging.Logger] = None) -> None:
    logger = _get_logger(logger)
    try:
        img_tools.click_str_by_server(d, "菇菇武道會", shift_y=-20)
        time.sleep(1)
        img_tools.click_str_by_server(d, "膜拜冠軍")
        time.sleep(1)
        for i in range(3):
            d.click(500,50)
        time.sleep(1)
        d.click(490, 919)
        time.sleep(1)
    except Exception as exc:
        logger.error(f"[{ip}] 菇菇武道會流程失敗: {exc}")


def run_periodic_cycle(
    *,
    ip: str,
    d,
    record_name: str,
    should_execute_fn: Callable[[str], Tuple[bool, bool]],
    action_fn: Callable[..., None],
    display_name: str,
    logger: Optional[logging.Logger] = None,
    daily_limit_name: Optional[str] = None,
    cycle_record_name: Optional[str] = None,
) -> None:
    logger = _get_logger(logger)
    should, need_record = should_execute_fn(ip)

    logger.info(f"[{ip}] {display_name}: 檢查執行條件 - should={should}, need_record={need_record}")

    if not should:
        logger.info(f"[{ip}] {display_name} 被排程跳過（未到週期或已過期）")
        return

    if daily_limit_name:
        daily_record = json_manager.return_time(ip, name=daily_limit_name)
        if daily_record and not daily_record.get("is_next_day", False):
            logger.info(f"[{ip}] {display_name} 今日已執行過，跳過。")
            return

    if need_record:
        if cycle_record_name:
            json_manager.time_recording(ip, name=cycle_record_name)
        json_manager.time_recording(ip, name=record_name)
    else:
        json_manager.time_recording(ip, name=record_name)

    logger.info(f"[{ip}] {display_name}: 開始執行")
    action_fn(ip=ip, d=d, logger=logger)

    if daily_limit_name:
        json_manager.time_recording(ip, name=daily_limit_name)
    time.sleep(3)


def run_mushroom_arena_cycle(ip: str, d, logger: Optional[logging.Logger] = None) -> None:
    run_periodic_cycle(
        ip=ip,
        d=d,
        record_name="mushroom_arena_cycle_start",
        should_execute_fn=should_execute_mushroom_arena,
        action_fn=mushroom_arena,
        display_name="菇菇武道會",
        logger=logger,
        daily_limit_name="mushroom_arena_daily",
    )


def run_sea_cycle_with_cooldown(ip: str, d, logger: Optional[logging.Logger] = None) -> None:
    run_periodic_cycle(
        ip=ip,
        d=d,
        record_name="sea_last_execution",
        should_execute_fn=should_execute_sea_with_cooldown,
        action_fn=sea,
        display_name="sea",
        logger=logger,
        cycle_record_name="sea_cycle_start",
    )


def run_weekly_fight_trial(ip: str, d, current_time: time.struct_time, logger: Optional[logging.Logger] = None) -> bool:
    logger = _get_logger(logger)
    record_time = json_manager.return_time(ip, name="萬神試煉")
    should_execute = record_time is None or record_time.get("is_next_week", False)

    if should_execute and (
        (current_time.tm_wday == 0 and current_time.tm_hour > 12)
        or (1 <= current_time.tm_wday <= 5)
    ):
        new_battle.fight_test(d)
        json_manager.time_recording(ip, name="萬神試煉")
        logger.info(f"[{ip}] 萬神試煉已執行並寫入紀錄")
        return True

    logger.info(f"[{ip}] 萬神試煉未到執行時間或本週已執行")
    return False
