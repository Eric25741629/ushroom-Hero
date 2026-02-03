import datetime
import time
from json_manager import _should_execute_cycle, time_recording, return_time
from utils.logging_utils import logger
import img_tools
from tools import click_white

# 週期常數
SEA_CYCLE_WEEKS = 4
MUSHROOM_ARENA_CYCLE_WEEKS = 4


def should_execute_sea(ip: str) -> tuple:
    """判斷是否該執行 sea（每4週執行1週）"""
    return _should_execute_cycle(ip, "sea_cycle_start", cycle_weeks=SEA_CYCLE_WEEKS, logger=logger)


def should_execute_mushroom_arena(ip: str) -> tuple:
    """判斷是否該執行菇菇武道會（每4週執行1週）"""
    return _should_execute_cycle(ip, "mushroom_arena_cycle_start", cycle_weeks=MUSHROOM_ARENA_CYCLE_WEEKS, logger=logger)


def mushroom_arena(ip, d):
    """菇菇武道會主流程（每3週執行1週）。"""
    try:
        img_tools.click_str_by_server(d, '菇菇武道會', shift_y=-20)
        time.sleep(1)
        img_tools.click_str_by_server(d,'膜拜冠軍')
        time.sleep(1)
        click_white(d)
        time.sleep(1)
        d.click(490,919)#點擊退出
        time.sleep(1)
    except Exception as exc:
        logger.error(f"[{ip}] 菇菇武道會流程失敗: {exc}")


def _run_periodic_cycle(ip, record_name, should_execute_fn, action_fn, display_name, d, daily_limit_name=None, cycle_record_name=None):
    should, need_record = should_execute_fn(ip)
    
    logger.info(f"[{ip}] {display_name}: 檢查執行條件 - should={should}, need_record={need_record}")
    
    if should:
        if daily_limit_name:
            daily_record = return_time(ip, name=daily_limit_name)
            if daily_record and not daily_record.get("is_next_day", False):
                logger.info(f"[{ip}] {display_name} 今日已執行過，跳過。")
                return
        if need_record:
            # 如果有 cycle_record_name，同時記錄週期開始
            if cycle_record_name:
                time_recording(ip, name=cycle_record_name)
            time_recording(ip, name=record_name)
        else:
            # 即使不需要週期記錄，也要更新執行時間
            time_recording(ip, name=record_name)
        
        logger.info(f"[{ip}] {display_name}: 開始執行")
        action_fn(ip=ip, d=d)
        
        if daily_limit_name:
            time_recording(ip, name=daily_limit_name)
        time.sleep(3)
    else:
        logger.info(f"[{ip}] {display_name} 被排程跳過（未到週期或已過期）")
