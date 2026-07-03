"""總後台 API blueprint（``admin_console``）— 帳號審核 / CRUD / 可見裝置 / 主機角色。

全部路由掛 ``@require_admin``（非管理員一律 403）。回應統一：
- 成功 ``{"status":"ok", ...}``
- 失敗 ``{"status":"error","message":...}``，HTTP 400（store 層回錯誤字串時）

主機角色（host_role）覆寫僅由本 API 寫入 ``dashboard_settings``；``config_manager``
實際消費覆寫是 Task 5 的事。因此本 API 的 ``effective`` 由
``config_manager.get_global_config()`` 疊上覆寫值自行組出，回報「將會生效」的值。
"""
from flask import Blueprint, jsonify, render_template, request

import config_manager
from control_panel.shared.auth import require_admin
from utils import dashboard_settings as ds

bp = Blueprint("admin_console", __name__)


@bp.route("/admin", methods=["GET"])
@require_admin
def admin_settings_page():
    """總後台設定頁（管理員專屬）。非管理員由 ``require_admin`` redirect ``/``。"""
    from control_panel.routes_pages import _get_frontend_version

    return render_template(
        "admin_settings.html", frontend_version=_get_frontend_version()
    )


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


_VALID_MODES = ("master", "worker")


@bp.route("/api/admin/host_role", methods=["GET"])
@require_admin
def get_host_role():
    """回報主機角色。

    ``effective`` 疊算方式對齊 Task 5 語意：覆寫的 key 只在「有值（truthy）」時才
    蓋過 base，否則落回 ``config_manager.get_global_config()`` 的值。故部分覆寫
    （例如只設 mode）不會把另一 key 洗成 null。``source`` 為單一字串、以 ``mode``
    key 為判準：覆寫存在且至少一個 truthy key 時為 "override"；否則若 raw
    ``host_settings`` 本機 entry 帶 mode 為 "host_settings"；再否則 "default"。
    """
    override = ds.get_host_role() or {}
    hostname = config_manager.get_hostname()
    base = config_manager.get_global_config()

    mode = override.get("mode") or base.get("mode")
    master_url = override.get("master_url") or base.get("master_url")

    has_truthy_override = bool(override.get("mode") or override.get("master_url"))
    if has_truthy_override:
        source = "override"
    elif _host_settings_has_key(hostname, "mode"):
        source = "host_settings"
    else:
        source = "default"

    return _ok(
        hostname=hostname,
        effective={"mode": mode, "master_url": master_url, "source": source},
        override=ds.get_host_role(),
    )


@bp.route("/api/admin/host_role", methods=["POST"])
@require_admin
def set_host_role():
    b = _body()
    mode = b.get("mode") or None
    master_url = b.get("master_url") or None
    if mode is not None and mode not in _VALID_MODES:
        return _err("mode 只能是 master 或 worker")
    ds.set_host_role(mode, master_url)
    return _ok(note="重啟 new_main_v2.py 後生效")
