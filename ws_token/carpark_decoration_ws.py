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
  - 769   role_info (0x0301)  (菇車幣 = numeric role attr 201; empty c2s -> fresh
          snapshot, so each read returns the LATEST balance)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_CAR_PARK_INFO = 12801
CMD_SHOP_INFO = 6913
CMD_SHOP_BUY = 6914
CMD_SKIN_UP = 12817        # s2c reply cmd; the c2s goes via the json envelope
CMD_JSON_PROTO = 14337     # json_proto.json_proto_c2s {1:proto_id, 2:msg-json}.
                           # The real client sends skin_up with useJson=true
                           # (netManager.send(name, msg, true)); a raw-protobuf
                           # 12817 c2s is handled erratically by the server
                           # (live 2026-07-05: executed+replied / executed
                           # silently / silently ignored).
CMD_ERROR = 0x0201  # 513
CMD_ROLE_INFO = 0x0301  # 769 — role.role_info; empty c2s body -> fresh role
                        # snapshot (server replies 769 AND re-pushes 769).
ROLE_ATTR_CAR_COIN = 201  # 菇車幣 = numeric role attribute id (NOT a bag item)

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

def _is_on_sale(open_time, now=None) -> bool:
    """True iff ``now`` falls inside a configMall ``open_time`` window.

    ``open_time`` = ``[[[Y,M,D],[h,m,s]], [[Y,M,D],[h,m,s]]]`` (start, end) or
    falsy for evergreen items. Seasonal frags outside their window are rejected
    by the server with 0x0201 code=283 (live 2026-07-05, shop 1739).
    """
    if not open_time:
        return True
    try:
        import datetime
        (sd, st), (ed, et) = open_time
        start = datetime.datetime(*sd, *st)
        end = datetime.datetime(*ed, *et)
        # Assumes host TZ == server TZ (UTC+8, true for this deployment).
        # Worst case at a boundary: quota is zeroed and the planner skips —
        # never a bad mutation.
        now = now or datetime.datetime.now()
        return start <= now <= end
    except Exception:  # noqa: BLE001 — malformed window: assume on sale
        return True


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


def parse_role_num_attrs(body: bytes) -> dict[int, int]:
    """Parse numeric role attributes ``{attr_id: value}`` from a 0x0301 body.

    role_info_s2c (769) structure, live-verified on 7fe98fc6 (2026-06-27):
        {1: {1: repeated attr_id(varint),
             2: {1: repeated num_attr{1:id, 2:int_value},
                 2: repeated str_attr{1:id, 2:bytes}}}}
    Only the numeric table (path 1.2.1[]) is returned; 菇車幣 = attr 201.
    """
    out: dict[int, int] = {}
    for f1, v1 in codec.walk(body):
        if f1 == 1 and isinstance(v1, (bytes, bytearray)):           # 1
            for f2, v2 in codec.walk(bytes(v1)):
                if f2 == 2 and isinstance(v2, (bytes, bytearray)):    # 1.2
                    for f3, v3 in codec.walk(bytes(v2)):
                        if f3 == 1 and isinstance(v3, (bytes, bytearray)):  # 1.2.1[]
                            d = codec.walk_dict(bytes(v3))
                            aid, val = d.get(1), d.get(2)
                            if isinstance(aid, int) and isinstance(val, int):
                                out[aid] = val
    return out


def read_car_coin(client: WSGameClient, *, timeout: float = 10.0) -> tuple[int | None, str | None]:
    """Read the LATEST 菇車幣 via pure WS. Returns ``(coin, error)``.

    Sends an empty c2s 769 (role_info); the server answers with a fresh role
    snapshot whose numeric attribute 201 is the current 菇車幣 balance. This is
    an on-demand read, so the value is always live — no stale login cache.
    菇車幣 is a role attribute (gtid < 1000), so it is NOT in the 0x0401 bag
    snapshot nor the 0x0402 item pushes; role_info is the only WS source.
    """
    try:
        cmd, reply = client.call_for(
            CMD_ROLE_INFO, b"", expect_cmds=(CMD_ROLE_INFO,), timeout=timeout)
        if cmd != CMD_ROLE_INFO or not reply:
            return None, "role_info_empty"
        coin = parse_role_num_attrs(reply).get(ROLE_ATTR_CAR_COIN)
        if coin is None:
            return None, "attr_201_absent_in_role_info"
        return int(coin), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


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
            # Seasonal frag outside its open_time window: the server rejects
            # the buy (0x0201 code=283), so zero the quota — planner skips it.
            off_shelf = bool(sh) and not _is_on_sale(sh.get("open_time"))
            if off_shelf:
                limit_remaining = 0

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
                "off_shelf": off_shelf,
            })

        decos.sort(key=lambda d: d["id"])

        coin, coin_error = read_car_coin(client, timeout=timeout)
        return {
            "coin": coin,
            "coin_source": "role_info_0x0301",
            "coin_error": coin_error,
            "decos": decos,
            "deco_count": len(decos),
        }, None

    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# Reply frames for 6914/12817 are a fast path only: live (2026-07-05, 7fe98fc6)
# skin_up replies can go missing even when the action landed. Wait this long
# for the reply, then fall back to ground-truth re-reads (6913 buy counts /
# 12801 levels) instead of trusting the frame.
_REPLY_WAIT_S = 8.0
# The server silently DROPS a skin_up arriving too soon after the previous one
# (live: ~1s gaps dropped, 10s gaps ok). After an unconfirmed upgrade, wait
# this long, re-verify (a late landing must not be re-sent), then retry once.
_COOLDOWN_WAIT_S = 6.0


def _error_code(body: bytes):
    """Decode error_info_s2c {error_code#1}; falls back to '?' if unparsable."""
    try:
        code = codec.walk_dict(body).get(1)
        return int(code) if isinstance(code, int) else "?"
    except Exception:  # noqa: BLE001
        return "?"


def _read_levels(client: WSGameClient, role_id: int,
                 timeout: float) -> dict[int, int]:
    body = client.call(CMD_CAR_PARK_INFO,
                       _build_car_park_info_body(role_id),
                       timeout=timeout)
    return dict(_parse_skin_list(body))


def _read_buy_counts(client: WSGameClient, timeout: float) -> dict[int, int]:
    body = client.call(CMD_SHOP_INFO, _build_shop_info_body(), timeout=timeout)
    return _parse_shop_buy_info(body)


def _cum_frags(catalog: dict, deco_id: int, level: int) -> int:
    """Ladder frags consumed to stand at ``level`` (expend of rows 1..level-1).

    Row 0 (the acquisition row) is excluded: decorations come from the free
    ParkingDecorateSelectView picker, so shop-bought frags only ever feed star
    upgrades — ``shop_bought - _cum_frags`` is then exactly the frags still
    held. If an acquisition DID consume row 0 the estimate overshoots by that
    row; the executor self-heals by buying the shortfall when the upgrade is
    rejected (see exec_buy_and_upgrade).
    """
    total = 0
    for lv in range(1, max(1, int(level))):
        row = catalog.get((deco_id, lv))
        if row and row.get("expend"):
            e = row["expend"][0]
            total += int(e[1]) if len(e) >= 2 else 0
    return total


def exec_buy_and_upgrade(
    client: WSGameClient,
    shop_id: int,
    skin_id: int,
    frags: int,
    *,
    do_upgrade: bool = True,
    timeout: float = 10.0,
    target_level: int | None = None,
    skin_up_gap: float = 0.0,
) -> tuple[dict | None, str | None]:
    """Buy frags + upgrade one star via pure WS. Returns (result, error).

    result: {ok, bought, name, before_level, after_level, frags_bought,
    resent, err?}
    Mutations are ground-truth verified by re-reads: a lost reply frame is not
    treated as failure, and a real 0x0201 reject surfaces its decoded code.

    ``skin_up_gap``: minimum seconds between consecutive skin_up SENDS on this
    connection (the server silently drops closely-spaced ones). The wait counts
    from the previous send timestamp stamped on ``client``, so time this step
    spends on reads/buys is credited — only the remainder is slept.
    ``resent=True`` in the result means the first send was dropped and a
    re-send was needed: the caller should back off to a wider gap.

    Idempotent by design so an interrupted step can be retried/re-run safely:
    - ``target_level``: if the decoration already sits at/above it (the
      interrupted upgrade landed after all), return ok without mutating.
    - frags already held (shop bought count minus the ladder's consumption for
      the current level) are not re-bought — only the shortfall is purchased.
    """
    try:
        from ws_token.client import WSTimeoutError

        role_id = client._creds.role_id
        catalog = _load_catalog()
        name = _name_of(catalog, skin_id)
        reply_wait = min(_REPLY_WAIT_S, timeout)

        before_level = _read_levels(client, role_id, timeout).get(skin_id, 0)
        if target_level is not None and before_level >= int(target_level):
            return {"ok": True, "bought": False, "already_done": True,
                    "name": name, "before_level": before_level,
                    "after_level": before_level, "frags_bought": 0}, None

        frags_bought = 0
        resent = False

        def _fail(err: str, after: int | None = None):
            return {"ok": False, "bought": frags_bought > 0, "name": name,
                    "before_level": before_level,
                    "after_level": before_level if after is None else after,
                    "frags_bought": frags_bought, "resent": resent,
                    "err": err}, None

        def _buy_frags(qty: int, pre_count: int | None = None):
            """Buy ``qty`` frags. -> (True, None) | (False, err_string).

            Reply frame = fast path; on timeout the buy is ground-truth
            verified via the 6913 count delta.
            """
            if pre_count is None:
                pre_count = _read_buy_counts(client, timeout).get(shop_id, 0)
            buy_body = (codec.pb_uint(1, 11)
                        + codec.pb_uint(2, shop_id)
                        + codec.pb_uint(3, qty))
            try:
                cmd, reply = client.call_for(
                    CMD_SHOP_BUY, buy_body,
                    expect_cmds=(CMD_SHOP_BUY, CMD_ERROR),
                    timeout=reply_wait)
                if cmd == CMD_SHOP_BUY:
                    return True, None
                return False, f"buy_rejected_code_{_error_code(reply)}"
            except WSTimeoutError:
                post_count = _read_buy_counts(client, timeout).get(shop_id, 0)
                if post_count >= pre_count + qty:
                    return True, None  # reply frame lost but the buy landed
                return False, "buy_unconfirmed_no_reply"

        if frags > 0:
            pre_count = _read_buy_counts(client, timeout).get(shop_id, 0)
            held = max(0, pre_count - _cum_frags(catalog, skin_id, before_level))
            to_buy = max(0, frags - held)
            if to_buy:
                ok, buy_err = _buy_frags(to_buy, pre_count)
                if not ok:
                    return _fail(buy_err)
                frags_bought = to_buy
        bought = True

        if not do_upgrade:
            return {"ok": True, "bought": bought, "name": name,
                    "before_level": before_level,
                    "after_level": before_level,
                    "frags_bought": frags_bought}, None

        def _send_upgrade():
            """-> ('rejected', code) | ('replied', None) | ('silent', None)."""
            up_msg = json.dumps({"type": 0, "skin_id": int(skin_id)},
                                separators=(",", ":"))
            up_body = codec.pb_uint(1, CMD_SKIN_UP) + codec.pb_str(2, up_msg)
            if skin_up_gap > 0:
                last = getattr(client, "_last_skin_up_ts", None)
                if last is not None:
                    wait = skin_up_gap - (time.monotonic() - last)
                    if wait > 0:
                        time.sleep(wait)
            client._last_skin_up_ts = time.monotonic()
            try:
                cmd, reply = client.call_for(
                    CMD_JSON_PROTO, up_body,
                    expect_cmds=(CMD_SKIN_UP, CMD_ERROR),
                    timeout=reply_wait)
                if cmd != CMD_SKIN_UP:
                    return "rejected", _error_code(reply)
                return "replied", None
            except WSTimeoutError:
                return "silent", None

        outcome, code = _send_upgrade()
        if outcome == "rejected" and frags_bought < frags:
            # The buy was skipped/shortened on the held-frags estimate; a
            # reject here can mean the estimate overshot (e.g. an acquisition
            # consumed row 0). Buy the remaining shortfall once and resend —
            # total bought never exceeds ``frags``, so no double spend.
            ok, buy_err = _buy_frags(frags - frags_bought)
            if not ok:
                return _fail(buy_err)
            frags_bought = frags
            outcome, code = _send_upgrade()
        if outcome == "rejected":
            return _fail(f"upgrade_rejected_code_{code}")
        after_level = _read_levels(client, role_id, timeout).get(
            skin_id, before_level)
        if after_level <= before_level and outcome == "silent":
            # Cooldown drop or lost/late reply: wait, re-verify (a late landing
            # must NOT be re-sent), then retry once.
            time.sleep(_COOLDOWN_WAIT_S)
            after_level = _read_levels(client, role_id, timeout).get(
                skin_id, before_level)
            if after_level <= before_level:
                resent = True
                outcome, code = _send_upgrade()
                after_level = _read_levels(client, role_id, timeout).get(
                    skin_id, before_level)
                # A rejected retry can mask a late-landing first send (frags
                # already consumed): trust the level re-read, not the reject.
                if outcome == "rejected" and after_level <= before_level:
                    return _fail(f"upgrade_rejected_code_{code}")

        if after_level <= before_level:
            return _fail("upgrade_no_levelup", after=after_level)
        return {"ok": True, "bought": bought, "name": name,
                "before_level": before_level,
                "after_level": after_level,
                "frags_bought": frags_bought, "resent": resent}, None

    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
