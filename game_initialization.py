"""
遊戲啟動後的頁面判斷與處理模組

功能：
- 處理遊戲啟動後的各種頁面（隱藏、獎勵、公告、購物管家等）
- 自動處理異常頁面（未知、異地登錄等）
- 等待遊戲進入主頁面或可操作的頁面
"""

import time
import random
import logging
from typing import Tuple
import img_tools
from tools import click_white
from adb_operations import connect_u2_with_retries, start_game_by_icon
from game_state.detector import get_stage
from game_actions.reward_manager import reward
from utils.logging_utils import logger, default_logger
import new_cnn.cnn_model as cnn_model_module

def handle_game_startup_pages(d, ip: str, easyocr_reader, start_game_fn, 
                               reward_fn, logger: logging.Logger = None) -> bool:
    """
    遊戲啟動後的頁面判斷與處理主函數。
    
    功能：
    - 循環判斷當前頁面狀態
    - 自動處理各種頁面（隱藏、獎勵、公告等）
    - 超時或異常時重新啟動遊戲
    - 達到主頁面或可操作頁面時結束
    
    參數：
    - d: uiautomator2 device 物件
    - ip: 設備 IP 或序列號
    - easyocr_reader: EasyOCR 讀取器
    - start_game_fn: 啟動遊戲的函數
    - reward_fn: 領取獎勵的函數
    - logger: logging.Logger 物件（若 None 則使用預設 logger）
    
    回傳：
    - True: 成功到達可操作的頁面
    - False: 遊戲啟動失敗或超時
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    
    # 從 new_main_before20250514 import 需要的函數
    from game_state.detector import stage_by_str
    
    wait_time = time.time()
    unknown_count = 0
    max_unknown_attempts = 3
    wait_timeout = 60  # 秒
    unknown_detection_delay = 30  # 30秒後才開始檢測未知頁面
    
    while True:
        try:
            img = d.screenshot(format='opencv')
            ocr_result = easyocr_reader.readtext(img, detail=0)
            current_stage = stage_by_str(d, ocr_result, img)
            logger.info(f"[{ip}] 目前頁面: {current_stage}")
            
            # ===== 頁面處理分支 =====
            
            if current_stage == "隱藏":
                logger.info(f"[{ip}] 檢測到隱藏頁面，點擊隱藏按鈕")
                img_tools.click_str_by_server(d, '隱藏')
                time.sleep(1)
                img_tools.click_str_by_server(d, '隱藏', y_range=(593, 662))
                time.sleep(1)
                click_white(d)
                unknown_count = 0
                
            elif current_stage == "離線獎勵" or current_stage == "放置獎勵":
                logger.info(f"[{ip}] 檢測到獎勵頁面，領取獎勵")
                reward_fn(d, easyocr_reader)
                unknown_count = 0
                
            elif "公告" in ocr_result:
                logger.info(f"[{ip}] 檢測到公告頁面，關閉公告")
                d.click(248, 812)
                time.sleep(1)
                click_white(d)
                time.sleep(1)
                unknown_count = 0
                
            elif current_stage == "購物管家":
                logger.info(f"[{ip}] 檢測到購物管家頁面，返回主頁面")
                img_tools.click_str_by_server(d, '採購', y_range=(690, 740))
                time.sleep(2)
                click_white(d)
                img_tools.click_str_by_server(d, '副本管家', y_range=(773, 839))
                time.sleep(2)
                img_tools.click_str_by_server(d, '掃蕩', y_range=(690, 740))
                time.sleep(2)
                for _ in range(6):
                    click_white(d)
                time.sleep(2)
                unknown_count = 0
                logger.info(f"[{ip}] 已返回主頁面（購物管家分支）")
                return True
                
            elif current_stage == "主頁面":
                logger.info(f"[{ip}] 已到達主頁面")
                unknown_count = 0
                return True
                
            elif current_stage == "車位倉庫":
                logger.info(f"[{ip}] 檢測到車位倉庫頁面，領取獎勵並返回")
                img_tools.click_str_by_server(d, '領取', y_range=(697, 737))
                time.sleep(2)
                click_white(d)
                time.sleep(1)
                unknown_count = 0
                
            elif current_stage == "異地登錄":
                logger.warning(f"[{ip}] 偵測到異地登錄，重新啟動遊戲")
                d.app_stop("com.mxdzz.tw.and")
                time.sleep(1)
                start_game_fn(d, ip)
                time.sleep(30 + random.randint(0, 5))
                wait_time = time.time()
                unknown_count = 0
                
            elif current_stage == "未知":
                # 只在啟動後30秒才開始檢測未知頁面
                time_elapsed = time.time() - wait_time
                if time_elapsed < unknown_detection_delay:
                    logger.info(f"[{ip}] 遊戲剛啟動，檢測到未知頁面但暫不處理 (已耗時 {time_elapsed:.1f}秒，需要等待 {unknown_detection_delay}秒)")
                    time.sleep(2)  # 等待2秒後重新檢測
                else:
                    unknown_count += 1
                    logger.info(f"[{ip}] 未知頁面，等待中... (count={unknown_count}/{max_unknown_attempts})")
                    d.press("back")
                    time.sleep(5)
                    
                    # 若連續多次都是未知，嘗試重啟應用以恢復
                    if unknown_count >= max_unknown_attempts:
                        logger.warning(f"[{ip}] 已連續 {unknown_count} 次偵測到未知，嘗試重啟遊戲以回復")
                        try:
                            d.app_stop("com.mxdzz.tw.and")
                        except Exception as e:
                            logger.error(f"[{ip}] 停止應用失敗: {e}")
                        time.sleep(1)
                        start_game_fn(d, ip)
                        time.sleep(30 + random.randint(0, 5))
                        wait_time = time.time()
                        unknown_count = 0
                    
            # ===== 超時檢查 =====
            if time.time() - wait_time > wait_timeout:
                logger.warning(f"[{ip}] 等待超時 ({wait_timeout}秒)，重新啟動遊戲")
                d.app_stop("com.mxdzz.tw.and")
                time.sleep(1)
                start_game_fn(d, ip)
                time.sleep(30 + random.randint(0, 5))
                wait_time = time.time()
                unknown_count = 0
                
        except Exception as e:
            logger.error(f"[{ip}] handle_game_startup_pages 發生異常: {e}", exc_info=True)
            # 異常時嘗試重新連接或重啟
            try:
                d.app_stop("com.mxdzz.tw.and")
            except Exception as e2:
                logger.error(f"[{ip}] 停止應用失敗: {e2}")
            time.sleep(1)
            start_game_fn(d, ip)
            time.sleep(30 + random.randint(0, 5))
            wait_time = time.time()
            unknown_count = 0

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
            screen_stage = cnn_model_module.predict_image(
                Cnn_model, d.screenshot(format='pillow'))
            logger.info(f"目前頁面: {screen_stage}")
            if screen_stage == "main":
                logger.info("in game")
                time.sleep(5)
                break
            elif cnn_model_module.predict_image(Cnn_model, d.screenshot(format='pillow')) == "reward":
                reward(d, easyocr_reader)
            else:
                logger.info("not in game")
                time.sleep(7)
                d.click(0.99, 0.01)
        except Exception as e:
            logger.error(f"連線失敗: {e}")
            d.app_stop("com.mxdzz.tw.and")
            return True
    if cnn_model_module.predict_image(Cnn_model, d.screenshot(format='pillow')) == "main":
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
