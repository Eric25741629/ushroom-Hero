"""龍骸 SOS 救援 dashboard blueprint — 純 WS 自動協助求助隊友。

- ``GET  /api/dragon_sos/status`` — 回 {open: 龍骸是否開放}，給前端決定 SOS 鈕可見性。
- ``POST /api/dragon_sos/<ip>``   — 用 ``ip`` 當協助號，純 WS 上線救所有 pending 求助隊友。

實作走 ``ws_token.dragon_sos.rescue_via_ws``（重用 ``control_panel.ws_session`` 持久連線）。
協議 live-verified 見 ``ws_token/dragon_sos.py`` docstring。
"""
import logging

from flask import Blueprint, jsonify

from control_panel.shared.auth import _fly_pet_auth
from game_actions.dragon_realm_scheduler import is_dragon_open
from ws_token import dragon_sos

logger = logging.getLogger(__name__)

bp = Blueprint("dragon_sos", __name__)


@bp.route("/api/dragon_sos/status", methods=["GET"])
def dragon_sos_status():
    """龍骸目前是否開放（決定 SOS 鈕顯示與否）。"""
    return jsonify({"open": is_dragon_open()})


@bp.route("/api/dragon_sos/<ip>", methods=["POST"])
@_fly_pet_auth
def dragon_sos_rescue(ip: str):
    """用 ``ip`` 當協助號救援 pending 求助隊友。error 時回 502。"""
    result = dragon_sos.rescue_via_ws(ip)
    code = 502 if result.get("status") == "error" else 200
    return jsonify(result), code
