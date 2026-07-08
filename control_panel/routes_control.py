"""裝置控制路由 (pause/resume/skip_sleep/wake_delay/manual_release/force_sleep/recover)。

純 code-motion，自 control_panel_app.py 搬出，行為不變。
"""
from flask import Blueprint, jsonify, request

import config_manager  # 新增設定管理器
from adb_operations import run_adb
from control_panel.shared.auth import require_device_access
from control_panel.shared.command_queue import (
    _is_local_command_target,
    queue_command,
)

bp = Blueprint("control", __name__)


# 加入搶佔/駐守（car_park.server_car_join, cmd 12861）。`{pos}`/`{qt}` 之後用
# .format 以「已驗證的 int」填入，無注入面；JS 內字面大括號全部 {{ }} 跳脫。
_SERVER_CAR_JOIN_JS = """
(function(){{
  return new Promise(function(resolve){{
    try {{
      var nm = window.netManager;
      if (!nm || !nm._cnet) {{ resolve(JSON.stringify({{error:'請先開啟網頁並進入遊戲(netManager未就緒)'}})); return; }}
      var sock = nm._cnet;
      var CMD = 12861;
      var done = false;
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(reply){{
        if (done) return; done = true;
        try {{ if (sock.reciveMsg === myWrap) sock.reciveMsg = origRecv; }} catch(e){{}}
        resolve(JSON.stringify(reply));
      }};
      myWrap = function(cmd, body){{
        try {{
          if ((cmd|0) === CMD && !done) {{
            var b = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
            var hex=''; if (b) {{ for (var i=0;i<Math.min(b.length,2048);i++) hex += b[i].toString(16).padStart(2,'0'); }}
            finish({{ok:true, reply_hex:hex, len: b?b.length:0}});
          }}
        }} catch(e){{}}
        return origRecv(cmd, body);
      }};
      sock.reciveMsg = myWrap;
      nm.send('car_park.server_car_join', {{pos: {pos}, queue_type: {qt}}});
      setTimeout(function(){{ finish({{ok:true, sent:true, reply_hex:null}}); }}, 3000);
    }} catch(e){{ resolve(JSON.stringify({{error: String(e)}})); }}
  }});
}})()
"""


@bp.route("/api/pause/<ip>", methods=["POST"])
def pause_bot(ip):
    require_device_access(ip)
    queue_command(ip, "paused", True)
    return jsonify({"status": "ok", "action": "paused", "ip": ip})


@bp.route("/api/resume/<ip>", methods=["POST"])
def resume_bot(ip):
    require_device_access(ip)
    queue_command(ip, "paused", False)
    return jsonify({"status": "ok", "action": "resumed", "ip": ip})


@bp.route("/api/skip_sleep/<ip>", methods=["POST"])
def skip_sleep(ip):
    require_device_access(ip)
    queue_command(ip, "skip_sleep", True)
    return jsonify({"status": "ok", "action": "skip_sleep", "ip": ip})


@bp.route("/api/wake_delay/<ip>", methods=["POST"])
def wake_delay(ip):
    require_device_access(ip)
    payload = request.get_json(silent=True) or {}
    try:
        delay_sec = float(payload.get("delay_sec", 0))
    except Exception:
        return jsonify({"status": "error", "message": "delay_sec must be a number"}), 400
    if delay_sec < 0:
        delay_sec = 0.0

    queue_command(ip, "wake_delay_sec", delay_sec)
    return jsonify(
        {
            "status": "ok",
            "action": "wake_delay",
            "ip": ip,
            "delay_sec": delay_sec,
        }
    )


@bp.route("/api/manual_release/<ip>", methods=["POST"])
def manual_release(ip):
    require_device_access(ip)
    queue_command(ip, "manual_release", True)
    return jsonify({"status": "ok", "action": "manual_release", "ip": ip})


@bp.route("/api/force_sleep/<ip>", methods=["POST"])
def force_sleep(ip):
    require_device_access(ip)
    queue_command(ip, "force_sleep", True)
    return jsonify({"status": "ok", "action": "force_sleep", "ip": ip})


@bp.route("/api/recover/<ip>", methods=["POST"])
def recover_screen(ip):
    require_device_access(ip)
    # 遠端設備（本機 TCP 模擬器同樣帶冒號，必須走 _is_local_command_target 判斷）
    if not _is_local_command_target(ip):
        queue_command(ip, "recover", True)
        return jsonify({"status": "ok", "action": "queued_recover", "ip": ip})

    # 本地設備
    try:
        if config_manager.get_flag(ip, "is_real_phone") or "fc65396d" in ip:
            run_adb("shell wm density reset && wm size reset", device_serial=ip)
            return jsonify({"status": "ok", "action": "screen_recovered", "ip": ip})
        return jsonify(
            {"status": "error", "message": "此設備未設定為實體機/特殊機型"}
        ), 403
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/carpark_rob/<ip>", methods=["POST"])
def carpark_rob(ip):
    require_device_access(ip)
    if ip != "7fe98fc6":
        return jsonify({"status": "error", "message": "此功能僅限小寶"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        pos = int(payload.get("pos"))
        queue_type = int(payload.get("queue_type"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "pos/queue_type 需為整數"}), 400
    if pos < 1:
        return jsonify({"status": "error", "message": "pos 需 >= 1"}), 400
    if queue_type not in (1, 2):
        return jsonify(
            {"status": "error", "message": "queue_type 需為 1(搶佔) 或 2(駐守)"}
        ), 400

    import control_panel_app as _cpa

    js = _SERVER_CAR_JOIN_JS.format(pos=pos, qt=queue_type)
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="reply", timeout=8)
