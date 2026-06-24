"""副本排程：萬神試煉（週）、雙週副本（5556 only）。

從 new_main_v2.py Phase 4 抽離。

時間窗：
- 萬神試煉：週一下午（tm_hour > 12）或週二～週五，週日跳過
- 雙週副本：週六/週日 20:00–20:59，只在 `emulator-5556` 執行

排程狀態：
- 萬神試煉：`return_time(ip, "萬神試煉").is_next_week == True`
- 雙週副本：`return_time(ip, "雙週副本").is_next_biweek == True`
- 兩者都在首次執行（record is None）時跑
"""
from __future__ import annotations

import bot_state
import new_battle
from json_manager import return_time, time_recording
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch


def _run_weekly_dungeon(
    d,
    ip: str,
    stage: str,
    enable_dungeon_manager: bool,
    current_time,
) -> None:
    """Run 萬神試煉 if scheduled for this week and appropriate day/time."""
    record_time = return_time(ip, name="萬神試煉")
    if record_time is None:
        should_execute = True
    else:
        should_execute = record_time.get("is_next_week", False)
    logger.info(
        "[%s] 萬神試煉檢查: 頁面=%s 時間=%02d:%02d wday=%d 副本管家=%s "
        "should_execute=%s 紀錄=%s",
        ip, stage, current_time.tm_hour, current_time.tm_min,
        current_time.tm_wday, enable_dungeon_manager, should_execute, record_time,
    )
    if not enable_dungeon_manager:
        logger.info("[%s] 萬神試煉：副本管家已停用，跳過", ip)
        return
    if not should_execute:
        logger.info("[%s] 萬神試煉：本週已執行(is_next_week=False)，跳過", ip)
        return
    # 時間窗：週一下午(tm_hour>12) 或 週二~週六(tm_wday 1~5)，週日(6)跳過
    in_time_window = (
        (current_time.tm_wday == 0 and current_time.tm_hour > 12)
        or (1 <= current_time.tm_wday <= 5)
    )
    if not in_time_window:
        logger.info(
            "[%s] 萬神試煉：未到執行時間窗(週一下午或週二~週六)，wday=%d，跳過",
            ip, current_time.tm_wday,
        )
        return
    if stage != "主頁面":
        log_main_page_mismatch(d, ip, stage, "萬神試煉", "萬神試煉到達執行時間但不在主頁面")
        return
    logger.info("[%s] 萬神試煉：條件滿足，開始執行 fight_test", ip)
    bot_state.update_state(ip, task="萬神試煉", step="執行中")
    ok = new_battle.fight_test(d)
    if ok:
        time_recording(ip, name="萬神試煉")
        logger.info("[%s] 萬神試煉：完成(至少打了一關)，已寫入本週記錄", ip)
    else:
        logger.warning("[%s] 萬神試煉：未實際挑戰(入場/進場失敗)，不寫記錄，下次重試", ip)


def _run_biweekly_dungeon(
    d,
    ip: str,
    stage: str,
    enable_dungeon_manager: bool,
    now_local,
) -> None:
    """Run 雙週副本 for emulator-5556 on Sat/Sun at 20:xx if due this biweek."""
    if ip == "emulator-5556" and enable_dungeon_manager:
        biweek_record = return_time(ip, name="雙週副本")
        should_execute_biweek = False

        if biweek_record is None:
            should_execute_biweek = True
            logger.info("[%s] 雙週副本紀錄：無（首次執行）", ip)
        else:
            should_execute_biweek = biweek_record.get("is_next_biweek", False)
            logger.info("[%s] 雙週副本紀錄：%s, should_execute: %s", ip, biweek_record, should_execute_biweek)

        if (now_local.tm_wday in (5, 6)) and (now_local.tm_hour == 20) and now_local.tm_min >= 0:
            if should_execute_biweek:
                if stage == "主頁面":
                    bot_state.update_state(ip, task="雙週副本", step="排程觸發與穩定流程")
                    new_battle.run_biweekly_bounty_road_single(
                        d,
                        ip,
                        logger_obj=logger,
                        should_stop=lambda: bot_state.check_pause(ip) or bot_state.check_skip_sleep(ip),
                    )
                    time_recording(ip, name="雙週副本")
                else:
                    log_main_page_mismatch(d, ip, stage, "雙週副本", "雙週副本到達執行時間但不在主頁面")
            else:
                logger.info("[%s] 本兩週已執行過雙週副本，跳過本次執行", ip)
