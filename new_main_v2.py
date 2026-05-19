import sys
import os

# 方案四：優化 SMB/NAS 執行效率
# 關閉 .pyc 檔案寫入，避免在網路路徑產生大量 I/O 導致卡頓
sys.dont_write_bytecode = True

import subprocess
from Sea import sea
import torch
import os
import datetime
import json
from adb_operations import (
    run_adb, connect_u2_with_retries, unlock_screen,
    start_game_by_icon, check_in_game, click_random,
    safe_click, ensure_screen_on, stop_app,
    screenshot_opencv, screenshot_pillow,
    set_screen_for_game, reset_screen_settings,
)
import daily_gift_task
import point
import uiautomator2 as u2
from everyday_mission.Guardian_Spirit_manger import get_Guardian_Spirit
import time
import numpy as np
from device import get_adb_devices,close_nofication,open_nofication
from adb_devices import launch_clone
import cv2
import mask
import img_tools
from Skill import *
from park import *
from family import Family_manager
from farm_v2 import manager as farm_manager
import new_battle
import random
from tools import click_white
from Spin_Wheel import spin_wheel
from Mission import mission
from State import state
from Assistant import assistant
import logging
import atexit
import pytz
import Store
import rank_events
#引入log 通知 不使用print
import Open_gold_paddle_ocr
import threading
from fight_car import flush_logs

from utils.logging_utils import (
    setup_logger_for_device,
    set_thread_logger,
    logger,
    default_logger,
    rotate_existing_logs_once,
)
from game_actions.skill_manager import switch_skill
from game_actions.reward_manager import reward
from utils.ocr_clicker import click_str
from game_state.detector import get_stage
from game_actions.miner_action import oracle, _should_perform_oracle_action
from game_actions.periodic_tasks import _run_periodic_cycle, should_execute_mushroom_arena, mushroom_arena
from game_initialization import (
    check_on_line,
    handle_game_startup_pages,
    resolve_stage_until_stable,
    StartupLoginConflictError,
)
from utils.model_loader import load_oracle_cnn_model
from game_actions.daily_tasks import daily_acceleration, click_arena_challenges
import new_cnn.cnn_model as cnn_model
# 導入新的JSON管理器，保持向後兼容
from json_manager import (
    time_recording, return_time, create_store_manager,
    is_expired, _should_execute_cycle, should_execute_sea_with_cooldown,
    is_record_expired
)
from miner.models.classifier import ClassifierCNN, load_cnn_model as load_miner_cnn_model
from miner.mining_service import run as run_mining
from miner.rl.rl_recorder import RLRecorder
import shlex
import re
from typing import Optional
import urllib3
import warnings
from urllib3.exceptions import InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)
warnings.filterwarnings('ignore', category=InsecureRequestWarning)
import requests
requests.packages.urllib3.disable_warnings()
import BUY
from utils.wake_up_handler import handle_device_wakeup, release_wakeup_lock
from utils.car_fight_utils import adjust_wake_time_for_cars
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR
import config_manager

import bot_state
from device_wrapper import MonitoredDevice
from opengold_v2.lamp_service import LampService as _LampServiceV2
from worker_webhook_api import ensure_worker_webhook_started
from runtime_services.device_scan_service import (
    refresh_adb_server,
    scan_and_start_devices,
    use_phone_ocr_lamp_mode,
)
from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    handle_connect_failure,
    is_emulator_serial,
    is_recoverable_connect_error,
    reset_connect_failure,
    sleep_until_wake_or_interrupt,
)
from runtime_services.push_server_service import ensure_push_server_started
from runtime_services.worker_sync_service import ensure_worker_sync_started
from runtime_services.web_session_service import (
    LOGIN_CONFLICT_SLEEP_SEC,
    handle_pending_web_launch,
    initialize_runtime_device,
    mark_login_conflict_sleep,
    process_online_check_requests,
    shutdown_web_devices,
)
from utils.smart_screenshot import SmartScreenshotRecorder

# Devices that should skip guardian spirit / skill partner collection.
# Keep legacy behavior: emulator-5558 is excluded from these tasks.
_DEVICE_SKIP_GUARDIAN = {
    "emulator-5558": True,
}


atexit.register(lambda: shutdown_web_devices(logger))
_smart_shot = SmartScreenshotRecorder()


def _sanitize_filename_part(value: object) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    return text[:80] or "unknown"


def save_error_screenshot(device_obj, ip: str, stage: str, reason: str) -> Optional[str]:
    try:
        image_path = _smart_shot.capture(
            device_obj=device_obj,
            ip=ip,
            stage=stage,
            reason=reason,
            task="",
        )
        if not image_path:
            logger.error(f"[{ip}] {reason} 失敗，無法取得截圖，stage={stage}")
            return None
        logger.error(f"[{ip}] {reason}，已保存截圖，stage={stage}, path={image_path}")
        return image_path
    except Exception as e:
        logger.error(f"[{ip}] 保存錯誤截圖失敗: reason={reason}, stage={stage}, err={e}", exc_info=True)
        return None


def log_main_page_mismatch(device_obj, ip: str, stage: str, task: str, reason: str) -> Optional[str]:
    bot_state.update_state(ip, task=task, step=f"未在主頁面: {stage}")
    screenshot_path = _smart_shot.capture(
        device_obj=device_obj,
        ip=ip,
        stage=stage,
        reason=reason,
        task=task,
    )
    if not screenshot_path:
        screenshot_path = save_error_screenshot(device_obj, ip, stage, reason)
    logger.error(f"[{ip}] {reason}，stage={stage}, screenshot={screenshot_path}")
    return screenshot_path


def stop_runtime_device_for_sleep(device_obj, ip: str, backend_kind: str, logger_obj) -> None:
    """Stop the current runtime device immediately for force-sleep handling."""
    if device_obj is None:
        logger_obj.warning(f"[{ip}] 強制休眠時找不到裝置實例，略過停止動作")
        return

    try:
        if backend_kind == "web_h5":
            close_fn = getattr(device_obj, "close", None)
            if callable(close_fn):
                close_fn()
                logger_obj.info(f"[{ip}] 強制休眠已關閉 web_h5 瀏覽器")
            else:
                device_obj.app_stop("com.mxdzz.tw.and")
                logger_obj.info(f"[{ip}] 強制休眠已停止 web_h5 會話")
        else:
            device_obj.app_stop("com.mxdzz.tw.and")
            logger_obj.info(f"[{ip}] 強制休眠已關閉 adb 應用")
    except Exception as stop_err:
        logger_obj.warning(f"[{ip}] 強制休眠停止裝置失敗: backend={backend_kind}, err={stop_err}")


def run_sleep_cycle(
    ip: str,
    logger_obj,
    *,
    forced_wake_ts: Optional[float] = None,
    force_sleep_now: bool = False,
    sleep_policy: str = "aligned_window",
    sleep_reason: str = "常規對齊喚醒",
    enable_dungeon_manager: bool = False,
):
    cur_ts = time.time()

    def calc_aligned_wake_ts(base_ts: float, min_sleep_sec: int, win_min: int = 20) -> float:
        earliest = base_ts + min_sleep_sec
        hour_floor = earliest - (earliest % 3600)
        win_end = hour_floor + win_min * 60

        if earliest <= win_end:
            return float(random.randint(int(earliest), int(win_end)))
        next_hour = hour_floor + 3600
        return float(next_hour + random.randint(0, win_min * 60))

    _cfg = config_manager.get_device_config(ip)
    _sleep_min = float(_cfg.get("sleep_min_hours", 1.0))
    _sleep_max = float(_cfg.get("sleep_max_hours", 1.0))
    min_sleep_sec = int(random.uniform(_sleep_min, max(_sleep_min, _sleep_max)) * 3600)

    if forced_wake_ts is not None:
        wake_ts = forced_wake_ts
    else:
        wake_ts = calc_aligned_wake_ts(cur_ts, min_sleep_sec, win_min=20)

    wake_ts = adjust_wake_time_for_cars(wake_ts)

    if ip == "emulator-5556" and enable_dungeon_manager:
        today_wday = time.localtime().tm_wday
        if today_wday in (5, 6):
            biweek_record = return_time(ip, name="雙週副本")
            should_execute_biweek = False

            if biweek_record is None:
                should_execute_biweek = True
            else:
                should_execute_biweek = biweek_record.get("is_next_biweek", False)

            if should_execute_biweek:
                now = time.time()
                today_midnight = now - (now % 86400) + 86400
                biweek_wake_ts = today_midnight - 86400 + 19 * 3600 + 57 * 60

                if biweek_wake_ts > now and biweek_wake_ts < wake_ts:
                    wake_ts = biweek_wake_ts
                    logger_obj.info(f"[{ip}] 雙週副本喚醒條件觸發，設定喚醒時間為 19:57")

    sleep_duration = max(0, int(wake_ts - cur_ts))
    wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
    bot_state.update_state(
        ip,
        task="休眠中",
        step=f"{sleep_reason} | policy={sleep_policy} | 預計休眠 {sleep_duration/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)",
        next_wake_at=wake_ts,
    )

    if forced_wake_ts is not None:
        logger_obj.info(
            f"[{ip}] 套用強制休眠策略: reason={sleep_reason}, policy={sleep_policy}, "
            f"預計休眠 {sleep_duration/60:.1f} 分鐘"
        )
    elif force_sleep_now:
        logger_obj.info(f"[{ip}] 已強制中斷當前任務，將直接進入休眠流程，預計休眠 {sleep_duration/60:.1f} 分鐘")
    elif "7fe98fc6" in ip:
        logger_obj.info(f"[{ip}] 裝置為 7fe98fc6，設定為每小時喚醒一次，預計休眠 {sleep_duration/60:.1f} 分鐘")
    else:
        logger_obj.info(f"[{ip}] 本次喚醒將落在每小時 00~20 分，預計休眠 {sleep_duration/60:.1f} 分鐘")

    interrupted = sleep_until_wake_or_interrupt(ip, wake_ts, logger_obj)
    return wake_ts, interrupted, time.time()

class LoginConflictError(Exception):
    """自定義異常：用於處理異地登錄並終止當前喚醒 session"""
    pass


class StartupBypassError(Exception):
    """遊戲啟動失敗，觸發避讓休眠。"""
    pass


def _run_lamp(d, ip: str, lamp_dur: int, is_compare: bool = True):
    """開神燈執行入口；use_opengold_v2=true 時走 LampService，否則走舊版。"""
    device_cfg = config_manager.get_device_config(ip)
    duration = lamp_dur + random.randint(-10, 10)
    if device_cfg.get("use_opengold_v2", False):
        svc = _LampServiceV2(d, device_ip=ip)
        svc.run(times=duration, is_compare=is_compare)
    else:
        Open_gold_paddle_ocr.open_the_gold(d, times=duration, is_compare=is_compare, device_ip=ip)


def get_stage_with_check(d, ip, Cnn_model, img=None):
    """
    使用與啟動流程相同的狀態判斷器。
    先清掉已知首頁彈窗，再回傳穩定 stage。
    """
    stage = resolve_stage_until_stable(
        d,
        ip,
        Cnn_model=Cnn_model,
        reward_fn=reward,
        logger=logger,
        img=img,
    )
    if stage == "異地登錄":
        logger.warning(f"[{ip}] 全域偵測到異地登錄，強制停止遊戲")
        d.app_stop("com.mxdzz.tw.and")
        mark_login_conflict_sleep(ip)
        raise LoginConflictError("偵測到異地登錄")
    return stage


# ---------------------------------------------------------------------------
# Helper functions extracted from main() to reduce its size
# ---------------------------------------------------------------------------

def _handle_startup_sleep(ip: str, device_logger) -> None:
    startup_sleep_sec = int(_STARTUP_SLEEP_SEC_BY_DEVICE.get(ip, 0) or 0)
    elapsed_sec = int(time.time() - _PROCESS_START_TS)
    remaining_startup_sleep = max(0, startup_sleep_sec - elapsed_sec)
    if remaining_startup_sleep > 0:
        for remain in range(remaining_startup_sleep, 0, -1):
            if ip == "emulator-5554":
                if (
                    bot_state.has_pending_online_check_request("emulator-5554")
                    or bot_state.is_online_check_priority_active("emulator-5554")
                ):
                    device_logger.info(f"[{ip}] 收到 emulator-5558 上線檢查請求，提前結束啟動休眠")
                    break
            if bot_state.has_pending_web_launch_request(ip):
                device_logger.info(f"[{ip}] 收到中控啟動請求，提前結束啟動休眠")
                break
            bot_state.update_state(ip, task="啟動後休眠", step=f"{remain} 秒後開始執行")
            time.sleep(1)


def _maybe_resume_sleep(
    ip: str,
    Cnn_model,
    resume_sleep_until_ts: Optional[float],
    resume_sleep_reason: str,
    logger_obj,
) -> tuple:
    """Handle resume-sleep gate at top of main while loop.

    Returns (updated_ts, updated_reason, should_continue).
    Caller does `if should_continue: continue`.
    """
    if ip == 'emulator-5554':
        has_req = bot_state.has_pending_online_check_request('emulator-5554')
        has_priority = bot_state.is_online_check_priority_active('emulator-5554')
        if has_req or has_priority:
            process_online_check_requests(ip, Cnn_model, logger_obj, check_on_line)
            time.sleep(0.2)
            return resume_sleep_until_ts, resume_sleep_reason, True
        if resume_sleep_until_ts is not None and time.time() < resume_sleep_until_ts:
            wake_time_str = time.strftime("%H:%M", time.localtime(resume_sleep_until_ts))
            remain_sec = max(0, int(resume_sleep_until_ts - time.time()))
            detail = resume_sleep_reason or "返回休眠"
            bot_state.update_state(
                ip,
                task="休眠中",
                step=f"{detail} | 預計休眠 {remain_sec/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)",
                next_wake_at=resume_sleep_until_ts,
            )
            logger_obj.info(f"[{ip}] {detail}，返回休眠至 {wake_time_str}")
            interrupted = sleep_until_wake_or_interrupt(ip, resume_sleep_until_ts, logger_obj)
            if interrupted:
                return None, "", True
            return None, "", False
    elif resume_sleep_until_ts is not None and time.time() < resume_sleep_until_ts:
        wake_time_str = time.strftime("%H:%M", time.localtime(resume_sleep_until_ts))
        remain_sec = max(0, int(resume_sleep_until_ts - time.time()))
        detail = resume_sleep_reason or "返回休眠"
        bot_state.update_state(
            ip,
            task="休眠中",
            step=f"{detail} | 預計休眠 {remain_sec/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)",
            next_wake_at=resume_sleep_until_ts,
        )
        logger_obj.info(f"[{ip}] {detail}，返回休眠至 {wake_time_str}")
        interrupted = sleep_until_wake_or_interrupt(ip, resume_sleep_until_ts, logger_obj)
        if interrupted:
            return None, "", True
        return None, "", False
    return resume_sleep_until_ts, resume_sleep_reason, False


def _run_at_main_page(
    d,
    ip: str,
    Cnn_model,
    task_name: str,
    mismatch_reason: str,
    fn,
    *,
    step: str = "執行中",
    log: Optional[str] = None,
) -> str:
    """Fetch stage, run fn() on main page, else log mismatch. Returns stage."""
    stage = get_stage_with_check(d, ip, Cnn_model)
    if stage == "主頁面":
        if log:
            bot_state.update_state(ip, task=task_name, step=step, log=log)
        else:
            bot_state.update_state(ip, task=task_name, step=step)
        fn()
    else:
        log_main_page_mismatch(d, ip, stage, task_name, mismatch_reason)
    return stage


def _run_lamp_if_due(d, ip: str, stage: str) -> None:
    """Run lamp (神燈) for the appropriate device mode using the given stage."""
    if use_phone_ocr_lamp_mode(ip):
        if stage == "主頁面":
            lamp_dur = config_manager.get_device_config(ip).get("lamp_duration_sec", 300)
            bot_state.update_state(ip, task="開神燈 (OCR)", step=f"執行中 ({lamp_dur}s)")
            logger.info(f"[{ip}] 使用手機 OCR 開神燈模式，持續 {lamp_dur}s")
            _run_lamp(d, ip, lamp_dur, is_compare=True)
        else:
            log_main_page_mismatch(d, ip, stage, "開神燈 (OCR)", "手機 OCR 開神燈前不在主頁面")
    if 'emulator-5560' in ip:
        if stage == "主頁面":
            lamp_dur = int(config_manager.get_device_config(ip).get("lamp_duration_sec", 300))
            bot_state.update_state(ip, task="開神燈 (OCR)", step=f"5560 執行中 ({lamp_dur}s)")
            logger.info(f"[{ip}] 使用 5560 OCR 開神燈模式，持續 {lamp_dur}s")
            _run_lamp(d, ip, lamp_dur, is_compare=False)
        else:
            log_main_page_mismatch(d, ip, stage, "開神燈 (OCR)", "5560 OCR 開神燈前不在主頁面")
    elif ip != "emulator-5558":
        if stage == "主頁面":
            device_cfg = config_manager.get_device_config(ip)
            lamp_interval = float(device_cfg.get("lamp_check_interval", 2))
            lamp_dur = int(device_cfg.get("lamp_duration_sec", 300))
            lamp_record_name = "general_lamp_last_execution"
            lamp_record = return_time(ip, name=lamp_record_name)
            now_ts = time.time()

            if lamp_interval <= 0:
                logger.warning(f"[{ip}] lamp_check_interval={lamp_interval}h 非法，改用 2h")
                lamp_interval = 2.0

            threshold_sec = lamp_interval * 3600.0
            last_ts = None
            last_dt_str = "None"
            elapsed_sec = None
            should_run_lamp = False
            reason = ""

            if lamp_record and lamp_record.get("timestamp"):
                try:
                    last_ts = float(lamp_record.get("timestamp"))
                except (TypeError, ValueError):
                    last_ts = None

            if last_ts and last_ts > 0:
                elapsed_sec = max(0.0, now_ts - last_ts)
                last_dt_str = datetime.datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")
                should_run_lamp = elapsed_sec >= threshold_sec
                remaining_sec = max(0.0, threshold_sec - elapsed_sec)
                reason = (
                    "elapsed>=threshold"
                    if should_run_lamp
                    else f"elapsed<threshold, remaining={remaining_sec/60:.1f}m"
                )
            else:
                should_run_lamp = True
                reason = "no_valid_last_record"

            elapsed_h_text = "N/A" if elapsed_sec is None else f"{elapsed_sec/3600.0:.2f}"
            logger.info(
                f"[{ip}] 開神燈排程檢查: last={last_dt_str}, elapsed_h={elapsed_h_text}, "
                f"threshold_h={lamp_interval:.2f}, should_run={should_run_lamp}, reason={reason}"
            )

            if should_run_lamp:
                bot_state.update_state(ip, task="開神燈", step=f"執行中 ({lamp_dur}s)")
                logger.info(
                    f"[{ip}] 觸發一般開神燈: duration={lamp_dur}s, interval_h={lamp_interval:.2f}, record={lamp_record_name}"
                )
                _run_lamp(d, ip, lamp_dur)
                time_recording(ip, name=lamp_record_name)
                logger.info(f"[{ip}] 一般開神燈完成，已更新執行時間記錄: {lamp_record_name}")
            else:
                logger.info(f"[{ip}] 本輪跳過一般開神燈: {reason}")
        else:
            logger.info(f"[{ip}] 本輪跳過一般開神燈: stage={stage} (需主頁面)")
            log_main_page_mismatch(d, ip, stage, "開神燈", "一般開神燈前不在主頁面")


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


def _run_daily_tasks(
    d,
    ip: str,
    Cnn_model,
    clf,
    rl_recorder,
    current_time,
    enable_dungeon_manager: bool,
    wheel_manager,
    mission_manager,
    family_manager,
) -> None:
    """Execute the full per-wake task sequence (20 tasks) for one device."""
    # Task 1: 地獄之門
    stage = get_stage_with_check(d, ip, Cnn_model)
    record_time = return_time(ip, name="地獄之門")
    logging.info("目前頁面: {}, 當前時間: {}:{}".format(stage, current_time.tm_hour, current_time.tm_min))
    logging.info("地獄之門紀錄: {}".format(record_time))
    hell_gate_time = 1
    if record_time is None:
        hell_gate_time = 0
        should_execute = True
    else:
        should_execute = record_time.get("is_next_day", False) or hell_gate_time == 0
        logging.info("hell_gate_time: {}, should_execute: {}, record_time: {}".format(hell_gate_time, should_execute, record_time))
    if should_execute and current_time.tm_min < 20:
        if stage == "主頁面":
            bot_state.update_state(ip, task="地獄之門", step="戰鬥執行中")
            new_battle.hell_door(d, ip)
            time_recording(ip, name="地獄之門")
        else:
            log_main_page_mismatch(d, ip, stage, "地獄之門", "地獄之門到達執行時間但不在主頁面")
    else:
        logger.info("地獄之門: 尚未到達執行時間或已執行過")

    # Task 2: 農場任務
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="農場任務",
        mismatch_reason="農場任務前不在主頁面",
        fn=lambda: farm_manager.farm(d, ip, Cnn_model),
        step="準備進入",
    )

    # Task 3: 點擊寶箱
    def _tap_chest():
        d.tap(random.randint(261, 271), 369)
        time.sleep(1)
        reward(d)
        time.sleep(3)
    _run_at_main_page(
        d, ip, Cnn_model,
        task_name="點擊寶箱",
        mismatch_reason="點擊寶箱前不在主頁面",
        fn=_tap_chest,
        step="領取獎勵",
    )

    # Task 4: 家族任務 — stage reused by Tasks 5+6
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="家族任務",
        mismatch_reason="家族任務前不在主頁面",
        fn=family_manager.go_to_family,
        step="執行中",
    )

    # Task 5 & 6: 守護靈 + 技能夥伴 (reuse stage from Task 4, matching original)
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False):
        if stage == "主頁面":
            guardian_record = return_time(ip, name="guardian_spirit")
            should_get_guardian = True
            if guardian_record is not None:
                should_get_guardian = guardian_record.get("is_next_day", False)
            if should_get_guardian:
                bot_state.update_state(ip, task="領取守護靈", step="領取中")
                get_Guardian_Spirit(d)
                time_recording(ip, name="guardian_spirit")
        else:
            log_main_page_mismatch(d, ip, stage, "領取守護靈", "領取守護靈前不在主頁面")
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False):
        if stage == "主頁面":
            bot_state.update_state(ip, task="抽技能夥伴", step="領取中")
            get_skill_and_partner(d)
            time.sleep(3)
        else:
            log_main_page_mismatch(d, ip, stage, "抽技能夥伴", "抽技能夥伴前不在主頁面")

    # Task 7: 商店購買
    stage = get_stage_with_check(d, ip, Cnn_model)
    if stage == "主頁面":
        device_cfg = config_manager.get_device_config(ip)
        if device_cfg.get("enable_shop_manager", True):
            store_record = return_time(ip, name="Store")
            should_check_store = is_record_expired(store_record, 10800) or current_time.tm_hour == 23
            if should_check_store:
                bot_state.update_state(ip, task="商店購買", step="執行中")
                Store.buy_store(d, Cnn_model)
                time_recording(ip, name="Store")
            else:
                logger.info("商店購買: 尚未過期且非23點，跳過")
        else:
            logger.info(f"[{ip}] 購物管家已停用，跳過商店購買")
    else:
        bot_state.update_state(ip, task="商店購買", step=f"未在主頁面: {stage}")
        screenshot_path = save_error_screenshot(d, ip, stage, "商店購買前不在主頁面")
        logger.error(f"[{ip}] 商店購買前不在主頁面，stage={stage}, screenshot={screenshot_path}")

    # Task 8: 坐騎強化
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="坐騎強化",
        mismatch_reason="坐騎強化前不在主頁面",
        fn=lambda: rank_events.park_spring(d, ip),
    )

    # Task 9: 每日加速 (no main-page guard)
    bot_state.update_state(ip, task="每日加速", step="領取中")
    daily_acceleration(d, ip, Cnn_model)

    # Task 10: 競技場挑戰
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="競技場挑戰",
        mismatch_reason="競技場挑戰前不在主頁面",
        fn=lambda: click_arena_challenges(d, ip),
        step="領取中",
    )

    # Task 11: 挖礦/Oracle (original had duplicate get_stage_with_check — collapsed to one via helper)
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="挖礦/Oracle",
        mismatch_reason="挖礦/Oracle 前不在主頁面",
        fn=lambda: oracle(
            d, None, ip=ip, clf=clf, rl_recorder=rl_recorder,
            Cnn_model=Cnn_model,
            max_duration_minutes=config_manager.get_device_config(ip).get("mining_duration_min", 6),
        ),
        log="開始執行挖礦任務",
    )

    # Task 12: 所有日常任務 (20:00–23:00 only)
    if 20 <= current_time.tm_hour < 23:
        stage = _run_at_main_page(
            d, ip, Cnn_model,
            task_name="所有日常任務",
            mismatch_reason="所有日常任務執行前不在主頁面",
            fn=lambda: mission_manager.do_allmission(),
            step="檢查/執行中",
        )

    # Task 13: 菇菇武道會
    _run_at_main_page(
        d, ip, Cnn_model,
        task_name="菇菇武道會",
        mismatch_reason="菇菇武道會前不在主頁面",
        fn=lambda: _run_periodic_cycle(
            ip,
            record_name="mushroom_arena_cycle_start",
            should_execute_fn=should_execute_mushroom_arena,
            action_fn=mushroom_arena,
            display_name="菇菇武道會",
            d=d,
            daily_limit_name="mushroom_arena_daily",
        ),
        step="週期檢查/執行",
    )

    # Task 14: 航海任務
    _run_at_main_page(
        d, ip, Cnn_model,
        task_name="航海任務 (Sea)",
        mismatch_reason="航海任務前不在主頁面",
        fn=lambda: _run_periodic_cycle(
            ip,
            record_name="sea_last_execution",
            should_execute_fn=should_execute_sea_with_cooldown,
            action_fn=sea,
            display_name="sea",
            d=d,
            cycle_record_name="sea_cycle_start",
        ),
        step="週期檢查/執行",
    )

    # Task 15: 萬神試煉
    stage = get_stage_with_check(d, ip, Cnn_model)
    _run_weekly_dungeon(d, ip, stage, enable_dungeon_manager, current_time)

    # Task 16: 雲端戰鬥
    if enable_dungeon_manager:
        _run_at_main_page(
            d, ip, Cnn_model,
            task_name="雲端戰鬥",
            mismatch_reason="雲端戰鬥前不在主頁面",
            fn=lambda: new_battle.run_weekly_cloud_fighting_single(d, ip),
            step="領取中",
        )
    else:
        logger.info(f"[{ip}] 副本管家已停用，跳過雲端戰鬥")

    # Task 17: 雙週副本
    stage = get_stage_with_check(d, ip, Cnn_model)
    now_local = time.localtime()
    _run_biweekly_dungeon(d, ip, stage, enable_dungeon_manager, now_local)

    # Task 18: 好友每日禮物 — stage refreshed after run for Task 19 (lamp)
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="好友每日禮物",
        mismatch_reason="好友每日禮物前不在主頁面",
        fn=lambda: daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1),
        step="領取中",
    )
    if stage == "主頁面":
        stage = get_stage_with_check(d, ip, Cnn_model)

    # Task 19: 開神燈
    _run_lamp_if_due(d, ip, stage)

    # Task 20: 轉盤金幣
    def _spin_wheel():
        logger.info(f"[{ip}] 準備執行轉盤金幣")
        if wheel_manager.spin_and_send_gold():
            logger.info(f"[{ip}] 轉盤金幣執行成功，本次確實完成轉盤操作")
        else:
            logger.info(f"[{ip}] 轉盤金幣本次未執行或未偵測到紅點，已略過")
    _run_at_main_page(
        d, ip, Cnn_model,
        task_name="轉盤金幣",
        mismatch_reason="轉盤金幣執行前不在主頁面",
        fn=_spin_wheel,
        step="執行中",
    )

    # Device cleanup
    if ip == "emulator-5558":
        switch_skill(d, '騙人用')
    if "fc65396d" in ip:
        d.app_start("com.android.chrome")
        time.sleep(2)
        d.app_stop("com.mxdzz.tw.and")
        time.sleep(1)
    else:
        d.app_stop("com.mxdzz.tw.and")


def main(ip, Cnn_model, oralce_cnn_model, oralce_classes, ocr):
    # 初始化狀態監控
    bot_state.init_device(ip)
    device_logger = logger
    backend_kind = "adb"
    enable_dungeon_manager = bool(
        config_manager.get_device_config(ip).get(
            "enable_dungeon_manager",
            config_manager.get_device_config(ip).get("enable_dungeon", True),
        )
    )
    d_orig = None
    resume_sleep_until_ts = None
    resume_sleep_reason = ""
    force_sleep_now = False
    
    try:
        # 為該設備設定獨立的 logger（按 IP 分檔），先建立 logger 以便連線階段可記錄
        device_logger = setup_logger_for_device(ip)
        # 設定當前線程的 logger
        set_thread_logger(device_logger)
        backend_kind = str(config_manager.get_device_config(ip).get("backend", "adb")).strip().lower()

        _handle_startup_sleep(ip, device_logger)

        while True:
            try:
                d_orig, d, backend_kind, skip_online_check_once = initialize_runtime_device(
                    ip,
                    device_logger,
                    connect_u2_with_retries,
                )
                reset_connect_failure(ip)
                break
            except ForceSleepRequested as e:
                force_sleep_now = True
                device_logger.warning(f"[{ip}] 初始化期間收到強制休眠，暫停啟動並進入休眠: {e}")
                stop_runtime_device_for_sleep(d_orig, ip, backend_kind, device_logger)
                _, _, wake_up_time = run_sleep_cycle(
                    ip,
                    device_logger,
                    force_sleep_now=True,
                    sleep_policy="force_sleep",
                    sleep_reason="強制休眠",
                    enable_dungeon_manager=enable_dungeon_manager,
                )
                force_sleep_now = False
                continue
            except Exception as e:
                if backend_kind == "web_h5":
                    device_logger.error(f"[{ip}] web_h5 backend init failed: {e}")
                    device_logger.warning(f"[{ip}] web_h5 init backoff 30s to avoid relaunch storm")
                    time.sleep(30)
                    bot_state.set_offline(ip, reason=f"init failed: {e}")
                    return
                handle_connect_failure(ip, e, device_logger, _running_threads, logger, refresh_adb_server)
                device_logger.error(f"[{ip}] connect init failed: {e}")
                bot_state.set_offline(ip, reason=f"init failed: {e}")
                return
        
        wake_up_time = time.time()
        
        # 為每個設備生成隨機的喚醒分鐘偏移 (0 到 2 分鐘)
        wake_random_offset = random.randint(0, 2)
        logger.info(f"[{ip}] 設定隨機喚醒偏移: {wake_random_offset} 分鐘")
        protect = False if ('emulator-5558' in ip or 'emulator-5562' in ip or '7fe98fc6' in ip or 'fc65396d' in ip) else True

        # manager = ParkingManager(
        #     device=d, reader=easyocr_reader, ip=ip, cnn_model=Cnn_model,protect=protect)
        # battle_manager = new_battle.BattleManager(
        #     device=d, reader=easyocr_reader, cnn_model=Cnn_model)
        wheel_manager = spin_wheel(device=d, cnn_model=Cnn_model,devices_serial=ip)
        mission_manager = mission(device=d, ip=ip)
        family_manager = Family_manager(device=d, ip=ip, cnn_model=Cnn_model)
        state_manager = state(device=d, cnn_model=Cnn_model)
        # assistant_manager = assistant(d=d, cnn_model=Cnn_model)
        clf = ClassifierCNN(model=oralce_cnn_model, classes=oralce_classes, dataset_root=DATASET_LOW_CONFIDENCE_DIR_STR)

        # 建立 RL 記錄器（記錄但不自動訓練）
        rl_logs_dir = os.path.join("miner", "rl_logs", ip.replace(":", "_"))
        os.makedirs(rl_logs_dir, exist_ok=True)
        rl_recorder = RLRecorder(
            log_dir=rl_logs_dir,
            auto_train=False,  # 不自動訓練
            flush_interval=1,
        )

        while (1):
            if bot_state.check_force_sleep(ip):
                raise ForceSleepRequested("force sleep requested from dashboard")
            if handle_pending_web_launch(ip, d, backend_kind, logger):
                continue
            process_online_check_requests(ip, Cnn_model, logger, check_on_line)
            resume_sleep_until_ts, resume_sleep_reason, _skip = _maybe_resume_sleep(
                ip, Cnn_model, resume_sleep_until_ts, resume_sleep_reason, logger
            )
            if _skip:
                continue
            forced_wake_ts = None
            sleep_policy = "aligned_window"
            sleep_reason = "常規對齊喚醒"
            try:
                # --- 喚醒與解鎖手機 ---
                bot_state.update_state(ip, task="喚醒檢查", step="正在檢查螢幕狀態")
                d = handle_device_wakeup(
                    d,
                    ip,
                    logger,
                    Cnn_model,
                    skip_online_check_once=skip_online_check_once,
                )
                # wake_up_handler may reconnect and return raw uiautomator2 device.
                # Re-wrap to keep a consistent interface (tap/click/swipe/pause guard).
                if not isinstance(d, MonitoredDevice):
                    d = MonitoredDevice(d, ip)
                skip_online_check_once = False
                if ip == 'emulator-5554':
                    has_req = bot_state.has_pending_online_check_request('emulator-5554')
                    has_priority = bot_state.is_online_check_priority_active('emulator-5554')
                    if has_req or has_priority:
                        logger.info(f"[{ip}] 喚醒流程後偵測到互檢請求，立即返回處理 emulator-5558 上線檢查")
                        time.sleep(0.2)
                        continue

                start = time.time()
                img = d.screenshot(format='opencv')
                # 進行ocr
                if state_manager.get_state() == "滑動解除節電模式'":
                    unlock_screen(d)
                if check_in_game(d) :
                    logger.debug(f"[{ip}] 已確認在遊戲中")
                    # 即使在遊戲中，也要檢查是否有「放置獎勵」或「領取」彈窗阻擋
                    stage_check = get_stage_with_check(d, ip, Cnn_model, img=img)
                    if stage_check in ["放置獎勵", "離線獎勵", "領取"]:
                        logger.info(f"[{ip}] 偵測到 {stage_check} 彈窗，執行自動領取...")
                        reward(d)
                        time.sleep(2)
                else:
                    logger.debug(f"[{ip}] 未確認在遊戲中，準備啟動")
                    bot_state.update_state(ip, task="啟動遊戲", step="正在啟動 APP")
                    if 'fc65396d' in ip or '192.168' in ip:
                        
                        time.sleep(1)
                        try:
                            if d.xpath_click('//*[@text="菇勇者傳說"]'):
                                logger.info(f"[{ip}] 找到遊戲圖示,點擊啟動")
                                time.sleep(2 + random.random())
                                set_screen_for_game(ip, logger=logger)
                            else:
                                raise Exception("未找到遊戲圖示")
                        except Exception as e:
                            logger.exception(f"[{ip}] launch_clone fallback failed, trying clone launch. error={e}")
                            output = launch_clone("com.mxdzz.tw.and", 2,device_serial=ip)
                            set_screen_for_game(ip, logger=logger)
                        time.sleep(1)
                        
                    else:
                        # 使用圖示啟動遊戲 (模擬真人操作)
                        logger.info(f"[{ip}] 透過桌面圖示啟動遊戲")
                        start_game_by_icon(d, ip)

                    result = handle_game_startup_pages(
                        d=d,
                        ip=ip,
                        start_game_fn=start_game_by_icon,
                        reward_fn=reward,
                        logger=device_logger
                    )
                    if result:
                        logger.info(f"[{ip}] 遊戲已進入可操作狀態")
                    else:
                        logger.warning(f"[{ip}] 遊戲啟動失敗，避讓休眠 30 分鐘...")
                        # 計算 30 分鐘後的喚醒時間
                        wake_ts = time.time() + 1800
                        wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
                        bot_state.update_state(ip, task="休眠中", step=f"啟動失敗避讓 (預計 {wake_time_str} 喚醒)", next_wake_at=wake_ts)
                        
                        # 拋出啟動避讓例外，交給外層套用固定休眠策略
                        raise StartupBypassError("啟動失敗避讓")
                                    # img = d.screenshot(format='opencv')
                # if red_envelope.check_red_in_pic(img):
                # red_envelope.open_red_envelope(d)

                _run_daily_tasks(
                    d, ip, Cnn_model, clf, rl_recorder,
                    current_time=time.localtime(),
                    enable_dungeon_manager=enable_dungeon_manager,
                    wheel_manager=wheel_manager,
                    mission_manager=mission_manager,
                    family_manager=family_manager,
                )
            except ForceSleepRequested as e:
                force_sleep_now = True
                sleep_policy = "force_sleep"
                sleep_reason = "強制休眠"
                logger.warning(f"[{ip}] 收到強制休眠請求，終止當前任務並進入休眠: {e}")
                stop_runtime_device_for_sleep(d, ip, backend_kind, logger)

            except StartupBypassError as e:
                forced_wake_ts = time.time() + 1800
                sleep_policy = "startup_bypass_30m"
                sleep_reason = "啟動失敗避讓"
                logger.warning(
                    f"[{ip}] 啟動流程中斷: {e} | policy={sleep_policy}, "
                    f"forced_sleep_sec=1800"
                )

            except StartupLoginConflictError as e:
                forced_wake_ts = time.time() + LOGIN_CONFLICT_SLEEP_SEC
                sleep_policy = "startup_login_conflict_30m"
                sleep_reason = "啟動偵測異地登錄"
                logger.warning(
                    f"[{ip}] 啟動階段異地登錄中斷本次執行: {e} | policy={sleep_policy}, "
                    f"forced_sleep_sec={LOGIN_CONFLICT_SLEEP_SEC}"
                )

            except LoginConflictError as e:
                forced_wake_ts = time.time() + LOGIN_CONFLICT_SLEEP_SEC
                sleep_policy = "runtime_login_conflict_30m"
                sleep_reason = "執行中偵測異地登錄"
                logger.warning(
                    f"[{ip}] 異地登錄中斷本次執行: {e} | policy={sleep_policy}, "
                    f"forced_sleep_sec={LOGIN_CONFLICT_SLEEP_SEC}"
                )
                # 不需要額外處理，後續代碼會處理釋放鎖和休眠

            end = time.time()
            if 'fc65396d' in ip or '192.168' in ip:
                reset_screen_settings(ip, logger=logger)
                time.sleep(1)
                try:
                    d.info
                except Exception as e:
                    logger.error(f"重新連線: {e}")
                    try:
                        d_orig = connect_u2_with_retries(ip, logger=device_logger)
                        d = MonitoredDevice(d_orig, ip)
                    except Exception as e2:
                        handle_connect_failure(ip, e2, device_logger, _running_threads, logger, refresh_adb_server)
                        logger.error(f"[{ip}] 重連失敗: {e2}")
                open_nofication(d)
                d.screen_off()
            release_wakeup_lock(ip)
            wake_ts, interrupted, wake_up_time = run_sleep_cycle(
                ip,
                logger,
                forced_wake_ts=forced_wake_ts,
                force_sleep_now=force_sleep_now,
                sleep_policy=sleep_policy,
                sleep_reason=sleep_reason,
                enable_dungeon_manager=enable_dungeon_manager,
            )
            if interrupted and bot_state.has_pending_web_launch_request(ip) and time.time() < wake_ts:
                resume_sleep_until_ts = wake_ts
                resume_sleep_reason = "手動操作結束後返回休眠"
            if interrupted and ip == "emulator-5554" and time.time() < wake_ts:
                if (
                    bot_state.has_pending_online_check_request("emulator-5554")
                    or bot_state.is_online_check_priority_active("emulator-5554")
                ):
                    resume_sleep_until_ts = wake_ts
                    resume_sleep_reason = "互檢完成後返回休眠"
    except Exception as e:
        if backend_kind != "web_h5" and is_emulator_serial(ip) and is_recoverable_connect_error(str(e)):
            handle_connect_failure(ip, e, device_logger, _running_threads, logger, refresh_adb_server)
        logger.error(f"[{ip}] main 執行發生未預期錯誤: {e}", exc_info=True)
        bot_state.update_state(ip, log=f"異常中斷: {e}")
    finally:
        try:
            if d_orig is not None and hasattr(d_orig, "close"):
                d_orig.close()
        except Exception as close_err:
            device_logger.warning(f"[{ip}] device close failed on thread exit: {close_err}")
        # 確保不管發生什麼事，執行緒結束時都會標記離線
        bot_state.set_offline(ip, reason="程式執行結束 (Thread Exit)")




# 全域變數，用來管理運行中的執行緒
# H6: 由 main thread (scan_and_start_devices) 與 device threads
# (handle_connect_failure → refresh_adb_server) 同時讀寫，必須在
# `_running_threads_lock` 保護下操作。鎖實際宣告於
# ``runtime_services.thread_registry`` 以避免循環匯入。
from runtime_services.thread_registry import running_threads_lock as _running_threads_lock  # noqa: E402

_running_threads = {} # {ip: Thread}
_PROCESS_START_TS = time.time()
_STARTUP_SLEEP_SEC_BY_DEVICE = {
    "emulator-5554": 3 * 60,
    "emulator-5556": 3 * 60,
    "emulator-5560": 3 * 60,
}

def temporary_reset_cycles():
    """臨時重置函數：強制將本週設為活動週期的開始"""
    import os
    import json
    from device import get_adb_devices
    
    logger.info("[System] 執行臨時週期重置 (重置週專用)...")
    try:
        devices = get_adb_devices()
        for ip in devices:
            filename = f"{ip}.json"
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 僅清除衝刺紀錄，讓 json_manager 判定這週為衝刺執行週
                keys_to_reset = ["衝刺-發條"]
                for key in keys_to_reset:
                    if key in data:
                        del data[key]
                        logger.info(f"  - [{ip}] 已清除 {key}")
                
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info("[System] 週期重置完成。")
    except Exception as e:
        logger.error(f"[System] 重置失敗：{e}")

if __name__ == "__main__":
    import config_manager
    rotate_existing_logs_once()
    ensure_push_server_started(base_dir=os.path.dirname(os.path.abspath(__file__)))
    import control_panel_app
    import threading
    # 只有 Master 模式才啟動網頁伺服器
    if config_manager.get_global_config().get("mode", "master") == "master":
        server_thread = threading.Thread(target=control_panel_app.run_server, args=(5002,), daemon=True)
        server_thread.start()
    else:
        logger.info("[Info] Worker 模式：不啟動本地網頁伺服器，將回報至 Master。")
        ensure_worker_webhook_started()
        ensure_worker_sync_started()
    # 確保模型在本機 SSD
    from utils.model_sync import ensure_local_model
    local_pth = ensure_local_model("cnn_model.pth")
    Cnn_model = cnn_model.load_cnn_model(local_pth)
    oralce_cnn_model, oralce_classes, resolved_device = load_miner_cnn_model()
    ocr = 1
    logger.info("[System] 核心已就緒，開始循環掃描 ADB 設備... (按 Ctrl+C 可退出)")
    try:
        while True:
            scan_and_start_devices(
                main,
                _running_threads,
                Cnn_model,
                oralce_cnn_model,
                oralce_classes,
                ocr,
                logger,
            )
            for _ in range(300):  # 0.1s * 300 = 30s
                if bot_state.check_refresh_needed():
                    logger.info("[System] 收到立即掃描請求！")
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("\n[System] 收到退出信號，正在關閉所有執行緒...")
        shutdown_web_devices(logger)
        logger.info("[System] 程式已結束。")
