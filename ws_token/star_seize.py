"""星據搶佔（奇星車場）純 WebSocket 協議。"""
from __future__ import annotations

import time

from ws_token import codec


CMD_ERROR = 0x0201
CMD_SERVER_CAR_INFO = 12860
CMD_SERVER_CAR_JOIN = 12861
CMD_SERVER_CAR_QUEUE = 12868


class StarSeizeRPCError(RuntimeError):
    """星據 RPC 被伺服器拒絕。"""

    def __init__(self, code: int, cmd: int):
        super().__init__(f"星據操作失敗（cmd={cmd}, code={code}）")
        self.code = int(code)
        self.cmd = int(cmd)


def _values(data: bytes, field_id: int) -> list[object]:
    return [value for fid, value in codec.walk(data) if fid == field_id]


def _varint(fields: dict[int, object], field_id: int, default: int = 0) -> int:
    value = fields.get(field_id, default)
    return int(value) if isinstance(value, int) else default


def _text(value: object) -> str:
    if not isinstance(value, (bytes, bytearray)):
        return ""
    return bytes(value).decode("utf-8", errors="replace")


def _call(client, cmd: int, body: bytes = b"", *, timeout: float = 8.0) -> bytes:
    reply_cmd, reply = client.call_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout
    )
    if reply_cmd == CMD_ERROR:
        raise StarSeizeRPCError(_varint(codec.walk_dict(reply), 1), cmd)
    return reply


def parse_state(body: bytes, *, my_server: int = 0,
                server_time: int | None = None) -> dict:
    """解析 server_car_info_s2c，維持前端既有欄位格式。"""
    top = codec.walk_dict(body)
    now = int(time.time()) if server_time is None else int(server_time)
    slots = []
    for raw in _values(body, 1):
        if not isinstance(raw, (bytes, bytearray)):
            continue
        fields = codec.walk_dict(bytes(raw))
        owner = _varint(fields, 2)
        free_end = _varint(fields, 6)
        slots.append({
            "pos": _varint(fields, 1),
            "owner": owner,
            "is_free": _varint(fields, 5),
            "free_end": free_end,
            "defQ": sum(1 for fid, _ in codec.walk(bytes(raw)) if fid == 4),
            "mount_id": _varint(fields, 8),
            "remaining": free_end - now if free_end else 0,
            "attackable": (
                owner != 0
                and free_end <= now
                and (my_server <= 0 or owner != my_server)
            ),
        })
    slots.sort(key=lambda item: item["pos"])
    attack_cd = _varint(top, 5)
    defend_cd = _varint(top, 4)
    tw_hour = ((now + 8 * 3600) % 86400) // 3600
    return {
        "serverTime": now,
        "myServer": my_server or None,
        "slots": slots,
        "attack_cd_end_time": attack_cd,
        "defend_cd_end_time": defend_cd,
        "my_attack_cd_remaining": max(0, attack_cd - now),
        "defend_cd_remaining": max(0, defend_cd - now),
        "truce": tw_hour >= 22 or tw_hour < 10,
        "taiwanHour": tw_hour,
    }


def read_state(client, *, my_server: int = 0) -> dict:
    return parse_state(_call(client, CMD_SERVER_CAR_INFO), my_server=my_server)


def parse_opponent(body: bytes, *, pos: int) -> dict:
    defenders = []
    for raw in _values(body, 3):
        if not isinstance(raw, (bytes, bytearray)):
            continue
        fields = codec.walk_dict(bytes(raw))
        attrs = []
        for entry in _values(bytes(raw), 6):
            if isinstance(entry, (bytes, bytearray)):
                pair = codec.walk_dict(bytes(entry))
                attrs.append({"k": _varint(pair, 1), "v": _varint(pair, 2)})
        defenders.append({
            "name": _text(fields.get(3)),
            "server": _varint(fields, 2),
            "queue_index": _varint(fields, 4),
            "attrs_kv": attrs,
        })
    return {"pos": int(pos), "defenders": defenders}


def read_opponent(client, pos: int) -> dict:
    body = _call(client, CMD_SERVER_CAR_QUEUE, codec.pb_uint(1, int(pos)))
    return parse_opponent(body, pos=pos)


def join(client, pos: int, queue_type: int) -> dict:
    """送出加入搶佔／駐守，僅依伺服器回包判定成功。"""
    body = codec.pb_uint(1, int(pos)) + codec.pb_uint(2, int(queue_type))
    reply = _call(client, CMD_SERVER_CAR_JOIN, body, timeout=12.0)
    fields = codec.walk_dict(reply)
    code = _varint(fields, 1)
    return {
        "ok": code == 0,
        "code": code,
        "pos": _varint(fields, 2, int(pos)),
        "queue_type": _varint(fields, 3, int(queue_type)),
        "queue_index": _varint(fields, 4),
        **({} if code == 0 else {"reason": f"server-code-{code}"}),
    }
