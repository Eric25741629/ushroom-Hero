"""狀態查詢路由 blueprint（OCR 健康檢查 / 程式資訊 / analyze_stage / daily_progress / status）。"""
import base64
import datetime
import functools
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
from control_panel.shared.auth import filter_visible_states, require_device_access
from game_actions import special_wanshen
from game_state.detector import stage_by_str
from utils import battle_speed

logger = logging.getLogger(__name__)

bp = Blueprint("status", __name__)

# flask-sock 實例由 façade 在 Sock(app) 建好後透過 init_ws(sock) 注入；
# flask_sock 缺席時為 None。get_status 用它判斷 live_view 是否可用。
sock = None


def init_ws(sock_instance):
    """façade 在 Sock(app) 建好後呼叫，注入 sock 供 get_status 判斷 live_view 可用性。"""
    global sock
    sock = sock_instance


# /api/status 每 2 秒被 dashboard 輪詢；health 探測若逐台同步等 timeout，
# 死掉的主 server 會讓「每一次」輪詢都卡住（實測 2s+）。TTL 快取 + 記住
# 上次成功的 server 優先探測 + 短 timeout，把 stall 壓到只剩快取過期那次。
_OCR_HEALTH_CACHE = {"ok": False, "expires_at": 0.0}
_OCR_HEALTH_TTL_SEC = 30.0
_OCR_HEALTH_TIMEOUT_SEC = 0.5
_OCR_LAST_GOOD = {"base": None}


def check_ocr_server():
    """Check OCR server health based on current OCR config (main/backup/auto)."""
    now = time.time()
    if now < _OCR_HEALTH_CACHE["expires_at"]:
        return _OCR_HEALTH_CACHE["ok"]
    ok = _probe_ocr_servers()
    _OCR_HEALTH_CACHE["ok"] = ok
    _OCR_HEALTH_CACHE["expires_at"] = now + _OCR_HEALTH_TTL_SEC
    return ok


def _probe_ocr_servers():
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
        else:
            # main / auto / 其餘皆同一優先序（保留 mode 只為 backup 分支）
            priority = [main, backup, local]

        for s in servers:
            if s not in priority:
                priority.append(s)

        last_good = _OCR_LAST_GOOD["base"]
        if last_good in priority:
            priority.remove(last_good)
            priority.insert(0, last_good)

        for base in priority:
            try:
                response = requests.get(
                    f"{base}/health", timeout=_OCR_HEALTH_TIMEOUT_SEC
                )
                if response.status_code == 200:
                    _OCR_LAST_GOOD["base"] = base
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


def _fmt_ts(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime(
            "%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return None


@bp.route("/api/carpark/<ip>", methods=["GET"])
def get_carpark(ip):
    """跨界車位快照 (唯讀 ws_state，bot 的 WS 階段每輪寫入)。

    顯示當前在停跨界車 + 各車 start_time / 已停時長 / 距 8h 自動收回，
    以及下次重停喚醒時刻。同時做 start_time epoch 校準判斷 (--parked 的
    dashboard 版)。**不從面板主動 WS 登入** (會踢掉裝置 session)。
    """
    require_device_access(ip)
    real = ip.split(":")[-1] if ":" in ip else ip
    try:
        cfg = config_manager.get_device_config(real) or {}
    except Exception as exc:  # noqa: BLE001
        return jsonify({"enabled": False, "error": str(exc)})
    plan = (cfg.get("ws_token") or {}).get("carpark_plan") or {}
    if not plan.get("enabled"):
        return jsonify({"enabled": False})

    try:
        from ws_token import state as ws_state
        snap = (ws_state.load_state(real) or {}).get("carpark_repark") or {}
    except Exception as exc:  # noqa: BLE001 — advisory display only
        return jsonify({"enabled": True, "captured": False, "error": str(exc)})

    if not snap:
        return jsonify({"enabled": True, "captured": False,
                        "note": "尚無快照 (bot 還沒跑過 carpark 任務)"})

    now = time.time()
    park_max = float(snap.get("park_max") or 28800)
    offset = int(snap.get("offset") or 0)
    cars = []
    worst_remaining = None
    epoch_sane = True
    for c in (snap.get("cars") or []):
        start = int(c.get("start_time") or 0) + offset
        elapsed = now - start
        remaining = park_max - elapsed
        # unix-epoch sanity: a real parked car has elapsed in [~0, park_max].
        sane = (-600 <= elapsed <= park_max + 3600)
        epoch_sane = epoch_sane and sane
        cars.append({
            "mount_id": c.get("mount_id"), "master_id": c.get("master_id"),
            "pos": c.get("pos"), "start_time": c.get("start_time"),
            "start_dt": _fmt_ts(start), "elapsed_h": round(elapsed / 3600, 2),
            "remaining_h": round(remaining / 3600, 2), "epoch_sane": sane,
        })
        if worst_remaining is None or remaining < worst_remaining:
            worst_remaining = remaining

    return jsonify({
        "enabled": True, "captured": True,
        "window": snap.get("window"), "target": snap.get("target"),
        "current": len(cars), "cars": cars,
        "next_ts": snap.get("next_ts"), "next_dt": _fmt_ts(snap.get("next_ts")),
        "captured_ts": snap.get("captured_ts"),
        "captured_dt": _fmt_ts(snap.get("captured_ts")),
        "park_max_h": round(park_max / 3600, 2), "offset": offset, "now": now,
        "worst_remaining_h": (round(worst_remaining / 3600, 2)
                              if worst_remaining is not None else None),
        "epoch_sane": epoch_sane if cars else None,
    })


def _record_is_today(manager, data, key_or_list):
    """某筆當日紀錄是否落在今天。支援三種 on-disk schema：

    1. dict 含 ``timestamp`` / ``date``：走 ``JsonDataManager.is_same_day``
       （Store / farm_plant_click / donate_family …）。
    2. dict 含 ``last_time`` 字串："YYYY-MM-DD HH:MM:SS"（family_market_timestamp）。
    3. **flat scalar** float/int：``mission_timestamp``（Mission.py 直接存數字，
       不可巢狀化）。``is_same_day`` 只認 dict，對 scalar 永遠回 False，所以這裡
       自行用 timezone 比對日期 —— 否則「每日任務」徽章即使今天剛完成也顯示 ⏳。

    任一 key 命中即回 True（家族任務用雙 key）。
    """
    keys = key_or_list if isinstance(key_or_list, list) else [key_or_list]
    today = datetime.datetime.now(manager.timezone).strftime("%Y-%m-%d")
    for key in keys:
        # schema 1: dict timestamp/date — 既有讀側
        if manager.is_same_day(key):
            return True
        rec = data.get(key)
        # schema 3: flat scalar timestamp（mission_timestamp）
        if isinstance(rec, (int, float)) and not isinstance(rec, bool) and rec > 0:
            try:
                rec_date = datetime.datetime.fromtimestamp(
                    float(rec), manager.timezone
                ).strftime("%Y-%m-%d")
                if rec_date == today:
                    return True
            except (OSError, OverflowError, ValueError):
                pass
        # schema 2: dict last_time 字串
        if isinstance(rec, dict):
            last_time = rec.get("last_time")
            if last_time:
                try:
                    if last_time.split(" ")[0] == today:
                        return True
                except Exception:
                    pass
    return False


# 每日進度徽章追蹤的任務。config 格式：
#   {"key": json_key|[keys]}                 → 點亮看「今日是否完成」(check_is_today)
#   {"cycle": (record_name, weeks)}          → 週期未到則隱藏 (坐騎/武道會)
#   {"sea_week": True} / {"triweekly": True} → 日曆檔期未到則隱藏 (航海/龍骸)
#   {"period": "week"}                        → 點亮改看「本檔期(本週)是否跑過」而非當天
# 航海 / 龍骸聖域是多週才開一檔的活動：只在「檔期週」顯示徽章；點亮看本週而非當天，
# 否則活動週跑完隔天又退回 ⏳。檔期判斷用日曆錨點(is_sea_week / _is_dragon_week)。
_DAILY_TASKS_CONFIG = {
    "農場買種": {"key": "farm_seed_purchase"},
    "農場種植": {"key": "farm_plant_click"},
    "挖礦": {"key": ["挖礦", "挖礦"]},
    "地獄之門": {"key": "地獄之門"},
    "萬神試煉": {"key": "萬神試煉", "period": "week"},
    "家族任務": {"key": ["family_market_timestamp", "donate_family"]},
    "商店購買": {"key": "Store"},
    "每日任務": {"key": "mission_timestamp"},
    "坐騎衝刺": {"key": "衝刺-發條", "cycle": ("衝刺-發條", 4), "event_open": "mount_sprint"},
    "菇菇武道會": {
        "key": "mushroom_arena_daily",
        "cycle": ("mushroom_arena_cycle_start", 4),
    },
    "航海": {"key": "sea_last_execution", "sea_week": True, "period": "week"},
    "龍骸聖域": {
        "key": "dragon_realm_last_run", "triweekly": True, "period": "week",
    },
}


def _compute_daily_progress(manager, device_id, *, today=None, now=None):
    """回傳 {display_name: 已完成?}；檔期/週期 gate 未過的任務直接省略(隱藏)。

    ``today`` 預設取 manager 時區的當天，可注入供測試。兩個日曆 gate
    (is_sea_week / _is_dragon_week) 都吃同一個 ``today`` —— 與點亮述語
    manager.is_same_week 共用 manager.timezone 時鐘，否則非 UTC+8 主機在週界
    邊緣，gate(本地)與述語(台北)可能落在不同 ISO 週，徽章顯示/點亮會錯週。
    """
    data = manager.load_data()
    if today is None:
        today = datetime.datetime.now(manager.timezone).date()
    results = {}
    for display_name, config in _DAILY_TASKS_CONFIG.items():
        # 1. gate：未到週期/檔期則隱藏
        if "cycle" in config:
            record_name, weeks = config["cycle"]
            should_exec, _ = json_manager._should_execute_cycle(
                device_id, record_name, cycle_weeks=weeks
            )
            if not should_exec:
                continue
        # 活動開放窗 gate：週期週內但活動已結算(如坐騎衝刺週三22:00後)則隱藏
        if config.get("event_open") == "mount_sprint":
            from rank_events import is_mount_sprint_open
            if not is_mount_sprint_open(now):
                continue
        if config.get("sea_week") and not json_manager.is_sea_week(today):
            continue
        if config.get("triweekly"):
            from game_actions.dragon_realm_scheduler import _is_dragon_week
            if not _is_dragon_week(today):
                continue

        # 2. 點亮：多週活動看「本週是否跑過」，其餘看「今日是否完成」
        if config.get("period") == "week":
            results[display_name] = manager.is_same_week(config["key"])
        else:
            results[display_name] = _record_is_today(manager, data, config["key"])
    return results


@bp.route("/api/daily_progress/<ip>", methods=["GET"])
def get_daily_progress(ip):
    """獲取設備的今日進度統計"""
    require_device_access(ip)
    try:
        real_device_id = ip.split(":")[-1] if ":" in ip else ip
        manager = json_manager.JsonDataManager(real_device_id)
        return jsonify(_compute_daily_progress(manager, real_device_id))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _online_monitor_status():
    """Which device currently holds the persistent online-check WS connection.

    Reads the live snapshot's ``detector`` (the account the online-monitor is
    logged in as right now to read everyone's presence). ``running: False`` when
    the monitor hasn't produced a snapshot yet.
    """
    def _name(dev):
        if not dev:
            return "—"
        try:
            return config_manager.get_device_config(dev).get("name") or dev
        except Exception:
            return dev

    try:
        from ws_token.online_monitor import (
            get_snapshot, get_last_switch, get_poll_sec)
        snap = get_snapshot()
        last = get_last_switch()
        poll = float(get_poll_sec() or 30.0)
    except Exception:
        return {"running": False}

    switch = None
    if last:
        switch = {
            "from": last.get("frm"),
            "from_name": _name(last.get("frm")),
            "to": last.get("to"),
            "to_name": _name(last.get("to")),
            "age_sec": round(time.time() - float(last.get("ts") or 0), 1),
        }

    if snap is None:
        return {"running": False, "last_switch": switch, "poll_sec": poll}
    age = time.time() - float(snap.timestamp)
    detector = snap.detector
    return {
        "running": True,
        "detector": detector,
        "detector_name": _name(detector),
        "age_sec": round(age, 1),
        "fresh": age < 60,
        "poll_sec": poll,
        "refresh_in_sec": round(max(0.0, poll - age), 1),
        "tracked": len(snap.entries),
        "last_switch": switch,
    }


@functools.lru_cache(maxsize=64)
def _device_role_id(device):
    """roleId this device represents (target_pid or creds), cached. None if unknown."""
    return config_manager.get_device_role_id(device)


def _account_presence():
    """{role_id: online} from the online-monitor snapshot (empty if unavailable).

    顯示層用 server 的 presence sentinel（last_login_ts == 0）精確判定，不吃快照
    烘入的 guard 寬限 bool（poll_friends threshold_sec=120 會讓登出後 ~2 分鐘仍
    顯示在線）。另 detector 永遠不在自己好友列表，但 monitor 正持有其 WS
    session → overlay 為在線。
    """
    try:
        from ws_token.online_monitor import get_snapshot, current_detector
        snap = get_snapshot()
    except Exception:
        return {}
    if not snap:
        return {}
    presence = {e.role_id: e.last_login_ts == 0 for e in snap.entries}
    detector = current_detector()
    if detector:
        rid = _device_role_id(detector)
        if rid is not None:
            presence[int(rid)] = True
    return presence


def _lease_fields(leases, ip, real_ip):
    """session_registry lease → /api/status 顯示欄位。key 依序試完整 ip 與 real_ip。"""
    lease = leases.get(ip) or leases.get(real_ip)
    if lease is None:
        return {"lease_owner": None, "lease_label": ""}
    return {"lease_owner": lease.owner.value, "lease_label": lease.label or ""}


def _special_wanshen_fields(real_ip, cfg):
    """把萬神專用狀態轉成 dashboard 欄位；一般帳號不讀取記錄檔。"""
    account = bool(cfg.get("special_wanshen_account", False))
    if not account:
        return {
            "special_wanshen_account": False,
            "special_wanshen_enabled": False,
            "special_wanshen_rounds": int(cfg.get("special_wanshen_rounds", 10) or 10),
            "special_wanshen_attempted_today": False,
            "special_wanshen_completed_this_week": False,
            "special_wanshen_next_attempt_at": None,
            "special_wanshen_mode": "off",
            "wanshen_until_cap": bool(cfg.get("wanshen_until_cap", False)),
        }
    status = special_wanshen.get_status(real_ip, cfg=cfg)
    return {
        "special_wanshen_account": True,
        "special_wanshen_enabled": status["enabled"],
        "special_wanshen_rounds": status["rounds"],
        "special_wanshen_attempted_today": status["attempted_today"],
        "special_wanshen_completed_this_week": status["completed_this_week"],
        "special_wanshen_next_attempt_at": status["next_attempt_at"],
        "special_wanshen_mode": status["mode"],
        "wanshen_until_cap": bool(cfg.get("wanshen_until_cap", False)),
    }


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
    presence = _account_presence()  # {role_id: online} from online-monitor
    try:
        from runtime_services import session_registry
        leases = session_registry.peek_all()
    except Exception:
        leases = {}
    for ip, info in states.items():
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        cfg = config_manager.get_device_config(real_ip)
        info["name"] = cfg.get("name") or real_ip
        rid = _device_role_id(ip)
        info["account_online"] = presence.get(rid) if rid is not None else None
        info.update(_lease_fields(leases, ip, real_ip))
        info["enabled"] = bool(cfg.get("enabled", True))
        info["is_real_phone"] = cfg.get("is_real_phone", False)
        info["backend"] = cfg.get("backend", "adb")
        info["ws_enabled"] = bool((cfg.get("ws_token") or {}).get("enabled"))
        info["carpark_plan_enabled"] = bool(
            ((cfg.get("ws_token") or {}).get("carpark_plan") or {}).get("enabled"))
        info["web_stop_mode"] = cfg.get("web_stop_mode", "keep_page")
        info["mining_planner_version"] = cfg.get("mining_planner_version", "v1")
        info["battle_speed_scale"] = battle_speed.coerce_battle_speed_scale(
            cfg.get("battle_speed_scale", 4)
        )
        info.update(_special_wanshen_fields(real_ip, cfg))
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
            "battle_speed_scale": battle_speed.coerce_battle_speed_scale(
                (dcfg or {}).get("battle_speed_scale", 4)
            ),
        }
        states[dev_id].update(_special_wanshen_fields(dev_id, dcfg or {}))

    # 出口統一過濾：非管理員只看得到自己可見的裝置（含上面 disabled 回填）。
    states = filter_visible_states(states)

    return jsonify(
        {"bots": states, "ocr_server": ocr_alive, "ocr_runtime": ocr_runtime,
         "online_monitor": _online_monitor_status()}
    )
