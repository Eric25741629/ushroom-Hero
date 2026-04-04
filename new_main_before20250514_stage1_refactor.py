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
    set_screen_for_game, reset_screen_settings
)
import daily_gift_task
import easyocr
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
import Open_gold_paddle_ocr
import threading
from fight_car import flush_logs

from utils.logging_utils import setup_logger_for_device, set_thread_logger, logger, default_logger
from game_actions.skill_manager import switch_skill
from game_actions.reward_manager import reward
from utils.ocr_clicker import click_str
from game_state.detector import get_stage
from game_actions.miner_action import oracle, _should_perform_oracle_action
from game_actions.periodic_tasks import _run_periodic_cycle, should_execute_mushroom_arena, mushroom_arena
from game_initialization import check_on_line, handle_game_startup_pages
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
import BUY
from utils.wake_up_handler import handle_device_wakeup, release_wakeup_lock
from utils.car_fight_utils import adjust_wake_time_for_cars

import bot_state
from device_wrapper import MonitoredDevice

class LoginConflictError(Exception):
    """自定義異常：用於處理異地登錄並終止當前喚醒 session"""
    pass

def get_stage_with_check(d, ip, Cnn_model, easyocr_reader):
    """
    包裝 get_stage，增加全域異地登錄檢查。
    如果發現異地登錄，則拋出 LoginConflictError。
    """
    stage = get_stage(d, Cnn_model, easyocr_reader)
    if stage == "異地登錄":
        logger.warning(f"[{ip}] 全域偵測到異地登錄，強制停止遊戲")
        d.app_stop("com.mxdzz.tw.and")
        
        # 設定下次喚醒時間為 1 小時後
        wake_ts = time.time() + 3600
        wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
        bot_state.update_state(ip, task="休眠中", step=f"偵測到異地登錄 (預計 {wake_time_str} 喚醒)", next_wake_at=wake_ts)
        
        # 拋出異常，讓主迴圈捕獲並跳到休眠階段
        raise LoginConflictError("偵測到異地登錄")

    if stage == "前往活動":
        logger.info(f"[{ip}] 偵測到『前往活動』彈窗，點擊空白位置關閉")
        click_white(d)
        time.sleep(1)
        
    return stage

def main(ip, easyocr_reader: easyocr.Reader, Cnn_model, oralce_cnn_model, oralce_classes, ocr):
    # 初始化狀態監控
    bot_state.init_device(ip)
    
    try:
        # 為該設備設定獨立的 logger（按 IP 分檔），先建立 logger 以便連線階段可記錄
        device_logger = setup_logger_for_device(ip)
        # 設定當前線程的 logger
        set_thread_logger(device_logger)

        try:
            d_orig = connect_u2_with_retries(ip, logger=device_logger)
            # 偷天換日：包裝設備物件，之後所有的模組調用的 d 都是被監控的
            d = MonitoredDevice(d_orig, ip)
        except Exception as e:
            device_logger.error(f"[{ip}] 無法連線到設備: {e}")
            bot_state.set_offline(ip, reason=f"連線失敗: {e}")
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
        clf = ClassifierCNN(model=oralce_cnn_model, classes=oralce_classes, dataset_root="dataset/low_confidence")

        # 建立 RL 記錄器（記錄但不自動訓練）
        rl_logs_dir = os.path.join("miner", "rl_logs", ip.replace(":", "_"))
        os.makedirs(rl_logs_dir, exist_ok=True)
        rl_recorder = RLRecorder(
            log_dir=rl_logs_dir,
            auto_train=False,  # 不自動訓練
            flush_interval=1,
        )

        while (1):
            try:
                # --- 喚醒與解鎖手機 ---
                bot_state.update_state(ip, task="喚醒檢查", step="正在檢查螢幕狀態")
                d = handle_device_wakeup(d, ip, logger, Cnn_model, easyocr_reader)

                start = time.time()
                img = d.screenshot(format='opencv')
                # 進行ocr
                if state_manager.get_state() == "滑動解除節電模式'":
                    unlock_screen(d)
                if check_in_game(d) :
                    print("in game")
                    # 即使在遊戲中，也要檢查是否有「放置獎勵」或「領取」彈窗阻擋
                    stage_check = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                    if stage_check in ["放置獎勵", "離線獎勵", "領取"]:
                        logger.info(f"[{ip}] 偵測到 {stage_check} 彈窗，執行自動領取...")
                        reward(d, easyocr_reader)
                        time.sleep(2)
                else:
                    print("not in game")
                    bot_state.update_state(ip, task="啟動遊戲", step="正在啟動 APP")
                    if 'fc65396d' in ip or '192.168' in ip:
                        
                        time.sleep(1)
                        try:
                            if (d.xpath('//*[@text="菇勇者傳說"]').click()):
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
                        easyocr_reader=easyocr_reader,
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
                        
                        # 拋出 LoginConflictError 跳到休眠階段
                        raise LoginConflictError("啟動失敗避讓")
                                    # img = d.screenshot(format='opencv')
                # if red_envelope.check_red_in_pic(img):
                # red_envelope.open_red_envelope(d)
                if ip == "emulator-5558":
                    switch_skill(d)

                current_time = time.localtime()
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                record_time = return_time(ip, name="地獄之門")
                logging.info("目前頁面: {}, 當前時間: {}:{}".format(stage, current_time.tm_hour, current_time.tm_min))
                logging.info("地獄之門紀錄: {}".format(record_time))
                hell_gate_time = 1
                if record_time is None :
                    #  "萬神試煉" 的記錄不存在  
                    hell_gate_time = 0
                    should_execute = True
                else:
                    should_execute = record_time.get("is_next_day", False) or hell_gate_time == 0
                    logging.info("hell_gate_time: {}, should_execute: {}, record_time: {}".format(hell_gate_time, should_execute, record_time))
                if should_execute and current_time.tm_min < 20 and stage == "主頁面":
                    bot_state.update_state(ip, task="地獄之門", step="戰鬥執行中")
                    # goto_hell_gate(d, easyocr_reader)
                    new_battle.hell_door(d, ip)
                    time_recording(ip, name="地獄之門")
                elif should_execute and current_time.tm_min < 20:
                    logger.info("地獄之門: 到達執行時間但不在主頁面，跳過本次循環")
                else:
                    logger.info("地獄之門: 尚未到達執行時間或已執行過")
                if '7fe98fc6' in ip and get_stage_with_check(d, ip, Cnn_model, easyocr_reader) == "主頁面":
                    flush_logs(d)

                # stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if get_stage_with_check(d, ip, Cnn_model, easyocr_reader) == "主頁面":
                    bot_state.update_state(ip, task="農場任務", step="準備進入")
                    save_time = farm_manager.farm(d, ip, Cnn_model)

                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面":
                    bot_state.update_state(ip, task="點擊寶箱", step="領取獎勵")
                    d.click(random.randint(261, 271), 369)  # 點擊寶箱
                    time.sleep(1)
                    reward(d, easyocr_reader)
                    time.sleep(3)
                # stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                # logger.info(f"目前頁面: {stage}, 開始檢查停車狀態")
                # if stage == "主頁面":
                #     status = manager.check_and_park(protect=True)

                print("確認資格: {}".format(get_stage_with_check(d, ip, Cnn_model, easyocr_reader) == "主頁面" or current_time.tm_hour == 23))
                if get_stage_with_check(d, ip, Cnn_model, easyocr_reader) == "主頁面" :
                    bot_state.update_state(ip, task="家族任務", step="執行中")
                    family_manager.go_to_family()

                # stage=get_stage(d,Cnn_model, easyocr_reader)

                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面" and ip != "emulator-5558":
                    guardian_record = return_time(ip, name="guardian_spirit")
                    should_get_guardian = True
                    if guardian_record is not None:
                        should_get_guardian = guardian_record.get("is_next_day", False)
                    if should_get_guardian:
                        bot_state.update_state(ip, task="領取守護靈", step="領取中")
                        get_Guardian_Spirit(d)
                        time_recording(ip, name="guardian_spirit")
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面" and ip != "emulator-5558":
                    bot_state.update_state(ip, task="抽技能夥伴", step="領取中")
                    get_skill_and_partner(d)
                    time.sleep(3)
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面":
                    store_record = return_time(ip, name="Store")
                    # 每 3 小時檢查一次 (10800秒) 或 23點強制檢查
                    should_check_store = is_record_expired(store_record, 10800) or current_time.tm_hour == 23
                    
                    if should_check_store:
                        bot_state.update_state(ip, task="商店購買", step="執行中")
                        Store.buy_store(d, Cnn_model)
                        time_recording(ip, name="Store")
                    else:
                        logger.info("商店購買: 尚未過期且非23點，跳過")

                # --- 新增：週活動 - 坐騎強化 (衝刺-發條) ---
                if get_stage_with_check(d, ip, Cnn_model, easyocr_reader) == "主頁面":
                    rank_events.park_spring(d, ip)
                
                bot_state.update_state(ip, task="每日加速", step="領取中")
                daily_acceleration(d, ip, Cnn_model)
                
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面":
                    bot_state.update_state(ip, task="競技場挑戰", step="領取中")
                    click_arena_challenges(d, ip)
                
                stage=get_stage(d,Cnn_model, easyocr_reader)
                print("stage:", stage)
                if get_stage_with_check(d, ip, Cnn_model, easyocr_reader) == "主頁面" :
                    bot_state.update_state(ip, task="挖礦/Oracle", step="執行中", log="開始執行挖礦任務")
                    oracle(d, easyocr_reader, ip=ip, clf=clf, rl_recorder=rl_recorder, Cnn_model=Cnn_model)

                    # if _should_perform_oracle_action(ip):
                    #     oracle(d, easyocr_reader, ip=ip, clf=clf, rl_recorder=rl_recorder, Cnn_model=Cnn_model)
                    #     time.sleep(3)
                    #     # 使用新的JSON管理器記錄
                    #     store_manager = create_store_manager(ip)
                        
                        # store_manager.record_purchase("挖礦", {"last_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面" and (20 <= current_time.tm_hour < 23):
                    # 直接呼叫 do_allmission，它內部會透過 check() 檢查 mission_timestamp
                    bot_state.update_state(ip, task="所有日常任務", step="檢查/執行中")
                    mission_manager.do_allmission()
                    
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面":
                    bot_state.update_state(ip, task="菇菇武道會", step="週期檢查/執行")
                    _run_periodic_cycle(
                        ip,
                        record_name="mushroom_arena_cycle_start",
                        should_execute_fn=should_execute_mushroom_arena,
                        action_fn=mushroom_arena,
                        display_name="菇菇武道會",
                        d=d,
                        daily_limit_name="mushroom_arena_daily",
                            )
                
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面":
                    bot_state.update_state(ip, task="航海任務 (Sea)", step="週期檢查/執行")
                    _run_periodic_cycle(
                        ip,
                        record_name="sea_last_execution",
                        should_execute_fn=should_execute_sea_with_cooldown,
                        action_fn=sea,
                        display_name="sea",
                        d=d,
                        cycle_record_name="sea_cycle_start",
                    )

                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                record_time = return_time(ip, name="萬神試煉")
                is_not_sunday = False
                is_monday_afternoon = False
                is_after_monday = False
                logging.info("目前頁面: {}, 當前時間: {}:{}".format(stage, current_time.tm_hour, current_time.tm_min))
                logging.info("萬神試煉紀錄: {}".format(record_time))
                fight_trial_time = 1
                if record_time is None :
                    #  "萬神試煉" 的記錄不存在  
                     
                    fight_trial_time = 0
                    should_execute = True
                else:
                    # is_same_week=True 代表本週已執行，應跳過；False 則本週尚未執行
                    should_execute = record_time.get("is_next_week", False) or fight_trial_time == 0
                    logging.info("fight_trial_time: {}, should_execute: {}, record_time: {}".format(fight_trial_time, should_execute, record_time))
                # 僅在星期一下午（大於12點）或星期二到六的任何時間執行，星期日永不執行
                if should_execute and (
                    (current_time.tm_wday == 0 and current_time.tm_hour > 12) or
                    (1 <= current_time.tm_wday <= 5)
                ):
                    is_not_sunday = current_time.tm_wday != 6  # 不是星期日
                    is_monday_afternoon = current_time.tm_wday == 0 and current_time.tm_hour > 12  # 星期一下午
                    is_after_monday = current_time.tm_wday > 0  # 星期二到星期六
                    should_run_fight_test = should_execute and is_not_sunday and (is_monday_afternoon or is_after_monday)
                    if should_run_fight_test and stage == "主頁面":
                        bot_state.update_state(ip, task="萬神試煉", step="執行中")
                        new_battle.fight_test(d)
                        time_recording(ip, name="萬神試煉")
                    elif should_run_fight_test:
                        logger.info("萬神試煉: 到達執行時間但不在主頁面，跳過本次循環")

                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面":
                    bot_state.update_state(ip, task="雲端戰鬥", step="領取中")
                    new_battle.run_weekly_cloud_fighting_single(d,ip)      
                else:
                    logger.info("沒有在主頁面，跳過雲端戰鬥")  
                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                if stage == "主頁面" and ip != "emulator-5556":
                    bot_state.update_state(ip, task="好友每日禮物", step="領取中")
                    daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1)
                    stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)
                    if stage == "主頁面" and 'fc65396d' in ip :
                        lamp_dur = config_manager.get_device_config(ip).get("lamp_duration_sec", 300)
                        bot_state.update_state(ip, task="點金 (OCR)", step=f"執行中 ({lamp_dur}s)")
                        Open_gold_paddle_ocr.open_the_gold(d, times=lamp_dur+random.randint(-10,10),is_compare=True,device_ip=ip)
                    
                    # --- 動態開神燈/點金邏輯 ---
                    elif stage == "主頁面" and ip != "emulator-5558":
                        device_cfg = config_manager.get_device_config(ip)
                        lamp_interval = device_cfg.get("lamp_check_interval", 2)
                        lamp_dur = device_cfg.get("lamp_duration_sec", 300)
                        
                        if current_time.tm_hour % lamp_interval == 0:
                            bot_state.update_state(ip, task="點金", step=f"執行中 ({lamp_dur}s)")
                            Open_gold_paddle_ocr.open_the_gold(d, times=lamp_dur+random.randint(-10,10))
                        
                    elif stage == "主頁面" and current_time.tm_hour % 2 == 0 and  ip == "emulator-5562" :
                        lamp_dur = config_manager.get_device_config(ip).get("lamp_duration_sec", 300)
                        bot_state.update_state(ip, task="點金", step=f"執行中 ({lamp_dur}s)")
                        Open_gold_paddle_ocr.open_the_gold(d, times=lamp_dur+random.randint(-10,10))
                            

                stage = get_stage_with_check(d, ip, Cnn_model, easyocr_reader)

                if stage == "主頁面":
                    bot_state.update_state(ip, task="轉盤金幣", step="執行中")
                    wheel_manager.spin_and_send_gold()

                if ip == "emulator-5558":
                    switch_skill(d,'騙人用')
                if "fc65396d" in ip:
                    #打開chrome 
                    d.app_start("com.android.chrome")
                    time.sleep(2)
                    d.app_stop("com.mxdzz.tw.and")
                    time.sleep(1)
                else:
                    d.app_stop("com.mxdzz.tw.and")
                
            except LoginConflictError as e:
                logger.warning(f"[{ip}] 異地登錄中斷本次執行: {e}")
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
                        logger.error(f"[{ip}] 重連失敗: {e2}")
                open_nofication(d)
                d.screen_off()
            release_wakeup_lock(ip)
            last_wake_time = time.time()
            cur_ts = last_wake_time

            def calc_aligned_wake_ts(cur_ts: float, min_sleep_sec: int, win_min: int = 20) -> float:
                """
                至少睡 min_sleep_sec 秒後，把喚醒時間對齊到每小時 00~win_min 分
                """
                earliest = cur_ts + min_sleep_sec
                hour_floor = earliest - (earliest % 3600)
                win_end = hour_floor + win_min * 60

                if earliest <= win_end:
                    return float(random.randint(int(earliest), int(win_end)))
                else:
                    next_hour = hour_floor + 3600
                    return float(next_hour + random.randint(0, win_min * 60))

            # 分開設定兩種設備的「最少休眠」
            if 'emulator-5558' in ip:
                min_sleep_sec = int(random.uniform(1, 3) * 3600)  # emulator-5558：1~3 小時
            else:
                min_sleep_sec = (60 + random.randint(-5, 5)) * 60  # 一般設備：60±5 分

            # 新增規則：若為 7fe98fc6，強制每小時喚醒一次
            if '7fe98fc6' in ip:
                # 每小時喚醒一次，加入少許抖動（0~30秒）以避免與其他裝置完全同步
                wake_ts = cur_ts + 3600 + random.randint(0, 30)
            else:
                wake_ts = calc_aligned_wake_ts(cur_ts, min_sleep_sec, win_min=20)

            # --- 新增：根據車位戰鬥調整時間 ---
            wake_ts = adjust_wake_time_for_cars(wake_ts)

            sleep_duration = max(0, int(wake_ts - cur_ts))
            wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
            bot_state.update_state(ip, task="休眠中", step=f"預計休眠 {sleep_duration/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)", next_wake_at=wake_ts)

            if '7fe98fc6' in ip:
                logger.info(f"[{ip}] 裝置為 7fe98fc6，設定為每小時喚醒一次，預計休眠 {sleep_duration/60:.1f} 分鐘")
            else:
                logger.info(f"[{ip}] 本次喚醒將落在每小時 00~20 分，預計休眠 {sleep_duration/60:.1f} 分鐘")

            while True:
                protect = False
                current_time = time.localtime()
                if current_time.tm_hour <= 1 or current_time.tm_hour >= 6:
                    protect = True
                if ('emulator-5558' in ip or 'emulator-5562' in ip or '7fe98fc6' in ip or 'fc65396d' in ip):
                    protect = False

                time.sleep(30)
                # 這裡也要檢查暫停，雖然在休眠，但可能 UI 會想在它醒來前做點事
                if bot_state.check_pause(ip):
                    logger.info(f"[{ip}] 偵測到從暫停中恢復，立即重啟初始化流程")
                    break # 跳出休眠迴圈，回到 while(1) 最上方執行 handle_device_wakeup
                
                # 檢查跳過休眠
                if bot_state.check_skip_sleep(ip):
                    logger.info(f"[{ip}] 收到跳過休眠指令，立即喚醒")
                    break

                # 對齊後喚醒時間到了就醒（一般設備 & emulator-5558 都共用 wake_ts）
                if time.time() >= wake_ts:
                    logger.info(f"[{ip}] 已達對齊後喚醒時間，執行喚醒")
                    break

            wake_up_time = time.time()

    except Exception as e:
        logger.error(f"[{ip}] main 執行發生未預期錯誤: {e}", exc_info=True)
        bot_state.update_state(ip, log=f"異常中斷: {e}")
    finally:
        # 確保不管發生什麼事，執行緒結束時都會標記離線
        bot_state.set_offline(ip, reason="程式執行結束 (Thread Exit)")



easyocr_reader = easyocr.Reader(['ch_tra', 'en'])

# 全域變數，用來管理運行中的執行緒
_running_threads = {} # {ip: Thread}

def scan_and_start_devices(Cnn_model, oralce_cnn_model, oralce_classes, ocr):
    """掃描 ADB 設備並啟動新執行緒"""
    global _running_threads
    
    # 1. 獲取當前 ADB 列表
    try:
        current_devices = get_adb_devices()
        # 過濾掉不需要的設備 (例如 5562)
        current_devices = [d for d in current_devices if d != 'emulator-5562']
    except Exception as e:
        print(f"[System] 掃描 ADB 失敗: {e}")
        return

    # 2. 清理已結束的執行緒
    stopped_ips = []
    for ip, thread in _running_threads.items():
        if not thread.is_alive():
            print(f"[System] 設備 {ip} 的執行緒已結束")
            stopped_ips.append(ip)
    
    for ip in stopped_ips:
        del _running_threads[ip]

    # 3. 啟動新設備
    for ip in current_devices:
        # 如果設備不在運行中，或者雖然在字典裡但執行緒已死
        should_start = False
        if ip not in _running_threads:
            should_start = True
        elif not _running_threads[ip].is_alive():
            should_start = True
            
        if should_start:
            print(f"[System] 發現新設備或重連設備: {ip}，正在啟動掛機執行緒...")
            t = threading.Thread(target=main, args=(
                ip, easyocr_reader, Cnn_model, oralce_cnn_model, oralce_classes, ocr
            ), name=f"Bot-{ip}", daemon=True)
            t.start()
            _running_threads[ip] = t

def temporary_reset_cycles():
    """臨時重置函數：強制將本週設為活動週期的開始"""
    import os
    import json
    from device import get_adb_devices
    
    print("[System] 執行臨時週期重置 (重置週專用)...")
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
                        print(f"  - [{ip}] 已清除 {key}")
                
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
        print("[System] 週期重置完成。")
    except Exception as e:
        print(f"[System] 重置失敗: {e}")

if __name__ == "__main__":
    import config_manager
    import control_panel_app
    import threading

    # 執行臨時重置 (如果是重置週才開啟)
    # temporary_reset_cycles()

    # 只有 Master 模式才啟動網頁伺服器
    if config_manager.get_global_config().get("mode", "master") == "master":
        server_thread = threading.Thread(target=control_panel_app.run_server, args=(5002,), daemon=True)
        server_thread.start()
    else:
        print("[Info] Worker 模式：不啟動本地網頁伺服器，將回報至 Master。")

    # --- 方案三：確保模型在本機 SSD (優化 NAS 效率) ---
    from utils.model_sync import ensure_local_model
    local_pth = ensure_local_model("cnn_model.pth")
    Cnn_model = cnn_model.load_cnn_model(local_pth)
    
    oralce_cnn_model, oralce_classes, resolved_device = load_miner_cnn_model()
    ocr = 1

    print("[System] 核心已就緒，開始循環掃描 ADB 設備... (按 Ctrl+C 可退出)")
    try:
        while True:
            # 立即執行一次掃描
            scan_and_start_devices(Cnn_model, oralce_cnn_model, oralce_classes, ocr)
            
            # 等待 30 秒，期間檢查是否有強制的刷新請求
            for _ in range(300): # 0.1s * 300 = 30s
                if bot_state.check_refresh_needed():
                    print("[System] 收到立即掃描請求！")
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[System] 收到退出信號，正在關閉所有執行緒...")
        # 由於子執行緒已設為 daemon，這裡直接退出即可
        print("[System] 程式已結束。")
    
    print("[System] 系統啟動，開始動態監控設備...")
    
    # 主監控迴圈
    try:
        while True:
            # 檢查是否有手動觸發刷新或每 60 秒自動刷新一次
            if bot_state.check_refresh_needed():
                print("[System] 收到刷新請求，立刻掃描設備...")
                scan_and_start_devices(Cnn_model, oralce_cnn_model, oralce_classes, ocr)
            
            # 定期掃描 (可以把頻率降低，節省 CPU)
            scan_and_start_devices(Cnn_model, oralce_cnn_model, oralce_classes, ocr)
            time.sleep(30)
    except KeyboardInterrupt:
        print("[System] 收到中斷信號，正在停止所有執行緒...")
        # 這裡可以加入 graceful shutdown 邏輯