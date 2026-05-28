# -*- coding: utf-8 -*-
"""
遊戲配置文件
"""
import pytz
from dataclasses import dataclass
from typing import Dict, List, Tuple

@dataclass
class GameConfig:
    """遊戲配置類"""
    # 基本配置
    PACKAGE_NAME = "com.mxdzz.tw.and"
    TPE = pytz.timezone('Asia/Taipei')
    
    # 時間設定
    PARK_EXPIRED_TIME = 60 * 60 * 3 + 59 * 60  # 3小時59分
    WAKE_UP_MIN_TIME = 60 * 30  # 30分鐘
    CHECK_INTERVAL = 10  # 檢查間隔
      # 設備配置
    EXCLUDED_DEVICES = ['emulator-5566']
    
    # 設備列表
    DEVICES = [
        "emulator-5554",
        "emulator-5560", 
        "emulator-5562",
        "emulator-5566",
        "emulator-5568",
        "fc65396d"
    ]
    
    # 設備啟動間隔（秒）
    DEVICE_START_INTERVAL = 10
    
    # 檔案路徑
    CNN_MODEL_PATH = "cnn_model.pth"
    ORACLE_MODEL_PATH = "./miner/oralce_model.pth"
    
    # 截圖相關配置
    SCREENSHOT_TIMEOUT = 5  # 截圖超時時間
    MAX_RETRY_COUNT = 3     # 最大重試次數
    
    # 狀態檢測閾值
    PIXEL_THRESHOLD = 10    # 像素差異閾值
    OCR_CONFIDENCE = 0.7    # OCR信心度閾值
    
    # 目錄配置
    SCREENSHOT_DIRS = [
        "announcement", "main", "reward", "family", "boss",
        "homeland", "homeplace", "farm", "find_img", "other_stage",
        "other_str", "other_login", "reward_get", "oracle"
    ]
    
    # 像素檢測點配置 (用於主頁面檢測)
    MAIN_PAGE_PIXELS = [
        (955, 535, [47, 138, 123]),
        (902, 39, [146, 232, 232]),
        (956, 6, [50, 140, 117]),
        (921, 135, [41, 21, 218]),
        (908, 223, [160, 165, 164]),
        (731, 27, [139, 170, 201]),
        (759, 30, [111, 143, 179]),
        (794, 37, [38, 60, 88]),
        (825, 380, [37, 58, 86])
    ]
    
    # 設備特殊配置
    DEVICE_CONFIGS = {
        'fc65396d': {
            'need_unlock': True,
            'need_clone': True,
            'density_reset': True,
            'screen_off': True
        },
        'emulator-5568': {
            'online_check': True,
            'check_count': 6,
            'check_interval': 300  # 5分鐘
        },
        'emulator-5554': {
            'delay': 300  # 5分鐘
        },
        'emulator-5566': {
            'delay': 300  # 5分鐘
        },
        'emulator-5560': {
            'delay': 30  # 30秒
        }
    }

# 全局配置實例
config = GameConfig()
