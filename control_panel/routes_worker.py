"""Master/Worker 同步路由 (poll_commands/refresh_devices/report_status/register)。

純 code-motion，自 control_panel_app.py 搬出，行為不變。

⚠ 晚綁定規則：呼叫 ``_push_to_worker_webhook`` / ``_push_remote_command_if_possible``
與 web-login helper (``_run_web_login_worker`` / ``_normalize_web_login_state`` /
``_web_login_lock``) 時，一律透過 façade 模組 ``control_panel_app`` 屬性查找，
因為 tests 會 monkeypatch façade 上的這些名字。

共用佇列狀態 (``_remote_commands`` / ``_global_commands`` /
``_worker_webhook_endpoints`` / ``_commands_lock``) 來自唯一真相
``control_panel.shared.command_queue``，不自建副本。
"""
import threading
import time

from flask import Blueprint, jsonify, request

import bot_state
import config_manager  # 新增設定管理器
from control_panel.shared.command_queue import (
    _commands_lock,
    _global_commands,
    _remote_commands,
    _worker_webhook_endpoints,
)

bp = Blueprint("worker_sync", __name__)

_state_maintenance_started = False


@bp.route("/api/poll_commands", methods=["POST"])
def poll_commands():
    """Worker 輪詢指令"""
    try:
        data = request.json
        worker_id = data.get("worker_id")
        ips = data.get("ips", [])

        # --- 1+2 在同一把鎖內完成快照，避免讀寫競態 ---
        with _commands_lock:
            response_cmds = {}
            for ip in ips:
                remote_id = f"{worker_id}:{ip}"
                if remote_id in _remote_commands:
                    response_cmds[ip] = _remote_commands[remote_id].copy()
                    # 對於 skip_sleep 這種一次性指令，讀取後刪除
                    if "skip_sleep" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["skip_sleep"]
                    if "recover" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["recover"]
                    if "manual_release" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["manual_release"]
                    if "force_sleep" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["force_sleep"]
                    if "wake_delay_sec" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["wake_delay_sec"]

            refresh_snapshot = _global_commands["refresh_needed"]

        return jsonify(
            {
                "status": "ok",
                "commands": response_cmds,
                "global_commands": {
                    "refresh_needed": refresh_snapshot
                },
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/refresh_devices", methods=["POST"])
def refresh_devices():
    """觸發所有端 (Master & Worker) 重新掃描 ADB"""
    print("[Master] 正在重置 ADB Server 以刷新裝置列表...")
    try:
        # 執行 ADB 重啟
        import subprocess

        subprocess.run(["adb", "kill-server"], check=False)
        time.sleep(1)
        subprocess.run(["adb", "start-server"], check=False)
        time.sleep(2)
        print("[Master] ADB Server 已重啟")
    except Exception as e:
        print(f"[Master] 重啟 ADB Server 失敗: {e}")

    # 1. 通知本地 Master
    bot_state.set_refresh_needed()

    # 2. 通知所有遠端 Worker
    import control_panel_app as _cpa
    with _commands_lock:
        _global_commands["refresh_needed"] = True
        worker_ids_snapshot = list(_worker_webhook_endpoints.keys())
    for worker_id in worker_ids_snapshot:
        _cpa._push_to_worker_webhook(
            worker_id,
            {
                "worker_id": worker_id,
                "commands": {},
                "global_commands": {"refresh_needed": True},
            },
        )

    # 3. 設定一個計時器，15 秒後把全域刷新 Flag 關掉 (確保所有 Worker 都有機會讀到)
    def reset_flag():
        time.sleep(15)
        with _commands_lock:
            _global_commands["refresh_needed"] = False
        print("[Master] 全域刷新 Flag 已重置")

    threading.Thread(target=reset_flag, daemon=True).start()

    return jsonify({"status": "ok", "message": "已觸發全域設備掃描"})


# --- Worker 回報 API ---
@bp.route("/api/report_status", methods=["POST"])
def report_status():
    """接收 Worker 回報的狀態"""
    try:
        data = (
            request.json
        )  # { "worker_id:ip": {status...}, "__CMD__": {...}, "__META__": {...} }
        if not data:
            return jsonify({"status": "empty"})

        meta = data.get("__META__", {})
        if "__META__" in data:
            del data["__META__"]
        if isinstance(meta, dict):
            worker_id = str(meta.get("worker_id") or "").strip()
            webhook_url = str(meta.get("webhook_url") or "").strip()
            if worker_id and webhook_url.startswith(("http://", "https://")):
                with _commands_lock:
                    _worker_webhook_endpoints[worker_id] = {
                        "url": webhook_url,
                        "updated_at": time.time(),
                    }

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
                paused=state_update.get("paused"),  # 接收暫停狀態
                status=state_update.get("status"),
            )

            # 如果有 Log 或截圖平均時間，透過公開 accessor 更新（已含鎖）
            bot_state.update_remote_metrics(
                remote_id,
                logs=state_update["logs"] if ("logs" in state_update and state_update["logs"]) else None,
                avg_screenshot_ms=state_update["avg_screenshot_ms"] if "avg_screenshot_ms" in state_update else None,
            )

        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/devices/register", methods=["POST"])
def register_device():
    """Create a new web_h5 device entry and immediately start the manual login flow."""
    import re

    import control_panel_app as _cpa
    try:
        data = request.get_json(silent=True) or {}
        device_id = str(data.get("device_id", "")).strip()
        if not device_id:
            return jsonify({"status": "error", "message": "device_id is required"}), 400
        if not re.match(r'^[A-Za-z0-9_\-\.]+$', device_id):
            return jsonify({"status": "error", "message": "device_id 只能包含英數字、底線、連字號、點"}), 400

        # 顯示名稱允許任意文字（含中文）；裝置 ID 因用於檔案路徑/URL 維持英數字。
        name = str(data.get("name", "")).strip() or device_id
        web_url = str(data.get("web_url", "")).strip() or "https://mushroomh5.acenetgame.com/"

        # 新裝置一律以「停用」建立:登入瀏覽器開著時若掃描器又開一條掛機 thread，
        # 兩個 Playwright session 會互搶。使用者登入完、把設定填好後，再到儀表板
        # 卡片手動「啟用」才會被掃描啟動。
        new_settings = {
            "name": name,
            "backend": "web_h5",
            "web_url": web_url,
            "enabled": False,
        }
        # 自動分配 CDP port（live view / WS 工具都依賴 web_debug_port）；
        # 重複註冊已有 port 的裝置則保留原值。
        devices = config_manager.load_config().get("devices", {})
        if not devices.get(device_id, {}).get("web_debug_port"):
            used = set()
            for dev in devices.values():
                try:
                    used.add(int(dev.get("web_debug_port") or 0))
                except (TypeError, ValueError):
                    pass
            port = 9223
            while port in used:
                port += 1
            new_settings["web_debug_port"] = port
        config_manager.update_device_config(device_id, new_settings)

        with _cpa._web_login_lock:
            state = _cpa._normalize_web_login_state(device_id)
            if state.get("running"):
                return jsonify({"status": "busy", "message": "web login is already running"}), 409

        login_payload = {
            "web_url": web_url,
            "prefer_existing_state": False,
            "backup_before_open": False,
        }
        t = threading.Thread(
            target=_cpa._run_web_login_worker,
            args=(device_id, login_payload),
            daemon=True,
            name=f"web-login-{device_id}",
        )
        t.start()
        return jsonify({"status": "ok", "device_id": device_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
