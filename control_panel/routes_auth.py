"""統一登入 / 申請帳號 / 登出的 blueprint（``auth_pages``）。

- ``GET/POST /login``  帳密驗證（僅 active 帳號可登入）；成功寫 session、redirect ``/``
- ``GET/POST /apply``  訪客申請帳號（寫成 pending，待管理員審核）；同 IP 簡易 rate limit
- ``GET /logout``      清空 session、redirect ``/login``

這些路由都在 ``check_request_auth`` 的豁免清單內，故本身不受登入牆影響。
"""
import time

from flask import Blueprint, redirect, render_template, request, session

from utils import dashboard_settings as ds

bp = Blueprint("auth_pages", __name__)

# 同 IP 申請 rate limit：60 秒窗內超過 5 次回 429（擋自動化灌帳號）。
_APPLY_WINDOW_SEC = 60
_APPLY_MAX_PER_WINDOW = 5
_apply_hits: dict[str, list[float]] = {}


def _frontend_version() -> str:
    # 延遲載入避免 blueprint 匯入順序耦合。
    from control_panel.routes_pages import _get_frontend_version

    return _get_frontend_version()


def _rate_limited(ip: str) -> bool:
    """記錄一次來自 ``ip`` 的申請並回傳是否已超出窗內上限。"""
    now = time.time()
    hits = [t for t in _apply_hits.get(ip, []) if now - t < _APPLY_WINDOW_SEC]
    hits.append(now)
    _apply_hits[ip] = hits
    return len(hits) > _APPLY_MAX_PER_WINDOW


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        acct = ds.verify_login(username, password)
        if acct is not None:
            session["dash_user"] = acct["username"]
            session["dash_admin"] = bool(acct.get("is_admin"))
            return redirect("/")
        return render_template(
            "login.html",
            error="帳號或密碼錯誤，或帳號尚未通過審核",
            frontend_version=_frontend_version(),
        )
    return render_template("login.html", frontend_version=_frontend_version())


@bp.route("/apply", methods=["GET", "POST"])
def apply():
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if _rate_limited(ip):
            return render_template(
                "apply.html",
                error="申請過於頻繁，請稍後再試",
                frontend_version=_frontend_version(),
            ), 429
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        err = ds.create_application(username, password)
        if err is not None:
            return render_template(
                "apply.html", error=err, frontend_version=_frontend_version()
            )
        return render_template(
            "apply.html",
            success="申請已送出，待管理員審核通過後即可登入",
            frontend_version=_frontend_version(),
        )
    return render_template("apply.html", frontend_version=_frontend_version())


@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")
