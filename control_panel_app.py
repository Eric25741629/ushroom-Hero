from flask import Flask, jsonify, render_template, request, send_from_directory
import bot_state
import config_manager # 新增設定管理器
import json_manager   # 新增資料管理器
import logging
import os
from adb_operations import run_adb
import requests
import datetime
import threading
import time

# Disable Flask logs to keep console clean
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# Master 模式：存放給遠端 Worker 的指令佇列
# { "school_laptop:emulator-5554": { "paused": True, "skip_sleep": False } }
# Worker ID 是 "worker_id:ip"
_remote_commands = {} 

# Master 模式：全域指令 (發給所有 Worker)
_global_commands = {
    "refresh_needed": False
}

def check_ocr_server():
    """Check OCR server health based on current OCR config (main/backup/auto)."""
    try:
        ocr_cfg = config_manager.get_ocr_config()
        mode = str(ocr_cfg.get("server_mode", "main")).strip().lower()
        servers = [str(s).strip().rstrip('/') for s in ocr_cfg.get("servers", []) if str(s).strip()]

        main = "http://100.64.0.5:5001"
        backup = "http://100.64.0.7:5001"
        local = "http://127.0.0.1:5001"

        if mode == "backup":
            priority = [backup, main, local]
        elif mode == "auto":
            priority = [main, backup, local]
        else:
            priority = [main, backup, local]

        for s in servers:
            if s not in priority:
                priority.append(s)

        for base in priority:
            try:
                response = requests.get(f"{base}/health", timeout=2)
                if response.status_code == 200:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False

import cv2
import numpy as np
import base64
from game_state.detector import stage_by_str
import new_cnn.cnn_model as cnn_model_module

# 全域模型快取 (避免重複載入)
_cached_models = {
    "cnn": None
}

@app.route('/api/analyze_stage', methods=['POST'])
def analyze_stage():
    """集中式頁面狀態判定服務"""
    try:
        data = request.json
        img_base64 = data.get("image")
        if not img_base64:
            return jsonify({"success": False, "error": "No image data"}), 400

        # 解碼圖片
        img_bytes = base64.b64decode(img_base64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # 載入模型 (如果尚未載入)
        if _cached_models["cnn"] is None:
            from pathlib import Path
            model_path = "cnn_model.pth"
            if os.path.exists(model_path):
                _cached_models["cnn"] = cnn_model_module.load_cnn_model(model_path)
        
        # 1. 執行 OCR 獲取文字 (透過本地邏輯)
        from img_tools import get_all_text
        ocr_result = get_all_text(img)
        
        # 2. 執行判定邏輯
        # 這裡我們需要傳入一個 mock 的 device 物件，因為目前的 stage_by_str 可能會用到 d (雖然目前沒用到)
        stage = stage_by_str(None, ocr_result, img)
        
        return jsonify({
            "success": True,
            "stage": stage,
            "ocr_text": ocr_result
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

from flask import Flask, jsonify, render_template, request, send_from_directory

# ... (其餘 import 與全域變數保持不變) ...

@app.route('/')
def index():
    """主控面板首頁"""
    return render_template('dashboard.html')

@app.route('/api/poll_commands', methods=['POST'])
def poll_commands():
    """Worker 輪詢指令"""
    try:
        data = request.json
        worker_id = data.get("worker_id")
        ips = data.get("ips", [])
        
        # --- 1. 處理全域指令 ---
        global_resp = {}
        if _global_commands["refresh_needed"]:
            global_resp["refresh_needed"] = True
            # 注意：這裡不能立刻設為 False，否則其他 Worker 就收不到了
            # 我們改用時間戳或一個計數器，這裡簡化處理：讓 Worker 自己決定是否刷新
            pass

        # --- 2. 處理特定設備指令 ---
        response_cmds = {}
        # ... (中間 ips 迴圈邏輯保持不變) ...
        for ip in ips:
            remote_id = f"{worker_id}:{ip}"
            if remote_id in _remote_commands:
                response_cmds[ip] = _remote_commands[remote_id].copy()
                # 對於 skip_sleep 這種一次性指令，讀取後刪除
                if "skip_sleep" in _remote_commands[remote_id]:
                    del _remote_commands[remote_id]["skip_sleep"]
                if "recover" in _remote_commands[remote_id]:
                    del _remote_commands[remote_id]["recover"]

        return jsonify({
            "status": "ok", 
            "commands": response_cmds,
            "global_commands": {
                "refresh_needed": _global_commands["refresh_needed"]
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/refresh_devices', methods=['POST'])
def refresh_devices():
    """觸發所有端 (Master & Worker) 重新掃描 ADB"""
    print("[Master] 正在重置 ADB Server 以刷新裝置列表...")
    try:
        # 執行 ADB 重啟
        import subprocess
        subprocess.run(['adb', 'kill-server'], check=False)
        time.sleep(1)
        subprocess.run(['adb', 'start-server'], check=False)
        time.sleep(2)
        print("[Master] ADB Server 已重啟")
    except Exception as e:
        print(f"[Master] 重啟 ADB Server 失敗: {e}")

    # 1. 通知本地 Master
    bot_state.set_refresh_needed()
    
    # 2. 通知所有遠端 Worker
    _global_commands["refresh_needed"] = True
    
    # 3. 設定一個計時器，15 秒後把全域刷新 Flag 關掉 (確保所有 Worker 都有機會讀到)
    def reset_flag():
        time.sleep(15)
        _global_commands["refresh_needed"] = False
        print("[Master] 全域刷新 Flag 已重置")
        
    threading.Thread(target=reset_flag, daemon=True).start()
    
    return jsonify({"status": "ok", "message": "已觸發全域設備掃描"})

@app.route('/api/device_data/<ip>', methods=['GET'])
def get_device_data(ip):
    """讀取設備的執行紀錄 JSON (例如 emulator-5554.json)"""
    try:
        # 處理分散式架構的 IP (例如: school_laptop:emulator-5554)
        # 因為使用 SMB 共用，所有 json 都在同一個目錄下，只要還原出真實的 device_id 即可
        real_device_id = ip
        if ":" in ip:
            # 取最後一部分作為真實 ID
            real_device_id = ip.split(":")[-1]
            
        manager = json_manager.JsonDataManager(real_device_id)
        data = manager.load_data()
        return jsonify(data)
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/daily_progress/<ip>', methods=['GET'])
def get_daily_progress(ip):
    """獲取設備的今日進度統計"""
    try:
        real_device_id = ip
        if ":" in ip:
            real_device_id = ip.split(":")[-1]
            
        manager = json_manager.JsonDataManager(real_device_id)
        
        # 定義要追蹤的任務清單
        # config 格式: { "key": json_key, "cycle": (record_name, weeks) }
        tasks_config = {
            "農場買種": {"key": "farm_seed_purchase"},
            "農場種植": {"key": "farm_plant_click"},
            "挖礦": {"key": ["\u6316\u7926", "挖礦"]},
            "地獄之門": {"key": "地獄之門"},
            "萬神試煉": {"key": "萬神試煉"},
            "家族任務": {"key": ["family_market_timestamp", "donate_family"]},
            "商店購買": {"key": "Store"},
            "每日任務": {"key": "mission_timestamp"},
            "坐騎衝刺": {
                "key": "衝刺-發條",
                "cycle": ("衝刺-發條", 4)
            },
            "菇菇武道會": {
                "key": "mushroom_arena_daily",
                "cycle": ("mushroom_arena_cycle_start", 4)
            },
            "航海": {
                "key": "sea_last_execution",
                "cycle": ("sea_cycle_start", 4)
            }
        }
        
        results = {}
        data = manager.load_data()

        def check_is_today(key_or_list):
            keys = key_or_list if isinstance(key_or_list, list) else [key_or_list]
            for key in keys:
                if manager.is_same_day(key):
                    return True
                if key in data and isinstance(data[key], dict):
                    last_time = data[key].get("last_time")
                    if last_time:
                        try:
                            record_date = last_time.split(" ")[0]
                            today = datetime.datetime.now(manager.timezone).strftime("%Y-%m-%d")
                            if record_date == today:
                                return True
                        except Exception:
                            pass
            return False

        for display_name, config in tasks_config.items():
            # 1. 檢查週期 (如果有的話)
            if "cycle" in config:
                record_name, weeks = config["cycle"]
                should_exec, _ = json_manager._should_execute_cycle(real_device_id, record_name, cycle_weeks=weeks)
                if not should_exec:
                    continue # 本週不執行，直接隱藏
            
            # 2. 檢查今日是否完成
            results[display_name] = check_is_today(config["key"])
            
        return jsonify(results)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Worker 回報 API ---
@app.route('/api/report_status', methods=['POST'])
def report_status():
    """接收 Worker 回報的狀態"""
    try:
        data = request.json # { "worker_id:ip": {status...}, "__CMD__": {"action": "clear_offline"} }
        if not data: return jsonify({"status": "empty"})
        
        # 處理特殊指令
        if "__CMD__" in data:
            cmd = data["__CMD__"]
            if cmd.get("action") == "clear_offline":
                bot_state.clear_offline_devices()
            del data["__CMD__"]

        for remote_id, state_update in data.items():
            # 使用 bot_state.update_state 統一處理，這會自動處理 Lock 和 last_update
            bot_state.update_state(
                remote_id, 
                task=state_update.get("task"), 
                step=state_update.get("step"),
                next_wake_at=state_update.get("next_wake_at"),
                paused=state_update.get("paused") # 接收暫停狀態
            )
            
            # 如果有 Log，也一併更新
            if "logs" in state_update and state_update["logs"]:
                with bot_state.get_device_lock(remote_id):
                    # 這裡直接操作 _states 補上 logs
                    if remote_id in bot_state._states:
                        bot_state._states[remote_id]["logs"] = state_update["logs"]
        
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- 控制 API (修改以支援遠端) ---

def queue_command(ip, cmd_key, cmd_val):
    """將指令加入佇列 (針對遠端) 或直接執行 (針對本地)"""
    if ":" in ip:
        if ip not in _remote_commands:
            _remote_commands[ip] = {}
        _remote_commands[ip][cmd_key] = cmd_val
    else:
        # 本地設備
        if cmd_key == "paused":
            bot_state.set_pause(ip, cmd_val)
        elif cmd_key == "skip_sleep" and cmd_val:
            bot_state.set_skip_sleep(ip)
        elif cmd_key == "recover" and cmd_val:
            # 本地直接執行 ADB (recover_screen 函數會處理)
            pass

@app.route('/api/status')
def get_status():
    states = bot_state.get_all_states()
    ocr_alive = check_ocr_server()
    ocr_runtime = {}
    try:
        from img_tools import get_ocr_runtime_status
        ocr_runtime = get_ocr_runtime_status()
    except Exception:
        ocr_runtime = {}
    for ip, info in states.items():
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        cfg = config_manager.get_device_config(real_ip)
        info['name'] = (cfg.get('name') or real_ip)
        info['is_real_phone'] = cfg.get('is_real_phone', False)
    return jsonify({
        "bots": states,
        "ocr_server": ocr_alive,
        "ocr_runtime": ocr_runtime
    })

@app.route('/api/config/<ip>', methods=['GET'])
def get_device_conf(ip):
    """獲取指定設備的設定"""
    # 處理遠端 IP 設定讀取 (需要從檔名反推)
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    return jsonify(config_manager.get_device_config(real_ip))

@app.route('/api/config/<ip>', methods=['POST'])
def set_device_conf(ip):
    """更新指定設備的設定"""
    try:
        data = request.json
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        config_manager.update_device_config(real_ip, data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/ocr_config', methods=['GET'])
def get_ocr_conf():
    """獲取 OCR 全域設定（含位置/重試/伺服器）。"""
    try:
        return jsonify(config_manager.get_ocr_config())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/ocr_config', methods=['POST'])
def set_ocr_conf():
    """更新 OCR 全域設定（動態生效，所有讀取端下次呼叫即使用新值）。"""
    try:
        data = request.json or {}
        config_manager.update_ocr_config(data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/pause/<ip>', methods=['POST'])
def pause_bot(ip):
    queue_command(ip, "paused", True)
    return jsonify({"status": "ok", "action": "paused", "ip": ip})

@app.route('/api/resume/<ip>', methods=['POST'])
def resume_bot(ip):
    queue_command(ip, "paused", False)
    return jsonify({"status": "ok", "action": "resumed", "ip": ip})

@app.route('/api/skip_sleep/<ip>', methods=['POST'])
def skip_sleep(ip):
    queue_command(ip, "skip_sleep", True)
    return jsonify({"status": "ok", "action": "skip_sleep", "ip": ip})

@app.route('/api/recover/<ip>', methods=['POST'])
def recover_screen(ip):
    # 遠端設備
    if ":" in ip:
        queue_command(ip, "recover", True)
        return jsonify({"status": "ok", "action": "queued_recover", "ip": ip})

    # 本地設備
    try:
        if config_manager.get_flag(ip, 'is_real_phone') or 'fc65396d' in ip:
             run_adb('shell wm density reset && wm size reset', device_serial=ip)
             return jsonify({"status": "ok", "action": "screen_recovered", "ip": ip})
        return jsonify({"status": "error", "message": "此設備未設定為實體機/特殊機型"}), 403
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def run_server(port=5002):

    print(f"🚀 中控台網頁伺服器啟動: http://127.0.0.1:{port}")

    # 在 Thread 中運行時必須關閉 debug 和 reloader，否則會觸發訊號錯誤

    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


