"""比格先生 / 伴侶 (spouse-favor) task over a logged-in WSGameClient — pure WS.

Modelled on ws_token/spirit.py + ws_token/farm.py + ws_token/redpack.py. marry
module 59 (docs/protocol/MARRY_RECON.md). c2s and s2c share the same cmd id.

  marry_status      15105  c2s {}  ->  s2c { state#1, lover_id#2:uint64, ... }
  favor_friend_info 15139  c2s {}  ->  s2c { left_times#1, page#2, num#3,
                                  friend_list#4:repeated p_favor_friend, ... }
  favor_give_flower 15140  c2s { friend_id#1, flower_id#2, num#3 }
                           ->  s2c { update_info#1:p_favor_friend }
  favor_reward_info 15141  c2s {} (or {friend_id})    (NON-married milestones)
  favor_reward_fetch 15142 c2s { friend_id#1, favor_lv#2:uint32 }  (NON-married only)
  marry_mark_info   15131  c2s {}  ->  s2c { start_time#1, next_reward_time#2 }
                                  (MARRIED anniversary countdown — read-only)
  marry_ring_info   15134  c2s {}  ->  s2c { level#1, use#2, exp#3, ... }
  marry_ring_levup  15135  c2s { type#1:uint32 }  ->  s2c { exp#1, old_lev#2, new_lev#3 }

  p_favor_friend { role_id#1:uint64, name#2:string, head#3, favor_lv#4:uint32,
                   favor#5:uint32, cur_power#6:uint64, gender#7, list#8 }

The user's daily/weekly flow (MARRY_RECON.md):
1. 贈禮: send ALL 奶茶 (1106) + ALL 玫瑰 (1614) to the FIRST partner (親密度↑).
   Live-verified (2026-06-09): MarrySendFlowerView m=[1106,1614] — 1031 is NOT
   a valid favor gift (server error 2); use MILK_TEA=1106 / FLOWER=1614.
2. 默契考驗 (the user's "每週日領" reward) = 好感週任務, shown in-game by
   MarryFavorWeekTaskView. These are plain TaskType.Marry (type 6) tasks in the
   global task push, CLAIMED VIA THE TASK SYSTEM — ``main_tasks.claim_marry_tasks``
   (task_commit{type:6, task_id}), NOT via this module. Live-verified 2026-06-10:
   3 (小寶) / 2 (emulator-5554) claimable Marry tasks committed OK, re-read 0 left.
   Do NOT confuse it with the two read-only / unmarried paths below:
   - favor_reward_fetch (15142) is the UNMARRIED friend-favor milestone claim
     (MarryIntimacyView, uses favorcfgs[].level which is built only for
     level_group<3); for MARRIED accounts it returns 0x0201 code 2 for every
     favor_lv — graceful (ok=False, never crashes), but it is the wrong feature.
   - marry_mark_info (15131, read_mark_info) is the結婚週年 countdown
     {start_time, next_reward_time} (~364-day cadence, auto-granted); read-only.
3. 戒指錘鍊: consume ALL 真愛之石 (1114) via marry_ring_levup, loop to zero.

# CRITICAL (live-verified 小寶 2026-06-09): a rejected mutate (give_flower /
#   reward_fetch / ring_levup) replies on the 0x0201 error channel, NOT on the
#   action's own cmd. So every mutate uses ``call_for(cmd, expect_cmds=(cmd,
#   0x0201))`` and treats a 0x0201 reply as failure (error_code), never a plain
#   ``call`` (which would WSTimeoutError-crash on rejection). Reads (status,
#   favor_info, reward_info, ring_info) use plain ``call``.
# Error codes (configErrorInfo): 90=冷卻時間未到, 159=次數不足, 173=活動已結束,
#   2=請求的參數不合法, 3=物品不足 (e.g. give_flower num=0 / ring_levup no stones).

# live-confirm:
#   - inventory counts (奶茶/鮮花/真愛之石 在手幾個) come from the login-time 0x0402
#     push, NOT a request — so "send all" / "forge until empty" take an explicit
#     ``num`` OR loop until the server rejects (code 3 物品不足). This module never
#     reads inventory itself.
#   - marry_ring_levup ``type`` value (1 assumed = 用真愛之石); read once live.
#   - which favor_lv to fetch (from favor_reward_info's claimable milestones).
#   - whether favor_reward_info needs a {friend_id} body (sent here when given).
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
CMD_STATUS = 15105        # marry.marry_status (module 59; empty c2s body)
CMD_FAVOR_INFO = 15139    # marry.favor_friend_info
CMD_GIVE_FLOWER = 15140   # marry.favor_give_flower
CMD_REWARD_INFO = 15141   # marry.favor_reward_info (favor milestones — NON-married)
CMD_REWARD_FETCH = 15142  # marry.favor_reward_fetch (NON-married only; married -> error 2)
CMD_RING_INFO = 15134     # marry.marry_ring_info
CMD_RING_LEVUP = 15135    # marry.marry_ring_levup
CMD_MARK_INFO = 15131     # marry.marry_mark_info (MARRIED weekly/anniversary, read-only)
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code#1}

# goods ids (configGoods CDP).
# Live-verified 2026-06-09: MarrySendFlowerView defines the ONLY two giftable
# favor items as var m=[1106,1614]: 1106=奶茶, 1614=玫瑰.
# 1031 is NOT a valid favor gift (server returns 0x0201 code 2 請求的參數不合法).
FLOWER = 1614             # 玫瑰 (live-verified: ok on 小寶 + emulator-5554)
MILK_TEA = 1106           # 奶茶 (live-verified)
LOVE_STONE = 1114         # 真愛之石

# Error codes decoded from the client (configErrorInfo):
ERR_COOLDOWN = 90         # 冷卻時間未到
ERR_NOT_ENOUGH = 159      # 次數不足
ERR_EVENT_ENDED = 173     # 活動已結束
ERR_BAD_PARAM = 2         # 請求的參數不合法
ERR_NOT_ENOUGH_ITEM = 3   # 物品不足 (e.g. 真愛之石 used up / give num=0)
_ERR_REASONS = {ERR_COOLDOWN: "冷卻時間未到", ERR_NOT_ENOUGH: "次數不足",
                ERR_EVENT_ENDED: "活動已結束", ERR_BAD_PARAM: "請求的參數不合法",
                ERR_NOT_ENOUGH_ITEM: "物品不足"}

_DEFAULT_RING_TYPE = 1    # marry_ring_levup type — 1 assumed = 用真愛之石 (live-confirm)
_DEFAULT_MAX_FORGES = 200
_DEFAULT_SPACING = 0.2
_GIFT_BATCH = 10          # 送禮批次單位（使用者 2026-06-12 改 10）
_GIFT_MAX_BATCHES = 20    # 護欄：最多 20 批
_GIFT_DOWNGRADE_LADDER = (5, 2, 1)  # code=3 物品不足時的降批次序列


@dataclass(frozen=True)
class Partner:
    """One p_favor_friend — a favor partner and their intimacy level/value.

    The FIRST entry of favor_friend_info.friend_list is the 伴侶 (spouse) in the
    99.99% case; its ``role_id`` is the ``friend_id`` used by give_flower /
    reward_fetch.
    """

    role_id: int       # #1
    name: str          # #2
    favor_lv: int      # #4 — 親密度等級
    favor: int         # #5 — 親密度值


@dataclass(frozen=True)
class MarkInfo:
    """marry_mark_info_s2c — the MARRIED couple's anniversary-reward countdown.

    Live-verified 2026-06-09 (both 7fe98fc6 + emulator-5554):
      field1 = start_time        — the marriage date (unix seconds)
      field2 = next_reward_time  — when the next 默契/紀念 reward fires (unix sec)

    The s2c carries NO claim flag and the marry module has NO mark-reward fetch
    cmd: the reward is AUTO-granted server-side at ``next_reward_time`` (the UI
    only shows "距下次獎勵 N 天"). So this is a READ-ONLY status — there is nothing
    to "claim" over WS. ``next_reward_time - start_time`` was ~364 days on both
    accounts (anniversary cadence, NOT weekly).
    """

    start_time: int       # #1 — marriage date (unix seconds)
    next_reward_time: int  # #2 — next auto-reward time (unix seconds)


# --- parsers ----------------------------------------------------------------

def _parse_partner(entry: bytes) -> Partner:
    d = codec.walk_dict(entry)
    return Partner(
        role_id=_as_int(d.get(1)),
        name=_as_str(d.get(2)),
        favor_lv=_as_int(d.get(4)),
        favor=_as_int(d.get(5)),
    )


def parse_favor_info(body: bytes) -> list[Partner]:
    """favor_friend_info_s2c: partners are friend_list#4 (first = the 伴侶)."""
    return [_parse_partner(bytes(val)) for fnum, val in codec.walk(body)
            if fnum == 4 and isinstance(val, (bytes, bytearray))]


def parse_mark_info(body: bytes) -> MarkInfo:
    """marry_mark_info_s2c {start_time#1, next_reward_time#2} -> MarkInfo."""
    d = codec.walk_dict(body)
    return MarkInfo(
        start_time=_as_int(d.get(1)),
        next_reward_time=_as_int(d.get(2)),
    )


# --- body builders ----------------------------------------------------------

def build_give_flower_body(friend_id: int, flower_id: int, num: int) -> bytes:
    """favor_give_flower_c2s {friend_id#1, flower_id#2, num#3}."""
    return (codec.pb_uint(1, friend_id) + codec.pb_uint(2, flower_id)
            + codec.pb_uint(3, num))


def build_reward_fetch_body(friend_id: int, favor_lv: int) -> bytes:
    """favor_reward_fetch_c2s {friend_id#1, favor_lv#2}."""
    return codec.pb_uint(1, friend_id) + codec.pb_uint(2, favor_lv)


def build_ring_levup_body(type_: int) -> bytes:
    """marry_ring_levup_c2s {type#1}."""
    return codec.pb_uint(1, type_)


# --- reads (plain call — no rejection channel) ------------------------------

def read_partner(client: WSGameClient, *, timeout: Optional[float] = None) -> int:
    """marry_status: return ``lover_id`` (#2); 0 means no partner."""
    body = client.call(CMD_STATUS, b"", timeout=timeout)
    lover_id = _as_int(codec.walk_dict(body).get(2))
    logger.info("ws_token couple: marry_status lover_id=%s", lover_id)
    return lover_id


def read_favor_info(
    client: WSGameClient, *, page: int = 1, timeout: Optional[float] = None
) -> list[Partner]:
    """favor_friend_info: parse the partner list (first = 伴侶).

    c2s requires {page#1} (live-confirmed) — an empty body gets NO reply and times
    out. Page 1 holds the partner(s).
    """
    body = codec.pb_uint(1, page)
    return parse_favor_info(client.call(CMD_FAVOR_INFO, body, timeout=timeout))


def read_mark_info(client: WSGameClient, *, timeout: Optional[float] = None) -> MarkInfo:
    """marry_mark_info: the MARRIED couple's anniversary-reward countdown (read-only).

    c2s body is EMPTY (live-confirmed). Returns a :class:`MarkInfo`
    {start_time, next_reward_time}. This is the path the user calls "默契考驗" for a
    MARRIED account — but it is purely informational: the reward is auto-granted
    server-side at ``next_reward_time`` and the marry module exposes NO claim cmd
    (favor_reward_fetch is the NON-married path and returns 0x0201 code 2 for a
    married account). So there is nothing to mutate here — just read the countdown.
    """
    body = client.call(CMD_MARK_INFO, b"", timeout=timeout)
    info = parse_mark_info(body)
    logger.info("ws_token couple: marry_mark_info start_time=%s next_reward_time=%s",
                info.start_time, info.next_reward_time)
    return info


def read_ring(client: WSGameClient, *, timeout: Optional[float] = None) -> dict:
    """marry_ring_info: return {level#1, use#2, exp#3}."""
    d = codec.walk_dict(client.call(CMD_RING_INFO, b"", timeout=timeout))
    return {"level": _as_int(d.get(1)), "use": _as_int(d.get(2)),
            "exp": _as_int(d.get(3))}


# --- mutates (call_for with 0x0201; never plain call) -----------------------

# 0x0201 codes that are actually SUCCESS notices, NOT errors (live-decoded from
# configErrorInfo). 369 = 贈送成功 — favor_give_flower's success comes back on the
# 0x0201 channel with this code, not on its own cmd (verified live on 小寶).
OK_NOTICE_CODES = frozenset({369})


def _mutate(
    client: WSGameClient, cmd: int, body: bytes, *, timeout: Optional[float] = None
) -> tuple[bool, int, bytes]:
    """Send a mutate that can be rejected on 0x0201.

    SUCCESS replies on the action's own ``cmd`` OR on 0x0201 with an OK-notice code
    (e.g. 369 贈送成功); a REJECTION replies on 0x0201 with an error code. Waits for
    EITHER so a rejection records ``ok=False`` + ``error_code`` instead of timing
    out / raising. Returns ``(ok, error_code, reply_body)``.
    """
    reply_cmd, reply = client.call_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        if code in OK_NOTICE_CODES:
            return True, 0, reply        # success notice on the 0x0201 channel
        return False, code, reply
    return True, 0, reply


def give_flower(
    client: WSGameClient, *,
    friend_id: int, flower_id: int, num: int,
    timeout: Optional[float] = None,
) -> dict:
    """favor_give_flower {friend_id, flower_id, num}. Returns {ok, error_code, raw}.

    A rejection (e.g. 0x0201 code 3 物品不足 when num=0 / not enough in hand)
    surfaces ok=False + error_code, never crashes.
    """
    ok, code, reply = _mutate(
        client, CMD_GIVE_FLOWER,
        build_give_flower_body(friend_id, flower_id, num), timeout=timeout)
    if not ok:
        logger.info("ws_token couple: give_flower rejected 0x0201 code=%s %s "
                    "(friend_id=%s flower_id=%s num=%s)", code,
                    _ERR_REASONS.get(code, ""), friend_id, flower_id, num)
    else:
        logger.info("ws_token couple: give_flower ok friend_id=%s flower_id=%s num=%s",
                    friend_id, flower_id, num)
    return {"ok": ok, "error_code": code, "raw": reply}


def give_all(
    client: WSGameClient, *,
    friend_id: int, flower_id: int, num: int,
    timeout: Optional[float] = None,
) -> dict:
    """Send ``num`` of ``flower_id`` to ``friend_id`` in ONE give_flower.

    The server caps the amount to inventory, so a single give_flower of ``num``
    (the in-hand count, supplied by the caller — inventory is the login-time
    0x0402 push, not a request) sends "all". Returns the give_flower result.
    """
    return give_flower(client, friend_id=friend_id, flower_id=flower_id,
                       num=num, timeout=timeout)


def give_all_in_hand(
    client: WSGameClient, *,
    friend_id: int, flower_id: int,
    batch: int = _GIFT_BATCH,
    max_batches: int = _GIFT_MAX_BATCHES,
    spacing: float = _DEFAULT_SPACING,
    timeout: Optional[float] = None,
) -> dict:
    """送光手上全部 ``flower_id``：以 ``batch``(=10) 為單位連送（使用者指定）。

    超量 num 不會被 server 封頂 —— 在手不足整批時直接回 0x0201 code 3 物品不足
    （live 2026-06-12 fc65396d 觀察）。所以 code 3 時沿 ``_GIFT_DOWNGRADE_LADDER``
    降批次重試把餘量送光；num=1 仍回 code 3 才是真正送完。其他錯誤碼立即停。
    回傳 {batches_ok, stopped_reason}。
    """
    batches_ok = 0
    stopped_reason = "max_batches"
    ladder = [n for n in _GIFT_DOWNGRADE_LADDER if n < batch]
    num = batch
    for _ in range(max_batches + len(ladder)):
        out = give_flower(client, friend_id=friend_id, flower_id=flower_id,
                          num=num, timeout=timeout)
        if not out["ok"]:
            stopped_reason = f"error_code={out['error_code']}"
            if out["error_code"] == ERR_NOT_ENOUGH_ITEM and ladder:
                num = ladder.pop(0)   # 物品不足 → 降批次把餘量送光
                continue
            break
        batches_ok += 1
        if batches_ok >= max_batches:
            stopped_reason = "max_batches"
            break
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token couple: give_all_in_hand flower_id=%s batches_ok=%d %s",
                flower_id, batches_ok, stopped_reason)
    return {"batches_ok": batches_ok, "stopped_reason": stopped_reason}


def fetch_favor_reward(
    client: WSGameClient, *,
    friend_id: int, favor_lv: int,
    timeout: Optional[float] = None,
) -> dict:
    """默契考驗: favor_reward_fetch {friend_id, favor_lv}. Returns {ok, error_code, raw}.

    A rejection (e.g. 0x0201 code 159 次數不足 = already claimed this week) surfaces
    ok=False + error_code, never crashes.
    """
    ok, code, reply = _mutate(
        client, CMD_REWARD_FETCH,
        build_reward_fetch_body(friend_id, favor_lv), timeout=timeout)
    if not ok:
        logger.info("ws_token couple: fetch_favor_reward rejected 0x0201 code=%s %s "
                    "(friend_id=%s favor_lv=%s)", code, _ERR_REASONS.get(code, ""),
                    friend_id, favor_lv)
    else:
        logger.info("ws_token couple: fetch_favor_reward ok friend_id=%s favor_lv=%s",
                    friend_id, favor_lv)
    return {"ok": ok, "error_code": code, "raw": reply}


def ring_levup(
    client: WSGameClient, type_: int = _DEFAULT_RING_TYPE, *,
    timeout: Optional[float] = None,
) -> dict:
    """戒指錘鍊: marry_ring_levup {type}. Returns {ok, error_code, exp, old_lev, new_lev}.

    SUCCESS replies on CMD_RING_LEVUP {exp#1, old_lev#2, new_lev#3}; a REJECTION
    (0x0201 code 3 物品不足 = 真愛之石 used up) surfaces ok=False + error_code.
    """
    ok, code, reply = _mutate(
        client, CMD_RING_LEVUP, build_ring_levup_body(type_), timeout=timeout)
    if not ok:
        logger.info("ws_token couple: ring_levup rejected 0x0201 code=%s %s (type=%s)",
                    code, _ERR_REASONS.get(code, ""), type_)
        return {"ok": False, "error_code": code, "exp": 0, "old_lev": 0, "new_lev": 0}
    d = codec.walk_dict(reply)
    out = {"ok": True, "error_code": 0, "exp": _as_int(d.get(1)),
           "old_lev": _as_int(d.get(2)), "new_lev": _as_int(d.get(3))}
    logger.info("ws_token couple: ring_levup ok type=%s old_lev=%s new_lev=%s",
                type_, out["old_lev"], out["new_lev"])
    return out


def forge_ring_until_empty(
    client: WSGameClient, *,
    type_: int = _DEFAULT_RING_TYPE,
    max_forges: int = _DEFAULT_MAX_FORGES,
    spacing: float = _DEFAULT_SPACING,
    timeout: Optional[float] = None,
) -> dict:
    """Loop marry_ring_levup until 真愛之石 runs out or ``max_forges`` is hit.

    STOPS on the FIRST 0x0201 (e.g. code 3 物品不足 = stones used up). Returns
    {forges, last_new_lev, stopped_reason} where ``stopped_reason`` is either
    ``"error_code=<n>"`` (server rejected) or ``"max_forges"`` (cap reached).
    """
    forges = 0
    last_new_lev = 0
    stopped_reason = "max_forges"
    for _ in range(max_forges):
        out = ring_levup(client, type_, timeout=timeout)
        if not out["ok"]:
            stopped_reason = f"error_code={out['error_code']}"
            break
        forges += 1
        last_new_lev = out["new_lev"]
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token couple: forge_ring_until_empty forges=%d last_new_lev=%s "
                "stopped_reason=%s", forges, last_new_lev, stopped_reason)
    return {"forges": forges, "last_new_lev": last_new_lev,
            "stopped_reason": stopped_reason}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _as_str(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(v).decode("utf-8", "replace")
    return "" if v is None else str(v)
