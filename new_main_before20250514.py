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
    is_expired, _should_execute_cycle, should_execute_sea_with_cooldown
)
from miner.models.classifier import ClassifierCNN, load_cnn_model as load_miner_cnn_model
from miner.mining_service import run as run_mining
from miner.rl.rl_recorder import RLRecorder
import shlex
import BUY
from utils.wake_up_handler import handle_device_wakeup, release_wakeup_lock
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR

def main(ip, easyocr_reader: easyocr.Reader, Cnn_model, oralce_cnn_model, oralce_classes, ocr):
    # 為該設備設定獨立的 logger（按 IP 分檔），先建立 logger 以便連線階段可記錄
    device_logger = setup_logger_for_device(ip)
    # 設定當前線程的 logger
    set_thread_logger(device_logger)

    try:
        d = connect_u2_with_retries(ip, logger=device_logger)
    except Exception as e:
        device_logger.error(f"[{ip}] 無法連線到設備: {e}")
        return
    
    wake_up_time = time.time()
    
    # 為每個設備生成隨機的喚醒分鐘偏移 (-3 到 +3 分鐘)
    wake_random_offset = random.randint(0, 6)
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

    try: # Add try block here
        while (1):
            # --- 喚醒與解鎖手機 (移至 utils/wake_up_handler.py) ---
            d = handle_device_wakeup(d, ip, logger, Cnn_model, easyocr_reader)

            start = time.time()
            img = d.screenshot(format='opencv')
            # 進行ocr
            if state_manager.get_state() == "滑動解除節電模式'":
                unlock_screen(d)
            if check_in_game(d) :
                print("in game")
            else:
                print("not in game")
                if 'fc65396d' in ip or '192.168' in ip:
                    
                    time.sleep(1)
                    try:
                        if (d.xpath('//*[@text="菇勇者傳說"]').click()):
                            logger.info(f"[{ip}] 找到遊戲圖示,點擊啟動")
                            time.sleep(2 + random.random())
                            set_screen_for_game(ip, logger=logger)
                        else:
                            raise Exception("未找到遊戲圖示")
                    except:                    
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
                    logger.warning(f"[{ip}] 無法進入可操作狀態，繼續重試")

            # img = d.screenshot(format='opencv')
            # if red_envelope.check_red_in_pic(img):
            # red_envelope.open_red_envelope(d)
            if ip == "emulator-5558":
                switch_skill(d)

            current_time = time.localtime()
            stage = get_stage(d, Cnn_model, easyocr_reader)
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
            if should_execute and current_time.tm_min < 20:
                # goto_hell_gate(d, easyocr_reader)
                new_battle.hell_door(d, ip)
                time_recording(ip, name="地獄之門")
            else:
                logger.info("地獄之門: 尚未到達執行時間或已執行過")
            if '7fe98fc6' in ip and get_stage(d, Cnn_model, easyocr_reader) == "主頁面":
                flush_logs(d)

            # stage = get_stage(d, Cnn_model, easyocr_reader)
            if get_stage(d, Cnn_model, easyocr_reader) == "主頁面":
                save_time = farm_manager.farm(d, ip, Cnn_model)

            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面":
                d.click(random.randint(261, 271), 369)  # 點擊寶箱
                time.sleep(1)
                reward(d, easyocr_reader)
                time.sleep(3)
            # stage = get_stage(d, Cnn_model, easyocr_reader)
            # logger.info(f"目前頁面: {stage}, 開始檢查停車狀態")
            # if stage == "主頁面":
            #     status = manager.check_and_park(protect=True)

            print("確認資格: {}".format(get_stage(d, Cnn_model, easyocr_reader) == "主頁面" or current_time.tm_hour == 23))
            if get_stage(d, Cnn_model, easyocr_reader) == "主頁面" :
                family_manager.go_to_family()

            # stage=get_stage(d,Cnn_model, easyocr_reader)

            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and ip != "emulator-5558":
                guardian_record = return_time(ip, name="guardian_spirit")
                should_get_guardian = True
                if guardian_record is not None:
                    should_get_guardian = guardian_record.get("is_next_day", False)
                if should_get_guardian:
                    get_Guardian_Spirit(d)
                    time_recording(ip, name="guardian_spirit")
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and ip != "emulator-5558":
                get_skill_and_partner(d)
                time.sleep(3)
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" :
                Store.buy_store(d,  Cnn_model)
            
            daily_acceleration(d, ip, Cnn_model)
            
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面":
                click_arena_challenges(d, ip)
            
            stage=get_stage(d,Cnn_model, easyocr_reader)
            print("stage:", stage)
            if get_stage(d, Cnn_model, easyocr_reader) == "主頁面" :
                oracle(d, easyocr_reader, ip=ip, clf=clf, rl_recorder=rl_recorder, Cnn_model=Cnn_model)

                # if _should_perform_oracle_action(ip):
                #     oracle(d, easyocr_reader, ip=ip, clf=clf, rl_recorder=rl_recorder, Cnn_model=Cnn_model)
                #     time.sleep(3)
                #     # 使用新的JSON管理器記錄
                #     store_manager = create_store_manager(ip)
                    
                    # store_manager.record_purchase("挖礦", {"last_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and  (current_time.tm_hour == 20 or current_time.tm_hour == 23 or current_time.tm_hour == 8):
                mission_manager.do_allmission()
                
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面":
                _run_periodic_cycle(
                    ip,
                    record_name="mushroom_arena_cycle_start",
                    should_execute_fn=should_execute_mushroom_arena,
                    action_fn=mushroom_arena,
                    display_name="菇菇武道會",
                    d=d,
                    daily_limit_name="mushroom_arena_daily",
                        )
            
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面":
                _run_periodic_cycle(
                    ip,
                    record_name="sea_last_execution",
                    should_execute_fn=should_execute_sea_with_cooldown,
                    action_fn=sea,
                    display_name="sea",
                    d=d,
                    cycle_record_name="sea_cycle_start",
                )

            stage = get_stage(d, Cnn_model, easyocr_reader)
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
                if should_run_fight_test:
                    new_battle.fight_test(d)
                    time_recording(ip, name="萬神試煉")
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面":
                new_battle.run_weekly_cloud_fighting_single(d,ip)      
            else:
                logger.info("沒有在主頁面，跳過雲端戰鬥")  
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and ip != "emulator-5556":
                daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1)
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and 'fc65396d' in ip :
                Open_gold_paddle_ocr.open_the_gold(d, times=500+random.randint(-50,50),is_compare=True,device_ip=ip)
            #時間
            elif stage == "主頁面" and current_time.tm_hour % 2 == 0 and  ip != "emulator-5558" :
                Open_gold_paddle_ocr.open_the_gold(d, times=500+random.randint(-50,50))
            elif stage == "主頁面" and current_time.tm_hour % 2 == 0 and  ip == "emulator-5562" :
                Open_gold_paddle_ocr.open_the_gold(d, times=60+random.randint(-50,50))

            stage = get_stage(d, Cnn_model, easyocr_reader)

            if stage == "主頁面":
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
            end = time.time()
            if 'fc65396d' in ip or '192.168' in ip:
                reset_screen_settings(ip, logger=logger)
                time.sleep(1)
                try:
                    d.info
                except Exception as e:
                    logger.error(f"重新連線: {e}")
                    try:
                        d = connect_u2_with_retries(ip, logger=device_logger)
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

            sleep_duration = max(0, int(wake_ts - cur_ts))

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


                # 對齊後喚醒時間到了就醒（一般設備 & emulator-5558 都共用 wake_ts）
                if time.time() >= wake_ts:
                    logger.info(f"[{ip}] 已達對齊後喚醒時間，執行喚醒")
                    break

            wake_up_time = time.time()

    except KeyboardInterrupt: # Add except block here
        logger.info(f"[{ip}] 使用者中斷程式執行 (Ctrl+C)")
        if 'fc65396d' in ip or '192.168' in ip:
            try:
                run_adb(
                    'shell wm density reset && wm size reset',
                    device_serial=ip)
            except Exception as e:
                logger.error(f"重設螢幕密度失敗 on {ip}: {e}")  
        
        d.app_stop("com.mxdzz.tw.and") # 確保遊戲關閉
        d.press("back")  # 按下返回鍵
        #點擊退出遊戲
        click_str("退出", d, easyocr_reader)
        # 可以選擇在這裡執行其他清理操作
        # 確保所有 handler 被 flush 並關閉
        try:
            logging.shutdown()
        except Exception:
            pass
        return # 結束此線程的 main 函數


easyocr_reader = easyocr.Reader(['ch_tra', 'en'])
if __name__ == "__main__":
    # d = u2.connect('emulator-5560')
    #檢測devices
    ocr = 1
    d_list = get_adb_devices()
    time.sleep(1)
    d_list = get_adb_devices()

    #不要5560
    d_list = [d for d in d_list if d != 'emulator-5562' ]
    print("devices:", d_list)
    # d_list = ['emulator-5562',  'emulator-5558',  'emulator-5556','3a8d31f2','fc65396d']
    # d_list = ['emulator-5560']
    Cnn_model = cnn_model.load_cnn_model("cnn_model.pth")
    oralce_cnn_model, oralce_classes, resolved_device = load_miner_cnn_model()
    # main("fc65396d", easyocr_reader,Cnn_model,oralce_cnn_model)
    import threading
    threads = []
    for ip in d_list:
        threads.append(threading.Thread(target=main, args=(
            ip, easyocr_reader, Cnn_model, oralce_cnn_model, oralce_classes, ocr)))
    for ip in threads:
        ip.start()
    for ip in threads:
        ip.join()