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
                           ; seed_id 101=免費種子(管家用) / 102=初級種子(金幣 shop 407)
  home_farm_fertilize 3079 c2s {role_id#1(=0 self), land_id#2, fertilizer_id#3, num#4}
                           s2c {code#1, role_id#2, new_land#3} ; per-land, 高產肥料 id=111
  worker_common_farm_get_other_role_info 18690  打工偵測 (pure read)
                           c2s {role_id#1(=self), team_cfg_id#2:repeated=[7001]}
                           s2c {team_list#1:p_other_worker[]} ; p_other_worker.worker_status#4>0=運作中
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
CMD_GET_OTHER_WORKER = 18690  # worker_common_farm_get_other_role_info — 打工偵測 (read)
CMD_SHOP_INFO = 6913      # shop_info {shop_type} — 讀當日各 shop_id 已購次數 (read)
CMD_SHOP_BUY = 6914       # shop_buy (種子/肥料/豐收卡; shop module 27)
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

# Live-verified values (5554 H5, 2026-06-14; docs/protocol/FARM_MANOR_WS_RECON.md).
SEED_ID_FREE = 101            # 免費種子 (btnSeedGet; the seed 管家 auto-plants)
SEED_ID_PRIMARY = 102         # 初級種子 (金幣購買; shop_type 4 / shop_id 407 給 item 102)
FERTILIZER_ID_HIGH_YIELD = 111  # 高產肥料 (shop_type 4 / shop_id 408 給 item 111 ×5)
FARM_WORKER_TEAM_CFG_ID = 7001  # 農場打工隊伍 team_cfg_id (live: get_other_role_info)
# 農場商店 (shop.shop_info/shop_buy shop_type=4):
FARM_SHOP_TYPE = 4
SHOP_ID_PRIMARY_SEED = 407    # → item 102 ×1, 金幣 (階梯價 [0,20,30,50,100,260,...])
SHOP_ID_HIGH_YIELD_FERTILIZER = 408  # → item 111 ×5, 金幣 (階梯價 [0,50,90,140,...])

DEFAULT_SEED_ID: Optional[int] = SEED_ID_FREE        # 管家/種菜預設用免費種子
DEFAULT_FERTILIZER_ID: Optional[int] = FERTILIZER_ID_HIGH_YIELD
DEFAULT_TEAM_CFG_ID: Optional[int] = FARM_WORKER_TEAM_CFG_ID
# 豐收卡 still unverified: 不在 farm shop(type4)，依舊例在跨界停車商店。live-confirm.
HARVEST_CARD_SHOP_TYPE: int = 11
HARVEST_CARD_SHOP_ID: int = 1604
SEED_ID_PREMIUM = 103         # 特級種子 (configGoods id=103, live-confirmed)
# Simple start/cancel cmds (module 71; distinct from worker_setting 18689 which configures):
CMD_WORKER_START = 18177      # start farm work (body {field1: 1001})
CMD_WORKER_CANCEL = 18178     # cancel farm work (body {field1: 1001})
FARM_WORK_ID = 1001           # work identifier in cmd body (live-captured 2026-06-15)


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


def build_fertilize_body(
    land_id: int, fertilizer_id: int, *, num: int = 1, role_id: int = 0
) -> bytes:
    """home_farm_fertilize_c2s {role_id#1, land_id#2, fertilizer_id#3, num#4}.

    Per-land (no batch cmd; the in-game 「一鍵施肥」 loops over each land client-side).
    Live: the client sends role_id#1 = 0 for self (the server uses the connection's
    role), so ``role_id`` defaults to 0; num#4 = 1 per land. fertilizer_id 111 =
    高產肥料. See docs/protocol/FARM_MANOR_WS_RECON.md §2.
    """
    return (codec.pb_uint(1, role_id) + codec.pb_uint(2, land_id)
            + codec.pb_uint(3, fertilizer_id) + codec.pb_uint(4, num))


def build_get_other_worker_body(role_id: int, team_cfg_ids: Iterable[int]) -> bytes:
    """worker_common_farm_get_other_role_info_c2s {role_id#1, team_cfg_id#2:repeated}.

    Passing your OWN role_id + the farm team_cfg_id (7001) reads your own 打工 worker
    — a pure read (does not mutate, unlike worker_setting). team_cfg_id MUST be sent;
    an empty list returns nothing. See FARM_MANOR_WS_RECON.md §3.
    """
    out = codec.pb_uint(1, role_id)
    for tid in team_cfg_ids:
        out += codec.pb_uint(2, tid)
    return out


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


def build_worker_start_cancel_body(work_id: int = FARM_WORK_ID) -> bytes:
    """Body for CMD_WORKER_START (18177) / CMD_WORKER_CANCEL (18178): {work_id#1}."""
    return codec.pb_uint(1, work_id)


def build_shop_buy_body(shop_type: int, shop_id: int, num: int) -> bytes:
    """shop_buy_c2s {shop_type#1, shop_id#2, num#3}.

    num = quantity in ONE call (live: num=3 → got 3 items, daily count +3). Price
    follows an escalating per-day ladder. See FARM_MANOR_WS_RECON.md §1.
    """
    return codec.pb_uint(1, shop_type) + codec.pb_uint(2, shop_id) + codec.pb_uint(3, num)


def build_shop_info_body(shop_type: int) -> bytes:
    """shop_info_c2s {shop_type#1} — read today's per-item purchase counts."""
    return codec.pb_uint(1, shop_type)


def parse_shop_counts(body: bytes) -> dict[int, int]:
    """shop_info_s2c {shop_type#1, item#2:repeated {shop_id#1, bought_count#2}}.

    Returns {shop_id: today_bought_count}. shop_ids with 0 buys are absent from
    the s2c, so callers must default missing ids to 0. Live: {2:{1:407, 2:4}}.
    """
    counts: dict[int, int] = {}
    for fnum, val in codec.walk(body):
        if fnum == 2 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            sid = _as_int(d.get(1))
            counts[sid] = _as_int(d.get(2))
    return counts


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


def fertilize_lands(
    client: WSGameClient,
    role_id: int,
    fertilizer_id: int,
    *,
    info: Optional[FarmInfo] = None,
    num: int = 1,
    spacing: float = 0.2,
    timeout: Optional[float] = None,
) -> dict:
    """Fertilize every non-empty land (per-land; mirrors the in-game 「一鍵施肥」).

    Sends one home_farm_fertilize (3079) per land that has a crop. A rejection
    (0x0201) or fertilize_s2c.code!=0 counts as failure. Pass a pre-read ``info``
    to skip the read (home_farm_info answers once per session — see plant_empty).
    Returns {attempted, fertilized, results}.
    """
    if info is None:
        info = read_farm(client, role_id, timeout=timeout)
    targets = tuple(land for land in info.lands if not land.is_empty)
    fertilized = 0
    results: list[dict] = []
    for land in targets:
        # Live: client sends role_id=0 for self-fertilize (server uses the
        # connection's role); ``role_id`` arg is only used to READ the farm above.
        ok, code, _body = _farm_action(
            client, CMD_FERTILIZE,
            build_fertilize_body(land.id, fertilizer_id, num=num),
            timeout=timeout)
        if ok:
            fertilized += 1
        results.append({"land_id": land.id, "code": code, "ok": ok})
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token farm: fertilize_lands attempted=%d fertilized=%d fert_id=%d",
                len(targets), fertilized, fertilizer_id)
    return {"attempted": len(targets), "fertilized": fertilized, "results": results}


def read_work_status(
    client: WSGameClient,
    role_id: int,
    *,
    team_cfg_id: int = FARM_WORKER_TEAM_CFG_ID,
    timeout: Optional[float] = None,
) -> dict:
    """打工偵測 — read whether the farm 管家 (worker) is running, WITHOUT mutating.

    Uses worker_common_farm_get_other_role_info (18690) with our OWN role_id + the
    farm team_cfg_id. The s2c carries team_list#1: p_other_worker[]; we read the
    matching entry's worker_status#4 (>0 = 運作中). team_cfg_id MUST be sent — an
    empty list returns nothing. Preferred over re-sending worker_setting (which
    mutates). See docs/protocol/FARM_MANOR_WS_RECON.md §3.

    Returns {running, worker_status, team_cfg_id, found}.
    """
    reply = client.call(CMD_GET_OTHER_WORKER,
                        build_get_other_worker_body(role_id, (team_cfg_id,)),
                        timeout=timeout)
    status = 0
    found = False
    for fnum, val in codec.walk(reply):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):  # team_list#1: p_other_worker
            w = codec.walk_dict(bytes(val))
            if _as_int(w.get(2)) == team_cfg_id:  # team_cfg_id#2
                status = _as_int(w.get(4))  # worker_status#4
                found = True
                break
    running = status > 0
    logger.info("ws_token farm: read_work_status team_cfg_id=%d worker_status=%d running=%s",
                team_cfg_id, status, running)
    return {"running": running, "worker_status": status,
            "team_cfg_id": team_cfg_id, "found": found}


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


def stop_work(
    client: WSGameClient,
    *,
    work_id: int = FARM_WORK_ID,
    timeout: Optional[float] = None,
) -> dict:
    """Cancel farm 打工 (companion worker) via CMD_WORKER_CANCEL (18178).

    Live-verified 2026-06-15 on 7fe98fc6: clicking '取消打工' sends cmd=18178
    with body {field1=1001}. Returns {ok, error_code}. Use start_work_simple()
    to re-enable after the harvest-card flow completes.
    """
    reply_cmd, reply = client.call_for(
        CMD_WORKER_CANCEL,
        build_worker_start_cancel_body(work_id),
        expect_cmds=(CMD_WORKER_CANCEL, CMD_ERROR),
        timeout=timeout,
    )
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning('ws_token farm: stop_work rejected 0x0201 code=%s', code)
        return {'ok': False, 'error_code': code}
    logger.info('ws_token farm: stop_work ok')
    return {'ok': True, 'error_code': 0}


def start_work_simple(
    client: WSGameClient,
    *,
    work_id: int = FARM_WORK_ID,
    timeout: Optional[float] = None,
) -> dict:
    """Start farm 打工 using CMD_WORKER_START (18177) — simple re-enable.

    Does NOT change companion/fertilizer config (unlike worker_setting 18689).
    Use this to re-enable work after stop_work() in the harvest-card flow.
    Live-verified 2026-06-15 on 7fe98fc6: clicking '開始打工' sends cmd=18177
    with body {field1=1001}.
    """
    reply_cmd, reply = client.call_for(
        CMD_WORKER_START,
        build_worker_start_cancel_body(work_id),
        expect_cmds=(CMD_WORKER_START, CMD_ERROR),
        timeout=timeout,
    )
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning('ws_token farm: start_work_simple rejected 0x0201 code=%s', code)
        return {'ok': False, 'error_code': code}
    logger.info('ws_token farm: start_work_simple ok')
    return {'ok': True, 'error_code': 0}


def run_harvest_card_cycle(
    client: WSGameClient,
    role_id: int,
    *,
    harvest_card_shop_type: int = HARVEST_CARD_SHOP_TYPE,
    harvest_card_shop_id: int = HARVEST_CARD_SHOP_ID,
    premium_seed_id: int = SEED_ID_PREMIUM,
    num_cards: int = 3,
    fertilizer_id: int = FERTILIZER_ID_HIGH_YIELD,
    land_ids: Sequence[int] = (),
    timeout: Optional[float] = None,
) -> dict:
    """豐收卡 harvest-card cycle via pure WS.

    Flow (per project_farm_mechanics memory 2026-05-28):
      1. Stop companion worker (cancel 打工)
      2. Fertilize all plots to force-ripen crops
      3. Harvest all ready crops (best-effort; 3077 has ~50% timeout)
      4. Buy harvest cards (shop_type=11, shop_id=1604)
      5. Plant premium seeds (seed_id=103) on every empty plot
      6. Re-enable companion worker
    """
    result = {
        'stopped_work': False,
        'fertilized': 0,
        'harvested': 0,
        'cards_bought': 0,
        'planted': 0,
        'restarted_work': False,
        'ok': False,
    }

    # 1. Stop companion worker
    sw = stop_work(client, timeout=timeout)
    result['stopped_work'] = sw.get('ok', False)

    # 2. Fertilize to force-ripen
    fert = fertilize_lands(client, role_id, fertilizer_id, timeout=timeout)
    result['fertilized'] = fert.get('fertilized', 0)

    # 3. Harvest ready crops (best-effort; 3077 may timeout)
    try:
        harv = harvest_ready(client, role_id, timeout=5.0)
        result['harvested'] = harv.get('harvested', 0)
    except Exception as exc:
        logger.info('ws_token farm: harvest_ready skipped (%s)', exc)

    # 4. Buy harvest cards up to num_cards
    card_result = buy_to_daily_target(
        client, harvest_card_shop_id, num_cards,
        shop_type=harvest_card_shop_type, timeout=timeout,
    )
    result['cards_bought'] = card_result.get('bought', 0)

    # 5. Plant premium seeds on empty plots
    plant = plant_empty(client, role_id, premium_seed_id, timeout=timeout)
    result['planted'] = plant.get('planted', 0)

    # 6. Re-enable companion worker
    rs = start_work_simple(client, timeout=timeout)
    result['restarted_work'] = rs.get('ok', False)
    result['ok'] = True

    logger.info('ws_token farm: run_harvest_card_cycle %s', result)
    return result


def read_shop_counts(
    client: WSGameClient, shop_type: int, *, timeout: Optional[float] = None
) -> dict[int, int]:
    """shop_info: read today's per-shop_id purchase counts for ``shop_type``.

    Unlike home_farm_info, shop_info answers reliably on every call (live). Returns
    {shop_id: count}; missing shop_ids = 0 bought today.
    """
    body = client.call(CMD_SHOP_INFO, build_shop_info_body(shop_type), timeout=timeout)
    return parse_shop_counts(body)


def buy_to_daily_target(
    client: WSGameClient,
    shop_id: int,
    target: int,
    *,
    shop_type: int = FARM_SHOP_TYPE,
    counts: Optional[dict[int, int]] = None,
    timeout: Optional[float] = None,
) -> dict:
    """Buy a farm-shop item UP TO a daily ``target`` count, never beyond.

    Reads today's count first (so a player who already bought via the in-game GUI
    is respected — we only buy the remainder, and buy NOTHING if already >= target).
    The escalating per-day ladder means re-buying what was already bought would
    overpay, so reading-first is required. num = target - current in one shop_buy.

    Pass a pre-read ``counts`` (from read_shop_counts) to batch several buys off one
    shop_info read. Returns {shop_id, target, before, need, bought, ok, code}.
    """
    if counts is None:
        counts = read_shop_counts(client, shop_type, timeout=timeout)
    before = int(counts.get(shop_id, 0))
    need = target - before
    if need <= 0:
        logger.info("ws_token farm: buy_to_daily_target shop_id=%d already %d/%d — skip",
                    shop_id, before, target)
        return {"shop_id": shop_id, "target": target, "before": before,
                "need": 0, "bought": 0, "ok": True, "code": 0}
    reply_cmd, reply = client.call_for(
        CMD_SHOP_BUY, build_shop_buy_body(shop_type, shop_id, need),
        expect_cmds=(CMD_SHOP_BUY, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning("ws_token farm: buy_to_daily_target shop_id=%d rejected 0x0201 code=%s",
                       shop_id, code)
        return {"shop_id": shop_id, "target": target, "before": before,
                "need": need, "bought": 0, "ok": False, "code": code}
    # shop_buy_s2c {shop_id#1, num#2}
    bought = _as_int(codec.walk_dict(reply).get(2))
    logger.info("ws_token farm: buy_to_daily_target shop_id=%d %d/%d -> bought %d",
                shop_id, before, target, bought)
    return {"shop_id": shop_id, "target": target, "before": before,
            "need": need, "bought": bought, "ok": True, "code": 0}


def buy_farm_shop(
    client: WSGameClient,
    buy_list: Sequence[dict],
    *,
    shop_type: int = FARM_SHOP_TYPE,
    timeout: Optional[float] = None,
) -> list[dict]:
    """Buy several farm-shop items up to their daily targets off ONE shop_info read.

    ``buy_list`` entries: {shop_id, target} (optional per-entry shop_type). Reads
    today's counts once, then buy_to_daily_target each. Respects items already
    bought via the GUI (reads current count, buys only the remainder).
    """
    if not buy_list:
        return []
    counts = read_shop_counts(client, shop_type, timeout=timeout)
    results: list[dict] = []
    for entry in buy_list:
        sid = int(entry["shop_id"])
        target = int(entry.get("target", 0))
        st = int(entry.get("shop_type", shop_type))
        # counts read for shop_type; entries with a different shop_type re-read.
        c = counts if st == shop_type else None
        results.append(buy_to_daily_target(
            client, sid, target, shop_type=st, counts=c, timeout=timeout))
    return results


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
