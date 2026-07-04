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

processing cycle (idempotent "閒置才補" — assign_idle_workshops is the entry):
  read_info -> each workshop's selected_food (pw_worker_info#7.f2): 0=idle, else
               the food it is producing. This (NOT worker_status, which reads ~602
               whether idle or busy — live 2026-06-19) is the real busy signal.
  producible_count(materials, food) -> 可做量 from the 原料庫存 via configFood.approach.
  choose_food(food_id, count, workshop_id) -> assign food; success confirmed by
               re-reading 18434 (selected_food == food_id), NOT by an 18435 ack.
  assign_idle_workshops -> for each idle 小隊加工, choose the highest-value
               producible food. NEVER cancels a running workshop (it自然 turns idle
               when its materials run out).
  (later) crops_transfer / cancel_work remain available for manual / explicit use.

p_worker_pw_food_info (#7): only f2 (=selected_food) is decoded; the rest stays
raw bytes (live-confirm 進度/剩餘量 sub-fields before parsing them).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

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
# 手動加工 (workshop_id=1) rejects team choose_food entirely (live-confirmed on
# 5554: code=2 param invalid). The user runs these recipes on 小隊加工
# (workshop_id 2/3); 8005 精英拼盤 is the higher-value food so it is preferred
# first when both are producible.
FOOD_CRISPY_COOKIE = 8001   # 脆脆餅乾
FOOD_ELITE_PLATTER = 8005   # 精英拼盤 (菁英拼盤)
RECIPE_FOOD_IDS = (FOOD_ELITE_PLATTER, FOOD_CRISPY_COOKIE)  # value-high first

# configFood.approach — 每生產一單位食物消耗的原料 (item_id, per_unit), live
# CDP-exported on 5554 (2026-06-19, authoritative). choose_food requires these
# materials in inventory; producible_count(materials, food_id) = how many units
# the current 原料庫存 can make. 8003 活力精華 has no approach (無料) so it is not
# producible via the material path and is intentionally absent from the recipes.
RECIPE_APPROACH: dict[int, list[tuple[int, int]]] = {
    8001: [(6017, 2)],
    8002: [(6017, 1), (6019, 2)],
    8004: [(6017, 1), (6019, 2), (6020, 2)],
    8005: [(6019, 2), (6020, 2), (6021, 2)],
}


def producible_count(materials: dict[int, int], food_id: int) -> int:
    """可做量: how many units of ``food_id`` the 原料庫存 can produce.

    = ``min(⌊materials[m] / per_unit⌋)`` over every (m, per_unit) in the food's
    configFood.approach. A missing material counts as 0 (the food becomes
    unproducible). Foods with no approach (e.g. 8003) or unknown foods return 0
    — there is no material-derived production amount for them.
    """
    approach = RECIPE_APPROACH.get(food_id)
    if not approach:
        return 0
    return min(materials.get(mat, 0) // per for mat, per in approach)


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class Workshop:
    """One加工坊 derived from a p_worker entry in food_info#2.

    ``selected_food`` (= pw_worker_info#7.f2, live-confirmed on 5554 2026-06-19)
    is the ONLY reliable "is this workshop busy" signal: 0 = idle, otherwise the
    food id currently in production. ``worker_status`` (601/602) is NOT a busy
    signal — it reads ~602 whether idle or producing. The raw pw_worker_info#7
    bytes are still preserved for fields not yet decoded (進度/剩餘量).
    """

    team_cfg_id: int          # p_worker.team_cfg_id#1
    worker_status: int        # p_worker.worker_status#3 (NOT a busy signal)
    auto_feed: int = 0        # p_worker.auto_feed#4
    unlock_slot_num: int = 0  # p_worker.unlock_slot_num#5
    selected_food: int = 0    # pw_worker_info#7.f2 (0=idle, else food in production)
    pw_worker_info: bytes = b""  # p_worker.pw_worker_info#7 (raw; only f2 decoded)

    @property
    def is_running(self) -> bool:
        """True iff the workshop is currently producing a food (selected_food != 0).

        Driven by selected_food (pw_worker_info#7.f2), NOT worker_status — the
        latter is ~602 even when idle (live 2026-06-19).
        """
        return self.selected_food != 0

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
    pw_bytes = bytes(pw) if isinstance(pw, (bytes, bytearray)) else b""
    # pw_worker_info#7.f2 = selected_food (0=idle); the rest stays raw.
    selected_food = _as_int(codec.walk_dict(pw_bytes).get(2)) if pw_bytes else 0
    return Workshop(
        team_cfg_id=_as_int(d.get(1)),
        worker_status=_as_int(d.get(3)),
        auto_feed=_as_int(d.get(4)),
        unlock_slot_num=_as_int(d.get(5)),
        selected_food=selected_food,
        pw_worker_info=pw_bytes,
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
    """指派食物開工: choose_food {food_list{k,v}, workshop_id}, 再 re-read 確認.

    food_k = food id, food_v = 要生產的單位數 (1 ≤ v ≤ 可做量; the server does NOT
    clamp — sending 0 or an over-count is rejected with 0x0201 error_code=3 道具不足).

    Success is NOT acked on cmd 18435 (waiting for it times out — live-confirmed
    2026-06-19); the only reliable signal is re-reading worker_pw_info (18434) and
    checking that this workshop's ``selected_food`` now equals ``food_k``. So this
    fires the request with ``client.send`` (no same-cmd wait) and confirms by
    re-read. ``food_v < 1`` returns ``{ok: False, reason: "no_count"}`` WITHOUT
    sending anything. Returns ``{ok, food_id, count, workshop_id, reason?}``.
    """
    if food_v < 1:
        logger.info("ws_token workshop: choose_food food=%s workshop_id=%s count<1 "
                    "— skip (would trigger 0x0201 error_code=3)", food_k, workshop_id)
        return {"ok": False, "food_id": food_k, "count": food_v,
                "workshop_id": workshop_id, "reason": "no_count"}
    logger.info("ws_token workshop: choose_food food=%s:%s workshop_id=%s",
                food_k, food_v, workshop_id)
    client.send(CMD_CHOOSE_FOOD, build_choose_food_body(food_k, food_v, workshop_id))
    info = read_info(client, timeout=timeout)
    ok = any(w.workshop_id == workshop_id and w.selected_food == food_k
             for w in info.workshops if w.team_cfg_id in TEAM_TO_WORKSHOP_ID)
    if not ok:
        logger.warning("ws_token workshop: choose_food food=%s workshop_id=%s "
                       "not confirmed by re-read (server rejected / state-gated)",
                       food_k, workshop_id)
    return {"ok": ok, "food_id": food_k, "count": food_v, "workshop_id": workshop_id}


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


# 小隊加工 team_cfg_ids (手動加工 6001 excluded — it rejects team choose_food).
TEAM_WORKSHOP_CFG_IDS = (6002, 6003)


def assign_idle_workshops(
    client: WSGameClient,
    materials: dict[int, int],
    *,
    prefer_order: tuple[int, ...] = RECIPE_FOOD_IDS,
    timeout: Optional[float] = None,
) -> dict:
    """閒置才補配方：只對閒置 (selected_food==0) 的小隊加工指派食物,絕不動 running 工坊.

    Idempotent — safe to call every wake. For each 小隊加工 (team_cfg_id in
    TEAM_WORKSHOP_CFG_IDS; 手動加工 6001 ignored):
      - selected_food != 0 (正在做東西) -> skip, leave it running untouched. We
        NEVER cancel a producing workshop (cancelling a 跑到原料歸零 workshop mid-run
        is the old bug); it自然 turns idle when its materials run out.
      - selected_food == 0 (閒置) -> pick the first food in ``prefer_order``
        (default RECIPE_FOOD_IDS = 8005 value-high first) with
        ``producible_count(materials, food) >= 1`` and choose_food that many units.

    ``materials`` = the live 原料庫存 (e.g. inventory_tracker.counts from the login
    0x0402 snapshot). choose_food re-reads 18434 to confirm; a count<1 food is
    never sent (no 0x0201 error_code=3). Returns ``{"workshops": [ {team_cfg_id,
    workshop_id?, action, food_id?, count?, ok?, reason?}, ... ]}`` (one entry per
    workshop, action in {assigned, skipped, ignored}).
    """
    info = read_info(client, timeout=timeout)
    results: list[dict] = []
    for w in info.workshops:
        if w.team_cfg_id not in TEAM_WORKSHOP_CFG_IDS:
            results.append({"team_cfg_id": w.team_cfg_id, "action": "ignored",
                            "reason": "manual_or_unknown_workshop"})
            continue
        if w.selected_food != 0:
            results.append({"team_cfg_id": w.team_cfg_id,
                            "workshop_id": w.workshop_id, "action": "skipped",
                            "reason": "running", "food_id": w.selected_food})
            continue
        food_id, count = _pick_producible(materials, prefer_order)
        if food_id is None:
            logger.info("ws_token workshop: team_cfg_id=%s idle but no producible "
                        "food (原料不足) — leaving idle", w.team_cfg_id)
            results.append({"team_cfg_id": w.team_cfg_id,
                            "workshop_id": w.workshop_id, "action": "skipped",
                            "reason": "no_producible_food"})
            continue
        chosen = choose_food(client, food_k=food_id, food_v=count,
                             workshop_id=w.workshop_id, timeout=timeout)
        results.append({"team_cfg_id": w.team_cfg_id,
                        "workshop_id": w.workshop_id, "action": "assigned",
                        "food_id": food_id, "count": count,
                        "ok": chosen.get("ok")})
    assigned = sum(1 for r in results if r["action"] == "assigned")
    logger.info("ws_token workshop: assign_idle_workshops assigned=%d of %d "
                "team workshops", assigned,
                sum(1 for r in results if r["action"] != "ignored"))
    return {"workshops": results}


def _pick_producible(
    materials: dict[int, int], prefer_order: tuple[int, ...]
) -> tuple[Optional[int], int]:
    """First (food_id, count) in ``prefer_order`` with producible_count >= 1.

    Returns ``(None, 0)`` when no preferred food can be produced from ``materials``.
    """
    for food_id in prefer_order:
        count = producible_count(materials, food_id)
        if count >= 1:
            return food_id, count
    return None, 0


# --- helpers ----------------------------------------------------------------

def _kv(k: int, v: int) -> bytes:
    """Encode one p_key_value {k#1, v#2}."""
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
