"""Idle / offline reward (掛機獎勵 / 離線獎勵) over a logged-in WSGameClient.

Pure WS, modelled on ws_token/redpack.py. The main_chapter module (13) serves
both the ONLINE accrual (打怪掛機, type=1) and the OFFLINE accrual (離線, type=2)
through the same two cmds:

  main_chapter_reward_info  3333  c2s{type#1:uint32} ->
      s2c{type#1, time#2:uint32, res_list#3:p_reward[], item_list#4:p_reward[]}
  main_chapter_claim_reward 3334  c2s{type#1:uint32} -> s2c (ack; body may be empty)
  p_reward { gtid#1:int32, num#2:int64 }

Flow:
- ONLINE  (type=1): pull with reward_info_c2s{1}, then claim_reward_c2s{1}.
- OFFLINE (type=2): the server PUSHES reward_info_s2c{type:2,...} right after
  login (no c2s needed); claim with claim_reward_c2s{2}.

NOTE: this only collects the *base* accrual. The in-game "看廣告加倍" (watch-ad
doubling) cannot be reproduced over WS — there is no ad SDK here — so we always
take the plain amount.

# live-confirm: 離線 reward 是否登入即 push reward_info_s2c{type:2}、以及在沒收到
#   push 的情況下 claim_reward_c2s{2} 能否直接領到 server 暫存的離線獎勵。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_REWARD_INFO = 3333    # main_chapter.main_chapter_reward_info_c2s/_s2c
CMD_CLAIM_REWARD = 3334   # main_chapter.main_chapter_claim_reward_c2s/_s2c

TYPE_ONLINE = 1           # 在線掛機 (pulled via reward_info_c2s)
TYPE_OFFLINE = 2          # 離線 (pushed by server after login)


@dataclass(frozen=True)
class IdleReward:
    """A parsed main_chapter_reward_info_s2c snapshot."""

    type: int                              # #1 (1=online, 2=offline)
    time: int                              # #2 accrued seconds
    res_list: list[tuple[int, int]]        # #3 p_reward[] -> (gtid, num)
    item_list: list[tuple[int, int]]       # #4 p_reward[] -> (gtid, num)
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def is_claimable(self) -> bool:
        """True iff there is anything to claim (res_list or item_list非空)."""
        return bool(self.res_list) or bool(self.item_list)


@dataclass(frozen=True)
class ClaimResult:
    type: int                 # the type we claimed (1 or 2)
    success: bool             # True iff the claim ack arrived on CMD_CLAIM_REWARD
    response_cmd: int
    response_body: bytes


def _rewards(body: bytes, fnum: int) -> list[tuple[int, int]]:
    """Decode every repeated p_reward at ``fnum`` into (gtid, num) pairs."""
    out: list[tuple[int, int]] = []
    for fn, v in codec.walk(body):
        if fn == fnum and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            out.append((_as_int(d.get(1)), _as_int(d.get(2))))
    return out


def parse_reward_info(body: bytes) -> IdleReward:
    """Parse main_chapter_reward_info_s2c into an :class:`IdleReward`."""
    d = codec.walk_dict(body)
    return IdleReward(
        type=_as_int(d.get(1)),
        time=_as_int(d.get(2)),
        res_list=_rewards(body, 3),
        item_list=_rewards(body, 4),
        raw=d,
    )


def build_reward_info_body(type_: int) -> bytes:
    """main_chapter_reward_info_c2s {type#1:uint32}."""
    return codec.pb_uint(1, type_)


def build_claim_body(type_: int) -> bytes:
    """main_chapter_claim_reward_c2s {type#1:uint32}."""
    return codec.pb_uint(1, type_)


def read_online(client: WSGameClient, *, timeout: Optional[float] = None) -> IdleReward:
    """Pull the ONLINE accrual: send reward_info_c2s{type=1}, parse the reply."""
    body = client.call(CMD_REWARD_INFO, build_reward_info_body(TYPE_ONLINE),
                       timeout=timeout)
    return parse_reward_info(body)


def claim(client: WSGameClient, type_: int, *,
          timeout: Optional[float] = None) -> ClaimResult:
    """Send claim_reward_c2s{type} and return whether the ack arrived."""
    cmd, body = client.call_for(
        CMD_CLAIM_REWARD, build_claim_body(type_),
        expect_cmds=(CMD_CLAIM_REWARD,), timeout=timeout)
    return ClaimResult(type=type_, success=(cmd == CMD_CLAIM_REWARD),
                       response_cmd=cmd, response_body=body)


def claim_online(client: WSGameClient, *,
                 timeout: Optional[float] = None) -> Optional[ClaimResult]:
    """read_online -> claim(1) only when there is something to claim.

    Returns the :class:`ClaimResult` on a claim, or ``None`` if nothing accrued.
    """
    info = read_online(client, timeout=timeout)
    if not info.is_claimable:
        logger.info("ws_token idle_reward: online accrual empty; nothing to claim")
        return None
    logger.info("ws_token idle_reward: online claim time=%ds res=%s item=%s",
                info.time, info.res_list, info.item_list)
    return claim(client, TYPE_ONLINE, timeout=timeout)


def claim_offline_from_push(client: WSGameClient, push_body: bytes, *,
                            timeout: Optional[float] = None) -> Optional[ClaimResult]:
    """Given an already-received offline push body, parse then claim(2).

    The offline reward arrives as a server push (reward_info_s2c{type:2}) right
    after login; capture it via ``WSGameClient(creds, push_handler=...)``. Only
    claims when the push actually carried rewards. Returns ``None`` if empty.
    """
    info = parse_reward_info(push_body)
    if not info.is_claimable:
        logger.info("ws_token idle_reward: offline push empty; nothing to claim")
        return None
    logger.info("ws_token idle_reward: offline claim time=%ds res=%s item=%s",
                info.time, info.res_list, info.item_list)
    return claim(client, TYPE_OFFLINE, timeout=timeout)


def claim_offline(client: WSGameClient, *,
                  timeout: Optional[float] = None) -> ClaimResult:
    """Claim the OFFLINE reward directly (no push needed).

    The server keeps a pending offline reward, so claim_reward_c2s{2} should be
    redeemable even when we did not capture the login push. Use
    :func:`claim_offline_from_push` instead when the push body is on hand and you
    want to skip the claim if it was empty.
    """
    logger.info("ws_token idle_reward: offline claim (direct, no push)")
    return claim(client, TYPE_OFFLINE, timeout=timeout)


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
