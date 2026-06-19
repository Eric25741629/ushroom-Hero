"""看廣告獎勵 (ad reward) dashboard blueprint — 純 WS 一鍵領取.

資料源與「神器附魔/庫存」面板完全一致：走 ``control_panel/ws_session.py`` 的
持久純 WS 連線（不開瀏覽器、有連線/閒置/踢線處理），路由用
``ws_session.get_client(ip)`` 取得已登入的 ``WSGameClient``，再呼叫
``ws_token.ad_reward`` 的 Python orchestrator。

協議（5556 web_h5 live 驗證 2026-06-19；memory reference_ws_ad_reward_protocol）：
  - ad_info   0x1601 (5633)  c2s {}  -> repeated {config_id, count, _, next_ts}
  - ad_reward 0x1602 (5634)  c2s {config_id, is_free=1}  -> 0x1602 成功 / 0x0201 拒絕
帳號買了 NO_ADS 特權，is_free=1 即時發獎、不需真的看影片。

紀律與 farm shop「讀當日次數→只補差額→到上限略過」一致：``claim_ads`` 讀一次
``ad_info``，每個 config_id 只補到 ``TIMES`` 上限、且 ``next_ts`` 未到不送封包，
故到上限/cd 的項目**不會送任何請求**（後端保證，前端照實顯示）。

預設領取 config_ids = [12 商城鑽石, 14 浮動鑽石, 15 農場種子]（使用者指定）。
"""
import logging

from flask import Blueprint, jsonify, request

from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from ws_token import ad_reward as ws_ad

logger = logging.getLogger(__name__)

bp = Blueprint("ad_reward", __name__)


def _session_client(ip: str):
    """取得 ``ip`` 的 live 純 WS client；沒有就嘗試 ensure 一條。回 (client, err)。

    與 routes_inventory._session_client 同一套行為（共用 ws_session registry）。
    """
    client = ws_session.get_client(ip)
    if client is not None:
        return client, None
    res = ws_session.ensure(ip)
    if res.get("status") == "error":
        return None, res.get("message", "WS 連線失敗")
    client = ws_session.get_client(ip)
    if client is None:
        return None, "WS 連線未就緒"
    return client, None


def _parse_config_ids(src) -> list[int]:
    """從 request 取 config_ids（預設 [12,14,15]）；過濾成已知且為 int。"""
    raw = src.get("config_ids") if isinstance(src, dict) else None
    if not raw:
        return list(ws_ad.DEFAULT_CONFIG_IDS)
    out: list[int] = []
    for v in raw:
        try:
            cid = int(v)
        except (TypeError, ValueError):
            continue
        if cid in ws_ad.TIMES:
            out.append(cid)
    return out or list(ws_ad.DEFAULT_CONFIG_IDS)


@bp.route("/api/ad_reward/status/<ip>", methods=["GET"])
@_fly_pet_auth
def ad_reward_status(ip):
    """各 config 的當日已領次數/上限（純 WS ad_info 0x1601），給前端顯示「已領 N/上限」。"""
    client, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        counts = ws_ad.read_ad_counts(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ad_reward_status 讀取失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    items = []
    for cid in ws_ad.DEFAULT_CONFIG_IDS:
        info = counts.get(cid) or {}
        count = int(info.get("count", 0) or 0)
        cap = ws_ad.TIMES.get(cid, 0)
        items.append({
            "config_id": cid,
            "name": ws_ad.AD_NAMES.get(cid, str(cid)),
            "count": count,
            "cap": cap,
            "remaining": max(0, cap - count),
            "next_ts": int(info.get("next_ts", 0) or 0),
            "maxed": count >= cap,
        })
    return jsonify({"status": "ok", "data": {"items": items}})


@bp.route("/api/ad_reward/claim/<ip>", methods=["POST"])
@_fly_pet_auth
def ad_reward_claim(ip):
    """一鍵領取看廣告獎勵（鑽石+種子）— 純 WS。

    body(可選): {config_ids:[12,14,15]}。到上限/冷卻的項目 ``claim_ads`` 不會送封包。
    回 ``{results:[{config_id,name,claimed,skipped,stopped}], total_claimed}``。
    """
    src = request.get_json(silent=True) or {}
    config_ids = _parse_config_ids(src)

    client, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        out = ws_ad.claim_ads(client, config_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ad_reward_claim 失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    # claim_ads 回 {results:{name:{name,claimed,stopped}|{name,skipped}}, total_claimed}.
    # 攤平成依 DEFAULT_CONFIG_IDS 順序的 list，補上 config_id 供前端對齊狀態列。
    name_to_id = {ws_ad.AD_NAMES.get(cid, str(cid)): cid for cid in config_ids}
    results = []
    for name, r in (out.get("results") or {}).items():
        results.append({
            "config_id": name_to_id.get(name),
            "name": name,
            "claimed": int(r.get("claimed", 0) or 0),
            "skipped": r.get("skipped"),
            "stopped": r.get("stopped"),
        })
    return jsonify({
        "status": "ok",
        "total_claimed": int(out.get("total_claimed", 0) or 0),
        "results": results,
    })
