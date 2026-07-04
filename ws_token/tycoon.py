"""傳奇大亨 (Legendary Tycoon / 大富翁 monopoly board) over a logged-in WSGameClient.

Pure WS, LIVE-verified on emulator-5556 (2026-06-14). Modelled on
ws_token/redpack.py + ws_token/couple.py. The activity lives in the **`act`
module (24)**, NOT act2/module 25 (the prior recon guessed 25 from a config-table
name — wrong). The dice is **server-authoritative**: the client sends an empty
roll (act_type only) and the server replies with the rolled point + the new board
position + the landed-tile reward, which is auto-granted (an `inventory 0x0402`
push follows). There is **no separate reward-claim cmd** — every roll's reward is
granted immediately, so "auto-claim" == rolling while dice remain.

  act.act_monopoly_info_c2s   6312 (0x18a8)  c2s {act_type#1}
  act.act_monopoly_info_s2c        (0x18a8)  s2c {act_type#1, circle#2, pos#3,
        dice_time#4, double_card_open#5, event#6:p_monopoly_event,
        grid_list#7:p_monopoly_grid[], reward_circle_list#8:uint32[]}
  act.act_monopoly_dice_c2s   6313 (0x18a9)  c2s {act_type#1}   <- "roll", empty but act_type
  act.act_monopoly_dice_s2c        (0x18a9)  s2c {act_type#1, dice_num#2,  <- SERVER rolls
        circle#3, pos#4, event#5:p_monopoly_event, reward#6:p_monopoly_reward[]}
  act.act_monopoly_dice_time_s2c 6314 (0x18aa) s2c {act_type#1, dice_time#2}  <- regen push
  act.act_task_update_s2c     6158 (0x180e)  s2c {type#1, task_list#2:p_act_task[]}  <- 創業日誌

  p_monopoly_grid   { grid_id#1, cfg_id#2, type#3 }           (20 tiles, type=1)
  p_monopoly_reward { type#1, item#2:p_key_value{k,v}, ...#3 }  (landed reward)

LIVE evidence (2026-06-14, account 菜雞 on 5556):
  roll #1: c2s 0x18a9 [08 a3 1f] -> s2c [08a31f 1006 2007 ...]  dice_num=6 pos=7
  roll #2: c2s 0x18a9 [08 a3 1f] -> s2c [08a31f 1003 200a ...]  dice_num=3 pos=10
  both auto-granted item 1401 +20 via 0x0402 push. No client RNG, no pos_list to
  report (unlike the guild dice board which sends guild_area_move {pos_list}).

VERDICT: server-authoritative -> pure-WS auto-roll is correct. Send the empty
roll, read dice_num/pos/reward from the same-cmd reply. Stop when the server
rejects on 0x0201 (out of dice) or ``max_rolls`` is hit.

# live-confirm (NOT yet observed): the exact 0x0201 error_code when out of dice
#   (dice count is client-computed from dice_time regen; the s2c carries no
#   "remaining" field). auto_play treats ANY 0x0201 as "stop". The 創業日誌 task
#   rewards (act_task_update push) are claimed via the global task system, NOT
#   this module — same split as couple's 默契考驗.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- cmd ids (c2s and s2c share the same id; FAILURES reply on 0x0201) -------
CMD_INFO = 0x18A8          # 6312 act.act_monopoly_info (board state)
CMD_DICE = 0x18A9          # 6313 act.act_monopoly_dice (roll — server-authoritative)
CMD_DICE_TIME = 0x18AA     # 6314 act.act_monopoly_dice_time (regen-timer push)
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}

ACT_TYPE = 4003            # 傳奇大亨 act_type (LIVE-verified: c2s body f1 == 4003)

_DEFAULT_MAX_ROLLS = 50    # guard: a full dice stock is ~30; cap well above it
_DEFAULT_SPACING = 0.3     # spacing between rolls (s); server animates each roll


@dataclass(frozen=True)
class Grid:
    """One p_monopoly_grid tile: {grid_id#1, cfg_id#2, type#3}."""

    grid_id: int      # #1 — board index (1..N)
    cfg_id: int       # #2 — configMonopoly tile id
    type: int         # #3 — tile type (1 in all observed tiles)


@dataclass(frozen=True)
class MonopolyBoard:
    """A parsed act_monopoly_info_s2c snapshot."""

    act_type: int                  # #1
    circle: int                    # #2 — laps completed
    pos: int                       # #3 — current token position
    dice_time: int                 # #4 — regen timer anchor (unix sec; 0 = full)
    double_card_open: int          # #5 — double-reward card flag
    grids: list[Grid]              # #7 — the board tiles
    reward_circle_list: list[int]  # #8 — lap-reward thresholds
    raw: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class DiceReward:
    """One p_monopoly_reward entry from a roll: {type#1, item{k,v}#2, ...#3}."""

    type: int          # #1 — reward kind
    item_id: int       # #2.k — granted item id
    count: int         # #2.v — granted count


@dataclass(frozen=True)
class DiceResult:
    """A parsed act_monopoly_dice_s2c (or 0x0201 rejection)."""

    ok: bool                       # True iff the reply was on CMD_DICE
    act_type: int                  # #1
    dice_num: int                  # #2 — the rolled point (SERVER-decided)
    circle: int                    # #3 — laps completed after this roll
    pos: int                       # #4 — token position after this roll
    rewards: list[DiceReward]      # #6 — landed-tile rewards (auto-granted)
    response_cmd: int = 0
    error_code: Optional[int] = None
    raw: dict = field(compare=False, default_factory=dict)


# --- parsers ----------------------------------------------------------------

def _parse_grid(entry: bytes) -> Grid:
    d = codec.walk_dict(entry)
    return Grid(grid_id=_as_int(d.get(1)), cfg_id=_as_int(d.get(2)),
               type=_as_int(d.get(3)))


def _parse_reward(entry: bytes) -> DiceReward:
    """p_monopoly_reward {type#1, item#2:p_key_value{k#1,v#2}, ...#3}."""
    d = codec.walk_dict(entry)
    item_id = count = 0
    item = d.get(2)
    if isinstance(item, (bytes, bytearray)):
        kv = codec.walk_dict(bytes(item))
        item_id = _as_int(kv.get(1))
        count = _as_int(kv.get(2))
    return DiceReward(type=_as_int(d.get(1)), item_id=item_id, count=count)


def parse_board(body: bytes) -> MonopolyBoard:
    """act_monopoly_info_s2c -> MonopolyBoard."""
    d = codec.walk_dict(body)
    grids = [_parse_grid(bytes(v)) for fnum, v in codec.walk(body)
             if fnum == 7 and isinstance(v, (bytes, bytearray))]
    circle_rewards = [int(v) for fnum, v in codec.walk(body)
                      if fnum == 8 and isinstance(v, int)]
    return MonopolyBoard(
        act_type=_as_int(d.get(1)),
        circle=_as_int(d.get(2)),
        pos=_as_int(d.get(3)),
        dice_time=_as_int(d.get(4)),
        double_card_open=_as_int(d.get(5)),
        grids=grids,
        reward_circle_list=circle_rewards,
        raw=d,
    )


def parse_dice_result(cmd: int, body: bytes) -> DiceResult:
    """act_monopoly_dice_s2c (CMD_DICE) OR error_info_s2c (CMD_ERROR)."""
    if cmd == CMD_ERROR:
        code = codec.walk_dict(body).get(1)
        code = int(code) if isinstance(code, int) else None
        return DiceResult(ok=False, act_type=0, dice_num=0, circle=0, pos=0,
                          rewards=[], response_cmd=cmd, error_code=code,
                          raw=codec.walk_dict(body))
    d = codec.walk_dict(body)
    rewards = [_parse_reward(bytes(v)) for fnum, v in codec.walk(body)
               if fnum == 6 and isinstance(v, (bytes, bytearray))]
    return DiceResult(
        ok=(cmd == CMD_DICE),
        act_type=_as_int(d.get(1)),
        dice_num=_as_int(d.get(2)),
        circle=_as_int(d.get(3)),
        pos=_as_int(d.get(4)),
        rewards=rewards,
        response_cmd=cmd,
        raw=d,
    )


# --- body builders ----------------------------------------------------------

def build_act_type_body(act_type: int = ACT_TYPE) -> bytes:
    """Both info_c2s and dice_c2s carry only {act_type#1} (LIVE-verified)."""
    return codec.pb_uint(1, act_type)


# --- reads (plain call) -----------------------------------------------------

def read_board(
    client: WSGameClient, *, act_type: int = ACT_TYPE,
    timeout: Optional[float] = None,
) -> MonopolyBoard:
    """act_monopoly_info: read the board state (pos / circle / grids)."""
    body = client.call(CMD_INFO, build_act_type_body(act_type), timeout=timeout)
    board = parse_board(body)
    logger.info("ws_token tycoon: board pos=%s circle=%s grids=%d",
                board.pos, board.circle, len(board.grids))
    return board


# --- mutate (call_for with 0x0201; never plain call) ------------------------

def roll_dice(
    client: WSGameClient, *, act_type: int = ACT_TYPE,
    timeout: Optional[float] = None,
) -> DiceResult:
    """act_monopoly_dice: roll ONE dice. Server decides the point + grants reward.

    SUCCESS replies on CMD_DICE {dice_num, pos, reward[]}; a REJECTION (out of
    dice / event ended) replies on 0x0201 with an error_code. Returns a
    :class:`DiceResult`; ``ok`` is False on rejection (never raises / times out).
    """
    cmd, reply = client.call_for(
        CMD_DICE, build_act_type_body(act_type),
        expect_cmds=(CMD_DICE, CMD_ERROR), timeout=timeout)
    result = parse_dice_result(cmd, reply)
    if not result.ok:
        logger.info("ws_token tycoon: roll rejected 0x%04x error_code=%s",
                    cmd, result.error_code)
    else:
        gained = ", ".join(f"{r.item_id}x{r.count}" for r in result.rewards) or "-"
        logger.info("ws_token tycoon: roll dice_num=%s pos=%s circle=%s reward=[%s]",
                    result.dice_num, result.pos, result.circle, gained)
    return result


def auto_play(
    client: WSGameClient, *,
    act_type: int = ACT_TYPE,
    max_rolls: int = _DEFAULT_MAX_ROLLS,
    spacing: float = _DEFAULT_SPACING,
    timeout: Optional[float] = None,
) -> dict:
    """Roll the dice until the server rejects (out of dice) or ``max_rolls``.

    Each roll's landed-tile reward is auto-granted server-side (no claim cmd),
    so this single loop covers both "auto roll" and "auto claim". BOUNDED:
    STOPS on the FIRST 0x0201 (rejection) or when ``max_rolls`` rolls succeed.

    Returns {rolls, total_rewards: {item_id: count}, last_pos, last_circle,
    stopped_reason} where ``stopped_reason`` is ``"error_code=<n>"`` (server
    rejected) or ``"max_rolls"`` (cap reached).
    """
    rolls = 0
    last_pos = 0
    last_circle = 0
    total_rewards: dict[int, int] = {}
    stopped_reason = "max_rolls"
    for _ in range(max_rolls):
        result = roll_dice(client, act_type=act_type, timeout=timeout)
        if not result.ok:
            stopped_reason = f"error_code={result.error_code}"
            break
        rolls += 1
        last_pos = result.pos
        last_circle = result.circle
        for r in result.rewards:
            if r.item_id:
                total_rewards[r.item_id] = total_rewards.get(r.item_id, 0) + r.count
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token tycoon: auto_play rolls=%d last_pos=%s last_circle=%s %s",
                rolls, last_pos, last_circle, stopped_reason)
    return {"rolls": rolls, "total_rewards": total_rewards, "last_pos": last_pos,
            "last_circle": last_circle, "stopped_reason": stopped_reason}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
