"""設定讀寫路由 blueprint（device config GET/POST、OCR config GET/POST）。"""
from flask import Blueprint, jsonify, request

import config_manager
from control_panel.shared.auth import require_admin, require_device_access
from game_actions import special_wanshen

bp = Blueprint("config", __name__)


@bp.route("/api/config/<ip>", methods=["GET"])
def get_device_conf(ip):
    """獲取指定設備的設定 (raw dict, includes non-typed keys like `carpark`,
    `statue_weekly`, `experimental_cocos_navigation`).

    Uses `get_device_config_dict` rather than `get_device_config`: the latter
    returns a `DeviceConfig` dataclass whose `_extra` field jsonify-leaks as a
    nested `_extra` object instead of flattening, hiding fields from frontend
    code that reads `config.<key>` directly.
    """
    # 處理遠端 IP 設定讀取 (需要從檔名反推)
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    return jsonify(config_manager.get_device_config_dict(real_ip))


@bp.route("/api/config/<ip>", methods=["POST"])
def set_device_conf(ip):
    """更新指定設備的設定"""
    try:
        data = request.json
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        config_manager.update_device_config(real_ip, data)
        # 設定可能改到 online_check_target_pid（決定 account_online 徽章對應的
        # roleId）。狀態頁 _device_role_id 有 lru_cache，不清會顯示舊 roleId 直到
        # 重啟。best-effort 清快取；失敗只影響徽章即時性，不擋設定寫入。
        try:
            from control_panel.routes_status import _device_role_id
            _device_role_id.cache_clear()
        except Exception:
            pass
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/special_wanshen/<ip>", methods=["GET", "POST"])
def special_wanshen_config(ip):
    """讀寫萬神專用排程開關；只有明確標記的帳號可使用。"""
    require_device_access(ip)
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    cfg = config_manager.get_device_config(real_ip)
    if not cfg.get("special_wanshen_account", False):
        return jsonify({
            "status": "error",
            "message": "此帳號不是萬神專用帳號",
        }), 403

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload.get("enabled"), bool):
            return jsonify({
                "status": "error",
                "message": "enabled 必須是布林值",
            }), 400
        enabled = payload["enabled"]
        update = {"special_wanshen_enabled": enabled}
        if enabled:
            # 專用腳本開啟時，同時確保裝置執行緒會被掃描器啟動。
            update["enabled"] = True
        config_manager.update_device_config(real_ip, update)
        cfg = config_manager.get_device_config(real_ip)

    return jsonify(special_wanshen.get_status(real_ip, cfg=cfg))


@bp.route("/api/ocr_config", methods=["GET"])
def get_ocr_conf():
    """獲取 OCR 全域設定（含位置/重試/伺服器）。"""
    try:
        return jsonify(config_manager.get_ocr_config())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/ocr_config", methods=["POST"])
@require_admin
def set_ocr_conf():
    """更新 OCR 全域設定（動態生效，所有讀取端下次呼叫即使用新值）。僅限管理員。"""
    try:
        data = request.json or {}
        config_manager.update_ocr_config(data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
