import subprocess
from token import OP
from Sea import sea
from requests import get
import miner
import torch
import os
from adb_devices import *
import datetime
import json
import miner.simplecnn
from adb_operations import (
    run_adb, connect_u2_with_retries, unlock_screen,
    start_game_by_icon, check_in_game, click_random,
    safe_click, ensure_screen_on, stop_app,
    screenshot_opencv, screenshot_pillow
)
# from oralce_manger import oralce
import daily_gift_task
import easyocr
import point
import uiautomator2 as u2
from everyday_mission.Guardian_Spirit_manger import get_Guardian_Spirit
import time
import numpy as np
from device import get_adb_devices,close_nofication,open_nofication
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
# 建立 logs 資料夾
if not os.path.exists("logs"):
    os.makedirs("logs")

# 執行緒鎖，確保 logger 初始化的執行緒安全
_logger_lock = threading.Lock()

def setup_logger_for_device(device_id: str) -> logging.Logger:
    """為指定的設備建立獨立 logger，按 IP 分檔並加上 [IP] 標籤。"""
    with _logger_lock:
        logger_name = f"logger_{device_id}"
        logger = logging.getLogger(logger_name)
        
        # 清除舊的 handler（避免重複或混淆）
        logger.handlers = []
        logger.propagate = False
        
        logger.setLevel(logging.INFO)
        
        # 檔案 handler：各設備獨立檔案
        log_file = f"logs/{device_id}.log"
        file_handler = FlushFileHandler(log_file, encoding='utf-8', mode='a')
        file_handler.setLevel(logging.INFO)
        
        # 格式：包含 [IP] 標籤
        formatter = logging.Formatter(
            f'%(asctime)s - %(levelname)s - [{device_id}] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # 也加入控制台 handler（可選）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger


class FlushFileHandler(logging.FileHandler):
    """自動 flush 的 FileHandler，確保每條 log 即時寫入。"""
    def emit(self, record):
        log_dir = os.path.dirname(self.baseFilename)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        for attempt in range(2):
            try:
                super().emit(record)
                break
            except FileNotFoundError:
                if attempt == 0:
                    self.stream = self._open()
                else:
                    raise

        try:
            # 確保 buffer flush
            self.flush()
            # 強制寫入磁碟，減少程式崩潰或非正常結束時遺失日誌的機率
            try:
                if hasattr(self.stream, "fileno"):
                    os.fsync(self.stream.fileno())
            except Exception:
                # 若底層無法 fsync（例如虛擬化環境），忽略但不阻斷日誌流程
                pass
        except FileNotFoundError:
            pass

# 預設 logger（用於主執行緒或不帶 IP 的日誌）
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
default_logger = logging.getLogger(__name__)

# 使用 threading.local() 為每個線程維護獨立的 logger
_thread_local = threading.local()

def get_thread_logger():
    """獲取當前線程的 logger，如果未設定則返回預設 logger"""
    return getattr(_thread_local, 'logger', default_logger)

def set_thread_logger(logger_instance):
    """為當前線程設定專屬 logger"""
    _thread_local.logger = logger_instance

# 為了向後兼容，使用屬性訪問
class LoggerProxy:
    def __getattr__(self, name):
        return getattr(get_thread_logger(), name)

logger = LoggerProxy()
# 在程式結束時強制關閉 logging handlers，確保所有日誌已 flush 並關閉
atexit.register(logging.shutdown)
import  new_cnn.cnn_model as cnn_model
# 導入新的JSON管理器，保持向後兼容
from json_manager import time_recording, return_time, create_store_manager
global lock
lock = False
ADB = "adb"
    # ...已移至 img_tools.py，請使用 img_tools.check_red_dot ...
from miner.Mining import ClassifierCNN
from miner.Mining import run as run_mining
from miner.Mining import load_cnn_model as load_miner_cnn_model
from miner.rl_recorder import RLRecorder
import shlex
import BUY

def switch_skill(d,skill_name='閃避推圖'):
    img_tools.click_str_by_server(d,'方案')
    img_tools.click_str_by_server(d,'冒險行裝',shift_y=80)
    img_tools.click_str_by_server(d,skill_name)
    time.sleep(2)
    img_tools.click_str_by_server(d,'切換方案')
    d.click(275,870)
    time.sleep(2)

# def _update_store_record_extra(manager, title, extra_fields):
#     """輔助更新商店記錄，不改動時間戳與 schema。"""
#     data = manager.load_data()
#     record = data.get(title, {})
#     record.update(extra_fields)
#     data[title] = record
#     manager.save_data(data)
# def buy_arean_everyday(d, ip: str) -> bool:
#     """每日競技場購買：已購買則跳過，並記錄檢查/購買次數。"""
#     store_manager = create_store_manager(ip)
#     today_str = datetime.datetime.now(TPE).strftime("%Y-%m-%d")
#     record = store_manager.get_purchase_record("競技場") or {}

#     # 每次呼叫都更新檢查次數（跨日重置）
#     last_check_date = record.get("last_check_date")
#     check_times = int(record.get("check_times", 0))
#     if last_check_date != today_str:
#         check_times = 0
#     check_times += 1
#     _update_store_record_extra(
#         store_manager,
#         "競技場",
#         {"check_times": check_times, "last_check_date": today_str},
#     )

#     if store_manager.is_purchased_today("競技場"):
#         logger.info(f"[{ip}] 今天已完成競技場購買，檢查 {check_times} 次，跳過。")
#         return False

#     logger.info(f"[{ip}] 嘗試執行競技場每日購買（今日第 {check_times} 次檢查）。")
#     try:
#         img_tools.click_str_by_server(d, '競技場', shift_y=-20)
#         time.sleep(2)
#         img_tools.click_str_by_server(d, '跨服')
#         time.sleep(2)
#         img_tools.click_str_by_server(d, '商城')
#         want_items = ['覺醒卷軸']
#         BUY.buy_items(d, want_items)
#         d.click(31, 918)
#         time.sleep(0.5)
#         d.click(491, 909)
#         time.sleep(2)
#     except Exception as exc:
#         logger.error(f"[{ip}] 競技場購買流程失敗: {exc}")
#         return False

#     purchase_count = int(record.get("count", 0)) + 1
#     store_manager.record_purchase(
#         "競技場",
#         {
#             "count": purchase_count,
#             "check_times": check_times,
#             "last_check_date": today_str,
#         },
#     )
#     logger.info(f"[{ip}] 競技場購買完成，累計 {purchase_count} 次，今日檢查 {check_times} 次。")
#     return True
# def fight_arean_everyday(d, ip: str) -> bool:
#     store_manager = create_store_manager(ip)
#     today_str = datetime.datetime.now(TPE).strftime("%Y-%m-%d")
#     record = store_manager.get_purchase_record("競技場_打架") or {}

#     # 每次呼叫都更新檢查次數（跨日重置）
#     last_check_date = record.get("last_check_date")
#     check_times = int(record.get("check_times", 0))
#     if last_check_date != today_str:
#         check_times = 0
#     check_times += 1
#     _update_store_record_extra(
#         store_manager,
#         "競技場",
#         {"check_times": check_times, "last_check_date": today_str},
#     )

#     if store_manager.is_purchased_today("競技場_打架"):
#         logger.info(f"[{ip}] 今天已完成競技場_打架，檢查 {check_times} 次，跳過。")
#         return False

#     logger.info(f"[{ip}] 嘗試執行競技場每日購買（今日第 {check_times} 次檢查）。")
#     try:
#         img_tools.click_str_by_server(d, '競技場', shift_y=-20)
#         time.sleep(2)
#         img_tools.click_str_by_server(d,'挑戰')
#         time.sleep(2)
#         for i in range(5):
#             img_tools.click_str_by_server(d,'挑戰',)#從下面的挑戰開始點 
#             time.sleep(1)
            
#     except Exception as exc:
#         logger.error(f"[{ip}] 競技場_打架流程失敗: {exc}")
#         return False
# #競技場購買
# def buy_arean_everyweek(d):
#     img_tools.click_str_by_server(d, '競技場',shift_y=-20)
#     time.sleep(2)
#     img_tools.click_str_by_server(d,'跨服')
#     time.sleep(2)
#     img_tools.click_str_by_server(d,'商城')
#     want_items = ['覺醒水晶']
#     BUY.buy_items(d, want_items)



# def get_Martial_Soul(d: u2.Device):
#     """
#     Checks for and handles the Martial Soul notification and subsequent actions.
#     """
#     logger.info("檢查武魂紅點...")
#     # ROI for the first red dot check
#     roi1 = (472, 590, 455, 507)
#     if img_tools.check_red_dot(d, roi1):
#         logger.info("偵測到武魂紅點，開始處理...")
#         d.click(495+random.randint(-5, 5), 582+random.randint(-5, 5))
#         time.sleep(2)
#         # ROI for the second red dot check
#         roi2 = (760, 791, 483, 525)
#         if img_tools.check_red_dot(d, roi2):
#                 logger.info("偵測到第二個紅點，執行升級流程...")
#                 d.click(451+random.randint(-5, 5), 792+random.randint(-5, 5))
#                 time.sleep(2+random.random())
#                 d.click(289+random.randint(-5, 5), 272+random.randint(-5, 5))
#                 time.sleep(1.5+random.random())
#                 d.click(474+random.randint(-5, 5), 254+random.randint(-5, 5))
#                 time.sleep(1.5+random.random())
#                 d.click(55+random.randint(-5, 5), 48+random.randint(-5, 5))
#                 time.sleep(1.5+random.random())
#                 d.click(486+random.randint(-5, 5), 908+random.randint(-5, 5))
#                 time.sleep(2+random.random())

#         logger.info("關閉武魂頁面...")
#         d.click(271, 883) # Close button
#         time.sleep(2)
#     else:
#         logger.info("未發現武魂紅點。")


def reward(d, easyocr_reader):
    d.click(162, 725)
    time.sleep(5)
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img, detail=0)
    if "領取" in result or "放置獎勵" in result:
        logger.info(img[328, 135])
        if abs(np.sum(img[328, 135])-np.sum([206, 237, 247])) > 12:
            if not os.path.exists("reward_get"):
                os.makedirs("reward_get")
            # cv2.imwrite(
            #     "reward_get/reward_get_{}.jpg".format(time.time()), img)
            click_white(d)
            time.sleep(1)
        d.click(330, 725)
        time.sleep(5)
        click_white(d)
        time.sleep(1)


    # ...已移至 img_tools.py，請使用 img_tools.save_stage_debug_image ...

def click_str(str1: str, d, easyocr_reader):
    img = d.screenshot(format='opencv')
    # if not os.path.exists("other_str"):
    #     os.makedirs("other_str")
    # cv2.imwrite("other_str/other_str_{}.jpg".format(time.time()), img)
    result = easyocr_reader.readtext(img)
    for i in result:
        if str1 in str(i[1]):
            [x1, x2, x3, x4] = i[0]
            center = [int((x1[0]+x3[0])/2), int((x1[1]+x3[1])/2)]
            d.click(center[0], center[1])
            return True
    return False


boss_time = 0
reward_time = 0
err = 0
other_time = 0
one_day_action = 0


# start_game_by_icon 和 check_in_game 已移至 adb_operations 模組

def new_stage_check(img):
    if [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10, abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10, abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10, abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10, abs(np.sum(img[908, 223]) - np.sum([160, 165, 164])) <= 10, abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10, abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10, abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10, abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]:
        return True
    return False

def stage_by_str(d, ocr_str: list, img: np.ndarray) -> str:
    """
    Determines the current game stage based on OCR text and saves a debug image.

    Args:
        d: The uiautomator2 device object (remains for potential future use).
        ocr_str: A list of strings detected by OCR.
        img: The OpenCV image corresponding to the OCR text.

    Returns:
        The name of the detected stage or "未知".
    """
    # Combine list of strings into a single string for easier searching
    full_text = "".join(ocr_str)
    # print("OCR偵測文字:", full_text)
    # Mapping of keywords to stage names and their properties
    stage_keywords = {
        "隱藏": "隱藏",
        "車位倉庫": "車位倉庫",
        "購物管家":"購物管家",
        "你的帳號在另一個地方登錄": "異地登錄",
        "退出遊戲": "異地登錄",
        "公告": "公告",
        "方案": "主頁面",
        "放置獎勵": "放置獎勵",
        "離線獎勵": "放置獎勵",
        "家族商店": "家族",
        "家族亂鬥": "家族",
        "征戰熔岩巨獸": "征戰熔岩巨獸",
        
    }

    for keyword, stage_name in stage_keywords.items():
        # Special condition for "征戰熔岩巨獸"
        if  "購物管家" in full_text and "離線獎勵" in full_text:
            return "放置獎勵"
        if  "購物管家" in full_text and "車位倉庫" in full_text:
            return "車位倉庫"
        if stage_name == "征戰熔岩巨獸":
            if "征戰熔岩巨獸" in full_text and "掃蕩" in full_text:
                # img_tools.save_stage_debug_image(stage_name, img)
                return stage_name
        # General condition for other stages
        elif keyword in full_text:
            # img_tools.save_stage_debug_image(stage_name, img)
            return stage_name
            
    return "未知"


def get_stage(d, Cnn_model, easyocr_reader):
    """ 截圖並判斷目前所在的頁面 """
    # cnn_result = cnn_model.predict_image(
    #     Cnn_model, d.screenshot(format='pillow'))
    img = d.screenshot(format='opencv')
    # if new_stage_check(img):
    #     logger.info("新方法")
    #     # if not os.path.exists("main"):
    #     #     os.makedirs("main")
    #     # cv2.imwrite("main/main_{}.jpg".format(time.time()), img)
    #     if cnn_result != "main":
    #         if not os.path.exists("other_stage"):
    #             os.makedirs("other_stage")
    #         return "other_stage"
    #     return "主頁面"
    # else:
    #     logger.warning("使用OCR方法")
    #     result = easyocr_reader.readtext(img, detail=0)
    #     stage_withocr = stage_by_str(d, result, img)
    #     if stage_withocr == "異地登錄":
    #         logger.error("異地登錄，請檢查帳號密碼安全性")
    #         return "異地登錄"
    #     if cnn_result != stage_withocr:
    #         if not os.path.exists("other_stage"):
    #             os.makedirs("other_stage")
    #         cv2.imwrite("other_stage/other_stage_{}.jpg".format(time.time()), img)
    result = easyocr_reader.readtext(img, detail=0)
    stage_withocr = stage_by_str(d, result, img)
    if stage_withocr == "異地登錄":
        logger.error("異地登錄，請檢查帳號密碼安全性")
        return "異地登錄"
    # logger.info(f"CNN辨識結果: {cnn_result}, OCR辨識結果: {stage_withocr}")
    logger.info(f"OCR辨識結果: {stage_withocr}")
    return stage_withocr



seed_timme = 0
check = True
# 檢測當前應用

def oracle(d: u2.Device, easyocr_reader, ip, clf: ClassifierCNN, rl_recorder: RLRecorder = None):
    d.click(321, 919)
    retry = 0
    while (retry < 5):
        img = d.screenshot(format='opencv')
        # 顏色檢測 - 家園
        # color check - homeland
        print(cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')))
        if cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "homeplace":
            break
        retry += 1
        click_white(d)
    if retry == 5:
        return
    time.sleep(1)
    d.click(101, 158)
    time.sleep(3)
    try:
        run_mining(d, ip, clf, rl_recorder=rl_recorder)
    except Exception as e:
        logger.error(f"連線失敗: {e}")

        d.click(500, 174)
        time.sleep(3)
        for _ in range(5):
            d.click(394+random.randint(-3,3),599+random.randint(-3,3)) #+30隻鎬子
        d.click(272, 752)
        time.sleep(3)
        click_str("確定", d, easyocr_reader)
        time.sleep(3)
        #保存截圖
        img = d.screenshot(format='opencv')
        if not os.path.exists("oracle"):
            os.makedirs("oracle")
    # cv2.imwrite("oracle/oracle_{}.jpg".format(time.time()), img)
    click_white(d)
    click_white(d)
    d.click(500, 913)
    time.sleep(3)
    d.click(321, 919)
    time.sleep(3)
  


# 設定台灣時區

# 設定台灣時區
TPE = datetime.timezone(datetime.timedelta(hours=8))


def is_expired(last_park_time, expired_time=60 * 60 * 3 + 55*60):
    # 計算當前時間
    now = time.time()

    # 計算是否超過 3 小時 30 分鐘
    time_exceeded = (now - last_park_time) > expired_time

    # 取得台灣當前日期
    current_date = datetime.datetime.now(TPE).strftime("%Y-%m-%d")
    recorded_date = datetime.datetime.fromtimestamp(
        last_park_time, TPE).strftime("%Y-%m-%d")

    # 判斷是否跨日
    is_next_day = (recorded_date != current_date)

    # 只要符合其中一個條件就回傳 True
    return time_exceeded or is_next_day


SEA_CYCLE_WEEKS = 4
MUSHROOM_ARENA_CYCLE_WEEKS = 4
_CYCLE_DEFAULT_WEEKS = SEA_CYCLE_WEEKS


def _should_execute_cycle(ip: str, record_name: str, cycle_weeks: int = _CYCLE_DEFAULT_WEEKS) -> tuple:
    """判斷指定週期是否該執行（每 cycle_weeks 週執行 1 週）。"""
    record = return_time(ip, name=record_name)
    now = datetime.datetime.now(TPE).date()
    current_monday = now - datetime.timedelta(days=now.weekday())

    if record is None:
        logger.info(f"[{ip}] {record_name}: 無記錄，第一次執行")
        return True, True

    recorded_date = None
    try:
        if isinstance(record, dict) and record.get("recorded_date"):
            recorded_date = datetime.datetime.strptime(record.get("recorded_date"), "%Y-%m-%d").date()
        elif isinstance(record, dict) and record.get("timestamp"):
            recorded_date = datetime.datetime.fromtimestamp(float(record.get("timestamp")), TPE).date()
        else:
            logger.warning(f"[{ip}] {record_name}: 記錄格式異常，重新執行")
            return True, True
    except Exception as e:
        logger.error(f"[{ip}] {record_name}: 解析記錄失敗 {e}，重新執行")
        return True, True

    recorded_monday = recorded_date - datetime.timedelta(days=recorded_date.weekday())
    weeks_since = (current_monday - recorded_monday).days // 7
    if weeks_since < 0:
        weeks_since = 0

    # 判斷是否在同一週內
    in_same_week = (weeks_since == 0)
    # 判斷是否輪到執行週（每 cycle_weeks 週執行一次）
    should_execute = (weeks_since % cycle_weeks) == 0
    
    logger.info(f"[{ip}] {record_name}: 記錄日期={recorded_date}, 當前週一={current_monday}, 距離{weeks_since}週, 週期={cycle_weeks}週, 同一週={in_same_week}, 應執行={should_execute}")
    
    return should_execute, False


def should_execute_sea(ip: str) -> tuple:
    return _should_execute_cycle(ip, "sea_cycle_start", cycle_weeks=SEA_CYCLE_WEEKS)


def should_execute_sea_with_cooldown(ip: str) -> tuple:
    """判斷是否該執行 sea（每4週中的1週，且該週內每4小時執行一次）。"""
    # 先檢查是否在正確的週期
    in_correct_week, need_week_record = _should_execute_cycle(ip, "sea_cycle_start", cycle_weeks=SEA_CYCLE_WEEKS)
    
    if not in_correct_week:
        return False, False
    
    # 在正確的週期內，檢查4小時冷卻
    last_execution = return_time(ip, name="sea_last_execution")
    
    if last_execution is None:
        # 第一次執行
        return True, need_week_record
    
    # 檢查是否已過4小時 (14400秒)
    expired_time = 4 * 60 * 60  # 4小時
    if is_expired(last_execution.get("timestamp", 0), expired_time=expired_time):
        return True, False  # 不需要更新週期記錄，只需更新執行時間
    
    return False, False


def should_execute_mushroom_arena(ip: str) -> tuple:
    return _should_execute_cycle(ip, "mushroom_arena_cycle_start", cycle_weeks=MUSHROOM_ARENA_CYCLE_WEEKS)


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
def check_on_line(Cnn_model, easyocr_reader):
    # 檢查是否在線上
    try:
        # 連接到設備
        ip ='emulator-5554'
        d = connect_u2_with_retries(ip, logger=default_logger)
        if not d.info.get('screenOn'):
            logger.info("螢幕未開啟，嘗試解鎖")
            d.unlock()
            time.sleep(1)
        
    except Exception as e:
        logger.error(f"連線失敗: {e}")
        try:
            d = connect_u2_with_retries('3a8d31f2', logger=default_logger)
        except Exception:
            raise
    
    # 使用圖示啟動遊戲
    start_game_by_icon(d, ip)
    time.sleep(20+random.randint(0, 5))  # 增加隨機延遲
    start_time = time.time()
    while (time.time() - start_time) < 60:
        try:
            screen_stage = cnn_model.predict_image(
                Cnn_model, d.screenshot(format='pillow'))
            logger.info(f"目前頁面: {screen_stage}")
            if screen_stage == "main":
                logger.info("in game")
                time.sleep(5)
                break
            elif cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "reward":
                reward(d, easyocr_reader)
            else:
                logger.info("not in game")
                time.sleep(7)
                d.click(0.99, 0.01)
        except Exception as e:
            logger.error(f"連線失敗: {e}")
            d.app_stop("com.mxdzz.tw.and")
            return True
    if cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "main":
        d.click(0.05, 0.01)
        time.sleep(1)
        d.click(54, 364)
        time.sleep(3)
        while (1):
            img = d.screenshot(format='opencv')[181:260, 60:366]
            result = str(easyocr_reader.readtext(img, detail=0))
            if "上" in result:
                logger.info("pass")
                d.app_stop("com.mxdzz.tw.and")
                return False
            else:
                d.app_stop("com.mxdzz.tw.and")
                return True
    elif get_stage(d, Cnn_model, easyocr_reader) == "異地登錄":
        d.app_stop("com.mxdzz.tw.and")
        time.sleep(5*60)
        return False
    d.app_stop("com.mxdzz.tw.and")
    return False

# def load_cnn_model(model_path, num_classes=10):
#     logger.warning("即將廢棄")
#     # 載入模型
#     model = cnn_model.SimpleCNN(num_classes=num_classes)
#     model.load_state_dict(torch.load(model_path))
#     model.eval()  # 設定為評估模式
#     return model


def load_oracle_cnn_model(model_path, num_classes=10):
    # 載入模型
    model = miner.simplecnn.SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path))
    model.eval()  # 設定為評估模式
    return model
from typing import Union, List

# run_adb 和 connect_u2_with_retries 已移至 adb_operations 模組


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

    manager = ParkingManager(
        device=d, reader=easyocr_reader, ip=ip, cnn_model=Cnn_model,protect=protect)
    battle_manager = new_battle.BattleManager(
        device=d, reader=easyocr_reader, cnn_model=Cnn_model)
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

    try: # Add try block here
        while (1):
            # --- 喚醒與解鎖手機 (fc65396d / 實體手機) ---
            if 'fc65396d' in ip or '192.168' in ip:
                logger.info(f"[{ip}] 檢查螢幕狀態...")
                while True:
                    try:
                        d.info.get('screenOn')
                        break
                    except Exception as e:
                        logger.error(f"[{ip}] 檢查螢幕狀態時發生錯誤: {e}")
                        try:
                            d = connect_u2_with_retries(ip, logger=device_logger)
                        except Exception as e2:
                            logger.error(f"[{ip}] 重新連線失敗: {e2}")
                        time.sleep(60)  # 等待 60 秒後重試
                while True:
                    try:
                        if d.info.get('screenOn'):
                            logger.info(f"[{ip}] 偵測到螢幕為開啟狀態，可能正在使用中。等待 5 秒後重試...")
                            time.sleep(5)
                        if not d.info.get('screenOn'):
                            break
                    except Exception as e:
                        logger.warning(f"[{ip}] 檢查螢幕狀態時發生錯誤: {e}")
                        time.sleep(60)  # 等待 60 秒後重試
                        try:
                            d = connect_u2_with_retries(ip, logger=device_logger)
                            logger.warning(f"[{ip}] 重新連接設備成功")
                        except Exception as e2:
                            logger.warning(f"[{ip}] 重新連接設備失敗: {e2}")
                if not d.info.get('screenOn'):
                    logger.info(f"[{ip}] 螢幕為關閉狀態，執行標準喚醒與解鎖...")
                    d.unlock()  # 使用 uiautomator2 的標準解鎖方法
                    d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
                    d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
                    time.sleep(2)  # 等待解鎖動畫完成

                    # 再次確認螢幕是否成功開啟
                    if not d.info.get('screenOn'):
                        logger.warning(f"[{ip}] 標準解鎖失敗，嘗試備用方案: Power鍵 + 上滑")
                        d.press("power")  # 按下電源鍵
                        time.sleep(1)
                        d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1) # 從下往上滑動
                        time.sleep(1)
                else:
                    logger.info(f"[{ip}] 螢幕已是開啟狀態。")
                
            if 'emulator-5556' in ip or '3a8d31f2' in ip or 'emulator-5554' in ip :
                logger.info(f"[{ip}] 休眠5分鐘後繼續")
                time.sleep(60*5)
            time.sleep(2)
            #打開chrome
            if 'fc65396d' in ip or '192.168' in ip:
                
                close_nofication(d)
            time.sleep(1)
            d.app_stop("com.mxdzz.tw.and")    
            global lock
            if 'emulator-5558' in ip :
                while lock == True:
                    time.sleep(1)
                    logger.warning(f"[{ip}] 等待解鎖")
                d.app_stop("com.mxdzz.tw.and")    
                lock = True # 問題點1：此處將 lock 設為 True。如果此執行緒在此後卡住或出錯，lock 可能無法被重設為 False。
                for i in range(60):
                    if check_on_line(Cnn_model, easyocr_reader):
                        break
                    time.sleep(5*60) # lock 為 True 的狀態可能持續很長時間 (長達 60*5 = 5 分鐘)
                lock = False
            # 問題點2：其他執行緒 (ip 為 '3a8d31f2' 或 'emulator-5554') 會檢查 lock
            # 如果 lock 被 'emulator-5558' 執行緒設為 True 且未能改回 False，
            # 則這些執行緒會在此處無限等待。

            while(('3a8d31f2' in ip or 'emulator-5554' in ip) and lock == True ):
                logger.warning("等待解鎖") # 將持續印出此訊息
                time.sleep(3)
            if 'emulator-5554' in ip or '3a8d31f2' in ip:
                lock = True    
            if 'emulator-5560' in ip:
                time.sleep(30*1)
            while(1):
                if d.info.get('screenOn') == True:
                    break;
                logger.info("螢幕未開啟，嘗試解鎖")
                d.unlock()
                d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05) # 從下往上滑動
                d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05) # 從下往上滑動
                time.sleep(1)
            d.press("home")
            d.press("home")
            d.press("home")

        

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
                            run_adb(
                            'shell wm density 240',
                            device_serial=ip
                            )
                            run_adb('shell wm size 540x960',
                            device_serial=ip
                            )
                        else:
                            raise Exception("未找到遊戲圖示")
                    except:                    
                        output = launch_clone("com.mxdzz.tw.and", 2,device_serial=ip)
                        run_adb(
                            'shell wm density 240',
                            device_serial=ip
                        )
                        run_adb('shell wm size 540x960',
                            device_serial=ip
                        )
                    time.sleep(1)
                    
                else:
                    # 使用圖示啟動遊戲 (模擬真人操作)
                    logger.info(f"[{ip}] 透過桌面圖示啟動遊戲")
                    start_game_by_icon(d, ip)
                # time.sleep(20+random.randint(0, 5))
                wait_time = time.time()
                while (1):
                    img = d.screenshot(format='opencv')
                    ocr_result = easyocr_reader.readtext(img, detail=0)
                    current_stage = stage_by_str(d, ocr_result, img)
                    if current_stage in ["主頁面", "公告", "放置獎勵", "家族", "離線獎勵","購物管家","車位倉庫","隱藏"]:
                        break
                    time.sleep(1)
                    if time.time()-wait_time > 60:
                        d.app_stop("com.mxdzz.tw.and")
                        time.sleep(1)
                        # 重新啟動時使用圖示點擊
                        logger.info(f"[{ip}] 等待超時,重新啟動遊戲")
                        start_game_by_icon(d, ip)
                        time.sleep(30+random.randint(0, 5))
                        wait_time = time.time()
                # 使用ocr檢測文字
                img = d.screenshot(format='opencv')
                wait_time = time.time()
                unknown_count = 0
                while (1):
                    img = d.screenshot(format='opencv')
                    ocr_result = easyocr_reader.readtext(img, detail=0)
                    current_stage = stage_by_str(d, ocr_result, img)
                    logger.info(f"[{ip}] 目前頁面: {current_stage}")
                    if current_stage =="隱藏":
                        img_tools.click_str_by_server(d, '隱藏')
                        time.sleep(1)
                        img_tools.click_str_by_server(d, '隱藏',y_range=(593,662))
                        time.sleep(1) 
                        click_white(d)
                        unknown_count = 0
                    if current_stage == "離線獎勵" or current_stage == "放置獎勵":
                        reward(d, easyocr_reader)
                        unknown_count = 0
                    if "公告" in ocr_result:
                        d.click(248, 812)
                        time.sleep(1)
                        click_white(d)
                        time.sleep(1)
                        unknown_count = 0
                    if current_stage == "購物管家":
                        logger.info("購物管家頁面，點擊返回主頁面")
                        img_tools.click_str_by_server(d, '採購', y_range=(690,740))
                        time.sleep(2)
                        click_white(d)
                        img_tools.click_str_by_server(d, '副本管家', y_range=(773,839))
                        time.sleep(2)
                        img_tools.click_str_by_server(d, '掃蕩', y_range=(690,740))
                        time.sleep(2)
                        for i in range(6):
                            click_white(d)
                        time.sleep(2)
                        unknown_count = 0
                        break
                    if current_stage == "主頁面":
                        unknown_count = 0
                        break 
                    if current_stage =="車位倉庫":
                        logger.info("車位倉庫頁面，點擊返回主頁面")
                        img_tools.click_str_by_server(d, '領取', y_range=(697,737))
                        time.sleep(2)
                        click_white(d)
                        time.sleep(1)
                        unknown_count = 0
                    if current_stage == "異地登錄":
                        logger.info("異地登錄頁面，重新啟動遊戲")
                        d.app_stop("com.mxdzz.tw.and")
                        time.sleep(1)
                        # 重新啟動時使用圖示點擊
                        time.time()
                        time.sleep(30+random.randint(0, 5))
                        start_game_by_icon(d, ip)
                        wait_time = time.time()
                        unknown_count = 0
                    if current_stage == "未知":
                        unknown_count += 1
                        logger.info(f"[{ip}] 未知頁面，等待中... (count={unknown_count})")
                        d.press("back")
                        time.sleep(5)
                        # 若連續多次都是未知，嘗試重啟應用以恢復
                        if unknown_count >= 3:
                            logger.warning(f"[{ip}] 已連續 {unknown_count} 次偵測到 未知，嘗試重啟遊戲以回復")
                            try:
                                d.app_stop("com.mxdzz.tw.and")
                            except Exception as e:
                                logger.error(f"[{ip}] 停止應用失敗: {e}")
                            time.sleep(1)
                            start_game_by_icon(d, ip)
                            time.sleep(30+random.randint(0, 5))
                            wait_time = time.time()
                            unknown_count = 0
                    if time.time()-wait_time > 60:
                        d.app_stop("com.mxdzz.tw.and")
                        time.sleep(1)
                        # 重新啟動時使用圖示點擊
                        logger.info(f"[{ip}] 等待超時,重新啟動遊戲")
                        start_game_by_icon(d, ip)
                        time.sleep(30+random.randint(0, 5))
                        wait_time = time.time()

            # # 進行ocr
            # result = easyocr_reader.readtext(img, detail=0)
            # if "你的帳號在另一個地方登錄" in result or "退出遊戲" in result:
            #     if not os.path.exists("other_login"):
            #         os.makedirs("other_login")
            #     cv2.imwrite(
            #         "other_login/other_login_{}.jpg".format(time.time()), img)
            #     click_str("退出遊戲", d, easyocr_reader)
            #     time.sleep(5)
            #     click_str("確認登出", d, easyocr_reader)
            #     end = time.time()
            #     while (1):
            #         time.sleep(1)
            #         if time.time()-end > 60*28:
            #             break
            #         print(time.time()-end)
            #     # 異地登錄後重新啟動
            #     logger.info(f"[{ip}] 異地登錄處理完成,重新啟動遊戲")
            #     start_game_by_icon(d, ip)
            #     time.sleep(30+random.randint(0, 5))

            # elif "公告" in result:
            #     d.click(248, 812)
            #     time.sleep(1)
            #     click_white(d)
            #     time.sleep(1)
            
            # img = d.screenshot(format='opencv')
            # stage = get_stage(d, Cnn_model, easyocr_reader)
            # if stage =="車位倉庫":
            #     logger.info("車位倉庫頁面，點擊返回主頁面")
            #     img_tools.click_str_by_server(d, '領取', y_range=(697,737))
            #     time.sleep(2)
            #     click_white(d)

            # stage = get_stage(d, Cnn_model, easyocr_reader)
            # if stage =="購物管家":
            #     logger.info("購物管家頁面，點擊返回主頁面")
            #     img_tools.click_str_by_server(d, '採購', y_range=(690,740))
            #     time.sleep(2)
            #     click_white(d)
            #     img_tools.click_str_by_server(d, '副本管家', y_range=(773,839))
            #     time.sleep(2)
            #     img_tools.click_str_by_server(d, '掃蕩', y_range=(690,740))
            #     time.sleep(2)
            #     for i in range(6):
            #         click_white(d)
            #     time.sleep(2)
            # img = d.screenshot(format='opencv')

            
            # if state_manager.get_state() == "放置獎勵":
            #     print('new method')
            #     reward(d, easyocr_reader)
            # else:
            #     # 仅在状态不符合时再进行 OCR 检测
            #     result = easyocr_reader.readtext(img, detail=0)
            #     if any(keyword in result for keyword in ["放置獎勵", "離線獎勵"]):
            #         reward(d, easyocr_reader)
            #         time.sleep(3)
            img = d.screenshot(format='opencv')
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
            stage = get_stage(d, Cnn_model, easyocr_reader)
            logger.info(f"目前頁面: {stage}, 開始檢查停車狀態")
            if stage == "主頁面":
                last_park_time = return_time(ip, name="park")
                if last_park_time is None:
                    last_park_time = 0
                try:
                    last_park_time = last_park_time["timestamp"]
                except:
                    last_park_time = 0
                if is_expired(last_park_time, expired_time=60*60*4) and ('5556' in ip) and (current_time.tm_hour >= 6 or current_time.tm_hour <= 1):
                    logger.info("超過三個小時，執行停車")
                    status = manager.check_and_park(protect=True)
                    time_recording(ip, name="park")
                # elif is_expired(last_park_time, expired_time=60*60*8) and (('emulator-5558' in ip) or ('7fe98fc6' in ip) or ('fc65396d' in ip)):
                #     logger.info("超過8個小時，執行停車")
                #     status = manager.check_and_park()
                #     time_recording(ip, name="park")
                else:
                    # if 'emulator-5558' in ip or 'emulator-5562' in ip or '7fe98fc6' in ip or 'fc65396d'  in ip:
                    #     logger.info("未超過8個小時，不執行收車")
                    # else:
                    #     logger.info("修哥不執行停車")
                    status = manager.check_and_park(protect=False)
                    if status == True:
                        time_recording(ip, name="park")
            last_park_time = return_time(ip, name="park")
            if last_park_time is None:
                last_park_time = 0
            else:
                last_park_time = last_park_time["timestamp"]

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
            #     buy_arean_everyday(d, ip)
            #紀錄並執行 每日點擊加速
            def daily_acceleration(d, ip):
                record = return_time(ip, name="daily_acceleration")
                should_execute = False
                if record is None:
                    should_execute = True
                else:
                    should_execute = record.get("is_next_day", False)
                if should_execute:
                    logger.info("執行每日加速")
                    d.click(321, 913)
                    time.sleep(1)
                    while(1):
                        cnn_result = cnn_model.predict_image(
                            Cnn_model, d.screenshot(format='pillow'))
                        if cnn_result == 'homeplace':
                            break
                    d.click(452,218) #點擊研究中心
                    time.sleep(1)
                    for i in range(5):
                        d.click(168,814) #跳過30分鐘
                        time.sleep(0.8)
                    d.click(487,923) #點擊返回
                    time.sleep(1)
                    d.click(321, 919) #點擊家園返回
                    time_recording(ip, name="daily_acceleration")
                else:
                    logger.info("今日已執行過每日加速，跳過")
            daily_acceleration(d, ip)
            stage=get_stage(d,Cnn_model, easyocr_reader)
            print("stage:", stage)
            if get_stage(d, Cnn_model, easyocr_reader) == "主頁面" :
                def _should_perform_oracle_action(ip: str) -> bool:
                    """
                    判斷是否需要執行 oracle（挖礦）動作。
                    修正：使用 StoreDataManager 的時間抽取工具並以管理器時區做比較，避免時區誤差造成跨日才更新的問題。
                    Returns True 表示應該執行。
                    """
                    store_manager = create_store_manager(ip)
                    record = store_manager.get_purchase_record("挖礦")
                    if not record:
                        logger.info("未找到'挖礦'記錄，將執行購買流程。")
                        return True

                    # 嘗試以管理器的工具解析為 local aware datetime（已含時區）
                    try:
                        local_dt = store_manager._extract_last_time_as_local(record)
                    except Exception:
                        local_dt = None

                    # 如果解析失敗，嘗試用 last_time_utc / last_time 回退處理
                    if local_dt is None:
                        # 優先使用標準 UTC 欄位
                        try:
                            utc_str = record.get("last_time_utc") or record.get("last_time")
                            if utc_str:
                                # 若是 last_time_utc (ISO)，用 parse 工具；若是 legacy last_time，視為 UTC 再轉回 local
                                dt_utc = None
                                from json_manager import StoreDataManager  # type: ignore
                                # 嘗試解析 ISO 或舊格式
                                try:
                                    dt_utc = store_manager._parse_iso_as_utc(utc_str)
                                except Exception:
                                    dt_utc = None
                                if dt_utc is None:
                                    try:
                                        dt_utc = store_manager._parse_legacy_naive_as_utc(utc_str)
                                    except Exception:
                                        dt_utc = None
                                if dt_utc:
                                    try:
                                        local_dt = dt_utc.astimezone(store_manager.timezone)
                                    except Exception:
                                        local_dt = None
                        except Exception:
                            local_dt = None

                    if local_dt is None:
                        logger.warning("記錄時間無法解析，將執行購買流程。")
                        return True

                    now_local = datetime.datetime.now(store_manager.timezone)
                    # 閾值：4 小時
                    if (now_local - local_dt) > datetime.timedelta(hours=2):
                        logger.info(f"上次'挖礦'時間為 {local_dt}, 已超過4小時，將執行購買流程。")
                        return True
                    else:
                        logger.info(f"上次'挖礦'時間為 {local_dt}, 尚未超過4小時，不執行。")
                        return False

                if _should_perform_oracle_action(ip):
                    oracle(d, easyocr_reader, ip=ip, clf=clf, rl_recorder=rl_recorder)
                    time.sleep(3)
                    # 使用新的JSON管理器記錄
                    store_manager = create_store_manager(ip)
                    
                    store_manager.record_purchase("挖礦", {"last_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

            # stage = get_stage(d, Cnn_model, easyocr_reader)
            # if stage == "主頁面" and current_time.tm_hour % 4 == 0:
            #     assistant_manager.go_to_get_assistant()

            # 結束com.mxdzz.tw.and
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and  (current_time.tm_hour == 20 or current_time.tm_hour == 23 or current_time.tm_hour == 8):
                mission_manager.do_allmission()
                
            # if current_time.tm_hour % 4 == 0 or current_time.tm_hour == 23:
            #     stage = get_stage(d, Cnn_model, easyocr_reader)
            #     if stage == "主頁面":
            #         d.click(228, 926)
            #         time.sleep(2)
            #         battle_manager.execute_all_battles(check=True,ip = ip)
            # if 'emulator-5558' not in ip:
            # stage = get_stage(d, Cnn_model, easyocr_reader)
            # if stage == "主頁面":
            #     _run_periodic_cycle(
            #         ip,
            #         record_name="sea_last_execution",
            #         should_execute_fn=should_execute_sea_with_cooldown,
            #         action_fn=sea,
            #         display_name="sea",
            #         d=d,
            #         cycle_record_name="sea_cycle_start",
            #     )
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

            # should_run_fight_test = should_execute and is_not_sunday and (is_monday_afternoon or is_after_monday)
            # if should_run_fight_test:
            #     new_battle.fight_test(d)
            #     time_recording(ip, name="萬神試煉")
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and ip != "emulator-5556":
                daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1)
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面" and 'fc65396d' in ip :
                Open_gold_paddle_ocr.open_the_gold(d, times=1800+random.randint(-50,50),is_compare=True,device_ip=ip)
            #時間
            elif stage == "主頁面" and current_time.tm_hour % 2 == 0 and  ip != "emulator-5558" :
                Open_gold_paddle_ocr.open_the_gold(d, times=500+random.randint(-50,50))
            elif stage == "主頁面" and current_time.tm_hour % 2 == 0 and  ip == "emulator-5562" :
                Open_gold_paddle_ocr.open_the_gold(d, times=60+random.randint(-50,50))
            # if stage == "主頁面" and ip != "emulator-5558":
            #     state_manager.check_and_change_state()
            stage = get_stage(d, Cnn_model, easyocr_reader)
            # if stage == "主頁面" and ip != "emulator-5558":
            if stage == "主頁面":
                wheel_manager.spin_and_send_gold()
            # if random.random()<0.7 and get_stage(d, Cnn_model, easyocr_reader) == "主頁面" and "fc65396d" not in ip:
            #     d.press("back")  # 按下返回鍵
            #     #點擊退出遊戲
            #     click_str("確認", d, easyocr_reader)
            # stage = get_stage(d, Cnn_model, easyocr_reader)
            # if stage == "主頁面":
                # new_battle.run_weekly_cloud_pre_single(d,ip,name='因仔仙')
                # new_battle.run_saturday_help_single(d, 'emulator-5558')
                # if ip != "emulator-5558" and ip != "emulator-5554":
            # new_battle.run_weekly_cloud_fighting_single(d,ip)
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
                run_adb(
                    'shell wm density reset',
                    device_serial=ip)
                time.sleep(1)
                run_adb('shell wm size reset',
                    device_serial=ip)
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
            if 'emulator-5554' in ip or '3a8d31f2' in ip:
                lock = False
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

                # 檢查停車是否過期
                # if ('emulator-5558' in ip or '7fe98fc6' in ip or 'fc65396d' in ip) and last_wake_time + 60*60*4 < time.time():
                #     is_park_expired = is_expired(last_park_time, expired_time=60*60*8)
                # else:
                #     is_park_expired = is_expired(last_park_time, expired_time=60*60*5) and protect == True

                # if is_park_expired:
                #     logger.info(f"[{ip}] 停車已超過3小時,強制喚醒執行收車")
                #     break

                # if (current_time.tm_hour == 0 and current_time.tm_min == 0) or (current_time.tm_hour == 23 and current_time.tm_min == 45):
                #     logger.info(f"[{ip}] 現在是0點,強制喚醒")
                #     break

                # # 對齊後喚醒時間到了就醒（一般設備 & emulator-5558 都共用 wake_ts）
                # if time.time() >= wake_ts:
                #     logger.info(f"[{ip}] 已達對齊後喚醒時間，執行喚醒")
                #     break

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
        
        # 開始重構
