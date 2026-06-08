"""Mining (挖礦) offline layer over a logged-in WSGameClient — home module 12.

OFFLINE-FIRST. This module decodes the mine board, builds the c2s bodies, and
tracks live inventory from pushes, but the dig orchestrators are
human-supervised only: digging mutates the real board and burns pickaxes, so
the smoke runner never digs and the tests never call them against a socket.

Protocol (docs/protocol/HOME_PROTO_SCHEMA.json — authoritative field numbers;
docs/protocol/MINING_SCHEMA.md — 5554 CDP-verified terrain/prop semantics):

  home_mine_info     0x0C01/3073  c2s {}  -> s2c
    {max_num#1, next_time#2, area#3, baseline#4, actives#5:uint32[],
     area_info#6:p_key_value[], blocks#7:p_mine_block[], holes#8:p_mine_hole[]}
  home_mine_use_goods    0x0C03/3075  c2s {goods_id#1, block_id#2}  (dig / use prop)
  home_mine_get_reward   0x0C04/3076  c2s {block_id#1}
  home_mine_auto_use_goods 0x0C19/3097 c2s {gtid#1:int32, auto_type#2, num#3:int32}

  p_mine_block {id#1, x#2, y#3, config_id#4, count#5, is_reward#6}
  p_mine_hole  {config_id#1, last_num#2, max_num#3, hole_num#4}
  p_key_value  {k#1, v#2}

  block_id = depth*100 + col.  terrain config_id (MINING_SCHEMA):
    201 = 石 (>=2 hit) / 202 = 泥 (1 hit) / 401 = 礦洞 (pit/reward).
  goods_id: 鎬=4001 / 鑽=4002 / 炸=4003 (MINING_SCHEMA §2; whether
    home_mine_use_goods takes this exact value is live-confirm — see below).

Inventory 現量: ``max_num`` is the cap, NOT the current count. The only trusted
live source is the 0x0402 push (evt 9800001 consume f3=new_count / 9800009 gain).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_MINE_INFO = 0x0C01        # home_mine_info: empty c2s -> full board s2c
CMD_USE_GOODS = 0x0C03        # home_mine_use_goods: dig one block / use a prop
CMD_GET_REWARD = 0x0C04       # home_mine_get_reward: collect a finished block reward
CMD_AUTO_USE_GOODS = 0x0C19   # home_mine_auto_use_goods: server-side auto dig
CMD_INVENTORY_PUSH = 0x0402   # item/currency delta push (rx-only)

# goods_id (= prop_id), MINING_SCHEMA §2 5554-verified for 0x0c03 dig.
# live-confirm: home_mine_use_goods.goods_id == prop_id 4001/4002/4003 is the
# working assumption; the use_goods cmd may key off a gtid alias instead.
GOODS_PICKAXE = 4001          # 鎬子 (single block)
GOODS_DRILL = 4002            # 鑽頭 (vertical + bottom row)
GOODS_BOMB = 4003             # 炸彈 (3x3 + cross)

# terrain config_id (= p_mine_block.config_id), MINING_SCHEMA §6 f4 enum.
TERRAIN_STONE = 201           # 石頭 (>=2 hits)
TERRAIN_DIRT = 202            # 泥土 (1 hit)
TERRAIN_PIT = 401             # 礦洞 (reward target)

# 0x0402 evt_type that carry an item 現量 (MINING_SCHEMA §3). Currency-only
# evt (e.g. 5011) are ignored so they never clobber an item count.
INV_EVT_CONSUME = 9800001     # 道具消耗 -> f3 = new_count
INV_EVT_GAIN = 9800009        # 道具獲得 -> f3 = new_count
INV_ITEM_EVT_TYPES = frozenset({INV_EVT_CONSUME, INV_EVT_GAIN})

# Item ids we surface as named properties (MINING_SCHEMA §2/§4).
_PROP_ITEM_IDS = (GOODS_PICKAXE, GOODS_DRILL, GOODS_BOMB)


@dataclass(frozen=True)
class MineBlock:
    """One p_mine_block from home_mine_info.blocks (#7)."""

    block_id: int       # id #1  (= depth*100 + col)
    x: int              # #2  (col, 1-indexed in the live board)
    y: int              # #3  (depth / floor row)
    config_id: int      # #4  (terrain: 201 stone / 202 dirt / 401 pit)
    count: int          # #5  (hits remaining)
    is_reward: int      # #6


@dataclass(frozen=True)
class MineHole:
    """One p_mine_hole from home_mine_info.holes (#8)."""

    config_id: int      # #1
    last_num: int       # #2
    max_num: int        # #3
    hole_num: int       # #4


@dataclass(frozen=True)
class MineBoard:
    """Decoded home_mine_info_s2c (0x0c01)."""

    max_num: int                # #1 (pickaxe CAP, not current count)
    next_time: int              # #2
    area: int                   # #3
    baseline: int               # #4 (depth offset of the viewport top)
    actives: list               # #5:uint32[]
    area_info: dict             # #6:p_key_value[] -> {k: v}
    blocks: list                # #7:p_mine_block[]
    holes: list                 # #8:p_mine_hole[]
    raw: dict = field(compare=False, default_factory=dict)


# --- parse ------------------------------------------------------------------

def _parse_block(sub: bytes) -> MineBlock:
    d = codec.walk_dict(sub)
    return MineBlock(
        block_id=_as_int(d.get(1)),
        x=_as_int(d.get(2)),
        y=_as_int(d.get(3)),
        config_id=_as_int(d.get(4)),
        count=_as_int(d.get(5)),
        is_reward=_as_int(d.get(6)),
    )


def _parse_hole(sub: bytes) -> MineHole:
    d = codec.walk_dict(sub)
    return MineHole(
        config_id=_as_int(d.get(1)),
        last_num=_as_int(d.get(2)),
        max_num=_as_int(d.get(3)),
        hole_num=_as_int(d.get(4)),
    )


def parse_board(body: bytes) -> MineBoard:
    """Decode a home_mine_info_s2c (0x0c01) body into a MineBoard."""
    max_num = next_time = area = baseline = 0
    actives: list = []
    area_info: dict = {}
    blocks: list = []
    holes: list = []
    for fnum, val in codec.walk(body):
        if fnum == 1:
            max_num = _as_int(val)
        elif fnum == 2:
            next_time = _as_int(val)
        elif fnum == 3:
            area = _as_int(val)
        elif fnum == 4:
            baseline = _as_int(val)
        elif fnum == 5 and isinstance(val, int):
            actives.append(val)
        elif fnum == 6 and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            area_info[_as_int(kv.get(1))] = _as_int(kv.get(2))
        elif fnum == 7 and isinstance(val, (bytes, bytearray)):
            blocks.append(_parse_block(bytes(val)))
        elif fnum == 8 and isinstance(val, (bytes, bytearray)):
            holes.append(_parse_hole(bytes(val)))
    return MineBoard(
        max_num=max_num, next_time=next_time, area=area, baseline=baseline,
        actives=actives, area_info=area_info, blocks=blocks, holes=holes,
    )


# --- builders ---------------------------------------------------------------

def build_use_goods_body(goods_id: int, block_id: int) -> bytes:
    """home_mine_use_goods_c2s {goods_id#1, block_id#2} — goods first, then block."""
    return codec.pb_uint(1, goods_id) + codec.pb_uint(2, block_id)


def build_get_reward_body(block_id: int) -> bytes:
    """home_mine_get_reward_c2s {block_id#1}."""
    return codec.pb_uint(1, block_id)


def build_auto_use_goods_body(gtid: int, auto_type: int, num: int) -> bytes:
    """home_mine_auto_use_goods_c2s {gtid#1:int32, auto_type#2, num#3:int32}.

    gtid/num are int32 in the schema; non-negative values match the unsigned
    varint encoder byte-for-byte, which is all the live use cases need.
    """
    return (codec.pb_uint(1, gtid) + codec.pb_uint(2, auto_type)
            + codec.pb_uint(3, num))


# --- inventory tracking (0x0402 push) ---------------------------------------

class InventoryTracker:
    """Maintains item 現量 from 0x0402 pushes — the only trusted live source.

    Install with ``client.set_push_handler(tracker.on_push)``. Only the
    item-bearing evt types (consume/gain) update counts; currency-only events
    are ignored so they never overwrite an item quantity.
    """

    def __init__(self) -> None:
        self.counts: dict[int, int] = {}

    def on_push(self, cmd: int, body: bytes) -> None:
        if cmd != CMD_INVENTORY_PUSH:
            return
        evt_type = None
        items: list[bytes] = []
        for fnum, val in codec.walk(body):
            if fnum == 1:
                evt_type = _as_int(val)
            elif fnum == 2 and isinstance(val, (bytes, bytearray)):
                items.append(bytes(val))
        if evt_type not in INV_ITEM_EVT_TYPES:
            return
        for sub in items:
            d = codec.walk_dict(sub)
            item_id = d.get(1)
            new_count = d.get(3)
            if isinstance(item_id, int) and isinstance(new_count, int):
                self.counts[item_id] = new_count

    @property
    def pickaxe(self) -> int:
        return self.counts.get(GOODS_PICKAXE, 0)

    @property
    def drill(self) -> int:
        return self.counts.get(GOODS_DRILL, 0)

    @property
    def bomb(self) -> int:
        return self.counts.get(GOODS_BOMB, 0)

    def as_props(self) -> dict:
        """{'pickaxe', 'drill', 'bomb'} snapshot for the planner adapter."""
        return {"pickaxe": self.pickaxe, "drill": self.drill, "bomb": self.bomb}


# --- orchestrators (human-supervised; NOT auto-run, NOT in tests) -----------

def read_board(client: WSGameClient, *, timeout: Optional[float] = None) -> MineBoard:
    """Query the current mine board (0x0c01). Read-only, safe to call."""
    return parse_board(client.call(CMD_MINE_INFO, b"", timeout=timeout))


def dig(client: WSGameClient, goods_id: int, block_id: int,
        *, timeout: Optional[float] = None) -> bytes:
    """Send one home_mine_use_goods (dig / use prop). HUMAN-SUPERVISED ONLY.

    Mutates the real board and consumes a prop; never call this from an
    unattended loop. Returns the raw incremental-board s2c body for the caller
    to decode.
    """
    return client.call(CMD_USE_GOODS, build_use_goods_body(goods_id, block_id),
                       timeout=timeout)


def get_reward(client: WSGameClient, block_id: int,
               *, timeout: Optional[float] = None) -> bytes:
    """Collect a finished block's reward (0x0c04). HUMAN-SUPERVISED ONLY."""
    return client.call(CMD_GET_REWARD, build_get_reward_body(block_id),
                       timeout=timeout)


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
