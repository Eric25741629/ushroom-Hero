"""總後台 API blueprint（``admin_console``）— 帳號審核 / CRUD / 可見裝置 / 主機角色。

全部路由掛 ``@require_admin``（非管理員一律 403）。回應統一：
- 成功 ``{"status":"ok", ...}``
- 失敗 ``{"status":"error","message":...}``，HTTP 400（store 層回錯誤字串時）

主機角色（host_role）覆寫僅由本 API 寫入 ``dashboard_settings``；``config_manager``
實際消費覆寫是 Task 5 的事。因此本 API 的 ``effective`` 由
``config_manager.get_global_config()`` 疊上覆寫值自行組出，回報「將會生效」的值。
"""
from flask import Blueprint, jsonify, request

import config_manager
from control_panel.shared.auth import require_admin
from utils import dashboard_settings as ds

bp = Blueprint("admin_console", __name__)


def _ok(**extra):
    return jsonify({"status": "ok", **extra})


def _err(message, code=400):
    return jsonify({"status": "error", "message": message}), code


def _body():
    return request.get_json(silent=True) or {}


@bp.route("/api/admin/accounts", methods=["GET"])
@require_admin
def list_accounts():
    return _ok(accounts=ds.list_accounts())


@bp.route("/api/admin/accounts", methods=["POST"])
@require_admin
def create_account():
    b = _body()
    err = ds.create_account(
        b.get("username") or "",
        b.get("password") or "",
        bool(b.get("is_admin")),
        b.get("visible_devices") or [],
    )
    if err is not None:
        return _err(err)
    return _ok()


@bp.route("/api/admin/accounts/<username>", methods=["DELETE"])
@require_admin
def delete_account(username):
    err = ds.delete_account(username)
    if err is not None:
        return _err(err)
    return _ok()


@bp.route("/api/admin/accounts/<username>/password", methods=["POST"])
@require_admin
def set_password(username):
    err = ds.set_password(username, _body().get("password") or "")
    if err is not None:
        return _err(err)
    return _ok()


@bp.route("/api/admin/accounts/<username>/visible_devices", methods=["POST"])
@require_admin
def set_visible_devices(username):
    err = ds.set_visible_devices(username, _body().get("visible_devices") or [])
    if err is not None:
        return _err(err)
    return _ok()


@bp.route("/api/admin/accounts/<username>/admin", methods=["POST"])
@require_admin
def set_admin(username):
    err = ds.set_admin(username, bool(_body().get("is_admin")))
    if err is not None:
        return _err(err)
    return _ok()


@bp.route("/api/admin/accounts/<username>/approve", methods=["POST"])
@require_admin
def approve_account(username):
    # store 的 approve/reject 對不存在帳號回 None（沉默），故先自行檢查存在性。
    if not any(a["username"] == username for a in ds.list_accounts()):
        return _err("帳號不存在")
    ds.approve_account(username, _body().get("visible_devices") or [])
    return _ok()


@bp.route("/api/admin/accounts/<username>/reject", methods=["POST"])
@require_admin
def reject_account(username):
    if not any(a["username"] == username for a in ds.list_accounts()):
        return _err("帳號不存在")
    ds.reject_account(username)
    return _ok()


@bp.route("/api/admin/pending_count", methods=["GET"])
@require_admin
def pending_count():
    return _ok(count=ds.pending_count())


def _host_settings_has_key(hostname, key):
    """raw config 的 host_settings 是否有（大小寫不敏感）本機且該 entry 帶 ``key``。"""
    try:
        host_settings = config_manager.load_config().get("global", {}).get(
            "host_settings", {}
        )
    except Exception:
        return False
    hostname_upper = hostname.strip().upper()
    for hkey, entry in host_settings.items():
        if hkey.strip().upper() == hostname_upper:
            return isinstance(entry, dict) and key in entry
    return False


@bp.route("/api/admin/host_role", methods=["GET"])
@require_admin
def get_host_role():
    override = ds.get_host_role()
    hostname = config_manager.get_hostname()
    base = config_manager.get_global_config()

    if override is not None:
        source = "override"
        mode = override.get("mode")
        master_url = override.get("master_url")
    else:
        mode = base.get("mode")
        master_url = base.get("master_url")
        source = "host_settings" if _host_settings_has_key(hostname, "mode") else "default"

    return _ok(
        hostname=hostname,
        effective={"mode": mode, "master_url": master_url, "source": source},
        override=override,
    )


@bp.route("/api/admin/host_role", methods=["POST"])
@require_admin
def set_host_role():
    b = _body()
    mode = b.get("mode") or None
    master_url = b.get("master_url") or None
    ds.set_host_role(mode, master_url)
    return _ok(note="重啟 new_main_v2.py 後生效")
