"""守護靈 (guardian spirit) draws over a logged-in WSGameClient — pure WS.

Modelled on ws_token/turntable.py + ws_token/farm.py. spirit module 77
(docs/protocol/SPIRIT_PROTO_SCHEMA.json). c2s and s2c share the same cmd id.

  spirit_draw_info  19743  c2s {}  ->  s2c { draw_list#1:repeated p_spirit_draw }
  spirit_draw       19744  c2s { draw_id#1:uint32, count#2:uint32 }
                           ->  s2c { new_draw#1:p_spirit_draw, reward#2:repeated p_reward }
  shop_buy          6914   c2s { shop_type#1, shop_id#2, num#3 }   (招喚貨幣 purchase)

  p_spirit_draw { draw_id#1:uint32, free_times#2:uint32, must_info#3:repeated p_key_value }
  p_reward      { gtid#1:int32, num#2:int64 }

``free_times`` = remaining FREE draws today for that pool (user: 2 free/day).
:func:`draw_all_free` consumes ONLY free draws — it never pays. Buying 招喚貨幣
(:func:`buy_summon_currency`) SPENDS premium currency, so it is caller-gated and
takes an explicit ``shop_id``.

# CRITICAL (live-verified 小寶 2026-06-09): a rejected mutate (spirit_draw /
#   shop_buy) replies on the 0x0201 error channel, NOT on the action's own cmd.
#   So every mutate uses ``call_for(cmd, expect_cmds=(cmd, 0x0201))`` and treats a
#   0x0201 reply as failure (error_code), never a plain ``call`` (which would
#   WSTimeoutError-crash on rejection). Reads (spirit_draw_info) use plain ``call``.
# Error codes (configErrorInfo): 90=冷卻時間未到, 159=次數不足, 173=活動已結束.

# live-confirm:
#   - real draw_id values + free_times caps (read once via spirit_draw_info).
#   - shop_id for 招喚貨幣 (configMall value) — passed in, never hardcoded.
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
CMD_DRAW_INFO = 19743     # spirit.spirit_draw_info (module 77; empty c2s body)
CMD_DRAW = 19744          # spirit.spirit_draw
CMD_BUY_SUMMON = 6914     # shop_buy (招喚貨幣; shop module 27)
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code#1}

# Error codes decoded from the client (configErrorInfo):
ERR_COOLDOWN = 90         # 冷卻時間未到
ERR_NOT_ENOUGH = 159      # 次數不足
ERR_EVENT_ENDED = 173     # 活動已結束
_ERR_REASONS = {ERR_COOLDOWN: "冷卻時間未到", ERR_NOT_ENOUGH: "次數不足",
                ERR_EVENT_ENDED: "活動已結束"}

_DEFAULT_SPACING = 0.3


@dataclass(frozen=True)
class SpiritDrawPool:
    """One p_spirit_draw pool — its id and remaining FREE draws today."""

    draw_id: int       # #1
    free_times: int    # #2 — remaining free draws today for this pool

    @property
    def has_free(self) -> bool:
        """True iff this pool still has a free draw available today."""
        return self.free_times > 0


# --- parsers ----------------------------------------------------------------

def parse_draw_info(body: bytes) -> list[SpiritDrawPool]:
    """spirit_draw_info_s2c {draw_list#1:repeated p_spirit_draw} -> pools."""
    pools: list[SpiritDrawPool] = []
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            pools.append(SpiritDrawPool(
                draw_id=_as_int(d.get(1)),
                free_times=_as_int(d.get(2)),
            ))
    return pools


def _parse_rewards(body: bytes) -> dict[int, int]:
    """spirit_draw_s2c.reward#2: repeated p_reward{gtid#1, num#2} -> {gtid: num}."""
    rewards: dict[int, int] = {}
    for fnum, val in codec.walk(body):
        if fnum == 2 and isinstance(val, (bytes, bytearray)):
            r = codec.walk_dict(bytes(val))
            gtid = _as_int(r.get(1))
            rewards[gtid] = rewards.get(gtid, 0) + _as_int(r.get(2))
    return rewards


# --- body builders ----------------------------------------------------------

def build_draw_body(draw_id: int, count: int) -> bytes:
    """spirit_draw_c2s {draw_id#1, count#2} — draw_id FIRST, then count."""
    return codec.pb_uint(1, draw_id) + codec.pb_uint(2, count)


def build_buy_summon_body(shop_type: int, shop_id: int, num: int) -> bytes:
    """shop_buy_c2s {shop_type#1, shop_id#2, num#3} — 招喚貨幣 purchase."""
    return (codec.pb_uint(1, shop_type) + codec.pb_uint(2, shop_id)
            + codec.pb_uint(3, num))


# --- orchestrators ----------------------------------------------------------

def read_draw_info(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> list[SpiritDrawPool]:
    """Send spirit_draw_info (empty body) and parse the pool list."""
    body = client.call(CMD_DRAW_INFO, b"", timeout=timeout)
    return parse_draw_info(body)


def draw(
    client: WSGameClient, draw_id: int, count: int, *,
    timeout: Optional[float] = None,
) -> dict:
    """spirit_draw {draw_id, count}. Returns {ok, error_code, rewards}.

    SUCCESS replies on CMD_DRAW (reward#2 parsed into ``rewards`` {gtid: num}); a
    REJECTION replies on the 0x0201 error channel. Waits for EITHER so a rejected
    draw records ``ok=False`` + ``error_code`` instead of timing out / raising.
    """
    reply_cmd, reply = client.call_for(
        CMD_DRAW, build_draw_body(draw_id, count),
        expect_cmds=(CMD_DRAW, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.info("ws_token spirit: draw rejected 0x0201 code=%s %s (draw_id=%s)",
                    code, _ERR_REASONS.get(code, ""), draw_id)
        return {"ok": False, "error_code": code, "rewards": {}}
    rewards = _parse_rewards(reply)
    logger.info("ws_token spirit: draw ok draw_id=%s count=%d rewards=%s",
                draw_id, count, rewards)
    return {"ok": True, "error_code": 0, "rewards": rewards}


def draw_all_free(
    client: WSGameClient, *,
    spacing: float = _DEFAULT_SPACING,
    timeout: Optional[float] = None,
) -> dict:
    """Read pools, then draw every pool's free_times (ONLY free draws).

    For each pool with ``free_times > 0`` issue one draw with ``count =
    free_times``; pools with no free draws are skipped. Rewards are summed across
    pools into ``rewards`` {gtid: num}. A rejected pool (0x0201) is recorded but
    yields no rewards. Never pays — paid draws / buying 招喚貨幣 are caller-gated.

    Returns {pools_drawn, rewards, results} where ``results`` is one dict per
    attempted pool: {draw_id, count, ok, error_code, rewards}.
    """
    pools = read_draw_info(client, timeout=timeout)
    pools_drawn = 0
    rewards: dict[int, int] = {}
    results: list[dict] = []
    free_pools = [p for p in pools if p.has_free]
    for pool in free_pools:
        out = draw(client, pool.draw_id, pool.free_times, timeout=timeout)
        if out["ok"]:
            pools_drawn += 1
            for gtid, num in out["rewards"].items():
                rewards[gtid] = rewards.get(gtid, 0) + num
        results.append({
            "draw_id": pool.draw_id,
            "count": pool.free_times,
            "ok": out["ok"],
            "error_code": out["error_code"],
            "rewards": out["rewards"],
        })
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token spirit: draw_all_free pools=%d free_pools=%d drawn=%d rewards=%s",
                len(pools), len(free_pools), pools_drawn, rewards)
    return {"pools_drawn": pools_drawn, "rewards": rewards, "results": results}


def buy_summon_currency(
    client: WSGameClient, *,
    shop_type: int, shop_id: int, num: int,
    timeout: Optional[float] = None,
) -> dict:
    """招喚貨幣: shop_buy {shop_type, shop_id, num}. SPENDS — caller-gated.

    Returns {ok, error_code}. A rejection replies on 0x0201 (e.g. 159 次數不足).
    # live-confirm: shop_id 是 configMall 的招喚貨幣項,必須由呼叫端帶入,不可硬編。
    """
    reply_cmd, reply = client.call_for(
        CMD_BUY_SUMMON, build_buy_summon_body(shop_type, shop_id, num),
        expect_cmds=(CMD_BUY_SUMMON, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning("ws_token spirit: buy_summon rejected 0x0201 code=%s %s "
                       "(shop_id=%s num=%s)", code, _ERR_REASONS.get(code, ""),
                       shop_id, num)
        return {"ok": False, "error_code": code}
    logger.info("ws_token spirit: buy_summon ok shop_id=%s num=%s", shop_id, num)
    return {"ok": True, "error_code": 0}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
