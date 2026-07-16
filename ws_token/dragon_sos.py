"""龍骸聖域 SOS — 純-WS 自動協助求助隊友。

Dashboard 按 SOS 時，指定的協助號用既有的持久純 WS 連線（``control_panel.ws_session``）
讀求助清單、對每個 pending 求助送 ``provide_help``，再重讀確認。

協議 2026-06-26 live-verified（CDP 抓 cmd id + 純 WS 實測救援成功，使用者手機確認）：
- ``help_event_list`` 0x4F15：c2s body 空；s2c = {event_list: repeated #1, help_hp:u32 #2}
- ``p_dragon_realm_event`` = {id:u64 #1, event_id:u32 #2, role_id:u64 #3, status:u32 #4, data #5}
  前端可協助判斷是 status!=1；進攻求助可能是 status=2，不能只判 status==0
- ``provide_help`` 0x4F14 = {help_target:u64 #1, event_id:u32 #2}；fire-and-forget，重讀確認
- ``receive_help_event`` 0x4F17 = {event_id:u32 #1}；只領我方 status==1，重讀確認

``rescue_pending`` 吃任意 client（``call``/``send`` 介面）故可單測；``rescue_via_ws`` 接
``ws_session``（預設真模組，測試注入 fake）。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ws_token import codec

logger = logging.getLogger(__name__)

CMD_HELP_LIST = 0x4F15      # help_event_list_c2s/s2c（同 cmd 回應）
CMD_PROVIDE_HELP = 0x4F14   # provide_help_c2s
CMD_RECEIVE_HELP = 0x4F17   # receive_help_event_c2s

_STATUS_DONE = 1            # 前端 eventHelpRed：status==1 是已完成/可領協助獎勵
_F_EVENT_LIST = 1           # help_event_list_s2c.event_list（repeated）
_F_HELP_HP = 2              # help_event_list_s2c.help_hp
# p_dragon_realm_event 欄位
_E_ID, _E_EVENT_ID, _E_ROLE_ID, _E_STATUS = 1, 2, 3, 4

_PROVIDE_PACE_SEC = 0.3     # 連送多個 provide_help 之間的節流
_LIST_TIMEOUT_SEC = 8.0     # 活動休眠時 server 不回 → 短 timeout 當作空清單


def read_help_list(client) -> tuple[list[dict], int]:
    """讀 0x4F15 求助清單。回傳 ``(events, help_hp)``。

    ``events`` 每筆 = {id, event_id, role_id, status}。用 ``codec.walk`` 保留
    repeated 的 event_list（``walk_dict`` 會 last-wins 只剩最後一筆）。
    """
    body = client.call(CMD_HELP_LIST, b"", expect_cmd=CMD_HELP_LIST,
                       timeout=_LIST_TIMEOUT_SEC)
    events: list[dict] = []
    help_hp = 0
    for fn, val in codec.walk(body):
        if fn == _F_EVENT_LIST and isinstance(val, (bytes, bytearray)):
            e = codec.walk_dict(bytes(val))
            events.append({
                "id": e.get(_E_ID),
                "event_id": e.get(_E_EVENT_ID),
                "role_id": e.get(_E_ROLE_ID),
                "status": e.get(_E_STATUS, 0),
            })
        elif fn == _F_HELP_HP and isinstance(val, int):
            help_hp = val
    return events, help_hp


def _is_pending(ev: dict) -> bool:
    return (
        # 前端不是只判 status==0，而是判 status != 1；進攻求助可能回 status=2。
        ev.get("status") != _STATUS_DONE
        and bool(ev.get("role_id"))
        and bool(ev.get("event_id"))
    )


def rescue_pending(client) -> dict[str, Any]:
    """讀清單 → 對每個可協助求助送 provide_help → 重讀確認。

    provide_help 是 fire-and-forget（無同 cmd 回應），故靠重讀清單確認剩餘量。
    """
    events, help_hp_before = read_help_list(client)
    pending = [e for e in events if _is_pending(e)]

    for ev in pending:
        body = codec.pb_uint(1, int(ev["role_id"])) + codec.pb_uint(2, int(ev["event_id"]))
        client.send(CMD_PROVIDE_HELP, body)
        logger.info("[dragon_sos] provide_help role_id=%s event_id=%s",
                    ev["role_id"], ev["event_id"])
        time.sleep(_PROVIDE_PACE_SEC)

    after, help_hp_after = read_help_list(client)
    remaining = [e for e in after if _is_pending(e)]
    return {
        "attempted": len(pending),
        "targets": pending,
        "remaining": len(remaining),
        "help_hp_before": help_hp_before,
        "help_hp_after": help_hp_after,
    }


def claim_help_rewards(client, my_role_id: int) -> dict[str, Any]:
    """領取本帳號已完成的協助事件，並用重讀清單確認。"""
    events, help_hp_before = read_help_list(client)
    claimable = [
        ev for ev in events
        if ev.get("role_id") == int(my_role_id)
        and ev.get("event_id")
        and ev.get("status") == _STATUS_DONE
    ]

    for ev in claimable:
        body = codec.pb_uint(1, int(ev["event_id"]))
        client.send(CMD_RECEIVE_HELP, body)
        logger.info("[dragon_sos] receive_help event_id=%s role_id=%s",
                    ev["event_id"], ev["role_id"])
        time.sleep(_PROVIDE_PACE_SEC)

    after, help_hp_after = read_help_list(client)
    remaining = [
        ev for ev in after
        if ev.get("role_id") == int(my_role_id)
        and ev.get("event_id")
        and ev.get("status") == _STATUS_DONE
    ]
    return {
        "attempted": len(claimable),
        "claimed": max(0, len(claimable) - len(remaining)),
        "remaining": len(remaining),
        "help_hp_before": help_hp_before,
        "help_hp_after": help_hp_after,
    }


def rescue_via_ws(ip: str, *, session=None) -> dict[str, Any]:
    """用 ``ip`` 當協助號，透過 ws_session 持久連線執行救援。

    若連線本來不存在（由本次 SOS 建立），結束時 disconnect 還原該機 bot；
    若是復用既有面板連線，則保留不動。連線失敗回 ``{status: error}``。
    """
    if session is None:
        from control_panel import ws_session as session

    preexisting = session.get_client(ip) is not None
    res = session.ensure(ip)
    if res.get("status") != "ok":
        return {"status": "error", "message": res.get("message", "WS 連線失敗")}

    client = session.get_client(ip)
    if client is None:
        if not preexisting:
            session.disconnect(ip)
        return {"status": "error", "message": "WS client 不可用（連線剛斷？）"}

    try:
        result = rescue_pending(client)
        result["status"] = "ok"
        return result
    except Exception as exc:  # noqa: BLE001 — 對前端統一回報
        logger.exception("[dragon_sos] %s 救援失敗", ip)
        return {"status": "error", "message": str(exc)}
    finally:
        if not preexisting:
            session.disconnect(ip)
