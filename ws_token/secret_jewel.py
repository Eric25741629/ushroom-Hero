"""秘寶 (尋寶) draws + daily 尋寶圖 purchase over a logged-in WSGameClient — pure WS.

Modelled on ws_token/spirit.py (同構). secret_jewel module 85, live-decoded
2026-06-27 on 5556 (docs/protocol/SECRET_JEWEL_RECON.md). c2s and s2c share the
same cmd id; FAILURES reply on the 0x0201 error channel.

  secret_jewel_info  21761  c2s {}  ->  s2c { jewel_list#1, suit_list#2,
                                              pool_list#3:repeated p_secret_jewel_pool,
                                              ext#4:repeated p_key_value }
  secret_jewel_draw  21764  c2s { pool_type#1:uint32, count#2:uint32 }
                            ->  s2c { pool#1:p_secret_jewel_pool, reward_list#2:repeated p_reward }
  shop_buy           6914   c2s { shop_type#1=26, shop_id#2=2600001, num#3 }   (買尋寶圖 1340)
  shop_info          6913   c2s { shop_type#1=26 }
                            ->  s2c { shop_type#1, buy_list#2:repeated {shop_id#1, bought#2} }

  p_secret_jewel_pool { pool_type#1:uint32, free_times#2:uint32, must_info#3:p_key_value[] }
  p_reward            { gtid#1:int32, num#2:int64 }

``free_times`` = remaining FREE draws today for that pool (user: 2 free/day).
:func:`draw_free` consumes ONLY free draws on the **塵世** pool — it never pays.
:func:`buy_daily_maps` SPENDS 粉鑽 (item 1340 尋寶圖 costs 600 粉鑽 each, daily cap 10)
so it is caller-gated by its own flag.

# Only 塵世秘寶 (pool_type=1) is open; 傳說(2)/遠古(3) are locked (still report
#   free_times=2 in info but draws would be rejected) — so draw_free targets pool 1 only.
# Failures (draw / shop_buy) reply on 0x0201, NOT the action cmd, so every mutate uses
#   ``call_for(cmd, expect_cmds=(cmd, 0x0201))`` and treats 0x0201 as failure (error_code).
#   Reads (info / shop_info) use plain ``call``.
# Both daily counters (free_times / shop bought) are SERVER-side daily-reset, so every
#   action is idempotent across hourly wakes — NO ws_state date gate needed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- cmd ids (c2s and s2c share the same id; FAILURES reply on 0x0201) -------
CMD_INFO = 21761          # secret_jewel.secret_jewel_info (module 85; empty c2s body)
CMD_DRAW = 21764          # secret_jewel.secret_jewel_draw
CMD_SHOP_BUY = 6914       # shop_buy (尋寶圖; shop module 27)
CMD_SHOP_INFO = 6913      # shop_info (今日已買數)
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code#1}

# 塵世秘寶 (the only unlocked pool). 傳說=2 / 遠古=3 locked.
POOL_DUST = 1

# 每日購買尋寶圖 (configMall 2600001: goods 尋寶圖1340 x1, cost 粉鑽 x600, daily cap 10).
SHOP_TYPE_MAP = 26
SHOP_ID_MAP = 2600001
MAP_ITEM = 1340           # 尋寶圖
DAILY_BUY_CAP = 10

# Error codes decoded from the client (configErrorInfo), shared across modules:
_ERR_REASONS = {90: "冷卻時間未到", 159: "次數不足", 173: "活動已結束"}

_DEFAULT_SPACING = 0.3


@dataclass(frozen=True)
class JewelPool:
    """One p_secret_jewel_pool — its type and remaining FREE draws today."""

    pool_type: int     # #1
    free_times: int    # #2 — remaining free draws today for this pool

    @property
    def has_free(self) -> bool:
        """True iff this pool still has a free draw available today."""
        return self.free_times > 0


# --- parsers ----------------------------------------------------------------

def parse_info(body: bytes) -> list[JewelPool]:
    """secret_jewel_info_s2c -> pools from pool_list#3 (repeated p_secret_jewel_pool)."""
    pools: list[JewelPool] = []
    for fnum, val in codec.walk(body):
        if fnum == 3 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            pools.append(JewelPool(
                pool_type=_as_int(d.get(1)),
                free_times=_as_int(d.get(2)),
            ))
    return pools


def _parse_rewards(body: bytes) -> dict[int, int]:
    """secret_jewel_draw_s2c.reward_list#2: repeated p_reward{gtid#1, num#2} -> {gtid: num}."""
    rewards: dict[int, int] = {}
    for fnum, val in codec.walk(body):
        if fnum == 2 and isinstance(val, (bytes, bytearray)):
            r = codec.walk_dict(bytes(val))
            gtid = _as_int(r.get(1))
            rewards[gtid] = rewards.get(gtid, 0) + _as_int(r.get(2))
    return rewards


def parse_shop_bought(body: bytes, shop_id: int) -> int:
    """shop_info_s2c {shop_type#1, buy_list#2:{shop_id#1, bought#2}} -> today's bought for shop_id."""
    for fnum, val in codec.walk(body):
        if fnum == 2 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            if _as_int(d.get(1)) == shop_id:
                return _as_int(d.get(2))
    return 0


# --- body builders ----------------------------------------------------------

def build_draw_body(pool_type: int, count: int) -> bytes:
    """secret_jewel_draw_c2s {pool_type#1, count#2} — pool_type FIRST, then count."""
    return codec.pb_uint(1, pool_type) + codec.pb_uint(2, count)


def build_buy_body(shop_type: int, shop_id: int, num: int) -> bytes:
    """shop_buy_c2s {shop_type#1, shop_id#2, num#3}."""
    return (codec.pb_uint(1, shop_type) + codec.pb_uint(2, shop_id)
            + codec.pb_uint(3, num))


def build_shop_info_body(shop_type: int) -> bytes:
    """shop_info_c2s {shop_type#1}."""
    return codec.pb_uint(1, shop_type)


# --- reads ------------------------------------------------------------------

def read_info(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> list[JewelPool]:
    """Send secret_jewel_info (empty body) and parse the pool list (free_times)."""
    body = client.call(CMD_INFO, b"", timeout=timeout)
    pools = parse_info(body)
    logger.info("ws_token secret_jewel: info %d pool(s) %s", len(pools),
                {p.pool_type: p.free_times for p in pools})
    return pools


def read_shop_bought(
    client: WSGameClient, *, shop_type: int = SHOP_TYPE_MAP,
    shop_id: int = SHOP_ID_MAP, timeout: Optional[float] = None,
) -> Optional[int]:
    """Today's bought count for ``shop_id`` via shop_info (6913). None on read failure."""
    try:
        body = client.call(CMD_SHOP_INFO, build_shop_info_body(shop_type),
                           timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — read failure → caller falls back to server cap
        logger.info("ws_token secret_jewel: shop_info 讀取失敗 (%s)，改靠 server 上限", exc)
        return None
    return parse_shop_bought(body, shop_id)


# --- mutates ----------------------------------------------------------------

def draw(
    client: WSGameClient, pool_type: int, count: int, *,
    timeout: Optional[float] = None,
) -> dict:
    """secret_jewel_draw {pool_type, count}. Returns {ok, error_code, rewards}.

    SUCCESS replies on CMD_DRAW (reward_list#2 -> ``rewards`` {gtid: num}); a
    REJECTION replies on 0x0201. Waits for EITHER so a rejected draw records
    ``ok=False`` + ``error_code`` instead of raising.
    """
    reply_cmd, reply = client.call_for(
        CMD_DRAW, build_draw_body(pool_type, count),
        expect_cmds=(CMD_DRAW, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.info("ws_token secret_jewel: draw rejected 0x0201 code=%s %s (pool=%s)",
                    code, _ERR_REASONS.get(code, ""), pool_type)
        return {"ok": False, "error_code": code, "rewards": {}}
    rewards = _parse_rewards(reply)
    logger.info("ws_token secret_jewel: draw ok pool=%s count=%d rewards=%s",
                pool_type, count, rewards)
    return {"ok": True, "error_code": 0, "rewards": rewards}


def draw_free(
    client: WSGameClient, *, pool_type: int = POOL_DUST,
    spacing: float = _DEFAULT_SPACING, timeout: Optional[float] = None,
) -> dict:
    """Read pools, then draw the 塵世 pool's free_times — ONE single pull at a time.

    Only the unlocked pool (``pool_type``, default 塵世=1) is drawn; locked pools
    report free_times>0 too but would be rejected. Each pull is count=1 (the free
    draw); stops early if a pull is rejected (cooldown / 次數不足). Never pays.

    Returns {pool_type, free_times, drew, ok, error_code, rewards}.
    """
    pools = read_info(client, timeout=timeout)
    pool = next((p for p in pools if p.pool_type == pool_type), None)
    if pool is None or not pool.has_free:
        free = pool.free_times if pool else 0
        return {"pool_type": pool_type, "free_times": free, "drew": 0,
                "ok": True, "error_code": 0, "rewards": {},
                "skipped": "no free draw"}
    rewards: dict[int, int] = {}
    drew = 0
    last_code = 0
    for _ in range(pool.free_times):
        out = draw(client, pool_type, 1, timeout=timeout)  # single free pull
        if not out["ok"]:
            last_code = out["error_code"]
            break
        drew += 1
        for gtid, num in out["rewards"].items():
            rewards[gtid] = rewards.get(gtid, 0) + num
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token secret_jewel: draw_free pool=%s drew=%d rewards=%s",
                pool_type, drew, rewards)
    return {"pool_type": pool_type, "free_times": pool.free_times, "drew": drew,
            "ok": drew > 0, "error_code": last_code, "rewards": rewards}


def buy_one_map(
    client: WSGameClient, *, timeout: Optional[float] = None,
) -> dict:
    """Buy ONE 尋寶圖 via shop_buy {26, 2600001, 1}. Returns {ok, error_code}.

    SPENDS 粉鑽. A rejection (daily cap / insufficient 粉鑽) replies on 0x0201.
    """
    reply_cmd, reply = client.call_for(
        CMD_SHOP_BUY, build_buy_body(SHOP_TYPE_MAP, SHOP_ID_MAP, 1),
        expect_cmds=(CMD_SHOP_BUY, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        return {"ok": False, "error_code": code}
    return {"ok": True, "error_code": 0}


def buy_daily_maps(
    client: WSGameClient, *, target: int = DAILY_BUY_CAP,
    spacing: float = _DEFAULT_SPACING, timeout: Optional[float] = None,
) -> dict:
    """買尋寶圖 up to ``target``/day (default 10). SPENDS 粉鑽 — caller-gated.

    Reads today's bought count (shop_info 6913) and buys the remainder ONE at a
    time, stopping early on a 0x0201 rejection (cap reached / 粉鑽不足). Idempotent:
    re-running the same day buys only the remaining count (server caps anyway, so a
    failed read just falls back to buying up to ``target`` and stopping on 0x0201).

    Returns {bought_before, bought, target, error_code, results}.
    """
    bought_before = read_shop_bought(client, timeout=timeout)
    start = bought_before if bought_before is not None else 0
    remaining = max(0, target - start)
    bought = 0
    last_code = 0
    results: list[dict] = []
    for _ in range(remaining):
        out = buy_one_map(client, timeout=timeout)
        results.append(out)
        if not out["ok"]:
            last_code = out["error_code"]
            logger.info("ws_token secret_jewel: buy stopped 0x0201 code=%s %s",
                        last_code, _ERR_REASONS.get(last_code, ""))
            break
        bought += 1
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token secret_jewel: buy_daily_maps before=%s bought=%d target=%d",
                bought_before, bought, target)
    return {"bought_before": bought_before, "bought": bought, "target": target,
            "error_code": last_code, "results": results}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
