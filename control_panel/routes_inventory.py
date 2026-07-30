"""倉庫 dashboard blueprint — 守護靈 + 神器附魔石 唯讀檢視 + 分解（賣）。

資料源改走**純 WS**（`control_panel/ws_session.py` 的持久連線，不需開瀏覽器）：
路由用 `ws_session.get_client(ip)` 取得已登入的 ``WSGameClient``，再呼叫
``ws_token.spirit`` / ``ws_token.artifact_gem`` 的 Python reader 解析，中文名由
``utils.config_names`` 查靜態表（`data/config_names.json`，一次性 dump，免瀏覽器）。

協議（5554 live 驗證 2026-06-19）：
  - 守護靈 spirit_info  cmd 19713 (0x4D01) — 見 ws_token/spirit.py
  - 神器附魔 artifact_gem_info  cmd 13569 (0x3501) / split 13578 (0x350A) — 見 ws_token/artifact_gem.py
    ⚠ gem 的 ``pos`` 是鑲嵌位置類型(1-6)非裝備狀態；「已裝備」= id 在 tab_list 內。
    分解防呆（排除 equipped + locked）以 ``artifact_gem.select_safe`` 為唯一來源。
"""
import logging

from flask import Blueprint, jsonify, render_template, request

from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from utils import config_names
from ws_token import artifact_gem as ws_gem
from ws_token import spirit as ws_spirit

logger = logging.getLogger(__name__)

bp = Blueprint("inventory", __name__)


# --- 純 WS session 取得（只能由前端「讀取」按鈕先建立） ----------------------

def _session_client(ip: str):
    """取得 ``ip`` 的 live 純 WS client；API 本身不得暗中建立連線。"""
    client = ws_session.get_client(ip)
    if client is not None:
        return client, None
    return None, "尚未建立純 WS，請先按讀取"


def _attr_list(pairs: dict[int, int]) -> list[dict]:
    """{attr_id: value} -> [{attr_id, name(中文), value}]。"""
    return [{"attr_id": k, "name": config_names.attr_name(k), "value": v}
            for k, v in pairs.items()]


def _gem_rand_attr_list(pairs: dict[int, int]) -> list[dict]:
    """神器附魔隨機詞條加上遊戲原生顏色色階。"""
    return [{"attr_id": k, "name": config_names.attr_name(k), "value": v,
             "quality": config_names.gem_attr_quality(k, v)}
            for k, v in pairs.items()]


# --- 守護靈 -----------------------------------------------------------------

def _spirit_to_json(s) -> dict:
    positions = []
    for p in s.positions:
        q = config_names.spirit_affix_quality(p.cur_id)
        if p.cur_id and not q:
            logger.warning("spirit_affix_quality miss: spirit=%s pos=%s cur_id=%s",
                           s.config_id, p.pos, p.cur_id)
        positions.append({
            "pos": p.pos,
            "cur_id": p.cur_id,
            "quality": q,
            "reshape_id": p.reshape_id,
            "affixes": _attr_list(p.cur_attrs),
            "reshape": _attr_list(p.reshape_attrs),
        })
    return {
        "id": str(s.id),
        "config_id": s.config_id,
        "name": config_names.spirit_name(s.config_id),
        "level": s.level,
        "lock": 1 if s.is_lock else 0,
        "positions": positions,
    }


# --- 神器附魔石 -------------------------------------------------------------

def _gem_to_json(g, equipped_ids) -> dict:
    return {
        "id": str(g.id),
        "quality": g.quality,
        "quality_name": config_names.quality_name(g.quality),
        "pos": g.pos,
        "suit": g.suit,
        "suit_name": config_names.suit_name(g.suit),
        "lv": g.lv,
        "is_red": 1 if g.is_red else 0,
        "is_lock": 1 if g.is_lock else 0,
        "is_equipped": g.id in equipped_ids,
        "base_attr": _attr_list(g.base_attr),
        "rand_attr": _gem_rand_attr_list(g.rand_attr),
    }


# --- routes -----------------------------------------------------------------

@bp.route("/inventory")
@_fly_pet_auth
def inventory_page():
    """守護靈 + 神器附魔石 倉庫檢視頁（純 WS、中文名、過濾、分解勾選）。"""
    from control_panel.routes_pages import _get_frontend_version

    return render_template("inventory.html", frontend_version=_get_frontend_version())


@bp.route("/api/spirit_list/<ip>", methods=["GET"])
@_fly_pet_auth
def spirit_list(ip):
    """守護靈倉庫：每隻守護靈 + 各位置詞條（中文名）。純 WS 唯讀 (cmd 19713)。"""
    client, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        inv = ws_spirit.read_spirit_info(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("spirit_list 讀取失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
    spirits = [_spirit_to_json(s) for s in inv.spirits]
    spirits.sort(key=lambda s: (-s["level"], s["config_id"]))
    return jsonify({"status": "ok", "data": {
        "reset_times": inv.reset_times,
        "reshape_times": inv.reshape_times,
        "tab": inv.tab,
        "count": len(spirits),
        "spirits": spirits,
    }})


@bp.route("/api/artifact_gem_list/<ip>", methods=["GET"])
@_fly_pet_auth
def artifact_gem_list(ip):
    """神器附魔石倉庫：每顆品質/套裝(中文)/等級/主屬/隨機詞條 + 是否裝備。純 WS (0x3501)。"""
    client, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        inv = ws_gem.read_gem_info(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact_gem_list 讀取失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
    gems = [_gem_to_json(g, inv.equipped_ids) for g in inv.gems]
    gems.sort(key=lambda g: (g["quality"], g["lv"]))
    return jsonify({"status": "ok", "data": {
        "count": len(gems),
        "tab": inv.tab,
        "equipped": len(inv.equipped_ids),
        "gems": gems,
    }})


@bp.route("/api/artifact_gem_action/<ip>", methods=["POST"])
@_fly_pet_auth
def artifact_gem_action(ip):
    """分解（賣）勾選的神器附魔石 — split 0x350A 批量。

    server 端硬防呆：先重讀倉庫快照算 equipped/lock，用 ``select_safe`` 擋掉
    已裝備/鎖定/不存在的 id（唯一防呆來源，不信任前端）。只送安全子集。
    """
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "split"))
    ids = data.get("ids") or []
    if action != "split":
        return jsonify({"status": "error", "message": f"不支援的動作 {action}"}), 400
    if not ids:
        return jsonify({"status": "error", "message": "未勾選任何寶石"}), 400

    client, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        inv = ws_gem.read_gem_info(client)  # 重讀快照 → 最新 equipped/lock
        safe, blocked = ws_gem.select_safe(inv, ids)
        blocked_str = {str(k): v for k, v in blocked.items()}
        if not safe:
            return jsonify({"status": "error",
                            "message": "全部被防呆擋下（已裝備/鎖定/不存在）",
                            "blocked": blocked_str}), 400
        result = ws_gem.decompose(client, safe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact_gem_action 失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({
        "status": "ok" if result["ok"] else "error",
        "requested": len(ids),
        "decomposed": result["removed"],
        "decomposed_count": len(result["removed"]),
        "blocked": blocked_str,
        "error_code": result["error_code"],
    })
