"""龍骸聖域 pure-WS — explore loop: tier1 -> key -> tier2 -> 2 keys -> stop.

Module 79 (0x4F). LIVE-verified 2026-06-24 via CDP binary WS on 5554/小寶.
Never enters tier 3 (user handles manually).

Protocol: explore and choice are fire-and-forget (s2c response cmd differs from
c2s cmd). Re-read info_c2s (0x4F01, same-cmd response) after each action to get
updated state. Key count tracked via InventoryTracker (0x0402 goods push).
"""
from __future__ import annotations

import logging
import time

from ws_token import codec
from ws_token.client import WSGameClient
from ws_token.mining import InventoryTracker

logger = logging.getLogger(__name__)

CMD_INFO = 0x4F01           # c2s {} -> s2c (same cmd) { team_id, ceng, hp, ... }
CMD_EXPLORE = 0x4F10        # c2s {} -> s2c (DIFFERENT cmd, fire-and-forget)
CMD_CHOICE = 0x4F12         # c2s { choice#1, event_uid#2 } -> s2c (different cmd)
CMD_ENTER_CENG = 0x4F11     # c2s { ceng#1 } -> s2c (different cmd)

K_PVE_HP = 1
K_TRAP_TIME = 2
K_IS_CHALLENGE = 4
K_MAX_HP = 6

KEY_ITEM = 1527
STAMINA = (1, 2, 3)  # per-tier stamina cost
TIER2_KEYS = 1
TIER3_KEYS = 2


def _read_info(client: WSGameClient) -> dict:
    body = client.call(CMD_INFO, b"")
    d = codec.walk_dict(body)
    ed_raw = d.get(9, b"")
    ed = codec.walk_dict(ed_raw) if isinstance(ed_raw, (bytes, bytearray)) else {}
    return {
        "ceng": d.get(2, 1),
        "hp": d.get(3, 0),
        "eid": d.get(7, 0),
        "euid": d.get(8, 0) if isinstance(d.get(8), int) else 0,
        "ed": ed,
    }


def _infer_type(ed: dict) -> str:
    if ed.get(K_PVE_HP) or ed.get(K_MAX_HP):
        return "monster"
    if K_TRAP_TIME in ed:
        return "trap"
    return "other"


def run(client: WSGameClient, tracker: InventoryTracker,
        *, max_actions: int = 200, pace: float = 1.0) -> str:
    """Run dragon realm loop. Returns stop reason."""
    try:
        tracker.seed_from_query(client)
    except Exception:
        logger.warning("[dragon_ws] inventory seed failed, key count may be stale")
    info = _read_info(client)
    logger.info("[dragon_ws] start: ceng=%d hp=%d keys=%d",
                info["ceng"], info["hp"], tracker.counts.get(KEY_ITEM, 0))

    actions = 0
    while actions < max_actions:
        info = _read_info(client)
        ceng, hp, eid, euid, ed = (
            info["ceng"], info["hp"], info["eid"], info["euid"], info["ed"])
        keys = tracker.counts.get(KEY_ITEM, 0)

        # tier transition
        if ceng == 1 and keys >= TIER2_KEYS:
            logger.info("[dragon_ws] entering ceng 2 (keys=%d)", keys)
            client.send(CMD_ENTER_CENG, codec.pb_uint(1, 2))
            time.sleep(pace)
            continue

        if ceng == 2 and keys >= TIER3_KEYS:
            logger.info("[dragon_ws] tier-3 gate (keys=%d), stop", keys)
            return "reached_tier_three_gate"

        # no event -> explore or stop
        if eid == 0:
            need = STAMINA[min(ceng - 1, 2)]
            if hp < need:
                logger.info("[dragon_ws] out of stamina (hp=%d)", hp)
                return "out_of_stamina"
            client.send(CMD_EXPLORE, b"")
            time.sleep(pace)
            # re-read to get event
            info = _read_info(client)
            eid, euid, ed = info["eid"], info["euid"], info["ed"]
            if not eid:
                continue  # explore didn't produce event yet, retry

        # handle event
        et = _infer_type(ed)
        if et == "trap" and ed.get(K_IS_CHALLENGE):
            client.send(CMD_CHOICE, codec.pb_uint(1, 3))  # ask help
        else:
            body = codec.pb_uint(1, 1)  # advance
            if euid and isinstance(euid, int):
                body += codec.pb_uint(2, euid)
            client.send(CMD_CHOICE, body)

        actions += 1
        logger.debug("[dragon_ws] [%d] c=%d hp=%d eid=%d et=%s", actions, ceng, hp, eid, et)
        time.sleep(pace * 0.5)

    return "budget_exhausted"
