import json
import os
import threading
import socket
import copy
import logging
from typing import Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILE = str(Path(__file__).resolve().parent / "bot_config.json")
_config_lock = threading.Lock()

# 預設的設備設定模板
DEFAULT_DEVICE_CONFIG = {
    "name": "",  # 自定義別名 (例如: 主力機)
    "backend": "adb",  # adb / web_h5
    "backend_display_id": "",  # display/config binding id (empty => use device key)
    "web_url": "",
    "web_canvas_selector": "canvas",
    "web_profile_dir": "playwright_profile/{device_id}",
    "web_state_file": "auth_state/{device_id}.json",
    "web_channel": "chrome",
    "web_headless": False,
    "web_clear_cookies_on_start": False,
    "web_viewport_width": 540,
    "web_viewport_height": 960,
    "web_manual_viewport_width": 0,  # 手動開啟視窗寬度 (0=使用 web_viewport_width)
    "web_manual_viewport_height": 0,  # 手動開啟視窗高度 (0=使用 web_viewport_height)
    "web_stop_mode": "keep_page",  # keep_page / blank / close_browser
    "enable_farm": True,  # 啟用農場
    "enable_arena": True,  # 啟用競技場
    "enable_mining": True,  # 啟用挖礦
    "enable_dungeon": True,  # 啟用副本(地獄/萬神)
    "enable_shop_manager": True,  # 啟用購物管家
    "enable_dungeon_manager": True,  # 啟用副本管家
    "is_real_phone": False,  # 是否為實體機/特殊機型 (原本的 fc65396d 邏輯)
    "keep_screen_on": False,  # 是否保持螢幕開啟 (不鎖屏)
    "screenshot_debug": False,  # 是否開啟截圖除錯
    "online_check_interval": 5,  # 偵測到上線後的避讓休眠時間 (分鐘)
    "lamp_check_interval": 2,  # 開神燈/點金的間隔時間 (小時)
    "lamp_duration_sec": 300,  # 每次開神燈任務執行的總秒數
    "mining_duration_min": 6,  # ?????????????
}

# OCR 全域設定
DEFAULT_OCR_CONFIG = {
    "servers": [
        "http://100.64.0.5:5001",
        "http://100.64.0.7:5001",
        "http://localhost:5001",
    ],
    "server_mode": "main",  # main / backup / auto
    "timeout_sec": 20,
    "img_decode_retries": 3,
    "ocr_empty_retries": 2,
    "retry_delay_sec": 0.6,
    # 可選：全域預設 OCR 擷取區域（給前端可填寫）
    "default_region_enabled": False,
    "default_x_range": [0, 0],
    "default_y_range": [0, 0],
}

# 預設的全域設定 (包含多主機範例)
DEFAULT_GLOBAL_CONFIG = {
    # 預設值 (當找不到特定主機設定時使用)
    "mode": "master",
    "master_url": "http://127.0.0.1:5002",
    "worker_id": "unknown_worker",
    "worker_sync_timeout_sec": 10.0,
    "worker_sync_failure_backoff_sec": 6.0,
    "ocr": copy.deepcopy(DEFAULT_OCR_CONFIG),
    # 針對特定電腦名稱的設定 (解決 NAS 共用檔案問題)
    # 格式: "COMPUTER_NAME": { 設定覆蓋 }
    "host_settings": {
        "DESKTOP-OV0ASQ4": {
            "mode": "worker",
            "master_url": "https://mushroom1_dashboard.infinite25741629.uk",
            "worker_id": "desktop_ov0asq4",
        },
        "YOUR-DORM-PC-NAME": {"mode": "master"},
        "YOUR-LAPTOP-NAME": {
            "mode": "worker",
            "master_url": "http://127.0.0.1:5002",
            "worker_id": "laptop_worker",
        },
    },
}


def get_hostname() -> str:
    return socket.gethostname()


def load_config() -> Dict[str, Any]:
    """讀取完整設定檔，並自動補全缺失的欄位"""
    with _config_lock:
        if not os.path.exists(CONFIG_FILE):
            # 初始化預設設定
            default = {"devices": {}, "global": copy.deepcopy(DEFAULT_GLOBAL_CONFIG)}
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(default, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"[Config] Failed to create default config: {e}")
            return default

        try:
            # Accept UTF-8 with or without BOM to avoid parser failures after external edits.
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            # --- 自動補全邏輯 ---
            changed = False

            # 1. 補全 global
            if "global" not in data:
                data["global"] = DEFAULT_GLOBAL_CONFIG.copy()
                changed = True
            else:
                # 補全 global 內部的缺失鍵值 (例如 master_url, worker_id)
                for k, v in DEFAULT_GLOBAL_CONFIG.items():
                    if k not in data["global"]:
                        data["global"][k] = copy.deepcopy(v)
                        changed = True

                # 2. 補全 host_settings
                if "host_settings" not in data["global"]:
                    data["global"]["host_settings"] = DEFAULT_GLOBAL_CONFIG[
                        "host_settings"
                    ].copy()
                    changed = True
                else:
                    # 補全缺少的主機設定，避免共用設定檔被舊內容覆蓋後失效
                    for host_name, host_cfg in DEFAULT_GLOBAL_CONFIG[
                        "host_settings"
                    ].items():
                        if host_name not in data["global"]["host_settings"]:
                            data["global"]["host_settings"][host_name] = host_cfg.copy()
                            changed = True

                # 3. 補全 OCR 設定
                if "ocr" not in data["global"] or not isinstance(
                    data["global"].get("ocr"), dict
                ):
                    data["global"]["ocr"] = copy.deepcopy(DEFAULT_OCR_CONFIG)
                    changed = True
                else:
                    ocr_cfg = data["global"]["ocr"]
                    for ok, ov in DEFAULT_OCR_CONFIG.items():
                        if ok not in ocr_cfg:
                            ocr_cfg[ok] = copy.deepcopy(ov)
                            changed = True

            # 如果有修補，寫回檔案
            if changed:
                try:
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                    logger.info("[Config] 已自動補全缺失的設定欄位")
                except Exception as e:
                    print(f"[Config] 寫回設定失敗: {e}")

            return data
        except Exception as e:
            print(f"[Config] 讀取失敗: {e}")
            return {"devices": {}, "global": copy.deepcopy(DEFAULT_GLOBAL_CONFIG)}


def get_global_config() -> Dict[str, Any]:
    """
    獲取當前電腦的全域設定。
    優先順序: host_settings[hostname] > global 預設值
    """
    config = load_config()
    global_cfg = config.get("global", copy.deepcopy(DEFAULT_GLOBAL_CONFIG))

    hostname = get_hostname().strip()
    host_settings = global_cfg.get("host_settings", {})

    # 取出預設值 (移除 host_settings 避免混淆)
    final_cfg = {k: v for k, v in global_cfg.items() if k != "host_settings"}

    # 如果有針對這台電腦的設定，進行覆蓋
    matched_key = None
    if hostname in host_settings:
        matched_key = hostname
    else:
        hostname_upper = hostname.upper()
        for key in host_settings.keys():
            if key.strip().upper() == hostname_upper:
                matched_key = key
                break

    if matched_key is not None:
        final_cfg.update(host_settings[matched_key])

    return final_cfg


def get_ocr_config() -> Dict[str, Any]:
    """取得 OCR 全域設定（含預設補齊）。"""
    global_cfg = get_global_config()
    raw = global_cfg.get("ocr", {}) if isinstance(global_cfg, dict) else {}

    merged = copy.deepcopy(DEFAULT_OCR_CONFIG)
    if isinstance(raw, dict):
        merged.update(raw)

    # 基本防呆
    if not isinstance(merged.get("servers"), list):
        merged["servers"] = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

    merged["servers"] = [
        str(s).strip().rstrip("/") for s in merged["servers"] if str(s).strip()
    ]
    mode = str(merged.get("server_mode", "main")).strip().lower()
    merged["server_mode"] = mode if mode in {"main", "backup", "auto"} else "main"
    if not merged["servers"]:
        merged["servers"] = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

    return merged


def update_ocr_config(new_settings: Dict[str, Any]):
    """更新 OCR 全域設定（寫入 bot_config.json 的 global.ocr）。"""
    config = load_config()
    if "global" not in config:
        config["global"] = copy.deepcopy(DEFAULT_GLOBAL_CONFIG)

    current = config["global"].get("ocr", copy.deepcopy(DEFAULT_OCR_CONFIG))
    if not isinstance(current, dict):
        current = copy.deepcopy(DEFAULT_OCR_CONFIG)

    current.update(new_settings or {})

    # 防呆處理
    servers = current.get("servers", [])
    if isinstance(servers, str):
        servers = [s.strip() for s in servers.splitlines() if s.strip()]
    if not isinstance(servers, list):
        servers = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

    current["servers"] = [str(s).strip().rstrip("/") for s in servers if str(s).strip()]
    mode = str(current.get("server_mode", "main")).strip().lower()
    current["server_mode"] = mode if mode in {"main", "backup", "auto"} else "main"
    if not current["servers"]:
        current["servers"] = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

    def _to_int(v, d):
        try:
            return int(v)
        except Exception:
            return d

    def _to_float(v, d):
        try:
            return float(v)
        except Exception:
            return d

    current["timeout_sec"] = max(
        1, _to_int(current.get("timeout_sec"), DEFAULT_OCR_CONFIG["timeout_sec"])
    )
    current["img_decode_retries"] = max(
        1,
        _to_int(
            current.get("img_decode_retries"), DEFAULT_OCR_CONFIG["img_decode_retries"]
        ),
    )
    current["ocr_empty_retries"] = max(
        0,
        _to_int(
            current.get("ocr_empty_retries"), DEFAULT_OCR_CONFIG["ocr_empty_retries"]
        ),
    )
    current["retry_delay_sec"] = max(
        0.0,
        _to_float(
            current.get("retry_delay_sec"), DEFAULT_OCR_CONFIG["retry_delay_sec"]
        ),
    )
    current["default_region_enabled"] = bool(
        current.get("default_region_enabled", False)
    )

    for key in ["default_x_range", "default_y_range"]:
        val = current.get(key, [0, 0])
        if not isinstance(val, list) or len(val) != 2:
            val = [0, 0]
        current[key] = [_to_int(val[0], 0), _to_int(val[1], 0)]

    config["global"]["ocr"] = current
    save_config(config)
    print("[Config] 已更新 OCR 全域設定")


def save_config(config: Dict[str, Any]):
    """寫入設定檔"""
    with _config_lock:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Config] 寫入失敗: {e}")


def get_device_config(ip: str) -> Dict[str, Any]:
    """獲取單一設備的設定，如果不存在則返回預設值"""
    config = load_config()
    devices = config.get("devices", {})

    # 如果該 IP 沒有設定，回傳預設值 (但不存入，直到使用者修改)
    if ip not in devices:
        default = DEFAULT_DEVICE_CONFIG.copy()
        default["name"] = ip  # 預設名稱就是 IP
        return default

    # 確保舊的設定檔也有新的欄位 (Migration)
    user_config = devices[ip]
    merged_config = DEFAULT_DEVICE_CONFIG.copy()
    merged_config.update(user_config)

    return merged_config


def update_device_config(ip: str, new_settings: Dict[str, Any]):
    """Update per-device config with validation/sanitization."""
    config = load_config()
    if "devices" not in config:
        config["devices"] = {}

    current = config["devices"].get(ip, DEFAULT_DEVICE_CONFIG.copy())
    current.update(new_settings or {})

    def _to_int(v, d):
        try:
            return int(v)
        except Exception:
            return d

    def _to_bool(v, d=False):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            text = v.strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
        return d

    backend = str(current.get("backend", "adb")).strip().lower()
    current["backend"] = backend if backend in {"adb", "web_h5"} else "adb"
    current["backend_display_id"] = str(current.get("backend_display_id", "")).strip()
    current["web_url"] = str(current.get("web_url", "")).strip()
    current["web_canvas_selector"] = (
        str(current.get("web_canvas_selector", "canvas")).strip() or "canvas"
    )
    current["web_profile_dir"] = (
        str(current.get("web_profile_dir", "playwright_profile/{device_id}")).strip()
        or "playwright_profile/{device_id}"
    )
    current["web_state_file"] = (
        str(current.get("web_state_file", "auth_state/{device_id}.json")).strip()
        or "auth_state/{device_id}.json"
    )
    current["web_channel"] = (
        str(current.get("web_channel", "chrome")).strip() or "chrome"
    )
    current["web_headless"] = _to_bool(current.get("web_headless", False), False)
    current["web_clear_cookies_on_start"] = _to_bool(
        current.get("web_clear_cookies_on_start", False), False
    )
    current["web_viewport_width"] = max(
        200, min(4096, _to_int(current.get("web_viewport_width"), 540))
    )
    current["web_viewport_height"] = max(
        200, min(4096, _to_int(current.get("web_viewport_height"), 960))
    )
    current["web_manual_viewport_width"] = max(
        0, min(4096, _to_int(current.get("web_manual_viewport_width"), 0))
    )
    current["web_manual_viewport_height"] = max(
        0, min(4096, _to_int(current.get("web_manual_viewport_height"), 0))
    )
    stop_mode = str(current.get("web_stop_mode", "keep_page")).strip().lower()
    # Supported aliases:
    # - keep_page: keep browser/page open after app_stop
    # - blank: navigate to about:blank on app_stop
    # - close / close_page / close_browser: close browser context on app_stop
    allowed_stop_modes = {"keep_page", "blank", "close", "close_page", "close_browser"}
    current["web_stop_mode"] = (
        stop_mode if stop_mode in allowed_stop_modes else "keep_page"
    )

    current["online_check_interval"] = max(
        1,
        min(
            60,
            _to_int(
                current.get("online_check_interval"),
                DEFAULT_DEVICE_CONFIG["online_check_interval"],
            ),
        ),
    )
    current["lamp_check_interval"] = max(
        1,
        min(
            24,
            _to_int(
                current.get("lamp_check_interval"),
                DEFAULT_DEVICE_CONFIG["lamp_check_interval"],
            ),
        ),
    )
    current["lamp_duration_sec"] = max(
        30,
        min(
            3600,
            _to_int(
                current.get("lamp_duration_sec"),
                DEFAULT_DEVICE_CONFIG["lamp_duration_sec"],
            ),
        ),
    )
    current["mining_duration_min"] = max(
        1,
        min(
            60,
            _to_int(
                current.get("mining_duration_min"),
                DEFAULT_DEVICE_CONFIG["mining_duration_min"],
            ),
        ),
    )

    for k in [
        "enable_farm",
        "enable_arena",
        "enable_mining",
        "enable_dungeon",
        "enable_shop_manager",
        "enable_dungeon_manager",
        "is_real_phone",
        "keep_screen_on",
        "screenshot_debug",
    ]:
        if k in current:
            current[k] = bool(current[k])

    current["name"] = str(current.get("name", ip)).strip() or ip

    config["devices"][ip] = current
    save_config(config)
    print(f"[Config] Updated device config: {ip} ({current.get('name')})")


def get_flag(ip: str, key: str, default=False) -> bool:
    """
    [給邏輯層使用] 快速獲取某個開關的狀態
    用法: if config_manager.get_flag(ip, 'is_real_phone'): ...
    """
    cfg = get_device_config(ip)
    return cfg.get(key, default)
