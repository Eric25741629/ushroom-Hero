"""龍骸聖域 dashboard blueprint — pure WS over ``ws_session``.

龍骸聖域 — ``ws_token.dragon_realm``. Long-running work runs in a background
thread tracked by the shared job registry in
``control_panel.tools_optimize_jobs``; the frontend polls
``/api/carpark/job/<job_id>`` (owned by ``routes_carpark_decorate_tools``).
"""
from flask import Blueprint, jsonify

import bot_state
from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from control_panel.tools_optimize_jobs import _job_log, _job_update, _spawn
from ws_token import dragon_realm as dr_logic
from ws_token.mining import InventoryTracker

bp = Blueprint("dragon_tools", __name__)


def _real_ip(ip: str) -> str:
    """Resolve the bot_state device key. A local TCP emulator id keeps its port
    (key == full id); only a remote/worker-prefixed id is stripped to the serial.
    (Blindly splitting on ':' would mis-key a local '127.0.0.1:5555' -> '5555'.)"""
    try:
        if bot_state.is_local_device(ip):
            return ip
    except Exception:  # noqa: BLE001
        pass
    return ip.split(":")[-1] if ":" in ip else ip


# ── dragon realm (龍骸聖域) ─────────────────────────────────────────────────

@bp.route("/api/dragon/status/<ip>")
@_fly_pet_auth
def dragon_status(ip):
    ip = _real_ip(ip)
    client = ws_session.get_client(ip)
    if not client:
        return jsonify({"status": "error", "message": "未連線，請先連線裝置"}), 400
    try:
        from ws_token import codec
        body = client.call(dr_logic.CMD_INFO, b"", timeout=5)
        d = codec.walk_dict(body)
        tracker = InventoryTracker()
        try:
            tracker.seed_from_query(client, timeout=5)
        except Exception:
            pass
        keys = tracker.counts.get(dr_logic.KEY_ITEM, 0)
        return jsonify({"status": "ok", "data": {
            "ceng": d.get(2, 0), "hp": d.get(3, 0), "keys": keys,
        }})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@bp.route("/api/dragon/run/<ip>", methods=["POST"])
@_fly_pet_auth
def dragon_run(ip):
    ip = _real_ip(ip)
    jid = _spawn(_run_dragon_job, ip)
    return jsonify({"status": "ok", "job_id": jid})


def _run_dragon_job(jid: str, ip: str) -> None:
    _job_update(jid, phase="connecting")
    client = ws_session.get_client(ip)
    if not client:
        _job_update(jid, status="error", error="未連線")
        return
    tracker = InventoryTracker()
    old_handler = None
    try:
        old_handler = client._push_handler
        # chain: forward to old handler AND track inventory
        def _chain(cmd, body):
            tracker.on_push(cmd, body)
            if old_handler:
                old_handler(cmd, body)
        client.set_push_handler(_chain)
        try:
            tracker.seed_from_query(client, timeout=5)
        except Exception:
            _job_log(jid, "inventory seed 失敗，鑰匙初始數量可能不準")

        _job_update(jid, phase="exploring")
        _job_log(jid, f"開始：keys={tracker.counts.get(dr_logic.KEY_ITEM, 0)}")

        # wrap dr_logic.run with per-action logging
        import logging
        class _LogHandler(logging.Handler):
            def emit(self, record):
                _job_log(jid, record.getMessage())
        h = _LogHandler()
        h.setLevel(logging.INFO)
        dr_logger = logging.getLogger("ws_token.dragon_realm")
        dr_logger.addHandler(h)
        try:
            reason = dr_logic.run(client, tracker, max_actions=200)
        finally:
            dr_logger.removeHandler(h)

        _job_update(jid, status="done", result=reason,
                    phase=f"完成：{reason}")
        _job_log(jid, f"結束：{reason}")
    except Exception as exc:
        _job_update(jid, status="error", error=str(exc))
        _job_log(jid, f"錯誤：{exc}")
    finally:
        if old_handler is not None:
            client.set_push_handler(old_handler)
