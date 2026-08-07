"""家園管家採購掃蕩 (housekeeper) task over a logged-in WSGameClient — pure WS.

Drives the worker_common family (module 73 / 0x49): read service-expiry state,
optionally renew, then run the购物管家 (shopping) sweep and/or the 副本管家
(dungeon) sweep. Built and framed by codec + client, NOT the in-game netManager.

Field numbers are the live-exported truth (docs/protocol/WORKER_COMMON_PROTO_SCHEMA.json,
module 73; message names carry a 'worker_common_farm_' prefix). c2s/s2c share id:
  info 18692:          c2s {} -> s2c {buy_housekeeper_info#1 repeated p_key_value(k=service_id, v=expiry_ts)}
  buy_service 18693:   c2s {day_num#1 uint32, id#2 uint32} -> info
  shopping 18696:      c2s {} -> s2c {shop_result#1 repeated p_shop_result{id#1,code#2,item_list#3[]}}
  shop_info 18697:     c2s {shop_type#1} -> s2c {shop_type#1, item_list#2 repeated p_key_value}
  set_shop_item 18698: c2s {shop_type#1, item_list#2 repeated p_key_value} -> s2c {result#1}
  dungeon_setting_info 18699: c2s {} -> s2c {setting#1 repeated p_key_kv_list{k#1 int32, list#2[]}}
  dungeon_sweep 18701: c2s {sweep_list#1 repeated p_sweep_req{id#1,level#2,times#3,use_ad#4}}
                       -> s2c {result#1 repeated p_sweep_result{id#1,code#2,reward_list#3[]}}
  p_key_value          { k#1 int64, v#2 int64 }
Service ids (ws_token/data/housekeeper_config.json -> configHousekeeper): 購物管家=1, 副本管家=2.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ws_token import codec, dungeon
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

CMD_ERR = 0x0201        # error.error_info_s2c — 續費被拒時的回應通道
RENEW_TIMEOUT = 8.0     # 續費回應探測上限（可能完全無回應，勿用預設長 timeout）

# --- cmd ids (module 73; c2s and s2c share the same id) ---------------------
CMD_INFO = 18692                  # worker_common_farm_housekeeper_info
CMD_BUY_SERVICE = 18693           # worker_common_farm_housekeeper_buy_service
CMD_SET_SWITCH = 18694            # worker_common_farm_housekeeper_set_switch
CMD_SWITCH_SETTING_INFO = 18695   # worker_common_farm_housekeeper_switch_setting_info
CMD_SHOPPING = 18696              # worker_common_farm_housekeeper_shopping
CMD_SHOP_INFO = 18697             # worker_common_farm_housekeeper_shop_info
CMD_SET_SHOP_ITEM = 18698         # worker_common_farm_housekeeper_set_shop_item
CMD_DUNGEON_SETTING_INFO = 18699  # worker_common_farm_housekeeper_dungeon_setting_info
CMD_SET_DUNGEON = 18700           # worker_common_farm_housekeeper_set_dungeon
CMD_DUNGEON_SWEEP = 18701         # worker_common_farm_housekeeper_dungeon_sweep

# dungeon_setting_info 的 list.k 是 MysteryDungeonKey，不是 level 或 times。
# 遊戲客戶端目前使用 1 表示「開啟副本掃蕩」；2 是武魂試煉的自動購買設定。
DUNGEON_SWEEP_SETTING = 1
DUNGEON_DAILY_REWARD_SETTING = 2

# Service row ids from configHousekeeper (housekeeper_config.json).
SERVICE_SHOPPING = 1  # 購物管家
SERVICE_DUNGEON = 2   # 副本管家

# configHousekeeper.chapter.id -> configChapter_type.type。
# ID 1 是「武魂試煉」專用設定，沒有一般副本掃蕩次數，不能拿來組 sweep_req。
HOUSEKEEPER_CHAPTER_TYPES: dict[int, int] = {
    1: 11,
    2: 2,
    3: 3,
    4: 8,
    5: 9,
    6: 22,
    7: 28,
    8: 29,
    9: 30,
    10: 36,
    11: 38,
    12: 6,
}

# 原生 MysteryDungeonManagerView.GetMaxLimit 會以這些門票的背包現量當 times。
# housekeeper_chapter_limit 的客戶端全域上限目前是 100。
HOUSEKEEPER_CHAPTER_TICKET_ITEMS: dict[int, int] = {
    2: 1003,
    3: 1004,
    4: 1009,
    5: 1011,
    6: 1075,
    11: 1326,
    12: 7002,
}
HOUSEKEEPER_CHAPTER_LIMIT = 100

# configChapter_type.ad > 0 的副本；其餘章節不可送廣告追加次數。
HOUSEKEEPER_AD_CHAPTERS = frozenset({2, 3, 4, 5, 6, 12})

# Renewal length sent in buy_service.day_num.
# live-confirm: day_num semantics — is it the literal day count (30) or the
# 1-based configHousekeeper.price tier index (4 == the 30-day tier)? The pinned
# task spec says day_num is the literal day count; verify before live renew.
RENEW_DAY_NUM = 30


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class HousekeeperInfo:
    """housekeeper_info_s2c: {service_id: expiry_ts} from buy_housekeeper_info."""

    expiry: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ShopResult:
    """One p_housekeeper_shopping_shop_result {id#1, code#2, item_list#3[]}."""

    shop_id: int
    code: int
    items: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ShoppingResult:
    """shopping_s2c: the per-shop results of one purchase sweep."""

    shops: tuple[ShopResult, ...] = ()
    raw: bytes = b""


@dataclass(frozen=True)
class SweepEntry:
    """One p_housekeeper_shopping_sweep_result {id#1, code#2, reward_list#3[]}."""

    chapter_id: int
    code: int
    rewards: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SweepResult:
    """dungeon_sweep_s2c: the per-chapter sweep results."""

    results: tuple[SweepEntry, ...] = ()
    raw: bytes = b""


# --- body builders ----------------------------------------------------------

def build_buy_service_body(day_num: int, service_id: int) -> bytes:
    """buy_service_c2s {day_num#1 uint32, id#2 uint32} — day_num FIRST, then id."""
    return codec.pb_uint(1, day_num) + codec.pb_uint(2, service_id)


def build_set_shop_item_body(
    shop_type: int, item_list: Iterable[tuple[int, int]]
) -> bytes:
    """set_shop_item_c2s {shop_type#1, item_list#2 repeated p_key_value{k,v}}.

    item_list is [(configMall_id, count), ...]. Pure body builder — no智慧選品.
    """
    out = codec.pb_uint(1, shop_type)
    for k, v in item_list:
        out += codec.pb_msg(2, _kv(k, v))
    return out


def build_sweep_body(sweep_list: Iterable[Sequence[int]]) -> bytes:
    """dungeon_sweep_c2s {sweep_list#1 repeated p_sweep_req{id#1,level#2,times#3,use_ad#4}}.

    Each entry is (id, level, times[, use_ad]); use_ad defaults to 0. Automatic
    derivation is handled separately by ``derive_sweep_list`` after reading the
    real switch settings and available dungeon levels.
    # live-confirm: sweep_list[].id maps to chapter id (1-12) vs chapter_type;
    #               level/times source; ticket (門票) consumption per sweep.
    """
    out = b""
    for entry in sweep_list:
        vals = tuple(entry)
        cid, level, times = vals[0], vals[1], vals[2]
        use_ad = vals[3] if len(vals) > 3 else 0
        req = (codec.pb_uint(1, cid) + codec.pb_uint(2, level)
               + codec.pb_uint(3, times) + codec.pb_uint(4, use_ad))
        out += codec.pb_msg(1, req)
    return out


# --- parsers ----------------------------------------------------------------

def parse_info(body: bytes) -> HousekeeperInfo:
    """housekeeper_info_s2c: buy_housekeeper_info#1 repeated p_key_value(k,v)."""
    expiry: dict[int, int] = {}
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            expiry[_as_int(kv.get(1))] = _as_int(kv.get(2))
    return HousekeeperInfo(expiry=expiry)


def parse_shopping(body: bytes) -> ShoppingResult:
    """shopping_s2c: shop_result#1 repeated p_housekeeper_shopping_shop_result."""
    shops: list[ShopResult] = []
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            shops.append(_parse_shop_result(bytes(val)))
    return ShoppingResult(shops=tuple(shops), raw=body)


def parse_sweep(body: bytes) -> SweepResult:
    """dungeon_sweep_s2c: result#1 repeated p_housekeeper_shopping_sweep_result."""
    results: list[SweepEntry] = []
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            results.append(_parse_sweep_result(bytes(val)))
    return SweepResult(results=tuple(results), raw=body)


def parse_dungeon_setting(body: bytes) -> dict[int, dict[int, int]]:
    """dungeon_setting_info_s2c: setting#1 repeated p_key_kv_list{k#1, list#2[]}.

    Returns {k: {sub_k: sub_v}} — the per-chapter (or per-key) setting map.
    """
    out: dict[int, dict[int, int]] = {}
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            k, kvs = _parse_kv_list(bytes(val))
            out[k] = kvs
    return out


def _parse_shop_result(entry: bytes) -> ShopResult:
    d = codec.walk(entry)
    shop_id = code = 0
    items: dict[int, int] = {}
    for fnum, val in d:
        if fnum == 1:
            shop_id = _as_int(val)
        elif fnum == 2:
            code = _as_int(val)
        elif fnum == 3 and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            items[_as_int(kv.get(1))] = _as_int(kv.get(2))
    return ShopResult(shop_id=shop_id, code=code, items=items)


def _parse_sweep_result(entry: bytes) -> SweepEntry:
    d = codec.walk(entry)
    chapter_id = code = 0
    rewards: dict[int, int] = {}
    for fnum, val in d:
        if fnum == 1:
            chapter_id = _as_int(val)
        elif fnum == 2:
            code = _as_int(val)
        elif fnum == 3 and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            rewards[_as_int(kv.get(1))] = _as_int(kv.get(2))
    return SweepEntry(chapter_id=chapter_id, code=code, rewards=rewards)


def _parse_kv_list(entry: bytes) -> tuple[int, dict[int, int]]:
    """p_key_kv_list {k#1 int32, list#2 repeated p_key_value} -> (k, {sub_k: sub_v})."""
    k = 0
    kvs: dict[int, int] = {}
    for fnum, val in codec.walk(entry):
        if fnum == 1:
            k = _as_int(val)
        elif fnum == 2 and isinstance(val, (bytes, bytearray)):
            sub = codec.walk_dict(bytes(val))
            kvs[_as_int(sub.get(1))] = _as_int(sub.get(2))
    return k, kvs


# --- state / activity -------------------------------------------------------

def _error_code(body: bytes) -> int:
    """error.error_info_s2c {error_code#1}; 0 if unparseable."""
    try:
        return int(codec.walk_dict(body).get(1) or 0)
    except Exception:
        return 0


def read_info(client: WSGameClient, *, timeout: Optional[float] = None) -> HousekeeperInfo:
    """Read housekeeper service-expiry state (info 18692, empty request body)."""
    return parse_info(client.call(CMD_INFO, b"", timeout=timeout))


def is_active(info: HousekeeperInfo, service_id: int, *, serv_time: int) -> bool:
    """True iff the service's expiry timestamp is strictly after serv_time."""
    return info.expiry.get(service_id, 0) > serv_time


def ensure_active(
    client: WSGameClient,
    service_id: int,
    *,
    serv_time: int,
    renew: bool = False,
    timeout: Optional[float] = None,
) -> bool:
    """Return whether ``service_id`` is in期; optionally auto-renew if expired.

    renew=True AND expired -> send buy_service{day_num=30, id=service_id} (spends
    家園幣), then re-read info to confirm. Defaults to renew=False so a stale
    service is reported, not silently bought.
    """
    info = read_info(client, timeout=timeout)
    if is_active(info, service_id, serv_time=serv_time):
        return True
    if not renew:
        logger.info("ws_token steward: service %s expired, renew=False -> skip", service_id)
        return False
    logger.info("ws_token steward: renewing service %s (day_num=%d)", service_id, RENEW_DAY_NUM)
    # 續費回應不是 18693 echo：成功回 info(18692)、被拒走 0x0201、也可能無回應
    # （live 2026-07-12 手機：只等 18693 → timeout → 購物+副本整包沒跑）。
    try:
        rc, rb = client.call_for(
            CMD_BUY_SERVICE, build_buy_service_body(RENEW_DAY_NUM, service_id),
            expect_cmds=(CMD_BUY_SERVICE, CMD_INFO, CMD_ERR),
            timeout=timeout if timeout is not None else RENEW_TIMEOUT)
        if rc == CMD_ERR:
            logger.warning("ws_token steward: renew service %s rejected code=%s",
                           service_id, _error_code(rb))
    except WSTimeoutError:
        logger.warning("ws_token steward: renew service %s no response", service_id)
    info = read_info(client, timeout=timeout)
    return is_active(info, service_id, serv_time=serv_time)


# --- task actions -----------------------------------------------------------

def run_shopping(client: WSGameClient, *, timeout: Optional[float] = None) -> ShoppingResult:
    """購物管家: send shopping{} (empty body); server sweeps the account's list."""
    return parse_shopping(client.call(CMD_SHOPPING, b"", timeout=timeout))


def read_dungeon_setting(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> dict[int, dict[int, int]]:
    """副本管家: read switch settings (dungeon_setting_info 18699).

    回傳 ``{configHousekeeper.chapter.id: {MysteryDungeonKey: enabled}}``；
    內層 key 是設定枚舉，不是副本 level/times。
    """
    return parse_dungeon_setting(client.call(CMD_DUNGEON_SETTING_INFO, b"", timeout=timeout))


def read_dungeon_levels(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> dict[int, int]:
    """讀取遊戲副本 level，轉成 housekeeper chapter id -> 原生掃蕩 level。"""
    rows = dungeon.list_dungeons(client, timeout=timeout)
    rows_by_type = {
        int(row.type): row
        for row in rows
        if int(row.max_level) > 0
    }
    out: dict[int, int] = {}
    for chapter_id, chapter_type in HOUSEKEEPER_CHAPTER_TYPES.items():
        row = rows_by_type.get(chapter_type)
        if row is None:
            continue
        # 武魂每日報酬原生 sweepShow 使用目前層，而不是最高通關層。
        level = row.cur_level if chapter_id == 1 else row.max_level
        if int(level) > 0:
            out[chapter_id] = int(level)
    return out


def derive_sweep_list(
    setting: Mapping[int, Mapping[int, int]],
    dungeon_levels: Mapping[int, int] | None = None,
    *,
    inventory_counts: Mapping[int, int] | None = None,
    times: int = 1,
    use_ad: int | None = None,
) -> list[tuple[int, int, int, int]]:
    """由真實副本設定與 level 組出副本管家 sweep_list。

    ``setting`` 的內層 key 是設定枚舉；level 來自 ``dungeon_list``。
    武魂每日報酬是 id=1/times=0 的特殊項目。一般副本若有背包快照，times
    依原生客戶端取門票現量；門票為 0 仍保留項目，讓 use_ad=1 消耗純廣告次數。
    """
    if not dungeon_levels:
        return []
    try:
        sweep_times = max(1, int(times))
    except (TypeError, ValueError):
        sweep_times = 1
    out: list[tuple[int, int, int, int]] = []
    for chapter_id in sorted(setting):
        try:
            switches = setting[chapter_id]
            level = int(dungeon_levels.get(int(chapter_id), 0))
        except (AttributeError, TypeError, ValueError):
            continue
        chapter_id = int(chapter_id)
        if level <= 0:
            continue
        if chapter_id == 1:
            if int(switches.get(DUNGEON_DAILY_REWARD_SETTING, 0)) > 0:
                out.append((chapter_id, level, 0, 0))
            continue
        if int(switches.get(DUNGEON_SWEEP_SETTING, 0)) <= 0:
            continue

        chapter_times = sweep_times
        ticket_item = HOUSEKEEPER_CHAPTER_TICKET_ITEMS.get(chapter_id)
        if inventory_counts is not None and ticket_item is not None:
            try:
                owned = max(0, int(inventory_counts.get(ticket_item, 0)))
            except (AttributeError, TypeError, ValueError):
                owned = 0
            chapter_times = min(owned, HOUSEKEEPER_CHAPTER_LIMIT)
        if use_ad is None:
            ad = int(chapter_id in HOUSEKEEPER_AD_CHAPTERS)
        else:
            ad = int(bool(use_ad))
        out.append((chapter_id, level, chapter_times, ad))
    return out


def run_dungeon_sweep(
    client: WSGameClient,
    sweep_list: Iterable[Sequence[int]],
    *,
    timeout: Optional[float] = None,
) -> SweepResult:
    """副本管家: send dungeon_sweep{sweep_list} and parse the per-chapter result.

    sweep_list is caller-supplied [(id, level, times[, use_ad]), ...]; v1 does
    NOT auto-derive level/times. See build_sweep_body for the live-confirm notes.
    """
    return parse_sweep(
        client.call(CMD_DUNGEON_SWEEP, build_sweep_body(sweep_list), timeout=timeout))


# --- orchestrator -----------------------------------------------------------

def run(
    client: WSGameClient,
    *,
    serv_time: int,
    do_shopping: bool = True,
    do_dungeon: bool = False,
    sweep_list: Optional[Iterable[Sequence[int]]] = None,
    renew: bool = False,
    timeout: Optional[float] = None,
) -> dict:
    """Top-level housekeeper task: ensure-active gates each sub-task.

    Returns a summary dict with the activity flags and (optional) result objects.
    renew=True will spend 家園幣 to re-up an expired service before acting.
    """
    summary: dict = {
        "shopping_active": False, "shopping": None,
        "dungeon_active": False, "sweep": None,
    }

    if do_shopping:
        active = ensure_active(client, SERVICE_SHOPPING,
                               serv_time=serv_time, renew=renew, timeout=timeout)
        summary["shopping_active"] = active
        if active:
            summary["shopping"] = run_shopping(client, timeout=timeout)

    if do_dungeon:
        active = ensure_active(client, SERVICE_DUNGEON,
                               serv_time=serv_time, renew=renew, timeout=timeout)
        summary["dungeon_active"] = active
        if active and sweep_list:
            summary["sweep"] = run_dungeon_sweep(client, sweep_list, timeout=timeout)
        elif active:
            logger.info("ws_token steward: dungeon active but no sweep_list -> skip sweep")

    logger.info("ws_token steward: shopping_active=%s dungeon_active=%s",
                summary["shopping_active"], summary["dungeon_active"])
    return summary


# --- helpers ----------------------------------------------------------------

def _kv(k: int, v: int) -> bytes:
    """Encode one p_key_value {k#1 int64, v#2 int64}."""
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
