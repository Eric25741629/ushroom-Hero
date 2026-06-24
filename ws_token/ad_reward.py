"""看廣告獎勵 (ad reward) — pure-WS auto-claim over the ad module (22 / 0x16).

ALL "watch ad to get X" buttons in 菇勇者 go through ONE generic cmd. The client
calls ``tryWatchAd(adType)`` which — for a NO_ADS-privilege (bought ad-free)
account — short-circuits to ``reqAdReward(config_id, ext=[], is_free=1)`` and the
reward is granted INSTANTLY (no video). This module reproduces that call purely
over :class:`WSGameClient`, so a device can auto-claim its daily 鑽石/種子 ad
rewards without a browser. Live-verified 2026-06-19 (5556 web_h5 CDP 9223); see
memory reference_ws_ad_reward_protocol.

Cmd map::

  ad_info   0x1601 (5633)  c2s {}  (empty body)
                           -> s2c repeated field1 = {config_id#1, count#2,
                                                     _#3, next_ts#4}
  ad_reward 0x1602 (5634)  c2s {config_id#1, is_free#3=1}  (ext#2 omitted)
                           -> s2c 0x1602 {1: {config_id#1, count#2, _#3, next_ts#4}}
                              (the real reward arrives as an item_change push
                              0x0406 — detection does NOT parse it; a 0x1602 reply
                              that is NOT 0x0201 means success)
                           -> 0x0201 {error_code#1} on reject (daily-limit = 89)

The discipline mirrors farm shop "read today's count → claim only the deficit →
skip at the cap" (:mod:`ws_token.farm`): read ``ad_info`` ONCE, then per config_id
claim only up to ``TIMES[config_id]`` and never when ``next_ts`` is still in the
future (cooldown not elapsed) — both gates skip WITHOUT sending any packet, so we
never trip the server's cooldown reject. A config_id absent from the s2c list is
treated as count=0 (same convention as shop_info).

Reward reference (for log / comments only; the server grants it server-side):
  12 → 鑽石 (item 2) ×200   14 → 鑽石 ×100   15 → 初級種子 (item 101) ×3
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- cmd ids (ad module 22 / 0x16); c2s and s2c share the same id ------------
CMD_AD_REWARD = 0x1602   # 5634  ad_reward_c2s {config_id, ext, is_free}
CMD_AD_INFO = 0x1601     # 5633  ad_info_c2s {} -> repeated per-config counts
CMD_ERROR = 0x0201       # error.error_info_s2c {error_code#1}

# Server is_free flag. The account bought NO_ADS, so is_free=1 grants the reward
# instantly with no video (live-verified 2026-06-19).
IS_FREE = 1

# Per-config daily cap (configAds ``times``). 13/16/17 are out of the default
# ad_rewards scope but kept so callers that opt them in are still capped
# correctly — 13 (AD_TURNTABLE) is claimed by ws_token.turntable, not here.
TIMES: dict[int, int] = {1: 2, 2: 2, 3: 2, 12: 3, 13: 2, 14: 5, 15: 2, 16: 2, 17: 4}

# Human names for logging (AdType / configAds key).
AD_NAMES: dict[int, str] = {
    1: "挖礦鎬子廣告",
    2: "挖礦鑽頭廣告",
    3: "挖礦炸彈廣告",
    12: "商城廣告鑽石",
    13: "轉盤廣告次數",
    14: "浮動廣告鑽石",
    15: "農場種子廣告",
    16: "農場肥料廣告",
    17: "建築加速廣告",
}

# The config_ids auto-claimed by default (user-specified 2026-06-19).
DEFAULT_CONFIG_IDS: list[int] = [1, 2, 3, 12, 14, 15]

# Server error code when the daily limit is exhausted (mirrors gacha 89).
_ERROR_DAILY_LIMIT = 89

# Hard guard: never loop more than the largest daily cap for one config_id.
_MAX_CLAIM_LOOPS = 32


# --- body builder -----------------------------------------------------------

def build_ad_reward_body(config_id: int, is_free: int = IS_FREE) -> bytes:
    """ad_reward_c2s {config_id#1, is_free#3} — ext#2 (repeated) omitted.

    Same two-varint shape as gacha's free-summon (which IS this cmd with the
    AdType enum in field#1).
    """
    return codec.pb_uint(1, config_id) + codec.pb_uint(3, is_free)


# --- result -----------------------------------------------------------------

@dataclass(frozen=True)
class AdClaimResult:
    """Outcome of one 0x1602 ad-reward claim."""

    success: bool
    response_cmd: int = 0
    error_code: Optional[int] = None
    new_count: Optional[int] = None   # on_ad_reward_s2c new_ad.count#2
    next_ts: Optional[int] = None     # on_ad_reward_s2c new_ad.next_ts#4

    @property
    def rejected(self) -> bool:
        return self.response_cmd == CMD_ERROR


# --- parsers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def parse_ad_counts(body: bytes) -> dict[int, dict]:
    """ad_info_s2c: repeated field1 = {config_id#1, count#2, _#3, next_ts#4}.

    Returns ``{config_id: {"count": int, "next_ts": int}}``. config_ids with no
    entry are absent (callers default them to count=0 / next_ts=0).
    """
    out: dict[int, dict] = {}
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            cid = _as_int(d.get(1))
            out[cid] = {"count": _as_int(d.get(2)), "next_ts": _as_int(d.get(4))}
    return out


def parse_ad_reward(cmd: int, body: bytes) -> AdClaimResult:
    """on_ad_reward_s2c {1: {config_id, count, _, next_ts}} or 0x0201 error.

    Success = a 0x1602 reply (NOT 0x0201). The real reward is an item_change push
    (0x0406) we do not need to parse.
    """
    if cmd == CMD_AD_REWARD:
        msg = codec.walk_dict(body).get(1)
        new_count = next_ts = None
        if isinstance(msg, (bytes, bytearray)):
            inner = codec.walk_dict(bytes(msg))
            new_count = _as_int(inner.get(2))
            next_ts = _as_int(inner.get(4))
        return AdClaimResult(success=True, response_cmd=cmd,
                             new_count=new_count, next_ts=next_ts)
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return AdClaimResult(success=False, response_cmd=cmd,
                             error_code=int(ec) if isinstance(ec, int) else None)
    return AdClaimResult(success=False, response_cmd=cmd)


# --- single calls -----------------------------------------------------------

def read_ad_counts(client: WSGameClient, *,
                   timeout: Optional[float] = None) -> dict[int, dict]:
    """ad_info (0x1601, empty body): read today's per-config watch counts.

    Returns ``{config_id: {"count": int, "next_ts": int}}``; missing config_ids
    are absent (treat as count=0). Unlike home_farm_info, ad_info answers reliably
    on every call (live).
    """
    cmd, reply = client.call_for(CMD_AD_INFO, b"", expect_cmds=(CMD_AD_INFO,),
                                 timeout=timeout)
    return parse_ad_counts(reply)


def claim_once(client: WSGameClient, config_id: int, *,
               is_free: int = IS_FREE,
               timeout: Optional[float] = None) -> AdClaimResult:
    """Send one 0x1602 {config_id, is_free}; reply on 0x1602 or 0x0201."""
    cmd, reply = client.call_for(
        CMD_AD_REWARD, build_ad_reward_body(config_id, is_free),
        expect_cmds=(CMD_AD_REWARD, CMD_ERROR), timeout=timeout)
    return parse_ad_reward(cmd, reply)


# --- orchestrators ----------------------------------------------------------

def claim_ad(client: WSGameClient, config_id: int, *,
             is_free: int = IS_FREE,
             counts: Optional[dict[int, dict]] = None,
             now: Optional[float] = None,
             timeout: Optional[float] = None) -> dict:
    """Claim a single ad reward UP TO its daily cap, respecting cooldown.

    Reads today's ``count`` / ``next_ts`` (from ``counts`` if supplied, else a
    fresh ad_info read) and:

      - cap gate: remaining = TIMES[config_id] - count; remaining <= 0 →
        ``{"skipped": "maxed N/N"}`` WITHOUT sending any packet.
      - cooldown gate: next_ts > now → ``{"skipped": "cooldown until ..."}``
        WITHOUT sending any packet (avoids the server's cooldown reject).

    Otherwise loops claim_once: on success bumps the claimed count, adopts the
    server's returned next_ts / new_count, and stops when ``claimed`` reaches
    ``remaining`` OR the server hands back a future next_ts (cooldown kicked in).
    A 0x0201 reject stops the loop and records the error code. Returns
    ``{"name", "claimed", "stopped"}`` (or ``{"skipped"}``).
    """
    name = AD_NAMES.get(config_id, str(config_id))
    times = TIMES.get(config_id)
    if times is None:
        return {"name": name, "skipped": f"unknown config_id {config_id}"}

    now = time.time() if now is None else now
    if counts is None:
        counts = read_ad_counts(client, timeout=timeout)
    info = counts.get(config_id) or {}
    count = _as_int(info.get("count"))
    next_ts = _as_int(info.get("next_ts"))

    remaining = times - count
    if remaining <= 0:
        return {"name": name, "skipped": f"maxed {count}/{times}"}
    if next_ts and next_ts > now:
        return {"name": name, "skipped": f"cooldown until {next_ts}"}

    claimed = 0
    stopped: Optional[str] = None
    for _ in range(min(remaining, _MAX_CLAIM_LOOPS)):
        r = claim_once(client, config_id, is_free=is_free, timeout=timeout)
        if not r.success:
            stopped = (f"error_code={r.error_code}" if r.rejected
                       else f"cmd=0x{r.response_cmd:04x}")
            break
        claimed += 1
        if r.new_count is not None:
            count = r.new_count
        if r.next_ts:
            next_ts = r.next_ts
        if claimed >= remaining:
            stopped = "remaining_zero"
            break
        if next_ts and next_ts > now:
            stopped = f"cooldown until {next_ts}"
            break

    logger.info("ws_token ad_reward[%s] config=%s -> claimed=%s (%s)",
                name, config_id, claimed, stopped or "done")
    return {"name": name, "claimed": claimed, "stopped": stopped}


def claim_ads(client: WSGameClient,
              config_ids: Iterable[int] = DEFAULT_CONFIG_IDS,
              *, timeout: Optional[float] = None) -> dict:
    """Claim every config_id's ad reward off ONE ad_info read.

    Reads today's counts once (degrading to ``{}`` = all zero if the read fails,
    in which case every id is still attempted — the per-id cap/cooldown then
    self-corrects from each claim's reply). Returns
    ``{"results": {name: claim_ad_result}, "total_claimed": int}``.
    """
    ids = list(config_ids)
    now = time.time()
    try:
        counts = read_ad_counts(client, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — degrade to zero counts, never abort
        logger.warning(
            "ws_token ad_reward: ad_info 讀取失敗 (%s); 視為全 0，改靠 server 0x0201 "
            "界限（已滿/冷卻會被擋下，claim_ad 自停）", exc)
        counts = {}

    results: dict[str, dict] = {}
    total = 0
    for cid in ids:
        res = claim_ad(client, cid, counts=counts, now=now, timeout=timeout)
        results[res.get("name", str(cid))] = res
        total += int(res.get("claimed", 0) or 0)

    logger.info("ws_token ad_reward: claim_ads ids=%s total_claimed=%s",
                ids, total)
    return {"results": results, "total_claimed": total}
