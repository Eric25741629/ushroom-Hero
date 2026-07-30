"""星據車位（奇星車場）純 WebSocket 頁面與 API。

協議：
- ``server_car_info`` 12860：四個槽位、攻守冷卻。
- ``server_car_join`` 12861：加入搶佔或駐守。
- ``server_car_queue`` 12868：指定槽位的守隊配置。

頁面與 API 都不使用 Playwright/CDP。只有使用者按「載入」時，前端才呼叫
``/api/ws_session/<ip>/connect``；下列資料 API 只取用既有 session。
"""
from __future__ import annotations

import logging
import threading

from flask import Blueprint, jsonify, render_template, request

import config_manager
from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth, require_device_access
from ws_token import star_seize as ws_star_seize

bp = Blueprint("star_seize", __name__)
logger = logging.getLogger(__name__)

_device_locks: dict[str, threading.RLock] = {}
_device_locks_guard = threading.Lock()


def _device_lock(ip: str) -> threading.RLock:
    with _device_locks_guard:
        return _device_locks.setdefault(ip, threading.RLock())


def _session_client(ip: str):
    """只取用由前端「載入」建立的 session，不在 API 內暗中連線。"""
    client = ws_session.get_client(ip)
    if client is None:
        return None, (
            jsonify({"status": "error", "message": "尚未建立純 WS，請先按載入"}),
            409,
        )
    return client, None


def _resolve_my_server(cfg, raw) -> int:
    """request 參數優先，其次裝置設定，無法解析時視為未知。"""
    value = raw if raw not in (None, "") else cfg.get("star_seize_my_server")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _evaluate_seize_gate(state, pos, queue_type, my_server):
    """依 fresh server state 判斷是否能送出加入指令。"""
    if not isinstance(state, dict) or state.get("error"):
        return False, "no-state"
    server_time = state.get("serverTime")
    if server_time is None:
        return False, "no-state"
    slot = next(
        (item for item in state.get("slots") or [] if item.get("pos") == pos),
        None,
    )
    if slot is None:
        return False, "pos-not-open"

    owner = slot.get("owner") or 0
    free_end = slot.get("free_end")
    if owner == 0:
        return False, "empty"
    if queue_type == 2:
        return True, None
    if my_server > 0 and owner == my_server:
        return False, "own-server"
    if free_end is None or free_end > server_time:
        return False, "protected"
    if (state.get("attack_cd_end_time") or 0) > server_time:
        return False, "cooldown"
    taiwan_hour = ((int(server_time) + 8 * 3600) % 86400) // 3600
    if taiwan_hour >= 22 or taiwan_hour < 10:
        return False, "truce"
    return True, None


def _rpc_error(ip: str, action: str, exc: Exception):
    logger.warning("star_seize %s 失敗 ip=%s: %s", action, ip, exc)
    return jsonify({"status": "error", "message": f"{action}失敗，請重試"}), 502


@bp.route("/star-seize")
@_fly_pet_auth
def star_seize_page():
    from control_panel.routes_pages import _get_frontend_version

    return render_template("star_seize.html", frontend_version=_get_frontend_version())


@bp.route("/api/star_seize/state/<ip>", methods=["GET"])
def star_seize_state(ip):
    require_device_access(ip)
    client, err = _session_client(ip)
    if err:
        return err
    cfg = config_manager.get_device_config(ip)
    my_server = _resolve_my_server(cfg, request.args.get("my_server"))
    try:
        with _device_lock(ip):
            state = ws_star_seize.read_state(client, my_server=my_server)
        return jsonify({"status": "ok", "state": state})
    except Exception as exc:
        return _rpc_error(ip, "讀取星據狀態", exc)


@bp.route("/api/star_seize/opponent/<ip>", methods=["GET"])
def star_seize_opponent(ip):
    require_device_access(ip)
    try:
        pos = int(request.args.get("pos"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "pos 需為整數"}), 400
    if pos not in (1, 2, 3, 4):
        return jsonify({"status": "error", "message": "pos 需為 1..4"}), 400
    client, err = _session_client(ip)
    if err:
        return err
    try:
        with _device_lock(ip):
            opponent = ws_star_seize.read_opponent(client, pos)
        return jsonify({"status": "ok", "opponent": opponent})
    except Exception as exc:
        return _rpc_error(ip, "讀取守隊", exc)


@bp.route("/api/star_seize/seize/<ip>", methods=["POST"])
def star_seize_seize(ip):
    """讀 fresh 狀態驗閘，通過後只送一次 server_car_join。"""
    require_device_access(ip)
    payload = request.get_json(silent=True) or {}
    try:
        pos = int(payload.get("pos"))
        queue_type = int(payload.get("queue_type"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "pos/queue_type 需為整數"}), 400
    if pos not in (1, 2, 3, 4):
        return jsonify({"status": "error", "message": "pos 需為 1..4"}), 400
    if queue_type not in (1, 2):
        return jsonify(
            {"status": "error", "message": "queue_type 需為 1(搶佔) 或 2(駐守)"}
        ), 400

    client, err = _session_client(ip)
    if err:
        return err
    cfg = config_manager.get_device_config(ip)
    my_server = _resolve_my_server(cfg, payload.get("my_server"))
    try:
        with _device_lock(ip):
            state = ws_star_seize.read_state(client, my_server=my_server)
            ok, reason = _evaluate_seize_gate(
                state, pos, queue_type, my_server
            )
            if not ok:
                return jsonify(
                    {"status": "ok", "reply": {"ok": False, "reason": reason}}
                )
            reply = ws_star_seize.join(client, pos, queue_type)
        return jsonify({"status": "ok", "reply": reply})
    except Exception as exc:
        return _rpc_error(ip, "加入星據佇列", exc)
