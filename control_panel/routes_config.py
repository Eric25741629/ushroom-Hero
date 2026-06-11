"""設定讀寫路由 blueprint（device config GET/POST、OCR config GET/POST）。"""
from flask import Blueprint, jsonify, request

import config_manager

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
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/ocr_config", methods=["GET"])
def get_ocr_conf():
    """獲取 OCR 全域設定（含位置/重試/伺服器）。"""
    try:
        return jsonify(config_manager.get_ocr_config())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/ocr_config", methods=["POST"])
def set_ocr_conf():
    """更新 OCR 全域設定（動態生效，所有讀取端下次呼叫即使用新值）。"""
    try:
        data = request.json or {}
        config_manager.update_ocr_config(data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
