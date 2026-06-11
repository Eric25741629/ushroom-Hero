"""Live-view 路由 (WebSocket 串流 + stop API)。

純 code-motion，自 control_panel_app.py 搬出，行為不變。

WebSocket 路由無法掛在 Blueprint 上 — flask_sock 的 @sock.route 需要 Sock(app)
實例。façade `init_ws(sock)` 在 Sock(app) 建好後由 control_panel_app 呼叫；
flask_sock 缺席時傳 None。
"""
import json
import logging
import threading

from flask import Blueprint, jsonify

import bot_state
import config_manager  # 新增設定管理器

logger = logging.getLogger(__name__)

bp = Blueprint("live_view", __name__)

# Registry of active live-view sessions keyed by real device IP.
# Used by /api/live_view/<ip>/stop to signal teardown when WS close frame
# is not cleanly received (proxy, half-open TCP, etc.).
_live_view_sessions: dict = {}
_live_view_lock = threading.Lock()


def live_view_ws(ws, ip):
    """Stream a web_h5 device's headless Chrome to the client and forward input.

    Attaches to the device's CDP remote-debugging-port (config web_debug_port)
    as an independent raw-CDP client; does not touch the bot's Playwright object.
    """
    from urllib.parse import urlsplit

    from runtime_services.live_view_bridge import (
        LiveViewSession,
        find_game_page_target,
    )

    real_ip = ip.split(":")[-1] if ":" in ip else ip
    cfg = config_manager.get_device_config(real_ip)
    debug_port = cfg.get("web_debug_port")
    if not debug_port:
        try:
            ws.send(json.dumps({"type": "error", "message": "no web_debug_port configured"}))
        except Exception:
            pass
        return
    url_host = ""
    try:
        url_host = urlsplit(str(cfg.get("web_url", ""))).hostname or ""
    except Exception:
        url_host = ""
    try:
        vw = int(cfg.get("web_viewport_width", 540) or 540)
        vh = int(cfg.get("web_viewport_height", 960) or 960)
    except Exception:
        vw, vh = 540, 960
    # Pause automation during manual takeover so the bot does not fight the
    # user's clicks. Resumes when the live-view WS disconnects (also covers the
    # browser tab being closed). set_pause does NOT touch the browser, so unlike
    # the old web_launch path it cannot trigger a game reload / re-login.
    paused_by_live_view = False
    try:
        bot_state.set_pause(real_ip, True)
        paused_by_live_view = True
    except Exception as pause_exc:
        logger.warning(f"[live_view] pause {real_ip} failed: {pause_exc}")

    # Auto-launch fallback: if no browser is currently running on the debug port,
    # request a headless launch so the user doesn't have to press 開啟網頁 first.
    # Probe quickly — when a page target already exists we must NOT request a launch
    # (app_start would re-navigate the loaded game and force a re-login). The probe
    # preserves the no-relogin path; only the "nothing running" case auto-launches.
    # force_headful=False keeps it headless (fine on a VPS); manual_hold=False lets
    # the device thread open the browser and return immediately — live-view's
    # set_pause already holds automation during the manual takeover. The pending
    # launch request breaks through the pause via bot_state.check_pause's carve-out.
    try:
        existing_target = find_game_page_target(
            int(debug_port), url_host, timeout_sec=1.5, poll_interval=0.3
        )
        if not existing_target:
            logger.info(
                f"[live_view] {real_ip} no live page on port {debug_port}; "
                "auto-launching headless browser"
            )
            bot_state.request_web_launch(
                real_ip,
                payload={"force_headful": False, "manual_hold_until_closed": False},
            )
    except Exception as launch_exc:
        logger.warning(f"[live_view] {real_ip} auto-launch probe failed: {launch_exc}")

    # Idle auto-disconnect window (default 60 min). Keeps a forgotten manual
    # takeover from pausing the device forever; configurable via global config
    # global.live_view.idle_timeout_sec (seconds; <= 0 disables).
    idle_timeout = 3600
    try:
        lv_cfg = config_manager.get_global_config().get("live_view", {}) or {}
        idle_timeout = int(lv_cfg.get("idle_timeout_sec", 3600))
    except Exception:
        idle_timeout = 3600

    try:
        session = LiveViewSession(
            ws,
            int(debug_port),
            url_host,
            viewport_width=vw,
            viewport_height=vh,
            idle_timeout_sec=idle_timeout,
            logger=logger,
        )
        with _live_view_lock:
            _live_view_sessions[real_ip] = session
        try:
            session.run()
        finally:
            with _live_view_lock:
                _live_view_sessions.pop(real_ip, None)
    finally:
        if paused_by_live_view:
            try:
                bot_state.set_pause(real_ip, False)
            except Exception as resume_exc:
                logger.warning(f"[live_view] resume {real_ip} failed: {resume_exc}")


def init_ws(sock):
    """façade 在 Sock(app) 建好後呼叫；flask_sock 缺席時傳 None。"""
    if sock is None:
        return
    sock.route("/ws/live_view/<ip>")(live_view_ws)


@bp.route("/api/live_view/<ip>/stop", methods=["POST"])
def stop_live_view(ip):
    """Explicitly stop a live-view session and unpause the device.

    Called by the frontend's closeLiveView() as a belt-and-suspenders fallback
    in case the WebSocket close frame is not cleanly received server-side (e.g.
    proxy timeout, half-open TCP, Werkzeug threading edge cases). The WS session
    will also call set_pause(False) in its own finally block once it exits; the
    calls are idempotent.
    """
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    with _live_view_lock:
        session = _live_view_sessions.get(real_ip)
    if session is not None:
        try:
            session.stop()
        except Exception as exc:
            logger.warning(f"[live_view] stop API: session.stop() failed for {real_ip}: {exc}")
    try:
        bot_state.set_pause(real_ip, False)
    except Exception as exc:
        logger.warning(f"[live_view] stop API: set_pause(False) failed for {real_ip}: {exc}")
    return jsonify({"ok": True})
