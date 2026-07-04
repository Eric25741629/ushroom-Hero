"""副本排程：萬神試煉（週）、雙週副本（5556 only）。

從 new_main_v2.py Phase 4 抽離。

時間窗：
- 萬神試煉：週一下午（tm_hour > 12）起算，週二～週六，週日跳過
- 雙週副本：週六/週日 20:00–20:59，只在 `emulator-5556` 執行

排程狀態：
- 萬神試煉：`return_time(ip, "萬神試煉").is_next_week == True`
- 雙週副本：`return_time(ip, "雙週副本").is_next_biweek == True`
- 兩者都在首次執行（record is None）時跑
"""
from __future__ import annotations

import logging

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
    logging.info("目前頁面: {}, 當前時間: {}:{}".format(stage, current_time.tm_hour, current_time.tm_min))
    logging.info("萬神試煉紀錄: {}".format(record_time))
    fight_trial_time = 1
    if record_time is None:
        fight_trial_time = 0
        should_execute = True
    else:
        should_execute = record_time.get("is_next_week", False) or fight_trial_time == 0
        logging.info("fight_trial_time: {}, should_execute: {}, record_time: {}".format(fight_trial_time, should_execute, record_time))
    if enable_dungeon_manager and should_execute and (
        (current_time.tm_wday == 0 and current_time.tm_hour > 12) or
        (1 <= current_time.tm_wday <= 5)
    ):
        is_not_sunday = current_time.tm_wday != 6
        is_monday_afternoon = current_time.tm_wday == 0 and current_time.tm_hour > 12
        is_after_monday = current_time.tm_wday > 0
        should_run_fight_test = should_execute and is_not_sunday and (is_monday_afternoon or is_after_monday)
        if should_run_fight_test:
            if stage == "主頁面":
                bot_state.update_state(ip, task="萬神試煉", step="執行中")
                new_battle.fight_test(d)
                time_recording(ip, name="萬神試煉")
            else:
                log_main_page_mismatch(d, ip, stage, "萬神試煉", "萬神試煉到達執行時間但不在主頁面")


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
            logging.info("雙週副本紀錄：無（首次執行）")
        else:
            should_execute_biweek = biweek_record.get("is_next_biweek", False)
            logging.info("雙週副本紀錄：{}, should_execute: {}".format(biweek_record, should_execute_biweek))

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
                logging.info("[{ip}] 本兩週已執行過雙週副本，跳過本次執行")
