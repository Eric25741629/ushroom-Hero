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
from ws_token import dragon_sos
from ws_token.client import WSGameClient
from ws_token.mining import InventoryTracker

logger = logging.getLogger(__name__)

CMD_INFO = 0x4F01           # c2s {} -> s2c (same cmd) { team_id, ceng, hp, ... }
CMD_EXPLORE = 0x4F10        # c2s {} -> s2c (DIFFERENT cmd, fire-and-forget)
CMD_CHOICE = 0x4F12         # c2s { choice#1, optional event_uid#2 } -> s2c (different cmd)
CMD_ENTER_CENG = 0x4F11     # c2s { ceng#1 } -> s2c (different cmd)

# dragon_realm_info_s2c 真實佈局（LIVE ground truth, dcache_5554.json）：
#   field 1=team_id 2=ceng 3=hp 4=next_hp_time 5=help_hp 6=help_counter
#   field 7=event_id 8=event_data(repeated {k#1,v#2}) 9=ext(repeated {k#1,v#2})
#   field 10=event_uid
# 修正前把 field 8 當 event_uid、field 9 當 event_data，導致 ext 的 {k:1,v:0} 被
# 誤讀成 K_PVE_HP → 寶箱被當小怪送 ADVANCE → server 忽略 → deadloop。
F_CENG = 2
F_HP = 3
F_EVENT_ID = 7
F_EVENT_DATA = 8
F_EVENT_UID = 10

# EventData key（當前事件 data 陣列的 k）
K_PVE_HP = 1
K_TRAP_TIME = 2
K_IS_CHALLENGE = 4
K_MAX_HP = 6

# event_choice c2s 的 choice 值（客戶端逆向，dragon_realm/constants.py 同義）
CHOICE_ADVANCE = 1   # 前進 / 擊殺
CHOICE_DETOUR = 2    # 繞路 / 繼續探索（寶箱不開箱走這個）
CHOICE_ASK_HELP = 3  # 求助（挑戰型事件）

KEY_ITEM = 1527
STAMINA = (1, 2, 3)  # per-tier stamina cost
TIER2_KEYS = 1
TIER3_KEYS = 2

# 寶箱類事件的 event_id：DETOUR(繼續探索)跳過、不開箱、不耗秘銀龍鑰；箱子進背包。
# 其餘非戰鬥事件（山洞探索報告等）一律 ADVANCE(1) 才能結束、繼續流程。
# LIVE 實證：山洞(eid=14) 客戶端關閉報告送 0x4F12{1:1}=ADVANCE；秘銀龍骸箱=eid 11；
# 2026-07-16 live：寶箱不只一種，不同種寶箱各有自己的 eid（另見 12/13；
# 舊版只認 11 導致其他寶箱被 ADVANCE 開箱、遭拒而 deadloop）。
# 未知非戰鬥 eid 一律走 DETOUR 優先 + 凍住才 ADVANCE 的 fallback（見 run()），不再靠列舉。
CHEST_EIDS = {11, 12, 13}


def _choice_body(choice: int, event_uid: int = 0) -> bytes:
    """event_choice c2s body = {choice#1, event_uid#2}.

    LIVE 黃金樣本（frame 8，寶箱 event_id=11 繼續探索）= 08021000 = {1:2, 2:0}。
    當前事件一律 event_uid=0（再次擊殺/協助等專用流程才帶非 0，本 loop 不走）。
    """
    return codec.pb_uint(1, int(choice)) + codec.pb_uint(2, int(event_uid))


def _event_data_map(body: bytes) -> dict:
    """把 repeated field 8（event_data，每筆 {k#1, v#2}）攤平成 {k: v}。"""
    ed: dict[int, int] = {}
    for fn, val in codec.walk(body):
        if fn == F_EVENT_DATA and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            k = kv.get(1)
            if isinstance(k, int):
                ed[k] = kv.get(2, 0)
    return ed


def _claim_rewards_at_boundary(client: WSGameClient) -> None:
    """純 WS 龍骸流程結束前，盡力領取本帳號協助獎勵。"""
    creds = getattr(client, "_creds", None)
    role_id = int(getattr(creds, "role_id", 0) or 0)
    if not role_id:
        logger.warning("[dragon_ws] cannot auto-claim help rewards: role_id unavailable")
        return
    try:
        result = dragon_sos.claim_help_rewards(client, role_id)
        if result["attempted"]:
            logger.info(
                "[dragon_ws] auto-claimed help rewards: attempted=%d claimed=%d remaining=%d",
                result["attempted"], result["claimed"], result["remaining"])
    except Exception:  # noqa: BLE001 — 領獎失敗不可中止探索結果
        logger.warning("[dragon_ws] auto-claim help rewards failed", exc_info=True)


def _read_info(client: WSGameClient) -> dict:
    body = client.call(CMD_INFO, b"")
    d = codec.walk_dict(body)
    euid = d.get(F_EVENT_UID, 0)
    return {
        "ceng": d.get(F_CENG, 1) if isinstance(d.get(F_CENG), int) else 1,
        "hp": d.get(F_HP, 0) if isinstance(d.get(F_HP), int) else 0,
        "eid": d.get(F_EVENT_ID, 0) if isinstance(d.get(F_EVENT_ID), int) else 0,
        "euid": euid if isinstance(euid, int) else 0,
        "ed": _event_data_map(body),
    }


def _infer_type(ed: dict) -> str:
    if ed.get(K_PVE_HP) or ed.get(K_MAX_HP):
        return "monster"
    if K_TRAP_TIME in ed:
        return "trap"
    return "other"


def run(client: WSGameClient, tracker: InventoryTracker,
        *, max_actions: int = 200, pace: float = 1.0, max_stuck: int = 6) -> str:
    """Run dragon realm loop. Returns stop reason."""
    try:
        tracker.seed_from_query(client)
    except Exception:
        logger.warning("[dragon_ws] inventory seed failed, key count may be stale")
    info = _read_info(client)
    logger.info("[dragon_ws] start: ceng=%d hp=%d keys=%d",
                info["ceng"], info["hp"], tracker.counts.get(KEY_ITEM, 0))

    actions = 0
    last_sig = None
    stuck = 0
    detoured_euid = None  # 未知非戰鬥事件已試過 DETOUR 的 euid（無效才升級 ADVANCE）
    while actions < max_actions:
        info = _read_info(client)
        ceng, hp, eid, euid, ed = (
            info["ceng"], info["hp"], info["eid"], info["euid"], info["ed"])

        # Dead-loop guard: if the server state is byte-identical for max_stuck
        # consecutive reads, our actions aren't moving it (observed live: a CAVE
        # whose choice has no effect froze the loop into 200 wasted sends). Bail
        # fast instead of burning the whole action budget. waits==0 means the
        # WAIT-based detector in dragon_realm.service can't catch this case.
        sig = (ceng, hp, eid, euid)
        if sig == last_sig:
            stuck += 1
            if stuck >= max_stuck:
                logger.warning(
                    "[dragon_ws] dead-loop: state frozen %dx (ceng=%d hp=%d eid=%d), abort",
                    stuck, ceng, hp, eid)
                _claim_rewards_at_boundary(client)
                return "deadloop"
        else:
            stuck = 0
        last_sig = sig

        keys = tracker.counts.get(KEY_ITEM, 0)

        # tier transition
        if ceng == 1 and keys >= TIER2_KEYS:
            logger.info("[dragon_ws] entering ceng 2 (keys=%d)", keys)
            client.send(CMD_ENTER_CENG, codec.pb_uint(1, 2))
            time.sleep(pace)
            continue

        # 只有 server 權威 ceng==2（實際站在 2 樓）且鑰匙足夠才宣告到 3 樓門前。
        # ceng==1 時上面的 tier transition 會先送 enter_ceng(2)，絕不落到這裡。
        if ceng == 2 and keys >= TIER3_KEYS:
            logger.info("[dragon_ws] tier-3 gate (keys=%d), stop", keys)
            _claim_rewards_at_boundary(client)
            return "reached_tier_three_gate"

        # no event -> explore or stop
        if eid == 0:
            need = STAMINA[min(ceng - 1, 2)]
            if hp < need:
                logger.info("[dragon_ws] out of stamina (hp=%d)", hp)
                _claim_rewards_at_boundary(client)
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
        if et in ("monster", "trap") and ed.get(K_IS_CHALLENGE):
            choice = CHOICE_ASK_HELP        # 挑戰型：求助，絕不掙扎
        elif et in ("monster", "trap"):
            choice = CHOICE_ADVANCE         # 前進 / 擊殺
        elif eid in CHEST_EIDS:
            # 寶箱：「繼續探索」(DETOUR)，永不開箱、不消耗秘銀龍鑰；箱子進背包。
            choice = CHOICE_DETOUR
        elif (eid, euid) != detoured_euid:
            # 未知非戰鬥事件：先試 DETOUR（對寶箱安全，不會誤開箱耗鑰）。
            choice = CHOICE_DETOUR
            detoured_euid = (eid, euid)
        else:
            # DETOUR 無效（同一事件還在）→ 山洞類，ADVANCE(1) 才會結束事件。
            # LIVE 實證：DETOUR 對山洞(eid=14)無效會 deadloop（卡住並擋住進樓）。
            choice = CHOICE_ADVANCE
        client.send(CMD_CHOICE, _choice_body(choice))

        actions += 1
        logger.debug("[dragon_ws] [%d] c=%d hp=%d eid=%d et=%s", actions, ceng, hp, eid, et)
        time.sleep(pace * 0.5)

    _claim_rewards_at_boundary(client)
    return "budget_exhausted"
