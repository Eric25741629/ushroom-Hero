"""飛寵 dashboard blueprint。

飛寵管理本體走 ``control_panel.ws_session`` 的純 WebSocket client，不需啟動
Playwright/Chrome。``/api/cdp_evaluate`` 僅保留為既有除錯工具，不參與飛寵頁流程。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

import bot_state
from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from ws_token import fly_pet as ws_fly_pet

bp = Blueprint("fly_pet", __name__)
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_FLY_PET_ICON_DIR = str(_ROOT / "static" / "flypet_icons")
_FLY_PET_CATALOG_PATH = _ROOT / "data" / "fly_pet_catalog.json"

# 同一裝置的飛寵 RPC 必須依序進行，避免同 cmd 的並行 waiter 對錯回應。
_device_locks: dict[str, threading.RLock] = {}
_device_locks_guard = threading.Lock()


def _device_lock(ip: str) -> threading.RLock:
    with _device_locks_guard:
        return _device_locks.setdefault(ip, threading.RLock())


def _session_client(ip: str):
    """只取用由前端「載入」建立的純 WS client。"""
    client = ws_session.get_client(ip)
    if client is not None:
        return client, None
    return None, "尚未建立純 WS，請先按載入"


def _client_or_response(ip: str):
    client, err = _session_client(ip)
    if err:
        return None, (jsonify({"status": "error", "message": err}), 409)
    return client, None


def _rpc_error(ip: str, action: str, exc: Exception):
    logger.warning("飛寵純 WS %s 失敗 ip=%s: %s", action, ip, exc)
    code = getattr(exc, "code", None)
    payload = {"status": "error", "message": str(exc)}
    if code is not None:
        payload["error_code"] = code
    return jsonify(payload), 500


def _load_catalog() -> dict:
    """讀本地 catalog；無 dump 時仍以圖示 id 提供可用的純 WS 降級目錄。"""
    data = {"species": [], "entries": []}
    if _FLY_PET_CATALOG_PATH.is_file():
        try:
            loaded = json.loads(_FLY_PET_CATALOG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data["species"] = list(loaded.get("species") or [])
                data["entries"] = list(loaded.get("entries") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("讀取飛寵 catalog 失敗: %s", exc)
    if not data["species"]:
        icon_dir = Path(_FLY_PET_ICON_DIR)
        ids = sorted(
            int(path.stem)
            for path in icon_dir.glob("*.png")
            if path.stem.isdigit()
        )
        data["species"] = [{"id": config_id, "name": f"飛寵 #{config_id}"}
                           for config_id in ids]
    return data


def _catalog_indexes():
    catalog = _load_catalog()
    species = {int(row["id"]): str(row.get("name") or f"飛寵 #{row['id']}")
               for row in catalog["species"] if row.get("id") is not None}
    by_level = {}
    by_id = {}
    for row in catalog["entries"]:
        if row.get("id") is None:
            continue
        entry_id = int(row["id"])
        normalized = {
            "id": entry_id,
            "level": int(row.get("level", 0)),
            "name": str(row.get("name") or f"詞條 #{entry_id}"),
            "quality": int(row.get("quality", 0)),
            "desc": str(row.get("desc") or ""),
            "desc_parm": list(row.get("desc_parm") or []),
            "belong_talent": int(row.get("belong_talent", 0)),
            "special_effect": row.get("special_effect", 0),
        }
        by_level[(entry_id, normalized["level"])] = normalized
        by_id.setdefault(entry_id, normalized)
    return catalog, species, by_level, by_id


def _entry_json(entry, by_level, by_id) -> dict:
    cfg = by_level.get((entry.id, entry.level)) or by_id.get(entry.id) or {}
    return {
        "id": entry.id,
        "level": entry.level,
        "name": cfg.get("name", f"詞條 #{entry.id}"),
        "quality": int(cfg.get("quality", 0)),
        "desc": cfg.get("desc", ""),
        "desc_parm": cfg.get("desc_parm", []),
        "belong_talent": int(cfg.get("belong_talent", 0)),
        "special_effect": cfg.get("special_effect", 0),
    }


def _pet_json(pet, species, by_level, by_id, collected=None) -> dict:
    collected = collected or set()
    return {
        "id": pet.id,
        "config_id": pet.config_id,
        "name": pet.name,
        "display_name": species.get(pet.config_id, f"飛寵 #{pet.config_id}"),
        "quality": pet.quality,
        "level": pet.level,
        "fight": pet.fight,
        "is_deployed": pet.fight == 1,
        "is_collected": pet.config_id in collected,
        "generation": pet.generation,
        "growth": pet.growth,
        "step": pet.step,
        "lock": 1 if pet.lock else 0,
        "star": 1 if pet.star else 0,
        "role_id": pet.role_id,
        "entries": [_entry_json(e, by_level, by_id) for e in pet.entries],
    }


def _base_pet_json(pet, species):
    if pet is None or pet.id <= 0:
        return None
    return {
        "id": pet.id,
        "config_id": pet.config_id,
        "name": pet.name,
        "display_name": species.get(pet.config_id, f"飛寵 #{pet.config_id}"),
        "quality": pet.quality,
        "role_id": pet.role_id,
    }


@bp.route("/api/cdp_evaluate/<ip>", methods=["GET", "POST"])
def cdp_evaluate(ip):
    """保留的 CDP 除錯入口；飛寵管理不會呼叫。"""
    import control_panel_app as _cpa

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        expression = body.get("expression", "")
        await_promise = bool(body.get("awaitPromise", False))
    else:
        expression = request.args.get("expr", "")
        await_promise = False
    if not expression:
        return jsonify({"status": "error", "message": "no expression"}), 400
    return _cpa._cdp_json_response(ip, expression, await_promise=await_promise)


@bp.route("/api/fly_pet_list/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_list(ip):
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            snapshot = ws_fly_pet.read_snapshot(client)
        _, species, by_level, by_id = _catalog_indexes()
        pets = [_pet_json(p, species, by_level, by_id,
                          snapshot.collected_config_ids)
                for p in snapshot.pets]
        pets.sort(key=lambda p: (-p["quality"], -p["level"]))
        return jsonify({"status": "ok", "pets": pets})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "讀取清單", exc)


@bp.route("/api/fly_pet_icon/<int:config_id>", methods=["GET"])
@_fly_pet_auth
def fly_pet_icon(config_id):
    import control_panel_app as _cpa

    filename = f"{config_id}.png"
    if not os.path.isfile(os.path.join(_cpa._FLY_PET_ICON_DIR, filename)):
        return "", 404
    response = send_from_directory(
        _cpa._FLY_PET_ICON_DIR, filename, mimetype="image/png"
    )
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@bp.route("/api/fly_pet_check_connection/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_check_connection(ip):
    client = ws_session.get_client(ip)
    return jsonify({"status": "ok", "data": {
        "connected": bool(client and client.is_running()),
        "transport": "pure_ws",
    }})


@bp.route("/api/fly_pet_browser_status/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_browser_status(ip):
    """舊前端相容端點；browser_up 代表純 WS session 是否已在線。"""
    client = ws_session.get_client(ip)
    connected = bool(client and client.is_running())
    return jsonify({"status": "ok", "data": {
        "browser_up": connected,
        "connected": connected,
        "transport": "pure_ws",
    }})


@bp.route("/api/fly_pet_bot_status/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_bot_status(ip):
    states = bot_state.get_all_states()
    state = states.get(ip, {})
    status = state.get("status", "OFFLINE")
    task = state.get("task", "")
    step = state.get("step", "")
    running = (
        status not in ("OFFLINE", "")
        and task not in ("待命", "初始化", "休眠中", "強制休眠", "手動操作", "")
    )
    # TOOL 純 WS lease 建立時 registry 已暫停 bot loop；不要讓舊 task 文案誤擋操作。
    if ws_session.get_client(ip) is not None:
        running = False
    return jsonify({
        "status": "ok",
        "bot_running": running,
        "bot_status": status,
        "bot_task": task,
        "bot_step": step,
    })


@bp.route("/api/fly_pet_resolve/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_resolve(ip):
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"status": "error",
                        "message": "ids must be a non-empty list"}), 400
    try:
        requested = [int(value) for value in ids]
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "ids must be integers"}), 400
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            snapshot, safe, skipped = ws_fly_pet.resolve_pets(client, requested)
        result = {
            "ok": bool(safe),
            "petCount": len(snapshot.pets) - len(safe),
            "skipped": skipped,
        }
        if not safe:
            result["message"] = "no safe pets"
        return jsonify({"status": "ok", "data": result})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "分解", exc)


def _breed_snapshot_json(snapshot):
    _, species, by_level, by_id = _catalog_indexes()
    homes = [{
        "id": base.id,
        "name": base.name,
        "state": base.state,
        "end_time": base.end_time,
        "fly_a": _base_pet_json(base.fly_a, species),
        "fly_b": _base_pet_json(base.fly_b, species),
        "fly_pet": None,
        "_raw": {
            "id": base.id,
            "name": base.name,
            "state": base.state,
            "start_time": base.start_time,
            "end_time": base.end_time,
        },
    } for base in snapshot.bases]
    shelved = [{
        **_pet_json(shelf.info, species, by_level, by_id),
        "state": shelf.state,
        "end_time": shelf.end_time,
    } for shelf in snapshot.shelves]
    eggs = [{
        "id": egg.id,
        "config_id": egg.config_id,
        **{f"ext_{key}": value for key, value in egg.ext.items()},
    } for egg in snapshot.eggs]
    return {"homes": homes, "shelved": shelved, "egg_list": eggs}


@bp.route("/api/fly_pet_breed_info/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_breed_info(ip):
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            snapshot = ws_fly_pet.read_breed_snapshot(client)
        return jsonify({"status": "ok", "data": _breed_snapshot_json(snapshot)})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "讀取繁殖狀態", exc)


@bp.route("/api/fly_pet_breed_methods/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_breed_methods(ip):
    return jsonify({"status": "ok", "data": [
        "send_66_1", "send_66_2", "send_66_3", "send_66_8",
        "send_66_21", "send_66_22", "send_66_23", "send_66_24",
        "send_66_27", "send_66_28", "send_66_32",
    ]})


@bp.route("/api/fly_pet_shelve/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_shelve(ip):
    data = request.get_json(silent=True) or {}
    pet_id = data.get("pet_id")
    action = data.get("action")
    if pet_id is None or action not in ("place", "remove"):
        return jsonify({"status": "error",
                        "message": "pet_id required, action must be 'place' or 'remove'"}), 400
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            ws_fly_pet.set_shelf(client, int(pet_id), action)
        return jsonify({"status": "ok", "data": {"ok": True}})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "上架/下架", exc)


@bp.route("/api/fly_pet_partner/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_partner(ip):
    role_id = request.args.get("role_id")
    try:
        role_id_int = int(role_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error",
                        "message": "role_id query param required and must be an integer"}), 400
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            shelves = ws_fly_pet.read_partner_shelves(client, role_id_int)
        _, species, by_level, by_id = _catalog_indexes()
        pets = []
        for shelf in shelves:
            item = _pet_json(shelf.info, species, by_level, by_id)
            item.update({"state": shelf.state, "end_time": shelf.end_time})
            pets.append(item)
        return jsonify({"status": "ok", "data": pets})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "讀取搭檔飛寵", exc)


@bp.route("/api/fly_pet_breed_start/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_breed_start(ip):
    data = request.get_json(silent=True) or {}
    values = (data.get("base_id"), data.get("fly_a_id"), data.get("fly_b_id"))
    if any(value is None for value in values):
        return jsonify({"status": "error",
                        "message": "base_id, fly_a_id, fly_b_id required"}), 400
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            base = ws_fly_pet.start_breeding(client, *(int(v) for v in values))
        return jsonify({"status": "ok", "data": {
            "ok": True, "state": base.state, "timed_out": False,
        }})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "開始繁殖", exc)


@bp.route("/api/fly_pet_breed_collect/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_breed_collect(ip):
    base_id = (request.get_json(silent=True) or {}).get("base_id")
    if base_id is None:
        return jsonify({"status": "error", "message": "base_id required"}), 400
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            base, eggs = ws_fly_pet.collect_breeding(client, int(base_id))
        return jsonify({"status": "ok", "data": {
            "ok": True,
            "state": base.state,
            "egg_ids": [egg.id for egg in eggs],
            "timed_out": False,
        }})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "收取繁殖結果", exc)


@bp.route("/api/fly_pet_hatch/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_hatch(ip):
    egg_id = (request.get_json(silent=True) or {}).get("egg_id")
    if egg_id is None:
        return jsonify({"status": "error", "message": "egg_id required"}), 400
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            pet_ids = ws_fly_pet.hatch_egg(client, int(egg_id))
        return jsonify({"status": "ok", "data": {
            "ok": True, "pet_ids": pet_ids,
        }})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "孵化", exc)


@bp.route("/api/fly_pet_partners/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_partners(ip):
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            snapshot = ws_fly_pet.read_breed_snapshot(client)
        partners = [
            {"role_id": p.role_id, "name": p.name, "head": p.head}
            for p in snapshot.partners if p.role_id
        ]
        return jsonify({"status": "ok", "partners": partners, "data": partners})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "讀取搭檔", exc)


@bp.route("/api/fly_pet_refresh_breed/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_refresh_breed(ip):
    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            ws_fly_pet.read_breed_snapshot(client)
        return jsonify({"status": "ok", "data": {
            "ok": True, "timed_out": False,
        }})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "刷新繁殖狀態", exc)


@bp.route("/api/fly_pet_find_pair/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_find_pair(ip):
    data = request.get_json(silent=True) or {}
    criteria = data.get("criteria") or {}
    exclude_ids = {int(value) for value in (data.get("exclude_ids") or [])}
    mode = str(criteria.get("mode", "quality_count"))
    quality = int(criteria.get("quality", 3))
    min_count = int(criteria.get("min_count", 2))
    min_total = int(criteria.get("min_total_entries", 3))
    prefer_low_gen = bool(criteria.get("prefer_low_gen", True))
    species_whitelist = {int(v) for v in (criteria.get("species_whitelist") or [])}
    entry_whitelist = {int(v) for v in (criteria.get("entry_whitelist") or [])}

    client, response = _client_or_response(ip)
    if response:
        return response
    try:
        with _device_lock(ip):
            snapshot = ws_fly_pet.read_snapshot(client)
            breed = ws_fly_pet.read_breed_snapshot(client)
        _, species, by_level, by_id = _catalog_indexes()
        cooldown_ids = {
            shelf.info.id for shelf in breed.shelves if shelf.state > 0
        }
        breeding_ids = {
            pet.id
            for base in breed.bases
            for pet in (base.fly_a, base.fly_b)
            if pet is not None and pet.id > 0
        }
        candidates = []
        for pet in snapshot.pets:
            if (pet.id in exclude_ids or pet.id in cooldown_ids
                    or pet.id in breeding_ids or pet.lock or pet.fight == 1):
                continue
            entries = [_entry_json(e, by_level, by_id) for e in pet.entries]
            matching_count = (
                sum(1 for entry in entries if entry["quality"] == quality)
                if mode == "quality_count" else len(entries)
            )
            matches = (
                matching_count >= min_count
                if mode == "quality_count" else len(entries) >= min_total
            )
            if not matches:
                continue
            if species_whitelist and pet.config_id not in species_whitelist:
                continue
            if entry_whitelist and not entry_whitelist.issubset(
                    {entry["id"] for entry in entries}):
                continue
            item = _pet_json(pet, species, by_level, by_id)
            item.update({
                "entry_count": len(entries),
                "matching_count": matching_count,
                "entries": [{"name": e["name"], "quality": e["quality"]}
                            for e in entries],
            })
            candidates.append(item)
        if prefer_low_gen:
            candidates.sort(key=lambda p: (
                p["generation"], -p["matching_count"], -p["entry_count"]
            ))
        else:
            candidates.sort(key=lambda p: (
                -p["matching_count"], -p["entry_count"], p["generation"]
            ))
        pair = (
            {"fly_a": candidates[0], "fly_b": candidates[1]}
            if len(candidates) >= 2 else None
        )
        return jsonify({"status": "ok", "data": {
            "pair": pair,
            "candidates_count": len(candidates),
            "skipped_cooldown": len(cooldown_ids),
            "skipped_breeding": len(breeding_ids),
        }})
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(ip, "尋找配對", exc)


@bp.route("/api/fly_pet_catalog/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_catalog(ip):
    catalog, _, _, _ = _catalog_indexes()
    # 前端只需每個詞條 id 一列；本地檔可保留各 level 供清單詳細資料使用。
    entries = {}
    for row in catalog["entries"]:
        if row.get("id") is not None:
            entries.setdefault(int(row["id"]), {
                "id": int(row["id"]),
                "name": str(row.get("name") or f"詞條 #{row['id']}"),
                "quality": int(row.get("quality", 0)),
            })
    return jsonify({"status": "ok", "data": {
        "species": catalog["species"],
        "entries": list(entries.values()),
    }})
