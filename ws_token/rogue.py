"""萬神試煉 (roguelike, module 76 "rogue") — 本周積分獎勵一鍵領取 over pure WS.

The live 萬神試煉 is the roguelike RogueView (module 76), NOT the older dungeon
type=23 in ``ws_token.dungeon``. Its weekly 積分 (points) milestone rewards are
all granted by ONE empty-body request:

  rogue_week_reward_c2s  19482 (0x4C1A)  c2s {}            (empty body)
  rogue_week_reward_s2c  19482           s2c { get_list#1:uint32[],
                                               reward_list#2:p_reward[] }
  p_reward                                   { gtid#1:int32, num#2:int64 }

LIVE-captured 2026-06-13 (小寶 7fe98fc6, CDP 9226) and cross-checked against
docs/protocol/ROGUE_PROTO_SCHEMA.json:
  c2s 19482 {}  ->  s2c 19482 {1=[1..10], 2=[(1501,200),(2,1800),(1291,1),(1,10000)]}

The s2c carries NO code field, so any 19482 reply is success; an empty get_list
just means "nothing left to claim this week" (still success). A real failure
(event not open / not unlocked) arrives as a 0x0201 error frame instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_WEEK_REWARD = 19482    # 0x4C1A rogue.rogue_week_reward_c2s/_s2c (empty c2s body)
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}


@dataclass(frozen=True)
class WeekRewardResult:
    """Outcome of rogue_week_reward (本周積分獎勵一鍵領取) or its failure."""

    success: bool                       # True iff a rogue_week_reward_s2c arrived
    claimed: tuple[int, ...] = ()       # get_list: milestone indices claimed now
    rewards: dict[int, int] = field(default_factory=dict)  # {gtid: num}
    response_cmd: int = 0
    fields: dict = field(compare=False, default_factory=dict)
    error_code: int | None = None
    error: str | None = None


def _collect_rewards(body: bytes, reward_field: int) -> dict[int, int]:
    """Decode every p_reward {gtid#1, num#2} on ``reward_field`` into {gtid: num}."""
    rewards: dict[int, int] = {}
    for fnum, v in codec.walk(body):
        if fnum == reward_field and isinstance(v, (bytes, bytearray)):
            r = codec.walk_dict(bytes(v))
            gtid = r.get(1)
            if isinstance(gtid, int):
                num = r.get(2)
                rewards[gtid] = int(num) if isinstance(num, int) else 0
    return rewards


def parse_week_reward(cmd: int, body: bytes) -> WeekRewardResult:
    """rogue_week_reward_s2c {get_list#1:uint32[], reward_list#2:p_reward[]}.

    No code field — any 19482 reply is success (empty get_list = already claimed).
    A 0x0201 frame is a server error (failure).
    """
    if cmd == CMD_WEEK_REWARD:
        claimed = tuple(v for fnum, v in codec.walk(body)
                        if fnum == 1 and isinstance(v, int))
        return WeekRewardResult(
            success=True, claimed=claimed,
            rewards=_collect_rewards(body, 2),
            response_cmd=cmd, fields=codec.walk_dict(body))
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return WeekRewardResult(success=False, response_cmd=cmd,
                                fields=codec.walk_dict(body), error_code=ec,
                                error=f"server error code={ec}")
    return WeekRewardResult(success=False, response_cmd=cmd,
                            fields=codec.walk_dict(body),
                            error=f"unexpected response cmd 0x{cmd:04x}")


def claim_week_reward(client: WSGameClient, *,
                      timeout: float | None = None) -> WeekRewardResult:
    """一鍵領取本周積分獎勵: send rogue_week_reward (19482) with an empty body.

    Replies on 19482 (rewards, possibly empty) or 0x0201 (event not open).
    """
    cmd, body = client.call_for(
        CMD_WEEK_REWARD, b"",
        expect_cmds=(CMD_WEEK_REWARD, CMD_ERROR), timeout=timeout)
    result = parse_week_reward(cmd, body)
    if result.success:
        logger.info("ws_token rogue: week_reward claimed=%s rewards=%s",
                    result.claimed, result.rewards)
    else:
        logger.warning("ws_token rogue: week_reward failed cmd=0x%04x code=%s",
                       cmd, result.error_code)
    return result
