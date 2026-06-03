"""龍骸聖域狀態模型 + 正規化。純 Python、可單測。

``from_raw`` 接受 client.py 從 ``window.__dr_state`` 攤平後的中間 dict
（事件 data 已轉成 EventDataKey->value 的 dict）。實際 live 鍵路徑見
docs/protocol/DRAGON_REALM_SCHEMA.md；client.py 負責把 raw s2c 攤成此形狀。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from dragon_realm import constants as C


@dataclass(frozen=True)
class DragonEvent:
    role_id: int
    event_id: int
    event_type: int
    uid: int
    back_kill_time: int
    is_mine: bool


@dataclass(frozen=True)
class DragonState:
    activity_open: bool
    ceng: int
    hp: int
    server_time: int
    help_hp: int
    event_id: int
    event_type: int
    event_uid: int
    is_challenge: bool
    is_ask_help: bool
    back_kill_time: int
    event_list: tuple
    help_events: tuple
    bag: Mapping[int, int]

    def bag_count(self, gtid: int) -> int:
        return int(self.bag.get(gtid, 0))

    @staticmethod
    def from_raw(raw: dict, my_role_id: int) -> "DragonState":
        data: Mapping[int, int] = {int(k): int(v) for k, v in (raw.get("event_data") or {}).items()}
        events = []
        for e in (raw.get("event_list") or []):
            rid = int(e.get("role_id", 0))
            events.append(DragonEvent(
                role_id=rid,
                event_id=int(e.get("event_id", 0)),
                event_type=int(e.get("event_type", 0)),
                uid=int(e.get("id", 0)),
                back_kill_time=int(e.get("back_kill_time", 0)),
                is_mine=(rid == my_role_id),
            ))
        return DragonState(
            activity_open=bool(raw.get("activity_open", False)),
            ceng=int(raw.get("ceng", 1)),
            hp=int(raw.get("hp", 0)),
            server_time=int(raw.get("server_time", 0)),
            help_hp=int(raw.get("help_hp", 0)),
            event_id=int(raw.get("event_id", 0)),
            event_type=int(raw.get("event_type", 0)),
            event_uid=int(raw.get("event_uid", 0)),
            is_challenge=bool(data.get(C.K_IS_CHALLENGE, 0)),
            is_ask_help=bool(data.get(C.K_IS_ASK_HELP, 0)),
            back_kill_time=int(data.get(C.K_BACK_KILL_TIME, 0)),
            event_list=tuple(events),
            help_events=tuple(int(x) for x in (raw.get("help_events") or [])),
            bag=dict(raw.get("bag") or {}),
        )
