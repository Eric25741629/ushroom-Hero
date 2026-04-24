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
from farm import farm_manager
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
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR
import config_manager

import bot_state
from device_wrapper import MonitoredDevice
from worker_webhook_api import ensure_worker_webhook_started
from runtime_services.device_scan_service import (
    refresh_adb_server,
    scan_and_start_devices,
)
from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    WakeLoopInterrupted,
    handle_connect_failure,
    is_emulator_serial,
    is_recoverable_connect_error,
    reset_connect_failure,
)
from runtime_services.push_server_service import ensure_push_server_started
from runtime_services.worker_sync_service import ensure_worker_sync_started
from runtime_services.web_session_service import (
    LOGIN_CONFLICT_SLEEP_SEC,
    handle_pending_web_launch,
    initialize_runtime_device,
    process_online_check_requests,
    shutdown_web_devices,
)
from utils.screenshot_helpers import save_error_screenshot, log_main_page_mismatch
from game_actions.lamp_scheduler import _run_lamp_if_due
from game_actions.stage_guard import (
    LoginConflictError,
    get_stage_with_check,
    _run_at_main_page,
)
from game_actions.dungeon_scheduler import _run_weekly_dungeon, _run_biweekly_dungeon
from runtime_services.sleep_service import (
    StartupBypassError,
    _maybe_resume_sleep,
    run_sleep_cycle,
    stop_runtime_device_for_sleep,
)
from runtime_services.startup_sleep import _handle_startup_sleep
from game_actions.daily_pipeline import DailyContext, run as run_daily_pipeline


atexit.register(lambda: shutdown_web_devices(logger))


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

                ctx = DailyContext(
                    d=d,
                    ip=ip,
                    Cnn_model=Cnn_model,
                    clf=clf,
                    rl_recorder=rl_recorder,
                    current_time=time.localtime(),
                    enable_dungeon_manager=enable_dungeon_manager,
                    wheel_manager=wheel_manager,
                    mission_manager=mission_manager,
                    family_manager=family_manager,
                )
                run_daily_pipeline(ctx)
            except ForceSleepRequested as e:
                force_sleep_now = True
                sleep_policy = "force_sleep"
                sleep_reason = "強制休眠"
                logger.warning(f"[{ip}] 收到強制休眠請求，終止當前任務並進入休眠: {e}")
                stop_runtime_device_for_sleep(d, ip, backend_kind, logger)

            except WakeLoopInterrupted as e:
                # Dashboard requested web-launch (or similar) while we were
                # blocked inside the wake-up loop. Do NOT put the device to
                # sleep — just jump back to the top of the loop so
                # handle_pending_web_launch fires on the next iteration.
                logger.info(
                    f"[{ip}] 喚醒流程被 dashboard 打斷，回主迴圈頂端重新評估: {e}"
                )
                continue

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
_running_threads = {} # {ip: Thread}

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
