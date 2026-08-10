import time
from json_manager import is_mushroom_arena_week, time_recording, return_time
from utils.logging_utils import logger
import img_tools
from tools import click_white
from game_actions.task_registry import TaskOutcome, TaskResult

# 武道會週期為 3 週；實際活動週由 json_manager.scheduling 的固定日曆錨點判斷。
MUSHROOM_ARENA_CYCLE_WEEKS = 3


def should_execute_mushroom_arena(ip: str) -> tuple:
    """判斷是否該執行菇菇武道會（固定日曆每 3 週開放 1 週）。"""
    should_execute = is_mushroom_arena_week()
    logger.info(f"[{ip}] 菇菇武道會: 日曆活動週={should_execute}")
    return should_execute, False


def mushroom_arena(ip, d) -> bool:
    """菇菇武道會主流程（每3週執行1週）。"""
    try:
        if not img_tools.click_str_by_server(d, '菇菇武道會', shift_y=-20):
            return False
        time.sleep(1)
        if not img_tools.click_str_by_server(d, '膜拜冠軍'):
            return False
        time.sleep(1)
        click_white(d)
        time.sleep(1)
        d.click(490,919)#點擊退出
        time.sleep(1)
        return True
    except Exception as exc:
        logger.error(f"[{ip}] 菇菇武道會流程失敗: {exc}")
        return False


def _run_periodic_cycle(ip, record_name, should_execute_fn, action_fn, display_name, d, daily_limit_name=None, cycle_record_name=None):
    should, need_record = should_execute_fn(ip)
    
    logger.info(f"[{ip}] {display_name}: 檢查執行條件 - should={should}, need_record={need_record}")
    
    if should:
        if daily_limit_name:
            daily_record = return_time(ip, name=daily_limit_name)
            if daily_record and not daily_record.get("is_next_day", False):
                logger.info(f"[{ip}] {display_name} 今日已執行過，跳過。")
                return TaskResult(TaskOutcome.SKIPPED, detail="今日已執行過")
        if need_record and cycle_record_name:
            # cycle_record_name 的語意是週期開始，可以在 action 前記錄。
            time_recording(ip, name=cycle_record_name)
        
        logger.info(f"[{ip}] {display_name}: 開始執行")
        result = action_fn(ip=ip, d=d)
        if result is False:
            logger.warning(f"[{ip}] {display_name}: action 未確認成功，不寫入完成記錄")
            return False

        # action 回傳 None 是既有成功契約；只有明確 False 才視為失敗。
        time_recording(ip, name=record_name)
        
        if daily_limit_name:
            time_recording(ip, name=daily_limit_name)
        time.sleep(3)
        return True
    else:
        logger.info(f"[{ip}] {display_name} 被排程跳過（未到週期或已過期）")
        return TaskResult(TaskOutcome.SKIPPED, detail="未到活動週期或已過期")
