"""狀態查詢路由 blueprint（OCR 健康檢查 / 程式資訊 / analyze_stage / device_data / daily_progress / status）。"""
import base64
import datetime
import logging
import os
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from flask import Blueprint, jsonify, request

import bot_state
import config_manager
import json_manager
import new_cnn.cnn_model as cnn_model_module
from game_state.detector import stage_by_str

logger = logging.getLogger(__name__)

bp = Blueprint("status", __name__)

# flask-sock 實例由 façade 在 Sock(app) 建好後透過 init_ws(sock) 注入；
# flask_sock 缺席時為 None。get_status 用它判斷 live_view 是否可用。
sock = None


def init_ws(sock_instance):
    """façade 在 Sock(app) 建好後呼叫，注入 sock 供 get_status 判斷 live_view 可用性。"""
    global sock
    sock = sock_instance


def check_ocr_server():
    """Check OCR server health based on current OCR config (main/backup/auto)."""
    try:
        ocr_cfg = config_manager.get_ocr_config()
        mode = str(ocr_cfg.get("server_mode", "main")).strip().lower()
        servers = [
            str(s).strip().rstrip("/")
            for s in ocr_cfg.get("servers", [])
            if str(s).strip()
        ]

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


# 全域模型快取 (避免重複載入)
_cached_models = {"cnn": None}

_PROGRAM_FIX_NOTE_FALLBACK = "(無法讀取最新 commit message)"
_PROGRAM_INFO_CACHE: dict = {"value": None, "expires_at": 0.0}
_PROGRAM_INFO_TTL_SEC = 30.0


def _get_program_info():
    """右上角程式資訊：版本 / git 時間 / 最新修補摘要。

    `fix_note` 取最新 commit 的 subject line，這樣每次 git commit 之後
    儀表板自動反映新內容（30 秒 TTL，避免把每次頁面渲染都付一次 git fork 成本）。
    """
    now = time.time()
    cached = _PROGRAM_INFO_CACHE.get("value")
    if cached and now < _PROGRAM_INFO_CACHE.get("expires_at", 0):
        return cached

    repo_root = Path(__file__).resolve().parents[1]
    version = "dev"
    git_time = "unknown"
    fix_note = _PROGRAM_FIX_NOTE_FALLBACK

    # Windows 預設 locale 常是 cp950 (zh-TW)，git 輸出 UTF-8 bytes 撞 cp950
    # 解碼會 raise UnicodeDecodeError，讓整段資訊全變 fallback。明確指定
    # encoding="utf-8" + errors="replace" 才是穩定做法。
    def _git(args):
        import shutil
        git_exe = shutil.which("git") or r"C:\Program Files\Git\mingw64\bin\git.exe"
        return subprocess.run(
            [git_exe, "-c", "i18n.logOutputEncoding=UTF-8", *args],
            cwd=str(repo_root),
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()

    try:
        short_sha = _git(["rev-parse", "--short", "HEAD"])
        if short_sha:
            version = f"git-{short_sha}"
    except Exception as exc:
        logger.warning("[program_info] read short_sha failed: %s", exc)

    try:
        commit_time = _git(["show", "-s", "--format=%cd", "--date=iso-local", "HEAD"])
        if commit_time:
            git_time = commit_time
    except Exception as exc:
        logger.warning("[program_info] read commit_time failed: %s", exc)

    try:
        commit_subject = _git(["show", "-s", "--format=%s", "HEAD"])
        if commit_subject:
            fix_note = commit_subject
    except Exception as exc:
        logger.warning("[program_info] read commit_subject failed: %s", exc)

    info = {
        "version": version,
        "git_time": git_time,
        "fix_note": fix_note,
    }
    _PROGRAM_INFO_CACHE["value"] = info
    _PROGRAM_INFO_CACHE["expires_at"] = now + _PROGRAM_INFO_TTL_SEC
    return info


@bp.route("/api/analyze_stage", methods=["POST"])
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

        return jsonify({"success": True, "stage": stage, "ocr_text": ocr_result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/device_data/<ip>", methods=["GET"])
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


@bp.route("/api/daily_progress/<ip>", methods=["GET"])
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
            "挖礦": {"key": ["挖礦", "挖礦"]},
            "地獄之門": {"key": "地獄之門"},
            "萬神試煉": {"key": "萬神試煉"},
            "家族任務": {"key": ["family_market_timestamp", "donate_family"]},
            "商店購買": {"key": "Store"},
            "每日任務": {"key": "mission_timestamp"},
            "坐騎衝刺": {"key": "衝刺-發條", "cycle": ("衝刺-發條", 4)},
            "菇菇武道會": {
                "key": "mushroom_arena_daily",
                "cycle": ("mushroom_arena_cycle_start", 4),
            },
            "航海": {"key": "sea_last_execution", "cycle": ("sea_cycle_start", 4)},
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
                            today = datetime.datetime.now(manager.timezone).strftime(
                                "%Y-%m-%d"
                            )
                            if record_date == today:
                                return True
                        except Exception:
                            pass
            return False

        for display_name, config in tasks_config.items():
            # 1. 檢查週期 (如果有的話)
            if "cycle" in config:
                record_name, weeks = config["cycle"]
                should_exec, _ = json_manager._should_execute_cycle(
                    real_device_id, record_name, cycle_weeks=weeks
                )
                if not should_exec:
                    continue  # 本週不執行，直接隱藏

            # 2. 檢查今日是否完成
            results[display_name] = check_is_today(config["key"])

        return jsonify(results)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/status")
def get_status():
    states = bot_state.get_all_states()
    ocr_alive = check_ocr_server()
    ocr_runtime = {}
    try:
        from img_tools import get_ocr_runtime_status

        ocr_runtime = get_ocr_runtime_status()
    except Exception:
        ocr_runtime = {}
    live_view_enabled = bool(
        (config_manager.get_global_config().get("live_view") or {}).get("enabled", False)
    )
    for ip, info in states.items():
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        cfg = config_manager.get_device_config(real_ip)
        info["name"] = cfg.get("name") or real_ip
        info["enabled"] = bool(cfg.get("enabled", True))
        info["is_real_phone"] = cfg.get("is_real_phone", False)
        info["backend"] = cfg.get("backend", "adb")
        info["ws_enabled"] = bool((cfg.get("ws_token") or {}).get("enabled"))
        info["web_stop_mode"] = cfg.get("web_stop_mode", "keep_page")
        info["mining_planner_version"] = cfg.get("mining_planner_version", "v4")
        info["live_view_available"] = bool(
            live_view_enabled
            and info["backend"] == "web_h5"
            and cfg.get("web_debug_port")
            and sock is not None
        )

    # 把「已停用且還沒跑起來」的裝置 (剛註冊、尚未啟用) 也補進來，否則它沒有
    # runtime state → 不會出現在儀表板 → 使用者找不到卡片去按「啟用」。
    # 缺 enabled 鍵的舊裝置視為已啟用,不在此補 (僅在實際有 thread 時才顯示)。
    try:
        all_devices = config_manager.load_config().get("devices", {}) or {}
    except Exception:
        all_devices = {}
    seen_ids = set(states.keys())
    seen_ids |= {k.split(":")[-1] if ":" in k else k for k in states.keys()}
    for dev_id, dcfg in all_devices.items():
        if dev_id in seen_ids:
            continue
        if bool((dcfg or {}).get("enabled", True)):
            continue  # 只補明確停用的
        states[dev_id] = {
            "name": (dcfg or {}).get("name") or dev_id,
            "enabled": False,
            "backend": (dcfg or {}).get("backend", "adb"),
            "is_real_phone": bool((dcfg or {}).get("is_real_phone", False)),
            "status": "DISABLED",
            "task": "未啟用",
            "step": "登入並設定完成後,按「啟用掛機」才會開始",
            "logs": [],
            "live_view_available": False,
        }

    return jsonify(
        {"bots": states, "ocr_server": ocr_alive, "ocr_runtime": ocr_runtime}
    )
