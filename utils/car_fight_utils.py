import json
import logging
import os
import time
import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

CAR_FIGHT_FILE = "push_project/web/car_fight.json"

def get_next_car_fight_ts() -> List[float]:
    """從 JSON 中讀取所有車位的結束時間，並轉為今日的時間戳列表"""
    if not os.path.exists(CAR_FIGHT_FILE):
        return []

    try:
        with open(CAR_FIGHT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        fight_ts_list = []
        now = datetime.datetime.now()
        
        for key, info in data.items():
            if not isinstance(info, dict) or "end_time" not in info:
                continue
            
            end_time_str = info["end_time"]
            try:
                # 處理兩種格式: "HH:MM:SS" 或 "YYYY-MM-DD HH:MM:SS"
                if len(end_time_str) <= 8:
                    target_dt = datetime.datetime.strptime(
                        f"{now.strftime('%Y-%m-%d')} {end_time_str}", 
                        "%Y-%m-%d %H:%M:%S"
                    )
                else:
                    target_dt = datetime.datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
                
                ts = target_dt.timestamp()
                # 保留未來 2 小時內的戰鬥即可，避免處理過時資料
                if ts > time.time() - 600:
                    fight_ts_list.append(ts)
            except Exception as e:
                logger.warning(f"[car_fight_utils] 例外: {e}")
                continue
        
        return sorted(fight_ts_list)
    except Exception as e:
        print(f"[CarFight] 讀取 JSON 失敗: {e}")
        return []

def adjust_wake_time_for_cars(original_ts: float) -> float:
    """
    根據車位戰鬥時間避開喚醒時間。
    如果喚醒時間落在戰鬥結束的前後 10 分鐘內，則延後喚醒。
    """
    fight_times = get_next_car_fight_ts()
    if not fight_times:
        return original_ts

    adjusted_ts = original_ts
    window_sec = 600 # 10 分鐘
    
    for f_ts in fight_times:
        # 檢查是否落在 [f_ts - 10min, f_ts + 10min] 區間
        if (f_ts - window_sec) <= adjusted_ts <= (f_ts + window_sec):
            new_ts = f_ts + window_sec + 60 # 延後到戰鬥結束後的 11 分鐘
            print(f"[CarFight] 偵測到喚醒衝突 (戰鬥結束: {datetime.datetime.fromtimestamp(f_ts)})")
            print(f"           原定: {datetime.datetime.fromtimestamp(adjusted_ts)}")
            print(f"           調整為避開區間後: {datetime.datetime.fromtimestamp(new_ts)}")
            adjusted_ts = new_ts
            # 調整後需要重新檢查是否又撞到下一個戰鬥，所以繼續迴圈
                
    return adjusted_ts