"""坐騎衝刺「發條」的純 WebSocket 協定與排程。"""
from __future__ import annotations

import datetime

import json_manager
from ws_token import codec
from ws_token import mount_activity
from ws_token.client import WSError


# 坐騎模組 31 (0x1f)
CMD_MOUNT_INFO = 0x1F01
CMD_MOUNT_LEVEL_UP = 0x1F02
CMD_ERROR = 0x0201

SPRINT_RECORD = "衝刺-發條"
ALLOWED_WEEKDAYS = (1, 2)  # 週二、週三
SPRINT_CLOSE_WEEKDAY = 2   # 週三
SPRINT_CLOSE_HOUR = 22     # 台灣時間 22:00 後結算
DEFAULT_QUANTITY = 3200
MOUNT_SPRINT_ITEM = 1008   # 無限時發條

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def is_open(now: datetime.datetime | None = None) -> bool:
    """判斷坐騎衝刺是否仍在活動開放窗。"""
    current = now or datetime.datetime.now(_TPE)
    if current.weekday() not in ALLOWED_WEEKDAYS:
        return False
    return not (
        current.weekday() == SPRINT_CLOSE_WEEKDAY
        and current.hour >= SPRINT_CLOSE_HOUR
    )


def is_due(device: str, now: datetime.datetime | None = None) -> bool:
    """判斷本裝置本活動週是否需要餵養一次。"""
    current = now or datetime.datetime.now(_TPE)
    if not is_open(current):
        return False

    record = json_manager.return_time(device, name=SPRINT_RECORD)
    if record and not record.get("is_next_week", True):
        return False
    should_run, _ = json_manager.should_execute_cycle(
        device,
        SPRINT_RECORD,
        cycle_weeks=4,
        allowed_weekdays=list(ALLOWED_WEEKDAYS),
        today=current.date(),
    )
    return bool(should_run)


def build_level_up_body(quantity: int) -> bytes:
    """建立一次自訂數量餵養的 protobuf body。"""
    return codec.pb_uint(1, 0) + codec.pb_uint(2, quantity)


def _raise_server_error(body: bytes) -> None:
    error_code = codec.walk_dict(body).get(1)
    raise WSError(f"mount levup rejected (error_code={error_code})")


def run(
    client,
    device: str,
    *,
    enabled: bool = True,
    quantity: int = DEFAULT_QUANTITY,
    now: datetime.datetime | None = None,
) -> dict:
    """在活動週用純 WS 餵養發條，成功後寫入週期記錄。"""
    if not enabled:
        return {"skipped": "mount sprint disabled"}
    current = now or datetime.datetime.now(_TPE)
    # 週期錨點不能代表同服本週真的開了坐騎衝刺，先向伺服器探測活動型別。
    if not is_open(current) or not is_due(device, now=current):
        return {"skipped": "not due"}
    active_type = mount_activity.find_active_act_type(client)
    if active_type is None:
        return {"skipped": "mount sprint: no active server event"}
    record = json_manager.return_time(device, name=SPRINT_RECORD)
    if record and not record.get("is_next_week", True):
        return {"skipped": "not due"}

    try:
        amount = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid mount sprint quantity: {quantity!r}") from exc
    if amount <= 0:
        raise ValueError(f"mount sprint quantity must be positive: {amount}")

    reply_cmd, reply = client.call_for(
        CMD_MOUNT_LEVEL_UP,
        build_level_up_body(amount),
        expect_cmds=(CMD_MOUNT_LEVEL_UP, CMD_ERROR),
    )
    if reply_cmd == CMD_ERROR:
        _raise_server_error(reply)
    if reply_cmd != CMD_MOUNT_LEVEL_UP:
        raise WSError(f"unexpected mount levup response: 0x{reply_cmd:04x}")

    result = {"quantity": amount, "act_type": active_type}
    exp = codec.walk_dict(reply).get(1)
    if isinstance(exp, int):
        result["exp"] = exp
    # 只有收到 server 的成功回應才記錄，避免 UI fallback 被誤跳過。
    json_manager.time_recording(device, name=SPRINT_RECORD)
    result["recorded"] = True
    return result
