import logging
import re
import time
import cv2
import numpy as np
import json
import os
from pathlib import Path
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

logger = logging.getLogger(__name__)


from json_manager import create_store_manager, _atomic_write_json
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
def cut_img(raw_img):
    gray = cv2.cvtColor(raw_img, cv2.COLOR_BGR2GRAY)
    # 1) 二值化（反相，讓線/字變白）
    bw = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,
        31, 10
)

    # 2) 只保留水平線：水平 kernel 越寬，越能抓長橫線
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (80, 1))
    h_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel, iterations=1)

    # 3) 把線加粗一點（方便找輪廓）
    h_lines = cv2.dilate(h_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)

    # 4) 找水平線輪廓
    cnts, _ = cv2.findContours(h_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    lines = []
    H, W = bw.shape
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        # 篩選：要夠長、夠扁（你可依畫面調）
        if w > 0.6 * W and h <= 8:
            lines.append((y, x, x+w))

    # 5) 依 y 排序並做去重（同一條線可能被切成多段）
    lines.sort(key=lambda t: t[0])
    ys = []
    for y, x1, x2 in lines:
        if not ys or abs(y - ys[-1]) > 10:   # 10px 內視為同一條
            ys.append(y)

    pad_top = 8
    pad_bottom = 8

    # 例如：切分線之間的區域
    segments = []
    for i in range(len(ys) - 1):
        y1 = max(0, ys[i] + pad_top)
        y2 = min(H, ys[i+1] - pad_bottom)
        if y2 - y1 > 20:
            roi = raw_img[y1:y2, :]
            segments.append(roi)
    return segments

def _save_ocr_fail(block_img, who_text, time_text, reason, idx):
    try:
        project_root = Path(__file__).resolve().parent
        base_dir = project_root / "ocr_fails" / "fight_car"
        os.makedirs(base_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        prefix = f"row_{idx:02d}_{ts}"
        img_path = str(base_dir / f"{prefix}.png")
        meta_path = str(base_dir / f"{prefix}.json")

        if block_img is not None and block_img.size > 0:
            cv2.imwrite(img_path, block_img)

        meta = {
            "reason": reason,
            "who_text": who_text,
            "time_text": time_text,
            "timestamp": ts
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ==========================================
# 2. 區塊解析邏輯
# ==========================================
def parse_log_blocks(device, rounds=3):
    print(f"\n--- 開始解析日誌 (物理位置排序版) ---")
    
    all_spots_data = {} 
    
    ROI_Y_START = 200
    ROI_Y_END = 777
    BLOCK_HEIGHT = 160 
    STEP = 60          
    
    for round_i in range(rounds):
        print(f"--- 第 {round_i + 1} / {rounds} 輪 ---")
        
        raw_img = device.screenshot(format='opencv')

        if raw_img is None: continue
        cut_imgs = cut_img(raw_img[ROI_Y_START:ROI_Y_END, :])
        if not cut_imgs:
            print("未能切出任何區塊，跳過本輪。")
            continue

        for idx, block_img in enumerate(cut_imgs):
            print(f"解析區塊 {idx + 1} / {len(cut_imgs)} ...")
            who_fight_X_start = 141
            who_fight_X_end = 387
            what_time_X_start = 391
            what_time_X_end = 481

            who_fight_result = None
            what_time_X_result = None
            try:
                who_fight_result = img_tools.analyze_skill_via_http(
                    preprocess_for_ocr(block_img[:, who_fight_X_start:who_fight_X_end])
                )
            except Exception:
                pass
            try:
                what_time_X_result = img_tools.analyze_skill_via_http(
                    preprocess_for_ocr(block_img[:, what_time_X_start:what_time_X_end])
                )
            except Exception:
                pass

            print("-----")
            who_fight_combined_text = ""
            if who_fight_result and isinstance(who_fight_result, dict):
                for r in who_fight_result.get('ocr_results', []):
                    who_fight_combined_text += r.get('text', '')
            print(f"Row {idx:02d} OCR Result: {who_fight_combined_text}")

            raw_str = ""
            if what_time_X_result and isinstance(what_time_X_result, dict):
                texts = [r.get('text', '') for r in what_time_X_result.get('ocr_results', [])]
                raw_str = " ".join(texts).strip()

            if raw_str:
                norm_raw = (raw_str.replace('Ｏ', '0')
                                     .replace('O', '0')
                                     .replace('o', '0')
                                     .replace('S', '5')
                                     .replace('s', '5'))
                time_match = re.search(r'(\d{2}:\d{2}:\d{2})', norm_raw)
                if time_match:
                    t_str = time_match.group(1)
                    try:
                        now = datetime.now()
                        dt_obj = datetime(now.year, now.month, now.day,
                                          int(t_str[:2]), int(t_str[3:5]), int(t_str[6:]))
                        if dt_obj > now and (dt_obj - now).total_seconds() > 3600:
                            dt_obj -= timedelta(days=1)
                        print(f"匹配成功！轉換後的物件: {dt_obj}")
                        print(f"年份: {dt_obj.year}, 小時: {dt_obj.hour}")
                    except ValueError as e:
                        print(f"匹配失敗，格式不符: {e}")
                        _save_ocr_fail(block_img, who_fight_combined_text, raw_str, "time_parse_error", idx)
                else:
                    print("匹配失敗，格式不符: 無法擷取時間")
                    _save_ocr_fail(block_img, who_fight_combined_text, raw_str, "time_missing", idx)
            else:
                print("匹配失敗，格式不符: 空字串")
                _save_ocr_fail(block_img, who_fight_combined_text, raw_str, "time_empty", idx)

            spot_name = None
            m_spot = re.search(r'跨界車位\s*(\d{1,2})', who_fight_combined_text)
            if m_spot:
                spot_name = f"跨界車位{m_spot.group(1)}"

            attacker = "Unknown"
            m_att = re.search(r'[sS]\s*(\d{4})', who_fight_combined_text)
            if m_att:
                attacker = m_att.group(1)

            if not spot_name:
                _save_ocr_fail(block_img, who_fight_combined_text, raw_str, "spot_missing", idx)

            if spot_name and raw_str:
                norm_raw = (raw_str.replace('Ｏ', '0')
                                     .replace('O', '0')
                                     .replace('o', '0')
                                     .replace('S', '5')
                                     .replace('s', '5'))
                time_match = re.search(r'(\d{2}:\d{2}:\d{2})', norm_raw)
                if time_match:
                    t_str = time_match.group(1)
                    try:
                        now = datetime.now()
                        event_dt = datetime(now.year, now.month, now.day,
                                            int(t_str[:2]), int(t_str[3:5]), int(t_str[6:]))
                        if event_dt > now and (event_dt - now).total_seconds() > 3600:
                            event_dt -= timedelta(days=1)
                    except ValueError:
                        event_dt = None
                else:
                    event_dt = None

                if event_dt is not None and spot_name not in all_spots_data:
                    end_dt = event_dt + timedelta(hours=4)
                    end_str = end_dt.strftime("%H:%M:%S")

                    print(f"  [抓到了] {spot_name} (s{attacker}) | 結束: {end_str}")
                    all_spots_data[spot_name] = {
                        'end_time': end_str,
                        'attacker': attacker
                    }
                elif event_dt is None:
                    _save_ocr_fail(block_img, who_fight_combined_text, raw_str, "event_dt_none", idx)
        
        if round_i < rounds - 1:
            device.swipe(86, 754, 86, 200, duration=0.5)
            device.click(95, 215)
            time.sleep(1.0)
            
    return all_spots_data
def _extract_spatial_geometry(ocr_data):
    """
    改良版：
    1) 行文字用空格 join，避免 車位 + 時間 黏在一起
    2) spot(車位) 只用「左側欄位文字」解析，排除時間/日期欄
    3) 車位 regex 避免吃到 21:xx:xx 或 2026/01/21
    4) sID 只收 4 位數，避免 [S146] 這種殘缺
    5) attacker 優先從含 成功/搶 的行取最左邊 id
    """
    if not ocr_data or 'ocr_results' not in ocr_data:
        return {}

    # --- helper ---
    def _norm_text(t: str) -> str:
        if not t:
            return ""
        # 常見 OCR 混淆：O->0（只做輕量處理）
        t = t.replace('Ｏ', '0').replace('O', '0').replace('o', '0')
        return t

    def _extract_spot_num(text: str):
        # 只允許 1~2 位數，且後面不能接 : 或 /（避免抓到 21:34:10 或 2026/01/21）
        # 也支援「的跨界車位10」「跨界車位 10」
        m = re.search(r'跨界車位\s*(\d{1,2})(?!\s*[:/])', text)
        if not m:
            # 有時 OCR 會少了「跨界」兩字
            m = re.search(r'車位\s*(\d{1,2})(?!\s*[:/])', text)
        if not m:
            return None
        try:
            n = int(m.group(1))
            if 0 < n < 100:
                return n
        except Exception as e:
            logger.warning(f"[fight_car] 例外: {e}")
            return None
        return None

    def _parse_dt_from_text(text: str):
        # 先進行標準化，處理 OCR 常見錯誤
        text = text.replace('.', ':').replace(',', ':').replace('Ｏ', '0').replace('O', '0')
        
        # 先嘗試完整日期時間
        dt_match = re.search(r'(\d{4}/\d{2}/\d{2}).*?(\d{2}:\d{2}:\d{2})', text)
        if dt_match:
            full_str = f"{dt_match.group(1)} {dt_match.group(2)}"
            try:
                return datetime.strptime(full_str, "%Y/%m/%d %H:%M:%S")
            except Exception as e:
                logger.warning(f"[fight_car] 例外: {e}")
                pass

        # 只有時間就用今天/昨天推定
        t_match = re.search(r'(\d{2}:\d{2}:\d{2})', text)
        if t_match:
            try:
                now = datetime.now()
                t_str = t_match.group(1)
                dt = datetime(now.year, now.month, now.day,
                              int(t_str[:2]), int(t_str[3:5]), int(t_str[6:]))
                if dt > now and (dt - now).total_seconds() > 3600:
                    dt -= timedelta(days=1)
                return dt
            except Exception as e:
                logger.warning(f"[fight_car] 例外: {e}")
                pass
        return None

    # --- collect elements ---
    elements = []
    for item in ocr_data['ocr_results']:
        text = _norm_text(item.get('text', ''))
        bbox = item.get('bbox', [0, 0, 0, 0])
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        elements.append({'text': text, 'cx': cx, 'cy': cy})

    if not elements:
        return {}

    # --- group into lines by cy ---
    elements.sort(key=lambda k: k['cy'])
    lines = []
    line_merge_y = 18
    for el in elements:
        target = None
        for line in lines:
            if abs(el['cy'] - line['cy']) <= line_merge_y:
                target = line
                break
        if target:
            target['items'].append(el)
            n = len(target['items'])
            target['cy'] = (target['cy'] * (n - 1) + el['cy']) / n
        else:
            lines.append({'cy': el['cy'], 'items': [el]})
    lines.sort(key=lambda k: k['cy'])

    # --- build line_infos ---
    line_infos = []
    for line in lines:
        items = sorted(line['items'], key=lambda k: k['cx'])

        # 找時間 token 的 cx（當作右欄切分點）
        time_cx = None
        for i in items:
            if re.search(r'\d{2}:\d{2}:\d{2}', i['text']):
                time_cx = i['cx']
                break
        if time_cx is None:
            time_cx = max(i['cx'] for i in items)

        # 左側欄位：排除時間/日期欄（避免車位抓到 21:xx:xx）
        left_items = [i for i in items if i['cx'] < time_cx - 25]

        full_text = ' '.join([i['text'] for i in items]).strip()
        left_text = ' '.join([i['text'] for i in left_items]).strip()

        # 只抓 4 位 server id，並允許缺右括號（OCR 常掉）
        ids = []
        for i in left_items:
            for m in re.finditer(r'\[\s*[sS]\s*(\d{4})', i['text']):
                ids.append({'id': m.group(1), 'cx': i['cx']})
            for m in re.finditer(r'(?<!\d)[sS]\s*(\d{4})(?!\d)', i['text']):
                ids.append({'id': m.group(1), 'cx': i['cx']})

        # 去重：保留最左
        if ids:
            dedup = {}
            for it in ids:
                if it['id'] not in dedup or it['cx'] < dedup[it['id']]['cx']:
                    dedup[it['id']] = it
            ids = list(dedup.values())

        dt = _parse_dt_from_text(full_text)

        line_infos.append({
            'cy': line['cy'],
            'items': items,
            'full_text': full_text,
            'left_text': left_text,
            'time_cx': time_cx,
            'ids': sorted(ids, key=lambda k: k['cx']),
            'dt': dt
        })

    # --- find spots (車位行) ---
    spots = []
    for idx, line in enumerate(line_infos):
        spot_num = _extract_spot_num(line['left_text'])
        if spot_num is not None:
            spots.append({'num': spot_num, 'idx': idx, 'cy': line['cy']})

    if not spots:
        return {}

    # --- find times (有 dt 的行) ---
    times = [{'dt': li['dt'], 'idx': i, 'cy': li['cy'], 'time_cx': li['time_cx']}
             for i, li in enumerate(line_infos) if li['dt'] is not None]

    # --- pair spot -> dt + attacker ---
    pairs = {}
    for s in spots:
        s_idx = s['idx']
        s_cy = s['cy']

        # 1) dt：優先 spot 自己那行，其次找附近有 dt 的行
        best_dt = line_infos[s_idx]['dt']
        if best_dt is None:
            best_time = None
            min_dist = 10**9
            for t in times:
                y_diff = t['cy'] - s_cy
                if -25 < y_diff < 90:  # 同一筆紀錄通常 time 在右側同列或稍上
                    dist = abs(y_diff)
                    if dist < min_dist:
                        min_dist = dist
                        best_time = t
            if best_time is None and times:
                for t in times:
                    dist = abs(t['cy'] - s_cy)
                    if dist < min_dist:
                        min_dist = dist
                        best_time = t
            if best_time:
                best_dt = best_time['dt']

        if best_dt is None:
            continue

        # 2) attacker：優先從含「成功/搶」的行取最左 sID
        attacker = "Unknown"
        for back in (0, 1, 2):
            idx = s_idx - back
            if idx < 0:
                continue
            txt = line_infos[idx]['left_text']
            if re.search(r'成功|搶', txt):
                ids_here = line_infos[idx]['ids']
                if ids_here:
                    attacker = ids_here[0]['id']  # 最左
                    break

        # fallback：如果上面找不到，就用 spot 行本身最左 id
        if attacker == "Unknown" and line_infos[s_idx]['ids']:
            attacker = line_infos[s_idx]['ids'][0]['id']

        spot_name = f"跨界車位{s['num']}"
        pairs[spot_name] = {'dt': best_dt, 'attacker': attacker}

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
    time.sleep(0.3)
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

        # 判斷是否需要每週重置（每週一中午之後的第一次執行）
        try:
            TPE = timezone(timedelta(hours=8))
            now_tpe = datetime.now(TPE)
            today_str = now_tpe.strftime("%Y-%m-%d")

            do_weekly_reset = False
            if now_tpe.weekday() == 0 and now_tpe.hour >= 12:
                last_reset = existing_data.get("_WEEKLY_RESET_DATE") if isinstance(existing_data, dict) else None
                if last_reset != today_str:
                    do_weekly_reset = True

            if do_weekly_reset:
                # 清掉舊資料，保留重置標記
                existing_data = {}
                existing_data["_WEEKLY_RESET_DATE"] = today_str
        except Exception:
            # 若時區計算失敗，退回為只做增量更新
            pass

        # 使用 dict.update 進行「增量更新」
        if not isinstance(existing_data, dict):
            existing_data = {}
        existing_data.update(log_result)

        # 記錄最後更新時間（便於檢查）
        existing_data["_LAST_UPDATE_AT"] = current_time_str

        # 原子寫回（temp + os.replace），固定路徑不適用 JsonDataManager。
        _atomic_write_json(output_path, existing_data, indent=2, ensure_ascii=False)

        print(f"\n=== 資料已更新至 {output_path} ({current_time_str}) ===")
    except Exception as e:
        print(f"寫入檔案失敗: {e}")
    time.sleep(1)
if __name__ == "__main__":
    devices = '7fe98fc6'  # 可改為 None 或空字串以處理所有連接裝置
    d = u2.connect(devices)
    if not devices:
        print("未偵測到任何連接的 Android 裝置。請確保已啟用 USB 偵錯模式並正確連接裝置。")
    else:
        print(f"\n=== 處理裝置: {devices} ===")
        flush_logs(d)
