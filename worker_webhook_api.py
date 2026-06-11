import threading

from flask import Flask, jsonify, request

import bot_state
import config_manager
from adb_operations import reset_screen_settings


_worker_webhook_thread_started = False
_worker_webhook_app = Flask("worker-webhook-receiver")


def normalize_master_url(raw: str) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.rstrip("/")


def normalize_url(raw: str) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.rstrip("/")


def resolve_worker_webhook_url(master_url: str, worker_id: str) -> str:
    g = config_manager.get_global_config()
    explicit = normalize_url(g.get("worker_webhook_url"))
    if explicit:
        if explicit.endswith("/api/worker_commands"):
            return explicit
        return f"{explicit}/api/worker_commands"

    try:
        port = int(g.get("worker_webhook_port", 5003))
    except Exception:
        port = 5003
    host = str(g.get("worker_webhook_host") or "").strip()
    if not host:
        host = worker_id
    if not host:
        return ""
    return f"http://{host}:{port}/api/worker_commands"


def apply_remote_commands(ips, commands: dict):
    for ip in ips:
        cmd = commands.get(ip) or {}
        if "paused" in cmd:
            bot_state.set_pause(ip, bool(cmd.get("paused")))
        if cmd.get("skip_sleep"):
            bot_state.set_skip_sleep(ip)
        if cmd.get("manual_release"):
            bot_state.set_manual_release(ip)
        if cmd.get("force_sleep"):
            bot_state.request_force_sleep(ip)
        if "wake_delay_sec" in cmd:
            bot_state.set_wake_override(ip, cmd.get("wake_delay_sec", 0))
        if cmd.get("recover"):
            try:
                reset_screen_settings(ip)
            except Exception as e:
                print(f"[WorkerSync] recover 失敗 {ip}: {e}")


@_worker_webhook_app.route("/api/worker_commands", methods=["POST"])
def worker_webhook_commands():
    try:
        data = request.get_json(silent=True) or {}
        commands = data.get("commands", {}) if isinstance(data, dict) else {}
        if not isinstance(commands, dict):
            commands = {}
        ips = sorted(commands.keys())
        if ips:
            apply_remote_commands(ips, commands)

        global_cmd = data.get("global_commands", {}) if isinstance(data, dict) else {}
        refresh_needed = bool((global_cmd or {}).get("refresh_needed"))
        if refresh_needed:
            bot_state.set_refresh_needed()

        return jsonify({"status": "ok", "applied_ips": ips, "refresh_needed": refresh_needed})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def ensure_worker_webhook_started():
    global _worker_webhook_thread_started
    if _worker_webhook_thread_started:
        return
    g = config_manager.get_global_config()
    if str(g.get("mode", "master")).lower() != "worker":
        return
    try:
        port = int(g.get("worker_webhook_port", 5003))
    except Exception:
        port = 5003
    host = str(g.get("worker_webhook_bind", "0.0.0.0")).strip() or "0.0.0.0"

    def _run():
        _worker_webhook_app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="worker-webhook")
    t.start()
    _worker_webhook_thread_started = True
    print(f"[WorkerSync] Webhook receiver 啟動: http://{host}:{port}/api/worker_commands")
