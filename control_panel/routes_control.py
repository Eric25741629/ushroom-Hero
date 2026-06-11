"""裝置控制路由 (pause/resume/skip_sleep/wake_delay/manual_release/force_sleep/recover)。

純 code-motion，自 control_panel_app.py 搬出，行為不變。
"""
from flask import Blueprint, jsonify, request

import config_manager  # 新增設定管理器
from adb_operations import run_adb
from control_panel.shared.command_queue import (
    _is_local_command_target,
    queue_command,
)

bp = Blueprint("control", __name__)


@bp.route("/api/pause/<ip>", methods=["POST"])
def pause_bot(ip):
    queue_command(ip, "paused", True)
    return jsonify({"status": "ok", "action": "paused", "ip": ip})


@bp.route("/api/resume/<ip>", methods=["POST"])
def resume_bot(ip):
    queue_command(ip, "paused", False)
    return jsonify({"status": "ok", "action": "resumed", "ip": ip})


@bp.route("/api/skip_sleep/<ip>", methods=["POST"])
def skip_sleep(ip):
    queue_command(ip, "skip_sleep", True)
    return jsonify({"status": "ok", "action": "skip_sleep", "ip": ip})


@bp.route("/api/wake_delay/<ip>", methods=["POST"])
def wake_delay(ip):
    payload = request.get_json(silent=True) or {}
    try:
        delay_sec = float(payload.get("delay_sec", 0))
    except Exception:
        return jsonify({"status": "error", "message": "delay_sec must be a number"}), 400
    if delay_sec < 0:
        delay_sec = 0.0

    queue_command(ip, "wake_delay_sec", delay_sec)
    return jsonify(
        {
            "status": "ok",
            "action": "wake_delay",
            "ip": ip,
            "delay_sec": delay_sec,
        }
    )


@bp.route("/api/manual_release/<ip>", methods=["POST"])
def manual_release(ip):
    queue_command(ip, "manual_release", True)
    return jsonify({"status": "ok", "action": "manual_release", "ip": ip})


@bp.route("/api/force_sleep/<ip>", methods=["POST"])
def force_sleep(ip):
    queue_command(ip, "force_sleep", True)
    return jsonify({"status": "ok", "action": "force_sleep", "ip": ip})


@bp.route("/api/recover/<ip>", methods=["POST"])
def recover_screen(ip):
    # 遠端設備（本機 TCP 模擬器同樣帶冒號，必須走 _is_local_command_target 判斷）
    if not _is_local_command_target(ip):
        queue_command(ip, "recover", True)
        return jsonify({"status": "ok", "action": "queued_recover", "ip": ip})

    # 本地設備
    try:
        if config_manager.get_flag(ip, "is_real_phone") or "fc65396d" in ip:
            run_adb("shell wm density reset && wm size reset", device_serial=ip)
            return jsonify({"status": "ok", "action": "screen_recovered", "ip": ip})
        return jsonify(
            {"status": "error", "message": "此設備未設定為實體機/特殊機型"}
        ), 403
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
