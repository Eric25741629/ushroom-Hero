"""龍骸聖域決策樹（純函式，無 IO）。

port 自客戶端 ActivityLhsyDataCache.autoExploreHandler，含三項覆寫：
  1. 陷阱 IsChallenge 一律求助 CHOICE_ASK_HELP，絕不 CHOICE_ADVANCE 掙扎。
  2. ceng==2 達 tier_three_require 即 STOP，不 enter_ceng(3)。
  3. 體力不足即 STOP，不使用體力道具。
判斷順序對齊客戶端：再次擊殺(列表) → 協助隊友 → 領獎 → 進層/停 → 當前事件。
"""
from __future__ import annotations

from dataclasses import dataclass

from dragon_realm import constants as C
from dragon_realm.state import DragonState


@dataclass(frozen=True)
class Prefs:
    my_role_id: int  # reserved: from_raw() uses role_id for is_mine; kept for callers/future use
    assist_teammates: bool = True
    auto_open_box: bool = True


@dataclass(frozen=True)
class Action:
    kind: str
    choice: int = 0
    uid: int = 0
    ceng: int = 0
    role_id: int = 0
    event_id: int = 0
    reason: str = ""

    @staticmethod
    def explore() -> "Action":
        return Action(C.A_EXPLORE)

    @staticmethod
    def from_choice(choice: int, uid: int = 0) -> "Action":
        return Action(C.A_CHOICE, choice=choice, uid=uid)

    @staticmethod
    def enter_ceng(ceng: int) -> "Action":
        return Action(C.A_ENTER_CENG, ceng=ceng)

    @staticmethod
    def provide_help(role_id: int, event_id: int) -> "Action":
        return Action(C.A_PROVIDE_HELP, role_id=role_id, event_id=event_id)

    @staticmethod
    def receive_help(event_id: int) -> "Action":
        return Action(C.A_RECEIVE_HELP, event_id=event_id)

    @staticmethod
    def wait() -> "Action":
        return Action(C.A_WAIT)

    @staticmethod
    def stop(reason: str) -> "Action":
        return Action(C.A_STOP, reason=reason)


def _cooldown_passed(state: DragonState, back_kill_time: int, cooldown: int) -> bool:
    return back_kill_time + cooldown - state.server_time <= 0


def decide(state: DragonState, config, prefs: Prefs) -> Action:
    if not state.activity_open:
        return Action.stop("activity_closed")

    # 1. 再次擊殺（列表）：我方 PVE/PVP 事件冷卻已過
    # NB(live-verify): faithful port of client. If event_list can contain the CURRENT challenge event with back_kill_time<=now-cooldown, this path could shadow the trap/monster ASK_HELP override. Confirm during live recon (DRAGON_REALM_SCHEMA.md) whether event_list excludes the active event.
    for ev in state.event_list:
        if ev.is_mine and ev.event_type in C.MONSTER_TYPES and ev.event_id:
            if _cooldown_passed(state, ev.back_kill_time, config.back_kill_cooldown):
                return Action.from_choice(C.CHOICE_ADVANCE, uid=ev.uid)

    # 2. 協助隊友（有 help_hp 時）
    if prefs.assist_teammates and state.help_hp > 0:
        for ev in state.event_list:
            if (not ev.is_mine) and ev.event_id:
                return Action.provide_help(ev.role_id, ev.event_id)

    # 3. 領取協助獎勵
    if state.help_events:
        return Action.receive_help(state.help_events[0])

    # 4. 進入下一層 / 終止（覆寫）
    if state.ceng == 1:
        gtid, need = config.tier_two_require
        if state.bag_count(gtid) >= need:
            return Action.enter_ceng(2)
    elif state.ceng == 2:
        gtid, need = config.tier_three_require
        if state.bag_count(gtid) >= need:
            return Action.stop("reached_tier_three_gate")

    # 5. 當前事件
    if state.event_id == 0:
        need = config.stamina_tier[state.ceng - 1]
        if state.hp >= need:
            return Action.explore()
        return Action.stop("out_of_stamina")

    et = state.event_type
    if et in C.MONSTER_TYPES:
        if state.is_challenge:
            if not state.is_ask_help:
                return Action.from_choice(C.CHOICE_ASK_HELP)
            if _cooldown_passed(state, state.back_kill_time, config.back_kill_cooldown):
                return Action.from_choice(C.CHOICE_ADVANCE, uid=state.event_uid)
            return Action.wait()
        return Action.from_choice(C.CHOICE_ADVANCE)

    if et == C.TRAP:
        if state.is_challenge:
            return Action.from_choice(C.CHOICE_ASK_HELP)   # 覆寫：永不掙扎
        return Action.from_choice(C.CHOICE_ADVANCE)

    if et == C.BOX:
        if prefs.auto_open_box:
            gtid = config.chest_key.get(state.event_id)
            if gtid and state.bag_count(gtid) > 0:
                return Action.from_choice(C.CHOICE_ADVANCE)
        return Action.from_choice(C.CHOICE_DETOUR)

    if et in (C.BUFF, C.CAVE):
        return Action.from_choice(C.CHOICE_ADVANCE)

    return Action.wait()
