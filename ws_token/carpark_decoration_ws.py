"""Pure WS reader + executor for dashboard 車位裝飾 (parking decoration).

Replaces the CDP-injected JS paths (READ_STATE_WS_JS / EXEC_STEP_WS_JS) with
direct WS calls via the ws_session persistent client. No browser required.

Static configs (one-time dumps, checked into repo):
  - ws_token/data/mall_parking_frag.json  (configMall shop_type=11)
  - docs/protocol/PARKING_DESIGN_CATALOG.json  (configParking_design)

WS commands:
  - 12801 car_park_info  (skin_list: owned decorations + levels)
  - 6913  shop_info      (buy counts per shop_id, shop_type=11)
  - 6914  shop_buy       (buy frags)
  - 12817 car_park_skin_up  (upgrade one star)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_CAR_PARK_INFO = 12801
CMD_SHOP_INFO = 6913
CMD_SHOP_BUY = 6914
CMD_SKIN_UP = 12817
CMD_ERROR = 0x0201  # 513
CMD_INVENTORY_QUERY = 0x0401  # full bag snapshot; repeated {item_id#1, uid#2, count#3}
GOODS_MUSHROOM_CAR_COIN = 201  # 菇車幣 role attribute

_DATA_DIR = Path(__file__).parent / "data"
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "docs" / "protocol" / "PARKING_DESIGN_CATALOG.json"

# --- static config caches (loaded once) ------------------------------------

_mall_cache: dict[int, dict] | None = None
_catalog_cache: dict[tuple[int, int], dict] | None = None


def _load_mall() -> dict[int, dict]:
    """Load frag_goods_id -> {shop_id, price, cap} from mall dump."""
    global _mall_cache
    if _mall_cache is not None:
        return _mall_cache
    p = _DATA_DIR / "mall_parking_frag.json"
    with open(p, encoding="utf-8-sig") as f:
        raw = json.load(f)
    _mall_cache = {int(k): v for k, v in raw.items()}
    return _mall_cache


def _load_catalog() -> dict[tuple[int, int], dict]:
    """Load (id, level) -> row from parking design catalog."""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    with open(_CATALOG_PATH, encoding="utf-8-sig") as f:
        data = json.load(f)
    rows = data.get("rows") or data if isinstance(data, list) else data.get("rows", [])
    _catalog_cache = {(r["id"], r["level"]): r for r in rows}
    return _catalog_cache


# --- helpers ---------------------------------------------------------------

def _name_of(catalog: dict, deco_id: int) -> str:
    for lv in (1, 0):
        row = catalog.get((deco_id, lv))
        if row and row.get("name"):
            return row["name"]
    return str(deco_id)


def _max_level(catalog: dict, deco_id: int) -> int:
    mx = 0
    for lv in range(1, 16):
        if (deco_id, lv) in catalog:
            mx = lv
        else:
            break
    return mx


def _attr_sum(own_attrs: list | None) -> int:
    return sum(a[1] for a in (own_attrs or []) if len(a) >= 2)


def _build_steps(catalog: dict, deco_id: int, cur_level: int) -> list[list[int]]:
    """Remaining upgrade steps: [[to_level, frags, marginal_attr], ...]."""
    out = []
    mx = _max_level(catalog, deco_id)
    for lv in range(cur_level + 1, mx + 1):
        prev = catalog.get((deco_id, lv - 1))
        cur = catalog.get((deco_id, lv))
        frags = 0
        if prev and prev.get("expend"):
            e = prev["expend"][0]
            frags = e[1] if len(e) >= 2 else 0
        a_prev = _attr_sum(prev.get("own_attrs") if prev else None)
        a_cur = _attr_sum(cur.get("own_attrs") if cur else None)
        out.append([lv, frags, a_cur - a_prev])
    return out


def _frag_goods_of(catalog: dict, deco_id: int) -> int | None:
    """Find the frag goods_id for a decoration from its catalog expend."""
    for lv in range(1, 16):
        row = catalog.get((deco_id, lv))
        if row and row.get("expend"):
            e = row["expend"][0]
            if len(e) >= 1:
                return e[0]
    return None


# --- WS protocol -----------------------------------------------------------

def _build_car_park_info_body(role_id: int) -> bytes:
    """car_park_info_c2s {type#1=0, master_id#2=role_id, ceng#3=0}."""
    return codec.pb_uint(1, 0) + codec.pb_uint(2, role_id) + codec.pb_uint(3, 0)


def _build_shop_info_body() -> bytes:
    """shop_info_c2s {shop_type#1=11}."""
    return codec.pb_uint(1, 11)


def _parse_skin_list(body: bytes) -> list[tuple[int, int]]:
    """Parse 12801 s2c field#8 repeated p_car_park_skin -> [(skin_id, skin_lev)]."""
    skins = []
    for fn, v in codec.walk(body):
        if fn == 8 and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            skin_id = int(d.get(1, 0))
            skin_lev = int(d.get(2, 0))
            skins.append((skin_id, skin_lev))
    return skins


def _query_coin(client: WSGameClient, timeout: float) -> int | None:
    """Query 菇車幣 via 0x0401 inventory snapshot. Returns count or None."""
    try:
        cmd, reply = client.call_for(
            CMD_INVENTORY_QUERY, b"",
            expect_cmds=(CMD_INVENTORY_QUERY,), timeout=timeout)
        if cmd != CMD_INVENTORY_QUERY or not reply:
            return None
        for fnum, val in codec.walk(reply):
            if fnum == 1 and isinstance(val, (bytes, bytearray)):
                d = codec.walk_dict(bytes(val))
                if d.get(1) == GOODS_MUSHROOM_CAR_COIN:
                    return int(d.get(3, 0))
    except Exception:
        pass
    return None


def _parse_shop_buy_info(body: bytes) -> dict[int, int]:
    """Parse 6913 s2c field#2 repeated p_key_value -> {shop_id: buy_count}."""
    buy = {}
    for fn, v in codec.walk(body):
        if fn == 2 and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            k = int(d.get(1, 0))
            val = int(d.get(2, 0))
            if k:
                buy[k] = val
    return buy


# --- public API ------------------------------------------------------------

def read_state(client: WSGameClient, *, timeout: float = 10.0) -> tuple[dict | None, str | None]:
    """Read decoration state via pure WS. Returns (state_dict, error_str).

    state_dict matches the shape the existing _plan() / _build_decos() expects:
    {coin, decos: [{id, name, level, price, limit_remaining, steps, shop_id, ...}]}
    """
    try:
        role_id = client._creds.role_id
        if not role_id:
            return None, "no role_id in client creds"

        catalog = _load_catalog()
        mall = _load_mall()

        # Send both requests, collect replies
        car_body = client.call(CMD_CAR_PARK_INFO,
                               _build_car_park_info_body(role_id),
                               timeout=timeout)
        shop_body = client.call(CMD_SHOP_INFO,
                                _build_shop_info_body(),
                                timeout=timeout)

        skins = _parse_skin_list(car_body)
        buy_info = _parse_shop_buy_info(shop_body)

        decos = []
        for skin_id, skin_lev in skins:
            if skin_lev < 1:
                continue  # lev 0 = free initial
            fg = _frag_goods_of(catalog, skin_id)
            sh = mall.get(fg) if fg else None
            bought = buy_info.get(sh["shop_id"], 0) if sh else 0
            cap = sh["cap"] if sh else 0
            limit_remaining = (cap - bought) if sh else 0
            price = sh["price"] if sh else 0
            shop_id = sh["shop_id"] if sh else None

            steps = _build_steps(catalog, skin_id, skin_lev)
            decos.append({
                "id": skin_id,
                "name": _name_of(catalog, skin_id),
                "level": skin_lev,
                "price": price,
                "limit_remaining": limit_remaining,
                "steps": steps,
                "shop_id": shop_id,
                "frag_goods": fg,
                "bought": bought,
                "cap": cap,
            })

        decos.sort(key=lambda d: d["id"])

        coin = _query_coin(client, timeout)
        return {"coin": coin, "decos": decos, "deco_count": len(decos)}, None

    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def exec_buy_and_upgrade(
    client: WSGameClient,
    shop_id: int,
    skin_id: int,
    frags: int,
    *,
    do_upgrade: bool = True,
    timeout: float = 10.0,
) -> tuple[dict | None, str | None]:
    """Buy frags + upgrade one star via pure WS. Returns (result, error).

    result: {ok, bought, name, before_level, after_level, err?}
    """
    try:
        role_id = client._creds.role_id
        catalog = _load_catalog()
        name = _name_of(catalog, skin_id)

        # Read current level
        car_body = client.call(CMD_CAR_PARK_INFO,
                               _build_car_park_info_body(role_id),
                               timeout=timeout)
        skins = dict(_parse_skin_list(car_body))
        before_level = skins.get(skin_id, 0)

        # Buy frags
        bought = False
        if frags > 0:
            buy_body = (codec.pb_uint(1, 11)
                        + codec.pb_uint(2, shop_id)
                        + codec.pb_uint(3, frags))
            cmd, reply = client.call_for(
                CMD_SHOP_BUY, buy_body,
                expect_cmds=(CMD_SHOP_BUY, CMD_ERROR),
                timeout=timeout)
            if cmd == CMD_SHOP_BUY:
                bought = True
            else:
                return {"ok": False, "bought": False, "name": name,
                        "before_level": before_level,
                        "err": f"buy_rejected_{cmd}"}, None
        else:
            bought = True

        # Upgrade
        if do_upgrade:
            up_body = codec.pb_uint(1, 0) + codec.pb_uint(2, skin_id)
            cmd, reply = client.call_for(
                CMD_SKIN_UP, up_body,
                expect_cmds=(CMD_SKIN_UP, CMD_ERROR),
                timeout=timeout)
            if cmd != CMD_SKIN_UP:
                return {"ok": False, "bought": bought, "name": name,
                        "before_level": before_level,
                        "after_level": before_level,
                        "err": f"upgrade_rejected_{cmd}"}, None

            # Verify new level
            car_body2 = client.call(CMD_CAR_PARK_INFO,
                                    _build_car_park_info_body(role_id),
                                    timeout=timeout)
            skins2 = dict(_parse_skin_list(car_body2))
            after_level = skins2.get(skin_id, before_level)
            if after_level <= before_level:
                return {"ok": False, "bought": bought, "name": name,
                        "before_level": before_level,
                        "after_level": after_level,
                        "err": "upgrade_no_levelup"}, None
            return {"ok": True, "bought": bought, "name": name,
                    "before_level": before_level,
                    "after_level": after_level}, None

        return {"ok": True, "bought": bought, "name": name,
                "before_level": before_level,
                "after_level": before_level}, None

    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
