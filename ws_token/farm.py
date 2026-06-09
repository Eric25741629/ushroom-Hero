"""農場/打工 task over a logged-in WSGameClient — pure WS.

Two surfaces, both built/framed by codec + client (NOT the in-game netManager):

- 互動農場 home_farm (home module 12): read land state, plant on empty land,
  harvest mature crops. Per-land cmds — there is no batch plant/harvest.
- 打工 (管家自動種+收) worker_common (module 73): one worker_setting call hands
  the whole farm to the server-side 管家 (auto plant + auto harvest). This is
  what the user mainly wants; sending an empty seed_used_seq_list = 用免費種子 =
  不買種 (matches the user's "種+收但不買種子" scope).

Field numbers are the live-exported truth (HOME/TYPE/WORKER_COMMON/SHOP
_PROTO_SCHEMA.json). c2s and s2c share the same cmd id.

  home_farm_info     3077  c2s {role_id#1:uint64}
                           s2c {role_id#1, name#2, level#3, exp#4,
                                land_list#5:repeated p_farm_land, ...}
  home_farm_plant    3078  c2s {seed_id#1:uint32, land_id#2:uint32}
                           s2c {code#1, new_land#2:p_farm_land}
  home_farm_harvest  3081  c2s {land_id#1:uint32}
                           s2c {code#1, new_land#2, level#3, exp#4,
                                reward_list#5:repeated p_reward}
  worker_common_farm_worker_setting 18689
                           c2s {team_cfg_id#1, fertilizer_list#2:repeated uint32,
                                fertilizer_time_rest#3, seed_used_seq_list#4:repeated p_key_value}
                           s2c {worker_info#1:p_worker}  ; p_worker.worker_status#3>0 = 運作中
  shop_buy           6914  c2s {shop_type#1, shop_id#2, num#3}  (豐收卡)

  p_farm_land {id#1, crop#2:p_farm_crop}     (crop absent = empty land)
  p_farm_crop {id#1, role_id#2, seed_id#3, cfg_id#4, state#5, start_time#6,
               acc_time#7, end_time#8, ...}
  p_reward    {gtid#1:int32, num#2:int64}

crop.state: 0=空地 NOT_EXIT / 1=GROWING / 2=MATURE(可收) / 3..7 其他.
成熟另看 end_time<=serverTime. code!=0 = 失敗.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- cmd ids (c2s and s2c share the same id, but FAILURES reply on 0x0201) ---
CMD_INFO = 3077            # home_farm_info       (home module 12)
CMD_PLANT = 3078          # home_farm_plant
CMD_FERTILIZE = 3079      # home_farm_fertilize
CMD_PICK = 3080           # home_farm_pick (偷菜)
CMD_HARVEST = 3081        # home_farm_harvest
CMD_WORKER_SETTING = 18689  # worker_common_farm_worker_setting (worker module 73)
CMD_SHOP_BUY = 6914       # shop_buy (豐收卡; shop module 27)
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code#1}

# Live (小寶 2026-06-09): a rejected plant/harvest replies on the 0x0201 error
# channel, NOT on the action's own cmd. Error codes decoded from the client
# (configErrorInfo): 173 = "活動已結束" (e.g. an event-crop seed whose event ended),
# 159 = "次數不足". The old ``client.call(CMD_PLANT)`` waited only for 3078 and
# crashed with WSTimeoutError on any rejection — actions must wait for EITHER the
# success cmd OR 0x0201. (打工 with free seeds is the intended path; event seeds
# like the live-tried 102 return 173 once their event is over.)

# crop.state enum (p_farm_crop.state#5)
STATE_NOT_EXIT = 0        # 空地
STATE_GROWING = 1
STATE_MATURE = 2          # 可收

# Tunables — all default to None/0 because the live values live in the game's
# client config and must be read once on a live session.
# live-confirm: 值在 config,需 live 取一次
DEFAULT_SEED_ID: Optional[int] = None       # 免費種子 id (configFarmSeed)
DEFAULT_FERTILIZER_ID: Optional[int] = None  # 肥料 id
DEFAULT_TEAM_CFG_ID: Optional[int] = None    # 打工隊伍 team_cfg_id (configFarmWorker)
HARVEST_CARD_SHOP_TYPE: Optional[int] = None  # 豐收卡 shop_type
HARVEST_CARD_SHOP_ID: Optional[int] = None    # 豐收卡 shop_id (configMall)


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class FarmLand:
    """One p_farm_land: a plot, optionally holding a crop.

    ``has_crop`` is False when the land_list entry has no crop sub-message.
    A crop with state==0 (NOT_EXIT) is also treated as empty.
    """

    id: int
    has_crop: bool
    state: int          # crop.state#5 (0 when no crop)
    end_time: int       # crop.end_time#8 (0 when no crop)
    seed_id: int = 0    # crop.seed_id#3
    cfg_id: int = 0     # crop.cfg_id#4
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """True when nothing is planted (no crop, or crop in NOT_EXIT state)."""
        return (not self.has_crop) or self.state == STATE_NOT_EXIT

    @property
    def is_ready(self) -> bool:
        """True when the crop is MATURE (state==2). For an end_time check use
        :meth:`is_ready_at`."""
        return self.has_crop and self.state == STATE_MATURE

    def is_ready_at(self, *, now: int) -> bool:
        """Ready to harvest: MATURE, or a growing crop whose end_time has passed."""
        if not self.has_crop or self.state == STATE_NOT_EXIT:
            return False
        return self.state == STATE_MATURE or (self.end_time and self.end_time <= now)


@dataclass(frozen=True)
class FarmInfo:
    """home_farm_info_s2c: role/level header + the land list."""

    role_id: int
    name: str
    level: int
    exp: int
    lands: tuple[FarmLand, ...] = ()
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def empty_lands(self) -> tuple[FarmLand, ...]:
        return tuple(land for land in self.lands if land.is_empty)

    @property
    def ready_lands(self) -> tuple[FarmLand, ...]:
        """Lands ready by state (MATURE). For an end_time-aware list pass a
        server time to :meth:`ready_lands_at`."""
        return tuple(land for land in self.lands if land.is_ready)

    def ready_lands_at(self, *, now: int) -> tuple[FarmLand, ...]:
        return tuple(land for land in self.lands if land.is_ready_at(now=now))


# --- body builders ----------------------------------------------------------

def build_plant_body(seed_id: int, land_id: int) -> bytes:
    """home_farm_plant_c2s {seed_id#1, land_id#2} — seed_id FIRST, then land_id."""
    return codec.pb_uint(1, seed_id) + codec.pb_uint(2, land_id)


def build_harvest_body(land_id: int) -> bytes:
    """home_farm_harvest_c2s {land_id#1}."""
    return codec.pb_uint(1, land_id)


def build_worker_setting_body(
    team_cfg_id: int,
    *,
    fertilizer_list: Iterable[int] = (),
    fertilizer_time_rest: int = 0,
    seed_used_seq: Iterable[Sequence[int]] = (),
) -> bytes:
    """worker_common_farm_worker_setting_c2s.

    {team_cfg_id#1, fertilizer_list#2:repeated uint32 (unpacked),
     fertilizer_time_rest#3, seed_used_seq_list#4:repeated p_key_value{k#1,v#2}}

    An empty ``seed_used_seq`` = 用免費種子 = 不買種 (the user's scope). Each
    seed_used_seq entry is (seed_id, count).
    """
    out = codec.pb_uint(1, team_cfg_id)
    for fid in fertilizer_list:
        out += codec.pb_uint(2, fid)
    if fertilizer_time_rest:
        out += codec.pb_uint(3, fertilizer_time_rest)
    for entry in seed_used_seq:
        k, v = entry[0], entry[1]
        out += codec.pb_msg(4, codec.pb_uint(1, k) + codec.pb_uint(2, v))
    return out


def build_shop_buy_body(shop_type: int, shop_id: int, num: int) -> bytes:
    """shop_buy_c2s {shop_type#1, shop_id#2, num#3} — 豐收卡 purchase."""
    return codec.pb_uint(1, shop_type) + codec.pb_uint(2, shop_id) + codec.pb_uint(3, num)


# --- parsers ----------------------------------------------------------------

def _parse_land(entry: bytes) -> FarmLand:
    d = codec.walk_dict(entry)
    land_id = _as_int(d.get(1))
    crop = d.get(2)
    if isinstance(crop, (bytes, bytearray)):
        c = codec.walk_dict(bytes(crop))
        return FarmLand(
            id=land_id,
            has_crop=True,
            state=_as_int(c.get(5)),
            end_time=_as_int(c.get(8)),
            seed_id=_as_int(c.get(3)),
            cfg_id=_as_int(c.get(4)),
            raw=c,
        )
    return FarmLand(id=land_id, has_crop=False, state=0, end_time=0, raw={})


def parse_farm_info(body: bytes) -> FarmInfo:
    """home_farm_info_s2c: header fields + land_list#5 (repeated p_farm_land)."""
    role_id = name = level = exp = None
    lands: list[FarmLand] = []
    raw: dict = {}
    for fnum, val in codec.walk(body):
        if fnum == 1:
            role_id = val
        elif fnum == 2:
            name = val
        elif fnum == 3:
            level = val
        elif fnum == 4:
            exp = val
        elif fnum == 5 and isinstance(val, (bytes, bytearray)):
            lands.append(_parse_land(bytes(val)))
    return FarmInfo(
        role_id=_as_int(role_id),
        name=_as_str(name),
        level=_as_int(level),
        exp=_as_int(exp),
        lands=tuple(lands),
        raw=raw,
    )


def parse_rewards(body: bytes) -> dict[int, int]:
    """harvest_s2c.reward_list#5: repeated p_reward{gtid#1, num#2} -> {gtid: num}."""
    rewards: dict[int, int] = {}
    for fnum, val in codec.walk(body):
        if fnum == 5 and isinstance(val, (bytes, bytearray)):
            r = codec.walk_dict(bytes(val))
            rewards[_as_int(r.get(1))] = rewards.get(_as_int(r.get(1)), 0) + _as_int(r.get(2))
    return rewards


# --- orchestrators ----------------------------------------------------------

def read_farm(
    client: WSGameClient, role_id: int, *, timeout: Optional[float] = None
) -> FarmInfo:
    """home_farm_info: send {role_id} and parse the land state."""
    body = client.call(CMD_INFO, codec.pb_uint(1, role_id), timeout=timeout)
    return parse_farm_info(body)


def _farm_action(
    client: WSGameClient, cmd: int, body: bytes, *, timeout: Optional[float] = None
) -> tuple[bool, int, bytes]:
    """Send a farm action (plant/harvest) that can be rejected.

    SUCCESS replies on the action's own ``cmd`` (code#1==0); a REJECTION replies on
    the 0x0201 error channel (code#1 = error code, live-observed 173). Waits for
    EITHER so a rejected action records ``ok=False`` instead of timing out / raising.
    Returns ``(ok, code, reply_body)``.
    """
    reply_cmd, reply = client.call_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout)
    code = _as_int(codec.walk_dict(reply).get(1))
    ok = reply_cmd == cmd and code == 0
    return ok, code, reply


def plant_empty(
    client: WSGameClient,
    role_id: int,
    seed_id: int,
    *,
    info: Optional[FarmInfo] = None,
    spacing: float = 0.2,
    timeout: Optional[float] = None,
) -> dict:
    """Plant ``seed_id`` on every empty land (per-land plant; no batch cmd).

    Returns {attempted, planted, results}. A plant rejection (0x0201) or
    plant_s2c.code!=0 counts as failure. Pass a pre-read ``info`` to skip the
    read: the live server answers ``home_farm_info`` only ONCE per session, so a
    caller that already read the farm (e.g. right after harvest) MUST reuse it or
    the second read times out.
    """
    if info is None:
        info = read_farm(client, role_id, timeout=timeout)
    targets = info.empty_lands
    planted = 0
    results: list[dict] = []
    for land in targets:
        ok, code, _body = _farm_action(
            client, CMD_PLANT, build_plant_body(seed_id, land.id), timeout=timeout)
        if ok:
            planted += 1
        results.append({"land_id": land.id, "code": code, "ok": ok})
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token farm: plant_empty attempted=%d planted=%d", len(targets), planted)
    return {"attempted": len(targets), "planted": planted, "results": results}


def harvest_ready(
    client: WSGameClient,
    role_id: int,
    *,
    info: Optional[FarmInfo] = None,
    now: Optional[int] = None,
    spacing: float = 0.2,
    timeout: Optional[float] = None,
) -> dict:
    """Harvest every ready land (MATURE, or end_time<=now if ``now`` is given).

    Returns {attempted, harvested, rewards, results}. reward_list#5 entries are
    summed across harvests into ``rewards`` {gtid: num}. A 0x0201 rejection or
    code!=0 = failure. Pass a pre-read ``info`` to skip the read (the live server
    answers ``home_farm_info`` only ONCE per session — see plant_empty).
    """
    if info is None:
        info = read_farm(client, role_id, timeout=timeout)
    targets = info.ready_lands_at(now=now) if now is not None else info.ready_lands
    harvested = 0
    rewards: dict[int, int] = {}
    results: list[dict] = []
    for land in targets:
        ok, code, body = _farm_action(
            client, CMD_HARVEST, build_harvest_body(land.id), timeout=timeout)
        if ok:
            harvested += 1
            for gtid, num in parse_rewards(body).items():
                rewards[gtid] = rewards.get(gtid, 0) + num
        results.append({"land_id": land.id, "code": code, "ok": ok})
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token farm: harvest_ready attempted=%d harvested=%d rewards=%s",
                len(targets), harvested, rewards)
    return {"attempted": len(targets), "harvested": harvested,
            "rewards": rewards, "results": results}


def start_work(
    client: WSGameClient,
    team_cfg_id: int,
    *,
    fertilizer_list: Iterable[int] = (),
    fertilizer_time_rest: int = 0,
    seed_used_seq: Iterable[Sequence[int]] = (),
    timeout: Optional[float] = None,
) -> dict:
    """打工: send worker_setting to hand the farm to the 管家 (auto plant +収).

    Empty ``seed_used_seq`` = 用免費種子 = 不買種. Returns
    {running, worker_status, raw}; running is True iff p_worker.worker_status>0.
    """
    body = build_worker_setting_body(
        team_cfg_id,
        fertilizer_list=fertilizer_list,
        fertilizer_time_rest=fertilizer_time_rest,
        seed_used_seq=seed_used_seq,
    )
    reply_cmd, reply = client.call_for(
        CMD_WORKER_SETTING, body,
        expect_cmds=(CMD_WORKER_SETTING, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning("ws_token farm: start_work rejected 0x0201 code=%s "
                       "(team_cfg_id=%s)", code, team_cfg_id)
        return {"running": False, "worker_status": 0, "error_code": code, "raw": reply}
    status = 0
    worker = codec.walk_dict(reply).get(1)  # worker_info#1 = p_worker
    if isinstance(worker, (bytes, bytearray)):
        status = _as_int(codec.walk_dict(bytes(worker)).get(3))  # worker_status#3
    running = status > 0
    logger.info("ws_token farm: start_work team_cfg_id=%s worker_status=%d running=%s",
                team_cfg_id, status, running)
    return {"running": running, "worker_status": status, "raw": reply}


def buy_harvest_card(
    client: WSGameClient,
    *,
    shop_type: int,
    shop_id: int,
    num: int = 1,
    timeout: Optional[float] = None,
) -> bytes:
    """豐收卡: shop_buy {shop_type, shop_id, num}. Returns the raw s2c body.

    # live-confirm: shop_type / shop_id 值在 client config configMall,需 live 取一次
    """
    return client.call(CMD_SHOP_BUY,
                       build_shop_buy_body(shop_type, shop_id, num), timeout=timeout)


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
