"""加工坊 (processing workshop) task over a logged-in WSGameClient — pure WS.

Drives the worker_processing_workshop family (module 72 / 0x48): read the
workshop state + dining-hall食物, assign food to start a workshop, cancel a
running workshop, and collect (transfer out) finished materials. Built and framed
by codec + client, NOT the in-game netManager.

Field numbers are the live-exported truth
(docs/protocol/WORKER_PROCESSING_WORKSHOP_PROTO_SCHEMA.json, module 72; captured
via CDP fake-cnet 2026-06-09). c2s and s2c share the same cmd id, BUT a rejected
mutate replies on the 0x0201 error channel — never on the action's own cmd.

  worker_pw_info     18434  c2s {} -> s2c { auto_use_food_list#1 repeated uint32,
                                            food_info#2 repeated p_worker }
  worker_pw_choose_food 18435 c2s { food_list#1 p_key_value{k,v}, workshop_id#2 }
  worker_pw_cancel_work 18438 c2s { workshop_id#1 }
  worker_pw_crops_transfer 18440 c2s { materials#1 uint32, materials_num#2 uint32 }
  worker_pw_dining_hall 18441 c2s {} -> s2c { food_list#1 repeated p_key_value }
  p_worker     { team_cfg_id#1, worker_base#2:p_worker_base, worker_status#3,
                 auto_feed#4, unlock_slot_num#5, ...,
                 pw_worker_info#7:p_worker_pw_food_info }   (#7 is the workshop state)
  p_key_value  { k#1 int64, v#2 int64 }

processing cycle (read once, reuse — see read_info docstring):
  read_info -> see each workshop's worker_status (>0 = running) + auto_use_food_list
  read_dining_hall -> available foods [(food_id, count), ...]
  choose_food(food_id, count, workshop_id) -> assign food, start processing
  (later) crops_transfer -> move finished output materials out
  cancel_work(workshop_id) -> stop a running workshop early

p_worker_pw_food_info (#7) is NOT in the exported schema, so it is kept as raw
bytes for now (live-confirm its sub-fields before parsing them).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

# --- cmd ids (module 72; c2s and s2c share the same id) ---------------------
# Failures reply on CMD_ERROR (0x0201), NOT on the action's own cmd.
CMD_CROPS_AUTO_TRANSFER = 18433  # worker_pw_crops_auto_transfer {material_id, is_auto_transfer}
CMD_INFO = 18434                 # worker_pw_info
CMD_CHOOSE_FOOD = 18435          # worker_pw_choose_food
CMD_CANCEL_WORK = 18438          # worker_pw_cancel_work
CMD_AUTO_ADD_MATERIALS = 18439   # worker_pw_auto_add_materials {code, workshop_id}
CMD_CROPS_TRANSFER = 18440       # worker_pw_crops_transfer {materials, materials_num}
CMD_DINING_HALL = 18441          # worker_pw_dining_hall
CMD_UNLOCK_WORKSHOP = 18443      # worker_pw_unlock_workshop {id}
CMD_ADD_MATERIALS = 18444        # worker_pw_add_materials {id, target_food}
CMD_FOOD_AUTO_USE = 18445        # worker_pw_food_auto_use {food_id, is_auto_use}
CMD_ERROR = 0x0201               # error.error_info_s2c {error_code#1}

# Known error codes (configErrorInfo, live-decoded 2026-06-09):
ERR_COOLDOWN = 90       # 冷卻時間未到
ERR_NOT_ENOUGH = 159    # 次數不足
ERR_EVENT_OVER = 173    # 活動已結束

# configWorkshop mapping (CDP-exported 2026-06-09, authoritative):
#   configWorkshop.id (=workshop_id on wire)  team_cfg_id  name
#   1                                          6001         手動加工  (manual; rejects choose_food/cancel)
#   2                                          6002         小隊加工  (team processing)
#   3                                          6003         小隊加工  (team processing)
#
# worker_pw_info (18434) food_info#2 p_worker.team_cfg_id#1 gives the team_cfg_id
# (e.g. 6001 / 6002); choose_food and cancel_work use configWorkshop.id on the wire.
# Use team_cfg_id_to_workshop_id() to convert, or Workshop.workshop_id property.
TEAM_TO_WORKSHOP_ID: dict[int, int] = {6001: 1, 6002: 2, 6003: 3}


def team_cfg_id_to_workshop_id(team_cfg_id: int) -> int:
    """Convert p_worker.team_cfg_id (6001/6002/6003) to configWorkshop.id (1/2/3).

    Raises KeyError for unknown team_cfg_id values.  Use the result as the
    workshop_id wire field in choose_food, cancel_work, etc.
    """
    return TEAM_TO_WORKSHOP_ID[team_cfg_id]


# 小隊加工 recipes (food ids resolved from configFood on live client, 2026-06-09).
# configFood (NOT configGoods): 8001 脆脆餅乾 approach=[[6017,2]],
#   8003 活力精華 approach=[], 8005 精英拼盤 approach=[[6019,2],[6020,2],[6021,2]].
# choose_food requires approach materials in inventory; 手動加工 (workshop_id=1)
# rejects team choose_food entirely (live-confirmed on 5554: code=2 param invalid).
# The user runs these two recipes on 小隊加工 (workshop_id 2/3):
FOOD_CRISPY_COOKIE = 8001   # 脆脆餅乾
FOOD_ELITE_PLATTER = 8005   # 精英拼盤 (菁英拼盤)
RECIPE_FOOD_IDS = (FOOD_CRISPY_COOKIE, FOOD_ELITE_PLATTER)


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class Workshop:
    """One加工坊 derived from a p_worker entry in food_info#2.

    Only the fields populatable from the exported schema are kept; the workshop
    detail blob pw_worker_info#7 (type p_worker_pw_food_info, NOT in the schema)
    is preserved as raw bytes for later live-confirmed parsing.
    """

    team_cfg_id: int          # p_worker.team_cfg_id#1
    worker_status: int        # p_worker.worker_status#3 (>0 = running)
    auto_feed: int = 0        # p_worker.auto_feed#4
    unlock_slot_num: int = 0  # p_worker.unlock_slot_num#5
    pw_worker_info: bytes = b""  # p_worker.pw_worker_info#7 (opaque; live-confirm)

    @property
    def is_running(self) -> bool:
        """True iff the workshop is currently processing (worker_status > 0)."""
        return self.worker_status > 0

    @property
    def workshop_id(self) -> int:
        """configWorkshop.id (1/2/3) derived from team_cfg_id via TEAM_TO_WORKSHOP_ID.

        This is the wire field for choose_food, cancel_work, etc.
        Raises KeyError if team_cfg_id is not in TEAM_TO_WORKSHOP_ID.
        """
        return team_cfg_id_to_workshop_id(self.team_cfg_id)


@dataclass(frozen=True)
class WorkshopInfo:
    """worker_pw_info_s2c: the auto-use food list + every workshop's p_worker."""

    workshops: tuple[Workshop, ...] = ()
    auto_use_food_list: tuple[int, ...] = ()
    raw: bytes = field(compare=False, default=b"")

    @property
    def running(self) -> tuple[Workshop, ...]:
        return tuple(w for w in self.workshops if w.is_running)

    @property
    def idle(self) -> tuple[Workshop, ...]:
        return tuple(w for w in self.workshops if not w.is_running)


# --- body builders ----------------------------------------------------------

def build_choose_food_body(food_k: int, food_v: int, workshop_id: int) -> bytes:
    """choose_food_c2s {food_list#1 p_key_value{k#1, v#2}, workshop_id#2}.

    food_list is ONE p_key_value (k = food id, v = count/amount), nested at field
    1; workshop_id#2 follows it.
    """
    return codec.pb_msg(1, _kv(food_k, food_v)) + codec.pb_uint(2, workshop_id)


def build_cancel_body(workshop_id: int) -> bytes:
    """cancel_work_c2s {workshop_id#1}."""
    return codec.pb_uint(1, workshop_id)


def build_collect_body(material_id: int, num: int) -> bytes:
    """crops_transfer_c2s {materials#1 uint32, materials_num#2 uint32}.

    # live-confirm: crops_transfer is keyed by (material_id, num), NOT by
    #   workshop_id — it transfers ``num`` units of a finished material out of the
    #   workshop store. The exact "collect all finished output" mechanism (which
    #   material_id, whether num is required, or whether crops_auto_transfer 18433
    #   is the real日常 path) must be confirmed on a live session before relying on
    #   it. Builder mirrors the schema field order exactly.
    """
    return codec.pb_uint(1, material_id) + codec.pb_uint(2, num)


# --- parsers ----------------------------------------------------------------

def _parse_worker(entry: bytes) -> Workshop:
    d = codec.walk_dict(entry)
    pw = d.get(7)
    return Workshop(
        team_cfg_id=_as_int(d.get(1)),
        worker_status=_as_int(d.get(3)),
        auto_feed=_as_int(d.get(4)),
        unlock_slot_num=_as_int(d.get(5)),
        pw_worker_info=bytes(pw) if isinstance(pw, (bytes, bytearray)) else b"",
    )


def parse_info(body: bytes) -> WorkshopInfo:
    """worker_pw_info_s2c: auto_use_food_list#1 (repeated uint32) +
    food_info#2 (repeated p_worker)."""
    workshops: list[Workshop] = []
    auto_use: list[int] = []
    for fnum, val in codec.walk(body):
        if fnum == 1:
            auto_use.append(_as_int(val))
        elif fnum == 2 and isinstance(val, (bytes, bytearray)):
            workshops.append(_parse_worker(bytes(val)))
    return WorkshopInfo(
        workshops=tuple(workshops),
        auto_use_food_list=tuple(auto_use),
        raw=body,
    )


def parse_dining_hall(body: bytes) -> list[tuple[int, int]]:
    """dining_hall_s2c: food_list#1 repeated p_key_value -> [(food_id, count), ...]."""
    out: list[tuple[int, int]] = []
    for fnum, val in codec.walk(body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            out.append((_as_int(kv.get(1)), _as_int(kv.get(2))))
    return out


# --- reads (info / dining_hall reply on their own cmd) ----------------------

def read_info(client: WSGameClient, *, timeout: Optional[float] = None) -> WorkshopInfo:
    """Read workshop state (info 18434, empty request body).

    Read once and reuse the result: the workshop state rarely changes within a
    single task pass, and re-reading needlessly wastes a round-trip.
    """
    return parse_info(client.call(CMD_INFO, b"", timeout=timeout))


def read_dining_hall(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> list[tuple[int, int]]:
    """Read the dining hall's available foods (dining_hall 18441, empty body)."""
    return parse_dining_hall(client.call(CMD_DINING_HALL, b"", timeout=timeout))


# --- mutates (success on own cmd OR failure on 0x0201) ----------------------

def _mutate(
    client: WSGameClient, cmd: int, body: bytes, *, timeout: Optional[float] = None
) -> dict:
    """Send a workshop mutate that can be rejected.

    SUCCESS replies on the action's own ``cmd``; a REJECTION replies on the 0x0201
    error channel ({error_code#1}). Waits for EITHER so a rejection records
    ``ok=False`` with its error code instead of timing out / raising. Returns
    ``{ok, error_code, reply_cmd, raw}``.
    """
    reply_cmd, reply = client.call_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning("ws_token workshop: cmd=%d rejected 0x0201 error_code=%s",
                       cmd, code)
        return {"ok": False, "error_code": code, "reply_cmd": reply_cmd, "raw": reply}
    return {"ok": True, "error_code": None, "reply_cmd": reply_cmd, "raw": reply}


def choose_food(
    client: WSGameClient,
    *,
    food_k: int,
    food_v: int,
    workshop_id: int,
    timeout: Optional[float] = None,
) -> dict:
    """指派食物開工: choose_food {food_list{k,v}, workshop_id}.

    food_k = food id, food_v = count/amount (from read_dining_hall). Returns
    {ok, error_code, ...}; a rejection (e.g. 90 冷卻時間未到) is ok=False, no crash.
    """
    logger.info("ws_token workshop: choose_food food=%s:%s workshop_id=%s",
                food_k, food_v, workshop_id)
    return _mutate(client, CMD_CHOOSE_FOOD,
                   build_choose_food_body(food_k, food_v, workshop_id), timeout=timeout)


def cancel_work(
    client: WSGameClient, workshop_id: int, *, timeout: Optional[float] = None
) -> dict:
    """取消加工: cancel_work {workshop_id}. Returns {ok, error_code, ...}."""
    logger.info("ws_token workshop: cancel_work workshop_id=%s", workshop_id)
    return _mutate(client, CMD_CANCEL_WORK,
                   build_cancel_body(workshop_id), timeout=timeout)


def collect(
    client: WSGameClient,
    *,
    material_id: int,
    num: int,
    timeout: Optional[float] = None,
) -> dict:
    """收成出貨: crops_transfer {materials=material_id, materials_num=num}.

    Transfers ``num`` units of finished material ``material_id`` out of the
    workshop store. Returns {ok, error_code, ...}; a rejection is ok=False.

    # live-confirm: see build_collect_body — the exact (material_id, num) source
    #   for a "collect all finished output" pass must be read off a live session.
    """
    logger.info("ws_token workshop: collect material_id=%s num=%s", material_id, num)
    return _mutate(client, CMD_CROPS_TRANSFER,
                   build_collect_body(material_id, num), timeout=timeout)


def switch_recipe(
    client: WSGameClient,
    *,
    team_cfg_id: int,
    food_id: int,
    cancel_first: bool = True,
    timeout: Optional[float] = None,
) -> dict:
    """切換小隊加工配方: 取消 (cancel) the workshop FIRST, then start the new food.

    ``team_cfg_id`` is p_worker.team_cfg_id#1 as returned by worker_pw_info (18434)
    — e.g. 6002 for 小隊加工.  It is translated to configWorkshop.id internally via
    TEAM_TO_WORKSHOP_ID before sending on the wire (wire field = 2, not 6002).

    In-game rule: you MUST press 取消 before changing the recipe of a running
    小隊加工; only then can you 確定 the new one.  The quantity is read from the
    dining hall (make as many as available — the workshop runs until materials hit
    zero).  ``food_id`` must be one of RECIPE_FOOD_IDS (8001 脆脆餅乾 / 8005 精英拼盤).

    ``cancel_first``: sending cancel_work to an IDLE (worker_status=0) workshop
    gets NO reply at all — state-gated silence (live-confirmed 2026-06-10 on
    7fe98fc6, same pattern as guild_treasure dormancy).  Callers should pass
    ``cancel_first=w.is_running`` (from read_info) to skip the pointless attempt;
    a skipped cancel reports ``{"ok": True, "error_code": None,
    "skipped": "not running"}``.

    BEST-EFFORT (live 2026-06-10, twice on 7fe98fc6): the server is state-gated
    silent on cancel AND choose whenever the account state does not match — it
    happened even on a worker_status>0 workshop, so worker_status is NOT a
    reliable "is processing" signal (the real state may live in the unparsed
    pw_worker_info#7 blob — needs live recon).  Both steps therefore catch
    :class:`WSTimeoutError` and surface ``{"ok": False, "error_code": None,
    "timeout": True}`` instead of raising; after a cancel timeout the choose
    still runs (choose proves by itself whether the switch is possible).

    Note: 手動加工 (team_cfg_id=6001, workshop_id=1) rejects team choose_food with
    code=2 (param invalid) — do NOT call switch_recipe for it.

    Returns {cancelled, chosen, food_id, count, workshop_id}.  Rejections (0x0201)
    are surfaced in sub-dicts (ok=False) rather than crashing.
    """
    wire_id = team_cfg_id_to_workshop_id(team_cfg_id)
    if cancel_first:
        try:
            cancelled = cancel_work(client, wire_id, timeout=timeout)
        except WSTimeoutError:
            logger.warning(
                "ws_token workshop: cancel_work workshop_id=%s no response "
                "(state-gated silence) — continuing to choose", wire_id)
            cancelled = {"ok": False, "error_code": None, "timeout": True}
    else:
        cancelled = {"ok": True, "error_code": None, "skipped": "not running"}
    foods = dict(read_dining_hall(client, timeout=timeout))
    count = foods.get(food_id, 0)
    try:
        chosen = choose_food(client, food_k=food_id, food_v=count,
                             workshop_id=wire_id, timeout=timeout)
    except WSTimeoutError:
        logger.warning(
            "ws_token workshop: choose_food food=%s workshop_id=%s no response "
            "(state-gated silence)", food_id, wire_id)
        chosen = {"ok": False, "error_code": None, "timeout": True}
    logger.info(
        "ws_token workshop: switch_recipe team_cfg_id=%s workshop_id=%s "
        "food_id=%s count=%s cancelled_ok=%s chosen_ok=%s",
        team_cfg_id, wire_id, food_id, count,
        cancelled.get("ok"), chosen.get("ok"),
    )
    return {
        "cancelled": cancelled,
        "chosen": chosen,
        "food_id": food_id,
        "count": count,
        "workshop_id": wire_id,
    }


def rotate_team_recipes(
    client: WSGameClient, *, parity: int, timeout: Optional[float] = None,
) -> dict:
    """12h 配方輪換（使用者 2026-06-10 指定：兩類別 12hr 切一次）。

    把 RECIPE_FOOD_IDS (8001 脆脆餅乾 / 8005 精英拼盤) 輪流指派給每個小隊加工
    （team_cfg_id 6002/6003；手動加工 6001 一律不動）：parity 偶數 = 依序
    [8001, 8005, ...]，奇數 = 反序。每個 workshop 走已驗的 switch_recipe
    （cancel → dining_hall → choose，count = 餐廳現有全量）。idle 工坊
    （worker_status=0）送 cancel 伺服器不回包 → 用 cancel_first=w.is_running
    跳過 cancel（live 2026-06-10 7fe98fc6 證實）。整段 best-effort：cancel/choose
    對帳號狀態不符時伺服器靜默不回（含 worker_status>0 的工坊也發生過）→
    switch_recipe 內吞 WSTimeoutError 回 timeout 標記，不往外拋；呼叫端
    （runner）以「至少一個 chosen ok」判定本輪是否有效。

    CADENCE 不在這裡：呼叫端（runner）用 ws_token.state 記 last_rotate_ts/parity，
    12h 未到就不呼叫本函式。Returns {parity, switched: [...]}。
    """
    info = read_info(client, timeout=timeout)
    teams = [w for w in info.workshops
             if w.team_cfg_id in TEAM_TO_WORKSHOP_ID and w.team_cfg_id != 6001]
    order = RECIPE_FOOD_IDS if parity % 2 == 0 else tuple(reversed(RECIPE_FOOD_IDS))
    switched: list[dict] = []
    for i, w in enumerate(teams):
        food_id = order[i % len(order)]
        result = switch_recipe(client, team_cfg_id=w.team_cfg_id,
                               food_id=food_id, cancel_first=w.is_running,
                               timeout=timeout)
        switched.append({"team_cfg_id": w.team_cfg_id, **result})
    logger.info("ws_token workshop: rotate_team_recipes parity=%d switched=%d",
                parity % 2, len(switched))
    return {"parity": parity % 2, "switched": switched}


# --- helpers ----------------------------------------------------------------

def _kv(k: int, v: int) -> bytes:
    """Encode one p_key_value {k#1, v#2}."""
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
