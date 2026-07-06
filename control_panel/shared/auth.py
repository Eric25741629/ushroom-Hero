"""dashboard 統一認證：全站 before_request 守門 + 管理員/裝置可見性 helper。

session keys：
- ``dash_user``（str）  當前登入帳號名
- ``dash_admin``（bool）是否管理員

豁免清單集中在本檔頂部，``check_request_auth`` 是唯一守門邏輯（掛在
``control_panel_app`` 的 ``@app.before_request``）。設定檔損毀時 fail-closed
回 503，絕不 fallback 成無密碼。
"""
import functools

from flask import abort, jsonify, redirect, request, session

from utils import dashboard_settings

# 免登入的頁面/端點：登入、申請、登出、favicon。
EXEMPT_PATHS = {"/login", "/apply", "/logout", "/favicon.ico"}
# 刻意公開（無需登入即可讀寫）的功能端點：坐騎追蹤頁 + 其檢視/編輯 API。
# 使用者要求「路人也能看與改」。/toggle 與 /rebootstrap 刻意不在此列——開關整個
# 掃描器 / 重建玩家庫屬管理操作，仍受全站登入牆 + @require_admin 雙重保護。
PUBLIC_PATHS = {
    "/mount-tracker",
    "/api/mount_tracker/results",
    "/api/mount_tracker/targets",
    "/api/mount_tracker/mark",
}
# 免登入的路徑前綴：靜態資產（設計系統 lib / 圖示）。
EXEMPT_PREFIXES = ("/static/",)
# worker→master 機器對機器同步端點（無瀏覽器 session，不能被登入牆擋住）。
# 注意：/api/devices/register 是瀏覽器觸發，不在此列；push server 是獨立
# port 5000 的另一個 Flask app，不受本守門影響。
MACHINE_EXEMPT_PATHS = {"/api/poll_commands", "/api/refresh_devices", "/api/report_status"}


def check_request_auth():
    """全站守門。回 ``None`` 放行；否則回 Flask response（redirect/401/503）。"""
    p = request.path
    if p in EXEMPT_PATHS or p in MACHINE_EXEMPT_PATHS or p in PUBLIC_PATHS:
        return None
    if any(p.startswith(pre) for pre in EXEMPT_PREFIXES):
        return None
    if session.get("dash_user"):
        # 已登入：對非管理員套用集中式裝置可見性守門。routing 先於
        # before_request 完成，故 ``request.view_args`` 已填。任何帶裝置 id
        # path param 的端點（全站一律命名 ``<ip>``；``device_id``/``device``
        # 為防禦性備援）不可見即 abort(403)，涵蓋所有 <ip> 端點而不需逐路由加守門。
        if not session.get("dash_admin"):
            view_args = request.view_args or {}
            for key in ("ip", "device_id", "device"):
                if key in view_args:
                    require_device_access(view_args[key])
        return None
    try:
        dashboard_settings.load_settings()
    except dashboard_settings.SettingsCorruptError:
        # fail-closed：設定損毀時一律拒絕，絕不放行成無密碼。
        return jsonify({"status": "error", "message": "settings corrupted"}), 503
    if request.is_json or p.startswith("/api/") or p.startswith("/ws/"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    return redirect("/login")


def current_user():
    """回當前登入帳號名；未登入回 ``None``。"""
    return session.get("dash_user")


def is_admin():
    """當前使用者是否為管理員。"""
    return bool(session.get("dash_admin"))


def require_admin(f):
    """限管理員存取的 decorator：非管理員 API 回 403 JSON、頁面 redirect ``/``。"""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "forbidden"}), 403
            return redirect("/")
        return f(*args, **kwargs)
    return wrapper


def current_visible_devices():
    """當前使用者可見的裝置清單。

    - 管理員回 ``None``（代表「全部可見」，呼叫端據此略過過濾）
    - 一般帳號回其 ``visible_devices`` list
    - 未登入 / 帳號查無 / 設定損毀 回空 list（fail-closed）
    """
    user = session.get("dash_user")
    if not user:
        return []
    if session.get("dash_admin"):
        return None
    try:
        data = dashboard_settings.load_settings()
    except dashboard_settings.SettingsCorruptError:
        return []
    for acct in data.get("accounts", []):
        if acct.get("username") == user:
            return list(acct.get("visible_devices", []))
    return []


def filter_visible_states(states):
    """依當前使用者可見裝置過濾 states dict（key=裝置 ip）。管理員原樣回傳。

    state key 可能是 ``worker_id:emulator-5554`` 複合形式，可見性比對一律
    正規化成真實裝置 id（``key.split(":")[-1]``）後再比。
    """
    visible = current_visible_devices()
    if visible is None:
        return states
    allowed = set(visible)
    return {
        key: st
        for key, st in (states or {}).items()
        if key.split(":")[-1] in allowed
    }


def require_device_access(ip):
    """當前使用者不可見該裝置時 ``abort(403)``。管理員一律放行。

    ``ip`` 可能是 ``worker_id:emulator-5554`` 複合形式，正規化成真實裝置
    id 後再比。403 訊息固定 ``"forbidden"``，不洩漏裝置是否存在。
    """
    visible = current_visible_devices()
    if visible is None:
        return
    if ip.split(":")[-1] not in set(visible):
        abort(403, description="forbidden")


def _fly_pet_auth(f):
    """相容 shim：保留原 decorator 介面（30+ 處 import 不用改）。

    全站已由 ``check_request_auth`` 守門，本 shim 只在裝飾點再確認一次
    ``dash_user``（改查統一 session key，不再看 legacy ``fly_pet_auth``）。
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("dash_user"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper
