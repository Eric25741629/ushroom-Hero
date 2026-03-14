import re
import time
import cv2
import numpy as np
import json
from datetime import datetime, timedelta, timezone
import json
import time
import random
import cnn_model
import torch
import uiautomator2 as u2
import easyocr
from tools import android_devices
import img_tools


from json_manager import create_store_manager
# ==========================================
# 1. 影像增強
# ==========================================
def preprocess_for_ocr(img_roi):
    if img_roi is None or img_roi.size == 0: return None
    gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
    scaled = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    binary = cv2.adaptiveThreshold(
        scaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 10
    )
    return binary

# ==========================================
# 2. 區塊解析邏輯
# ==========================================
def parse_log_blocks(device, rounds=3):
    print(f"\n--- 開始解析日誌 (物理位置排序版) ---")
    
    all_spots_data = {} 
    
    ROI_Y_START = 220
    ROI_Y_END = 850
    BLOCK_HEIGHT = 160 
    STEP = 60          
    
    for round_i in range(rounds):
        print(f"--- 第 {round_i + 1} / {rounds} 輪 ---")
        
        raw_img = device.screenshot(format='opencv')
        if raw_img is None: continue

        list_area = raw_img[ROI_Y_START:ROI_Y_END, :]
        h, w = list_area.shape[:2]
        
        for y in range(0, h - BLOCK_HEIGHT + 1, STEP):
            block_img = list_area[y : y + BLOCK_HEIGHT, :]
            processed_block = preprocess_for_ocr(block_img)
            if processed_block is None: continue
            
            try:
                ocr_result = img_tools.analyze_skill_via_http(processed_block)
            except: continue
            
            if ocr_result and isinstance(ocr_result, dict) and 'ocr_results' in ocr_result:
                
                # 使用幾何排序法解析
                block_data = _extract_spatial_geometry(ocr_result)
                
                for spot_name, info in block_data.items():
                    if spot_name not in all_spots_data:
                        event_dt = info['dt']
                        attacker = info['attacker']
                        
                        end_dt = event_dt + timedelta(hours=4)
                        end_str = end_dt.strftime("%H:%M:%S")
                        
                        print(f"  [抓到了] {spot_name} (s{attacker}) | 結束: {end_str}")
                        all_spots_data[spot_name] = {
                            'end_time': end_str,
                            'attacker': attacker
                        }
        
        if round_i < rounds - 1:
            device.swipe(86, 754, 86, 200, duration=0.5)
            device.click(95, 215)
            time.sleep(1.0)
            
    return all_spots_data

def _extract_spatial_geometry(ocr_data):
    """
    不依賴文字內容，純粹使用幾何位置 (Top-Left Rule) 來判斷攻擊者。
    """
    if not ocr_data or 'ocr_results' not in ocr_data:
        return {}

    pairs = {}
    
    spots = []      # 車位
    times = []      # 時間
    ids = []        # 所有 s數字
    
    # 1. 蒐集所有元件
    for item in ocr_data['ocr_results']:
        text = item.get('text', '')
        bbox = item.get('bbox', [0,0,0,0])
        cx = (bbox[0] + bbox[2]) / 2 
        cy = (bbox[1] + bbox[3]) / 2 
        
        # A. 找車位
        if '車位' in text:
            match = re.search(r'車位.*?(\d+)', text)
            if match:
                try:
                    num = int(match.group(1))
                    if num < 100:
                        spots.append({'name': f"跨界車位{num}", 'cy': cy, 'cx': cx})
                except: pass

        # B. 找時間
        dt_match = re.search(r'(\d{4}/\d{2}/\d{2}).*?(\d{2}:\d{2}:\d{2})', text)
        if dt_match:
             full_str = f"{dt_match.group(1)} {dt_match.group(2)}"
             try:
                 dt = datetime.strptime(full_str, "%Y/%m/%d %H:%M:%S")
                 times.append({'dt': dt, 'cy': cy, 'cx': cx})
                 continue
             except: pass
        
        t_match = re.search(r'(\d{2}:\d{2}:\d{2})', text)
        if t_match:
            try:
                now = datetime.now()
                t_str = t_match.group(1)
                dt = datetime(now.year, now.month, now.day, int(t_str[:2]), int(t_str[3:5]), int(t_str[6:]))
                if dt > now and (dt - now).total_seconds() > 3600: 
                    dt = dt - timedelta(days=1)
                times.append({'dt': dt, 'cy': cy, 'cx': cx})
            except: pass

        # C. 找所有 ID (完全不過濾內容)
        for m in re.finditer(r'\[?s(\d+)', text):
            sid = m.group(1)
            # 這裡我們不檢查 "的" 或 "搶佔"，避免誤判名字
            ids.append({'id': sid, 'cy': cy, 'cx': cx})

    # 2. 配對邏輯
    for s_item in spots:
        s_cy = s_item['cy']
        
        # --- 找時間 ---
        best_dt = None
        min_dist_t = float('inf')
        for t_item in times:
            y_diff = t_item['cy'] - s_cy
            if -20 < y_diff < 80:
                dist = abs(y_diff) + abs(t_item['cx'] - s_item['cx']) * 0.1
                if dist < min_dist_t:
                    min_dist_t = dist
                    best_dt = t_item['dt']
        
        if not best_dt: continue 
        
        # --- 找攻擊者 (Top-Left Rule) ---
        # 我們收集所有在「車位」上方或同一行的 ID
        candidates = []
        for id_item in ids:
            y_diff = s_cy - id_item['cy'] # 正數代表 ID 在車位上方
            
            # 合理範圍：同一行 (-15) 到 上兩行 (+100)
            if -15 < y_diff < 100:
                candidates.append(id_item)
        
        best_attacker = "Unknown"
        
        if candidates:
            # [核心修正]：依照 (Y, X) 排序
            # 1. Y 越小 (越上面) 越優先
            # 2. X 越小 (越左邊) 越優先
            # 這樣一定會抓到排版最前面的那個 ID，也就是主詞 (攻擊者)
            candidates.sort(key=lambda k: (k['cy'], k['cx']))
            
            # 取第一個
            top_candidate = candidates[0]
            
            # [額外檢查]：攻擊者通常在畫面左側
            # 如果抓到的 ID 在畫面很右邊 (例如 X > 250)，那可能是漏抓了攻擊者，只抓到被害者
            # 但既然我們要數字，就先回傳這個，總比 Unknown 好
            best_attacker = top_candidate['id']

        pairs[s_item['name']] = {'dt': best_dt, 'attacker': best_attacker}

    return pairs
def flush_logs(device):
    # 每週自動重置窗口檢查：若為週日20:00之後或週一12:00之前，直接 return
    try:
        TPE = timezone(timedelta(hours=8))
        now_tpe = datetime.now(TPE)
        weekday = now_tpe.weekday()  # Monday=0 .. Sunday=6
        hour = now_tpe.hour
        if (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 12):
            print(f"[flush_logs] 在每週重置窗口 ({now_tpe.strftime('%Y-%m-%d %H:%M:%S')})，直接 return，不做關閉或睡眠。")
            return
    except Exception:
        # 若時區計算失敗，繼續執行原本動作（較保守）
        pass

    # 執行掃描
    img_tools.click_str_by_server(device, '家園', y_range=(850, 959),shift_y=-50)
    img_tools.click_str_by_server(device, '菇菇車位', y_range=(451, 502), wait_timeout=10)
    img_tools.click_str_by_server(device, '找車位', y_range=(850, 950), wait_timeout=5)
    img_tools.click_str_by_server(device, '跨服車位', y_range=(700, 850), wait_timeout=5)
    img_tools.click_str_by_server(device, '跨界車位', y_range=(0, 123), wait_timeout=5,shift_y=70)
    time.sleep(2)
    log_result = parse_log_blocks(device, rounds=3)
    # [新增] 插入一筆特殊紀錄，記錄當下時間
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_result["_SYSTEM_UPDATE_TIME"] = {
        "end_time": current_time_str, # 這裡借用 end_time 欄位存時間
        "attacker": "SYSTEM"          # 標記為系統
    }
    img_tools.click_str_by_server(device, '跨界車位日誌', y_range=(0, 167), wait_timeout=5,shift_y=700)
    img_tools.click_str_by_server(device, '返回', y_range=(850, 959),shift_y=-50)
    time.sleep(0.5)
    img_tools.click_str_by_server(device, '關閉', y_range=(850, 959),shift_y=-25)

    # 寫入檔案到 push_project/web/car_fight.json（改為更新既有 JSON 而非整檔覆寫）
    try:
        output_path = "push_project/web/car_fight.json"

        # 先讀取舊資料（若檔案不存在或壞掉則視為空 dict）
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            if not isinstance(existing_data, dict):
                existing_data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            existing_data = {}

        # 使用 dict.update 進行「增量更新」
        existing_data.update(log_result)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)

        print(f"\n=== 資料已更新至 {output_path} ({current_time_str}) ===")
    except Exception as e:
        print(f"寫入檔案失敗: {e}")
    time.sleep(1)