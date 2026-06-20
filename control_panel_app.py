"""中控台 Flask app（façade）。

實際路由已拆分至 ``control_panel/`` 套件（2026-06-11 blueprint 重構）：

- ``control_panel/shared/``        — command queue / CDP / 飛寵認證（跨 blueprint 共用真相）
- ``control_panel/routes_pages``   — dashboard / updates / war-room / fly-pet 頁面
- ``control_panel/routes_status``  — /api/status、device_data、daily_progress、analyze_stage
- ``control_panel/routes_config``  — /api/config、/api/ocr_config
- ``control_panel/routes_control`` — pause/resume/skip_sleep/wake_delay/.../recover
- ``control_panel/routes_worker``  — poll_commands/report_status/register/refresh（master-worker 同步）
- ``control_panel/routes_web_session`` — Playwright web 登入/啟動/關閉/備份
- ``control_panel/routes_live_view``   — /ws/live_view 直播橋接
- ``control_panel/routes_labeler``     — OCR labeler / trainer
- ``control_panel/routes_fly_pet``     — 17 條 /api/fly_pet_* + cdp_evaluate

本檔職責：建立 ``app``、註冊 blueprints、初始化 flask-sock、提供 ``run_server``，
並 re-export 測試與跨模組晚綁定所依賴的符號（tests 會 monkeypatch
``control_panel_app.<符號>``，blueprint 在呼叫時透過本模組屬性查找）。
"""
import hashlib
import logging
import os

import requests  # noqa: F401 — re-exported（tests monkeypatch cpa.requests.post）
from flask import Flask, jsonify, request  # noqa: F401 — jsonify re-exported for tests

import bot_state  # noqa: F401 — re-exported（tests 透過 cpa.bot_state 取用）
import config_manager  # noqa: F401 — re-exported（tests 透過 cpa.config_manager 取用）
import json_manager  # noqa: F401 — re-exported

# Disable Flask logs to keep console clean
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
# secret_key from env (MUSHROOM_DASHBOARD_SECRET) so a forgeable static key is not
# shipped in source; falls back to the legacy constant when unset (keeps live
# sessions valid). SECURITY: set it to a long random string —
# see docs/BACKEND_ARCH_AUDIT_2026-06-21.md §3.5.
_secret_src = os.environ.get("MUSHROOM_DASHBOARD_SECRET")
app.secret_key = hashlib.sha256(
    _secret_src.encode("utf-8") if _secret_src else b"mushroom-fly-pet-dashboard-key"
).digest()

# --- shared 層 re-export（與 blueprint 共用同一批物件） ---
from control_panel.shared.auth import (  # noqa: E402,F401
    _FLY_PET_USERS,
    _fly_pet_auth,
)
from control_panel.shared.cdp import (  # noqa: E402,F401
    _FLY_PET_LOCK_JS,
    _cdp_evaluate,
    _cdp_json_response,
)
from control_panel.shared.command_queue import (  # noqa: E402,F401
    _commands_lock,
    _global_commands,
    _is_local_command_target,
    _push_remote_command_if_possible,
    _push_to_worker_webhook,
    _remote_commands,
    _worker_webhook_endpoints,
    queue_command,
)

# --- blueprints ---
from control_panel import (  # noqa: E402
    routes_ad_reward,
    routes_relic_sprint,
    routes_tools_optimize,
    routes_config,
    routes_control,
    routes_fly_pet,
    routes_inventory,
    routes_labeler,
    routes_live_view,
    routes_pages,
    routes_status,
    routes_web_session,
    routes_worker,
    ws_session,
)

for _mod in (
    routes_pages,
    routes_status,
    routes_config,
    routes_control,
    routes_worker,
    routes_web_session,
    routes_live_view,
    routes_labeler,
    routes_fly_pet,
    routes_inventory,
    routes_tools_optimize,
    routes_ad_reward,
    routes_relic_sprint,
    ws_session,
):
    app.register_blueprint(_mod.bp)

# --- blueprint 內部狀態/可 monkeypatch 符號 re-export ---
# tests 會 monkeypatch control_panel_app.<名字>；blueprint 路由在呼叫時透過
# 本模組屬性晚綁定查找，因此 patch 這裡即可生效。
_FLY_PET_ICON_DIR = routes_fly_pet._FLY_PET_ICON_DIR
_run_web_login_worker = routes_web_session._run_web_login_worker
_normalize_web_login_state = routes_web_session._normalize_web_login_state
_web_login_lock = routes_web_session._web_login_lock
_web_login_state = routes_web_session._web_login_state
_live_view_sessions = routes_live_view._live_view_sessions
_live_view_lock = routes_live_view._live_view_lock
check_ocr_server = routes_status.check_ocr_server
_get_program_info = routes_status._get_program_info
_get_frontend_version = routes_pages._get_frontend_version
_load_readme_text = routes_pages._load_readme_text
_load_update_text = routes_pages._load_update_text
DEFAULT_LABELER_UI_CONFIG = routes_labeler.DEFAULT_LABELER_UI_CONFIG

# WebSocket support for the remote live-view bridge (Approach B). Imported
# defensively: if flask-sock is not installed the dashboard still works, only the
# /ws/live_view route is unavailable.
try:
    from flask_sock import Sock

    sock = Sock(app)
except Exception as _sock_exc:  # pragma: no cover - optional dep
    sock = None
    logger.warning(f"flask-sock unavailable, live-view disabled: {_sock_exc}")

routes_live_view.init_ws(sock)
routes_status.init_ws(sock)


@app.after_request
def add_no_cache_headers(response):
    # Fly-pet icons are immutable real-sprite PNGs keyed by config_id — let the
    # browser cache them so the card wall doesn't re-fetch ~38 URLs on every scroll.
    if request.method == "GET" and not request.path.startswith("/api/fly_pet_icon/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def run_server(port=5002):

    print(f"🚀 中控台網頁伺服器啟動: http://127.0.0.1:{port}")

    # 在 Thread 中運行時必須關閉 debug 和 reloader，否則會觸發訊號錯誤
    # threaded=True：let WebSocket (live-view) connections coexist with the
    # status-polling endpoints instead of blocking the single WSGI worker.

    # Bind host from env (MUSHROOM_DASHBOARD_HOST); defaults to 0.0.0.0 for
    # backward-compat. SECURITY: set 127.0.0.1 if the dashboard need not be
    # LAN-reachable — see docs/BACKEND_ARCH_AUDIT_2026-06-21.md §3.5.
    host = os.environ.get("MUSHROOM_DASHBOARD_HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
